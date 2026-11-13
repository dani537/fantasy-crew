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
from src.tools.strategy_engine.lifecycle_manager import LeagueLifecycleEngine, SeasonPhase
from src.tools.strategy_engine.injury_tactician import InjuryTacticalDecisionEngine, InjuryAction
from src.tools.strategy_engine.archetypes import StrategyArchetype, STRATEGY_ARCHETYPES
from src.tools.strategy_engine.posture_controller import StandingsPostureController, PostureModifiers
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
        archetype: Optional[StrategyArchetype] = None,
        protect_pillars_absolute: Optional[bool] = None,
    ):
        self.rules = rules or LeagueRulesEngine.load()
        self.xp_engine = xp_engine or ExpectedPointsEngine()
        self.auction_model = auction_model or AuctionBidModel()
        self.archetype = archetype or STRATEGY_ARCHETYPES.get(StrategySettings.ACTIVE_ARCHETYPE)
        if protect_pillars_absolute is not None:
            self.protect_pillars_absolute = bool(protect_pillars_absolute)
        elif self.archetype is not None:
            self.protect_pillars_absolute = bool(self.archetype.protect_pillars_absolute)
        else:
            self.protect_pillars_absolute = bool(StrategySettings.PROTECT_PILLARS_ABSOLUTE)

        # Initialize clause sniper with archetype parameters if provided
        if clause_sniper is not None:
            self.clause_sniper = clause_sniper
        else:
            c_pol = StrategySettings.CLAUSE_POLICY
            r_cap = StrategySettings.CLAUSE_RATIO_CAP
            min_avg = StrategySettings.CLAUSE_MIN_AVG_PTS
            min_dx = StrategySettings.CLAUSE_MIN_DELTA_XP
            res_pct = 0.80
            if self.archetype is not None:
                c_pol = str(getattr(self.archetype, "clause_policy", c_pol) or c_pol)
                r_cap = self.archetype.clause_ratio_cap
                min_avg = self.archetype.clause_min_avg_pts
                min_dx = self.archetype.clause_min_delta_xp
                res_pct = self.archetype.clause_cash_reserve_pct
            self.clause_sniper = ClauseSniper(
                policy=c_pol,
                rules=self.rules,
                league_ratio_cap=r_cap,
                clause_min_avg_pts=min_avg,
                clause_min_delta_xp=min_dx,
                clause_budget_reserve_pct=res_pct,
            )

        self.credit_limit_ratio = (
            float(credit_limit_ratio)
            if credit_limit_ratio is not None
            else float(self.rules.credit_limit_ratio)
        )
        if self.archetype is not None and max_squad_size is None:
            self.max_squad_size = int(self.archetype.target_roster_size)
        else:
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
        xp_idx_cache: Optional[Dict[int, Dict[str, Any]]] = None,
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

        xp_idx = xp_idx_cache if xp_idx_cache is not None else ClauseSniper._build_xp_idx(xp_table)
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
        causal_standings_ctx: Optional[Dict[str, Any]] = None,
        llm_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Full daily autonomous decision cycle:
          Step 1: Scarcity & Multi-Round Hold-vs-Sell NPV + Offer-Roll Patience + Hold-Decay Rotation
          Step 2: Joint MILP Starting XI (+ Pre-Kickoff Debt Solvency when `must_be_solvent`)
          Step 3: Posture- & Macro-Adapted Collateral-Backed Credit-Leveraged Acquisitions (Bids + Clauses)
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

        xp_idx = ClauseSniper._build_xp_idx(xp_table)

        # Build live MarketScarcityEngine
        scarcity_engine = MarketScarcityEngine(
            df_market_today=df_market_all,
            xp_table=xp_table,
            my_manager_name=my_manager_name,
            rules=self.rules,
            clause_locked_ids=clause_locked_ids,
        )

        # Macro Lifecycle Intelligence + Standings Posture Adaptation
        sc_summary = scarcity_engine.get_league_scarcity_summary()
        current_jn = int(phase_ctx.target_jornada_num if phase_ctx else 1)
        is_fifa = bool(phase_ctx.is_international_break if phase_ctx else False)
        macro_ctx = LeagueLifecycleEngine.evaluate_macro_context(
            free_players_ge_5m=sc_summary.get("free_ge_5m", 15),
            free_players_ge_10m=sc_summary.get("free_ge_10m", 3),
            current_jornada=current_jn,
            total_jornadas=38,
            days_to_next_match=eff_days_to_kickoff,
            is_international_break=is_fifa,
        )

        arch = self.archetype
        posture_ctrl = StandingsPostureController(enabled=StrategySettings.POSTURE_ADAPTATION_ENABLED)
        if arch is not None and not getattr(arch, "rank_adaptive", True):
            posture_mods = PostureModifiers(posture="STATIC_ARCHETYPE")
        elif isinstance(causal_standings_ctx, dict) and isinstance(causal_standings_ctx.get("posture_modifiers"), dict):
            pm_raw = causal_standings_ctx["posture_modifiers"]
            posture_mods = PostureModifiers(**{k: v for k, v in pm_raw.items() if k in PostureModifiers.__dataclass_fields__})
        elif isinstance(causal_standings_ctx, dict) and "my_rank" in causal_standings_ctx:
            posture_mods = posture_ctrl.compute_modifiers(
                my_rank=int(causal_standings_ctx.get("my_rank", 1)),
                total_managers=int(causal_standings_ctx.get("total_managers", 15)),
                my_points=float(causal_standings_ctx.get("my_points", 0.0)),
                leader_points=float(causal_standings_ctx.get("leader_points", 0.0)),
                second_points=float(causal_standings_ctx.get("second_points", 0.0)),
                podium_points=float(causal_standings_ctx.get("podium_points", 0.0)),
                rounds_settled=int(causal_standings_ctx.get("rounds_settled", 0)),
                my_net_worth=float(causal_standings_ctx.get("my_net_worth", 0.0)),
                leader_net_worth=float(causal_standings_ctx.get("leader_net_worth", 0.0)),
                llm_overrides=llm_overrides,
            )
        else:
            posture_mods = PostureModifiers()

        top_rivals: Set[str] = set()
        if isinstance(causal_standings_ctx, dict) and causal_standings_ctx.get("top_rival_managers"):
            top_rivals = {str(x) for x in causal_standings_ctx["top_rival_managers"] if str(x) != my_manager_name}

        eff_points_w = float(macro_ctx.get("points_weight", 1.0)) * float(posture_mods.points_weight_mult)
        eff_spec_w = float(macro_ctx.get("speculation_weight", 1.0)) * float(posture_mods.speculation_weight_mult)
        eff_pt_val = float(macro_ctx.get("xp_point_value_eur", 60_000.0)) * float(posture_mods.points_weight_mult)
        eff_core_aggr = float(getattr(arch, "core_bid_aggression", 1.0) if arch else 1.0) * float(posture_mods.bid_aggression_mult)
        eff_spec_aggr = float(getattr(arch, "spec_bid_aggression", 1.0) if arch else 1.0) * float(posture_mods.spec_bid_aggression_mult)
        eff_overbid_cap = float(getattr(arch, "auction_overbid_factor", 1.0) if arch else 1.0) * float(posture_mods.overbid_cap_mult)
        eff_credit_aggr = float(getattr(arch, "credit_utilization_aggression", 1.0) if arch else 1.0) * float(posture_mods.credit_usage_mult)
        eff_reserve_buf = int(getattr(arch, "reserve_buffer_eur", 0) if arch else 0)

        eff_max_squad = self.max_squad_size
        if arch is not None and arch.lifecycle_driven:
            if macro_ctx["phase"] == SeasonPhase.EARLY_EXPANSION.value:
                eff_max_squad = min(int(self.rules.team_max_size), max(self.max_squad_size, self.max_squad_size + 2))
            elif macro_ctx["phase"] == SeasonPhase.ENDGAME_RUNIN.value:
                eff_max_squad = max(12, min(self.max_squad_size, self.max_squad_size - 2))

        # Baseline MILP XI before sales to identify current XI starters & bench backup xP per line
        base_milp = self.solve_joint_lineup_and_solvency_milp(
            squad_df=sq,
            xp_table=xp_table,
            efr_map=efr_map,
            current_cash=current_cash,
            must_be_solvent=False,
            xp_idx_cache=xp_idx,
        )
        core_xi_ids = {s["player_id"] for s in base_milp["lineup_slots"]}

        # Pre-extract squad column lists ONCE (avoids multiple O(N) df.iterrows() calls!)
        sq_pids = sq["player_id"].tolist() if not sq.empty else []
        sq_pos = sq["posicion"].tolist() if (not sq.empty and "posicion" in sq.columns) else [""] * len(sq_pids)
        sq_nom = sq["nombre"].tolist() if (not sq.empty and "nombre" in sq.columns) else [""] * len(sq_pids)
        sq_eq = sq["equipo"].tolist() if (not sq.empty and "equipo" in sq.columns) else [""] * len(sq_pids)
        sq_est = sq["estado"].tolist() if (not sq.empty and "estado" in sq.columns) else ["ok"] * len(sq_pids)
        sq_pr = sq["precio"].tolist() if (not sq.empty and "precio" in sq.columns) else [150_000] * len(sq_pids)
        sq_sub = sq["subida_24h"].tolist() if (not sq.empty and "subida_24h" in sq.columns) else [0] * len(sq_pids)
        sq_cl = sq["clausula_actual"].tolist() if (not sq.empty and "clausula_actual" in sq.columns) else [0] * len(sq_pids)
        sq_acq = sq["acquired_date"].tolist() if (not sq.empty and "acquired_date" in sq.columns) else [""] * len(sq_pids)
        sq_pc = (
            sq["precio_compra"].tolist()
            if (not sq.empty and "precio_compra" in sq.columns)
            else (sq["purchase_price"].tolist() if (not sq.empty and "purchase_price" in sq.columns) else [0] * len(sq_pids))
        )

        # Compute realistic bench backup xP per line (`GK`, `DF`, `MF`, `FW`)
        bench_xp_by_line: Dict[str, float] = {"GK": 0.0, "DF": 2.2, "MF": 2.4, "FW": 2.2}
        if sq_pids:
            b_vals_map: Dict[str, List[float]] = {ln: [] for ln in LINES}
            for pid, pos_s in zip(sq_pids, sq_pos):
                if pid in core_xi_ids:
                    continue
                x_inf = xp_idx.get(pid, {})
                if x_inf.get("is_available", True):
                    for ln in self.xp_engine.get_valid_lines(pid, pos_s):
                        b_vals_map[ln].append(float(x_inf.get(f"xp_{ln}", 0.0)))
            for ln in LINES:
                bench_xp_by_line[ln] = max(b_vals_map[ln]) if b_vals_map[ln] else (0.0 if ln == "GK" else 1.8)

        prev_theses = memory_ledger.get_asset_theses_map() if memory_ledger is not None else {}
        role_by_pid: Dict[int, str] = {}
        npv_by_pid: Dict[int, Dict[str, Any]] = {}
        theses_rows: List[Dict[str, Any]] = []

        # Evaluate Asset Role & Multi-Round Hold-vs-Sell NPV for every owned player
        if sq_pids:
            pt_shadow = max(65_000.0, float(self.rules.bonus_per_point_eur) * 1.7, eff_pt_val)
            for pid, pos_s, nom_s, eq_s, est_s, pr_raw, sub_raw, cl_raw, acq_s, pc_raw in zip(
                sq_pids, sq_pos, sq_nom, sq_eq, sq_est, sq_pr, sq_sub, sq_cl, sq_acq, sq_pc
            ):
                price = safe_num(pr_raw, 150_000.0)
                subida = safe_num(sub_raw, 0.0)
                efr_72h = float(efr_map.get(pid, subida * 2.4))
                xp_info = xp_idx.get(pid, {})
                is_avail = bool(xp_info.get("is_available", True))
                estado = str(xp_info.get("estado", est_s) or "ok")
                xp_best = float(xp_info.get("xp_best", 0.0))
                xp_healthy = float(xp_info.get("xp_healthy", max(xp_best, 2.5)))
                p_start_h = float(xp_info.get("p_start_healthy", xp_info.get("p_start", 0.5)))
                valid_lines = self.xp_engine.get_valid_lines(pid, pos_s)
                best_ln = str(xp_info.get("best_line", sorted(valid_lines)[0] if valid_lines else "MF"))

                offer_or_est = float(offers[pid]) if (pid in offers and offers[pid] > 0) else price * 0.99
                purchase_p = safe_num(pc_raw or prev_theses.get(pid, {}).get("precio_compra", 0), 0.0)

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
                    team_name=str(eq_s or xp_info.get("equipo", "")),
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
                    "nombre": str(nom_s or ""),
                    "rol": role,
                    "fecha_compra": str(acq_s or prev_theses.get(pid, {}).get("fecha_compra", "") or ""),
                    "precio_compra": int(round(purchase_p if purchase_p > 0 else price)),
                    "precio_actual": int(round(price)),
                    "clausula_actual": int(round(safe_num(cl_raw, 0.0) or self.rules.compute_official_clause(max(purchase_p, price)))),
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

        # Step 1: Microeconomic NPV Disposals, Multi-Day Offer-Roll Patience & Hold-Decay Rotation
        original_owned_ids: Set[int] = set(sq_pids)
        routine_sales: List[Dict[str, Any]] = []
        sold_ids: Set[int] = set()
        working_cash = float(current_cash)
        spec_max_days = int(getattr(arch, "speculative_hold_days_max", StrategySettings.SPECULATIVE_HOLD_DAYS_MAX) if arch else StrategySettings.SPECULATIVE_HOLD_DAYS_MAX)
        hold_decay_eur = int(getattr(arch, "hold_decay_penalty_eur", 80_000) if arch else 80_000)

        if sq_pids:
            live_board_best_xp_by_line: Dict[str, float] = {ln: 0.0 for ln in LINES}
            if market_listings_df is not None and not market_listings_df.empty and "player_id" in market_listings_df.columns:
                for raw_lpid in market_listings_df["player_id"].tolist():
                    try:
                        lpid = int(float(raw_lpid))
                    except (TypeError, ValueError):
                        continue
                    if lpid in original_owned_ids:
                        continue
                    lx = xp_idx.get(lpid)
                    if lx and lx.get("is_available", False):
                        for ln in self.xp_engine.get_valid_lines(lpid):
                            v_ln = float(lx.get(f"xp_{ln}", lx.get("xp_best", 0.0)))
                            if v_ln > live_board_best_xp_by_line[ln]:
                                live_board_best_xp_by_line[ln] = v_ln

            fit_count = sum(1 for pid in sq_pids if xp_idx.get(pid, {}).get("is_available", True))
            gk_count = sum(1 for pid, pos_s in zip(sq_pids, sq_pos) if "GK" in self.xp_engine.get_valid_lines(pid, pos_s))

            for pid, pos_s, nom_s, est_s, pr_raw, sub_raw, acq_s, pc_raw in zip(
                sq_pids, sq_pos, sq_nom, sq_est, sq_pr, sq_sub, sq_acq, sq_pc
            ):
                if pid in locked_sell or pid in untouchables or pid not in offers:
                    continue
                offer_amt = float(offers[pid])
                if offer_amt <= 0:
                    continue

                price = safe_num(pr_raw, 150_000.0)
                subida = safe_num(sub_raw, 0.0)
                efr_72h = float(efr_map.get(pid, subida * 2.4))
                xp_info = xp_idx.get(pid, {})
                is_avail = bool(xp_info.get("is_available", True))
                xp_best = float(xp_info.get("xp_best", 0.0))
                xp_healthy = float(xp_info.get("xp_healthy", max(xp_best, 2.5)))
                valid_lns_p = self.xp_engine.get_valid_lines(pid, pos_s)
                is_gk = "GK" in valid_lns_p
                role = role_by_pid.get(pid, "BENCH_PATCH")
                npv_res = npv_by_pid.get(pid, {})
                purchase_p = safe_num(pc_raw or prev_theses.get(pid, {}).get("precio_compra", 0), 0.0)

                if is_gk and gk_count <= 1:
                    continue
                if is_avail and fit_count <= 11 and pid not in forced_sells:
                    continue

                offer_pct = (offer_amt - price) / max(price, 1.0)
                prev_rolls = int(prev_theses.get(pid, {}).get("offer_rolls", 0) or 0)

                held_days = 99
                if acq_s is not None and str(acq_s) not in ("", "nan", "None"):
                    try:
                        held_days = (pd.Timestamp(current_date) - pd.Timestamp(str(acq_s))).days
                    except (ValueError, TypeError):
                        held_days = 99

                is_forced = (pid in forced_sells)
                was_claused_premium = (purchase_p >= 1.20 * price) and (held_days < 8)
                is_depreciating = (efr_72h <= -35_000.0) or (subida <= -15_000.0)
                board_best_here = max((live_board_best_xp_by_line.get(ln, 0.0) for ln in valid_lns_p), default=0.0)
                needs_slot_or_liquidity = (
                    (len(sq_pids) - len(sold_ids) >= eff_max_squad - 1)
                    or (working_cash < 2_500_000.0 and board_best_here > xp_best + 0.85)
                )

                inj_decision = InjuryTacticalDecisionEngine.decide(
                    player_id=pid,
                    nombre=str(nom_s or ""),
                    estado=str(est_s or xp_info.get("estado", "ok")),
                    absence_days_est=int(npv_res.get("absence_days_est", 0)),
                    missed_rounds_calendar=int(npv_res.get("missed_rounds_est", 0)),
                    is_fifa_break=bool(macro_ctx.get("is_fifa_break", False)),
                    asset_role=role,
                    efr_72h=efr_72h,
                    market_price=price,
                    best_market_alternative_xp=float(npv_res.get("reinvest_xp_est", 3.0)),
                    player_healthy_xp=float(xp_healthy),
                )

                is_pillar = bool(
                    self.protect_pillars_absolute
                    and (role == "FRANCHISE_CORE" or (xp_healthy >= 5.8 and price >= 11_000_000.0))
                )

                if macro_ctx.get("fifa_break_hold_momentum", False) and efr_72h >= 20_000.0 and not is_forced:
                    continue

                if is_pillar and not is_forced:
                    if inj_decision["action"] != InjuryAction.SELL_CASH_OUT:
                        continue

                # Stale speculative flip hold-decay tax (`speculative_hold_days_max` + `hold_decay_penalty_eur`)
                is_hold_decay_sell = False
                if (
                    is_avail
                    and role in ("SPECULATIVE_FLIP", "BENCH_PATCH")
                    and pid not in core_xi_ids
                    and not is_fifa
                    and held_days > spec_max_days
                    and efr_72h < 65_000.0
                ):
                    decay_tax = (held_days - spec_max_days) * hold_decay_eur
                    if offer_amt >= (npv_res.get("npv_hold_eur", price) - decay_tax) and offer_pct >= -0.025:
                        is_hold_decay_sell = True

                min_hold_days = 8 if was_claused_premium else 2
                can_proactively_rotate = (not is_avail) or is_depreciating or needs_slot_or_liquidity

                if not is_avail:
                    is_npv_disposal = (inj_decision["action"] == InjuryAction.SELL_CASH_OUT)
                else:
                    is_npv_disposal = (
                        bool(npv_res.get("recommend_sell", False))
                        and can_proactively_rotate
                        and held_days >= min_hold_days
                    ) or is_hold_decay_sell

                if is_npv_disposal and not is_forced:
                    if (
                        is_avail
                        and not is_hold_decay_sell
                        and offer_pct < -0.018
                        and eff_days_to_kickoff >= 2
                        and prev_rolls < 2
                        and efr_72h > -95_000
                    ):
                        is_npv_disposal = False

                is_peak_harvest = (
                    role in ("SPECULATIVE_FLIP", "BENCH_PATCH")
                    and pid not in core_xi_ids
                    and offer_pct >= 0.032
                    and (offer_amt - price) >= max(25_000.0, efr_72h * 0.78)
                    and xp_best < 4.9
                    and not is_pillar
                )

                if is_forced or is_npv_disposal or is_peak_harvest:
                    requires_human = bool(
                        role in ("FRANCHISE_CORE", "STRUCTURAL_STARTER")
                        or price >= self.important_sale_min_eur
                    ) and not is_forced
                    reason = (
                        "USER_DIRECTIVE_SELL"
                        if is_forced
                        else (
                            "HOLD_DECAY_ROTATION"
                            if (is_hold_decay_sell and not bool(npv_res.get("recommend_sell", False)))
                            else ("NPV_OPTIMAL_DISPOSAL" if is_npv_disposal else "PEAK_OFFER_ARBITRAGE")
                        )
                    )
                    routine_sales.append({
                        "player_id": pid,
                        "nombre": str(nom_s or ""),
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
            xp_idx_cache=xp_idx,
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
            safe_acquisition_budget = max(0.0, working_cash - eff_reserve_buf)
            top_k_liquid_collateral = 0.0
        else:
            k_slots = max(1, int(self.rules.market_max_user_sales))
            non_core_values: List[float] = []
            if not final_sq.empty:
                for pid, pr_raw in zip(final_sq["player_id"].tolist(), final_sq["precio"].tolist()):
                    pid_i = int(pid)
                    if eff_days_to_kickoff <= 1 and pid_i in locked_sell and int(self.rules.days_for_sale) > 1:
                        continue
                    rl = role_by_pid.get(pid_i, "BENCH_PATCH")
                    if rl != "FRANCHISE_CORE" and pid_i not in untouchables:
                        w = 0.95 if rl in ("SPECULATIVE_FLIP", "BENCH_PATCH") else 0.55
                        non_core_values.append(safe_num(pr_raw, 0.0) * w)
            non_core_values.sort(reverse=True)
            top_k_liquid_collateral = float(sum(non_core_values[:k_slots]))
            base_lev = 0.84 if eff_days_to_kickoff >= 2 else 0.72
            leverage_factor = float(np.clip(base_lev * eff_credit_aggr, 0.50, 0.96))
            safe_debt_cap = top_k_liquid_collateral * leverage_factor
            safe_acquisition_budget = min(max_official_credit_bid, working_cash + safe_debt_cap - eff_reserve_buf)
            safe_acquisition_budget = max(0.0, safe_acquisition_budget)

        owned_ids = {int(x) for x in final_sq["player_id"].tolist()} if not final_sq.empty else set()
        open_roster_slots = max(0, eff_max_squad - len(owned_ids))

        # 3A. Evaluate Market Auction Candidates (`AuctionBidModel`)
        bid_candidates: List[Dict[str, Any]] = []
        prio_pos = {str(p).upper() for p in (posture_mods.extra.get("priority_positions") or [])}
        if open_roster_slots > 0 and safe_acquisition_budget >= 150_000 and market_listings_df is not None and not market_listings_df.empty:
            # Fast market column lookup (avoids full df_market_all to_dict('index') & iterrows)
            mkt_pids = df_market_all["player_id"].tolist()
            mkt_nom = df_market_all["nombre"].tolist() if "nombre" in df_market_all.columns else [""] * len(mkt_pids)
            mkt_pr = df_market_all["precio"].tolist() if "precio" in df_market_all.columns else [0] * len(mkt_pids)
            mkt_sub = df_market_all["subida_24h"].tolist() if "subida_24h" in df_market_all.columns else [0] * len(mkt_pids)
            mkt_cl = df_market_all["clausula_actual"].tolist() if "clausula_actual" in df_market_all.columns else [0] * len(mkt_pids)
            mkt_fast: Dict[int, Tuple[str, float, float, float]] = {}
            for mp, mn, mpr, msub, mcl in zip(mkt_pids, mkt_nom, mkt_pr, mkt_sub, mkt_cl):
                try:
                    mpi = int(float(mp))
                    if mpi > 0:
                        mkt_fast[mpi] = (str(mn or ""), safe_num(mpr, 0.0), safe_num(msub, 0.0), safe_num(mcl, 0.0))
                except (TypeError, ValueError):
                    continue

            l_pids = market_listings_df["player_id"].tolist()
            l_pr = market_listings_df["precio_mercado_dia"].tolist() if "precio_mercado_dia" in market_listings_df.columns else [0] * len(l_pids)
            l_ask = (
                market_listings_df["ask_price"].tolist()
                if "ask_price" in market_listings_df.columns
                else (market_listings_df["precio_salida_o_oferta"].tolist() if "precio_salida_o_oferta" in market_listings_df.columns else [0] * len(l_pids))
            )
            l_sel = (
                market_listings_df["seller_name"].tolist()
                if "seller_name" in market_listings_df.columns
                else (market_listings_df["vendedor_o_origen"].tolist() if "vendedor_o_origen" in market_listings_df.columns else ["Mercado"] * len(l_pids))
            )
            l_nom = market_listings_df["jugador"].tolist() if "jugador" in market_listings_df.columns else [""] * len(l_pids)

            for raw_pid, raw_lpr, raw_lask, raw_lsel, raw_lnom in zip(l_pids, l_pr, l_ask, l_sel, l_nom):
                try:
                    pid = int(float(raw_pid))
                except (ValueError, TypeError):
                    continue
                if pid <= 0 or pid in owned_ids or pid in original_owned_ids or pid in blacklist_buys:
                    continue

                xp_info = xp_idx.get(pid)
                if not xp_info or not xp_info.get("is_available", False):
                    continue

                fast_m = mkt_fast.get(pid)
                vm = fast_m[1] if (fast_m and fast_m[1] > 0) else safe_num(raw_lpr, 0.0)
                if vm <= 0:
                    continue
                ask = safe_num(raw_lask, vm)
                subida = fast_m[2] if fast_m else 0.0
                nombre_b = fast_m[0] if (fast_m and fast_m[0]) else str(raw_lnom or f"ID {pid}")

                seller = str(raw_lsel or "Mercado").strip()
                if seller == my_manager_name:
                    continue
                is_rival = seller not in ("Mercado", "Libre", "None", "nan", "")

                best_ln = str(xp_info.get("best_line", xp_info.get("natural_pos", "MF")))
                delta_xp = max(0.0, float(xp_info.get("xp_best", 0.0)) - xi_floor_by_pos.get(best_ln, 3.2))
                if pid in priority_buys:
                    delta_xp += 0.45
                elif best_ln in prio_pos and delta_xp >= 0.20:
                    delta_xp += 0.15
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
                    idle_cash_eur=max(0.0, working_cash - eff_reserve_buf),
                    xp_point_value_eur=eff_pt_val,
                    points_weight=eff_points_w,
                    speculation_weight=eff_spec_w,
                    efr_weight=float(getattr(arch, "efr_weight", 1.0) if arch else 1.0),
                    min_efr_trigger=float(getattr(arch, "min_efr_trigger", macro_ctx.get("min_efr_threshold", 40_000)) if arch else macro_ctx.get("min_efr_threshold", 40_000)),
                    core_bid_aggression=eff_core_aggr,
                    spec_bid_aggression=eff_spec_aggr,
                    xp_horizon_rounds=float(getattr(arch, "xp_horizon_rounds", 7.5) if arch else 7.5),
                    overbid_cap_mult=eff_overbid_cap,
                    p_start_floor_delta=float(posture_mods.p_start_floor_delta),
                )

                if not bid_eval["should_bid"]:
                    continue
                b_amt = int(bid_eval["bid_amount"])
                cl_val = fast_m[3] if (fast_m and len(fast_m) >= 4) else 0.0
                if is_rival and eff_clauses_open and cl_val > 0 and b_amt >= cl_val * 0.98 and cl_val <= max(0.0, clause_budget):
                    # Prefer instant cheaper release clause via ClauseSniper over slow transfer-market overbidding
                    continue
                if b_amt > max_official_credit_bid or b_amt > safe_acquisition_budget:
                    continue

                bid_candidates.append({
                    "action_type": "MARKET_BID",
                    "player_id": pid,
                    "nombre": nombre_b,
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
                    "requires_human_approval": False,
                })

        # 3B. Evaluate Surgical Scarcity Clause Snipes (FIXED non-inverted `clause_cash_reserve_pct`!)
        macro_cl_frac = float(macro_ctx.get("clause_budget_fraction", 0.30))
        if arch is not None:
            arch_cl_pct = float(arch.clause_cash_reserve_pct)
            if arch.lifecycle_driven:
                clause_frac = min(1.0, 0.45 * macro_cl_frac + 0.55 * arch_cl_pct)
            else:
                clause_frac = arch_cl_pct
        else:
            clause_frac = macro_cl_frac
        clause_budget = max(0.0, (working_cash - 300_000.0 - eff_reserve_buf) * clause_frac) if eff_clauses_open else 0.0
        clause_targets = self.clause_sniper.scan_offensive_targets(
            df_market_today=df_market_all,
            xp_table=xp_table,
            efr_map=efr_map,
            my_manager_name=my_manager_name,
            my_current_xi_floor_by_pos=xi_floor_by_pos,
            locked_player_ids=(clause_locked_ids or set()) | owned_ids,
            recent_match_shock_map=recent_match_shock_map,
            max_budget=min(max_official_credit_bid, max(0.0, clause_budget)),
            idle_cash_eur=max(0.0, working_cash - eff_reserve_buf),
            clauses_open=eff_clauses_open,
            scarcity_engine=scarcity_engine,
            blacklist_ids=blacklist_buys,
            xp_idx=xp_idx,
            xp_point_value_eur=eff_pt_val,
            clause_alpha_floor_delta_eur=int(posture_mods.clause_alpha_floor_delta_eur),
            priority_positions=posture_mods.extra.get("priority_positions"),
            top_rival_managers=top_rivals,
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
                "requires_human_approval": True,
            })

        # 3C. Joint Hierarchical Allocation
        def _acq_tier_key(x: Dict[str, Any]) -> Tuple[int, float]:
            dx = float(x.get("delta_xp_xi", 0.0))
            is_empty_pos = (xi_floor_by_pos.get(x["position"], 3.0) <= 0.1)
            reg = str(x.get("regime", ""))
            is_prio = int(x["player_id"]) in priority_buys

            if is_empty_pos:
                tier = 3
            elif is_prio or dx >= 0.65 or reg in ("STAR_UNDERCLAUSED_SNIPE", "VORP_ELITE_SNIPE", "MICRO_BREAKOUT_SNIPE") or (reg == "CORE_XI_UPGRADE" and dx >= 0.35):
                tier = 2
            elif dx >= 0.25 or float(x.get("efr_72h", 0.0)) >= 80_000:
                tier = 1
            else:
                tier = 0

            composite_score = float(x["score_per_euro"]) * 0.40 + (float(x["expected_surplus"]) / 1_000_000.0) * 0.60
            return (tier, composite_score)

        combined_acqs = sorted(clause_actions + bid_candidates, key=_acq_tier_key, reverse=True)

        arch_bids = int(arch.max_simultaneous_bids) if arch else 5
        macro_bids = int(macro_ctx.get("max_simultaneous_bids", 5))
        max_bids_allowed = (
            arch_bids
            if (arch is not None and not arch.lifecycle_driven)
            else max(2, min(7, int(round(0.65 * arch_bids + 0.35 * macro_bids))))
        )
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
                reserve_weight = 1.0 if (not can_use_credit or eff_days_to_kickoff <= 1) else max(0.68, float(acq["win_prob_est"]))
                eff_cost = amt * reserve_weight
                if amt <= max_official_credit_bid and eff_cost <= rem_budget and len(selected_bids) < max_bids_allowed:
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
            available_cash_surplus=max(0.0, post_clause_cash - worst_case_bid_outflow - eff_reserve_buf),
            clauses_open=eff_clauses_open,
            scarcity_engine=scarcity_engine,
            clause_locked_ids=clause_locked_ids,
            xp_idx=xp_idx,
            shield_budget_pct=float(getattr(arch, "shield_rival_threat_pct", 0.18) if arch else 0.18),
            shield_threshold_mult=float(posture_mods.shield_threshold_mult),
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
            "scarcity_summary": sc_summary,
            "posture_state": posture_mods.to_dict(),
            "macro_context": macro_ctx,
            "lineup": {
                "formation": milp_sol["formation"],
                "player_ids": milp_sol["lineup_ids"],
                "slots": milp_sol["lineup_slots"],
                "expected_points": milp_sol["expected_xi_points"],
            },
            "is_solvent": milp_sol["is_solvent"],
        }
