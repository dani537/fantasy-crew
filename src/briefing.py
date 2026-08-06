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

# Offer sizing for post-7:00 rival negotiations (we always negotiate DOWN from value)
RIVAL_OFFER_RATIO = 0.70          # offer ~70% of market value
MAX_RIVAL_OFFERS = 3
_POS_OFFENSIVE_RANK = {"FW": 0, "MF": 1, "DF": 2, "GK": 3}


def _negotiated_amount(player_row, budget):
    """Bids BELOW the player's market value (negotiation a la baja)."""
    try:
        value = float(player_row.get("PLAYER_PRICE") or 0)
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0:
        return None
    offer = int(value * RIVAL_OFFER_RATIO)
    # Never exceed what we can actually pay today.
    offer = min(offer, int(budget * 0.5))
    # Round down to a "clean" figure to look like a deliberate lowball.
    return int(offer / 100_000) * 100_000


def build_rival_offers(df_master, my_team, budget):
    """
    POST-7:00 phase. Targets players owned by rival managers (not on our market
    auction) and proposes a DIRECT offer, negotiating DOWN from market value.
    Covers structural needs first; prefers probable starters and more offensive
    lines (FW > MF > DF > GK) since those score more long-term.
    """
    if df_master is None or df_master.empty:
        return []
    if my_team is None:
        return []

    squad = df_master[df_master.get("BIWPLAYER_TEAM_NAME") == my_team]
    from src.strategy.guardrails import compute_squad_needs
    missing = set(compute_squad_needs(squad).get("missing_positions", []))

    rival = df_master[
        (df_master.get("BIWPLAYER_TEAM_ID").notna())
        & (df_master.get("BIWPLAYER_TEAM_NAME").notna())
        & (df_master.get("BIWPLAYER_TEAM_NAME") != my_team)
    ].copy()
    if rival.empty:
        return []

    # Skip injured; skip players already on the market auction (those are bids, not offers).
    if "PLAYER_STATUS" in rival.columns:
        rival = rival[rival["PLAYER_STATUS"].fillna("ok") != "injured"]
    rival = rival[rival.get("MARKET_SALE_PRICE", 0).fillna(0) <= 0]

    candidates = []
    for _, r in rival.iterrows():
        starter = float(r.get("COMUNIATE_STARTER") or 0.0)
        pos = r.get("PLAYER_POSITION")
        momentum = float(r.get("MOMENTUM_TREND") or 0.0)
        value = float(r.get("PLAYER_PRICE") or 0)
        if value <= 0:
            continue
        need_bonus = 10 if pos in missing else 0
        # Offensive lines rank higher; starters rank higher; momentum helps.
        score = (
            need_bonus
            + _POS_OFFENSIVE_RANK.get(pos, 3) * 1.0
            + starter * 4.0
            + min(max(momentum, -2), 2) * 0.5
        )
        candidates.append({"row": r, "score": score})

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:MAX_RIVAL_OFFERS]


def execute_rival_offers(df_master) -> list:
    """Builds + executes POST-7:00 rival offers. Deterministic (no LLM)."""
    results = []
    if df_master is None or df_master.empty:
        return results

    my_team = pd.read_csv('./data/user_info.csv')['team_name'].iloc[0]
    try:
        budget = float(pd.read_csv('./data/user_info.csv')['balance'].iloc[0])
    except Exception:
        budget = 0.0

    targets = build_rival_offers(df_master, my_team, budget)
    if not targets:
        print("   ℹ️ No rival offers to negotiate this run.")
        return results

    dry_run = GeneralSettings.DRY_RUN
    actions = None
    if not dry_run:
        from src.data_extraction.auth import BiwengerAuth
        from src.actions import BiwengerActions
        from src.config import Credentials
        auth = BiwengerAuth(Credentials.BIWENGER_USERNAME, Credentials.BIWENGER_PASSWORD)
        auth.login()
        info = auth.get_user_info()
        session = auth.get_session()
        session.headers.update({'x-league': str(info.league_id), 'x-user': str(info.team_id)})
        actions = BiwengerActions(session)

    spent = 0.0
    for t in targets:
        r = t["row"]
        amount = _negotiated_amount(r, budget - spent)
        if amount is None or amount <= 0:
            continue
        owner_id = int(r["BIWPLAYER_TEAM_ID"])
        name = r.get("PLAYER_NAME", "?")
        value = int(r.get("PLAYER_PRICE") or 0)
        starter = float(r.get("COMUNIATE_STARTER") or 0.0)
        print(f"   🤝 Offer to rival: {name} @ {amount:,}€ (value {value:,}€, start {starter:.0%})")
        ok = True if dry_run else actions.market.place_offer(amount, int(r["PLAYER_ID"]), owner_id)
        tag = "DRY-RUN 🧪" if dry_run else ("SUCCESS ✅" if ok else "FAILED ❌")
        results.append(f"Oferta a <b>{name}</b> por {amount:,}€ (valor {value:,}€ — negociado a la baja) · {tag}")
        spent += amount

    return results



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

    # 3. POST-7:00 RIVAL NEGOTIATIONS: once we know how the auction went, we make
    #    direct offers to rival-owned players, negotiating DOWN. (Market bids were
    #    already placed pre-7:00 to avoid tipping off rivals / inflating prices.)
    offer_results = execute_rival_offers(df_master)
    for line in offer_results:
        print(f"   🤝 {line}")

    # 4. Build compact context
    context = build_briefing_context(df_master)
    if cleanup_results:
        context += "\n\nPOST-AUCTION CLEANUP DONE THIS RUN:\n" + "\n".join(cleanup_results)
    if offer_results:
        context += "\n\nRIVAL OFFERS PLACED THIS RUN (negociadas a la baja):\n" + "\n".join(offer_results)

    # 5. Single LLM call: newspaper morning edition
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
