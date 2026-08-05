"""
Test Full Workflow
==================
Executes the full streamlined Biwenger Agent workflow and prints a 
detailed terminal breakdown of the tactical recommendations, transfer decisions, 
API operations executed, and generated reports.

Usage:
    .venv/bin/python test/test_full_workflow.py
"""

import sys
import os
import json
import time

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from main import run_fantasy_crew


def main():
    print("=" * 70)
    print("🧪 BIWENGER AGENT - FULL WORKFLOW INTEGRATION TEST")
    print("=" * 70)
    
    start_time = time.time()
    run_fantasy_crew()
    elapsed = time.time() - start_time
    
    print(f"\n⏱️ Total Execution Time: {elapsed:.2f} seconds")
    
    # Inspect generated JSON files
    reports = {
        'Coach Report': './reports/01_coach_report.json',
        'Sporting Director Decisions': './reports/02_sporting_director_decisions.json',
        'Final Report': './reports/00_final_report.md'
    }
    
    print("\n🔍 --- REPORT INTEGRITY CHECK ---")
    for name, fpath in reports.items():
        if os.path.exists(fpath):
            size_bytes = os.path.getsize(fpath)
            print(f"✅ {name}: {fpath} ({size_bytes:,} bytes)")
        else:
            print(f"❌ {name}: {fpath} MISSING")

    # Display Executive Summary of Decisions
    if os.path.exists('./reports/02_sporting_director_decisions.json'):
        print("\n💼 --- EXECUTED DECISIONS SUMMARY ---")
        try:
            with open('./reports/02_sporting_director_decisions.json', 'r', encoding='utf-8') as f:
                decisions = json.load(f)
                print(json.dumps(decisions, indent=2, ensure_ascii=False))
        except Exception as e:
            print(f"Could not parse JSON report: {e}")


if __name__ == "__main__":
    main()
