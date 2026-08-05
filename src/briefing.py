"""
Morning Briefing Mode
=====================
Lightweight post-market-reset run (~7:15 AM). Extracts fresh data, builds a
compact intelligence context and sends a newspaper-style morning briefing
explaining:
- What happened overnight (auctions won/lost, transfers on the board).
- Current squad and financial state.
- Today's market opportunities.
- Warnings (injuries, bids still pending, expiring auctions).

NO actions are executed and only ONE LLM call is made (the email writer),
keeping token usage minimal.
"""

import os
import pandas as pd
from datetime import datetime
from jinja2 import Template

from src.data_extraction.runner import orchestrate_pipeline
from src.auction import cleanup_redundant_hedge_bids
from src.llm_endpoints.deepseek import DeepseekClient
from src.prompts.email_prompts import get_briefing_email_prompt, EMAIL_SUMMARY_SYSTEM_ROLE
from src.utils.email_templates import BASE_HTML_TEMPLATE
from src.utils.email_sender import send_report_email
from src.utils.json_helper import extract_json_from_llm
from src.config import GeneralSettings, get_language_name


def _compact_table(df: pd.DataFrame, cols: list, max_rows: int = 12) -> str:
    if df is None or df.empty:
        return "None."
    existing = [c for c in cols if c in df.columns]
    if not existing:
        return "None."
    return df[existing].head(max_rows).to_markdown(index=False)


def build_briefing_context(df_master: pd.DataFrame) -> str:
    """Builds a token-efficient context string for the morning briefing."""
    parts = []

    # --- My account ---
    if os.path.exists('./data/user_info.csv'):
        user = pd.read_csv('./data/user_info.csv').iloc[0]
        parts.append(
            f"MY ACCOUNT: team '{user['team_name']}' in league '{user['league_name']}'. "
            f"Balance: €{user['balance']:,}."
        )

    my_team = None
    if os.path.exists('./data/user_info.csv'):
        my_team = pd.read_csv('./data/user_info.csv')['team_name'].iloc[0]

    # --- My squad ---
    if my_team and 'BIWPLAYER_TEAM_NAME' in df_master.columns:
        squad = df_master[df_master['BIWPLAYER_TEAM_NAME'] == my_team]
        parts.append(
            "MY SQUAD NOW:\n" + _compact_table(
                squad.sort_values('PLAYER_PRICE', ascending=False),
                ['PLAYER_NAME', 'PLAYER_POSITION', 'TEAM_NAME', 'PLAYER_STATUS',
                 'PLAYER_PRICE', 'PLAYER_PRICE_INCREMENT'],
                max_rows=20,
            )
        )

    # --- Overnight board activity (auctions resolved, transfers) ---
    if os.path.exists('./data/board_transfers.csv'):
        board = pd.read_csv('./data/board_transfers.csv')
        if not board.empty:
            # Resolve player names via master
            names = df_master.set_index('PLAYER_ID')['PLAYER_NAME'].to_dict() if 'PLAYER_ID' in df_master.columns else {}
            board = board.copy()
            board['player_name'] = board['player_id'].map(names)
            cols = ['date', 'type', 'player_name', 'buyer_name', 'seller_name', 'amount']
            parts.append("OVERNIGHT LEAGUE ACTIVITY (latest first):\n" + _compact_table(board.iloc[::-1], cols, max_rows=12))

    # --- Our pending bids ---
    if 'MARKET_OFFER_ID' in df_master.columns:
        offers = df_master[df_master['MARKET_OFFER_ID'].notna()]
        if not offers.empty:
            own = offers
            if my_team and 'MARKET_OFFER_FROM_NAME' in offers.columns:
                own = offers[offers['MARKET_OFFER_FROM_NAME'] == my_team]
            parts.append(
                "OUR PENDING BIDS (still waiting):\n" + _compact_table(
                    own,
                    ['PLAYER_NAME', 'PLAYER_POSITION', 'PLAYER_STATUS', 'MARKET_OFFER_AMOUNT', 'MARKET_OFFER_UNTIL'],
                    max_rows=10,
                ) or "None."
            )
        else:
            parts.append("OUR PENDING BIDS: none.")

    # --- Today's market ---
    if 'MARKET_SALE_PRICE' in df_master.columns:
        market = df_master[df_master['MARKET_SALE_PRICE'] > 0]
        if not market.empty:
            market = market.sort_values('PLAYER_PRICE', ascending=False)
            parts.append(
                "TODAY'S MARKET (top by price):\n" + _compact_table(
                    market,
                    ['PLAYER_NAME', 'PLAYER_POSITION', 'TEAM_NAME', 'PLAYER_STATUS',
                     'COMUNIATE_STARTER', 'PLAYER_PRICE', 'MARKET_SALE_PRICE',
                     'PLAYER_PRICE_INCREMENT', 'MARKET_SALE_USER_NAME'],
                    max_rows=15,
                )
            )

    # --- Warnings: injured/suspended in my squad ---
    if my_team and 'PLAYER_STATUS' in df_master.columns:
        squad = df_master[df_master['BIWPLAYER_TEAM_NAME'] == my_team]
        risky = squad[squad['PLAYER_STATUS'].isin(['injured', 'suspended', 'sanctioned', 'doubt'])]
        if not risky.empty:
            parts.append(
                "SQUAD WARNINGS:\n" + _compact_table(
                    risky, ['PLAYER_NAME', 'PLAYER_POSITION', 'PLAYER_STATUS', 'PLAYER_STATUS_INFO'], max_rows=8
                )
            )

    return "\n\n".join(parts) if parts else "No data available."


def run_briefing():
    """Executes the morning briefing mode: extract -> cleanup -> brief -> email."""
    print("=" * 65)
    print("🗞️  BIWENGER AGENT - Morning Briefing Mode")
    print(f"📅 Run Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 65)

    # 1. Fresh data (read-only)
    df_master = orchestrate_pipeline(extract=True)

    # 2. Deterministic post-auction cleanup: if we won players overnight, cancel
    #    our remaining pending bids on those same positions (hedge bids).
    cleanup_results = cleanup_redundant_hedge_bids(df_master)

    # 3. Build compact context
    context = build_briefing_context(df_master)
    if cleanup_results:
        context += "\n\nPOST-AUCTION CLEANUP DONE THIS RUN:\n" + "\n".join(cleanup_results)

    # 4. Single LLM call: newspaper morning edition
    print("🗞️  Generating morning edition...")
    html_content = None
    friendly_summary = "Morning briefing from your Biwenger Agent."
    try:
        llm = DeepseekClient()
        prompt = get_briefing_email_prompt(context, get_language_name())
        output = llm.generate_content(prompt, system_prompt=EMAIL_SUMMARY_SYSTEM_ROLE)
        segments = extract_json_from_llm(output)

        if segments and "error" not in segments:
            sections = segments.get("sections", [])
            sections = [s for s in sections if isinstance(s, dict)] if isinstance(sections, list) else []
            html_content = Template(BASE_HTML_TEMPLATE).render(
                lang=GeneralSettings.LANGUAGE,
                newspaper_name="BIWENGER CHRONICLE",
                edition_line=f"{datetime.now().strftime('%A, %d %B %Y')} · Morning Edition",
                headline=segments.get("headline", "Morning Briefing"),
                lede=segments.get("lede", ""),
                stats_html=segments.get("stats_html", ""),
                sections=sections,
                actions_html=segments.get("actions_html", ""),
                footer_line=f"Biwenger Chronicle · Morning Edition",
            )
            friendly_summary = f"{segments.get('headline')}\n\n{segments.get('lede')}"
            with open("./reports/email_preview.html", "w", encoding="utf-8") as f:
                f.write(html_content)
            print("   💾 Email preview saved to ./reports/email_preview.html")
    except Exception as e:
        print(f"   ⚠️ Briefing generation failed: {e}")

    attachments = ["./data/_master.xlsx"] if os.path.exists("./data/_master.xlsx") else []
    subject = f"🗞️ Biwenger Chronicle - Morning Edition {datetime.now().strftime('%d/%m')}"
    success = send_report_email(friendly_summary, subject=subject, attachments=attachments, html_content=html_content)

    print("\n" + "=" * 65)
    print(f"✅ BRIEFING COMPLETE! Email sent: {success}")
    print("=" * 65)
