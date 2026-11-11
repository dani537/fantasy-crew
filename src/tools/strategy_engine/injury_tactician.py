"""
Injury Tactical Decision Engine (`injury_tactician.py`)
======================================================
Crosses medical status, projected recovery horizon, player portfolio role,
and real match schedule (including FIFA international breaks) to make optimal
sports-finance decisions:
- `HOLD_PILLAR`: Superstars (Mbappé, Pedri, Raphinha) with short/medium absence (<= 3 rounds)
  are strictly held and covered by bench patches. Selling them destroys structural ceiling!
- `HOLD_FIFA_BREAK`: Injuries overlapping a 14-day international break miss 0 rounds. Held!
- `SELL_CASH_OUT`: Medium/long-term injuries (>= 3 rounds) on depreciating assets or catastrophic
  injuries (>= 6 rounds) are sold immediately before value crashes, freeing capital for a healthy starter.
"""

from enum import Enum
from typing import Dict, Any, Optional


class InjuryAction(str, Enum):
    HOLD_PILLAR = "HOLD_PILLAR"
    HOLD_FIFA_BREAK = "HOLD_FIFA_BREAK"
    SELL_CASH_OUT = "SELL_CASH_OUT"
    BENCH_PATCH_COVER = "BENCH_PATCH_COVER"


class InjuryTacticalDecisionEngine:
    """Evaluates whether to HOLD, COVER WITH BENCH, or SELL an injured or doubtful player."""

    @staticmethod
    def decide(
        player_id: int,
        nombre: str,
        estado: str,
        absence_days_est: int,
        missed_rounds_calendar: int,
        is_fifa_break: bool,
        asset_role: str,
        efr_72h: float,
        market_price: float,
        best_market_alternative_xp: float,
        player_healthy_xp: float,
    ) -> Dict[str, Any]:
        st = str(estado or "ok").strip().lower()
        is_core = (asset_role == "FRANCHISE_CORE")
        is_starter = (asset_role == "STRUCTURAL_STARTER")
        missed = max(0, int(missed_rounds_calendar))
        days = max(0, int(absence_days_est))

        # 1. Healthy / Available / Minor Duda without missed matches
        if missed == 0 and (days <= 4 or st in ("ok", "disponible", "")):
            return {
                "action": InjuryAction.HOLD_PILLAR if (is_core or is_starter) else InjuryAction.BENCH_PATCH_COVER,
                "should_sell": False,
                "reason": "Disponible o duda leve sin impacto en jornadas.",
            }

        # 2. FIFA Break Absorption: 14 days without matches absorbs medium downtime
        if is_fifa_break and missed <= 1 and days <= 16:
            return {
                "action": InjuryAction.HOLD_FIFA_BREAK,
                "should_sell": False,
                "reason": f"Baja de {days} días absorbida casi 100% por el parón FIFA ({missed} jornada afectada).",
            }

        # 3. Franchise Core Pillar (Mbappé, Pedri, Bellingham)
        if is_core:
            # If missing <= 3 rounds, NEVER sell! Cover with bench patch.
            if missed <= 3:
                return {
                    "action": InjuryAction.HOLD_PILLAR,
                    "should_sell": False,
                    "reason": f"Pilar franquicia con baja asumible ({missed}J). Sustituir temporalmente con parche.",
                }
            # Only liquidate if injury is truly catastrophic (>= 6 rounds / ligament tear)
            # AND a top alternative is available immediately in the market
            if missed >= 6 and best_market_alternative_xp >= player_healthy_xp * 0.85:
                return {
                    "action": InjuryAction.SELL_CASH_OUT,
                    "should_sell": True,
                    "reason": f"Baja catastrófica de {missed}J en pilar. Venta para reinversión en titular TOP activo.",
                }
            return {
                "action": InjuryAction.HOLD_PILLAR,
                "should_sell": False,
                "reason": "Pilar franquicia protegido contra ventas precipitadas.",
            }

        # 4. Structural Starter
        if is_starter:
            # Short absence (1 round): hold with bench cover
            if missed <= 1:
                return {
                    "action": InjuryAction.HOLD_PILLAR,
                    "should_sell": False,
                    "reason": "Baja leve (1 jornada). Se cubre con rotación de banquillo.",
                }
            # Medium absence (>= 2 rounds): sell if price is crashing or market offers immediate upgrade
            if missed >= 2 and (efr_72h <= -35_000 or best_market_alternative_xp >= player_healthy_xp * 0.95):
                return {
                    "action": InjuryAction.SELL_CASH_OUT,
                    "should_sell": True,
                    "reason": f"Titular con baja de {missed}J y desvalorización activa ({int(efr_72h):,}€/72h). Venta para comprar titular sano.",
                }
            # If stable price and misses 2 rounds: hold
            if missed <= 2 and efr_72h > -35_000:
                return {
                    "action": InjuryAction.HOLD_PILLAR,
                    "should_sell": False,
                    "reason": f"Titular con precio estable y baja moderada ({missed}J). Aguante táctico.",
                }

        # 5. Non-core / Speculative / Bench Patch
        if missed >= 1:
            return {
                "action": InjuryAction.SELL_CASH_OUT,
                "should_sell": True,
                "reason": f"Activo secundario lesionado ({missed}J). Venta inmediata para liberar hueco y liquidez.",
            }

        return {
            "action": InjuryAction.BENCH_PATCH_COVER,
            "should_sell": False,
            "reason": "Rotación de banquillo estándar.",
        }
