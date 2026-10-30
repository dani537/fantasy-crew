"""
Rival Financial & League Tracker Tool
======================================
Tracks rival league transfers, estimated cash balances, initial configs,
and updates Google Sheets and local data/rival_financials.csv.
"""

from src.tools.rival_tracker.tracker import BiwengerSheetsTracker, _clean_id

__all__ = ["BiwengerSheetsTracker", "_clean_id"]
