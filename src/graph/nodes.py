"""
LangGraph Node Functions for Fantasy Crew Multi-Agent System
=============================================================

Each node is a function that takes the current state and returns
an updated state dictionary.
"""

from src.graph.state import AgentState
from src.agents.data_analyst import DataAnalyst
from src.agents.coach import Coach
from src.agents.sporting_director import SportingDirector
from src.agents.president import President


def data_analyst_node(state: AgentState) -> dict:
    """
    Node: Data Analyst
    ------------------
    Extracts data, performs feature engineering, and generates df_master.
    This is the entry point of the workflow.
    """
    try:
        analyst = DataAnalyst()
        df_master = analyst.run(extract=True)
        return {
            "df_master": df_master,
            "error": None
        }
    except Exception as e:
        return {
            "df_master": None,
            "error": f"DataAnalyst Error: {str(e)}"
        }


def coach_node(state: AgentState) -> dict:
    """
    Node: Coach (The Mister)
    ------------------------
    Analyzes the squad and generates lineup recommendations and market strategy.
    """
    if state.get("error"):
        return {"coach_report": f"Skipped due to error: {state['error']}"}
    
    try:
        coach = Coach()
        df_master = state["df_master"]
        report = coach.analyze(df_master)
        return {
            "coach_report": report,
            "error": None
        }
    except Exception as e:
        return {
            "coach_report": "",
            "error": f"Coach Error: {str(e)}"
        }


def sporting_director_node(state: AgentState) -> dict:
    """
    Node: Sporting Director (The Broker)
    -------------------------------------
    Scans the market and generates transfer proposals based on Coach's report.
    """
    if state.get("error"):
        return {"sd_proposals": f"Skipped due to error: {state['error']}"}
    
    try:
        sd = SportingDirector()
        df_master = state["df_master"]
        coach_report = state["coach_report"]
        proposals = sd.propose(coach_report, df_master)
        return {
            "sd_proposals": proposals,
            "error": None
        }
    except Exception as e:
        return {
            "sd_proposals": "",
            "error": f"SportingDirector Error: {str(e)}"
        }


def president_node(state: AgentState) -> dict:
    """
    Node: President (The Strategist)
    ---------------------------------
    Reviews all proposals and makes the final executive decision.
    """
    if state.get("error"):
        return {
            "president_decision": f"Skipped due to error: {state['error']}",
            "decision_status": "error"
        }
    
    try:
        president = President()
        df_master = state["df_master"]
        coach_report = state["coach_report"]
        sd_proposals = state["sd_proposals"]
        
        decision = president.decide(coach_report, sd_proposals, df_master)
        
        # Increment iteration count
        iteration_count = state.get("iteration_count", 0) + 1
        
        # For now, always approve (no loop-back logic yet)
        return {
            "president_decision": decision,
            "decision_status": "approved",
            "iteration_count": iteration_count,
            "error": None
        }
    except Exception as e:
        return {
            "president_decision": "",
            "decision_status": "error",
            "error": f"President Error: {str(e)}"
        }


def generate_report_node(state: AgentState) -> dict:
    """
    Node: Report Generator
    ----------------------
    Consolidates all agent outputs into final reports.
    """
    import os
    from datetime import datetime
    
    reports_dir = "./reports"
    os.makedirs(reports_dir, exist_ok=True)
    
    # Save individual reports
    with open(f"{reports_dir}/01_coach_report.md", 'w', encoding='utf-8') as f:
        f.write(state.get("coach_report", "No coach report generated."))
    
    with open(f"{reports_dir}/02_sporting_director_proposals.md", 'w', encoding='utf-8') as f:
        f.write(state.get("sd_proposals", "No proposals generated."))
    
    with open(f"{reports_dir}/03_president_decision.md", 'w', encoding='utf-8') as f:
        f.write(state.get("president_decision", "No decision generated."))
    
    # Generate consolidated report
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    final_report = f"""# 🏆 Fantasy Crew - Final Report
**Generated**: {timestamp}

---

## 📋 Coach Report
{state.get("coach_report", "No report available.")}

---

## 💼 Sporting Director Proposals
{state.get("sd_proposals", "No proposals available.")}

---

## 🏛️ President Decision
{state.get("president_decision", "No decision available.")}
"""
    
    with open(f"{reports_dir}/00_final_report.md", 'w', encoding='utf-8') as f:
        f.write(final_report)
    
    print("📄 Saved: ./reports/01_coach_report.md")
    print("📄 Saved: ./reports/02_sporting_director_proposals.md")
    print("📄 Saved: ./reports/03_president_decision.md")
    print("📄 Saved: ./reports/00_final_report.md")
    
    return {"final_report": final_report}


def email_report_node(state: AgentState) -> dict:
    """
    Node: Email Report
    ------------------
    Sends the final report via Gmail.
    """
    from src.utils.email_sender import send_report_email
    
    final_report = state.get("final_report", "")
    
    if not final_report:
        # Try to read from file if not in state
        try:
            with open("./reports/00_final_report.md", 'r', encoding='utf-8') as f:
                final_report = f.read()
        except:
            final_report = "No report content available."
    
    success = send_report_email(final_report)
    
    if success:
        return {"email_sent": True}
    else:
        return {"email_sent": False}
