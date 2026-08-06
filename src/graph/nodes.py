"""
LangGraph Node Functions for Fantasy Crew Multi-Agent System (Simplified Architecture)
========================================================================================

Workflow Nodes:
1. data_extraction_node: Python deterministic pipeline (Extract + Feature Engineering).
2. coach_node: Tactical analysis (Lineup recommendation + Squad priorities).
3. sporting_director_node: Executive decision maker (Lineup selection, Market bids, Sales, Offers review).
4. execute_actions_node: API execution (Lineup set, Place players on market, Place bids).
5. generate_report_node: Saves JSON & Markdown reports to ./reports/.
6. email_report_node: Sends HTML executive summary email.
"""

import os
import json
import re
import pandas as pd
from datetime import datetime
from jinja2 import Template

from src.graph.state import AgentState
from src.agents.coach import Coach
from src.agents.sporting_director import SportingDirector
from src.data_extraction.runner import orchestrate_pipeline


def data_extraction_node(state: AgentState) -> dict:
    """
    Node: Data Extraction (Deterministic Python Pipeline)
    """
    try:
        df_master = orchestrate_pipeline(extract=True)
        return {
            "df_master": df_master,
            "error": None
        }
    except Exception as e:
        return {
            "df_master": None,
            "error": f"DataExtraction Error: {str(e)}"
        }


def coach_node(state: AgentState) -> dict:
    """
    Node: Coach (The Mister - Tactical Analysis)
    """
    if state.get("error"):
        return {"coach_report": {"error": state["error"]}}
    
    try:
        coach = Coach()
        df_master = state["df_master"]
        report = coach.analyze(df_master)
        return {
            "coach_report": report,
            "error": None
        }
    except Exception as e:
        return {
            "coach_report": {},
            "error": f"Coach Error: {str(e)}"
        }


def sporting_director_node(state: AgentState) -> dict:
    """
    Node: Sporting Director (The Broker & Executive Decisor)
    -------------------------------------------------------
    Takes Coach report + Market & Rival Financial Data and outputs final decisions.
    """
    if state.get("error"):
        return {"sd_decisions": {"error": state["error"]}}
    
    try:
        sd = SportingDirector()
        df_master = state["df_master"]
        coach_report = state["coach_report"]
        decisions = sd.propose(coach_report, df_master)
        
        # Structure approved actions for execution node
        approved_actions = decisions if isinstance(decisions, dict) else {}
        
        return {
            "sd_decisions": decisions,
            "approved_actions": approved_actions,
            "error": None
        }
    except Exception as e:
        return {
            "sd_decisions": {},
            "approved_actions": {},
            "error": f"SportingDirector Error: {str(e)}"
        }


def execute_actions_node(state: AgentState) -> dict:
    """
    Node: Execute Actions
    ---------------------
    Executes automated operations in Biwenger API:
    - Set 11 Lineup & Formation (validated; deterministic fallback if the LLM
      proposal is illegal)
    - Place Players on Market for Sale (after guardrail validation)
    - Place Bids / Buyout Clauses (after guardrail validation, addressing
      offers to rival sellers when required)

    Every LLM decision passes through the deterministic strategy layer first.
    Set DRY_RUN=true in .env to simulate without writing to the API.

    Note: Incoming sales offers are recommended in email, not auto-accepted per user instruction.
    """
    from src.config import GeneralSettings
    from src.strategy import select_best_lineup, filter_sales, filter_bids
    from src.strategy.lineup import validate_lineup, order_lineup_for_api
    from src.strategy.market_intel import adjust_bids_to_competitive

    dry_run = GeneralSettings.DRY_RUN
    approved_actions = state.get("approved_actions")
    if not approved_actions:
        print("   ℹ️ No approved actions found in state. Skipping execution.")
        return {"execution_results": ["No actions to execute."]}

    print(f"🚀 Node: ExecuteActions - Executing API operations...{' (DRY-RUN: no writes will be sent)' if dry_run else ''}")
    results = []

    df_master = state.get("df_master")

    # Identify my squad and the market from the master dataframe
    my_team_name = None
    try:
        if os.path.exists('./data/user_info.csv'):
            df_user = pd.read_csv('./data/user_info.csv')
            if not df_user.empty:
                my_team_name = df_user['team_name'].iloc[0]
    except Exception:
        pass

    squad = pd.DataFrame()
    market = pd.DataFrame()
    if df_master is not None and not df_master.empty and 'PLAYER_ID' in df_master.columns:
        if my_team_name and 'BIWPLAYER_TEAM_NAME' in df_master.columns:
            squad = df_master[df_master['BIWPLAYER_TEAM_NAME'] == my_team_name]
        if 'MARKET_SALE_PRICE' in df_master.columns:
            market = df_master[df_master['MARKET_SALE_PRICE'] > 0]

    # Current balance for bid validation
    balance = 0.0
    try:
        if os.path.exists('./data/user_info.csv'):
            balance = float(pd.read_csv('./data/user_info.csv')['balance'].iloc[0])
    except Exception:
        pass

    try:
        actions = None
        if not dry_run:
            from src.data_extraction.auth import BiwengerAuth
            from src.actions import BiwengerActions
            from src.config import Credentials

            auth = BiwengerAuth(Credentials.BIWENGER_USERNAME, Credentials.BIWENGER_PASSWORD)
            auth.login()
            player_info = auth.get_user_info()
            session = auth.get_session()

            if not session:
                return {"execution_results": ["❌ Login failed, skipping actions."]}

            session.headers.update({
                'x-league': str(player_info.league_id),
                'x-user': str(player_info.team_id)
            })
            actions = BiwengerActions(session)

        def _tag(success: bool) -> str:
            if dry_run:
                return "DRY-RUN 🧪"
            return 'SUCCESS ✅' if success else 'FAILED ❌'

        # 1. Update Lineup (LLM proposal -> validation -> deterministic fallback)
        lineup_data = approved_actions.get("lineup") or approved_actions.get("alineacion")
        if not lineup_data:
            # Also accept the Coach's raw proposal if the SD did not forward one
            coach_report = state.get("coach_report") or {}
            raw = coach_report.get("alineacion_propuesta") if isinstance(coach_report, dict) else None
            if raw:
                lineup_data = {
                    "formation": raw.get("formacion"),
                    "player_ids": raw.get("id_jugadores_titulares"),
                }

        lineup_to_set = None
        if lineup_data and isinstance(lineup_data, dict):
            candidate = {
                "formation": lineup_data.get("formation") or lineup_data.get("formacion"),
                "player_ids": lineup_data.get("player_ids") or lineup_data.get("jugadores_id"),
            }
            if validate_lineup(candidate, squad):
                lineup_to_set = candidate
            else:
                print("   ⚠️ Coach's lineup is ILLEGAL (wrong count/positions/formation). Using deterministic fallback.")
                results.append("Coach lineup rejected by validation (illegal XI) ⚠️")

        if lineup_to_set is None:
            lineup_to_set = select_best_lineup(squad)
            if lineup_to_set:
                print(f"   🤖 Deterministic lineup selected: {lineup_to_set['formation']}")

        if lineup_to_set:
            ordered_ids = order_lineup_for_api(lineup_to_set["player_ids"], squad)
            print(f"   ⚽ Setting lineup: {lineup_to_set['formation']} with {len(ordered_ids)} players")
            success = True if dry_run else actions.lineup.set_lineup(lineup_to_set["formation"], ordered_ids)
            results.append(f"Lineup ({lineup_to_set['formation']}): {_tag(success)}")
        else:
            results.append("Lineup: SKIPPED ⏭️ (no legal XI possible - e.g. no goalkeeper in squad)")

        # 2. Process Sales (guardrailed)
        sales = approved_actions.get("sales") or approved_actions.get("operaciones_venta") or []
        approved_sales, blocked_sales = filter_sales(sales, squad)
        for b in blocked_sales:
            results.append(f"Sale of {b.get('nombre', b.get('id_jugador'))}: BLOCKED 🛡️ ({b['blocked_reason']})")
            print(f"   🛡️ Sale blocked: {b.get('nombre')} -> {b['blocked_reason']}")

        for sale in approved_sales:
            pid = int(sale.get("player_id") or sale.get("id_jugador"))
            price = sale.get("price") or sale.get("precio_minimo_esperado") or sale.get("precio", 0)

            # Ensure price >= market price if available in df_master
            if not squad.empty:
                p_match = squad[squad['PLAYER_ID'] == pid]
                if not p_match.empty and 'PLAYER_PRICE' in p_match.columns:
                    market_val = p_match['PLAYER_PRICE'].iloc[0]
                    if pd.notna(market_val) and int(price) < int(market_val):
                        price = int(market_val)

            if pid and price:
                print(f"   🏷️ Placing player {pid} on market for {int(price):,}€")
                success = True if dry_run else actions.market.place_player_on_market(pid, int(price))
                results.append(f"Place Player {pid} on Market ({int(price):,}€): {_tag(success)}")

        # 2.b Process Removing Players from Market (Cancel Sale)
        removals = approved_actions.get("removals") or approved_actions.get("operaciones_retirar_mercado") or []
        for rem in removals:
            pid = rem.get("player_id") or rem.get("id_jugador")
            if pid:
                print(f"   🗑️ Removing player {pid} from market")
                success = True if dry_run else actions.market.remove_player_from_market(int(pid))
                results.append(f"Remove Player {pid} from Market: {_tag(success)}")

        # 2.c Process Cancelling Our Own Pending Bids
        # Deterministic safety net: ALWAYS cancel our bids on players who are now injured
        # (money at risk on a player who cannot score). LLM cancellations are added on top.
        cancel_ids = set()
        if (
            df_master is not None and not df_master.empty
            and 'MARKET_OFFER_ID' in df_master.columns
            and 'PLAYER_STATUS' in df_master.columns
        ):
            df_offers = df_master[df_master['MARKET_OFFER_ID'].notna()]
            if not df_offers.empty and my_team_name and 'MARKET_OFFER_FROM_NAME' in df_offers.columns:
                own_offers = df_offers[df_offers['MARKET_OFFER_FROM_NAME'] == my_team_name]
            else:
                own_offers = df_offers
            risky = own_offers[own_offers['PLAYER_STATUS'].isin(['injured', 'suspended', 'sanctioned'])]
            for _, row in risky.iterrows():
                cancel_ids.add(int(row['MARKET_OFFER_ID']))
                results.append(f"Auto-cancel bid on {row.get('PLAYER_NAME')} (now {row['PLAYER_STATUS']}) 🛡️")

        # Resolve REAL offer ids from our own pending offers in the data, NEVER from the
        # LLM output (the LLM invents offer_ids -> DELETE /offers/{id} fails).
        offer_id_by_player = {}
        if (
            df_master is not None and not df_master.empty
            and 'MARKET_OFFER_ID' in df_master.columns
            and 'PLAYER_ID' in df_master.columns
        ):
            df_offers = df_master[df_master['MARKET_OFFER_ID'].notna()]
            if not df_offers.empty:
                if my_team_name and 'MARKET_OFFER_FROM_NAME' in df_offers.columns:
                    df_offers = df_offers[df_offers['MARKET_OFFER_FROM_NAME'] == my_team_name]
                for _, row in df_offers.iterrows():
                    try:
                        offer_id_by_player[int(row['PLAYER_ID'])] = int(row['MARKET_OFFER_ID'])
                    except (TypeError, ValueError):
                        continue

        cancellations = approved_actions.get("operaciones_cancelar_pujas") or []
        for c in cancellations:
            pid = c.get("player_id") or c.get("id_jugador_mercado") or c.get("id_jugador")
            resolved = None
            if pid is not None:
                try:
                    resolved = offer_id_by_player.get(int(pid))
                except (TypeError, ValueError):
                    resolved = None
            if resolved is None:
                oid = c.get("offer_id") or c.get("id_oferta")
                try:
                    oid = None if oid is None or int(oid) <= 0 else int(oid)
                except (TypeError, ValueError):
                    oid = None
                if oid is not None:
                    resolved = oid
                    print(f"   ⚠️ LLM-supplied offer_id {resolved} NOT verified against our offers; using it as-is")
                else:
                    print(f"   ⚠️ Cannot resolve a real offer for cancel decision on player {pid}; skipping")
                    results.append(f"Cancel bid on player {pid}: SKIPPED ⏭️ (no real offer_id found)")
                    continue
            cancel_ids.add(resolved)

        for oid in sorted(cancel_ids):
            print(f"   ✖️ Cancelling offer {oid}")
            success = True if dry_run else actions.market.cancel_offer(oid)
            results.append(f"Cancel Offer {oid}: {_tag(success)}")

        # 3. Process Bids (guardrailed, addressed to rival sellers when needed)
        bids = approved_actions.get("bids") or approved_actions.get("operaciones_compra") or []

        # Do not duplicate bids we already have pending for the same player
        pending_bid_pids = set()
        if (
            df_master is not None and not df_master.empty
            and 'MARKET_OFFER_ID' in df_master.columns
        ):
            df_offers = df_master[df_master['MARKET_OFFER_ID'].notna()]
            if not df_offers.empty:
                if my_team_name and 'MARKET_OFFER_FROM_NAME' in df_offers.columns:
                    df_offers = df_offers[df_offers['MARKET_OFFER_FROM_NAME'] == my_team_name]
                pending_bid_pids = set(df_offers['PLAYER_ID'].astype(int).tolist())

        if pending_bid_pids and bids:
            deduped = []
            for bid in bids:
                pid = bid.get("player_id") or bid.get("id_jugador_mercado")
                try:
                    pid = int(pid)
                except (TypeError, ValueError):
                    deduped.append(bid)
                    continue
                if pid in pending_bid_pids:
                    results.append(f"Bid for Player {pid}: SKIPPED ⏭️ (bid already pending)")
                else:
                    deduped.append(bid)
            bids = deduped

        # Biwenger enforces: max bid = balance - sum(our pending bids).
        # Mirror that rule so we never attempt bids the API will reject.
        available_budget = balance
        if (
            df_master is not None and not df_master.empty
            and 'MARKET_OFFER_ID' in df_master.columns
            and 'MARKET_OFFER_AMOUNT' in df_master.columns
        ):
            df_offers = df_master[df_master['MARKET_OFFER_ID'].notna()]
            if not df_offers.empty:
                if my_team_name and 'MARKET_OFFER_FROM_NAME' in df_offers.columns:
                    df_offers = df_offers[df_offers['MARKET_OFFER_FROM_NAME'] == my_team_name]
                committed = pd.to_numeric(df_offers['MARKET_OFFER_AMOUNT'], errors='coerce').fillna(0).sum()
                available_budget = max(0.0, balance - float(committed))
                if committed > 0:
                    print(f"   💳 Balance: {balance:,.0f}€ | Committed in pending bids: {committed:,.0f}€ | Available: {available_budget:,.0f}€")

        approved_bids, blocked_bids = filter_bids(bids, market, available_budget, squad)
        for b in blocked_bids:
            results.append(f"Bid for {b.get('nombre', '?')}: BLOCKED 🛡️ ({b['blocked_reason']})")
            print(f"   🛡️ Bid blocked: {b.get('nombre')} -> {b['blocked_reason']}")

        # ---- MARKET-AUCTION PHASE (pre-7:00) ----
        # User rule: before 7:00 we ONLY place MARKET bids (computer/free agents).
        # Offers to RIVAL managers are negotiated AFTER the auction resolves (post-7:00),
        # to avoid revealing our intentions and inflating the price on players we want.
        market_bids = [b for b in approved_bids if not b.get("seller_is_rival")]
        rival_offers = [b for b in approved_bids if b.get("seller_is_rival")]

        # Bidding intelligence: this league pays +X% over the current price per line.
        # Raise our market bids to a competitive level so we stop underbidding/losing.
        market_bids = adjust_bids_to_competitive(market_bids, pd.read_csv('./data/board_bids.csv') if os.path.exists('./data/board_bids.csv') else None, df_master, auction=True)

        for bid in market_bids:
            pid = bid["player_id"]
            amount = bid["amount"]
            raised = " ⬆️" if bid.get("competitive_raise") else ""
            print(f"   💰 Bidding {amount:,}€ for player {pid}{raised}")
            success = True if dry_run else actions.market.place_offer(amount, pid, None)
            results.append(f"Bid {amount:,}€ for Player {pid}: {_tag(success)}")

        if rival_offers:
            pending = ", ".join(str(b.get('nombre', b['player_id'])) for b in rival_offers)
            results.append(f"Rival offer deferred to post-7:00 negotiation — {pending} (⏭️ evitamos inflar precio)")

        if not results:
            results.append("No active operations triggered.")

        # Deterministic projected balance: current free cash minus the NEW bids we
        # are placing now. (LLM-reported projections are unreliable -> override.)
        projected_balance = available_budget - sum(b["amount"] for b in market_bids)

        return {"execution_results": results, "projected_balance": projected_balance}

    except Exception as e:
        print(f"   ❌ Execution Error: {e}")
        return {"execution_results": [f"Error executing actions: {str(e)}"]}


def generate_report_node(state: AgentState) -> dict:
    """
    Node: Report Generator
    ----------------------
    Consolidates Coach & Sporting Director outputs into final reports.
    """
    reports_dir = "./reports"
    os.makedirs(reports_dir, exist_ok=True)
    
    with open(f"{reports_dir}/01_coach_report.json", 'w', encoding='utf-8') as f:
        json.dump(state.get("coach_report", {}), f, indent=2, ensure_ascii=False)
    
    with open(f"{reports_dir}/02_sporting_director_decisions.json", 'w', encoding='utf-8') as f:
        json.dump(state.get("sd_decisions", {}), f, indent=2, ensure_ascii=False)
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    final_report = f"""# 🏆 Biwenger Agent - Informe Ejecutivo Final
**Fecha de generación**: {timestamp}

---

## 📋 Informe del Coach (Míster)
```json
{json.dumps(state.get("coach_report", {}), indent=2, ensure_ascii=False)}
```

---

## 💼 Decisiones Ejecutivas - Director Deportivo
```json
{json.dumps(state.get("sd_decisions", {}), indent=2, ensure_ascii=False)}
```

---

## ⚡ Resultados de Ejecución API
```json
{json.dumps(state.get("execution_results", []), indent=2, ensure_ascii=False)}
```
"""
    
    with open(f"{reports_dir}/00_final_report.md", 'w', encoding='utf-8') as f:
        f.write(final_report)
    
    print("📄 Saved: ./reports/01_coach_report.json")
    print("📄 Saved: ./reports/02_sporting_director_decisions.json")
    print("📄 Saved: ./reports/00_final_report.md")
    
    return {"final_report": final_report}


def email_report_node(state: AgentState) -> dict:
    """
    Node: Email Report
    ------------------
    Sends the executive summary email in the configured LANGUAGE.

    The BODY is built deterministically from the agents' JSON reports
    (see src/utils/email_builder.py), so the manager always sees a clear,
    schematic picture: Míster diagnosis/recommendations, Director Deportivo
    actions and API execution results. No LLM is needed for the body.
    """
    from src.utils.email_builder import render_action_email
    from src.utils.email_templates import BASE_HTML_TEMPLATE
    from src.utils.email_sender import send_report_email
    from src.config import GeneralSettings
    from jinja2 import Template

    final_report = state.get("final_report", "")

    if not final_report:
        try:
            with open("./reports/00_final_report.md", 'r', encoding='utf-8') as f:
                final_report = f.read()
        except Exception:
            final_report = "No documentation available for this run."

    print("🚀 Node: EmailReport - Building deterministic schematic summary...")

    _FOOTERS = {
        "es": "Generado por tu Biwenger Agent",
        "en": "Generated by your Biwenger Agent",
        "ca": "Generat pel teu Biwenger Agent",
        "fr": "Généré par votre Biwenger Agent",
        "de": "Erstellt von deinem Biwenger Agent",
        "it": "Generato dal tuo Biwenger Agent",
        "pt": "Gerado pelo seu Biwenger Agent",
    }
    footer_text = f"Biwenger Chronicle · {_FOOTERS.get(GeneralSettings.LANGUAGE, _FOOTERS['es'])}"

    my_team = None
    try:
        if os.path.exists('./data/user_info.csv'):
            my_team = pd.read_csv('./data/user_info.csv')['team_name'].iloc[0]
    except Exception:
        pass

    segments = render_action_email(
        state.get("coach_report", {}),
        state.get("sd_decisions", {}),
        state.get("execution_results", []),
        state.get("df_master"),
        my_team,
        projected_balance=state.get("projected_balance"),
    )

    html_content = None
    try:
        template = Template(BASE_HTML_TEMPLATE)
        html_content = template.render(
            lang=GeneralSettings.LANGUAGE,
            newspaper_name="BIWENGER CHRONICLE",
            edition_line=f"{datetime.now().strftime('%A, %d %B %Y')} · Fantasy Edition",
            headline=segments.get("headline", "Reporte de Biwenger Agent"),
            lede=segments.get("lede", ""),
            stats_html=segments.get("stats_html", ""),
            sections=segments.get("sections", []),
            actions_html=segments.get("actions_html", ""),
            footer_line=footer_text,
        )
        with open("./reports/email_preview.html", "w", encoding="utf-8") as f:
            f.write(html_content)
        print("   💾 Email preview saved to ./reports/email_preview.html")
    except Exception as e:
        print(f"   ⚠️ HTML content generation failed: {e}")

    friendly_summary = f"{segments.get('headline')}\n\n{segments.get('lede')}\n\n{segments.get('stats_html', '')}"

    attachments = []
    for fpath in ["./reports/00_final_report.md", "./reports/01_coach_report.json", "./reports/02_sporting_director_decisions.json", "./data/_master.xlsx"]:
        if os.path.exists(fpath):
            attachments.append(fpath)

    subject = f"🗞️ Biwenger Chronicle - Fantasy Edition {datetime.now().strftime('%d/%m')}"
    success = send_report_email(friendly_summary, subject=subject, attachments=attachments, html_content=html_content)

    return {"email_sent": success}
