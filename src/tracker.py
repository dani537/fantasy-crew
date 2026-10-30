"""
Compatibility Proxy for Rival Financial Tracker
================================================
Canonical implementation has been organized into:
  src.tools.rival_tracker.tracker

This proxy preserves full backward-compatibility for any legacy imports.
"""

from src.tools.rival_tracker.tracker import BiwengerSheetsTracker, _clean_id

__all__ = ["BiwengerSheetsTracker", "_clean_id"]

if __name__ == "__main__":
    import sys
    tracker = BiwengerSheetsTracker()
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    tracker.sync(days_back=days)
