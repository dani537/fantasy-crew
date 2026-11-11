"""
League Season Lifecycle & Macro-Phase Engine (`lifecycle_manager.py`)
====================================================================
Quantitative macro-regime detector that replaces static round thresholds with
live market scarcity signals, calendar density, and FIFA international breaks:
1. `EARLY_EXPANSION` (Abundant Free Market: >= 20 free players >= 5M€):
   - Fast portfolio compounding on market auctions (max 5 simultaneous bids, EFR >= 30k).
   - Broad roster (17-19 players) to capture daily market capital growth.
   - Strict `Pillar Lock`: super-stars (Mbappé, Bellingham, Pedri) are NEVER liquidated for day-trading profits.
2. `MATURE_SCARCITY` (Drying Free Market: < 20 free players >= 5M€ or Jornada >= 6):
   - Computer auctions dry up; the ONLY mechanism to acquire elite starters is Release Clauses.
   - Shifts capital allocation: 80% cash reserved for surgical clause snipes against rivals.
   - Activates defensive clause shields on our own starters; trims bench bloat (14-15 players).
3. `ENDGAME_RUNIN` (Final 8 Rounds: Jornadas 31-38):
   - Zero-dividend horizon: day-trading is mathematically dead (assets won't have time to yield NPV).
   - Full liquidation of non-playing bench deadweight; all-in on weekly starting-XI xP.
   - Predatory clauses on direct rivals above us in the standings.
4. `FIFA_BREAK` (International 14-day hiatus):
   - Zero points at risk for 14 days.
   - Absorbs player injuries (<14 days miss 0 rounds!).
   - Freezes premature sales: holds rising assets to harvest 14 days of compound appreciation.
"""

from enum import Enum
from typing import Dict, Any, Optional
import pandas as pd


class SeasonPhase(str, Enum):
    EARLY_EXPANSION = "EARLY_EXPANSION"
    MATURE_SCARCITY = "MATURE_SCARCITY"
    ENDGAME_RUNIN = "ENDGAME_RUNIN"


class LeagueLifecycleEngine:
    """Evaluates the macro-phase and dynamic operational posture for the current date."""

    @staticmethod
    def evaluate_macro_context(
        free_players_ge_5m: int,
        free_players_ge_10m: int,
        current_jornada: int,
        total_jornadas: int = 38,
        days_to_next_match: int = 4,
        is_international_break: bool = False,
    ) -> Dict[str, Any]:
        """
        Derives operational constraints based on live market scarcity and calendar state.
        """
        jn = max(1, int(current_jornada))
        tot_j = max(38, int(total_jornadas))
        remaining_rounds = max(0, tot_j - jn)

        # 1. Determine Macro Regime
        if remaining_rounds <= 8:
            phase = SeasonPhase.ENDGAME_RUNIN
        elif free_players_ge_5m <= 18 or jn >= 6:
            phase = SeasonPhase.MATURE_SCARCITY
        else:
            phase = SeasonPhase.EARLY_EXPANSION

        # 2. Derive Phase-Aware Strategic Posture
        if phase == SeasonPhase.EARLY_EXPANSION:
            max_simultaneous_bids = 5
            min_efr_threshold = 30_000
            clause_budget_fraction = 0.25          # 25% cash to clauses, 75% to auction compounding
            target_roster_size = 19                # Deep roster for maximum daily appreciation
            allow_pillar_liquidation = False       # Pillars strictly protected from day-trading
            overbid_ceiling_pct = 0.025
        elif phase == SeasonPhase.MATURE_SCARCITY:
            max_simultaneous_bids = 2
            min_efr_threshold = 70_000             # Only bid on market for high-conviction momentum
            clause_budget_fraction = 0.80          # 80% cash reserved for star clause snipes
            target_roster_size = 15                # Lean squad: 11 starters + 4 key rotations
            allow_pillar_liquidation = False
            overbid_ceiling_pct = 0.040
        else:  # ENDGAME_RUNIN
            max_simultaneous_bids = 1
            min_efr_threshold = 999_999            # Zero day-trading: no time to harvest capital gains
            clause_budget_fraction = 1.00          # 100% liquidity dedicated to starting-XI upgrades
            target_roster_size = 12                # 11 starters + 1 goalkeeper backup
            allow_pillar_liquidation = False
            overbid_ceiling_pct = 0.080

        # 3. Calendar Event: FIFA Break Optimization
        fifa_break_hold_momentum = bool(is_international_break and days_to_next_match >= 5)

        return {
            "phase": phase.value,
            "current_jornada": jn,
            "remaining_rounds": remaining_rounds,
            "free_players_ge_5m": int(free_players_ge_5m),
            "free_players_ge_10m": int(free_players_ge_10m),
            "is_fifa_break": bool(is_international_break),
            "days_to_next_match": int(days_to_next_match),
            "fifa_break_hold_momentum": fifa_break_hold_momentum,
            "max_simultaneous_bids": max_simultaneous_bids,
            "min_efr_threshold": min_efr_threshold,
            "clause_budget_fraction": clause_budget_fraction,
            "target_roster_size": target_roster_size,
            "allow_pillar_liquidation": allow_pillar_liquidation,
            "overbid_ceiling_pct": overbid_ceiling_pct,
        }
