"""
Joint MILP Portfolio, Credit-Leverage & Starting-XI Optimizer (`PortfolioOptimizer` v3)
======================================================================================
Uses `scipy.optimize.milp` (HiGHS exact C++ solver) + Microeconomic Scarcity/NPV to solve:
1. Optimal Legal Starting XI + Multiposition Back-Slotting across all 7 official
   Biwenger formations (`4-5-1`, `3-5-2`, `4-4-2`, `4-3-3`, `3-4-3`, `5-4-1`, `5-3-2`).
2. Pre-Kickoff Debt-Clearing Knapsack: guarantees `post_sale_cash >= 0 €` before
   matchday kickoff while protecting `FRANCHISE_CORE` & `INTOCABLES`.
3. Anti-Overfitting Multi-Round Hold-vs-Sell NPV (`evaluate_hold_vs_sell_npv`) across
   real calendar dates (including FIFA breaks) + Multi-Day Offer-Roll Patience.
4. Official `marketMaxUserSales` (`<= 5` simultaneous listings) Optimization &
   Top-5 Collateral-Backed 25% Credit-Line Arbitrage.
"""

from typing import Dict, List, Any, Optional, Set, Tuple
import numpy as np
import pandas as pd
from scipy.optimize import milp, LinearConstraint, Bounds

from src.config import StrategySettings
from src.tools.strategy_engine.league_rules import LeagueRulesEngine, RoundPhaseContext
from src.tools.strategy_engine.xp_model import ExpectedPointsEngine
from src.tools.strategy_engine.auction_model import AuctionBidModel
from src.tools.strategy_engine.clause_sniper import ClauseSniper
from src.tools.strategy_engine.replacement_cost import (
    MarketScarcityEngine,
    evaluate_hold_vs_sell_npv,
)
from src.tools.strategy_engine.strategy_memory import StrategyMemoryLedger
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
    """Exact MILP & Quantitative Strategy Decision Engine (Fantasy Champion v3)."""

    def __init__(
        self,
        xp_engine: Optional[ExpectedPointsEngine] = None,
        auction_model: Optional[AuctionBidModel] = None,
        clause_sniper: Optional[ClauseSniper] = None,
        credit_limit_ratio: Optional[float] = None,
        max_squad_size: Optional[int] = None,
        preventive_listing: bool = True,
        rules: Optional[LeagueRulesEngine] = None,
        important_sale_min_eur: Optional[int] = None,
    ):
        self.rules = rules or LeagueRulesEngine.load()
        self.xp_engine = xp_engine or ExpectedPointsEngine()
        self.auction_model = auction_model or AuctionBidModel()
        self.clause_sniper = clause_sniper or ClauseSniper(
            policy=StrategySettings.CLAUSE_POLICY,
            rules=self.rules,
        )
        self.credit_limit_ratio = (
            float(credit_limit_ratio)
            if credit_limit_ratio is not None
            else float(self.rules.credit_limit_ratio)
        )
        self.max_squad_size = (
            int(max_squad_size)
            if max_squad_size is not None
            else min(int(self.rules.team_max_size), int(StrategySettings.MAX_SQUAD_SIZE))
        )
        self.preventive_listing = bool(preventive_listing)
        self.important_sale_min_eur = (
            int(important_sale_min_eur)
            if important_sale_min_eur is not None
            else int(StrategySettings.IMPORTANT_SALE_MIN_EUR)
        )

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
        core_pillar_ids: Optional[Set[int]] = None,
    ) -> Dict[str, Any]:
        """
        Solves the exact Mixed-Integer Linear Program choosing:
          - `y_i in {0, 1}`: whether to sell player `i` today (at firm offer `O_i`)
          - `x_{i, l} in {0, 1}`: whether to field player `i` in line `l in {GK, DF, MF, FW}`
          - `f_k in {0, 1}`: which of the 7 official Biwenger formations is active
          - `slack_l >= 0`: empty-slot slack (heavily penalized so 11 players are always fielded)
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
        core_pillars = core_pillar_ids or set()

        debt_to_clear = max(0.0, -float(current_cash)) if must_be_solvent else 0.0

        # Check if non-untouchable firm offers alone can clear 100% of Friday debt;
        # if yes, hard-lock untouchables (`ub = 0`) so user untouchables are NEVER sold!
        non_untouch_liquidity = 0.0
        if debt_to_clear > 0:
            for _, r in sq.iterrows():
                pid = int(r["player_id"])
                if pid in locked_sell or pid in untouchables:
                    continue
                if pid in offers and offers[pid] > 0:
                    non_untouch_liquidity += float(offers[pid])
                elif len(offers) == 0:
                    non_untouch_liquidity += safe_num(r.get("precio", 0), 0.0) * 0.98
        hard_lock_untouchables = (non_untouch_liquidity >= debt_to_clear)

        n_vars = 5 * n + 12
        c = np.zeros(n_vars, dtype=float)
        lb = np.zeros(n_vars, dtype=float)
        ub = np.ones(n_vars, dtype=float)
        integrality = np.ones(n_vars, dtype=int)

        for l_idx in range(4):
            ub[5 * n + 7 + l_idx] = 5.0
        ub[5 * n + 11] = 1e9
        integrality[5 * n + 11] = 0

        sale_proceeds = np.zeros(n, dtype=float)

        for i, r in sq.iterrows():
            pid = int(r["player_id"])
            price = safe_num(r.get("precio", 150_000), 150_000.0)
            subida = safe_num(r.get("subida_24h", 0), 0.0)
            efr = float(efr_map.get(pid, subida * 2.4))
            xp_info = xp_idx.get(pid, {})
            is_avail = bool(xp_info.get("is_available", True))
            valid_lines = self.xp_engine.get_valid_lines(pid, r.get("posicion", ""))

            if pid in offers and offers[pid] > 0:
                proceeds = float(offers[pid])
                has_firm_offer = True
            else:
                proceeds = 0.0 if must_be_solvent and len(offers) > 0 and pid not in offers else price * 0.98
                has_firm_offer = False
            sale_proceeds[i] = proceeds

            can_sell = (pid not in locked_sell) and (proceeds > 0)
            if pid in untouchables and hard_lock_untouchables:
                can_sell = False
            if not must_be_solvent:
                can_sell = False
            ub[4 * n + i] = 1.0 if can_sell else 0.0

            for l_idx, ln in enumerate(LINES):
                var_idx = 4 * i + l_idx
                if ln not in valid_lines:
                    ub[var_idx] = 0.0
                else:
                    xp_val = float(xp_info.get(f"xp_{ln}", 0.0)) if is_avail else 0.01
                    depth_tie = (3 - l_idx) * 0.015
                    c[var_idx] = -(100.0 * xp_val + depth_tie)

            xp_best = float(xp_info.get("xp_best", 0.0))
            xp_healthy = float(xp_info.get("xp_healthy", xp_best))
            offer_prem = (proceeds - price) if has_firm_offer else 0.0
            net_future_eur = efr - offer_prem
            pillar_bonus = 75.0 if pid in core_pillars else 0.0
            untouch_bonus = 250.0 if pid in untouchables else 0.0
            hold_score = (
                (net_future_eur / 50_000.0) * 5.5
                + max(xp_best, xp_healthy * 0.85) * 4.8
                + pillar_bonus
                + untouch_bonus
            )
            c[4 * n + i] = hold_score + 0.30

        for k, (_, _, n_mf, n_fw) in enumerate(OFFICIAL_FORMATIONS):
            c[5 * n + k] = -(0.005 * n_mf + 0.003 * n_fw)

        for l_idx in range(4):
            c[5 * n + 7 + l_idx] = 650.0

        c[5 * n + 11] = 0.05

        rows_A = []
        rows_lb = []
        rows_ub = []

        # 1. Exactly 1 formation chosen
        row_f = np.zeros(n_vars)
        row_f[5 * n : 5 * n + 7] = 1.0
        rows_A.append(row_f)
        rows_lb.append(1.0)
        rows_ub.append(1.0)

        # 2. Player coherence
        for i in range(n):
            row_p = np.zeros(n_vars)
            row_p[4 * i : 4 * i + 4] = 1.0
            row_p[4 * n + i] = 1.0
            rows_A.append(row_p)
            rows_lb.append(0.0)
            rows_ub.append(1.0)

        # 3. GK count
        row_gk = np.zeros(n_vars)
        for i in range(n):
            row_gk[4 * i + 0] = 1.0
        row_gk[5 * n + 7 + 0] = 1.0
        rows_A.append(row_gk)
        rows_lb.append(1.0)
        rows_ub.append(1.0)

        # DF, MF, FW counts
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

        # 4. Solvency Constraint
        if debt_to_clear > 0:
            row_solv = np.zeros(n_vars)
            for i in range(n):
                row_solv[4 * n + i] = sale_proceeds[i]
            row_solv[5 * n + 11] = 1.0
            rows_A.append(row_solv)
            rows_lb.append(debt_to_clear)
            rows_ub.append(1e12)

        c = np.nan_to_num(c, nan=0.0, posinf=1e6, neginf=-1e6)
        sale_proceeds = np.nan_to_num(sale_proceeds, nan=0.0)
        A_mat = np.nan_to_num(np.vstack(rows_A), nan=0.0)
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
                mkt_p = safe_num(r.get("precio", inc), inc)
                cash_after += inc
                sales_to_execute.append({
                    "player_id": pid,
                    "nombre": str(r.get("nombre", "")),
                    "sale_price": int(round(inc)),
                    "market_price": int(round(mkt_p)),
                    "reason": "FRIDAY_SOLVENCY_MILP",
                    "requires_human_approval": False,  # Emergency pre-kickoff solvency is authorized in SEMI_AUTO
                })

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

    def select_top_k_preventive_listings(
        self,
        final_sq: pd.DataFrame,
        role_by_pid: Dict[int, str],
        npv_by_pid: Dict[int, Dict[str, Any]],
        efr_map: Dict[int, float],
        projected_max_debt_eur: float,
        sell_locked_ids: Set[int],
        untouchable_ids: Set[int],
        force_sell_ids: Optional[Set[int]] = None,
        max_slots: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Selects at most `K = rules.market_max_user_sales` (e.g., 5) players to list on the market:
          - Excludes `untouchables`, `sell_locked_ids`, and `FRANCHISE_CORE` (unless in `force_sell_ids`).
          - Priority 1: Non-core liquidity collateral covering `>= 1.25x` of `projected_max_debt_eur`.
          - Priority 2: Active disposals (`force_sell_ids` or `NPV_SELL > NPV_HOLD`).
          - Priority 3: Plateauing `SPECULATIVE_FLIP` & `BENCH_PATCH` for `+5%` Computer offer harvesting.
        """
        if not self.preventive_listing or final_sq is None or final_sq.empty:
            return []

        k_limit = int(max_slots if max_slots is not None else self.rules.market_max_user_sales)
        if k_limit <= 0:
            return []

        forced = force_sell_ids or set()
        candidates: List[Dict[str, Any]] = []

        for _, r in final_sq.iterrows():
            pid = int(r["player_id"])
            if pid in untouchable_ids:
                continue
            # Only skip sell_locked_ids if league lock > 1 day (with standard 1-day lock, players bought today unlock tomorrow at 06:00!)
            if pid in sell_locked_ids and int(self.rules.days_for_sale) > 1:
                continue
            role = role_by_pid.get(pid, "BENCH_PATCH")
            if role == "FRANCHISE_CORE" and pid not in forced:
                continue

            price = safe_num(r.get("precio", 150_000), 150_000.0)
            subida = safe_num(r.get("subida_24h", 0), 0.0)
            efr = float(efr_map.get(pid, subida * 2.4))
            npv_info = npv_by_pid.get(pid, {})
            want_sell = bool(pid in forced or npv_info.get("recommend_sell", False))

            # Option harvest attractiveness: high VM * 4.5% potential bonus minus ongoing momentum
            option_score = price * 0.045 - max(0.0, efr * 0.22)
            # Collateral efficiency: liquid cash provided per unit of structural role importance
            role_penalty = 0.0 if role in ("SPECULATIVE_FLIP", "BENCH_PATCH") else 1.0
            collateral_score = (price * 0.955) / (1.0 + 2.5 * role_penalty)

            ask_target = int(round(price * 1.05, -4))
            candidates.append({
                "player_id": pid,
                "nombre": str(r.get("nombre", "")),
                "asking_price": max(150_000, ask_target),
                "market_price": int(round(price)),
                "role": role,
                "want_sell": want_sell,
                "option_score": option_score,
                "collateral_score": collateral_score,
                "conservative_proceeds": price * 0.955,
            })

        if not candidates:
            return []

        selected: List[Dict[str, Any]] = []
        selected_pids: Set[int] = set()
        covered_collateral = 0.0
        target_collateral = max(0.0, float(projected_max_debt_eur) * 1.25)

        # Priority 1 (WHEN IN DEBT / PENDING BIDS): Solvency collateral MUST cover >= 1.25x worst-case debt FIRST
        # (prevents five 150k€ micro-disposals from crowding out 5 slots and leaving Friday debt uncovered!)
        if target_collateral > 0 and len(selected) < k_limit:
            coll_pool = sorted(
                candidates,
                key=lambda x: (x["want_sell"] and x["conservative_proceeds"] >= target_collateral / max(1, k_limit), x["collateral_score"]),
                reverse=True,
            )
            for c in coll_pool:
                if len(selected) >= k_limit or covered_collateral >= target_collateral:
                    break
                c["listing_reason"] = "SOLVENCY_COLLATERAL"
                selected.append(c)
                selected_pids.add(c["player_id"])
                covered_collateral += c["conservative_proceeds"]

        # Priority 2: Active disposals (`want_sell`) in remaining slots
        disposals = sorted(
            [c for c in candidates if c["want_sell"] and c["player_id"] not in selected_pids],
            key=lambda x: (x["role"] != "STRUCTURAL_STARTER", x["market_price"]),
            reverse=True,
        )
        for c in disposals:
            if len(selected) >= k_limit:
                break
            c["listing_reason"] = "DISPOSAL_NPV"
            selected.append(c)
            selected_pids.add(c["player_id"])
            covered_collateral += c["conservative_proceeds"]

        # Priority 3: Fill remaining slots up to K with highest option-harvest flips/bench assets
        if len(selected) < k_limit:
            flip_pool = sorted(
                [c for c in candidates if c["player_id"] not in selected_pids and c["role"] != "STRUCTURAL_STARTER"],
                key=lambda x: x["option_score"],
                reverse=True,
            )
            for c in flip_pool:
                if len(selected) >= k_limit:
                    break
                c["listing_reason"] = "OPTION_HARVEST_FLIP"
                selected.append(c)
                selected_pids.add(c["player_id"])

        return [
            {
                "player_id": s["player_id"],
                "nombre": s["nombre"],
                "asking_price": s["asking_price"],
                "market_price": s["market_price"],
                "role": s["role"],
                "listing_reason": s.get("listing_reason", "OPTION_HARVEST_FLIP"),
            }
            for s in selected[:k_limit]
        ]

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
        phase_ctx: Optional[RoundPhaseContext] = None,
        clauses_open: Optional[bool] = None,
        df_calendar: Optional[pd.DataFrame] = None,
        memory_ledger: Optional[StrategyMemoryLedger] = None,
        force_sell_ids: Optional[Set[int]] = None,
        blacklist_buy_ids: Optional[Set[int]] = None,
        priority_target_ids: Optional[Set[int]] = None,
    ) -> Dict[str, Any]:
        """
        Full daily autonomous decision cycle (v3):
          Step 1: Scarcity & Multi-Round Hold-vs-Sell NPV + Offer-Roll Patience + Peak Offer Harvest
          Step 2: Joint MILP Starting XI (+ Pre-Kickoff Debt Solvency when `must_be_solvent`)
          Step 3: Top-5 Collateral-Backed Credit-Leveraged Acquisitions (Bids + Surgical Clauses)
          Step 4: 1:1 Defensive Shielding & Top-K (`<= 5`) Preventive Market Listings + Thesis Update
        """
        offers = dict(active_offers_map or {})
        locked_sell = set(sell_locked_ids or set())
        untouchables = set(untouchable_ids or set())
        forced_sells = set(force_sell_ids or set()) - untouchables
        blacklist_buys = set(blacklist_buy_ids or set())
        priority_buys = set(priority_target_ids or set())
        indebted_rivals = indebted_rival_managers or set()

        # Resolve temporal phase flags
        if phase_ctx is not None:
            must_be_solvent = bool(phase_ctx.must_be_solvent_now)
            can_use_credit = bool(phase_ctx.can_use_credit_leverage)
            eff_days_to_kickoff = int(phase_ctx.days_to_kickoff)
            eff_clauses_open = bool(phase_ctx.clauses_open) if clauses_open is None else bool(clauses_open)
        else:
            must_be_solvent = bool(is_kickoff_day)
            can_use_credit = not bool(is_kickoff_day)
            eff_days_to_kickoff = int(days_to_kickoff)
            if clauses_open is not None:
                eff_clauses_open = bool(clauses_open)
            else:
                lock_days = int(np.ceil(self.rules.clause_disabled_hours / 24.0))
                eff_clauses_open = (not is_kickoff_day) and (eff_days_to_kickoff >= lock_days)

        sq = squad_df.copy() if (squad_df is not None and not squad_df.empty) else pd.DataFrame()
        if not sq.empty:
            sq["player_id"] = pd.to_numeric(sq["player_id"], errors="coerce").fillna(0).astype(int)
            sq = sq.drop_duplicates(subset=["player_id"]).reset_index(drop=True)

        xp_idx = xp_table.set_index("player_id").to_dict("index") if (xp_table is not None and not xp_table.empty) else {}

        # Build live MarketScarcityEngine
        scarcity_engine = MarketScarcityEngine(
            df_market_today=df_market_all,
            xp_table=xp_table,
            my_manager_name=my_manager_name,
            rules=self.rules,
            clause_locked_ids=clause_locked_ids,
        )

        # Baseline MILP XI before sales to identify current XI starters & bench backup xP per line
        base_milp = self.solve_joint_lineup_and_solvency_milp(
            squad_df=sq,
            xp_table=xp_table,
            efr_map=efr_map,
            current_cash=current_cash,
            must_be_solvent=False,
        )
        core_xi_ids = {s["player_id"] for s in base_milp["lineup_slots"]}

        # Compute realistic bench backup xP per line (`GK`, `DF`, `MF`, `FW`)
        bench_xp_by_line: Dict[str, float] = {"GK": 0.0, "DF": 2.2, "MF": 2.4, "FW": 2.2}
        if not sq.empty:
            for ln in LINES:
                b_vals = []
                for _, r in sq.iterrows():
                    pid = int(r["player_id"])
                    if pid in core_xi_ids:
                        continue
                    x_inf = xp_idx.get(pid, {})
                    if x_inf.get("is_available", True) and ln in self.xp_engine.get_valid_lines(pid, r.get("posicion", "")):
                        b_vals.append(float(x_inf.get(f"xp_{ln}", 0.0)))
                bench_xp_by_line[ln] = max(b_vals) if b_vals else (0.0 if ln == "GK" else 1.8)

        prev_theses = memory_ledger.get_asset_theses_map() if memory_ledger is not None else {}
        role_by_pid: Dict[int, str] = {}
        npv_by_pid: Dict[int, Dict[str, Any]] = {}
        theses_rows: List[Dict[str, Any]] = []

        # Evaluate Asset Role & Multi-Round Hold-vs-Sell NPV for every owned player
        if not sq.empty:
            pt_shadow = max(65_000.0, float(self.rules.bonus_per_point_eur) * 1.7)
            for _, r in sq.iterrows():
                pid = int(r["player_id"])
                price = safe_num(r.get("precio", 150_000), 150_000.0)
                subida = safe_num(r.get("subida_24h", 0), 0.0)
                efr_72h = float(efr_map.get(pid, subida * 2.4))
                xp_info = xp_idx.get(pid, {})
                is_avail = bool(xp_info.get("is_available", True))
                estado = str(xp_info.get("estado", r.get("estado", "ok")) or "ok")
                xp_best = float(xp_info.get("xp_best", 0.0))
                xp_healthy = float(xp_info.get("xp_healthy", max(xp_best, 2.5)))
                p_start_h = float(xp_info.get("p_start_healthy", xp_info.get("p_start", 0.5)))
                valid_lines = self.xp_engine.get_valid_lines(pid, r.get("posicion", ""))
                best_ln = str(xp_info.get("best_line", sorted(valid_lines)[0] if valid_lines else "MF"))

                offer_or_est = float(offers[pid]) if (pid in offers and offers[pid] > 0) else price * 0.99
                purchase_p = safe_num(r.get("precio_compra", prev_theses.get(pid, {}).get("precio_compra", 0)), 0.0)

                npv_res = evaluate_hold_vs_sell_npv(
                    player_id=pid,
                    current_date=current_date,
                    market_price=price,
                    sale_offer_eur=offer_or_est,
                    efr_72h=efr_72h,
                    is_available=is_avail,
                    estado=estado,
                    xp_current=xp_best,
                    xp_healthy=xp_healthy,
                    p_start_healthy=p_start_h,
                    valid_lines=valid_lines,
                    bench_backup_xp=bench_xp_by_line.get(best_ln, 2.2),
                    scarcity_engine=scarcity_engine,
                    df_calendar=df_calendar,
                    team_name=str(r.get("equipo", xp_info.get("equipo", ""))),
                    purchase_price=purchase_p if purchase_p > 0 else None,
                    point_shadow_value_eur=pt_shadow,
                    clauses_open=eff_clauses_open,
                )
                npv_by_pid[pid] = npv_res

                rep_prof = scarcity_engine.compute_replacement_profile(
                    player_id=pid,
                    natural_pos=best_ln,
                    xp_healthy=xp_healthy,
                    market_price=price,
                    valid_lines=valid_lines,
                )
                role = scarcity_engine.classify_asset_role(
                    player_id=pid,
                    price=price,
                    xp_healthy=xp_healthy,
                    p_start_healthy=p_start_h,
                    efr_72h=efr_72h,
                    in_current_xi=(pid in core_xi_ids),
                    rep_profile=rep_prof,
                    is_untouchable=(pid in untouchables),
                )
                role_by_pid[pid] = role

                theses_rows.append({
                    "player_id": pid,
                    "nombre": str(r.get("nombre", "")),
                    "rol": role,
                    "fecha_compra": str(r.get("acquired_date", prev_theses.get(pid, {}).get("fecha_compra", "")) or ""),
                    "precio_compra": int(round(purchase_p if purchase_p > 0 else price)),
                    "precio_actual": int(round(price)),
                    "clausula_actual": int(round(safe_num(r.get("clausula_actual", 0), 0.0) or self.rules.compute_official_clause(max(purchase_p, price)))),
                    "coste_reposicion": npv_res["replacement_cost_eur"],
                    "escasez_tier": npv_res["scarcity_tier"],
                    "npv_hold": npv_res["npv_hold_eur"],
                    "npv_sell": npv_res["npv_sell_eur"],
                    "recomendacion_npv": "SELL" if npv_res["recommend_sell"] else "HOLD",
                    "ultima_oferta": int(round(offers.get(pid, 0))),
                    "intocable": pid in untouchables,
                    "forzar_venta": pid in forced_sells,
                    "tesis_resumen": (
                        f"{role} | xP_sano={xp_healthy:.1f} | Escasez={npv_res['scarcity_tier']} "
                        f"(bajas={npv_res['missed_rounds_est']}J, NPV_Hold={npv_res['npv_hold_eur']/1e6:.2f}M vs "
                        f"NPV_Sell={npv_res['npv_sell_eur']/1e6:.2f}M)"
                    ),
                })

        # Step 1: Microeconomic NPV Disposals, Multi-Day Offer-Roll Patience & Peak Offer Arbitrage
        routine_sales: List[Dict[str, Any]] = []
        sold_ids: Set[int] = set()
        working_cash = float(current_cash)

        if not sq.empty:
            # Measure actual upgrade ceiling available on TODAY's live market board (prevents blind liquidation into empty boards!)
            live_board_best_xp_by_line: Dict[str, float] = {ln: 0.0 for ln in LINES}
            if market_listings_df is not None and not market_listings_df.empty and "player_id" in market_listings_df.columns:
                for raw_lpid in market_listings_df["player_id"].tolist():
                    try:
                        lpid = int(float(raw_lpid))
                    except (TypeError, ValueError):
                        continue
                    lx = xp_idx.get(lpid)
                    if lx and lx.get("is_available", False):
                        for ln in self.xp_engine.get_valid_lines(lpid):
                            v_ln = float(lx.get(f"xp_{ln}", lx.get("xp_best", 0.0)))
                            if v_ln > live_board_best_xp_by_line[ln]:
                                live_board_best_xp_by_line[ln] = v_ln

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
                xp_best = float(xp_info.get("xp_best", 0.0))
                valid_lns_p = self.xp_engine.get_valid_lines(pid, r.get("posicion", ""))
                is_gk = "GK" in valid_lns_p
                role = role_by_pid.get(pid, "BENCH_PATCH")
                npv_res = npv_by_pid.get(pid, {})
                purchase_p = safe_num(r.get("precio_compra", prev_theses.get(pid, {}).get("precio_compra", 0)), 0.0)

                if is_gk and gk_count <= 1:
                    continue
                if is_avail and fit_count <= 11 and pid not in forced_sells:
                    continue

                offer_pct = (offer_amt - price) / max(price, 1.0)
                prev_rolls = int(prev_theses.get(pid, {}).get("offer_rolls", 0) or 0)

                acq_date = r.get("acquired_date", None)
                held_days = 99
                if acq_date is not None and str(acq_date) not in ("", "nan", "None"):
                    try:
                        held_days = (pd.Timestamp(current_date) - pd.Timestamp(str(acq_date))).days
                    except (ValueError, TypeError):
                        held_days = 99

                is_forced = (pid in forced_sells)
                was_claused_premium = (purchase_p >= 1.20 * price) and (held_days < 8)
                is_depreciating = (efr_72h <= -35_000.0) or (subida <= -15_000.0)
                board_best_here = max((live_board_best_xp_by_line.get(ln, 0.0) for ln in valid_lns_p), default=0.0)
                needs_slot_or_liquidity = (
                    (len(sq) - len(sold_ids) >= self.max_squad_size - 1)
                    or (working_cash < 2_500_000.0 and board_best_here > xp_best + 0.85)
                )

                # Rule 1A: Microeconomic NPV Disposal (`NPV_SELL > NPV_HOLD` across real calendar & replacement cost)
                # - Unavailable (injured/suspended) or actively depreciating assets: evaluated directly by NPV!
                # - Healthy, stable/appreciating assets: only proactively liquidated when we actually need a slot/cash for a live upgrade (never dumped into empty boards!)
                min_hold_days = 8 if was_claused_premium else 2
                can_proactively_rotate = (not is_avail) or is_depreciating or needs_slot_or_liquidity
                is_npv_disposal = (
                    bool(npv_res.get("recommend_sell", False))
                    and can_proactively_rotate
                    and ((not is_avail and int(npv_res.get("missed_rounds_est", 0)) >= 2) or held_days >= min_hold_days)
                )
                if is_npv_disposal and not is_forced:
                    # Multi-Day Offer-Roll Patience: reject lowball Computer offers (<-1.8%) when >=2 days to kickoff!
                    if (
                        is_avail
                        and offer_pct < -0.018
                        and eff_days_to_kickoff >= 2
                        and prev_rolls < 2
                        and efr_72h > -95_000
                    ):
                        is_npv_disposal = False

                # Rule 1B: Peak Computer Offer Arbitrage (+3.2%..+5.0% instant risk-free premium on non-core flip)
                is_peak_harvest = (
                    role in ("SPECULATIVE_FLIP", "BENCH_PATCH")
                    and pid not in core_xi_ids
                    and offer_pct >= 0.032
                    and (offer_amt - price) >= max(25_000.0, efr_72h * 0.78)
                    and xp_best < 4.9
                )

                if is_forced or is_npv_disposal or is_peak_harvest:
                    requires_human = bool(
                        role in ("FRANCHISE_CORE", "STRUCTURAL_STARTER")
                        or price >= self.important_sale_min_eur
                    ) and not is_forced
                    reason = (
                        "USER_DIRECTIVE_SELL"
                        if is_forced
                        else ("NPV_OPTIMAL_DISPOSAL" if is_npv_disposal else "PEAK_OFFER_ARBITRAGE")
                    )
                    routine_sales.append({
                        "player_id": pid,
                        "nombre": str(r.get("nombre", "")),
                        "sale_price": int(round(offer_amt)),
                        "market_price": int(round(price)),
                        "offer_premium_pct": round(offer_pct * 100.0, 2),
                        "asset_role": role,
                        "requires_human_approval": requires_human,
                        "npv_hold_eur": npv_res.get("npv_hold_eur", int(round(price))),
                        "npv_sell_eur": npv_res.get("npv_sell_eur", int(round(offer_amt))),
                        "missed_rounds_est": npv_res.get("missed_rounds_est", 0),
                        "replacement_cost_eur": npv_res.get("replacement_cost_eur", int(round(price))),
                        "reason": reason,
                    })
                    sold_ids.add(pid)
                    working_cash += offer_amt
                    if is_avail:
                        fit_count -= 1
                    if is_gk:
                        gk_count -= 1

        remaining_sq = sq[~sq["player_id"].isin(sold_ids)].copy() if not sq.empty else pd.DataFrame()
        remaining_offers = {k: v for k, v in offers.items() if k not in sold_ids}
        core_pillar_ids = {pid for pid, rl in role_by_pid.items() if rl == "FRANCHISE_CORE"}

        # Step 2: Joint MILP Starting XI (+ Mandatory Solvency when `must_be_solvent`)
        milp_sol = self.solve_joint_lineup_and_solvency_milp(
            squad_df=remaining_sq,
            xp_table=xp_table,
            efr_map=efr_map,
            current_cash=working_cash,
            must_be_solvent=must_be_solvent,
            offer_map=remaining_offers,
            sell_locked_ids=locked_sell,
            untouchable_ids=untouchables,
            core_pillar_ids=core_pillar_ids,
        )

        solvency_sales = milp_sol["sales_to_execute"]
        for s in solvency_sales:
            spid = int(s["player_id"])
            s["asset_role"] = role_by_pid.get(spid, "BENCH_PATCH")
            sold_ids.add(spid)
        working_cash = float(milp_sol["projected_cash"])
        final_sq = sq[~sq["player_id"].isin(sold_ids)].copy() if not sq.empty else pd.DataFrame()

        if memory_ledger is not None:
            kept_theses = [t for t in theses_rows if int(t["player_id"]) not in sold_ids]
            memory_ledger.update_squad_theses(kept_theses, current_date=current_date)

        # Compute XI floor xP by position
        xi_floor_by_pos: Dict[str, float] = {"GK": 2.5, "DF": 3.0, "MF": 3.2, "FW": 3.2}
        slots_by_line: Dict[str, List[float]] = {"GK": [], "DF": [], "MF": [], "FW": []}
        for sl in milp_sol["lineup_slots"]:
            slots_by_line[sl["linea"]].append(float(sl["xp"]))
        for ln, vals in slots_by_line.items():
            xi_floor_by_pos[ln] = min(vals) if vals else 0.0

        # Step 3: Safe Credit-Leveraged Purchasing Power Bounded by Top-K (`<= 5`) Listable Non-Core Collateral!
        rem_squad_value = float(pd.to_numeric(final_sq["precio"], errors="coerce").fillna(0).sum()) if not final_sq.empty else 0.0
        max_official_credit_bid = self.rules.compute_max_bid(working_cash, rem_squad_value)
        if self.credit_limit_ratio != self.rules.credit_limit_ratio:
            max_official_credit_bid = working_cash + self.credit_limit_ratio * rem_squad_value

        if not can_use_credit:
            safe_acquisition_budget = max(0.0, working_cash)
            top_k_liquid_collateral = 0.0
        else:
            # Because Biwenger caps simultaneous market sales at `market_max_user_sales` (5),
            # our pre-kickoff liquidation capacity is bounded by the TOP K non-core players at 0.95 * VM!
            k_slots = max(1, int(self.rules.market_max_user_sales))
            non_core_values: List[float] = []
            if not final_sq.empty:
                for _, r in final_sq.iterrows():
                    pid = int(r["player_id"])
                    if eff_days_to_kickoff <= 1 and pid in locked_sell and int(self.rules.days_for_sale) > 1:
                        continue
                    rl = role_by_pid.get(pid, "BENCH_PATCH")
                    if rl != "FRANCHISE_CORE" and pid not in untouchables:
                        # Prioritize bench/flips (95% worst-case Computer offer) and discount structural starters (55%)
                        w = 0.95 if rl in ("SPECULATIVE_FLIP", "BENCH_PATCH") else 0.55
                        non_core_values.append(safe_num(r.get("precio", 0), 0.0) * w)
            non_core_values.sort(reverse=True)
            top_k_liquid_collateral = float(sum(non_core_values[:k_slots]))
            leverage_factor = 0.84 if eff_days_to_kickoff >= 2 else 0.72
            safe_debt_cap = top_k_liquid_collateral * leverage_factor
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
                if pid <= 0 or pid in owned_ids or pid in blacklist_buys:
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
                if pid in priority_buys:
                    delta_xp += 0.45  # User strategic priority boost
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
                    "requires_human_approval": False,  # Autonomous in SEMI_AUTO (with user notification)
                })

        # 3B. Evaluate Surgical Scarcity Clause Snipes (ONLY when `eff_clauses_open` & positive cash!)
        clause_budget = max(0.0, working_cash - 300_000.0) if eff_clauses_open else 0.0
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
            clauses_open=eff_clauses_open,
            scarcity_engine=scarcity_engine,
            blacklist_ids=blacklist_buys,
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
                "scarcity_tier": ct.get("scarcity_tier", "SCARCE"),
                "expected_surplus": float(ct["net_alpha_eur"]),
                "score_per_euro": float(ct["alpha_roi"]),
                "regime": ct["snipe_type"],
                "win_prob_est": 1.0,
                "requires_human_approval": True,  # Always gated by human in SEMI_AUTO!
            })

        # 3C. Joint Hierarchical Allocation
        def _acq_tier_key(x: Dict[str, Any]) -> Tuple[int, float]:
            dx = float(x.get("delta_xp_xi", 0.0))
            is_empty_pos = (xi_floor_by_pos.get(x["position"], 3.0) <= 0.1)
            reg = str(x.get("regime", ""))
            is_prio = int(x["player_id"]) in priority_buys

            if is_empty_pos:
                tier = 3
            elif is_prio or dx >= 0.65 or reg == "STAR_UNDERCLAUSED_SNIPE" or (reg == "CORE_XI_UPGRADE" and dx >= 0.35):
                tier = 2
            elif dx >= 0.25 or float(x.get("efr_72h", 0.0)) >= 80_000:
                tier = 1
            else:
                tier = 0

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
                if len(selected_clauses) >= 2 or amt > rem_clause_budget or amt > rem_budget:
                    continue
                selected_clauses.append(acq)
                rem_clause_budget -= amt
                rem_budget -= amt
                rem_slots -= 1
                seen_acq_pids.add(pid)
            else:
                # Day immediately before kickoff (`eff_days_to_kickoff <= 1`): use 1.0 worst-case fill so Friday solvency is 100% guaranteed!
                reserve_weight = 1.0 if (not can_use_credit or eff_days_to_kickoff <= 1) else max(0.68, float(acq["win_prob_est"]))
                eff_cost = amt * reserve_weight
                if amt <= max_official_credit_bid and eff_cost <= rem_budget and len(selected_bids) < 5:
                    selected_bids.append(acq)
                    rem_budget -= eff_cost
                    if reserve_weight >= 0.75:
                        rem_slots -= 1
                    seen_acq_pids.add(pid)

        # Step 4: Defensive Shields (`1:1` ratio) & Top-K (`<= 5`) Preventive Market Listings
        post_clause_cash = working_cash - sum(c["amount"] for c in selected_clauses)
        worst_case_bid_outflow = sum(float(b["amount"]) for b in selected_bids)
        shields = self.clause_sniper.scan_defensive_shields(
            my_squad_df=final_sq,
            xp_table=xp_table,
            efr_map=efr_map,
            max_rival_purchasing_power=max_rival_purchasing_power,
            available_cash_surplus=max(0.0, post_clause_cash - worst_case_bid_outflow),
            clauses_open=eff_clauses_open,
            scarcity_engine=scarcity_engine,
            clause_locked_ids=clause_locked_ids,
        )
        for sh in shields:
            sh["requires_human_approval"] = True

        projected_worst_debt = max(0.0, -(post_clause_cash - worst_case_bid_outflow))
        preventive_listings = self.select_top_k_preventive_listings(
            final_sq=final_sq,
            role_by_pid=role_by_pid,
            npv_by_pid=npv_by_pid,
            efr_map=efr_map,
            projected_max_debt_eur=projected_worst_debt,
            sell_locked_ids=locked_sell,
            untouchable_ids=untouchables,
            force_sell_ids=forced_sells,
            max_slots=self.rules.market_max_user_sales,
        )

        return {
            "date": current_date,
            "is_kickoff_day": must_be_solvent,
            "clauses_open": eff_clauses_open,
            "can_use_credit_leverage": can_use_credit,
            "days_to_kickoff": eff_days_to_kickoff,
            "starting_cash": round(current_cash),
            "post_sales_cash": round(working_cash),
            "top_k_liquid_collateral": round(top_k_liquid_collateral),
            "safe_acquisition_budget": round(safe_acquisition_budget),
            "routine_sales": routine_sales,
            "solvency_sales": solvency_sales,
            "all_sales": routine_sales + solvency_sales,
            "market_bids": selected_bids,
            "clause_snipes": selected_clauses,
            "defensive_shields": shields,
            "preventive_listings": preventive_listings,
            "asset_theses": [t for t in theses_rows if int(t["player_id"]) not in sold_ids],
            "scarcity_summary": scarcity_engine.get_league_scarcity_summary(),
            "lineup": {
                "formation": milp_sol["formation"],
                "player_ids": milp_sol["lineup_ids"],
                "slots": milp_sol["lineup_slots"],
                "expected_points": milp_sol["expected_xi_points"],
            },
            "is_solvent": milp_sol["is_solvent"],
        }
