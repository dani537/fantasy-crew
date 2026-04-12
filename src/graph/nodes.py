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


def debate_node(state: AgentState) -> dict:
    """
    Node: Debate (Coach critiques SD proposals)
    ---------------------------------------------
    The Coach reviews the Sporting Director's proposals and flags
    any tactical concerns before the President makes the final call.
    """
    if state.get("error"):
        return {"coach_critique": f"Skipped due to error: {state['error']}"}
    
    try:
        from src.llm_endpoints.deepseek import DeepseekClient
        from src.prompts.coach_prompts import get_coach_critique_prompt
        from src.prompts.system_roles import COACH_CRITIQUE_SYSTEM_ROLE
        import os
        import pandas as pd

        llm = DeepseekClient()

        # Get team name
        my_team_name = "Unknown"
        if os.path.exists('./data/user_info.csv'):
            df_user = pd.read_csv('./data/user_info.csv')
            if not df_user.empty:
                my_team_name = df_user['team_name'].iloc[0]

        prompt = get_coach_critique_prompt(
            my_team_name=my_team_name,
            coach_report=state["coach_report"],
            sd_proposals=state["sd_proposals"],
        )
        
        critique = llm.generate_content(prompt, system_prompt=COACH_CRITIQUE_SYSTEM_ROLE)
        
        if critique:
            print("🗣️ Coach Critique Generated")
            return {"coach_critique": critique, "error": None}
        else:
            return {"coach_critique": "No critique generated.", "error": None}
    except Exception as e:
        return {
            "coach_critique": "",
            "error": f"Debate Error: {str(e)}"
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
        coach_critique = state.get("coach_critique", "No critique available.")
        
        decision = president.decide(coach_report, sd_proposals, df_master, coach_critique)
        
        # Extract JSON from the decision
        import re
        import json
        approved_actions = None
        json_match = re.search(r'```json\s*(.*?)\s*```', decision, re.DOTALL)
        if json_match:
            try:
                # Pre-process to remove potential comments //
                json_str = json_match.group(1)
                json_str_clean = re.sub(r'//.*', '', json_str)
                approved_actions = json.loads(json_str_clean)
                print(f"   ✅ Parsed President Actions: {len(approved_actions)} categories found.")
            except json.JSONDecodeError as e:
                print(f"   ⚠️ Warning: Could not parse President JSON: {e}")
        else:
            print("   ⚠️ Warning: No JSON block found in President's decision.")
        
        # Increment iteration count
        iteration_count = state.get("iteration_count", 0) + 1
        
        # For now, always approve (no loop-back logic yet)
        return {
            "president_decision": decision,
            "decision_status": "approved",
            "iteration_count": iteration_count,
            "approved_actions": approved_actions,
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
    Sends a synthesized, premium HTML summary via email and attaches the raw report and data files.
    """
    import os
    import json
    import re
    from jinja2 import Template
    from src.llm_endpoints.deepseek import DeepseekClient
    from src.prompts.email_prompts import get_email_summary_prompt, EMAIL_SUMMARY_SYSTEM_ROLE
    from src.utils.email_templates import BASE_HTML_TEMPLATE
    from src.utils.email_sender import send_report_email
    
    final_report = state.get("final_report", "")
    
    # Ensure we have the report content
    if not final_report:
        try:
            with open("./reports/00_final_report.md", 'r', encoding='utf-8') as f:
                final_report = f.read()
        except:
            final_report = "No documentation available for this run."
    
    print("🚀 Node: EmailReport - Generating Premium LLM summary...")
    friendly_summary = "Aquí tienes el reporte de hoy de tu Fantasy Crew. Los detalles y archivos adjuntos se encuentran abajo."
    html_content = None

    try:
        llm = DeepseekClient()
        summary_prompt = get_email_summary_prompt(final_report)
        llm_output = llm.generate_content(summary_prompt, system_prompt=EMAIL_SUMMARY_SYSTEM_ROLE)
        
        # Parse the JSON output from LLM
        json_match = re.search(r'\{.*\}', llm_output, re.DOTALL)
        if json_match:
            segments = json.loads(json_match.group(0))
            
            # Render Jinja2 Template
            template = Template(BASE_HTML_TEMPLATE)
            html_content = template.render(
                headline=segments.get("headline", "Reporte de Jornada"),
                introduction=segments.get("introduction", "Hola Manager,"),
                debate_summary=segments.get("debate_summary", ""),
                president_verdict=segments.get("president_verdict", ""),
                actions_html=segments.get("actions_html", "")
            )
            
            # Plain text fallback
            friendly_summary = f"{segments.get('headline')}\n\n{segments.get('introduction')}\n\n{segments.get('president_verdict')}"
        else:
            print("   ⚠️ LLM did not return valid JSON for summary.")
    except Exception as e:
        print(f"   ⚠️ HTML content generation failed: {e}")

    # Collect attachments
    attachments = []
    # 1. The raw report
    if os.path.exists("./reports/00_final_report.md"):
        attachments.append("./reports/00_final_report.md")
    
    # 2. Excel and important CSVs from data/
    important_patterns = [
        "./data/_master.xlsx",
        "./data/_master.csv",
        "./data/market_sales.csv",
        "./data/user_info.csv"
    ]
    for pattern in important_patterns:
        if os.path.exists(pattern):
            attachments.append(pattern)
    
    success = send_report_email(friendly_summary, attachments=attachments, html_content=html_content)
    
    return {"email_sent": success}

def execute_actions_node(state: AgentState) -> dict:
    """
    Node: Execute Actions
    ---------------------
    Executes the approved JSON orders in Biwenger using BiwengerActions.
    """
    approved_actions = state.get("approved_actions")
    if not approved_actions:
        print("   ℹ️ No approved actions found in state. Skipping execution.")
        return {"execution_results": ["No actions to execute."]}

    print("🚀 Node: ExecuteActions - Firing automated requests...")
    results = []

    try:
        from src.data_extraction.auth import BiwengerAuth
        from src.actions import BiwengerActions
        from src.config import Credentials

        auth = BiwengerAuth(Credentials.BIWENGER_USERNAME, Credentials.BIWENGER_PASSWORD)
        auth.login()
        player_info = auth.get_user_info()
        session = auth.get_session()
        
        # Mandatory headers for Biwenger actions
        session.headers.update({
            'x-league': str(player_info.league_id),
            'x-user': str(player_info.team_id)
        })
        
        if not session:
            return {"execution_results": ["❌ Login failed, skipping actions."]}

        actions = BiwengerActions(session)

        # 1. Update Lineup
        lineup_data = approved_actions.get("lineup")
        if lineup_data and "formation" in lineup_data and "player_ids" in lineup_data:
            print(f"   ⚽ Setting lineup: {lineup_data['formation']}")
            success = actions.lineup.set_lineup(lineup_data["formation"], lineup_data["player_ids"])
            results.append(f"Lineup {lineup_data['formation']}: {'Success' if success else 'Failed'}")

        # 2. Process Sales (Placing players on market)
        sales = approved_actions.get("sales", [])
        for sale in sales:
            pid = sale.get("player_id")
            price = sale.get("price", 0)
            if pid:
                print(f"   🏷️ Selling player {pid} for {price}€")
                success = actions.market.place_player_on_market(pid, price)
                results.append(f"Sell Player {pid}: {'Success' if success else 'Failed'}")

        # 3. Process Bids (Offers / Buying)
        bids = approved_actions.get("bids", [])
        for bid in bids:
            pid = bid.get("player_id")
            amount = bid.get("amount", 0)
            to_user = bid.get("to_user_id")
            if pid and amount:
                print(f"   💰 Bidding {amount}€ for player {pid}")
                success = actions.market.place_offer(amount, pid, to_user)
                results.append(f"Bid {amount}€ for Player {pid}: {'Success' if success else 'Failed'}")

        return {"execution_results": results}

    except Exception as e:
        print(f"   ❌ Execution Error: {e}")
        return {"execution_results": [f"Error: {str(e)}"]}

