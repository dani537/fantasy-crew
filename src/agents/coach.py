from src.llm_endpoints.deepseek import DeepseekClient
from src.data_extraction.pipeline import print_step
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
        Returns "Dani SR" as fallback if file not found.
        """
        try:
            if os.path.exists('./data/user_info.csv'):
                df_user = pd.read_csv('./data/user_info.csv')
                if not df_user.empty:
                    return df_user['team_name'].iloc[0]
        except Exception as e:
            print(f"⚠️ Warning: Could not read user_info.csv: {e}")
            
        return "Dani SR" # Fallback

    def analyze(self, df_master):
        """
        Analyzes the squad using data and LLM.
        Args:
            df_master (pd.DataFrame): The consolidated player data.
        """
        print_step(20, "Coach (The Mister) is analyzing the squad")

        # 1. Identify "My Team"
        my_team_name = self.get_my_team_name()
        print(f"   ℹ️ Analyzing squad for team: '{my_team_name}'")

        # 2. Filter my players
        if 'BIWPLAYER_TEAM_NAME' in df_master.columns:
            my_squad = df_master[df_master['BIWPLAYER_TEAM_NAME'] == my_team_name].copy()
        else:
            print("⚠️ Warning: 'BIWPLAYER_TEAM_NAME' column not found in data.")
            my_squad = pd.DataFrame()
        
        if my_squad.empty:
            print(f"⚠️ Coach Warning: No players found for team '{my_team_name}'.")
            return f"Could not analyze squad: Team '{my_team_name}' not found."

        # 3. Load Context (Date, Next Match, Odds)
        from datetime import datetime
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        next_match_info = "No upcoming match information found."
        is_home_game = None
        
        # 3. Load Context (Date, Next Match, Odds)
        from datetime import datetime
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        jornada_info = "Unknown Jornada"
        
        if os.path.exists('./data/next_match.csv'):
            try:
                df_next = pd.read_csv('./data/next_match.csv')
                if not df_next.empty:
                    # Get general Jornada info from the first match
                    first_match = df_next.iloc[0]
                    jornada_name = first_match['NEXT_MATCH_JORNADA']
                    start_date = first_match['NEXT_MATCH_FECHA']
                    jornada_info = f"{jornada_name} (Starts: {start_date})"
            except Exception as e:
                print(f"⚠️ Warning reading next_match.csv: {e}")

        # 4. Preparing Squad Data for Prompt
        # We need specific columns. If some are missing in df_master, handle gracefully.
        relevant_cols = [
            'PLAYER_NAME', 'TEAM_NAME', 'PLAYER_POSITION', 'PLAYER_ALT_POSITIONS', 
            'PLAYER_STATUS', 'COMUNIATE_STARTER', 
            'PLAYER_FITNESS', 'AVG_POINTS', 'ODDS_1', 'ODDS_X', 'ODDS_2' 
        ]
        # Note: AVG_POINTS_HOME/AWAY usually helpful, but simple AVG_POINTS + ODDS might be enough to save tokens 
        # unless user strictly wants Home/Away splits. User mentioned "AVG_POINTS_HOME y AVG_POINTS_AWAY".
        # Let's keep them if available.
        relevant_cols += ['AVG_POINTS_HOME', 'AVG_POINTS_AWAY']

        # Select existing columns
        cols_to_use = [c for c in relevant_cols if c in my_squad.columns]
        squad_view = my_squad[cols_to_use].copy()
        
        squad_summary = squad_view.to_markdown(index=False)
        
        # 5. Construct the Super Prompt
        prompt = f"""
        ROLE:
        You are "The Mister", an expert Fantasy Football Manager and Head Coach.
        Current Date/Time: {current_time}
        
        OBJECTIVE:
        Maximize points for the upcoming **{jornada_info}**.
        
        YOUR TEAM: "{my_team_name}"
        
        SQUAD STATUS (with Odds for player's upcoming match):
        {squad_summary}
        
        RULES & TACTICS:
        1. **Formations**: You can use 3-4-3 (preferred for points), 3-5-2, 4-3-3, 4-4-2, 5-4-1, 5-3-2. Adapt based on available players.
        2. **Empty Positions**: An empty position in the lineup penalizes **-4 POINTS**. This must be avoided at all costs.
        3. **Player Positions**: 
           - 'PLAYER_POSITION' is the primary position.
           - 'PLAYER_ALT_POSITIONS' are valid alternatives. Use them to fill gaps.
        4. **Availability**:
           - Check 'PLAYER_STATUS' (ok, injured, suspended...).
           - Check 'COMUNIATE_STARTER' (probability of starting). High % is better.
        5. **Performance**:
           - 'PLAYER_FITNESS': Momentum (last 5 games points, left=most recent). High numbers are good.
           - 'AVG_POINTS': General average.
           - 'RELEVANT_AVG': Performance in current context (Home vs Away). Use this to decide similar doubts.
        6. **Teammates & Goalkeepers**: 
           - Use 'TEAM_NAME' to identify players from the same club.
           - If you have a starter GK and a substitute GK from the SAME team, you are covered (safe pair).
        
        MARKET STRATEGY:
        - You MUST list exactly **5 players** to put on the Transfer Market.
        - Specify if the sale is "REAL" (player not needed/bad form) or "RESERVE" (listing just to receive offers in case of injury/crash, but keeping him for now).
        
        OUTPUT FORMAT (Markdown):
        Provide a concise professional "COACH REPORT" in the following language: {GeneralSettings.LANGUAGE}.
        
        ## 🧠 Match Analysis
        Brief analysis of the rival and difficulty (based on odds/stats).
        
        ## 📋 Squad Status
        Summary of health, critical injuries, or great form (momentum).
        
        ## ⚽ Recommended Lineup
        **Formation**: [e.g. 3-4-3]
        - **GK**: [Name]
        - **DF**: [Names]
        - **MF**: [Names]
        - **FW**: [Names]
        *(Explain key decisions if controversial)*
        
        ## 🚨 Urgent Needs
        If we risk a -4 penalty or have no starters in a line, shout: "WE NEED [POSITION]" (Translate "WE NEED" to target language).
        
        ## 💰 Market Strategy (For Sale)
        List 5 players to list on market:
        1. [Name] - [REAL/RESERVE] - [Reason]
        2. ...
        """
        
        # Generate Report
        report = self.llm.generate_content(prompt, system_prompt="You are an expert Fantasy Football Coach.")
        
        if report:
            print("📝 Coach Report Generated")
            return report
        else:
            return "Error generating Coach Report."
