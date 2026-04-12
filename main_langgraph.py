"""
Fantasy Crew - LangGraph Multi-Agent System
============================================

Entry point for running the Fantasy Crew using LangGraph orchestration.

Usage:
    python main_langgraph.py

This version uses LangGraph StateGraph for:
- Explicit workflow visualization
- State management across agents
- Conditional routing (President can reject → loop back to SD)
- Checkpointing and debugging support
"""

from datetime import datetime
from src.graph import fantasy_crew_graph


def run_fantasy_crew_langgraph():
    """
    Runs the Fantasy Crew multi-agent system using LangGraph.
    """
    print("=" * 60)
    print("🚀 FANTASY CREW - LangGraph Multi-Agent System")
    print(f"📅 Run Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    # Initial state
    initial_state = {
        "df_master": None,
        "coach_report": "",
        "sd_proposals": "",
        "coach_critique": "",
        "president_decision": "",
        "approved_actions": None,
        "execution_results": None,
        "iteration_count": 0,
        "max_iterations": 2,
        "decision_status": "pending",
        "error": None
    }
    
    # Execute the graph
    print("\n🔄 Executing LangGraph workflow...\n")
    
    try:
        # Stream execution to see progress
        for step in fantasy_crew_graph.stream(initial_state):
            # Print which node is executing
            for node_name, node_output in step.items():
                if node_name == "data_analyst":
                    print("🚀 Node: DataAnalyst - Extracting and processing data...")
                    if node_output.get("error"):
                        print(f"   ❌ Error: {node_output['error']}")
                    else:
                        rows = len(node_output.get("df_master", [])) if node_output.get("df_master") is not None else 0
                        print(f"   ✅ df_master generated with {rows} rows")
                
                elif node_name == "coach":
                    print("🚀 Node: Coach - Analyzing squad...")
                    if node_output.get("error"):
                        print(f"   ❌ Error: {node_output['error']}")
                    else:
                        print("   📝 Coach Report Generated")
                
                elif node_name == "sporting_director":
                    print("🚀 Node: SportingDirector - Scanning market...")
                    if node_output.get("error"):
                        print(f"   ❌ Error: {node_output['error']}")
                    else:
                        print("   💼 Transfer Proposals Generated")

                elif node_name == "debate":
                    print("🚀 Node: Debate - Coach critiques SD proposals...")
                    if node_output.get("error"):
                        print(f"   ❌ Error: {node_output['error']}")
                    else:
                        print("   🗣️ Coach Critique Generated")
                
                elif node_name == "president":
                    print("🚀 Node: President - Making decision...")
                    status = node_output.get("decision_status", "unknown")
                    iteration = node_output.get("iteration_count", 0)
                    print(f"   🏛️ Decision: {status.upper()} (Iteration {iteration})")

                elif node_name == "execute_actions":
                    print("🚀 Node: Execute Actions - Running API operations...")
                    results = node_output.get("execution_results", [])
                    for res in results:
                        print(f"   ⚙️ {res}")
                
                elif node_name == "generate_reports":
                    print("🚀 Node: GenerateReports - Saving reports...")
        
        print("\n" + "=" * 60)
        print("✅ FANTASY CREW LANGGRAPH RUN COMPLETE!")
        print("=" * 60)
        print("📂 Reports saved in ./reports/:")
        print("   • 00_final_report.md (consolidated)")
        print("   • 01_coach_report.md")
        print("   • 02_sporting_director_proposals.md")
        print("   • 03_president_decision.md")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ Fatal Error: {e}")
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    run_fantasy_crew_langgraph()
