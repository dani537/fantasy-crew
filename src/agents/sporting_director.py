from src.llm_endpoints.deepseek import DeepseekClient
from src.data_extraction.pipeline import print_step
import pandas as pd
import os

class SportingDirector:
    """
    The Broker.
    Role: Scans the market, manages budget, and proposes transfers based on Coach's needs.
    """
    def __init__(self):
        self.llm = DeepseekClient()

    def get_budget_info(self):
        """
        Retrieves the current balance/budget from user_info.csv.
        Returns 0.0 if not found.
        """
        try:
            if os.path.exists('./data/user_info.csv'):
                df_user = pd.read_csv('./data/user_info.csv')
                if not df_user.empty:
                    # Try to find a balance column. Common names: 'balance', 'credit', 'money'
                    for col in ['balance', 'credit', 'money', 'budget']:
                        if col in df_user.columns:
                            return float(df_user[col].iloc[0])
        except Exception as e:
            print(f"⚠️ Warning: Could not read budget from user_info.csv: {e}")
            
        return 0.0

    def propose(self, coach_report, df_master):
        """
        Generates transfer proposals.
        """
        print_step(21, "Sporting Director (The Broker) is scanning the market")
        
        # 1. Financial Context
        current_balance = self.get_budget_info()
        print(f"   💰 Current Balance: €{current_balance:,.0f}")

        # 2. Filter Market Opportunities
        # Players in `df_master` with `MARKET_SALE_PRICE` > 0 are for sale.
        market_players = df_master[df_master['MARKET_SALE_PRICE'] > 0].copy()
        
        if market_players.empty:
            print("   ⚠️ No players found on the market.")
            return "No players currently on the market."

        # Prepare market summary for LLM
        # Show Top 30 interesting options based on Points and Price Increment
        # Metric: Points per Million (Value) + Trend
        market_players['SCORE'] = market_players['AVG_POINTS'] * (market_players['PLAYER_PRICE_INCREMENT'] / 100_000 + 1)
        
        candidates = market_players.sort_values(by='SCORE', ascending=False).head(30)
        
        market_summary = candidates[[
            'PLAYER_NAME', 'PLAYER_POSITION', 'TEAM_NAME', 'MARKET_SALE_PRICE', 
            'PLAYER_PRICE_INCREMENT', 'AVG_POINTS', 'PLAYER_STATUS'
        ]].to_markdown(index=False)
        
        # 3. Time Context
        from datetime import datetime
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        from src.config import GeneralSettings

        prompt = f"""
        ROLE:
        You are the Sporting Director (The Broker) and Financial Controller of a Fantasy Football Team.
        Current Date/Time: {current_time}
        
        FINANCIAL STATUS:
        Current Balance: €{current_balance:,.0f}
        
        MISSION:
        1. Satisfy the HEAD COACH's needs (Positions, specific requests).
        2. Maximizing Squad Points.
        3. **CRITICAL RULE**: We must NOT end the Jornada with a negative balance. If we are currently negative, we MUST sell players or avoid buying until we are safe. If we are positive, we can spend up to the limit, but always ensuring we stay positive.
        
        COACH'S REPORT (Needs & Sales Strategy):
        {coach_report}
        
        MARKET OPPORTUNITIES (Top Candidates):
        {market_summary}
        
        OBJECTIVE:
        Propose up to 3 Transfer Operations (Signings/Sales) to improve the team while respecting the budget.
        
        STRATEGY:
        - Check the Coach's "Market Strategy" (Players to Sell). Estimate their revenue to increase your budget logic.
        - If the Coach screams "WE NEED [POSITION]", finding a player for that position is PRIORITY #1.
        - If we are rich, go for premium players.
        - If we are poor, look for "bargains" (Positive Price Increment, decent points).
        
        OUTPUT FORMAT (Markdown):
        Provide a "TRANSFER PLAN" in the following language: {GeneralSettings.LANGUAGE}.
        
        ## 💼 Financial Outlook
        Brief logic about available money vs needs.
        
        ## 🔄 Proposed Operations
        Order by priority (1 = Most Urgent). For each:
        
        ### OPERATION [1/2/3]: [Name]
        *   **Type**: [SIGNING / SALE Recommendation]
        *   **Cost/Revenue**: [Amount]
        *   **Rationale**: Why? Link to Coach's need and Financial Safety.
        """
        
        proposals = self.llm.generate_content(prompt, system_prompt="You are an astute Football Sporting Director and Financial Advisor.")
        
        if proposals:
            print("💼 Transfer Proposals Generated")
            return proposals
        else:
            return "Error generating Transfer Proposals."
