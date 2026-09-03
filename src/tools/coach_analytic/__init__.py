"""
Coach Analytic Tool
====================
Provides tactical squad analysis, formation selection, multiposition exploitation,
and lineup submission capabilities for Biwenger.
"""

from src.tools.coach_analytic.coach import (
    CoachAnalytic,
    run_coach_analytic,
    get_latest_coach_report,
    format_coach_response_md,
    COACH_REQUIRED_COLUMNS
)
from src.tools.coach_analytic.prompts import (
    get_coach_analysis_prompt,
    COACH_SYSTEM_ROLE
)
from src.tools.coach_analytic.lineup import (
    validate_lineup,
    order_lineup_for_api,
    OFFICIAL_FORMATIONS
)
from src.tools.coach_analytic.actions import (
    LineupActions
)

__all__ = [
    "CoachAnalytic",
    "run_coach_analytic",
    "format_coach_response_md",
    "COACH_REQUIRED_COLUMNS",
    "get_coach_analysis_prompt",
    "COACH_SYSTEM_ROLE",
    "validate_lineup",
    "order_lineup_for_api",
    "OFFICIAL_FORMATIONS",
    "LineupActions"
]
