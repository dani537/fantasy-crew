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
from src.data_extraction.transformers import print_step
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
            if os.path.exists('./data/next_jornada.csv'):
                df_next = pd.read_csv('./data/next_jornada.csv')
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

    def propose(self, coach_report, df_master, phase: str = None):
        """
        Generates transfer proposals based on coach needs and financial constraints.
        """
        print_step(21, "Sporting Director (The Broker) is scanning the market")
        
        if not phase:
            phase = "pre_auction" if datetime.now().hour < 7 else "post_auction"
        print(f"   ⏱️ Operating Phase: {phase.upper()}")
        
        # 0. Get My Team Name
        my_team_name = self.get_my_team_name()
        if not my_team_name:
            print("   ⚠️ Warning: Could not identify my team name.")
            my_team_name = "Unknown"
        
        # 1. Financial Context
        current_balance = self.get_budget_info()
        print(f"   💰 Current Balance: €{current_balance:,.0f}")

        # Biwenger rule: max bid = balance - sum(our pending bids). Compute the
        # effective budget so we never propose bids the API would reject.
        committed_bids = 0.0
        if 'MARKET_OFFER_ID' in df_master.columns and 'MARKET_OFFER_AMOUNT' in df_master.columns:
            df_off = df_master[df_master['MARKET_OFFER_ID'].notna()]
            if not df_off.empty:
                if 'MARKET_OFFER_FROM_NAME' in df_off.columns:
                    df_off = df_off[df_off['MARKET_OFFER_FROM_NAME'] == my_team_name]
                committed_bids = float(pd.to_numeric(df_off['MARKET_OFFER_AMOUNT'], errors='coerce').fillna(0).sum())
        effective_budget = max(0.0, current_balance - committed_bids)
        if committed_bids > 0:
            print(f"   💳 Committed in pending bids: €{committed_bids:,.0f} → Effective budget: €{effective_budget:,.0f}")
        
        # 2. Clause Deadline
        clause_deadline, clause_window_open = self.get_clause_deadline()
        clause_status = "OPEN ✅" if clause_window_open else "CLOSED ❌"
        print(f"   ⏰ Clause Deadline: {clause_deadline} ({clause_status})")

        # ==========================================================================
        # 3. SEGMENTED DATA VIEWS
        # ==========================================================================
        
        # --- A. MARKET (Free Agents vs Rival Players according to phase) ---
        market_cols = [
            'PLAYER_ID', 'PLAYER_NAME', 'PLAYER_POSITION', 'TEAM_NAME', 
            'PLAYER_STATUS', 'COMUNIATE_STARTER', 'PLAYER_PRICE_INCREMENT', 
            'PLAYER_PRICE', 'MARKET_SALE_PRICE', 'MARKET_SALE_USER_NAME',
            'AVG_POINTS', 'EXPECTED_POINTS', 'COST_PER_XP',
            'TEAM_IS_HOME', 'ODDS_1', 'ODDS_2', 'MOMENTUM_TREND'
        ]
        existing_market_cols = [c for c in market_cols if c in df_master.columns]
        
        # Filter on-market players AND NOT INJURED.
        market_players = df_master[df_master['MARKET_SALE_PRICE'] > 0].copy()
        if 'PLAYER_STATUS' in market_players.columns:
            market_players = market_players[market_players['PLAYER_STATUS'] != 'injured']
            
        if phase == "pre_auction":
            # PRE-7 phase: only Mercado (computer) free agents
            if 'MARKET_SALE_USER_NAME' in market_players.columns:
                market_players = market_players[
                    (market_players['MARKET_SALE_USER_NAME'].isna())
                    | (market_players['MARKET_SALE_USER_NAME'] == 'Mercado')
                ]
        else:
            # POST-7 phase: rival-owned players listed on market
            if 'MARKET_SALE_USER_NAME' in market_players.columns:
                rival_players = market_players[
                    (market_players['MARKET_SALE_USER_NAME'].notna())
                    & (market_players['MARKET_SALE_USER_NAME'] != 'Mercado')
                ]
                if not rival_players.empty:
                    market_players = rival_players

        if not market_players.empty:
            candidates = []
            if 'EXPECTED_POINTS' in market_players.columns and market_players['EXPECTED_POINTS'].sum() > 0:
                top_xp = market_players.sort_values(by='EXPECTED_POINTS', ascending=False).head(10)
                candidates.append(top_xp)
            if 'COST_PER_XP' in market_players.columns:
                valid_cpxp = market_players[market_players['COST_PER_XP'] > 0]
                if not valid_cpxp.empty:
                    top_value = valid_cpxp.sort_values(by='COST_PER_XP', ascending=True).head(10)
                    candidates.append(top_value)
            if 'PLAYER_PRICE_INCREMENT' in market_players.columns:
                top_trend = market_players.sort_values(by='PLAYER_PRICE_INCREMENT', ascending=False).head(10)
                candidates.append(top_trend)
            if 'PLAYER_PRICE' in market_players.columns:
                top_price = market_players.sort_values(by='PLAYER_PRICE', ascending=False).head(10)
                candidates.append(top_price)
            if candidates:
                market_players = pd.concat(candidates).drop_duplicates(subset=['PLAYER_ID']).head(30)
            else:
                market_players = market_players.head(25)
            market_summary = market_players[existing_market_cols].to_markdown(index=False)
        else:
            market_summary = "No free agents available on market."
        
        # --- B. CLAUSE TARGETS (Other teams' players with clauses) ---
        clause_cols = [
            'PLAYER_ID', 'PLAYER_NAME', 'TEAM_NAME', 'BIWPLAYER_TEAM_NAME',
            'PLAYER_STATUS', 'COMUNIATE_STARTER', 'BIWPLAYER_CLAUSE', 'PLAYER_PRICE_INCREMENT'
        ]
        existing_clause_cols = [c for c in clause_cols if c in df_master.columns]
        
        clause_summary = "No clause opportunities or clause window is closed."
        if clause_window_open and 'BIWPLAYER_CLAUSE' in df_master.columns:
            clause_targets = df_master[
                (df_master['BIWPLAYER_CLAUSE'] > 0) & 
                (df_master['BIWPLAYER_TEAM_NAME'] != my_team_name) &
                (df_master['BIWPLAYER_TEAM_NAME'].notna())
            ].copy()
            if 'PLAYER_STATUS' in clause_targets.columns:
                clause_targets = clause_targets[clause_targets['PLAYER_STATUS'] != 'injured']
            
            if not clause_targets.empty:
                clause_summary = clause_targets.head(15)[existing_clause_cols].to_markdown(index=False)
        
        # --- C. MY SQUAD (For Sales) ---
        squad_cols = [
            'PLAYER_ID', 'PLAYER_NAME', 'PLAYER_POSITION', 'TEAM_NAME', 
            'PLAYER_STATUS', 'COMUNIATE_STARTER', 'PLAYER_PRICE', 
            'PLAYER_PRICE_INCREMENT', 'AVG_POINTS'
        ]
        existing_squad_cols = [c for c in squad_cols if c in df_master.columns]
        
        my_squad = df_master[df_master['BIWPLAYER_TEAM_NAME'] == my_team_name].copy()
        if not my_squad.empty:
            my_squad_summary = my_squad[existing_squad_cols].to_markdown(index=False)
        else:
            my_squad_summary = "Could not load squad data."

        # --- D. OUR PENDING OUTGOING BIDS (review & cancel if they stopped making sense) ---
        pending_bids_summary = "No pending outgoing bids."
        offer_cols = [
            'MARKET_OFFER_ID', 'PLAYER_ID', 'PLAYER_NAME', 'PLAYER_POSITION',
            'PLAYER_STATUS', 'MARKET_OFFER_AMOUNT', 'MARKET_OFFER_UNTIL', 'MARKET_OFFER_FROM_NAME'
        ]
        existing_offer_cols = [c for c in offer_cols if c in df_master.columns]
        if 'MARKET_OFFER_ID' in df_master.columns:
            df_offers = df_master[df_master['MARKET_OFFER_ID'].notna()].copy()
            if not df_offers.empty:
                if 'MARKET_OFFER_FROM_NAME' in df_offers.columns:
                    own = df_offers[df_offers['MARKET_OFFER_FROM_NAME'] == my_team_name]
                else:
                    own = df_offers
                if not own.empty:
                    pending_bids_summary = own[existing_offer_cols].to_markdown(index=False)

        # ==========================================================================
        # 4. CONSTRUCT THE PROMPT
        # ==========================================================================
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        active_round_info = self.get_active_round_info()
        season_context_str = f"⚠️ SEASON CONTEXT: {active_round_info}\n" if active_round_info else ""
        if committed_bids > 0:
            season_context_str += (
                f"💳 We already have €{committed_bids:,.0f} committed in pending bids. "
                f"The usable budget for NEW bids is €{effective_budget:,.0f} (Biwenger rejects anything above).\n"
            )
        recent_bids_summary = "No recent rival bids recorded."
        market_intel_summary = ""
        if os.path.exists('./data/board_bids.csv'):
            try:
                df_bids = pd.read_csv('./data/board_bids.csv')
                if not df_bids.empty:
                    recent_bids_summary = df_bids.head(10).to_markdown(index=False)
                from src.strategy.market_intel import build_market_intel_summary
                market_intel_summary = build_market_intel_summary(df_bids, df_master)
            except Exception:
                pass

        from src.prompts.sporting_director_prompts import get_sd_proposal_prompt
        from src.prompts.system_roles import SPORTING_DIRECTOR_SYSTEM_ROLE
        from src.strategy.guardrails import compute_squad_needs

        needs = compute_squad_needs(my_squad)
        squad_needs_summary = (
            f"Squad size: {needs['squad_size']} players ({needs['fit_players']} fit). "
            f"By line: {needs['counts']}. {needs['summary']}"
        )

        import json
        prompt = get_sd_proposal_prompt(
            my_team_name=my_team_name,
            current_time=current_time,
            current_balance=effective_budget,
            clause_status=clause_status,
            clause_deadline=clause_deadline,
            season_context_str=season_context_str,
            coach_report=json.dumps(coach_report.get('briefing_direccion_deportiva', coach_report)) if isinstance(coach_report, dict) else str(coach_report),
            market_summary=market_summary,
            clause_summary=clause_summary,
            my_squad_summary=my_squad_summary,
            squad_needs_summary=squad_needs_summary,
            pending_bids_summary=pending_bids_summary,
            recent_bids_summary=recent_bids_summary,
            market_intel_summary=market_intel_summary,
            phase=phase,
        )
        
        from src.utils.json_helper import extract_json_from_llm
        
        proposals_text = self.llm.generate_content(prompt, system_prompt=SPORTING_DIRECTOR_SYSTEM_ROLE)
        
        if proposals_text:
            print("💼 Transfer Proposals JSON Generated")
            return extract_json_from_llm(proposals_text)
        else:
            return {"error": "Error generating Transfer Proposals."}

