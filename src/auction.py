"""
Auction Moment Mode
===================
Designed around the daily market reset (auctions resolve at ~7:00 AM local).

The mode AUTO-DETECTS the auction resolution time from the market data itself
(`until` timestamps of active sales, which Biwenger sets at the daily reset),
and adapts its behavior to the time margin available:

- Started with margin (> ~4 min before resolution): full action workflow
  (lineup, bids, sales), then waits for the resolution.
- Started right before the reset (e.g. 6:59): no time to analyze — the bids
  placed the previous day stand; it just waits the short remaining time.
- Started after the reset: skips straight to the evaluation.

After the resolution it re-extracts fresh data, reports which auctions we
won/lost, and cancels redundant HEDGE bids (e.g. won a GK -> our other
pending GK bids are cancelled automatically).

The cleanup is fully deterministic (no extra LLM calls).
"""

import time
import pandas as pd
from datetime import datetime, timedelta

from src.data_extraction.runner import orchestrate_pipeline
from src.config import Credentials, GeneralSettings
from src.strategy.guardrails import compute_squad_needs

# Minimum time needed to run the agents and place bids before the resolution
ANALYSIS_MARGIN = timedelta(minutes=4)
# Small buffer so Biwenger has time to process the resolution
RESOLUTION_BUFFER = timedelta(seconds=90)
# Fallback resolution hour if it cannot be detected from data
FALLBACK_HOUR = 7


def detect_resolution_datetime() -> datetime:
    """
    Auto-detects today's auction resolution time from the market sales' `until`
    timestamps (stored in UTC by the API). Falls back to 07:00 local time.
    Always returns a FUTURE (or very recent) local datetime.
    """
    local_tz = datetime.now().astimezone().tzinfo
    now = datetime.now()
    try:
        df = pd.read_csv('./data/market_sales.csv')
        untils = pd.to_datetime(df['until'], errors='coerce').dropna()
        if not untils.empty:
            t = untils.min()
            t_local = t.tz_localize('UTC').tz_convert(local_tz).replace(tzinfo=None)
            # If the file is stale (from a previous day), reuse the same hour today
            if t_local < now - timedelta(hours=12):
                t_local = now.replace(hour=t_local.hour, minute=t_local.minute,
                                      second=0, microsecond=0)
                if t_local < now:
                    t_local += timedelta(days=1)
            return t_local
    except Exception:
        pass
    t = now.replace(hour=FALLBACK_HOUR, minute=0, second=0, microsecond=0)
    return t if t > now - timedelta(minutes=30) else t + timedelta(days=1)


def _get_my_team_name() -> str:
    return pd.read_csv('./data/user_info.csv')['team_name'].iloc[0]


def _snapshot(df_master: pd.DataFrame, my_team: str):
    """Returns (squad_player_ids, own_pending_bids:{player_id: info})."""
    squad_ids, pending = set(), {}
    if df_master is None or df_master.empty:
        return squad_ids, pending
    if 'BIWPLAYER_TEAM_NAME' in df_master.columns:
        squad_ids = set(
            df_master.loc[df_master['BIWPLAYER_TEAM_NAME'] == my_team, 'PLAYER_ID']
            .dropna().astype(int).tolist()
        )
    if 'MARKET_OFFER_ID' in df_master.columns:
        offers = df_master[df_master['MARKET_OFFER_ID'].notna()]
        if not offers.empty and 'MARKET_OFFER_FROM_NAME' in offers.columns:
            own = offers[offers['MARKET_OFFER_FROM_NAME'] == my_team]
            for _, r in own.iterrows():
                try:
                    pending[int(r['PLAYER_ID'])] = {
                        'offer_id': int(r['MARKET_OFFER_ID']),
                        'position': r.get('PLAYER_POSITION'),
                        'amount': r.get('MARKET_OFFER_AMOUNT'),
                        'name': r.get('PLAYER_NAME'),
                    }
                except (TypeError, ValueError):
                    continue
    return squad_ids, pending


def evaluate_auction_results(pre_master, post_master, my_team: str):
    """Compares pre/post snapshots: returns (won:[(id,name,pos)], lost:[info])."""
    pre_squad, pre_pending = _snapshot(pre_master, my_team)
    post_squad, post_pending = _snapshot(post_master, my_team)

    won_ids = post_squad - pre_squad
    won = []
    if won_ids and 'PLAYER_ID' in post_master.columns:
        names = post_master.set_index('PLAYER_ID')['PLAYER_NAME'].to_dict()
        positions = post_master.set_index('PLAYER_ID')['PLAYER_POSITION'].to_dict()
        won = [(pid, names.get(pid, str(pid)), positions.get(pid)) for pid in won_ids]

    lost = [info for pid, info in pre_pending.items()
            if pid not in post_pending and pid not in won_ids]
    return won, lost


def cleanup_redundant_hedge_bids(df_master) -> list:
    """
    Cancels our pending bids on positions that are NO LONGER missing
    (i.e. the need is already covered, e.g. we won the GK auction, so the
    backup GK bid is redundant). Fully deterministic; safe to run standalone.
    Returns a list of human-readable result strings.
    """
    results = []
    if df_master is None or df_master.empty or 'MARKET_OFFER_ID' not in df_master.columns:
        return results

    my_team = _get_my_team_name()
    squad = df_master[df_master['BIWPLAYER_TEAM_NAME'] == my_team]
    missing = set(compute_squad_needs(squad)['missing_positions'])

    offers = df_master[df_master['MARKET_OFFER_ID'].notna()]
    if offers.empty or 'MARKET_OFFER_FROM_NAME' not in offers.columns:
        return results
    own = offers[offers['MARKET_OFFER_FROM_NAME'] == my_team]

    to_cancel = [r for _, r in own.iterrows() if r.get('PLAYER_POSITION') not in missing]
    if not to_cancel:
        return results

    if GeneralSettings.DRY_RUN:
        for r in to_cancel:
            results.append(f"DRY-RUN: would cancel hedge bid on {r.get('PLAYER_NAME')} ({r.get('PLAYER_POSITION')})")
            print(f"   🧪 {results[-1]}")
        return results

    from src.data_extraction.auth import BiwengerAuth
    from src.actions import BiwengerActions
    auth = BiwengerAuth(Credentials.BIWENGER_USERNAME, Credentials.BIWENGER_PASSWORD)
    auth.login()
    info = auth.get_user_info()
    session = auth.get_session()
    session.headers.update({'x-league': str(info.league_id), 'x-user': str(info.team_id)})
    actions = BiwengerActions(session)

    for r in to_cancel:
        ok = actions.market.cancel_offer(int(r['MARKET_OFFER_ID']))
        results.append(
            f"Cancel hedge bid on {r.get('PLAYER_NAME')} ({r.get('PLAYER_POSITION')}): "
            f"{'SUCCESS ✅' if ok else 'FAILED ❌'}"
        )
    return results


def _wait_until(target: datetime):
    now = datetime.now()
    if now >= target:
        return
    wait_s = (target - now).total_seconds()
    print(f"⏳ Waiting {wait_s/60:.1f} min until auction resolution ({target.strftime('%H:%M:%S')})...")
    time.sleep(wait_s)


def run_auction():
    """Auction moment mode: (act) -> wait for resolution -> evaluate -> cleanup."""
    print("=" * 65)
    print("⏰ BIWENGER AGENT - Auction Moment Mode")
    print(f"📅 Run Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 65)

    # 1. Auto-detect when today's auctions resolve
    resolution = detect_resolution_datetime()
    now = datetime.now()
    print(f"🕖 Auction resolution detected at: {resolution.strftime('%H:%M')} (in {(resolution - now).total_seconds()/60:.1f} min)")

    # 2. Act only if there is real margin to analyze and place bids
    if now < resolution - ANALYSIS_MARGIN:
        from main import run_fantasy_crew
        run_fantasy_crew()
    elif now < resolution + RESOLUTION_BUFFER:
        print("⚡ Too close to the reset to analyze + bid — yesterday's bids stand. Waiting for resolution...")
    else:
        print("ℹ️ Resolution time already passed — evaluating results directly.")

    # 3. Pre-resolution snapshot (from the data saved by the last extraction)
    my_team = _get_my_team_name()
    pre_master = pd.read_csv('./data/_master.csv')

    # 4. Wait for the resolution (+ buffer) and re-extract
    _wait_until(resolution + RESOLUTION_BUFFER)
    print("\n🔍 Extracting post-auction data...")
    post_master = orchestrate_pipeline(extract=True)

    # 5. Evaluate outcomes
    won, lost = evaluate_auction_results(pre_master, post_master, my_team)
    print("\n📊 --- AUCTION RESULTS ---")
    for pid, name, pos in won:
        print(f"   🏆 WON: {name} ({pos})")
    for info in lost:
        print(f"   ❌ LOST: {info.get('name')} ({info.get('position')}) — bid {info.get('amount')}€")
    if not won and not lost:
        print("   (No auctions resolved for us this time.)")

    # 6. Cleanup redundant hedge bids (deterministic)
    cleanup = cleanup_redundant_hedge_bids(post_master)
    for line in cleanup:
        print(f"   🧹 {line}")
    if not cleanup and won:
        print("\n✅ No redundant bids to cancel.")

    # 7. Send the schematic auction-close email
    _send_auction_close_email(won, lost, cleanup, post_master, my_team)

    print("\n" + "=" * 65)
    print("✅ AUCTION MODE COMPLETE!")
    print("=" * 65)


def _send_auction_close_email(won, lost, cleanup, post_master, my_team):
    """Deterministic schematic email right after the 7:00 auction resolution."""
    from src.utils.email_builder import render_auction_close_email
    from src.utils.email_templates import BASE_HTML_TEMPLATE
    from src.utils.email_sender import send_report_email
    from jinja2 import Template

    segments = render_auction_close_email(None, None, won, lost, cleanup, post_master, my_team)

    html_content = None
    try:
        html_content = Template(BASE_HTML_TEMPLATE).render(
            lang=GeneralSettings.LANGUAGE,
            newspaper_name="BIWENGER CHRONICLE",
            edition_line=f"{datetime.now().strftime('%A, %d %B %Y')} · Auction Close Edition",
            headline=segments.get("headline", "Cierre de subasta"),
            lede=segments.get("lede", ""),
            stats_html=segments.get("stats_html", ""),
            sections=segments.get("sections", []),
            actions_html=segments.get("actions_html", ""),
            footer_line=f"Biwenger Chronicle · {datetime.now().strftime('%d/%m/%Y')}",
        )
        with open("./reports/email_auction_close.html", "w", encoding="utf-8") as f:
            f.write(html_content)
        print("   💾 Auction-close email preview saved to ./reports/email_auction_close.html")
    except Exception as e:
        print(f"   ⚠️ Auction-close email HTML failed: {e}")

    summary = f"{segments.get('headline')}\n\n{segments.get('lede')}"
    subject = f"🔁 Cierre de subasta - {datetime.now().strftime('%d/%m')}"
    try:
        send_report_email(summary, subject=subject, attachments=[], html_content=html_content)
    except Exception as e:
        print(f"   ⚠️ Auction-close email send failed: {e}")
