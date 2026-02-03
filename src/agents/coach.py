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
            return "Could not analyze squad: Team name missing in user_info.csv."

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

        # 3. Load Context (Date, Next Match, Jornada)
        from datetime import datetime
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        jornada_info = "Unknown Jornada"
        matches_summary = "No match data available."
        
        if os.path.exists('./data/next_match.csv'):
            try:
                df_next = pd.read_csv('./data/next_match.csv')
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
                print(f"⚠️ Warning reading next_match.csv: {e}")

        # 4. Preparing Squad Data for Prompt
        # We need specific columns. If some are missing in df_master, handle gracefully.
        relevant_cols = [
            'PLAYER_NAME', 'TEAM_NAME', 'TEAM_IS_HOME', 'PLAYER_POSITION', 'PLAYER_ALT_POSITIONS', 
            'PLAYER_STATUS', 'COMUNIATE_STARTER', 
            'PLAYER_FITNESS', 'AVG_POINTS', 'AVG_POINTS_HOME', 'AVG_POINTS_AWAY',
            'PLAYER_PRICE', 'BIWPLAYER_PURCHASE_PRICE', 'SALE_PROFIT'
        ]

        # Select existing columns
        cols_to_use = [c for c in relevant_cols if c in my_squad.columns]
        squad_view = my_squad[cols_to_use].copy()
        
        squad_summary = squad_view.to_markdown(index=False)
        
        # 5. Construct the Super Prompt
        prompt = f"""
ROLE: You are "The Mister", an expert Fantasy Football Manager and Head Coach.
Current Date/Time: {current_time}

OBJECTIVE: Maximize points for the upcoming **{jornada_info}**.
YOUR TEAM: "{my_team_name}"

---
## UPCOMING MATCHES (Context for Odds & Difficulty)
{matches_summary}

---
## YOUR SQUAD
{squad_summary}

---
## FIELD DEFINITIONS
- **TEAM_IS_HOME**: `True` = team plays at home, `False` = plays away. Use `AVG_POINTS_HOME` if True, else `AVG_POINTS_AWAY`.
- **PLAYER_STATUS**: 'ok' (available), 'injured', 'sanctioned' (suspended), 'doubt' (uncertain).
- **COMUNIATE_STARTER**: Probability of starting (1.0 = 100%, 0.0 = 0%). Prefer higher values.
- **PLAYER_FITNESS**: Last 5 match points `[most_recent, ..., oldest]`. Text values mean the player didn't score (e.g., 'sanctioned').
- **AVG_POINTS / AVG_POINTS_HOME / AVG_POINTS_AWAY**: Scoring averages. Compare home/away based on `TEAM_IS_HOME`.
- **ODDS_1 / ODDS_X / ODDS_2**: Win probabilities (Home Win / Draw / Away Win) from betting odds. Higher ODDS_1 when playing home = easier match.
- **BIWPLAYER_PURCHASE_PRICE**: Amount paid to acquire the player (via market or clause).
- **SALE_PROFIT**: PLAYER_PRICE - PURCHASE_PRICE. Negative = selling at LOSS. Positive = selling at PROFIT.

---
## RULES & TACTICS

> [!CAUTION]
> **POSITION RULE**: Players can ONLY be placed in their `PLAYER_POSITION` or `PLAYER_ALT_POSITIONS`. 
> A DF cannot play as FW. A FW cannot play as GK. NEVER place a player in an invalid position.

1. **Formations**: 3-4-3 (preferred), 3-5-2, 4-3-3, 4-4-2, 5-4-1, 5-3-2.
2. **Empty Positions**: Penalizes **-4 POINTS**. Avoid at all costs.
3. **Scoring Strategy**: Goals give **DF (+5), MF (+4), FW (+3)**. Place versatile players in the most "defensive" valid line.
4. **Goalkeeper Safety**: If you have 2 GKs from the SAME TEAM, you have automatic coverage. **DO NOT recommend selling the backup GK if they share a team with your starter.**

---
## MARKET STRATEGY
List exactly **5 players** to sell:
- **REAL**: Not needed / bad form.
- **RESERVE**: List to receive offers, but keep for now.

> [!WARNING]
> **SALE LOSS RULE**: AVOID recommending sales where `SALE_PROFIT` is negative (selling at a loss).
> Exception: Long-term injuries or players with no future value.
> If a player was acquired via an expensive clause, selling them now likely means a significant loss.

---
## OUTPUT FORMAT

### 🧠 Match Analysis
Brief rival/difficulty analysis using odds.

### 📋 Squad Status
Health summary, injuries, standout form.

### ⚽ Recommended Lineup
**Formation**: [e.g. 3-4-3]
- **GK**: [Name]
- **DF**: [Names]
- **MF**: [Names]
- **FW**: [Names]
*(Justify key decisions)*

### 🚨 Urgent Needs
If risk of -4 penalty: "NECESSITEM [POSITION]" (or equivalent in target language).

### 💰 Market Strategy (For Sale)
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
