"""
Dual Outbound Notification Hub: HTML Email (SMTP) + Telegram Push (`notifier.py`)
=================================================================================
Delivers automated morning briefings, intraday tactical alerts (`PRE_CLAUSE_LOCK`,
`PRE_KICKOFF_SOLVENCY`, `FINAL_LINEUP_GUARD`), and `SEMI_AUTO` approval requests
simultaneously via:
  1. Rich Executive HTML Email (`GMAIL_ADDRESS` / `GMAIL_ADRESS` + `GMAIL_PASSWORD` -> `NOTIFY_EMAIL_TO`)
  2. Formatted Telegram Bot Push (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_ALLOWED_CHAT_ID`)
"""

import os
import re
import html
import json
import smtplib
import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, List, Any, Optional
import requests

from src.config import Credentials, resolve_active_user_identity


NOTIFICATION_LOG_PATH = "./data/autopilot/notifications_log.jsonl"


class NotificationHub:
    """Unified HTML Email + Telegram notification dispatcher."""

    def __init__(self, log_path: str = NOTIFICATION_LOG_PATH):
        self.log_path = log_path
        os.makedirs(os.path.dirname(self.log_path) or ".", exist_ok=True)

    @property
    def email_configured(self) -> bool:
        return bool(Credentials.GMAIL_ADDRESS and Credentials.GMAIL_PASSWORD and Credentials.NOTIFY_EMAIL_TO)

    @property
    def telegram_configured(self) -> bool:
        return bool(Credentials.TELEGRAM_BOT_TOKEN and Credentials.TELEGRAM_ALLOWED_CHAT_ID)

    def _log(self, channel: str, subject: str, ok: bool, detail: str = "") -> None:
        rec = {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "channel": channel,
            "subject": subject,
            "ok": ok,
            "detail": detail,
        }
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def send_email(self, subject: str, html_body: str, text_body: str) -> Dict[str, Any]:
        if not self.email_configured:
            return {"sent": False, "reason": "EMAIL_NOT_CONFIGURED"}

        sender = Credentials.GMAIL_ADDRESS
        pwd = Credentials.GMAIL_PASSWORD
        recipient = Credentials.NOTIFY_EMAIL_TO

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Fantasy Champion Bot <{sender}>"
        msg["To"] = recipient
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
                server.login(sender, pwd)
                server.sendmail(sender, [r.strip() for r in recipient.split(",") if r.strip()], msg.as_string())
            self._log("EMAIL", subject, True, f"Sent to {recipient}")
            return {"sent": True, "to": recipient}
        except Exception as e:
            self._log("EMAIL", subject, False, str(e))
            return {"sent": False, "reason": str(e)}

    def send_telegram(self, text: str, parse_mode: Optional[str] = "HTML") -> Dict[str, Any]:
        if not self.telegram_configured:
            return {"sent": False, "reason": "TELEGRAM_NOT_CONFIGURED"}

        token = Credentials.TELEGRAM_BOT_TOKEN
        chat_id = Credentials.TELEGRAM_ALLOWED_CHAT_ID
        url = f"https://api.telegram.org/bot{token}/sendMessage"

        # Split safely below Telegram's 4096-character limit
        chunks: List[str] = []
        rem = str(text or "").strip()
        while len(rem) > 3800:
            cut = rem.rfind("\n", 0, 3800)
            if cut <= 0:
                cut = 3800
            chunks.append(rem[:cut])
            rem = rem[cut:].lstrip("\n")
        if rem:
            chunks.append(rem)

        try:
            for ch in chunks:
                payload: Dict[str, Any] = {
                    "chat_id": chat_id,
                    "text": ch,
                    "disable_web_page_preview": True,
                }
                if parse_mode:
                    payload["parse_mode"] = parse_mode
                resp = requests.post(url, json=payload, timeout=15)
                if resp.status_code != 200 and parse_mode:
                    # Fallback to plain text if HTML entity mismatch occurred
                    payload.pop("parse_mode", None)
                    resp = requests.post(url, json=payload, timeout=15)
                if resp.status_code != 200:
                    self._log("TELEGRAM", "push", False, resp.text[:300])
                    return {"sent": False, "reason": f"HTTP {resp.status_code}: {resp.text[:200]}"}
            self._log("TELEGRAM", "push", True, f"Sent {len(chunks)} msg(s) to {chat_id}")
            return {"sent": True, "chat_id": chat_id}
        except Exception as e:
            self._log("TELEGRAM", "push", False, str(e))
            return {"sent": False, "reason": str(e)}

    def dispatch_cycle_report(
        self,
        cycle_summary: Dict[str, Any],
        pending_decisions: Optional[List[Dict[str, Any]]] = None,
        trigger_label: str = "MORNING_FULL",
    ) -> Dict[str, Any]:
        """Formats and dispatches an Autopilot cycle report to both HTML Email and Telegram."""
        ident = resolve_active_user_identity()
        team_name = cycle_summary.get("team_name") or ident.get("team_name", "Mi Equipo")
        league_name = cycle_summary.get("league_name") or ident.get("league_name", "Liga Biwenger")
        date_s = cycle_summary.get("date", datetime.date.today().isoformat())
        mode = cycle_summary.get("mode", "FULL_AUTO")
        j_num = cycle_summary.get("target_jornada_num", "?")
        phase_info = cycle_summary.get("phase_context") or {}
        phase_name = phase_info.get("phase_name", trigger_label)

        plan = cycle_summary.get("plan") or {}
        lineup = plan.get("lineup") or {}
        pending = pending_decisions or []

        subj = (
            f"🏆 [{league_name}] {team_name} — J{j_num} ({trigger_label}) | "
            f"Saldo: {int(cycle_summary.get('starting_cash', 0)):,}€ | XI: {lineup.get('expected_points', 0):.1f} xP"
        )
        if pending:
            subj = f"🔔 [{len(pending)} APROBACIÓN PENDIENTE] " + subj

        txt_body = self._build_plain_text(cycle_summary, pending, team_name, league_name, trigger_label, phase_name)
        tg_html = self._build_telegram_html(cycle_summary, pending, team_name, league_name, trigger_label, phase_name)
        email_html = self._build_email_html(cycle_summary, pending, team_name, league_name, trigger_label, phase_name)

        email_res = self.send_email(subject=subj, html_body=email_html, text_body=txt_body)
        tg_res = self.send_telegram(text=tg_html, parse_mode="HTML")
        return {"email": email_res, "telegram": tg_res, "subject": subj}

    @staticmethod
    def _build_plain_text(
        summary: Dict[str, Any],
        pending: List[Dict[str, Any]],
        team_name: str,
        league_name: str,
        trigger_label: str,
        phase_name: str,
    ) -> str:
        plan = summary.get("plan") or {}
        lu = plan.get("lineup") or {}
        lines = [
            f"FANTASY CHAMPION v3 — {team_name} ({league_name})",
            f"Fecha: {summary.get('date')} | Fase: {trigger_label} ({phase_name}) | Modo: {summary.get('mode')}",
            f"Saldo Inicial: {int(summary.get('starting_cash', 0)):,} € -> Proyectado: {int(summary.get('projected_cash', 0)):,} €",
            f"Once MILP J{summary.get('target_jornada_num')}: {lu.get('formation', '4-4-2')} ({lu.get('expected_points', 0):.1f} xP)",
            "",
        ]
        if lu.get("slots"):
            xi_str = ", ".join(f"{s['nombre']} ({s['linea']} {s['xp']:.1f})" for s in lu["slots"])
            lines.append(f"Titulares: {xi_str}")
        if plan.get("market_bids"):
            lines.append("\nPujas Autónomas:")
            for b in plan["market_bids"]:
                lines.append(f"  - {b['nombre']} ({b['position']}): {int(b['amount']):,} € (VM {int(b['market_price']):,} €)")
        if plan.get("preventive_listings"):
            lst = ", ".join(f"{p['nombre']} ({int(p['asking_price']):,}€)" for p in plan["preventive_listings"])
            lines.append(f"\nEscaparate Top-{len(plan['preventive_listings'])} (+5% VM): {lst}")
        if pending:
            lines.append("\n⚠️ REQUIERE TU CONFIRMACIÓN (Cláusulas / Ventas Importantes):")
            for p in pending:
                lines.append(
                    f"  - [{p['decision_id']}] {p['action_type']}: {p['nombre']} por {int(p['amount_eur']):,} € -> "
                    f"Cambia status a 'APROBAR' en Google Sheets (Decisiones_Pendientes), usa GitHub Actions (Run workflow), o '/aprobar {p['decision_id']}' en Telegram"
                )
        if summary.get("briefing"):
            lines.append("\n--- BRIEFING DIRECTOR DEPORTIVO ---\n" + str(summary["briefing"]))
        if summary.get("easy_summary"):
            lines.append("\n--- RESUMEN FÁCIL DE LO QUE SE HA HECHO HOY ---\n" + str(summary["easy_summary"]))
        return "\n".join(lines)

    @staticmethod
    def _build_telegram_html(
        summary: Dict[str, Any],
        pending: List[Dict[str, Any]],
        team_name: str,
        league_name: str,
        trigger_label: str,
        phase_name: str,
    ) -> str:
        esc = html.escape
        plan = summary.get("plan") or {}
        lu = plan.get("lineup") or {}
        phase_ctx = summary.get("phase_context") or {}
        scarcity = plan.get("scarcity_summary") or {}

        out = [
            f"🏆 <b>{esc(team_name)}</b> · <i>{esc(league_name)}</i>",
            f"📅 <code>{esc(str(summary.get('date')))}</code> | ⚡ <b>{esc(trigger_label)}</b> (<code>{esc(phase_name)}</code>)",
            f"💰 Caja: <b>{int(summary.get('starting_cash', 0)):,} €</b> → Proy: <b>{int(summary.get('projected_cash', 0)):,} €</b> "
            f"({'✅ Solvente' if summary.get('is_solvent') else '⚠️ Revisar Deuda'})",
        ]
        if phase_ctx.get("next_kickoff_ts"):
            out.append(
                f"⏱️ J{summary.get('target_jornada_num')} arranca: <code>{esc(str(phase_ctx['next_kickoff_ts']))}</code> "
                f"| Cláusulas: {'🟢 Abiertas' if plan.get('clauses_open', True) else '🔒 Cerradas (48h/Jornada)'}"
            )
        if scarcity:
            out.append(
                f"📊 Escasez Liga: Libres ≥5M: <b>{scarcity.get('free_ge_5m', 0)}</b> | Libres ≥10M: <b>{scarcity.get('free_ge_10m', 0)}</b>"
            )

        out.append(f"\n⚽ <b>Once Óptimo MILP ({esc(str(lu.get('formation', '4-4-2')))} · {lu.get('expected_points', 0):.1f} xP):</b>")
        if lu.get("slots"):
            by_ln: Dict[str, List[str]] = {"GK": [], "DF": [], "MF": [], "FW": []}
            for s in lu["slots"]:
                by_ln.setdefault(s["linea"], []).append(f"{esc(s['nombre'])} ({s['xp']:.1f})")
            for ln in ("GK", "DF", "MF", "FW"):
                if by_ln.get(ln):
                    out.append(f"  • <b>{ln}:</b> " + ", ".join(by_ln[ln]))

        execs = summary.get("executions") or []
        bids = plan.get("market_bids") or []
        is_full_auto = (str(summary.get("mode", "")).upper() == "FULL_AUTO")
        auto_sales = (
            (plan.get("all_sales") or [])
            if is_full_auto
            else [s for s in (plan.get("all_sales") or []) if not s.get("requires_human_approval")]
        )
        listings = plan.get("preventive_listings") or []

        out.append(f"\n🤖 <b>Operaciones Autónomas ({esc(str(summary.get('mode')))}):</b>")
        if bids:
            for b in bids:
                out.append(
                    f"  🎯 Puja: <b>{esc(b['nombre'])}</b> ({b['position']}) → <code>{int(b['amount']):,} €</code> "
                    f"(+{b['delta_xp_xi']:.2f} xP, P_win {int(float(b.get('win_prob_est', 0))*100)}%)"
                )
        else:
            out.append("  • Pujas: Ninguna supera umbral calidad/precio hoy.")

        if auto_sales:
            for s in auto_sales:
                out.append(f"  💸 Venta ejecutada: <b>{esc(s['nombre'])}</b> → <code>{int(s['sale_price']):,} €</code> ({esc(s['reason'])})")

        if is_full_auto:
            for c in (plan.get("clause_snipes") or []):
                out.append(f"  ⚡ Cláusula ejecutada: <b>{esc(c['nombre'])}</b> → <code>{int(c['amount']):,} €</code> a {esc(str(c.get('seller', '')))}")
            for sh in (plan.get("defensive_shields") or []):
                out.append(f"  🛡️ Blindaje ejecutado: <b>{esc(sh['nombre'])}</b> (+<code>{int(sh['recommended_cash_spend']):,} €</code>)")

        if listings:
            names_l = ", ".join(f"{esc(p['nombre'])}" for p in listings)
            out.append(f"  🏷️ Escaparate Top-{len(listings)} (+5% VM): {names_l} ({len(execs)} llamadas registradas)")

        if pending:
            out.append("\n🔔 <b>REQUIERE TU DECISIÓN (Cláusulas / Ventas Importantes):</b>")
            for p in pending:
                out.append(
                    f"  ⚠️ <code>{esc(p['decision_id'])}</code> [{esc(p['action_type'])}] <b>{esc(p['nombre'])}</b> "
                    f"por <b>{int(p['amount_eur']):,} €</b>\n"
                    f"     <i>{esc(str(p.get('rationale', ''))[:140])}</i>\n"
                    f"     👉 Aprobar: <code>/aprobar {esc(p['decision_id'])}</code> | Rechazar: <code>/rechazar {esc(p['decision_id'])}</code>"
                )

        out.append("\n💬 <i>Escríbeme cualquier duda o usa /estado, /once, /tesis, /pendientes, /intocable &lt;jugador&gt;.</i>")
        return "\n".join(out)

    @staticmethod
    def _build_email_html(
        summary: Dict[str, Any],
        pending: List[Dict[str, Any]],
        team_name: str,
        league_name: str,
        trigger_label: str,
        phase_name: str,
    ) -> str:
        esc = html.escape
        plan = summary.get("plan") or {}
        lu = plan.get("lineup") or {}
        phase_ctx = summary.get("phase_context") or {}
        scarcity = plan.get("scarcity_summary") or {}

        pending_html = ""
        if pending:
            rows = "".join(
                f"<tr><td style='padding:8px;border-bottom:1px solid #fecaca;'><b>{esc(p['decision_id'])}</b></td>"
                f"<td style='padding:8px;border-bottom:1px solid #fecaca;'>{esc(p['action_type'])}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #fecaca;'><b>{esc(p['nombre'])}</b></td>"
                f"<td style='padding:8px;border-bottom:1px solid #fecaca;'>{int(p['amount_eur']):,} €</td>"
                f"<td style='padding:8px;border-bottom:1px solid #fecaca;'>{esc(str(p.get('rationale', '')))}<br/>"
                f"<span style='color:#7f1d1d;font-size:12px;'>👉 En Google Sheets (pestaña <b>Decisiones_Pendientes</b>) escribe <code>APROBAR</code> en <i>status</i>, "
                f"o en GitHub Actions pulsa <b>Run workflow</b> con <code>{esc(p['decision_id'])}</code>, o en Telegram <code>/aprobar {esc(p['decision_id'])}</code></span></td></tr>"
                for p in pending
            )
            pending_html = f"""
            <div style="background:#fef2f2;border-left:4px solid #dc2626;padding:14px;margin:16px 0;border-radius:6px;">
              <h3 style="margin:0 0 8px 0;color:#991b1b;">🔔 Decisiones Reservadas para Ti (Cláusulas y Ventas Importantes)</h3>
              <table style="width:100%;border-collapse:collapse;font-size:13px;">
                <tr style="background:#fee2e2;text-align:left;">
                  <th style="padding:8px;">ID</th><th style="padding:8px;">Tipo</th><th style="padding:8px;">Jugador</th>
                  <th style="padding:8px;">Importe</th><th style="padding:8px;">Tesis / Comando Telegram</th>
                </tr>
                {rows}
              </table>
            </div>
            """

        is_full_auto = (str(summary.get("mode", "")).upper() == "FULL_AUTO")
        exec_ops_items: List[str] = []
        for s in (plan.get("all_sales") or []):
            if is_full_auto or not s.get("requires_human_approval"):
                exec_ops_items.append(
                    f"<li>💸 <b>Venta ejecutada:</b> {esc(s['nombre'])} por <b>{int(s['sale_price']):,} €</b> ({esc(s['reason'])})</li>"
                )
        if is_full_auto:
            for c in (plan.get("clause_snipes") or []):
                exec_ops_items.append(
                    f"<li>⚡ <b>Cláusula ejecutada:</b> {esc(c['nombre'])} por <b>{int(c['amount']):,} €</b> a {esc(str(c.get('seller', '')))} (+{c['delta_xp_xi']:.2f} xP)</li>"
                )
            for sh in (plan.get("defensive_shields") or []):
                exec_ops_items.append(
                    f"<li>🛡️ <b>Blindaje ejecutado:</b> {esc(sh['nombre'])} (+<b>{int(sh['recommended_cash_spend']):,} €</b>)</li>"
                )
        exec_ops_html = ""
        if exec_ops_items:
            exec_ops_html = (
                "<div style='background:#eff6ff;border-left:4px solid #2563eb;padding:12px 16px;margin:14px 0;border-radius:6px;font-size:13px;'>"
                "<b style='color:#1e40af;'>⚡ Ventas, Cláusulas y Blindajes Ejecutados Autónomamente:</b>"
                f"<ul style='margin:6px 0 0 0;padding-left:18px;'>{''.join(exec_ops_items)}</ul></div>"
            )

        bids_rows = "".join(
            f"<tr><td style='padding:6px;border-bottom:1px solid #e5e7eb;'><b>{esc(b['nombre'])}</b> ({b['position']})</td>"
            f"<td style='padding:6px;border-bottom:1px solid #e5e7eb;'>{int(b['market_price']):,} €</td>"
            f"<td style='padding:6px;border-bottom:1px solid #e5e7eb;'><b>{int(b['amount']):,} €</b></td>"
            f"<td style='padding:6px;border-bottom:1px solid #e5e7eb;'>+{b['delta_xp_xi']:.2f} xP ({esc(b['regime'])})</td></tr>"
            for b in (plan.get("market_bids") or [])
        ) or "<tr><td colspan='4' style='padding:8px;color:#6b7280;'>Sin pujas recomendadas hoy.</td></tr>"

        xi_rows = "".join(
            f"<span style='display:inline-block;background:#ecfdf5;color:#065f46;border:1px solid #a7f3d0;"
            f"padding:4px 9px;margin:3px;border-radius:12px;font-size:12px;'><b>{s['linea']}</b> · {esc(s['nombre'])} ({s['xp']:.1f} xP)</span>"
            for s in (lu.get("slots") or [])
        )

        listings_str = ", ".join(
            f"<b>{esc(p['nombre'])}</b> ({int(p['asking_price']):,} € · {esc(p.get('listing_reason', ''))})"
            for p in (plan.get("preventive_listings") or [])
        ) or "Ninguno"

        brief_html = esc(str(summary.get("briefing") or "")).replace("\n", "<br/>")

        # 📋 Resumen Ejecutivo de Decisiones Tomadas (Cierre claro del ciclo)
        bids_summary_str = ", ".join(f"<b>{esc(b['nombre'])}</b> ({int(b['amount']):,} €)" for b in (plan.get("market_bids") or [])) or "Ninguna puja hoy"
        sales_summary_str = ", ".join(f"<b>{esc(s['nombre'])}</b> ({int(s['sale_price']):,} €)" for s in (plan.get("all_sales") or [])) or "Ninguna venta forzada"
        listings_summary_str = ", ".join(f"<b>{esc(p['nombre'])}</b>" for p in (plan.get("preventive_listings") or [])) or "Ninguno"
        solvency_badge = "<span style='color:#16a34a;font-weight:700;'>✅ 100% Solvente</span>" if summary.get("is_solvent", True) else "<span style='color:#dc2626;font-weight:700;'>⚠️ Requiere Liquidez</span>"

        decisions_summary_html = f"""
        <div style="margin-top:20px;background:#f0fdf4;border:2px solid #86efac;border-radius:8px;padding:16px;">
          <h3 style="margin:0 0 10px 0;color:#166534;font-size:15px;">
            📋 Resumen Ejecutivo de Decisiones Tomadas en esta Ejecución
          </h3>
          <table style="width:100%;border-collapse:collapse;font-size:13px;line-height:1.6;">
            <tr>
              <td style="padding:6px 8px;border-bottom:1px solid #dcfce7;width:28%;font-weight:600;color:#14532d;">⚽ Alineación Sincronizada:</td>
              <td style="padding:6px 8px;border-bottom:1px solid #dcfce7;color:#1f2937;">Formación <b>{esc(str(lu.get('formation', '4-4-2')))}</b> ({lu.get('expected_points', 0):.1f} xP proyectados) · {len(lu.get('slots') or [])} titulares fijados en Biwenger.</td>
            </tr>
            <tr>
              <td style="padding:6px 8px;border-bottom:1px solid #dcfce7;font-weight:600;color:#14532d;">💰 Tesorería y Caja:</td>
              <td style="padding:6px 8px;border-bottom:1px solid #dcfce7;color:#1f2937;"><b>{int(summary.get('starting_cash', 0)):,} €</b> disponibles · {solvency_badge}.</td>
            </tr>
            <tr>
              <td style="padding:6px 8px;border-bottom:1px solid #dcfce7;font-weight:600;color:#14532d;">🎯 Ofertas y Pujas Mercado:</td>
              <td style="padding:6px 8px;border-bottom:1px solid #dcfce7;color:#1f2937;">{bids_summary_str}.</td>
            </tr>
            <tr>
              <td style="padding:6px 8px;border-bottom:1px solid #dcfce7;font-weight:600;color:#14532d;">🏷️ Escaparate (+5% s/VM):</td>
              <td style="padding:6px 8px;border-bottom:1px solid #dcfce7;color:#1f2937;">{listings_summary_str} (colateral activo y pescando ofertas pico).</td>
            </tr>
            <tr>
              <td style="padding:6px 8px;font-weight:600;color:#14532d;">⚡ Movimientos de Plantilla:</td>
              <td style="padding:6px 8px;color:#1f2937;">{sales_summary_str}.</td>
            </tr>
          </table>
        </div>
        """

        easy_raw = str(summary.get("easy_summary") or "").strip()
        easy_summary_html = ""
        if easy_raw:
            easy_fmt = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc(easy_raw)).replace("\n", "<br/>")
            easy_summary_html = f"""
            <div style="margin-top:20px;background:#f8fafc;border:2px solid #cbd5e1;border-radius:8px;padding:16px;">
              <h3 style="margin:0 0 8px 0;color:#1e293b;font-size:15px;">
                💬 En resumen: ¿qué se ha hecho hoy?
              </h3>
              <div style="font-size:13.5px;line-height:1.6;color:#334155;">
                {easy_fmt}
              </div>
            </div>
            """

        return f"""
        <html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#111827;max-width:740px;margin:0 auto;padding:16px;">
          <div style="background:linear-gradient(135deg,#064e3b,#047857);color:white;padding:18px 22px;border-radius:10px;">
            <h2 style="margin:0;">🏆 Fantasy Champion v3 — {esc(team_name)}</h2>
            <p style="margin:6px 0 0 0;opacity:0.9;font-size:14px;">
              Liga: <b>{esc(league_name)}</b> · Jornada Objetivo: <b>J{summary.get('target_jornada_num')}</b> ·
              Disparador: <b>{esc(trigger_label)}</b> ({esc(phase_name)}) · Modo: <b>{esc(str(summary.get('mode')))}</b>
            </p>
          </div>

          <div style="display:flex;gap:12px;margin:16px 0;flex-wrap:wrap;">
            <div style="flex:1;min-width:160px;background:#f9fafb;border:1px solid #e5e7eb;padding:12px;border-radius:8px;">
              <div style="font-size:12px;color:#6b7280;">SALDO ACTUAL → PROYECTADO</div>
              <div style="font-size:16px;font-weight:700;margin-top:4px;">{int(summary.get('starting_cash', 0)):,} € → {int(summary.get('projected_cash', 0)):,} €</div>
            </div>
            <div style="flex:1;min-width:160px;background:#f9fafb;border:1px solid #e5e7eb;padding:12px;border-radius:8px;">
              <div style="font-size:12px;color:#6b7280;">VENTANA CLÁUSULAS / INICIO J{summary.get('target_jornada_num')}</div>
              <div style="font-size:13px;font-weight:600;margin-top:4px;">
                {'🟢 Cláusulas Abiertas' if plan.get('clauses_open', True) else '🔒 Cláusulas Cerradas (48h)'}<br/>
                Kickoff: {esc(str(phase_ctx.get('next_kickoff_ts', 'N/D')))}
              </div>
            </div>
            <div style="flex:1;min-width:160px;background:#f9fafb;border:1px solid #e5e7eb;padding:12px;border-radius:8px;">
              <div style="font-size:12px;color:#6b7280;">ESCASEZ DE MERCADO</div>
              <div style="font-size:13px;font-weight:600;margin-top:4px;">
                Libres ≥5M: {scarcity.get('free_ge_5m', 0)} · Libres ≥10M: {scarcity.get('free_ge_10m', 0)}
              </div>
            </div>
          </div>

          {pending_html}
          {exec_ops_html}

          <h3 style="color:#065f46;border-bottom:2px solid #d1fae5;padding-bottom:6px;">⚽ Once Titular Óptimo MILP ({esc(str(lu.get('formation', '4-4-2')))} · {lu.get('expected_points', 0):.1f} xP)</h3>
          <div style="margin-bottom:16px;">{xi_rows}</div>

          <h3 style="color:#065f46;border-bottom:2px solid #d1fae5;padding-bottom:6px;">🎯 Pujas Autónomas de Mercado</h3>
          <table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:16px;">
            <tr style="background:#f3f4f6;text-align:left;">
              <th style="padding:6px;">Jugador</th><th style="padding:6px;">Valor Mercado</th>
              <th style="padding:6px;">Puja Óptima</th><th style="padding:6px;">Impacto</th>
            </tr>
            {bids_rows}
          </table>

          <p style="font-size:13px;background:#f9fafb;padding:10px;border-radius:6px;border:1px solid #e5e7eb;">
            <b>🏷️ Escaparate Preventivo Top-{len(plan.get('preventive_listings') or [])} (máx. oficial 5, +5% VM):</b> {listings_str}
          </p>

          <h3 style="color:#065f46;border-bottom:2px solid #d1fae5;padding-bottom:6px;">🧠 Informe del Director Deportivo</h3>
          <div style="font-size:13.5px;line-height:1.55;background:#ffffff;border:1px solid #e5e7eb;padding:14px;border-radius:8px;">
            {brief_html}
          </div>

          {decisions_summary_html}
          {easy_summary_html}
        </body></html>
        """
