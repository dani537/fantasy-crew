"""
Fantasy Crew - Multi-Agent System (Simplified Architecture)
===========================================================

Entry point for running the Fantasy Crew using the streamlined LangGraph orchestration.

Workflow (action mode):
  1. DataExtraction (Deterministic Python Pipeline)
  2. Coach (Tactical Analysis)
  3. SportingDirector (Executive Decisor)
  4. ExecuteActions (Biwenger API)
  5. GenerateReports (JSON & Markdown)
  6. EmailReport (HTML Notification)

Briefing mode: read-only extraction + morning newspaper email (no actions, 1 LLM call).

Usage:
    python main.py                    # action mode (any time of day)
    python main.py --mode auction     # auction moment (auto-detects the ~7:00 reset,
                                      # acts if there is margin, then waits and cleans up)
    python main.py --mode briefing    # morning briefing (post-reset, read-only + cleanup)
"""

import sys
from datetime import datetime
from src.graph import fantasy_crew_graph
from src.config import GeneralSettings


def run_fantasy_crew():
    """
    Runs the streamlined Fantasy Crew multi-agent workflow.
    """
    print("=" * 65)
    print("🚀 BIWENGER AGENT - Streamlined Multi-Agent Workflow")
    print(f"📅 Run Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    if GeneralSettings.DRY_RUN:
        print("🧪 DRY-RUN MODE: no write operations will be sent to Biwenger")
    print("=" * 65)
    
    # Initial state
    initial_state = {
        "df_master": None,
        "coach_report": {},
        "sd_decisions": {},
        "approved_actions": None,
        "execution_results": None,
        "final_report": None,
        "email_sent": False,
        "error": None
    }
    
    print("\n🔄 Executing agent workflow...\n")
    
    try:
        for step in fantasy_crew_graph.stream(initial_state):
            for node_name, node_output in step.items():
                if node_name == "data_extraction":
                    rows = len(node_output.get("df_master", [])) if node_output.get("df_master") is not None else 0
                    print(f"   📊 [DataExtraction] Master DataFrame generated with {rows} players.")
                
                elif node_name == "coach":
                    print("   📋 [Coach] Tactical analysis and squad recommendations complete.")
                
                elif node_name == "sporting_director":
                    print("   💼 [SportingDirector] Executive decisions generated (Lineup, Bids, Sales).")

                elif node_name == "execute_actions":
                    results = node_output.get("execution_results", [])
                    print(f"   ⚡ [ExecuteActions] Executed {len(results)} operations in Biwenger API.")
                    for res in results:
                        print(f"      • {res}")
                
                elif node_name == "generate_reports":
                    print("   📄 [GenerateReports] Final reports generated in ./reports/")

                elif node_name == "send_email":
                    sent = node_output.get("email_sent", False)
                    print(f"   📧 [EmailReport] Report email sent: {sent}")
        
        print("\n" + "=" * 65)
        print("✅ BIWENGER AGENT RUN COMPLETE!")
        print("=" * 65)
        print("📂 Reports saved in ./reports/:")
        print("   • 00_final_report.md (Consolidated Report)")
        print("   • 01_coach_report.json (Tactical Analysis)")
        print("   • 02_sporting_director_decisions.json (Executive Operations)")
        print("=" * 65)
        
    except Exception as e:
        print(f"\n❌ Fatal Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    mode = "action"
    if "--mode" in sys.argv:
        idx = sys.argv.index("--mode")
        if idx + 1 < len(sys.argv):
            mode = sys.argv[idx + 1].strip().lower()

    if mode == "briefing":
        from src.briefing import run_briefing
        run_briefing()
    elif mode == "auction":
        from src.auction import run_auction
        run_auction()
    else:
        run_fantasy_crew()
