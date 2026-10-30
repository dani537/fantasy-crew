"""
24/7 Aggressive Release Clause Sniper & Defensive Shield Optimizer (`ClauseSniper`)
===================================================================================
Exploits AZ Finance's official clause bracket asymmetries:
  - Base <= 2M € -> 225%   | 2M-3M € -> 5M € abs | 3M-5M € -> 200%
  - 5M-6.5M € -> 10M € abs | 6.5M-9M € -> 175%   | 9M-12M € -> 16M € abs
  - 12M-15M € -> 150%      | 15M-20M € -> 24M €  | 20M-25M € -> 135%
  - 25M-30M € -> 35M €     | > 30M € -> 125%

Offensive Edge ("A Machete", 7 days/week):
1. Breakout Snipes: When a sub-3M rival player erupts (high `subida_24h`, high
   `efr_72h`, or fresh weekend match points >= 9 before market catches up),
   their clause is locked to yesterday's tiny base while 10-day value + points
   blow past the clause.
2. Under-Claused Star Snipes: High-xP starters (`xP >= 5.5`) in upper brackets
   (`125% - 150%`) whose clause/price ratio is compressed and who upgrade our XI.

Defensive Edge (`ShieldOptimizer`):
Recommends surgical clause increments ONLY on our own exposed core starters
when liquid rivals can afford their clause and expected replacement loss > 2.5x cost.
"""

from typing import List, Dict, Any, Set, Optional
import numpy as np
import pandas as pd
from src.tools.data_extraction.warehouse import compute_official_clause
from src.utils.numeric import safe_num


class ClauseSniper:
    """Offensive release-clause scanner & defensive shield calculator."""

    def __init__(self, policy: str = "AGGRESSIVE"):
        self.policy = (policy or "AGGRESSIVE").upper()

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
    ) -> List[Dict[str, Any]]:
        """
        Returns ranked rival players whose release clause offers positive net alpha.
        """
        if self.policy == "DISABLED" or df_market_today is None or df_market_today.empty or max_budget <= 300_000:
            return []

        locked = locked_player_ids or set()
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
            clause = raw_clause if raw_clause > 0 else float(compute_official_clause(price))

            if clause <= 0 or clause > max_budget or price <= 0:
                continue

            # Never snipe falling players
            efr_72h = float(efr_map.get(pid, subida * 2.4))
            if subida < 0 or efr_72h < 0:
                continue

            xp_best = float(xp_info.get("xp_best", 0.0))
            best_line = str(xp_info.get("best_line", xp_info.get("natural_pos", "MF")))
            p_start = float(xp_info.get("p_start", 0.0))
            xi_floor = float(my_current_xi_floor_by_pos.get(best_line, 3.5))
            delta_xp = max(0.0, xp_best - xi_floor)

            # Project 12-day Trajectory (Breakout wave + Match Shock impulse)
            shock_pts = float(shocks.get(pid, 0.0))  # e.g., points above expectation in weekend match
            shock_wave_eur = max(0.0, shock_pts - 6.0) * 145_000.0 if price < 3_500_000 else max(0.0, shock_pts - 7.0) * 190_000.0

            # Autoregressive 12-day momentum projection (decays smoothly)
            proj_12d_gain = float(efr_72h) * 2.85 + shock_wave_eur
            # Cap unrealistic extrapolations on micro-caps at 4.5x current price + 1.8M
            proj_12d_price = min(price + proj_12d_gain, price * 4.5 + 1_800_000.0)

            # Value of extra starting-XI points (at 60k €/pt compounding weekly prize + league title edge)
            idle_boost = 1.0 + float(np.clip((float(idle_cash_eur) - 5_000_000.0) / 8_000_000.0, 0.0, 3.5))
            horizon_matchdays = 8.0 if p_start >= 0.80 else (6.0 if p_start >= 0.70 else 3.5)
            xi_points_eur = delta_xp * horizon_matchdays * 60_000.0 * idle_boost if p_start >= 0.65 else 0.0

            total_12d_valuation = proj_12d_price + xi_points_eur
            net_alpha_eur = total_12d_valuation - clause
            clause_ratio = clause / price

            # Strict Conviction Gates so we ONLY fire lethal, high-ROI clausulazos:
            is_micro_breakout = (
                clause <= 3_800_000
                and (subida >= 60_000 or shock_pts >= 9.0)
                and net_alpha_eur >= max(280_000.0, clause * 0.16)
                and proj_12d_price >= clause * 1.05  # resale value alone must cover the 225% clause
                and idle_cash_eur < 30_000_000.0     # When ultra-flush (>30M), focus 100% on XI stars!
            )
            flush = idle_boost >= 1.6  # >= 10M € idle: star clauses become the primary engine of victory
            ultra_flush = float(idle_cash_eur) >= 20_000_000.0
            # Financial discipline: never pay uncompressed >138% clauses unless a breakout star has massive appreciation
            max_ratio = 1.38 if not ultra_flush else (1.45 if subida >= 80_000 else 1.38)
            is_star_steal = (
                p_start >= 0.75
                and delta_xp >= (0.70 if ultra_flush else 0.90)
                and clause_ratio <= max_ratio
                and subida >= (0 if flush else 40_000)
                and net_alpha_eur >= max(750_000.0, clause * (0.08 if flush else 0.12))
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
    ) -> List[Dict[str, Any]]:
        """
        Identifies our own core XI stars dangerously exposed to rival clause steals.
        Never spends more than 20% of positive surplus cash on shields.
        """
        if self.policy == "DISABLED" or my_squad_df is None or my_squad_df.empty or available_cash_surplus < 1_500_000:
            return []

        xp_idx = xp_table.set_index("player_id").to_dict("index") if (xp_table is not None and not xp_table.empty) else {}
        shields: List[Dict[str, Any]] = []
        shield_budget = available_cash_surplus * 0.20

        for _, r in my_squad_df.iterrows():
            pid = int(r["player_id"])
            xp_info = xp_idx.get(pid, {})
            xp_best = float(xp_info.get("xp_best", 0.0))
            p_start = float(xp_info.get("p_start", 0.0))
            price = safe_num(r.get("precio", 0), 0.0)
            subida = safe_num(r.get("subida_24h", 0), 0.0)
            clause = safe_num(r.get("clausula_actual", 0), 0.0) or float(compute_official_clause(price))
            efr_72h = float(efr_map.get(pid, subida * 2.4))

            # Only shield elite surging starters that rivals can actually afford
            if xp_best < 5.8 or p_start < 0.80 or subida < 70_000:
                continue
            if clause > max_rival_purchasing_power * 0.95:
                continue

            proj_10d = price + max(0.0, efr_72h * 2.6)
            if clause < proj_10d * 1.22:
                # Surgical increment: raise clause to 1.32x projected 10d price (rounded to 100k)
                desired_clause = np.ceil((proj_10d * 1.32) / 100_000.0) * 100_000.0
                # Note: in Biwenger AZ Finance, 1€ spent increases clause typically by 2€ (or 1:1 depending on setting; assume conservative 1:2)
                increment_eur = int(min(shield_budget, max(250_000.0, (desired_clause - clause) * 0.5)))
                if increment_eur >= 250_000 and shield_budget >= increment_eur:
                    shields.append({
                        "player_id": pid,
                        "nombre": str(r.get("nombre", "")),
                        "current_clause": int(round(clause)),
                        "market_price": int(round(price)),
                        "recommended_cash_spend": increment_eur,
                        "reason": f"Titular élite ({xp_best:.1f} xP, +{int(subida):,}€/d) expuesto a liquidez rival",
                    })
                    shield_budget -= increment_eur

        return shields
