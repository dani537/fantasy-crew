"""
COACH AGENT (The Mister)
=========================

Role:
-----
Expert Fantasy Football Manager and Head Coach. The main objective is to maximize 
points for the upcoming jornada by selecting the best lineup and optimizing the squad.

Workflow:
---------
1. **Identify User Team**:
   - Retrieves the team name strictly from `./data/user_info.csv`.
   - Aborts analysis with an error if the team name is missing (no hardcoded fallbacks).
2. **Filter Squad**:
   - Filters the consolidated `df_master` to isolate players belonging to the identified team.
3. **Gather Context**:
   - Loads upcoming Jornada details (name, dates) from `./data/next_match.csv`.
   - Captures current timestamp for context.
4. **Prepare Technical Metrics**:
   - **Positions**: Primary and valid alternatives for each player.
   - **Availability**: Match status (injured, suspended, ok) and starter probability (%).
   - **Form**: Recent momentum (fitness) and scoring averages (total, home, and away).
   - **Rivals**: Betting odds for the player's upcoming match to gauge difficulty.
5. **Apply Coaching Logic (LLM)**:
   - **Tactics**: Selection of best formation (3-4-3, 3-5-2, 4-3-3, etc.).
   - **Points**: High priority on avoiding empty slots (-4 pt penalty).
   - **Master Move**: Apply scoring rules (**DF:+5, MF:+4, FW:+3** per goal).
   - **Optimization**: Strategic placement of versatile players in the most "defensive" valid line to maximize goal points (e.g., placing a FW/MF as a MF).
   - **Decisions**: Use Home/Away split stats and odds to resolve technical doubts.
   - **Reliability**: Ensure goalkeeper coverage (starter + sub from same team) and handle doubtful players.
6. **Generate Coach Report**:
   - **Match Analysis**: Rival difficulty and match context.
   - **Squad Status**: Health summary and standout performers.
   - **Recommended Lineup**: Chosen formation and player list with justifications.
   - **Urgent Needs**: Alerts if specific positions are missing or at risk of penalty.
   - **Market Strategy**: List of 5 players for sale (REAL or RESERVE) with reasoning.

Information Used:
-----------------
- **user_info.csv**: Crucial for team identification.
- **next_match.csv**: Context for the upcoming Jornada and rivals.
- **df_master**: The source of truth for player stats, status, and probabilities.
"""

from src.llm_endpoints.deepseek import DeepseekClient
from src.data_extraction.transformers import print_step
import pandas as pd
import os

from src.config import GeneralSettings

class Coach:
    """
    The Mister.
    Role: Analyzes the squad status, lineups, and performance to define sporting needs.
    """
    def __init__(self):
        self.llm = DeepseekClient()

    def get_my_team_name(self):
        """
        Retrieves the user's team name from the persisted user_info.csv.
        Returns None if file not found.
        """
        try:
            if os.path.exists('./data/user_info.csv'):
                df_user = pd.read_csv('./data/user_info.csv')
                if not df_user.empty:
                    return df_user['team_name'].iloc[0]
        except Exception as e:
            print(f"⚠️ Warning: Could not read user_info.csv: {e}")
            
        return None

    def analyze(self, df_master):
        """
        Analyzes the squad using data and LLM.
        Args:
            df_master (pd.DataFrame): The consolidated player data.
        """
        print_step(20, "Coach (The Mister) is analyzing the squad")

        # 1. Identify "My Team"
        my_team_name = self.get_my_team_name()
        if not my_team_name:
            print("❌ Coach Error: 'team_name' not found in ./data/user_info.csv. Cannot proceed.")
            return {"error": "Could not analyze squad: Team name missing in user_info.csv."}

        print(f"   ℹ️ Analyzing squad for team: '{my_team_name}'")

        # 2. Filter my players
        if 'BIWPLAYER_TEAM_NAME' in df_master.columns:
            my_squad = df_master[df_master['BIWPLAYER_TEAM_NAME'] == my_team_name].copy()
        else:
            print("⚠️ Warning: 'BIWPLAYER_TEAM_NAME' column not found in data.")
            my_squad = pd.DataFrame()
        
        if my_squad.empty:
            print(f"⚠️ Coach Warning: No players found for team '{my_team_name}'.")
            return {"error": f"Could not analyze squad: Team '{my_team_name}' not found."}

        # 3. Load Context (Date, Next Match, Jornada)
        from datetime import datetime
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        jornada_info = "Unknown Jornada"
        matches_summary = "No match data available."
        
        if os.path.exists('./data/next_jornada.csv'):
            try:
                df_next = pd.read_csv('./data/next_jornada.csv')
                if not df_next.empty:
                    # Get general Jornada info from the first match
                    first_match = df_next.iloc[0]
                    jornada_name = first_match['NEXT_MATCH_JORNADA']
                    start_date = first_match['NEXT_MATCH_FECHA']
                    jornada_info = f"{jornada_name} (Starts: {start_date})"
                    
                    # Build match context table
                    match_cols = ['NEXT_MATCH_LOCAL', 'NEXT_MATCH_VISITANTE', 'NEXT_MATCH_FECHA', 'ODDS_1', 'ODDS_X', 'ODDS_2']
                    existing_match_cols = [c for c in match_cols if c in df_next.columns]
                    matches_summary = df_next[existing_match_cols].to_markdown(index=False)
            except Exception as e:
                print(f"⚠️ Warning reading next_jornada.csv: {e}")

        # 4. Preparing Squad Data for Prompt
        # We need specific columns. If some are missing in df_master, handle gracefully.
        relevant_cols = [
            'PLAYER_ID', 'PLAYER_NAME', 'TEAM_NAME', 'TEAM_IS_HOME', 'PLAYER_POSITION', 'PLAYER_ALT_POSITIONS', 
            'PLAYER_STATUS', 'COMUNIATE_STARTER', 
            'EXPECTED_POINTS', 'AVG_POINTS_MOMENTUM', 'MOMENTUM_TREND',
            'PLAYER_FITNESS', 'AVG_POINTS', 
            'PLAYER_PRICE'
        ]

        # Select existing columns
        cols_to_use = [c for c in relevant_cols if c in my_squad.columns]
        squad_view = my_squad[cols_to_use].copy()
        
        squad_summary = squad_view.to_markdown(index=False)
        
        # 5. Construct the Prompt (loaded from src/prompts/)
        from src.prompts.coach_prompts import get_coach_analysis_prompt
        from src.prompts.system_roles import COACH_SYSTEM_ROLE
        from src.strategy.guardrails import compute_squad_needs

        needs = compute_squad_needs(my_squad)
        squad_needs_summary = (
            f"Squad size: {needs['squad_size']} players ({needs['fit_players']} fit). "
            f"By line: {needs['counts']}. {needs['summary']}"
        )

        prompt = get_coach_analysis_prompt(
            current_time=current_time,
            jornada_info=jornada_info,
            my_team_name=my_team_name,
            matches_summary=matches_summary,
            squad_summary=squad_summary,
            squad_needs_summary=squad_needs_summary,
        )
        
        from src.utils.json_helper import extract_json_from_llm
        
        # Generate Report
        response_text = self.llm.generate_content(prompt, system_prompt=COACH_SYSTEM_ROLE)
        
        if response_text:
            print("📝 Coach JSON Generated")
            return extract_json_from_llm(response_text)
        else:
            return {"error": "Error generating Coach Report."}

