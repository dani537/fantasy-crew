"""
Strategy Module
================
Deterministic decision-support layer that sits BETWEEN the LLM agents and the
Biwenger API. LLMs propose; this layer validates, corrects and (if needed)
replaces their output with safe, data-driven decisions.

- lineup.py     → Optimal XI selection (formations, positions, ordering).
- guardrails.py → Hard safety rules for sales and bids (financial protection).
"""

from src.strategy.lineup import select_best_lineup, FORMATIONS
from src.strategy.guardrails import filter_sales, filter_bids, compute_squad_needs

__all__ = [
    "select_best_lineup",
    "FORMATIONS",
    "filter_sales",
    "filter_bids",
    "compute_squad_needs",
]
