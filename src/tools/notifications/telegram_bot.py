"""
Bidirectional Conversational Telegram Sports Director (`telegram_bot.py`)
=========================================================================
Allows the user to talk to Fantasy Champion v3 anytime on Telegram:
- Instant Slash Commands:
  - `/estado` (`/status`): Real-time `<12s` delta refresh (Balance, Phase, 48h Clause Window, Listings & Offers)
  - `/once` (`/xi`): Current optimal MILP Starting XI & expected points
  - `/plan`: Latest daily quantitative plan summary
  - `/tesis`: Squad asset theses (`FRANCHISE_CORE`, `SPECULATIVE_FLIP`, `NPV_HOLD` vs `NPV_SELL`)
  - `/pendientes`: Lists `SEMI_AUTO` human-gated decisions (clauses, shields, important sales)
  - `/aprobar <ID>` & `/rechazar <ID>`: Executes or rejects a pending `SEMI_AUTO` decision live!
  - `/intocable <jugador>`, `/transferible <jugador>`, `/vender <jugador>`, `/vetar <jugador>`, `/objetivo <jugador>`
- Natural Language Chat:
  - Understands free-form Spanish questions & strategic instructions, backed by live
    `FastDeltaRefresh`, `StrategyMemoryLedger`, and `TieredLLMRouter` ($0 Gemini prioritized).
- Execution Modes:
  - `python -m src.tools.notifications.telegram_bot --listen` (24/7 instant long-polling daemon)
  - `python -m src.tools.notifications.telegram_bot --drain-once` (Serverless / GitHub Actions webhook drain)
"""

import os
import json
import time
import html
import argparse
from typing import Dict, List, Any, Optional
import pandas as pd
import requests

from src.config import Credentials, GeneralSettings, StrategySettings, GoogleSheetsConfig, resolve_active_user_identity
from src.tools.data_extraction.fast_delta_refresh import FastDeltaRefresh
from src.tools.strategy_engine.league_rules import LeagueRulesEngine
from src.tools.strategy_engine.strategy_memory import StrategyMemoryLedger
from src.tools.biwenger_actions.market_actions import BiwengerMarketActions
from src.tools.backtester.causal_clock import StrictCausalDataView
from src.llm_endpoints.router import TieredLLMRouter


OFFSET_PATH = "./data/autopilot/telegram_offset.json"
LATEST_PLAN_PATH = "./data/autopilot/daily_plan_latest.json"


class TelegramDirectorBot:
    """Two-way interactive Telegram bot for Fantasy Champion v3."""

    def __init__(
        self,
        token: Optional[str] = None,
        allowed_chat_id: Optional[str] = None,
        offset_path: str = OFFSET_PATH,
    ):
        self.token = token or Credentials.TELEGRAM_BOT_TOKEN
        self.allowed_chat_id = str(allowed_chat_id or Credentials.TELEGRAM_ALLOWED_CHAT_ID or "").strip()
        self.offset_path = offset_path
        self.memory = StrategyMemoryLedger(sync_sheets=GoogleSheetsConfig.ENABLED)
        self.delta_refresher = FastDeltaRefresh()
        os.makedirs(os.path.dirname(self.offset_path) or ".", exist_ok=True)

    @property
    def api_url(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    def _load_offset(self) -> int:
        if os.path.exists(self.offset_path):
            try:
                with open(self.offset_path, "r", encoding="utf-8") as f:
                    return int(json.load(f).get("offset", 0))
            except Exception:
                return 0
        return 0

    def _save_offset(self, offset: int) -> None:
        with open(self.offset_path, "w", encoding="utf-8") as f:
            json.dump({"offset": int(offset)}, f)

    def send_reply(self, chat_id: str, text: str, parse_mode: Optional[str] = "HTML") -> bool:
        if not self.token:
            return False
        rem = str(text or "").strip()
        chunks: List[str] = []
        while len(rem) > 3800:
            cut = rem.rfind("\n", 0, 3800)
            if cut <= 0:
                cut = 3800
            chunks.append(rem[:cut])
            rem = rem[cut:].lstrip("\n")
        if rem:
            chunks.append(rem)

        ok_all = True
        for ch in chunks:
            payload: Dict[str, Any] = {"chat_id": chat_id, "text": ch, "disable_web_page_preview": True}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            try:
                r = requests.post(f"{self.api_url}/sendMessage", json=payload, timeout=15)
                if r.status_code != 200 and parse_mode:
                    payload.pop("parse_mode", None)
                    r = requests.post(f"{self.api_url}/sendMessage", json=payload, timeout=15)
                ok_all = ok_all and (r.status_code == 200)
            except Exception:
                ok_all = False
        return ok_all

    def _load_latest_plan(self) -> Dict[str, Any]:
        if os.path.exists(LATEST_PLAN_PATH):
            try:
                with open(LATEST_PLAN_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def handle_message_text(self, text: str) -> str:
        """Processes an incoming user message (command or free-form chat) and returns reply text."""
        msg = str(text or "").strip()
        if not msg:
            return "Escribe <code>/ayuda</code> para ver los comandos disponibles o pregúntame lo que quieras."

        parts = msg.split(maxsplit=1)
        cmd = parts[0].lower().split("@")[0]
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/start", "/ayuda", "/help"):
            return (
                "🤖 <b>Fantasy Champion v3 — Director Deportivo Interactivo</b>\n\n"
                "<b>Comandos rápidos:</b>\n"
                "• <code>/estado</code> — Saldo real, fase de jornada, bloqueo 48h y ofertas activas (&lt;12s)\n"
                "• <code>/once</code> — Once titular óptimo MILP y xP esperados\n"
                "• <code>/plan</code> — Resumen del plan cuantitativo de hoy\n"
                "• <code>/tesis</code> — Rol de cada jugador y valoración <code>NPV_HOLD</code> vs <code>NPV_SELL</code>\n"
                "• <code>/pendientes</code> — Cláusulas y ventas importantes esperando tu OK\n"
                "• <code>/aprobar ID</code> — Ejecuta una decisión pendiente (ej. <code>/aprobar CL-1234</code>)\n"
                "• <code>/rechazar ID</code> — Descarta una decisión pendiente\n"
                "• <code>/intocable Nombre</code> — Protege un jugador en memoria persistente\n"
                "• <code>/transferible Nombre</code> — Quita blindaje de intocable\n"
                "• <code>/vender Nombre</code> | <code>/objetivo Nombre</code> | <code>/vetar Nombre</code>\n\n"
                "💬 <i>También puedes hablarme en lenguaje natural (ej. «¿Cómo vamos para el parón?» o «Reserva caja esta semana»).</i>"
            )

        if cmd in ("/estado", "/status"):
            return self._cmd_estado()

        if cmd in ("/once", "/xi"):
            return self._cmd_once()

        if cmd == "/plan":
            return self._cmd_plan()

        if cmd == "/tesis":
            return self._cmd_tesis()

        if cmd == "/pendientes":
            return self._cmd_pendientes()

        if cmd == "/aprobar":
            return self._cmd_aprobar(arg)

        if cmd == "/rechazar":
            return self._cmd_rechazar(arg)

        if cmd == "/intocable":
            return html.escape(self.memory.add_user_directive("untouchable", arg, source="TELEGRAM"))

        if cmd == "/transferible":
            return html.escape(self.memory.add_user_directive("transferable", arg, source="TELEGRAM"))

        if cmd == "/vender":
            return html.escape(self.memory.add_user_directive("force_sell", arg, source="TELEGRAM"))

        if cmd == "/vetar":
            return html.escape(self.memory.add_user_directive("blacklist_buy", arg, source="TELEGRAM"))

        if cmd == "/objetivo":
            return html.escape(self.memory.add_user_directive("target", arg, source="TELEGRAM"))

        if cmd == "/nota":
            return html.escape(self.memory.add_user_directive("note", arg, source="TELEGRAM"))

        return self._handle_natural_conversation(msg)

    def _cmd_estado(self) -> str:
        delta = self.delta_refresher.fetch(max_cache_age_seconds=30)
        rules = LeagueRulesEngine.load()
        try:
            view = StrictCausalDataView()
            cal = view.df_calendar
        except Exception:
            cal = pd.DataFrame()
        phase = rules.resolve_round_phase(cal)

        bal = delta.get("balance")
        bal_s = f"{int(bal):,} €" if bal is not None else "N/D"
        listed = delta.get("currently_listed_pids") or []
        offers = delta.get("waiting_offers_by_pid") or {}
        live_status = delta.get("live_player_status") or {}

        lines = [
            f"📡 <b>Estado en Vivo — {html.escape(str(delta.get('team_name')))}</b> ({'🟢 Live API' if delta.get('is_live') else '💾 Caché Local'})",
            f"💰 Saldo actual: <b>{bal_s}</b>",
            f"🗓️ Fase J{phase.target_jornada_num}: <code>{phase.phase_name}</code>",
            f"⚽ Inicio J{phase.target_jornada_num}: <code>{phase.next_kickoff_ts.strftime('%Y-%m-%d %H:%M')}</code> ({phase.hours_to_kickoff:.1f}h)",
            f"🔒 Cláusulas ({rules.clause_disabled_hours}h): {'🟢 Abiertas hasta ' + phase.clause_lock_ts.strftime('%Y-%m-%d %H:%M') if phase.clauses_open else '🔴 Bloqueadas'}",
            f"🏷️ En venta ({len(listed)}/{rules.market_max_user_sales}): " + (
                ", ".join(html.escape(live_status.get(str(p), {}).get("name", f"ID {p}")) for p in listed) or "Ninguno"
            ),
            f"📩 Ofertas recibidas esperando respuesta: <b>{len(offers)}</b>",
        ]
        for pid_s, of in offers.items():
            nm = html.escape(live_status.get(str(pid_s), {}).get("name", f"ID {pid_s}"))
            vm = int(live_status.get(str(pid_s), {}).get("price", 0) or 0)
            pct = ((float(of["amount"]) - vm) / vm * 100.0) if vm > 0 else 0.0
            lines.append(f"  • <b>{nm}</b>: <code>{int(of['amount']):,} €</code> ({pct:+.1f}% vs VM) de {html.escape(str(of['from_name']))}")
        return "\n".join(lines)

    def _cmd_once(self) -> str:
        latest = self._load_latest_plan()
        lu = (latest.get("plan") or {}).get("lineup") or {}
        if not lu.get("slots"):
            from src.tools.strategy_engine.autopilot import AutopilotRunner
            latest = AutopilotRunner(mode="DRY_RUN").execute_cycle(generate_briefing=False, send_notifications=False)
            lu = (latest.get("plan") or {}).get("lineup") or {}

        lines = [
            f"⚽ <b>Once Óptimo MILP J{latest.get('target_jornada_num', '?')}</b> — "
            f"Formación <b>{html.escape(str(lu.get('formation', '4-4-2')))}</b> (<b>{lu.get('expected_points', 0):.1f} xP</b>):"
        ]
        for ln in ("GK", "DF", "MF", "FW"):
            players = [f"{html.escape(s['nombre'])} ({s['xp']:.1f})" for s in (lu.get("slots") or []) if s["linea"] == ln]
            if players:
                lines.append(f"• <b>{ln}:</b> " + ", ".join(players))
        return "\n".join(lines)

    def _cmd_plan(self) -> str:
        latest = self._load_latest_plan()
        if not latest:
            from src.tools.strategy_engine.autopilot import AutopilotRunner
            latest = AutopilotRunner(mode="DRY_RUN").execute_cycle(generate_briefing=False, send_notifications=False)
        pending = self.memory.get_pending_decisions("PENDING")
        from src.tools.notifications.notifier import NotificationHub
        return NotificationHub._build_telegram_html(
            summary=latest,
            pending=pending,
            team_name=latest.get("team_name") or resolve_active_user_identity().get("team_name", "Mi Equipo"),
            league_name=latest.get("league_name") or resolve_active_user_identity().get("league_name", "Liga"),
            trigger_label="CONSULTA_TELEGRAM",
            phase_name=(latest.get("phase_context") or {}).get("phase_name", "ACTIVO"),
        )

    def _cmd_tesis(self) -> str:
        theses = list(self.memory.get_asset_theses_map().values())
        if not theses:
            return "Aún no hay tesis registradas. Ejecuta <code>/plan</code> para evaluar la plantilla."
        order = {"FRANCHISE_CORE": 0, "STRUCTURAL_STARTER": 1, "SPECULATIVE_FLIP": 2, "BENCH_PATCH": 3}
        theses.sort(key=lambda x: (order.get(x.get("rol", ""), 9), -int(x.get("precio_actual", 0))))
        lines = ["🧠 <b>Tesis Microeconómica de Plantilla (NPV 6 Jornadas & Coste Reposición):</b>"]
        for t in theses:
            icon = "👑" if t["rol"] == "FRANCHISE_CORE" else ("⚽" if t["rol"] == "STRUCTURAL_STARTER" else ("📈" if t["rol"] == "SPECULATIVE_FLIP" else "🧩"))
            rec = "HOLD" if t.get("recomendacion_npv") != "SELL" else "⚠️ SELL"
            lines.append(
                f"{icon} <b>{html.escape(str(t['nombre']))}</b> [{rec}] · VM <code>{int(t['precio_actual'])/1e6:.2f}M</code> "
                f"| Reposición <code>{int(t['coste_reposicion'])/1e6:.2f}M</code> ({t['escasez_tier']})"
            )
        return "\n".join(lines)

    def _cmd_pendientes(self) -> str:
        pending = self.memory.get_pending_decisions("PENDING")
        if not pending:
            return "✅ No tienes ninguna cláusula ni venta importante pendiente de confirmación."
        lines = ["🔔 <b>Decisiones Pendientes de Confirmación Humana:</b>"]
        for p in pending:
            lines.append(
                f"• <code>{html.escape(p['decision_id'])}</code> [{html.escape(p['action_type'])}] "
                f"<b>{html.escape(p['nombre'])}</b> por <b>{int(p['amount_eur']):,} €</b>\n"
                f"  <i>{html.escape(str(p.get('rationale', '')))}</i>\n"
                f"  👉 <code>/aprobar {html.escape(p['decision_id'])}</code> | <code>/rechazar {html.escape(p['decision_id'])}</code>"
            )
        return "\n".join(lines)

    def _cmd_rechazar(self, decision_id: str) -> str:
        if not decision_id:
            return "Indica el ID: <code>/rechazar CL-1234</code>"
        rec = self.memory.resolve_pending_decision(decision_id, "REJECTED")
        if not rec:
            return f"No encontré ninguna decisión con ID <code>{html.escape(decision_id)}</code>."
        return f"🗑️ Descartada decisión <code>{html.escape(rec['decision_id'])}</code> ({html.escape(rec['nombre'])})."

    def _cmd_aprobar(self, decision_id: str) -> str:
        if not decision_id:
            return "Indica el ID: <code>/aprobar CL-1234</code>"
        open_items = self.memory.get_decisions_by_statuses(["PENDING", "APROBAR", "APPROVED", "SI", "SÍ"])
        target = next((p for p in open_items if str(p["decision_id"]).upper() == decision_id.strip().upper()), None)
        if not target:
            return f"No hay decisión pendiente activa con ID <code>{html.escape(decision_id)}</code> (usa <code>/pendientes</code>)."

        delta = self.delta_refresher.fetch(max_cache_age_seconds=0)
        auth = self.delta_refresher.session_auth
        is_dry = GeneralSettings.DRY_RUN or (StrategySettings.AUTOPILOT_MODE == "DRY_RUN")
        actions = BiwengerMarketActions(
            session=(auth.session if auth else None),
            league_id=int(delta.get("league_id") or 0),
            user_id=int(delta.get("team_id") or 0),
            dry_run=is_dry,
        )

        act_type = target["action_type"]
        pid = int(target["player_id"])
        amt = int(target["amount_eur"])

        if act_type == "CLAUSE_SNIPE":
            owner_uid = int(target.get("owner_user_id") or 0)
            if owner_uid <= 0:
                owner_uid = int((delta.get("manager_id_by_name") or {}).get(str(target.get("counterparty", "")), 0))
            if owner_uid <= 0 and not is_dry:
                return f"⚠️ No pude resolver el user_id del rival propietario ({html.escape(str(target.get('counterparty')))})."
            res = actions.pay_clause(player_id=pid, amount=amt, owner_user_id=owner_uid)
        elif act_type == "DEFENSIVE_SHIELD":
            res = actions.raise_clause(player_id=pid, increment_eur=amt)
        elif act_type == "IMPORTANT_SALE":
            oid = int(target.get("offer_id") or 0)
            live_of = (delta.get("waiting_offers_by_pid") or {}).get(str(pid))
            if live_of and int(live_of.get("offer_id", 0)) > 0:
                oid = int(live_of["offer_id"])
            if oid <= 0 and not is_dry:
                return f"⚠️ No hay una oferta activa en Biwenger ahora mismo para <b>{html.escape(target['nombre'])}</b>."
            res = actions.accept_offer(offer_id=oid)
        else:
            return f"Tipo de acción no soportado: {html.escape(act_type)}"

        if res.get("status") in ("OK", "DRY_RUN"):
            new_st = "DRY_RUN_APPROVED" if res.get("status") == "DRY_RUN" else "EXECUTED"
            self.memory.resolve_pending_decision(target["decision_id"], new_st)
            return (
                f"✅ <b>Orden Ejecutada ({res.get('status')}):</b> <code>{html.escape(target['decision_id'])}</code> — "
                f"<b>{html.escape(target['nombre'])}</b> por <code>{amt:,} €</code>."
            )
        return f"❌ Error al ejecutar en Biwenger (<code>{html.escape(str(res.get('status')))}</code>): {html.escape(str(res.get('detail', ''))[:200])}"

    def _handle_natural_conversation(self, user_msg: str) -> str:
        """Conversational AI Director response grounded in live state & persistent strategy memory."""
        delta = self.delta_refresher.fetch(max_cache_age_seconds=180)
        rules = LeagueRulesEngine.load()
        try:
            cal = StrictCausalDataView().df_calendar
        except Exception:
            cal = pd.DataFrame()
        phase = rules.resolve_round_phase(cal)
        mem_brief = self.memory.build_context_brief_for_llm()
        latest = self._load_latest_plan()

        # Persist user message in strategic memory if they express a preference/instruction
        lower_m = user_msg.lower()
        if any(k in lower_m for k in ("recuerda", "no vendas", "prioriza", "reserva", "estrategia", "parón", "paron", "clausula", "cláusula", "aguanta")):
            self.memory.add_user_directive("note", user_msg, source="TELEGRAM_CHAT")

        plan_lu = ((latest.get("plan") or {}).get("lineup") or {})
        prompt = f"""
Mensaje del Mánager ({delta.get('team_name')} en {delta.get('league_name')}):
"{user_msg}"

Estado Real en Vivo ({delta.get('fetched_at')}):
- Saldo actual: {delta.get('balance')} € | Modo Autopilot: {StrategySettings.AUTOPILOT_MODE}
- Fase Jornada J{phase.target_jornada_num}: {phase.phase_name} | Kickoff: {phase.next_kickoff_ts} ({phase.hours_to_kickoff:.1f}h)
- Cláusulas abiertas ({rules.clause_disabled_hours}h): {phase.clauses_open} (Cierre: {phase.clause_lock_ts}) | Parón Internacional: {phase.is_international_break}
- Límite ventas simultáneas mercado: {rules.market_max_user_sales} (Actualmente listados: {len(delta.get('currently_listed_pids') or [])})
- Once MILP actual: {plan_lu.get('formation', 'N/D')} ({plan_lu.get('expected_points', 0)} xP)

{mem_brief}

Responde en español, directo, cercano y cuantitativo (máximo 180 palabras). Si el usuario quiere ejecutar una cláusula o venta pendiente, recuérdale el comando `/aprobar <ID>` o `/intocable <Jugador>`. No uses etiquetas Markdown complejas incompatibles, usa texto limpio con viñetas simples.
"""
        try:
            client = TieredLLMRouter.get_client(tier="daily")
            ans = str(client.call(
                prompt=prompt,
                system_prompt="Eres el Director Deportivo Cuantitativo (Fantasy Champion v3) del usuario en Biwenger.",
            )).strip()
            return html.escape(ans)
        except Exception as e:
            return (
                f"📊 <b>Estado Rápido (Modo Offline LLM: {html.escape(str(e)[:80])}):</b>\n"
                f"Saldo: <code>{delta.get('balance')} €</code> | Fase J{phase.target_jornada_num}: <code>{phase.phase_name}</code> | "
                f"Cláusulas abiertas: <code>{phase.clauses_open}</code>.\nUsa <code>/estado</code>, <code>/tesis</code> o <code>/pendientes</code>."
            )

    def poll_once(self, timeout_sec: int = 0, discover_chat_id: bool = False) -> int:
        """Drains pending Telegram updates once and returns number of processed messages."""
        if not self.token:
            print("⚠️ TELEGRAM_BOT_TOKEN no configurado en .env.")
            return 0

        offset = self._load_offset()
        try:
            r = requests.get(
                f"{self.api_url}/getUpdates",
                params={"offset": offset, "timeout": int(timeout_sec)},
                timeout=max(10, int(timeout_sec) + 8),
            )
            if r.status_code != 200:
                print(f"⚠️ Telegram getUpdates HTTP {r.status_code}: {r.text[:200]}")
                return 0
            updates = r.json().get("result") or []
        except Exception as e:
            print(f"⚠️ Error consultando Telegram getUpdates: {e}")
            return 0

        processed = 0
        for upd in updates:
            upd_id = int(upd.get("update_id", 0))
            self._save_offset(upd_id + 1)

            msg_obj = upd.get("message") or upd.get("edited_message") or {}
            chat_id = str((msg_obj.get("chat") or {}).get("id", "")).strip()
            text = str(msg_obj.get("text", "") or "").strip()
            if not chat_id or not text:
                continue

            if discover_chat_id and not self.allowed_chat_id:
                print(f"🔑 Chat ID detectado: TELEGRAM_ALLOWED_CHAT_ID={chat_id}")
                self.send_reply(
                    chat_id,
                    f"🔑 Tu <code>TELEGRAM_ALLOWED_CHAT_ID</code> es: <code>{chat_id}</code>\nAñádelo a tu archivo <code>.env</code>.",
                )
                processed += 1
                continue

            if self.allowed_chat_id and chat_id != self.allowed_chat_id:
                print(f"🛡️ Ignorando mensaje de chat_id no autorizado: {chat_id}")
                continue

            reply = self.handle_message_text(text)
            self.send_reply(chat_id, reply, parse_mode="HTML")
            processed += 1

        return processed

    def listen_forever(self, discover_chat_id: bool = False) -> None:
        print("🤖 Escuchando mensajes en Telegram en tiempo real (Ctrl+C para salir)...")
        while True:
            try:
                self.poll_once(timeout_sec=25, discover_chat_id=discover_chat_id)
            except KeyboardInterrupt:
                print("\n👋 Bot de Telegram detenido.")
                break
            except Exception as e:
                print(f"⚠️ Error en bucle Telegram: {e}")
                time.sleep(5)


def main():
    parser = argparse.ArgumentParser(description="Fantasy Champion v3 — Bidirectional Telegram Sports Director")
    parser.add_argument("--listen", action="store_true", help="Run continuous 24/7 long-polling listener")
    parser.add_argument("--drain-once", action="store_true", help="Process pending messages once and exit (CI/Webhook)")
    parser.add_argument("--discover-chat-id", action="store_true", help="Print & reply incoming chat_id for setup")
    parser.add_argument("--direct-chat-id", default="", help="Direct chat_id from webhook repository_dispatch payload")
    parser.add_argument("--direct-text", default="", help="Direct message text from webhook repository_dispatch payload")
    args = parser.parse_args()

    bot = TelegramDirectorBot()
    if args.direct_chat_id and args.direct_text:
        if bot.allowed_chat_id and str(args.direct_chat_id).strip() != bot.allowed_chat_id:
            print("🛡️ Ignorando direct-chat-id no autorizado.")
            return
        reply = bot.handle_message_text(args.direct_text)
        bot.send_reply(str(args.direct_chat_id).strip(), reply, parse_mode="HTML")
        print("✅ Respondido mensaje directo de webhook.")
    elif args.listen:
        bot.listen_forever(discover_chat_id=args.discover_chat_id)
    else:
        n = bot.poll_once(timeout_sec=1, discover_chat_id=args.discover_chat_id)
        print(f"✅ Procesados {n} mensaje(s) de Telegram.")


if __name__ == "__main__":
    main()
