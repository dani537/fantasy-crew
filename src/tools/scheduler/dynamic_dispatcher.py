"""
Dynamic Calendar-Driven Tactical Pulse Dispatcher (`dynamic_dispatcher.py`)
===========================================================================
Solves the problem that LaLiga matchdays do NOT always start on Friday at 21:00
(midweek Tuesday/Wednesday rounds, Thursday openers, Saturday openers, FIFA breaks):

- Designed to be invoked periodically (e.g., every 20-30 minutes via `tactical_pulse.yml`
  or local cron) with ZERO Biwenger API calls when outside tactical windows (<0.2s exit!).
- Inspects `Partidos_Calendario` via `LeagueRulesEngine.resolve_round_phase(now_ts)`:
  1. `PRE_CLAUSE_LOCK` (`T_kickoff - clauseRoundDisabledHours - 2.5h` .. `T_clause_lock`):
     Last window before clauses freeze (e.g. Wednesday 18:15-20:55 before Friday 21:00 kickoff).
  2. `PRE_KICKOFF_SOLVENCY` (`T_kickoff - 3.0h` .. `T_kickoff - 45m`):
     Ensures `balance >= 0 €` before round start so the team NEVER scores 0 points.
  3. `FINAL_LINEUP_GUARD` (`T_kickoff - 45m` .. `T_kickoff - 8m`):
     Refreshes live injuries/suspensions & Comuniate XI right before kickoff and updates MILP XI!
- Uses `StrategyMemoryLedger.has_round_event_fired` (`Eventos_Jornada`) for 100% idempotency
  (each tactical window fires at most ONCE per round!).
- Also drains any pending incoming Telegram messages (`--drain-telegram`) during the pulse!
"""

import argparse
import datetime
import re
from typing import Dict, Any, Optional, List
import pandas as pd

from src.config import StrategySettings, GoogleSheetsConfig
from src.tools.strategy_engine.league_rules import LeagueRulesEngine
from src.tools.strategy_engine.strategy_memory import StrategyMemoryLedger
from src.tools.backtester.causal_clock import StrictCausalDataView


class DynamicRoundDispatcher:
    """Zero-API-cost calendar check that triggers `AutopilotRunner` ONLY inside critical windows."""

    def __init__(self, db_path: str = "./data/warehouse/biwenger_history.db"):
        self.db_path = db_path
        self.rules = LeagueRulesEngine.load()
        self.memory = StrategyMemoryLedger(db_path=self.db_path, sync_sheets=GoogleSheetsConfig.ENABLED)

    def process_human_approvals(
        self,
        approve_id: Optional[str] = None,
        reject_id: Optional[str] = None,
        notify: bool = True,
    ) -> List[Dict[str, str]]:
        """
        Processes explicit CLI/workflow approvals (`--approve-id`, `--reject-id`) AND
        rows edited directly in Google Sheets `Decisiones_Pendientes` (`APROBAR` / `RECHAZAR`).
        """
        from src.tools.notifications.telegram_bot import TelegramDirectorBot
        from src.tools.notifications.notifier import NotificationHub

        bot = TelegramDirectorBot()
        bot.memory = self.memory
        notifier = NotificationHub()
        results: List[Dict[str, str]] = []

        to_reject_ids: List[str] = []
        if reject_id and str(reject_id).strip():
            to_reject_ids.append(str(reject_id).strip().upper())
        for row in self.memory.get_decisions_by_statuses(["RECHAZAR", "REJECT", "REJECTED_BY_USER", "NO"]):
            did = str(row.get("decision_id", "")).strip().upper()
            if did and did not in to_reject_ids:
                to_reject_ids.append(did)

        for did in to_reject_ids:
            msg_html = bot._cmd_rechazar(did)
            clean_msg = re.sub(r"<[^>]+>", "", msg_html)
            print(f"🗑️ {clean_msg}")
            results.append({"decision_id": did, "action": "REJECT", "result": clean_msg})

        to_approve_ids: List[str] = []
        if approve_id and str(approve_id).strip():
            to_approve_ids.append(str(approve_id).strip().upper())
        for row in self.memory.get_decisions_by_statuses(["APROBAR", "APPROVE", "APPROVED", "SI", "SÍ"]):
            did = str(row.get("decision_id", "")).strip().upper()
            if did and did not in to_approve_ids:
                to_approve_ids.append(did)

        for did in to_approve_ids:
            msg_html = bot._cmd_aprobar(did)
            clean_msg = re.sub(r"<[^>]+>", "", msg_html)
            print(f"⚡ {clean_msg}")
            results.append({"decision_id": did, "action": "APPROVE", "result": clean_msg})
            if notify:
                subj = f"✅ Orden SEMI_AUTO procesada [{did}] — Fantasy Champion v3"
                notifier.send_email(
                    subject=subj,
                    html_body=f"<div style='font-family:sans-serif;padding:16px;'><h3>{subj}</h3><p>{msg_html}</p></div>",
                    text_body=clean_msg,
                )
                notifier.send_telegram(msg_html, parse_mode="HTML")

        return results

    def _ensure_full_warehouse_ready(self) -> None:
        """Hydrates full Google Sheets tables only when a tactical Autopilot cycle is actually about to execute."""
        view = StrictCausalDataView(db_path=self.db_path)
        today_iso = datetime.date.today().isoformat()
        if GoogleSheetsConfig.ENABLED and (not view.all_dates or view.all_dates[-1] < today_iso):
            try:
                from src.tools.data_extraction.sync_local import sync_sheets_to_local
                print("☁️ Hidratando almacén completo desde Google Sheets antes de disparar Autopilot táctico...")
                sync_sheets_to_local(refresh_live_api=False)
            except Exception as e:
                print(f"⚠️ Aviso al hidratar almacén completo: {e}")

    def check_and_dispatch(
        self,
        now_ts: Optional[pd.Timestamp] = None,
        mode: Optional[str] = None,
        notify: bool = True,
        drain_telegram: bool = True,
        force_phase: Optional[str] = None,
        approve_id: Optional[str] = None,
        reject_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        # 0. Process any human-gated approvals/rejections from Google Sheets or workflow inputs
        approval_results = self.process_human_approvals(
            approve_id=approve_id,
            reject_id=reject_id,
            notify=notify,
        )

        # 1. Drain any pending Telegram messages if configured (uses Telegram API, 0 Biwenger calls unless message present)
        tg_processed = 0
        if drain_telegram:
            try:
                from src.tools.notifications.telegram_bot import TelegramDirectorBot
                bot = TelegramDirectorBot()
                if bot.token:
                    tg_processed = bot.poll_once(timeout_sec=1)
            except Exception as e_tg:
                print(f"⚠️ Dispatcher Telegram drain warning: {e_tg}")

        # 2. Check calendar phase & settled-round / strategy cadence locally (<0.1s, 0 API calls)
        view = StrictCausalDataView(db_path=self.db_path)
        phase = self.rules.resolve_round_phase(view.df_calendar, now_ts=now_ts)
        jn = phase.target_jornada_num

        # Detect newly settled round (`POST_ROUND_SETTLEMENT`) from local warehouse
        settled_jn = 0
        try:
            import sqlite3
            with sqlite3.connect(self.db_path) as conn:
                df_pr = pd.read_sql("SELECT DISTINCT Jornada FROM Primas_Jornadas", conn)
            if not df_pr.empty:
                j_nums = [int("".join(c for c in str(v) if c.isdigit())) for v in df_pr["Jornada"] if any(c.isdigit() for c in str(v))]
                if j_nums:
                    settled_jn = max(j_nums)
        except Exception:
            settled_jn = 0

        # Resolve strategy-driven clause timing mode (`IMMEDIATE | PRE_LOCK_SURGICAL | HYBRID`)
        timing_mode = StrategySettings.CLAUSE_TIMING_MODE
        try:
            from src.tools.strategy_engine.llm_supervisor import LLMStrategicSupervisor
            sup_state = LLMStrategicSupervisor.load_persisted_state(memory_ledger=self.memory)
            sup_timing = str((sup_state.get("overrides") or {}).get("clause_timing_preference", "AUTO")).upper()
            if sup_timing in ("IMMEDIATE", "PRE_LOCK_SURGICAL", "HYBRID"):
                timing_mode = sup_timing
        except Exception:
            pass

        chosen_trigger: Optional[str] = force_phase
        chosen_jn = jn
        if not chosen_trigger:
            if not phase.is_schedule_placeholder:
                if phase.in_final_lineup_guard_window:
                    if not self.memory.has_round_event_fired(f"J{jn}_FINAL_LINEUP_GUARD"):
                        chosen_trigger = "FINAL_LINEUP_GUARD"
                elif phase.in_pre_kickoff_solvency_window:
                    if not self.memory.has_round_event_fired(f"J{jn}_PRE_KICKOFF_SOLVENCY"):
                        chosen_trigger = "PRE_KICKOFF_SOLVENCY"
                elif phase.in_pre_clause_lock_window and timing_mode in ("PRE_LOCK_SURGICAL", "HYBRID", "IMMEDIATE"):
                    if not self.memory.has_round_event_fired(f"J{jn}_PRE_CLAUSE_LOCK"):
                        chosen_trigger = "PRE_CLAUSE_LOCK"
            if not chosen_trigger and settled_jn >= 1 and phase.active_in_progress_jornada is None:
                if not self.memory.has_round_event_fired(f"J{settled_jn}_POST_ROUND_SETTLEMENT"):
                    chosen_trigger = "POST_ROUND_SETTLEMENT"
                    chosen_jn = settled_jn

        if not chosen_trigger:
            return {
                "triggered": False,
                "approvals_processed": approval_results,
                "telegram_messages_processed": tg_processed,
                "phase": phase.to_dict(),
                "clause_timing_mode": timing_mode,
                "reason": "Outside tactical windows or already executed for this round.",
            }

        event_key = f"J{chosen_jn}_{chosen_trigger}"
        eff_mode = mode or StrategySettings.AUTOPILOT_MODE
        print(f"⚡ Ventana táctica activa detectada: {event_key} (Kickoff J{jn}: {phase.next_kickoff_ts}) -> Disparando Autopilot ({eff_mode})...")

        self._ensure_full_warehouse_ready()

        from src.tools.strategy_engine.autopilot import AutopilotRunner
        runner = AutopilotRunner(
            mode=eff_mode,
            db_path=self.db_path,
            trigger_phase=chosen_trigger,
            use_live_delta=True,
        )
        summary = runner.execute_cycle(generate_briefing=True, send_notifications=notify)

        if chosen_trigger != "MORNING_FULL":
            self.memory.mark_round_event_fired(
                event_key=event_key,
                jornada_num=chosen_jn,
                trigger_type=chosen_trigger,
                status="OK",
                summary=f"Modo={eff_mode} | SaldoProy={summary.get('projected_cash')} | XI={summary['plan']['lineup']['formation']}",
            )
        return {
            "triggered": True,
            "event_key": event_key,
            "trigger_phase": chosen_trigger,
            "approvals_processed": approval_results,
            "telegram_messages_processed": tg_processed,
            "phase": phase.to_dict(),
            "autopilot_summary": summary,
        }


def main():
    parser = argparse.ArgumentParser(description="Fantasy Champion v3 — Dynamic Calendar Pulse Dispatcher")
    parser.add_argument("--mode", default=None, choices=["DRY_RUN", "SEMI_AUTO", "FULL_AUTO"])
    parser.add_argument(
        "--force-phase",
        default=None,
        choices=["MORNING_FULL", "POST_ROUND_SETTLEMENT", "PRE_CLAUSE_LOCK", "PRE_KICKOFF_SOLVENCY", "FINAL_LINEUP_GUARD"],
    )
    parser.add_argument("--approve-id", default=None, help="Immediately approve & execute a SEMI_AUTO pending decision ID")
    parser.add_argument("--reject-id", default=None, help="Immediately reject a SEMI_AUTO pending decision ID")
    parser.add_argument("--check-morning-done", action="store_true", help="Print ALREADY_DONE if today's MORNING_FULL already succeeded")
    parser.add_argument("--no-notify", action="store_true", help="Disable Email/Telegram push")
    parser.add_argument("--no-telegram-drain", action="store_true", help="Skip draining incoming Telegram messages")
    args = parser.parse_args()

    dispatcher = DynamicRoundDispatcher()
    if args.check_morning_done:
        today_key = f"{datetime.date.today().isoformat()}_MORNING_FULL"
        if dispatcher.memory.has_round_event_fired(today_key):
            print("ALREADY_DONE")
        else:
            print("NEEDS_RUN")
        return

    res = dispatcher.check_and_dispatch(
        mode=args.mode,
        notify=(not args.no_notify),
        drain_telegram=(not args.no_telegram_drain),
        force_phase=args.force_phase,
        approve_id=args.approve_id,
        reject_id=args.reject_id,
    )
    if res["triggered"]:
        print(f"✅ Disparador ejecutado: {res['event_key']}")
    else:
        p = res["phase"]
        print(
            f"💤 Pulso sin ventana activa (J{p['target_jornada_num']} · {p['phase_name']} · "
            f"Kickoff en {p['hours_to_kickoff']}h · Aprobaciones={len(res.get('approvals_processed', []))} · "
            f"Telegram msgs={res['telegram_messages_processed']})."
        )


if __name__ == "__main__":
    main()
