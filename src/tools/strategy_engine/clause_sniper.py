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

    @staticmethod
    def _build_xp_idx(xp_table: Optional[pd.DataFrame]) -> Dict[int, Dict[str, Any]]:
        if xp_table is None or xp_table.empty:
            return {}
        recs = xp_table.to_dict("records")
        out: Dict[int, Dict[str, Any]] = {}
        for r in recs:
            try:
                out[int(float(r["player_id"]))] = r
            except Exception:
                continue
        return out

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
        xp_idx: Optional[Dict[int, Dict[str, Any]]] = None,
        xp_point_value_eur: Optional[float] = None,
        clause_alpha_floor_delta_eur: int = 0,
        priority_positions: Optional[List[str]] = None,
        top_rival_managers: Optional[Set[str]] = None,
        remaining_rounds: int = 26,
        macro_phase: Optional[str] = None,
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
        xp_map = xp_idx if xp_idx is not None else self._build_xp_idx(xp_table)
        prio_pos = {str(p).upper() for p in (priority_positions or [])}
        top_rivals = top_rival_managers or set()

        # Vectorized column extraction (eliminates slow df.iterrows() per day)
        pid_col = df_market_today["player_id"].tolist()
        own_col = df_market_today["propietario_liga"].tolist() if "propietario_liga" in df_market_today.columns else [""] * len(pid_col)
        nom_col = df_market_today["nombre"].tolist() if "nombre" in df_market_today.columns else [""] * len(pid_col)
        eq_col = df_market_today["equipo"].tolist() if "equipo" in df_market_today.columns else [""] * len(pid_col)
        pr_col = df_market_today["precio"].tolist() if "precio" in df_market_today.columns else [0] * len(pid_col)
        sub_col = df_market_today["subida_24h"].tolist() if "subida_24h" in df_market_today.columns else [0] * len(pid_col)
        cl_col = df_market_today["clausula_actual"].tolist() if "clausula_actual" in df_market_today.columns else [0] * len(pid_col)
        avg_col = df_market_today["media_puntos"].tolist() if "media_puntos" in df_market_today.columns else [0] * len(pid_col)

        pt_val_base = float(xp_point_value_eur) if xp_point_value_eur is not None else max(50_000.0, float(self.rules.bonus_per_point_eur) * 1.2)
        idle_boost_raw = float(np.clip((float(idle_cash_eur) - 5_000_000.0) / 8_000_000.0, 0.0, 3.2))
        cap = getattr(self, "league_ratio_cap", 2.20)
        min_avg = getattr(self, "clause_min_avg_pts", 4.8)
        min_dx = getattr(self, "clause_min_delta_xp", 0.75)

        candidates: List[Dict[str, Any]] = []
        for raw_pid, raw_own, raw_nom, raw_eq, raw_pr, raw_sub, raw_cl, raw_avg in zip(
            pid_col, own_col, nom_col, eq_col, pr_col, sub_col, cl_col, avg_col
        ):
            try:
                pid = int(float(raw_pid))
            except (ValueError, TypeError):
                continue

            if pid <= 0 or pid in locked:
                continue

            owner = str(raw_own or "").strip()
            if not owner or owner in ("Libre", "Mercado", "nan", "None", my_manager_name):
                continue

            xp_info = xp_map.get(pid)
            if not xp_info or not xp_info.get("is_available", False):
                continue

            price = safe_num(raw_pr, 0.0)
            if price <= 0:
                continue
            subida = safe_num(raw_sub, 0.0)
            raw_clause = safe_num(raw_cl, 0.0)
            clause = raw_clause if raw_clause > 0 else float(self.rules.compute_official_clause(price))
            if clause <= 0 or clause > max_budget:
                continue

            avg_pts_raw = safe_num(str(raw_avg).replace(",", "."))
            efr_72h = float(efr_map.get(pid, subida * 2.4))
            xp_best = float(xp_info.get("xp_best", 0.0))
            xp_healthy = float(xp_info.get("xp_healthy", xp_best))
            best_line = str(xp_info.get("best_line", xp_info.get("natural_pos", "MF")))
            valid_lines = {x.strip() for x in str(xp_info.get("valid_lines", best_line)).split(",") if x.strip()}
            p_start = float(xp_info.get("p_start", 0.0))
            xi_floor = float(my_current_xi_floor_by_pos.get(best_line, 3.5))
            delta_xp = max(0.0, xp_best - xi_floor)

            # High-value / Tier-1 peaked stars frequently oscillate slightly (-10k..-45k/day) in Biwenger
            # without losing XI point ceiling; only exclude heavy crashes or negative low-quality fluff.
            rel_tick_tol = max(35_000.0, price * 0.0065)
            is_elite_rebound = (xp_healthy >= 4.9 or avg_pts_raw >= 4.9) and subida >= -rel_tick_tol and efr_72h >= -(rel_tick_tol * 2.5)
            if (subida < 0 or efr_72h < 0) and not is_elite_rebound:
                continue

            clause_ratio = clause / price
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

            shock_pts = float(shocks.get(pid, 0.0))
            shock_wave_eur = (
                max(0.0, shock_pts - 6.0) * 145_000.0
                if price < 3_500_000
                else max(0.0, shock_pts - 7.0) * 190_000.0
            )
            proj_12d_gain = float(efr_72h) * 2.85 + shock_wave_eur
            proj_12d_price = min(price + proj_12d_gain, price * 4.5 + 1_800_000.0)

            is_scarce_anchor = (scarcity_tier in ("IRREPLACEABLE_FREE", "SCARCE") or xp_best >= 5.3) and p_start >= 0.78
            is_tier1_superstar = (xp_healthy >= 6.2 or scarcity_tier == "IRREPLACEABLE_FREE" or price >= 12_000_000.0)
            idle_boost = 1.0 + (idle_boost_raw if is_tier1_superstar else 0.55 * idle_boost_raw)
            if is_scarce_anchor:
                horizon_matchdays = float(np.clip(0.55 * float(remaining_rounds), 9.5, 20.0))
                eff_pt_val = max(pt_val_base, 88_000.0 if float(idle_cash_eur) >= 6_000_000.0 else 72_000.0)
            else:
                horizon_matchdays = 8.0 if p_start >= 0.80 else (6.0 if p_start >= 0.70 else 3.5)
                eff_pt_val = pt_val_base
            xi_points_eur = delta_xp * horizon_matchdays * eff_pt_val * idle_boost if p_start >= 0.65 else 0.0

            strategic_bonus_eur = 0.0
            if owner in top_rivals:
                strategic_bonus_eur += min(650_000.0, clause * 0.04)
            if best_line in prio_pos:
                strategic_bonus_eur += 350_000.0

            total_12d_valuation = proj_12d_price + xi_points_eur + strategic_bonus_eur
            net_alpha_eur = total_12d_valuation - clause - float(clause_alpha_floor_delta_eur)

            # Avoid destroying equity on >1.40x clauses for ordinary starters when comparable free agents abound
            abundant_overpay_block = (
                scarcity_tier == "ABUNDANT"
                and n_free_comp >= 2
                and xp_best < 5.5
                and clause_ratio > 1.38
            )
            # Block >1.50x GK overpay when already rostering a starting GK (cheap free GKs abound)
            gk_overpay_block = (
                "GK" in valid_lines
                and xi_floor >= 2.2
                and clause_ratio > 1.50
            )
            # Also block >1.68x clause overpays on sub-6.5M stagnant mid-tier starters lacking irreplaceable scarcity
            mid_tier_overpay_block = (
                price < 6_500_000.0
                and xp_healthy < 5.2
                and efr_72h < 105_000.0
                and scarcity_tier != "IRREPLACEABLE_FREE"
                and clause_ratio > 1.68
            )
            overpay_blocked = abundant_overpay_block or gk_overpay_block or mid_tier_overpay_block

            is_micro_breakout = False
            is_star_steal = False
            if self.policy == "VALUE_COMPOUND_VORP":
                # Synergy of EFR capital compounding + scarcity + genuine XI VORP delta
                league_ratio_cap = cap if (float(idle_cash_eur) >= 4_000_000.0 or n_free_comp <= 1) else min(cap, 1.95)
                is_micro_breakout = (
                    not gk_overpay_block
                    and clause <= 5_200_000
                    and clause_ratio <= min(cap, 2.20)
                    and p_start >= 0.68
                    and efr_72h >= 105_000
                    and (subida >= 50_000 or shock_pts >= 8.0)
                    and (scarcity_tier != "ABUNDANT" or xp_best >= 5.0)
                    and proj_12d_price >= clause * 0.94
                    and net_alpha_eur >= max(220_000.0, clause * 0.06)
                )
                is_star_steal = (
                    not overpay_blocked
                    and p_start >= 0.72
                    and (xp_best >= 4.4 or avg_pts_raw >= min_avg)
                    and delta_xp >= max(0.45, min_dx * 0.85)
                    and clause_ratio <= league_ratio_cap
                    and (scarcity_tier in ("IRREPLACEABLE_FREE", "SCARCE") or delta_xp >= 0.95 or efr_72h >= 90_000)
                    and net_alpha_eur >= max(280_000.0, clause * 0.04)
                )
            elif self.policy == "COMPETITIVE_VORP":
                league_ratio_cap = cap if float(idle_cash_eur) >= 5_000_000.0 else min(cap, 1.95)
                is_micro_breakout = (
                    not gk_overpay_block
                    and clause <= 5_000_000
                    and clause_ratio <= min(cap, 2.20)
                    and p_start >= 0.68
                    and (subida >= 65_000 or shock_pts >= 8.5)
                    and (scarcity_tier != "ABUNDANT" or xp_best >= 5.0)
                    and net_alpha_eur >= max(240_000.0, clause * 0.08)
                    and proj_12d_price >= clause * 0.94
                )
                is_star_steal = (
                    not overpay_blocked
                    and p_start >= 0.72
                    and (xp_best >= 4.4 or avg_pts_raw >= min_avg)
                    and delta_xp >= min_dx
                    and clause_ratio <= league_ratio_cap
                    and net_alpha_eur >= max(320_000.0, clause * 0.05)
                )
            elif self.policy == "SURGICAL_SCARCITY":
                has_surplus_buffer = float(idle_cash_eur) >= (clause + 1_200_000.0)
                is_micro_breakout = (
                    has_surplus_buffer
                    and not gk_overpay_block
                    and clause <= 4_200_000
                    and clause_ratio <= min(cap, 1.85)
                    and p_start >= 0.72
                    and (subida >= 95_000 or shock_pts >= 11.0)
                    and net_alpha_eur >= max(380_000.0, clause * 0.14)
                    and proj_12d_price >= clause * 1.02
                    and idle_cash_eur < 30_000_000.0
                )
                flush = idle_boost >= 1.55
                ultra_flush = float(idle_cash_eur) >= 13_000_000.0
                max_ratio = min(cap, 1.80 if not ultra_flush else 2.00)
                scarcity_gate = (n_free_comp <= 1 and scarcity_tier in ("IRREPLACEABLE_FREE", "SCARCE")) or (clause_ratio <= 1.38)
                subida_ok = subida >= (0 if not is_scarce_anchor else -rel_tick_tol) if flush else (subida >= 20_000 or is_scarce_anchor)
                is_star_steal = (
                    not overpay_blocked
                    and float(idle_cash_eur) >= (clause + 400_000.0)
                    and scarcity_gate
                    and p_start >= 0.76
                    and delta_xp >= max(0.55, min_dx if not ultra_flush else min_dx * 0.85)
                    and clause_ratio <= max_ratio
                    and subida_ok
                    and net_alpha_eur >= max(450_000.0, clause * (0.06 if flush else 0.10))
                )
            else:  # AGGRESSIVE
                is_micro_breakout = (
                    not gk_overpay_block
                    and clause <= 5_400_000
                    and (subida >= 50_000 or shock_pts >= 8.0)
                    and (scarcity_tier != "ABUNDANT" or xp_best >= 5.0)
                    and net_alpha_eur >= max(200_000.0, clause * 0.09)
                    and proj_12d_price >= clause * 0.94
                    and idle_cash_eur < 35_000_000.0
                )
                flush = idle_boost >= 1.45
                ultra_flush = float(idle_cash_eur) >= 12_000_000.0
                max_ratio = min(cap, 2.05 if not ultra_flush else 2.30)
                is_star_steal = (
                    not overpay_blocked
                    and p_start >= 0.70
                    and (xp_best >= 4.2 or avg_pts_raw >= min_avg * 0.92)
                    and delta_xp >= max(0.45, min_dx * 0.85)
                    and clause_ratio <= max_ratio
                    and net_alpha_eur >= max(350_000.0, clause * 0.05)
                )

            if is_micro_breakout or is_star_steal:
                candidates.append({
                    "player_id": pid,
                    "nombre": str(raw_nom or ""),
                    "equipo": str(raw_eq or ""),
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
        xp_idx: Optional[Dict[int, Dict[str, Any]]] = None,
        shield_budget_pct: float = 0.18,
        shield_threshold_mult: float = 1.0,
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
            or float(shield_budget_pct) <= 0.01
        ):
            return []

        locked = clause_locked_ids or set()
        xp_map = xp_idx if xp_idx is not None else self._build_xp_idx(xp_table)
        shields: List[Dict[str, Any]] = []
        shield_budget = available_cash_surplus * float(np.clip(shield_budget_pct, 0.02, 0.40))
        inc_ratio = max(0.25, float(self.rules.clause_increment_ratio))
        deadweight_rate = max(0.10, 1.0 - float(self.rules.clause_decrement_refund_pct))
        thresh_mult = float(np.clip(shield_threshold_mult, 0.65, 1.35))

        pid_c = my_squad_df["player_id"].tolist()
        nom_c = my_squad_df["nombre"].tolist() if "nombre" in my_squad_df.columns else [""] * len(pid_c)
        pr_c = my_squad_df["precio"].tolist() if "precio" in my_squad_df.columns else [0] * len(pid_c)
        sub_c = my_squad_df["subida_24h"].tolist() if "subida_24h" in my_squad_df.columns else [0] * len(pid_c)
        cl_c = my_squad_df["clausula_actual"].tolist() if "clausula_actual" in my_squad_df.columns else [0] * len(pid_c)

        for raw_pid, raw_nom, raw_pr, raw_sub, raw_cl in zip(pid_c, nom_c, pr_c, sub_c, cl_c):
            pid = int(raw_pid)
            if pid in locked:
                continue

            xp_info = xp_map.get(pid, {})
            xp_best = float(xp_info.get("xp_best", 0.0))
            xp_healthy = float(xp_info.get("xp_healthy", xp_best))
            p_start = float(xp_info.get("p_start", 0.0))
            best_line = str(xp_info.get("best_line", xp_info.get("natural_pos", "MF")))
            price = safe_num(raw_pr, 0.0)
            subida = safe_num(raw_sub, 0.0)
            clause = safe_num(raw_cl, 0.0) or float(compute_official_clause(price))
            efr_72h = float(efr_map.get(pid, subida * 2.4))

            if xp_best < 5.6 * min(1.05, max(0.90, thresh_mult)) or p_start < 0.80 or subida < 70_000 * thresh_mult:
                continue
            if clause > max_rival_purchasing_power * 0.95:
                continue

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

                expected_steal_loss = 0.30 * max(replacement_spread, proj_10d - clause)
                if increment_eur >= 250_000 and shield_budget >= increment_eur and expected_steal_loss >= (2.2 * thresh_mult) * deadweight_cost:
                    shields.append({
                        "player_id": pid,
                        "nombre": str(raw_nom or ""),
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
