"""
SPORTING DIRECTOR AGENT (The Broker)
=====================================

Role:
-----
Strategic planner and financial controller. The mission is to build the most competitive 
squad possible while ensuring strict financial health.

Mission & Logic:
----------------
1. **Financial Health (CRITICAL)**:
   - **Positive Balance Rule**: The team MUST have a positive balance at the start of the 
     Jornada. If the balance is negative, the team scores **0 POINTS** regardless of performance.
   - Managing the budget is the top priority to ensure point-scoring eligibility.

2. **Squad Enhancement**:
   - Analyze the Coach's report to identify and reinforce weak areas (missing positions, 
     low-performance lines).
   - Use market data to find the best value for money (Points per Million).

3. **Strategic "Weapons"**:
   - **Market Bids (Free Agents)**: Compete for players in the "Mercado". Calibrate bids 
     based on competition (number of bids) and player potential (points + price trend).
   - **Direct Offers**: Negotiate for players from other teams, especially those listed 
     as transferable.
   - **Buyout Clauses (Cláusulas)**: Execute immediate signings by paying release clauses 
     when available and strategically sound. DEADLINE: 48h before Jornada starts.

4. **Sales Management**:
   - Execute sales recommended by the Coach.
   - Set optimal market prices to maximize revenue while ensuring liquidity for new signings.

5. **Reporting**:
   - Consolidate all financial and transfer logic into a "TRANSFER PLAN".
   - Submit the plan to the President for final execution.

Workflow:
---------
1. Retrieve current balance from 'user_info.csv'.
2. Load and analyze the Coach's "COACH REPORT" for needs and sales suggestions.
3. Scan the market (df_master) for opportunities (Free Agents vs. League Players).
4. Apply value-based metrics (Points/Price, Price Trends).
5. Forecast financial status after proposed operations.
6. Generate a prioritized Transfer Plan for the President.
"""

from src.llm_endpoints.deepseek import DeepseekClient
from src.data_extraction.pipeline import print_step
import pandas as pd
import os
from datetime import datetime, timedelta

class SportingDirector:
    """
    The Broker.
    Role: Scans the market, manages budget, and proposes transfers based on Coach's needs and financial safety.
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
        return None

    def get_active_round_info(self):
        """
        Reads active_events.csv to check for ongoing rounds.
        Returns a string context or None.
        """
        try:
            if os.path.exists('./data/active_events.csv'):
                df_events = pd.read_csv('./data/active_events.csv')
                if not df_events.empty:
                    active_rounds = df_events[df_events['status'] == 'active']
                    if not active_rounds.empty:
                        event = active_rounds.iloc[0]
                        return f"Jornada '{event['name']}' is ACTIVE until {event['end']}. You will receive income after it ends."
        except Exception as e:
            print(f"⚠️ Warning: Could not read active_events.csv: {e}")
        return None

    def get_clause_deadline(self):
        """
        Calculates the deadline for clause buyouts (48h before jornada starts).
        Returns a tuple (deadline_str, is_open: bool).
        """
        try:
            if os.path.exists('./data/next_match.csv'):
                df_next = pd.read_csv('./data/next_match.csv')
                if not df_next.empty:
                    # Get the earliest match date
                    first_match_date_str = df_next['NEXT_MATCH_FECHA'].iloc[0]
                    first_match_date = pd.to_datetime(first_match_date_str)
                    
                    # Deadline is 48 hours before the first match
                    clause_deadline = first_match_date - timedelta(hours=48)
                    now = datetime.now(clause_deadline.tzinfo) if clause_deadline.tzinfo else datetime.now()
                    
                    is_open = now < clause_deadline
                    deadline_str = clause_deadline.strftime("%Y-%m-%d %H:%M")
                    
                    return deadline_str, is_open
        except Exception as e:
            print(f"⚠️ Warning: Could not calculate clause deadline: {e}")
        return "Unknown", False

    def get_budget_info(self):
        """
        Retrieves the current balance/budget from user_info.csv.
        Returns 0.0 if not found.
        """
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

    def propose(self, coach_report, df_master):
        """
        Generates transfer proposals based on coach needs and financial constraints.
        """
        print_step(21, "Sporting Director (The Broker) is scanning the market")
        
        # 0. Get My Team Name
        my_team_name = self.get_my_team_name()
        if not my_team_name:
            print("   ⚠️ Warning: Could not identify my team name.")
            my_team_name = "Unknown"
        
        # 1. Financial Context
        current_balance = self.get_budget_info()
        print(f"   💰 Current Balance: €{current_balance:,.0f}")
        
        # 2. Clause Deadline
        clause_deadline, clause_window_open = self.get_clause_deadline()
        clause_status = "OPEN ✅" if clause_window_open else "CLOSED ❌"
        print(f"   ⏰ Clause Deadline: {clause_deadline} ({clause_status})")

        # ==========================================================================
        # 3. SEGMENTED DATA VIEWS
        # ==========================================================================
        
        # --- A. MARKET (Free Agents) ---
        market_cols = ['PLAYER_ID', 'PLAYER_NAME', 'PLAYER_POSITION', 'TEAM_NAME', 
                       'AVG_POINTS', 'MARKET_SALE_PRICE', 'FINAL_SCORE', 
                       'COST_PER_XP', 'COST_PER_POINT', 'COST_PER_MOMENTUM_POINT']
        existing_market_cols = [c for c in market_cols if c in df_master.columns]
        
        market_players = df_master[df_master['MARKET_SALE_PRICE'] > 0].copy()
        if not market_players.empty and 'FINAL_SCORE' in market_players.columns:
            # Sort by Value Efficiency (Cost per xP) instead of Score
            if 'COST_PER_XP' in market_players.columns:
                 market_players = market_players.sort_values(by='COST_PER_XP', ascending=True).head(15)
            else:
                 market_players = market_players.sort_values(by='FINAL_SCORE', ascending=False).head(15)
            market_summary = market_players[existing_market_cols].to_markdown(index=False)
        else:
            market_summary = "No free agents on the market."
        
        # --- B. CLAUSE TARGETS (Other teams' players with clauses) ---
        clause_cols = ['PLAYER_ID', 'PLAYER_NAME', 'TEAM_NAME', 'BIWPLAYER_TEAM_NAME',
                       'BIWPLAYER_CLAUSE', 'COST_PER_XP', 'COST_PER_MOMENTUM_POINT']
        existing_clause_cols = [c for c in clause_cols if c in df_master.columns]
        
        clause_summary = "No clause opportunities or clause window is closed."
        if clause_window_open and 'BIWPLAYER_CLAUSE' in df_master.columns:
            # Filter: Has clause, NOT my team, and clause > 0
            clause_targets = df_master[
                (df_master['BIWPLAYER_CLAUSE'] > 0) & 
                (df_master['BIWPLAYER_TEAM_NAME'] != my_team_name) &
                (df_master['BIWPLAYER_TEAM_NAME'].notna())
            ].copy()
            
            # Sort by lowest Cost/xP (Moneyball efficiency)
            if not clause_targets.empty and 'COST_PER_XP' in clause_targets.columns:
                clause_targets = clause_targets.sort_values(by='COST_PER_XP', ascending=True).head(15)
                clause_summary = clause_targets[existing_clause_cols].to_markdown(index=False)
        
        # --- C. MY SQUAD (For Sales) ---
        squad_cols = ['PLAYER_ID', 'PLAYER_NAME', 'PLAYER_POSITION', 'TEAM_NAME', 'EXPECTED_POINTS', 
                      'PLAYER_PRICE', 'MOMENTUM_TREND']
        existing_squad_cols = [c for c in squad_cols if c in df_master.columns]
        
        my_squad = df_master[df_master['BIWPLAYER_TEAM_NAME'] == my_team_name].copy()
        if not my_squad.empty:
            my_squad_summary = my_squad[existing_squad_cols].to_markdown(index=False)
        else:
            my_squad_summary = "Could not load squad data."

        # ==========================================================================
        # 4. CONSTRUCT THE PROMPT
        # ==========================================================================
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        active_round_info = self.get_active_round_info()
        season_context_str = f"⚠️ SEASON CONTEXT: {active_round_info}\n" if active_round_info else ""

        prompt = f"""
ROLE: You are the Sporting Director (The Broker) for "{my_team_name}".
Current Date/Time: {current_time}

---
## 💰 FINANCIAL STATUS
- **Current Balance**: €{current_balance:,.0f}
- **Clause Window**: {clause_status} (Deadline: {clause_deadline})
{season_context_str}
> [!CAUTION]
> **CRITICAL RULE**: We MUST have a **POSITIVE balance (€0+)** by the start of the Jornada.
> A negative balance results in **0 POINTS** for the entire team.

---
## 📋 COACH'S REPORT (Needs & Sales Advice)
{coach_report}

---
## 🛒 MARKET OPPORTUNITIES (Free Agents)
Sorted by **Efficiency** (Lowest Cost/xP first).
{market_summary}

---
## 🔓 CLAUSE BUYOUT OPPORTUNITIES
Sorted by **Efficiency** (Lowest Cost/xP first).
{clause_summary}

---
## 👥 MY SQUAD (For Sales Strategy)
High Price + Low xP + Negative Trend = SELL
{my_squad_summary}

---
## 📖 FIELD DEFINITIONS
- **COST_PER_XP**: Millions paid per Expected Point. **LOWER IS BETTER**. (e.g. 0.5 is better than 1.2).
- **COST_PER_MOMENTUM_POINT**: Cost per recent form point. If this is MUCH LOWER than Cost/Point, it's a **BARGAIN (Chollo)**.
- **EXPECTED_POINTS (xP)**: Risk-adjusted points expected for this week.
- **MOMENTUM_TREND**: Price/Form momentum. Positive = rising.
- **BIWPLAYER_PURCHASE_PRICE**: What we PAID to acquire this player (market or clause).
- **BIWPLAYER_CLAUSE**: What OTHERS must pay to steal this player from us.

> [!CAUTION]
> **CLAUSE PROTECTION RULE (VALUE MAXIMIZATION)**:
> - **Voluntary Sale** → We receive `PLAYER_PRICE` (low market value, e.g., 6M).
> - **Being Clausuled** → We receive `BIWPLAYER_CLAUSE` (high clause value, e.g., 15M).
> - If `BIWPLAYER_PURCHASE_PRICE` > `PLAYER_PRICE` but < `BIWPLAYER_CLAUSE`:
>   - Selling is a **LOSS**. Being clausuled is a **PROFIT**.
>   - **DO NOT recommend selling these players.** Wait for a clause buyout.
> - **EXCEPTIONS (Sell even at a loss)**:
>   - Long-term injuries (>4 weeks).
>   - **Sustained declining performance**: `MOMENTUM_TREND` very negative over multiple weeks.
>   - Truly unusable players (permanently out of squad rotation).
> - Remember: **Maximizing squad VALUE** is a secondary objective after points.


> [!IMPORTANT]
> **CLAUSE REALITY**: When signing a player from another team, the **CLAUSE is the real price**.

---
## 🎯 YOUR TASKS
1. **Ensure Liquidity**: Check Coach's recommended sales. Estimate income to fix balance if negative or to fund signings.
2. **Reinforce Weaknesses**: If Coach needs a position, find the **most efficient** signing (Lowest Cost/xP).
3. **Strategic Bidding**: 
   - Identify **BARGAINS**: Players with low Cost/xP and positive trend.
   - If clause window is OPEN, identify high-value clause targets.
4. **Asset Management**: 
   - Set prices for players the coach wants to sell.
   - **PROTECT high-investment players**: If `PURCHASE_PRICE` > `PLAYER_PRICE`, DO NOT recommend voluntary sale.

---
## 📄 OUTPUT FORMAT (Markdown)

### 💵 Financial Diagnosis
Explain current balance status and how you plan to stay positive.

### 🎯 Target Signings
Prioritized list of signings (Market bid OR Clause buyout).
| Player | Type | Price/Clause | Rationale |
|--------|------|--------------|-----------|
| Name   | Market/Clause | €X | Why this player |

### 📤 Recommended Sales
Players to list, suggested price, and expected revenue.
| Player | Suggested Price | Reason |
|--------|-----------------|--------|
| Name   | €X              | Why sell |

### ⚖️ Final Forecast
Estimated balance after all operations.
```
Current Balance:    €{current_balance:,.0f}
+ Expected Sales:   €X
- Expected Buys:    €X
= Projected Balance: €X
```
"""
        
        proposals = self.llm.generate_content(prompt, system_prompt="You are a brilliant Football Sporting Director and master of financial logic.")
        
        if proposals:
            print("💼 Transfer Proposals Generated")
            return proposals
        else:
            return "Error generating Transfer Proposals."
