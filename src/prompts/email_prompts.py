"""
Email Prompts
==============
Contains prompts for generating email summaries.
"""

def get_email_summary_prompt(final_report: str) -> str:
    """
    Prompt to generate structured segments for the premium HTML email.
    """
    return f"""
ROLE: You are an expert sports journalist for a premium Fantasy Biwenger newsletter.

TASK: Analyze the following multi-agent report and extract structured segments for a high-quality summary.

REPORT CONTENT:
---
{final_report}
---

INSTRUCTIONS:
1.  ** Language**: Spanish.
2.  ** Tone**: Professional, engaging, and authoritative.
3.  ** Output Format**: You MUST return a VALID JSON object with the following keys:
    - "headline": A short, catchy sports headline (e.g., "Crisis en la portería y decisiones drásticas").
    - "introduction": A brief, friendly greeting to the manager.
    - "debate_summary": A 2-3 paragraph synthesis of the disagreement/discussion between the Coah and the Sporting Director. Use HTML tags like <b> and <i> for emphasis (NO MARKDOWN ASTERISKS).
    - "president_verdict": A concise summary of the President's final ruling and why. Use HTML tags for emphasis.
    - "actions_html": A clean HTML <ul> list of the specific actions taken (Lineup, Bids, Sales).
4.  ** No Markdown**: Do NOT use markdown symbols (*, #, etc.) in the text values. Use only valid HTML tags for formatting.

RETURN ONLY THE JSON OBJECT.
"""

EMAIL_SUMMARY_SYSTEM_ROLE = "You are an expert sports journalist and assistant specialized in Biwenger fantasy football."
