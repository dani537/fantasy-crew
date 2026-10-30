"""
91-Day Strict Causal Backtesting Suite (`src/tools/backtester`)
===============================================================
Simulates Biwenger AZ Finance (15 managers, scoreID=5, official clauses,
25% credit line, +-5% Computer offers, postponed-round lock & recalculation,
and exact reverse-handicap matchday bonuses) from 2026-08-01 to 2026-10-30
with zero temporal lookahead.
"""

from src.tools.backtester.causal_clock import StrictCausalDataView, CausalEFRPredictor
from src.tools.backtester.market_env import AZFinanceMarketEnv
from src.tools.backtester.benchmark_suite import BacktestBenchmarkSuite

__all__ = [
    "StrictCausalDataView",
    "CausalEFRPredictor",
    "AZFinanceMarketEnv",
    "BacktestBenchmarkSuite",
]
