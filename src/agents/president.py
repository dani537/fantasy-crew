from src.llm_endpoints.deepseek import DeepseekClient
from src.data_extraction.pipeline import print_step

class President:
    """
    The Strategist.
    Role: Highest authority. Validates proposals based on long-term strategy and risk.
    """
    def __init__(self):
        self.llm = DeepseekClient()

    def decide(self, proposals):
        """
        Makes the final decision on transfer proposals.
        """
        print_step(22, "President (The Strategist) is reviewing proposals")
        
        if not proposals or "No players" in proposals or "Error" in proposals:
            return "No decisions required from the President."

        prompt = f"""
        CONTEXT:
        You are the Club President (The Strategist).
        
        PROPOSALS ON YOUR DESK:
        {proposals}
        
        OBJECTIVE:
        Review these proposals.
        Approve or Reject them based on:
        - Financial Risk (Is it too expensive?).
        - Strategic Fit (Do we really need this?).
        - Long-term Value.
        
        OUTPUT FORMAT:
        Provide an "EXECUTIVE DECISION" in Spanish.
        For each proposal:
        - **Decision**: APPROVED / REJECTED.
        - **Reason**: Brief strategic justification.
        """
        
        decision = self.llm.generate_content(prompt, system_prompt="You are a decisive Football Club President concerned with winning and financial sustainability.")
        
        if decision:
            print("🏛️ Executive Decision Made")
            return decision
        else:
            return "Error generating Executive Decision."
