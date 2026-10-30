"""
Joint MILP Portfolio, Credit-Leverage & Starting-XI Optimizer (`PortfolioOptimizer`)
====================================================================================
Uses `scipy.optimize.milp` (HiGHS exact C++ solver) to jointly solve:
1. Optimal Legal Starting XI + Multiposition Back-Slotting across all 7 official
   Biwenger formations (`4-5-1`, `3-5-2`, `4-4-2`, `4-3-3`, `3-4-3`, `5-4-1`, `5-3-2`).
2. Friday Pre-Kickoff Debt-Clearing Knapsack: guarantees `post_sale_cash >= 0 €`
   before matchday kickoff while sacrificing the absolute minimum `xP_XI + λ * EFR`.
3. Weekday 25% Credit-Line Arbitrage (`Cash + 0.25 * SquadValue`), Preventive
   Daily Market Listing (0€ cost to harvest +-5% Computer offers), Peak Offer
   Harvesting (+3.5%..+5.0%), Stop-Loss execution, Auction Bids & Clause Snipes.
"""

from typing import Dict, List, Any, Optional, Set, Tuple
import numpy as np
import pandas as pd
from scipy.optimize import milp, LinearConstraint, Bounds

from src.tools.strategy_engine.xp_model import ExpectedPointsEngine
from src.tools.strategy_engine.auction_model import AuctionBidModel
from src.tools.strategy_engine.clause_sniper import ClauseSniper
from src.utils.numeric import safe_num


OFFICIAL_FORMATIONS: List[Tuple[str, int, int, int]] = [
    ("3-5-2", 3, 5, 2),
    ("3-4-3", 3, 4, 3),
    ("4-5-1", 4, 5, 1),
    ("4-4-2", 4, 4, 2),
    ("4-3-3", 4, 3, 3),
    ("5-4-1", 5, 4, 1),
    ("5-3-2", 5, 3, 2),
]
LINES = ("GK", "DF", "MF", "FW")


class PortfolioOptimizer:
    """Exact MILP & Quantitative Strategy Decision Engine for AZ Finance."""

    def __init__(
        self,
        xp_engine: Optional[ExpectedPointsEngine] = None,
        auction_model: Optional[AuctionBidModel] = None,
        clause_sniper: Optional[ClauseSniper] = None,
        credit_limit_ratio: float = 0.25,
        max_squad_size: int = 21,
        preventive_listing: bool = True,
    ):
        self.xp_engine = xp_engine or ExpectedPointsEngine()
        self.auction_model = auction_model or AuctionBidModel()
        self.clause_sniper = clause_sniper or ClauseSniper(policy="AGGRESSIVE")
        self.credit_limit_ratio = credit_limit_ratio
        self.max_squad_size = max_squad_size
        self.preventive_listing = preventive_listing

    def solve_joint_lineup_and_solvency_milp(
        self,
        squad_df: pd.DataFrame,
        xp_table: pd.DataFrame,
        efr_map: Dict[int, float],
        current_cash: float,
        must_be_solvent: bool = False,
        offer_map: Optional[Dict[int, float]] = None,
        sell_locked_ids: Optional[Set[int]] = None,
        untouchable_ids: Optional[Set[int]] = None,
    ) -> Dict[str, Any]:
        """
        Solves the exact Mixed-Integer Linear Program choosing:
          - `y_i in {0, 1}`: whether to sell player `i` today (at firm offer `O_i` or emergency VM*0.97)
          - `x_{i, l} in {0, 1}`: whether to field player `i` in line `l in {GK, DF, MF, FW}`
          - `f_k in {0, 1}`: which of the 7 official Biwenger formations is active
          - `slack_l >= 0`: empty-slot slack (heavily penalized by -50 pts so 11 players are always fielded if possible)
        """
        if squad_df is None or squad_df.empty:
            return {
                "formation": "4-4-2",
                "lineup_ids": [],
                "lineup_slots": [],
                "expected_xi_points": 0.0,
                "sales_to_execute": [],
                "projected_cash": current_cash,
                "is_solvent": current_cash >= 0,
            }

        sq = squad_df.copy()
        sq["player_id"] = pd.to_numeric(sq["player_id"], errors="coerce").fillna(0).astype(int)
        sq = sq[sq["player_id"] > 0].drop_duplicates(subset=["player_id"]).reset_index(drop=True)
        n = len(sq)

        xp_idx = xp_table.set_index("player_id").to_dict("index") if (xp_table is not None and not xp_table.empty) else {}
        offers = offer_map or {}
        locked_sell = sell_locked_ids or set()
        untouchables = untouchable_ids or set()

        debt_to_clear = max(0.0, -float(current_cash)) if must_be_solvent else 0.0

        # Variable layout:
        # [0 .. 4n-1]: x_{i, l} for i in 0..n-1, l in 0..3 (GK, DF, MF, FW)
        # [4n .. 5n-1]: y_i (sell indicator for i in 0..n-1)
        # [5n .. 5n+6]: f_k (formation choice k in 0..6)
        # [5n+7 .. 5n+10]: s_l (empty position slack for l in 0..3, integer >= 0)
        # [5n+11]: debt_slack >= 0 (continuous slack if even selling everything unlocked cannot clear 100% debt)
        n_vars = 5 * n + 12
        c = np.zeros(n_vars, dtype=float)  # scipy.optimize.milp MINIMIZES c^T z -> negate rewards!
        lb = np.zeros(n_vars, dtype=float)
        ub = np.ones(n_vars, dtype=float)
        integrality = np.ones(n_vars, dtype=int)

        # Slacks upper bounds
        for l_idx in range(4):
            ub[5 * n + 7 + l_idx] = 5.0
        ub[5 * n + 11] = 1e9
        integrality[5 * n + 11] = 0  # continuous euro debt slack

        sale_proceeds = np.zeros(n, dtype=float)

        for i, r in sq.iterrows():
            pid = int(r["player_id"])
            price = safe_num(r.get("precio", 150_000), 150_000.0)
            subida = safe_num(r.get("subida_24h", 0), 0.0)
            efr = float(efr_map.get(pid, subida * 2.4))
            xp_info = xp_idx.get(pid, {})
            is_avail = bool(xp_info.get("is_available", True))
            valid_lines = self.xp_engine.get_valid_lines(pid, r.get("posicion", ""))

            # Firm sale price if sold today
            if pid in offers and offers[pid] > 0:
                proceeds = float(offers[pid])
                has_firm_offer = True
            else:
                # Can only realize cash before tonight's kickoff if there is an active offer!
                proceeds = 0.0 if must_be_solvent and len(offers) > 0 and pid not in offers else price * 0.98
                has_firm_offer = False
            sale_proceeds[i] = proceeds

            # Can player i be sold today?
            can_sell = (pid not in locked_sell) and (proceeds > 0)
            if not must_be_solvent:
                # If no debt emergency, never sell via MILP (routine sales handled by peak/stop-loss rules)
                can_sell = False
            ub[4 * n + i] = 1.0 if can_sell else 0.0

            # Lineup assignment rewards (weight 100.0 per xP point so XI quality dominates!)
            for l_idx, ln in enumerate(LINES):
                var_idx = 4 * i + l_idx
                if ln not in valid_lines:
                    ub[var_idx] = 0.0
                else:
                    # Even unavailable players score 0 pts (better than -4 empty slot penalty if desperate!)
                    xp_val = float(xp_info.get(f"xp_{ln}", 0.0)) if is_avail else 0.01
                    # Small tie-breaker (+0.02) for deeper defensive lines on multiposition ties
                    depth_tie = (3 - l_idx) * 0.015
                    c[var_idx] = -(100.0 * xp_val + depth_tie)

            # Hold retention value (so when clearing Friday debt, MILP sells lowest future-value bench assets first!)
            xp_best = float(xp_info.get("xp_best", 0.0))
            offer_prem = (proceeds - price) if has_firm_offer else 0.0
            # Hold score in "xP-equivalent units": future 72h appreciation minus today's offer premium + bench depth value
            net_future_eur = efr - offer_prem
            hold_score = (net_future_eur / 50_000.0) * 6.0 + xp_best * 4.5 + (40.0 if pid in untouchables else 0.0)
            # Selling (`y_i = 1`) loses `hold_score` -> positive cost in minimization objective `c`
            # Add tiny +0.2 cost per sale to prefer 1 surgical sale over 4 tiny sales (Surgical Rule!)
            c[4 * n + i] = hold_score + 0.25

        # Formation slight offensive tie-breaker (prefer 3-4-3 / 3-5-2 on exact ties)
        for k, (_, _, n_mf, n_fw) in enumerate(OFFICIAL_FORMATIONS):
            c[5 * n + k] = -(0.005 * n_mf + 0.003 * n_fw)

        # Heavy penalty on empty lineup slots (-4 pts real penalty + missed starter -> +600 cost!)
        for l_idx in range(4):
            c[5 * n + 7 + l_idx] = 650.0

        # Massive penalty on un-cleared Friday debt (scoring 0 pts for the whole team!)
        c[5 * n + 11] = 0.05  # 0.05 per un-cleared Euro -> 1M debt = 50,000 penalty >> any player's xP!

        # Build Linear Constraints: A_ub / lb_c <= A z <= ub_c
        rows_A = []
        rows_lb = []
        rows_ub = []

        # 1. Exactly 1 formation chosen: sum_k f_k == 1
        row_f = np.zeros(n_vars)
        row_f[5 * n : 5 * n + 7] = 1.0
        rows_A.append(row_f)
        rows_lb.append(1.0)
        rows_ub.append(1.0)

        # 2. Player coherence: sum_l x_{i, l} + y_i <= 1 (each player plays at most 1 line, and 0 if sold)
        for i in range(n):
            row_p = np.zeros(n_vars)
            row_p[4 * i : 4 * i + 4] = 1.0
            row_p[4 * n + i] = 1.0
            rows_A.append(row_p)
            rows_lb.append(0.0)
            rows_ub.append(1.0)

        # 3. Line counts match chosen formation (with empty-slot slack s_l):
        # GK: sum_i x_{i, GK} + s_GK == 1
        row_gk = np.zeros(n_vars)
        for i in range(n):
            row_gk[4 * i + 0] = 1.0
        row_gk[5 * n + 7 + 0] = 1.0
        rows_A.append(row_gk)
        rows_lb.append(1.0)
        rows_ub.append(1.0)

        # DF, MF, FW: sum_i x_{i, l} + s_l - sum_k FormCount_{k, l} * f_k == 0
        for l_idx, form_pos_idx in [(1, 1), (2, 2), (3, 3)]:
            row_ln = np.zeros(n_vars)
            for i in range(n):
                row_ln[4 * i + l_idx] = 1.0
            row_ln[5 * n + 7 + l_idx] = 1.0
            for k, f_tuple in enumerate(OFFICIAL_FORMATIONS):
                row_ln[5 * n + k] = -float(f_tuple[form_pos_idx])
            rows_A.append(row_ln)
            rows_lb.append(0.0)
            rows_ub.append(0.0)

        # 4. Solvency Constraint (when must_be_solvent and debt_to_clear > 0):
        # sum_i sale_proceeds_i * y_i + debt_slack >= debt_to_clear
        if debt_to_clear > 0:
            row_solv = np.zeros(n_vars)
            for i in range(n):
                row_solv[4 * n + i] = sale_proceeds[i]
            row_solv[5 * n + 11] = 1.0
            rows_A.append(row_solv)
            rows_lb.append(debt_to_clear)
            rows_ub.append(1e12)

        # Defensive: never let a NaN/inf from upstream data reach HiGHS
        c = np.nan_to_num(c, nan=0.0, posinf=1e6, neginf=-1e6)
        sale_proceeds = np.nan_to_num(sale_proceeds, nan=0.0)
        A_mat = np.vstack(rows_A)
        A_mat = np.nan_to_num(A_mat, nan=0.0)
        constraints = LinearConstraint(A_mat, rows_lb, rows_ub)
        bounds = Bounds(lb, ub)

        res = milp(c=c, integrality=integrality, bounds=bounds, constraints=constraints)
        if not res.success or res.x is None:
            return {
                "formation": "4-4-2",
                "lineup_ids": [],
                "lineup_slots": [],
                "expected_xi_points": 0.0,
                "sales_to_execute": [],
                "projected_cash": current_cash,
                "is_solvent": current_cash >= 0,
            }

        z = res.x
        chosen_k = int(np.argmax(z[5 * n : 5 * n + 7]))
        chosen_formation = OFFICIAL_FORMATIONS[chosen_k][0]

        sales_to_execute = []
        cash_after = float(current_cash)
        for i, r in sq.iterrows():
            if z[4 * n + i] > 0.5:
                pid = int(r["player_id"])
                inc = float(sale_proceeds[i])
                cash_after += inc
                sales_to_execute.append({
                    "player_id": pid,
                    "nombre": str(r.get("nombre", "")),
                    "sale_price": int(round(inc)),
                    "reason": "FRIDAY_SOLVENCY_MILP",
                })

        # Extract ordered lineup: GK -> DF -> MF -> FW
        lineup_slots = []
        exp_xi_pts = 0.0
        for l_idx, ln in enumerate(LINES):
            line_players = []
            for i, r in sq.iterrows():
                if z[4 * i + l_idx] > 0.5:
                    pid = int(r["player_id"])
                    xp_info = xp_idx.get(pid, {})
                    xp_val = float(xp_info.get(f"xp_{ln}", 0.0)) if xp_info.get("is_available", True) else 0.0
                    exp_xi_pts += xp_val
                    line_players.append((xp_val, pid, str(r.get("nombre", ""))))
            line_players.sort(reverse=True)
            for xp_val, pid, nm in line_players:
                lineup_slots.append({
                    "player_id": pid,
                    "nombre": nm,
                    "linea": ln,
                    "xp": round(xp_val, 2),
                })

        # Penalize empty slots (-4 pts each in official Biwenger rules)
        n_empty = max(0, 11 - len(lineup_slots))
        exp_xi_pts = max(0.0, exp_xi_pts - 4.0 * n_empty)

        return {
            "formation": chosen_formation,
            "lineup_ids": [s["player_id"] for s in lineup_slots],
            "lineup_slots": lineup_slots,
            "expected_xi_points": round(exp_xi_pts, 2),
            "sales_to_execute": sales_to_execute,
            "projected_cash": round(cash_after),
            "is_solvent": cash_after >= -0.5,
        }

    def generate_daily_plan(
        self,
        current_date: str,
        my_manager_name: str,
        current_cash: float,
        squad_df: pd.DataFrame,
        df_market_all: pd.DataFrame,
        market_listings_df: pd.DataFrame,
        xp_table: pd.DataFrame,
        efr_map: Dict[int, float],
        active_offers_map: Optional[Dict[int, float]] = None,
        sell_locked_ids: Optional[Set[int]] = None,
        clause_locked_ids: Optional[Set[int]] = None,
        untouchable_ids: Optional[Set[int]] = None,
        is_kickoff_day: bool = False,
        days_to_kickoff: int = 4,
        recent_match_shock_map: Optional[Dict[int, float]] = None,
        indebted_rival_managers: Optional[Set[str]] = None,
        league_median_max_bid: Optional[float] = None,
        max_rival_purchasing_power: float = 25_000_000.0,
    ) -> Dict[str, Any]:
        """
        Full daily autonomous decision cycle:
          Step 1: Routine Stop-Loss & Peak Offer Harvesting (+3.5%..+5.0% Computer offers)
          Step 2: Joint MILP Starting XI (+ Friday Pre-Kickoff Debt Solvency if `is_kickoff_day`)
          Step 3: 25% Credit-Leveraged Acquisitions (Auction Bids + 24/7 Clause Snipes)
          Step 4: Defensive Shielding & Preventive Daily Market Listing (0€ Option Harvest)
        """
        offers = dict(active_offers_map or {})
        locked_sell = set(sell_locked_ids or set())
        untouchables = set(untouchable_ids or set())
        indebted_rivals = indebted_rival_managers or set()

        sq = squad_df.copy() if (squad_df is not None and not squad_df.empty) else pd.DataFrame()
        if not sq.empty:
            sq["player_id"] = pd.to_numeric(sq["player_id"], errors="coerce").fillna(0).astype(int)
            sq = sq.drop_duplicates(subset=["player_id"]).reset_index(drop=True)

        xp_idx = xp_table.set_index("player_id").to_dict("index") if (xp_table is not None and not xp_table.empty) else {}

        # Baseline MILP XI before sales to identify our current core XI starters!
        base_milp = self.solve_joint_lineup_and_solvency_milp(
            squad_df=sq,
            xp_table=xp_table,
            efr_map=efr_map,
            current_cash=current_cash,
            must_be_solvent=False,
        )
        core_xi_ids = {s["player_id"] for s in base_milp["lineup_slots"]}

        # Step 1: Immediate Profit-Taking (+3.5%..+5% Computer Offer on Plateau) & Toxic Stop-Loss
        routine_sales: List[Dict[str, Any]] = []
        sold_ids: Set[int] = set()
        working_cash = float(current_cash)

        if not sq.empty:
            # Count fit players so we never drop below 11 fit players on kickoff day
            fit_count = sum(
                1 for _, r in sq.iterrows() if xp_idx.get(int(r["player_id"]), {}).get("is_available", True)
            )
            gk_count = sum(
                1 for _, r in sq.iterrows()
                if "GK" in self.xp_engine.get_valid_lines(int(r["player_id"]), r.get("posicion", ""))
            )

            for _, r in sq.iterrows():
                pid = int(r["player_id"])
                if pid in locked_sell or pid in untouchables or pid not in offers:
                    continue
                offer_amt = float(offers[pid])
                if offer_amt <= 0:
                    continue

                price = safe_num(r.get("precio", 150_000), 150_000.0)
                subida = safe_num(r.get("subida_24h", 0), 0.0)
                efr_72h = float(efr_map.get(pid, subida * 2.4))
                xp_info = xp_idx.get(pid, {})
                is_avail = bool(xp_info.get("is_available", True))
                p_start = float(xp_info.get("p_start", 0.0))
                xp_best = float(xp_info.get("xp_best", 0.0))
                is_gk = "GK" in self.xp_engine.get_valid_lines(pid, r.get("posicion", ""))

                if is_gk and gk_count <= 1:
                    continue
                if is_avail and fit_count <= 11:
                    continue

                offer_pct = (offer_amt - price) / max(price, 1.0)

                acq_date = r.get("acquired_date", None)
                held_days = 99
                if acq_date is not None and str(acq_date) not in ("", "nan", "None"):
                    try:
                        held_days = (pd.Timestamp(current_date) - pd.Timestamp(str(acq_date))).days
                    except (ValueError, TypeError):
                        held_days = 99

                # Rule 1A: Toxic Asset Stop-Loss (injured/falling non-core or severe crash)
                is_toxic_stop_loss = (not is_avail and price >= 1_200_000 and efr_72h <= -30_000) or (
                    held_days >= 3
                    and (
                        (pid not in core_xi_ids and (subida <= -40_000 or efr_72h <= -80_000) and p_start < 0.65)
                        or (subida <= -130_000 and efr_72h <= -220_000 and xp_best < 5.2)
                    )
                )

                # Rule 1B: Peak Computer Offer Arbitrage (+3.3%..+5.0% instant risk-free premium on non-core / plateauing flip)
                is_peak_harvest = (
                    pid not in core_xi_ids
                    and offer_pct >= 0.032
                    and (offer_amt - price) >= max(25_000.0, efr_72h * 0.78)
                    and xp_best < 4.8
                )

                if is_toxic_stop_loss or is_peak_harvest:
                    routine_sales.append({
                        "player_id": pid,
                        "nombre": str(r.get("nombre", "")),
                        "sale_price": int(round(offer_amt)),
                        "market_price": int(round(price)),
                        "offer_premium_pct": round(offer_pct * 100.0, 2),
                        "reason": "STOP_LOSS_TOXIC" if is_toxic_stop_loss else "PEAK_OFFER_ARBITRAGE",
                    })
                    sold_ids.add(pid)
                    working_cash += offer_amt
                    if is_avail:
                        fit_count -= 1
                    if is_gk:
                        gk_count -= 1

        remaining_sq = sq[~sq["player_id"].isin(sold_ids)].copy() if not sq.empty else pd.DataFrame()
        remaining_offers = {k: v for k, v in offers.items() if k not in sold_ids}

        # Step 2: Joint MILP Starting XI (+ Mandatory Solvency if Kickoff Day!)
        milp_sol = self.solve_joint_lineup_and_solvency_milp(
            squad_df=remaining_sq,
            xp_table=xp_table,
            efr_map=efr_map,
            current_cash=working_cash,
            must_be_solvent=is_kickoff_day,
            offer_map=remaining_offers,
            sell_locked_ids=locked_sell,
            untouchable_ids=untouchables,
        )

        solvency_sales = milp_sol["sales_to_execute"]
        for s in solvency_sales:
            sold_ids.add(int(s["player_id"]))
        working_cash = float(milp_sol["projected_cash"])
        final_sq = sq[~sq["player_id"].isin(sold_ids)].copy() if not sq.empty else pd.DataFrame()

        # Compute XI floor xP by position to measure structural upgrade value of new signings
        xi_floor_by_pos: Dict[str, float] = {"GK": 2.5, "DF": 3.0, "MF": 3.2, "FW": 3.2}
        slots_by_line: Dict[str, List[float]] = {"GK": [], "DF": [], "MF": [], "FW": []}
        for sl in milp_sol["lineup_slots"]:
            slots_by_line[sl["linea"]].append(float(sl["xp"]))
        for ln, vals in slots_by_line.items():
            if vals:
                xi_floor_by_pos[ln] = min(vals)
            else:
                xi_floor_by_pos[ln] = 0.0  # urgent empty slot!

        # Step 3: Safe 25% Credit-Leveraged Purchasing Power & Target Allocation
        rem_squad_value = float(pd.to_numeric(final_sq["precio"], errors="coerce").fillna(0).sum()) if not final_sq.empty else 0.0
        max_official_credit_bid = working_cash + self.credit_limit_ratio * rem_squad_value

        if is_kickoff_day:
            # NEVER enter negative balance on kickoff day! Only spend positive surplus cash!
            safe_acquisition_budget = max(0.0, working_cash)
        else:
            # Mon-Thu Leverage Rule: can borrow up to 25% credit line, bounded by 82% of unlocked non-core collateral
            # so Friday solvency MILP ALWAYS has plenty of liquid collateral to get back to >= 0 €!
            final_xi_top_ids = {s["player_id"] for s in sorted(milp_sol["lineup_slots"], key=lambda x: x["xp"], reverse=True)[:8]}
            liquid_collateral = 0.0
            if not final_sq.empty:
                for _, r in final_sq.iterrows():
                    pid = int(r["player_id"])
                    if pid not in final_xi_top_ids and pid not in untouchables:
                        liquid_collateral += safe_num(r.get("precio", 0), 0.0)
            # Note: players bought Monday-Wednesday (days_to_kickoff >= 2) will ALSO be unlocked (>24h) by Friday!
            safe_debt_cap = liquid_collateral * (0.85 if days_to_kickoff >= 2 else 0.65)
            safe_acquisition_budget = min(max_official_credit_bid, working_cash + safe_debt_cap)
            safe_acquisition_budget = max(0.0, safe_acquisition_budget)

        owned_ids = set(final_sq["player_id"].tolist()) if not final_sq.empty else set()
        open_roster_slots = max(0, self.max_squad_size - len(owned_ids))

        # 3A. Evaluate Market Auction Candidates (`AuctionBidModel`)
        bid_candidates: List[Dict[str, Any]] = []
        if open_roster_slots > 0 and safe_acquisition_budget >= 150_000 and market_listings_df is not None and not market_listings_df.empty:
            mkt_all_idx = (
                df_market_all.assign(pid_int=pd.to_numeric(df_market_all["player_id"], errors="coerce").fillna(0).astype(int))
                .drop_duplicates(subset=["pid_int"], keep="last")
                .set_index("pid_int")
                .to_dict("index")
            )
            for _, lr in market_listings_df.iterrows():
                try:
                    pid = int(float(lr["player_id"]))
                except (KeyError, ValueError, TypeError):
                    continue
                if pid <= 0 or pid in owned_ids:
                    continue

                p_base = mkt_all_idx.get(pid, {})
                xp_info = xp_idx.get(pid)
                if not xp_info or not xp_info.get("is_available", False):
                    continue

                vm = safe_num(p_base.get("precio", lr.get("precio_mercado_dia", 0)), 0.0)
                ask = safe_num(lr.get("ask_price", lr.get("precio_salida_o_oferta", vm)), vm)
                subida = safe_num(p_base.get("subida_24h", 0), 0.0)
                if vm <= 0:
                    continue

                seller = str(lr.get("seller_name", lr.get("vendedor_o_origen", "Mercado")) or "Mercado").strip()
                if seller == my_manager_name:
                    continue
                is_rival = seller not in ("Mercado", "Libre", "None", "nan", "")

                best_ln = str(xp_info.get("best_line", xp_info.get("natural_pos", "MF")))
                delta_xp = max(0.0, float(xp_info.get("xp_best", 0.0)) - xi_floor_by_pos.get(best_ln, 3.2))
                efr_72h = float(efr_map.get(pid, subida * 2.4))

                bid_eval = self.auction_model.compute_optimal_bid(
                    player_id=pid,
                    market_price=vm,
                    asking_price=ask,
                    subida_24h=subida,
                    efr_72h=efr_72h,
                    delta_xp_xi=delta_xp,
                    p_start=float(xp_info.get("p_start", 0.0)),
                    seller_is_rival=is_rival,
                    rival_seller_in_debt=(seller in indebted_rivals),
                    league_median_max_bid=league_median_max_bid,
                    idle_cash_eur=max(0.0, working_cash),
                )

                if not bid_eval["should_bid"]:
                    continue
                b_amt = int(bid_eval["bid_amount"])
                # Single-bid Biwenger rule: cannot exceed max_official_credit_bid
                if b_amt > max_official_credit_bid or b_amt > safe_acquisition_budget:
                    continue

                bid_candidates.append({
                    "action_type": "MARKET_BID",
                    "player_id": pid,
                    "nombre": str(p_base.get("nombre", lr.get("jugador", f"ID {pid}"))),
                    "position": best_ln,
                    "seller": seller,
                    "market_price": int(round(vm)),
                    "ask_price": int(round(ask)),
                    "amount": b_amt,
                    "efr_72h": int(round(efr_72h)),
                    "xp_best": round(float(xp_info.get("xp_best", 0.0)), 2),
                    "delta_xp_xi": round(delta_xp, 2),
                    "expected_surplus": float(bid_eval["expected_surplus"]),
                    "score_per_euro": float(bid_eval["expected_surplus"]) / max(b_amt, 250_000),
                    "regime": bid_eval["regime"],
                    "win_prob_est": bid_eval["win_prob_est"],
                })

        # 3B. Evaluate 24/7 Surgical Clause Snipes (`ClauseSniper`)
        # Strict Financial Guardrail: Release clauses settle instantly at a multiple over VM (125%-175%).
        # Therefore, clauses must ALWAYS be funded by positive liquid cash (never by 25% credit leverage!),
        # ensuring we never risk Friday debt emergencies from paying unhedged clause premiums.
        clause_budget = max(0.0, working_cash - 300_000.0)
        clause_targets = self.clause_sniper.scan_offensive_targets(
            df_market_today=df_market_all,
            xp_table=xp_table,
            efr_map=efr_map,
            my_manager_name=my_manager_name,
            my_current_xi_floor_by_pos=xi_floor_by_pos,
            locked_player_ids=(clause_locked_ids or set()) | owned_ids,
            recent_match_shock_map=recent_match_shock_map,
            max_budget=min(max_official_credit_bid, max(0.0, clause_budget)),
            idle_cash_eur=max(0.0, working_cash),
        )

        clause_actions: List[Dict[str, Any]] = []
        for ct in clause_targets:
            clause_actions.append({
                "action_type": "CLAUSE_SNIPE",
                "player_id": ct["player_id"],
                "nombre": ct["nombre"],
                "position": ct["position"],
                "seller": ct["victim_manager"],
                "market_price": ct["market_price"],
                "amount": ct["clause_amount"],
                "efr_72h": ct["efr_72h"],
                "xp_best": ct["xp_best"],
                "delta_xp_xi": ct["delta_xp_xi"],
                "expected_surplus": float(ct["net_alpha_eur"]),
                "score_per_euro": float(ct["alpha_roi"]),
                "regime": ct["snipe_type"],
                "win_prob_est": 1.0,
            })

        # 3C. Allocate Budget Jointly Across Clause Snipes & Market Bids (Hierarchical Priority)
        def _acq_tier_key(x: Dict[str, Any]) -> Tuple[int, float]:
            dx = float(x.get("delta_xp_xi", 0.0))
            is_empty_pos = (xi_floor_by_pos.get(x["position"], 3.0) <= 0.1)
            reg = str(x.get("regime", ""))

            # Tier 3: Emergency fill of an empty XI slot (-4 pts penalty avoidance)
            if is_empty_pos:
                tier = 3
            # Tier 2: Star Starting-XI Upgrade (+0.40 xP or STAR_UNDERCLAUSED_SNIPE)
            elif (dx >= 0.65 or reg == "STAR_UNDERCLAUSED_SNIPE" or (reg == "CORE_XI_UPGRADE" and dx >= 0.35)):
                tier = 2
            # Tier 1: Moderate XI upgrade (dx >= 0.25) or high-ROI speculative flip (efr_72h >= 80k)
            elif dx >= 0.25 or float(x.get("efr_72h", 0.0)) >= 80_000:
                tier = 1
            # Tier 0: Minor flips / micro-breakouts
            else:
                tier = 0

            # Within tier, value combines points surplus + financial ROI
            composite_score = float(x["score_per_euro"]) * 0.40 + (float(x["expected_surplus"]) / 1_000_000.0) * 0.60
            return (tier, composite_score)

        combined_acqs = sorted(clause_actions + bid_candidates, key=_acq_tier_key, reverse=True)

        selected_bids: List[Dict[str, Any]] = []
        selected_clauses: List[Dict[str, Any]] = []
        rem_budget = float(safe_acquisition_budget)
        rem_clause_budget = float(max(0.0, clause_budget))
        rem_slots = open_roster_slots
        seen_acq_pids: Set[int] = set()

        for acq in combined_acqs:
            if rem_slots <= 0 or rem_budget < 150_000:
                break
            pid = int(acq["player_id"])
            if pid in seen_acq_pids:
                continue
            amt = float(acq["amount"])

            if acq["action_type"] == "CLAUSE_SNIPE":
                # Limit to at most 2 surgical clause snipes per day to preserve liquidity discipline
                if len(selected_clauses) >= 2 or amt > rem_clause_budget or amt > rem_budget:
                    continue
                selected_clauses.append(acq)
                rem_clause_budget -= amt
                rem_budget -= amt
                rem_slots -= 1
                seen_acq_pids.add(pid)
            else:
                # For sealed bids, weight budget reservation by win probability (min 0.55x so we never over-leverage!)
                reserve_weight = 1.0 if is_kickoff_day else max(0.65, float(acq["win_prob_est"]))
                eff_cost = amt * reserve_weight
                if amt <= max_official_credit_bid and eff_cost <= rem_budget and len(selected_bids) < 5:
                    selected_bids.append(acq)
                    rem_budget -= eff_cost
                    if reserve_weight >= 0.75:
                        rem_slots -= 1
                    seen_acq_pids.add(pid)

        # Step 4: Defensive Shields & Preventive Market Listings (0€ Option Harvest)
        post_clause_cash = working_cash - sum(c["amount"] for c in selected_clauses)
        shields = self.clause_sniper.scan_defensive_shields(
            my_squad_df=final_sq,
            xp_table=xp_table,
            efr_map=efr_map,
            max_rival_purchasing_power=max_rival_purchasing_power,
            available_cash_surplus=max(0.0, post_clause_cash - sum(b["amount"] for b in selected_bids)),
        )

        preventive_listings: List[Dict[str, Any]] = []
        if self.preventive_listing and not final_sq.empty:
            for _, r in final_sq.iterrows():
                pid = int(r["player_id"])
                if pid in locked_sell or pid in untouchables:
                    continue
                price = safe_num(r.get("precio", 150_000), 150_000.0)
                # List at +5% over VM so rivals don't snatch cheap while Computer (`Mercado`) still sends its +-5% offer!
                ask_target = int(round(price * 1.05, -4))
                preventive_listings.append({
                    "player_id": pid,
                    "nombre": str(r.get("nombre", "")),
                    "asking_price": max(150_000, ask_target),
                })

        return {
            "date": current_date,
            "is_kickoff_day": is_kickoff_day,
            "starting_cash": round(current_cash),
            "post_sales_cash": round(working_cash),
            "safe_acquisition_budget": round(safe_acquisition_budget),
            "routine_sales": routine_sales,
            "solvency_sales": solvency_sales,
            "all_sales": routine_sales + solvency_sales,
            "market_bids": selected_bids,
            "clause_snipes": selected_clauses,
            "defensive_shields": shields,
            "preventive_listings": preventive_listings,
            "lineup": {
                "formation": milp_sol["formation"],
                "player_ids": milp_sol["lineup_ids"],
                "slots": milp_sol["lineup_slots"],
                "expected_points": milp_sol["expected_xi_points"],
            },
            "is_solvent": milp_sol["is_solvent"],
        }
