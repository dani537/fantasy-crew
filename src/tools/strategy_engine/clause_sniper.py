"""
Surgical Scarcity-Aware Release Clause Sniper & Defensive Shield Optimizer (`ClauseSniper`)
===========================================================================================
Operates under official league rules (`LeagueRulesEngine`):
- Strictly enforces `clauses_open` (`0` clause actions inside `clauseRoundDisabledHours` pre-kickoff
  or during an active matchday, and `0` on players inside `clauseActivationDelay` days).
- Supports policies:
  - `SURGICAL_SCARCITY` (Recommended v3 default): Only fires offensive clause snipes when EITHER
    (a) a sub-3.8M micro-breakout's 12-day resale trajectory covers the clause (`proj_12d >= 1.06 * clause`), OR
    (b) an under-claused elite XI upgrade (`delta_xp >= 0.65`) has NO comparable free-market alternative
        available soon (`scarcity_tier in ("IRREPLACEABLE_FREE", "SCARCE")` or hyper-compressed `<= 1.28x` ratio),
    always funded 100% by positive surplus cash (never by credit leverage).
  - `AGGRESSIVE`: Broader snipe thresholds when surplus cash is abundant.
  - `DISABLED`: Zero clause activity.
- Exact Defensive Shield math (`scan_defensive_shields`):
  - Uses official `rules.clause_increment_ratio` (`1.0` => 1 € cash = +1 € clause) and accounts
    for `50%` deadweight refund friction (`1 - clause_decrement_refund_pct`), shielding ONLY
    irreplaceable core starters when expected replacement spread loss > 2.5x friction cost.
"""

from typing import List, Dict, Any, Set, Optional
import numpy as np
import pandas as pd

from src.tools.data_extraction.warehouse import compute_official_clause
from src.tools.strategy_engine.league_rules import LeagueRulesEngine
from src.tools.strategy_engine.replacement_cost import MarketScarcityEngine
from src.utils.numeric import safe_num


class ClauseSniper:
    """Offensive release-clause scanner & defensive shield calculator."""

    def __init__(
        self,
        policy: str = "COMPETITIVE_VORP",
        rules: Optional[LeagueRulesEngine] = None,
        league_ratio_cap: float = 2.20,
        clause_min_avg_pts: float = 4.8,
        clause_min_delta_xp: float = 0.75,
        clause_budget_reserve_pct: float = 0.80,
    ):
        self.policy = (policy or "COMPETITIVE_VORP").upper()
        self.rules = rules or LeagueRulesEngine.load()
        self.league_ratio_cap = float(league_ratio_cap)
        self.clause_min_avg_pts = float(clause_min_avg_pts)
        self.clause_min_delta_xp = float(clause_min_delta_xp)
        self.clause_budget_reserve_pct = float(clause_budget_reserve_pct)

    def scan_offensive_targets(
        self,
        df_market_today: pd.DataFrame,
        xp_table: pd.DataFrame,
        efr_map: Dict[int, float],
        my_manager_name: str,
        my_current_xi_floor_by_pos: Dict[str, float],
        locked_player_ids: Optional[Set[int]] = None,
        recent_match_shock_map: Optional[Dict[int, float]] = None,
        max_budget: float = 0.0,
        idle_cash_eur: float = 0.0,
        clauses_open: bool = True,
        scarcity_engine: Optional[MarketScarcityEngine] = None,
        blacklist_ids: Optional[Set[int]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns ranked rival players whose release clause offers positive net alpha,
        strictly respecting the 48h pre-kickoff / active-round freeze (`clauses_open`)
        and 5-day post-purchase immunity (`locked_player_ids`).
        """
        if (
            self.policy == "DISABLED"
            or not clauses_open
            or not self.rules.clause_enabled
            or df_market_today is None
            or df_market_today.empty
            or max_budget <= 300_000
        ):
            return []

        locked = (locked_player_ids or set()) | (blacklist_ids or set())
        shocks = recent_match_shock_map or {}
        xp_idx = xp_table.set_index("player_id").to_dict("index") if (xp_table is not None and not xp_table.empty) else {}

        candidates: List[Dict[str, Any]] = []
        for _, r in df_market_today.iterrows():
            try:
                pid = int(float(r["player_id"]))
            except (KeyError, ValueError, TypeError):
                continue

            if pid in locked:
                continue

            owner = str(r.get("propietario_liga", "") or "").strip()
            if not owner or owner in ("Libre", "Mercado", "nan", "None", my_manager_name):
                continue

            xp_info = xp_idx.get(pid)
            if not xp_info or not xp_info.get("is_available", False):
                continue

            price = safe_num(r.get("precio", 0), 0.0)
            subida = safe_num(r.get("subida_24h", 0), 0.0)
            raw_clause = safe_num(r.get("clausula_actual", 0), 0.0)
            clause = raw_clause if raw_clause > 0 else float(self.rules.compute_official_clause(price))

            if clause <= 0 or clause > max_budget or price <= 0:
                continue

            # Never snipe rapidly falling assets unless they are established elite starters
            avg_pts_raw = safe_num(str(r.get("media_puntos", "0")).replace(",", "."))
            efr_72h = float(efr_map.get(pid, subida * 2.4))
            is_elite_rebound = (avg_pts_raw >= 5.0 and subida >= -20_000 and self.policy in ("COMPETITIVE_VORP", "AGGRESSIVE"))
            if (subida < 0 or efr_72h < 0) and not is_elite_rebound:
                continue

            xp_best = float(xp_info.get("xp_best", 0.0))
            xp_healthy = float(xp_info.get("xp_healthy", xp_best))
            best_line = str(xp_info.get("best_line", xp_info.get("natural_pos", "MF")))
            valid_lines = {x.strip() for x in str(xp_info.get("valid_lines", best_line)).split(",") if x.strip()}
            p_start = float(xp_info.get("p_start", 0.0))
            xi_floor = float(my_current_xi_floor_by_pos.get(best_line, 3.5))
            delta_xp = max(0.0, xp_best - xi_floor)

            # Project 12-day Trajectory (Breakout wave + Match Shock impulse)
            shock_pts = float(shocks.get(pid, 0.0))
            shock_wave_eur = (
                max(0.0, shock_pts - 6.0) * 145_000.0
                if price < 3_500_000
                else max(0.0, shock_pts - 7.0) * 190_000.0
            )
            proj_12d_gain = float(efr_72h) * 2.85 + shock_wave_eur
            proj_12d_price = min(price + proj_12d_gain, price * 4.5 + 1_800_000.0)

            idle_boost = 1.0 + float(np.clip((float(idle_cash_eur) - 5_000_000.0) / 8_000_000.0, 0.0, 3.2))
            horizon_matchdays = 8.0 if p_start >= 0.80 else (6.0 if p_start >= 0.70 else 3.5)
            pt_val = max(50_000.0, float(self.rules.bonus_per_point_eur) * 1.2)
            xi_points_eur = delta_xp * horizon_matchdays * pt_val * idle_boost if p_start >= 0.65 else 0.0

            total_12d_valuation = proj_12d_price + xi_points_eur
            net_alpha_eur = total_12d_valuation - clause
            clause_ratio = clause / price

            # Check free-market scarcity if engine provided
            scarcity_tier = "SCARCE"
            n_free_comp = 0
            if scarcity_engine is not None:
                rep = scarcity_engine.compute_replacement_profile(
                    player_id=pid,
                    natural_pos=best_line,
                    xp_healthy=xp_healthy,
                    market_price=price,
                    valid_lines=valid_lines,
                )
                scarcity_tier = rep.scarcity_tier
                n_free_comp = rep.n_free_comparable

            if self.policy == "COMPETITIVE_VORP":
                # In AZ Finance / standard Biwenger rules, minimum clauses are 1.70x to 2.25x.
                # Allow official league ratios when cash is abundant and player delivers high VORP.
                cap = getattr(self, "league_ratio_cap", 2.20)
                league_ratio_cap = cap if float(idle_cash_eur) >= 5_000_000.0 else min(cap, 1.95)
                min_avg = getattr(self, "clause_min_avg_pts", 4.8)
                min_dx = getattr(self, "clause_min_delta_xp", 0.75)
                is_micro_breakout = (
                    clause <= 5_200_000
                    and clause_ratio <= 2.25
                    and p_start >= 0.65
                    and (subida >= 60_000 or shock_pts >= 8.0)
                    and net_alpha_eur >= max(200_000.0, clause * 0.08)
                    and proj_12d_price >= clause * 0.88
                )
                is_star_steal = (
                    p_start >= 0.70
                    and (xp_best >= 4.2 or avg_pts_raw >= min_avg)
                    and delta_xp >= min_dx
                    and clause_ratio <= league_ratio_cap
                    and (subida >= -20_000)
                    and net_alpha_eur >= max(350_000.0, clause * 0.06)
                )
            elif self.policy == "SURGICAL_SCARCITY":
                # Avoid >1.65x deadweight clause brackets (e.g. 225% sub-2M bench traps) and keep 1.5M auction buffer
                has_surplus_buffer = float(idle_cash_eur) >= (clause + 1_500_000.0)
                is_micro_breakout = (
                    has_surplus_buffer
                    and clause <= 4_200_000
                    and clause_ratio <= 1.65
                    and p_start >= 0.70
                    and (subida >= 95_000 or shock_pts >= 11.0)
                    and net_alpha_eur >= max(380_000.0, clause * 0.18)
                    and proj_12d_price >= clause * 1.08
                    and idle_cash_eur < 30_000_000.0
                )
                flush = idle_boost >= 1.6
                ultra_flush = float(idle_cash_eur) >= 16_000_000.0
                max_ratio = 1.38 if not ultra_flush else (1.46 if (subida >= 50_000 or n_free_comp <= 1) else 1.40)
                scarcity_gate = (n_free_comp <= 1 and scarcity_tier in ("IRREPLACEABLE_FREE", "SCARCE")) or (clause_ratio <= 1.26)
                is_star_steal = (
                    float(idle_cash_eur) >= (clause + 800_000.0)
                    and scarcity_gate
                    and p_start >= 0.78
                    and delta_xp >= (0.70 if ultra_flush else 0.85)
                    and clause_ratio <= max_ratio
                    and subida >= (0 if flush else 30_000)
                    and net_alpha_eur >= max(750_000.0, clause * (0.09 if flush else 0.13))
                )
            else:  # AGGRESSIVE
                is_micro_breakout = (
                    clause <= 5_000_000
                    and (subida >= 50_000 or shock_pts >= 8.0)
                    and net_alpha_eur >= max(200_000.0, clause * 0.12)
                    and proj_12d_price >= clause * 0.95
                    and idle_cash_eur < 30_000_000.0
                )
                flush = idle_boost >= 1.6
                ultra_flush = float(idle_cash_eur) >= 16_000_000.0
                max_ratio = 1.85 if not ultra_flush else 2.10
                is_star_steal = (
                    p_start >= 0.72
                    and delta_xp >= 0.65
                    and clause_ratio <= max_ratio
                    and subida >= (0 if flush else 20_000)
                    and net_alpha_eur >= max(500_000.0, clause * 0.08)
                )

            if is_micro_breakout or is_star_steal:
                candidates.append({
                    "player_id": pid,
                    "nombre": str(r.get("nombre", "")),
                    "equipo": str(r.get("equipo", "")),
                    "victim_manager": owner,
                    "position": best_line,
                    "market_price": round(price),
                    "clause_amount": int(round(clause)),
                    "clause_ratio": round(clause_ratio, 2),
                    "subida_24h": round(subida),
                    "efr_72h": round(efr_72h),
                    "xp_best": round(xp_best, 2),
                    "delta_xp_xi": round(delta_xp, 2),
                    "scarcity_tier": scarcity_tier,
                    "n_free_comparable": n_free_comp,
                    "projected_12d_val": round(total_12d_valuation),
                    "net_alpha_eur": round(net_alpha_eur),
                    "alpha_roi": round(net_alpha_eur / clause, 4),
                    "snipe_type": "MICRO_BREAKOUT_SNIPE" if is_micro_breakout else "STAR_UNDERCLAUSED_SNIPE",
                })

        candidates.sort(key=lambda x: (x["alpha_roi"], x["net_alpha_eur"]), reverse=True)
        return candidates[:8]

    def scan_defensive_shields(
        self,
        my_squad_df: pd.DataFrame,
        xp_table: pd.DataFrame,
        efr_map: Dict[int, float],
        max_rival_purchasing_power: float,
        available_cash_surplus: float,
        clauses_open: bool = True,
        scarcity_engine: Optional[MarketScarcityEngine] = None,
        clause_locked_ids: Optional[Set[int]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Identifies our own unlocked core XI stars dangerously exposed to rival clause steals.
        Uses exact `rules.clause_increment_ratio` (`1.0` => 1€ cash = +1€ clause) and accounts
        for `50%` deadweight friction (`1 - clause_decrement_refund_pct`).
        """
        if (
            self.policy == "DISABLED"
            or not clauses_open
            or not self.rules.clause_enabled
            or my_squad_df is None
            or my_squad_df.empty
            or available_cash_surplus < 2_000_000
        ):
            return []

        locked = clause_locked_ids or set()
        xp_idx = xp_table.set_index("player_id").to_dict("index") if (xp_table is not None and not xp_table.empty) else {}
        shields: List[Dict[str, Any]] = []
        shield_budget = available_cash_surplus * 0.18
        inc_ratio = max(0.25, float(self.rules.clause_increment_ratio))  # 1.0 => 1€ cash raises clause by 1€
        deadweight_rate = max(0.10, 1.0 - float(self.rules.clause_decrement_refund_pct))  # 0.50 deadweight friction

        for _, r in my_squad_df.iterrows():
            pid = int(r["player_id"])
            if pid in locked:
                # Already immune (5-day post-purchase lock)! Zero need to spend cash shielding today.
                continue

            xp_info = xp_idx.get(pid, {})
            xp_best = float(xp_info.get("xp_best", 0.0))
            xp_healthy = float(xp_info.get("xp_healthy", xp_best))
            p_start = float(xp_info.get("p_start", 0.0))
            best_line = str(xp_info.get("best_line", xp_info.get("natural_pos", "MF")))
            price = safe_num(r.get("precio", 0), 0.0)
            subida = safe_num(r.get("subida_24h", 0), 0.0)
            clause = safe_num(r.get("clausula_actual", 0), 0.0) or float(compute_official_clause(price))
            efr_72h = float(efr_map.get(pid, subida * 2.4))

            if xp_best < 6.0 or p_start < 0.80 or subida < 70_000:
                continue
            if clause > max_rival_purchasing_power * 0.95:
                continue

            # Check replacement cost spread: if easy to replace in free market, don't burn deadweight cash!
            replacement_spread = price * 0.25
            if scarcity_engine is not None:
                rep = scarcity_engine.compute_replacement_profile(
                    player_id=pid,
                    natural_pos=best_line,
                    xp_healthy=xp_healthy,
                    market_price=price,
                )
                if rep.scarcity_tier in ("ABUNDANT", "MODERATE"):
                    continue
                replacement_spread = max(0.0, rep.blended_replacement_cost_eur - clause)

            proj_10d = price + max(0.0, efr_72h * 2.6)
            if clause < proj_10d * 1.20:
                desired_clause = np.ceil((proj_10d * 1.28) / 100_000.0) * 100_000.0
                clause_delta = max(0.0, desired_clause - clause)
                cash_needed = int(round(clause_delta / inc_ratio))
                increment_eur = int(min(shield_budget, max(250_000, cash_needed)))
                deadweight_cost = increment_eur * deadweight_rate

                # Expected loss if stolen (~30% steal risk on surging underclaused star) must exceed 2.2x deadweight cost
                expected_steal_loss = 0.30 * max(replacement_spread, proj_10d - clause)
                if increment_eur >= 250_000 and shield_budget >= increment_eur and expected_steal_loss >= 2.2 * deadweight_cost:
                    shields.append({
                        "player_id": pid,
                        "nombre": str(r.get("nombre", "")),
                        "current_clause": int(round(clause)),
                        "new_clause_est": int(round(clause + increment_eur * inc_ratio)),
                        "market_price": int(round(price)),
                        "recommended_cash_spend": increment_eur,
                        "deadweight_friction_eur": int(round(deadweight_cost)),
                        "reason": (
                            f"Pilar escaso ({xp_best:.1f} xP, +{int(subida):,}€/d) desbloqueado y "
                            f"expuesto a liquidez rival (ratio 1:{inc_ratio:g})"
                        ),
                    })
                    shield_budget -= increment_eur

        return shields
