"""
PRESIDENT AGENT (The Strategist)
=================================

Role:
-----
The highest authority with a GLOBAL VISION. The President reconciles the perspectives of
the Coach (sporting needs) and Sporting Director (market operations) to make final decisions
that maximize long-term success.

CORE OBJECTIVES (In Priority Order):
-------------------------------------
1. **POSITIVE BALANCE**: ALWAYS ensure positive balance (€0+) at jornada start.
   - Negative balance = 0 POINTS for the entire team. This is non-negotiable.
   
2. **NO EMPTY POSITIONS**: Avoid gaps in the lineup that trigger -4 point penalties.
   - If the Coach signals URGENT NEEDS, these must be addressed.

3. **MAXIMIZE POINTS**: Accept transfers that improve the starting XI's expected points.

4. **GROW SQUAD VALUE**: Strategic asset management - buy low, sell high, invest wisely.

Decision Framework:
-------------------
The President receives:
- **Coach Report**: Lineup, urgent positional needs, players recommended for sale.
- **Sporting Director Proposals**: Market targets, clause opportunities, sale prices.
- **Financial Status**: Current balance, projected balance after operations.
- **Clause Deadline**: Whether the clause window is still open.

The President then:
1. Validates that ALL operations maintain positive balance.
2. Prioritizes signings that cover urgent needs (avoid -4 penalty).
3. Approves/Rejects each proposal with strategic reasoning.
4. Issues the FINAL EXECUTIVE ORDER with specific actions.

Output:
-------
A clear, actionable Executive Order that can be executed by the user.
"""

from src.llm_endpoints.deepseek import DeepseekClient
from src.data_extraction.pipeline import print_step
import pandas as pd
import os
from datetime import datetime, timedelta

class President:
    """
    The Strategist.
    Role: Highest authority. Validates proposals based on long-term strategy, 
    financial safety, and sporting needs.
    """
    def __init__(self):
        self.llm = DeepseekClient()

    def get_my_team_name(self):
        """Retrieves the user's team name from user_info.csv."""
        try:
            if os.path.exists('./data/user_info.csv'):
                df_user = pd.read_csv('./data/user_info.csv')
                if not df_user.empty:
                    return df_user['team_name'].iloc[0]
        except Exception as e:
            print(f"⚠️ Warning: Could not read user_info.csv: {e}")
        return "Unknown Team"

    def get_budget_info(self):
        """Retrieves the current balance/budget from user_info.csv."""
        try:
            if os.path.exists('./data/user_info.csv'):
                df_user = pd.read_csv('./data/user_info.csv')
                if not df_user.empty:
                    for col in ['balance', 'credit', 'money', 'budget']:
                        if col in df_user.columns:
                            return float(df_user[col].iloc[0])
        except Exception as e:
            print(f"⚠️ Warning: Could not read budget from user_info.csv: {e}")
        return 0.0

    def get_jornada_info(self):
        """Gets next jornada name and start time."""
        try:
            if os.path.exists('./data/next_match.csv'):
                df_next = pd.read_csv('./data/next_match.csv')
                if not df_next.empty:
                    first_match = df_next.iloc[0]
                    jornada_name = first_match['NEXT_MATCH_JORNADA']
                    start_date = first_match['NEXT_MATCH_FECHA']
                    return jornada_name, start_date
        except Exception as e:
            print(f"⚠️ Warning: Could not read next_match.csv: {e}")
        return "Unknown", "Unknown"

    def get_clause_deadline(self):
        """Calculates clause deadline (48h before jornada)."""
        try:
            if os.path.exists('./data/next_match.csv'):
                df_next = pd.read_csv('./data/next_match.csv')
                if not df_next.empty:
                    first_match_date = pd.to_datetime(df_next['NEXT_MATCH_FECHA'].iloc[0])
                    clause_deadline = first_match_date - timedelta(hours=48)
                    now = datetime.now(clause_deadline.tzinfo) if clause_deadline.tzinfo else datetime.now()
                    is_open = now < clause_deadline
                    return clause_deadline.strftime("%Y-%m-%d %H:%M"), is_open
        except Exception as e:
            print(f"⚠️ Warning: Could not calculate clause deadline: {e}")
        return "Unknown", False

    def get_squad_position_summary(self, df_master):
        """Counts available players per position in my squad."""
        my_team = self.get_my_team_name()
        summary = {"GK": 0, "DF": 0, "MF": 0, "FW": 0}
        
        if 'BIWPLAYER_TEAM_NAME' not in df_master.columns:
            return summary
            
        my_squad = df_master[df_master['BIWPLAYER_TEAM_NAME'] == my_team]
        
        if 'PLAYER_POSITION' in my_squad.columns:
            for pos in ['GK', 'DF', 'MF', 'FW']:
                # Count players with this position (including alt positions)
                count = len(my_squad[my_squad['PLAYER_POSITION'] == pos])
                summary[pos] = count
        
        return summary

    def decide(self, coach_report, sporting_director_proposals, df_master, coach_critique="No critique available."):
        """
        Makes the final decision on all proposals with full context.
        
        Args:
            coach_report (str): The Coach's analysis and recommendations.
            sporting_director_proposals (str): The Sporting Director's transfer plan.
            df_master (pd.DataFrame): The master data for additional context.
            coach_critique (str): The Coach's critique of the SD's proposals (debate round).
        """
        print_step(22, "President (The Strategist) is reviewing proposals")
        
        # Gather all context
        my_team = self.get_my_team_name()
        current_balance = self.get_budget_info()
        jornada_name, jornada_start = self.get_jornada_info()
        clause_deadline, clause_open = self.get_clause_deadline()
        position_summary = self.get_squad_position_summary(df_master)
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        # Format position summary
        pos_str = ", ".join([f"{pos}: {count}" for pos, count in position_summary.items()])
        total_players = sum(position_summary.values())
        
        # Build MY SQUAD ROSTER with real player_ids
        my_squad_roster = "No squad data available."
        if 'BIWPLAYER_TEAM_NAME' in df_master.columns:
            my_squad = df_master[df_master['BIWPLAYER_TEAM_NAME'] == my_team].copy()
            if not my_squad.empty:
                roster_cols = ['PLAYER_ID', 'PLAYER_NAME', 'PLAYER_POSITION', 'PLAYER_PRICE']
                # Add optional useful columns if they exist
                for col in ['EXPECTED_POINTS', 'PLAYER_STATUS', 'AVG_POINTS_MOMENTUM']:
                    if col in my_squad.columns:
                        roster_cols.append(col)
                available_cols = [c for c in roster_cols if c in my_squad.columns]
                roster_df = my_squad[available_cols].sort_values('PLAYER_POSITION')
                my_squad_roster = roster_df.to_markdown(index=False)
        
        # Check for potential issues
        warnings = []
        if current_balance < 0:
            warnings.append("⚠️ **NEGATIVE BALANCE**: Immediate action required to avoid 0 points!")
        if current_balance < 500000:
            warnings.append("⚠️ **LOW BALANCE**: Risk of going negative after operations.")
        if position_summary.get('GK', 0) < 2:
            warnings.append("⚠️ **GK RISK**: Less than 2 goalkeepers. Risk of -4 penalty.")
        
        warnings_str = "\n".join(warnings) if warnings else "✅ No critical warnings."

        from src.prompts.president_prompts import get_president_decision_prompt
        from src.prompts.system_roles import PRESIDENT_SYSTEM_ROLE

        prompt = get_president_decision_prompt(
            my_team=my_team,
            current_time=current_time,
            current_balance=current_balance,
            jornada_name=jornada_name,
            jornada_start=jornada_start,
            clause_open=clause_open,
            clause_deadline=clause_deadline,
            total_players=total_players,
            pos_str=pos_str,
            warnings_str=warnings_str,
            coach_report=coach_report,
            sporting_director_proposals=sporting_director_proposals,
            coach_critique=coach_critique,
            my_squad_roster=my_squad_roster,
        )
        
        decision = self.llm.generate_content(prompt, system_prompt=PRESIDENT_SYSTEM_ROLE)
        
        if decision:
            print("🏛️ Executive Decision Made")
            return decision
        else:
            return "Error generating Executive Decision."

