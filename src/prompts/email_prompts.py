"""
Email Prompts
==============
Prompts for generating the newspaper-style email.
The output language is controlled via the LANGUAGE setting in .env.
"""


def get_email_summary_prompt(final_report: str, language_name: str) -> str:
    """
    Prompt to generate structured newspaper segments for the ACTION-mode email
    (after the agents have analyzed and executed operations).
    """
    return f"""
ROLE: You are the editor-in-chief of a premium Fantasy Football daily newspaper.

TASK: Turn the following agent report into a newspaper edition.

REPORT CONTENT:
---
{final_report}
---

INSTRUCTIONS:
1. **Language**: Write ALL text values in {language_name}.
2. **Tone**: Sports journalism — engaging, witty but precise. Like Marca/The Athletic.
3. **Output Format**: Return ONLY a VALID JSON object with these keys:
   - "headline": Catchy front-page headline about today's key decision (max 12 words).
   - "lede": 1-2 sentence subheadline expanding the headline (italic style text, no HTML).
   - "stats_html": One line of key figures separated by " · " using <b> for numbers (e.g. "<b>19,2M€</b> available · <b>4</b> bids placed"). Plain text with <b> tags only.
   - "sections": An array of 2-4 sections, each with:
       - "title": Section header with an emoji (e.g. "📋 LA CRÓNICA DEL MÍSTER").
       - "body_html": 1-2 short paragraphs of HTML (<b>, <i> allowed, NO markdown). Mention real player names and figures.
   - "actions_html": A compact HTML <ul> list of the operations executed and manual recommendations for the manager.
4. **No Markdown** anywhere. Only valid inline HTML tags.
5. If the report contains errors or no actions, still write an honest, readable edition.

RETURN ONLY THE JSON OBJECT.
"""


def get_briefing_email_prompt(briefing_context: str, language_name: str) -> str:
    """
    Prompt for the MORNING BRIEFING email (post market-reset run).
    Explains what happened overnight and what today's market looks like.
    """
    return f"""
ROLE: You are the editor-in-chief of a premium Fantasy Football daily newspaper.

TASK: Write the MORNING EDITION for the manager, explaining the overnight market
reset and the current state of the team. NO operations were executed in this run
— this is a pure intelligence briefing to read with the morning coffee.

OVERNIGHT DATA:
---
{briefing_context}
---

INSTRUCTIONS:
1. **Language**: Write ALL text values in {language_name}.
2. **Tone**: Sports journalism — warm, insightful, morning-coffee friendly.
3. **Output Format**: Return ONLY a VALID JSON object with these keys:
   - "headline": Catchy headline about the overnight market outcome (max 12 words).
   - "lede": 1-2 sentence subheadline (plain text).
   - "stats_html": One line of key figures separated by " · " using <b> for numbers (balance, squad size, players on market today).
   - "sections": An array of 3-4 sections covering: what happened overnight (won/lost auctions), state of the squad, today's market opportunities worth watching, and any warnings (injuries, expiring bids). Each with "title" (emoji + header) and "body_html" (short HTML paragraphs, real player names and figures).
   - "actions_html": A compact HTML <ul> list of suggested manual actions for the manager (if none, an honest note saying the team is on track).
4. **No Markdown** anywhere. Only valid inline HTML tags.
5. Do NOT invent events. If no auction resolved overnight, say so plainly.

RETURN ONLY THE JSON OBJECT.
"""


EMAIL_SUMMARY_SYSTEM_ROLE = (
    "You are an award-winning sports newspaper editor specialized in fantasy football. "
    "You write vivid, accurate editions and output strict JSON only."
)
