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

    def decide(self, coach_report, sporting_director_proposals, df_master):
        """
        Makes the final decision on all proposals with full context.
        
        Args:
            coach_report (str): The Coach's analysis and recommendations.
            sporting_director_proposals (str): The Sporting Director's transfer plan.
            df_master (pd.DataFrame): The master data for additional context.
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
        
        # Check for potential issues
        warnings = []
        if current_balance < 0:
            warnings.append("⚠️ **NEGATIVE BALANCE**: Immediate action required to avoid 0 points!")
        if current_balance < 500000:
            warnings.append("⚠️ **LOW BALANCE**: Risk of going negative after operations.")
        if position_summary.get('GK', 0) < 2:
            warnings.append("⚠️ **GK RISK**: Less than 2 goalkeepers. Risk of -4 penalty.")
        
        warnings_str = "\n".join(warnings) if warnings else "✅ No critical warnings."

        prompt = f"""
ROLE: You are the Club President (The Strategist) for "{my_team}".
Current Date/Time: {current_time}

---
## 🏟️ SITUATION OVERVIEW

| Metric | Value |
|--------|-------|
| **Current Balance** | €{current_balance:,.0f} |
| **Next Jornada** | {jornada_name} (Starts: {jornada_start}) |
| **Clause Window** | {"OPEN ✅" if clause_open else "CLOSED ❌"} (Deadline: {clause_deadline}) |
| **Squad Size** | {total_players} players ({pos_str}) |

### ⚠️ ALERTS
{warnings_str}

---
## 🎯 YOUR CORE OBJECTIVES (Priority Order)

1. **POSITIVE BALANCE** (Non-negotiable)
   - We MUST have €0+ at jornada start. Negative = 0 POINTS for the entire team.
   
2. **NO EMPTY POSITIONS** 
   - Every position in the formation must be filled. Empty slot = -4 POINTS penalty.
   
3. **MAXIMIZE POINTS**
   - Accept operations that improve our expected points for the upcoming jornada.
   
4. **GROW SQUAD VALUE**
   - Strategic asset management: buy undervalued, sell overvalued.

---
## 📋 COACH'S REPORT (Sporting Perspective)
*The Coach focuses on lineup, tactics, and immediate needs.*

{coach_report}

---
## 💼 SPORTING DIRECTOR'S PROPOSALS (Market Perspective)
*The Sporting Director focuses on market opportunities and financial operations.*

{sporting_director_proposals}

---
## 📖 DECISION RULES

1. **Validate Financial Safety**: 
   - Calculate: Current Balance + Sales - Purchases = Final Balance
   - If Final Balance < €0 → REJECT operations until balance is safe.

2. **Prioritize Urgent Needs**:
   - If Coach signals "WE NEED [POSITION]" → This signing is HIGH PRIORITY.
   - Avoid -4 penalty at all costs.

3. **Evaluate Value**:
   - Cheap player with good points > Expensive star with marginal improvement.
   - Consider `COST_PER_XP` as the key efficiency metric.

4. **Approve in Order**:
   - First: Sales (to generate liquidity).
   - Second: Signings (using generated liquidity).

> [!CAUTION]
> **CLAUSE PROTECTION RULE (VALUE MAXIMIZATION)**:
> - **Voluntary Sale** → We receive `PLAYER_PRICE` (low market value).
> - **Being Clausuled** → We receive `BIWPLAYER_CLAUSE` (high clause value).
> - If a player was acquired via **expensive clause** and their market price has dropped:
>   - **DO NOT approve voluntary sales** that result in significant losses.
>   - Better to wait for someone to clausule them (we recover the investment).
> - **EXCEPTIONS (Approve sale even at a loss)**:
>   - Long-term injuries (>4 weeks).
>   - **Sustained declining performance**: `MOMENTUM_TREND` very negative over multiple weeks.
>   - Truly unusable players (permanently out of squad rotation).
> - **Maximizing squad VALUE** is a secondary objective after points.


---
## 📄 OUTPUT FORMAT (Executive Order)

### 🏛️ EXECUTIVE SUMMARY
Brief assessment of the current situation and overall strategy.

### ✅ APPROVED OPERATIONS
| # | Operation | Player | Amount | Reason |
|---|-----------|--------|--------|--------|
| 1 | SELL / BUY / CLAUSE | Name | €X | Strategic justification |

### ❌ REJECTED OPERATIONS
| Operation | Player | Reason for Rejection |
|-----------|--------|---------------------|
| ... | ... | ... |

### 💰 FINANCIAL PROJECTION
```
Current Balance:     €{current_balance:,.0f}
+ Approved Sales:    €X
- Approved Purchases: €X
= Final Balance:     €X
```

### 🎯 FINAL ORDERS
Numbered list of SPECIFIC ACTIONS to execute in Biwenger:
1. [Action 1]
2. [Action 2]
...
"""
        
        decision = self.llm.generate_content(
            prompt, 
            system_prompt="You are a decisive Football Club President. You balance sporting ambition with financial prudence. Your decisions are final and must be actionable."
        )
        
        if decision:
            print("🏛️ Executive Decision Made")
            return decision
        else:
            return "Error generating Executive Decision."
