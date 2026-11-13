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
from email.mime.image import MIMEImage
from typing import Dict, List, Any, Optional, Set
import requests

from src.config import Credentials, GeneralSettings, StrategySettings, resolve_active_user_identity
from src.tools.notifications.email_visuals import build_animated_hero_gif, build_animated_pitch_gif


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

    def send_email(
        self,
        subject: str,
        html_body: str,
        text_body: str,
        inline_images: Optional[Dict[str, bytes]] = None,
    ) -> Dict[str, Any]:
        if not self.email_configured:
            return {"sent": False, "reason": "EMAIL_NOT_CONFIGURED"}

        sender = Credentials.GMAIL_ADDRESS
        pwd = Credentials.GMAIL_PASSWORD
        recipient = Credentials.NOTIFY_EMAIL_TO

        alt_part = MIMEMultipart("alternative")
        alt_part.attach(MIMEText(text_body, "plain", "utf-8"))
        alt_part.attach(MIMEText(html_body, "html", "utf-8"))

        if inline_images:
            msg = MIMEMultipart("related")
            msg.attach(alt_part)
            for cid, raw_bytes in inline_images.items():
                if not raw_bytes:
                    continue
                img_part = MIMEImage(raw_bytes, _subtype="gif")
                img_part.add_header("Content-ID", f"<{cid}>")
                img_part.add_header("Content-Disposition", "inline", filename=f"{cid}.gif")
                img_part.add_header("X-Attachment-Id", cid)
                msg.attach(img_part)
        else:
            msg = alt_part

        msg["Subject"] = subject
        msg["From"] = f"Fantasy Champion Bot <{sender}>"
        msg["Reply-To"] = sender
        msg["To"] = recipient
        msg["X-Mailer"] = "FantasyChampion/3.0"

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

        inline_images: Dict[str, bytes] = {}
        try:
            sup_rep = cycle_summary.get("supervisor_report") or {}
            model_used = str(sup_rep.get("model_used") or GeneralSettings.LLM_MODEL_DAILY or "gemini-4-hydrogen")
            llm_badge = "GEMINI 4 HYDROGEN" if "hydrogen" in model_used.lower() else model_used.split("/")[-1].upper()
            hero_gif = build_animated_hero_gif(
                summary=cycle_summary,
                team_name=team_name,
                league_name=league_name,
                trigger_label=trigger_label,
                active_archetype=StrategySettings.ACTIVE_ARCHETYPE,
                llm_label=llm_badge,
            )
            if hero_gif:
                inline_images["hero_banner"] = hero_gif
            pitch_gif = build_animated_pitch_gif(lineup=lineup, jornada_num=j_num)
            if pitch_gif:
                inline_images["tactical_pitch"] = pitch_gif
        except Exception:
            inline_images = {}

        email_html = self._build_email_html(
            cycle_summary,
            pending,
            team_name,
            league_name,
            trigger_label,
            phase_name,
            inline_cids=set(inline_images.keys()),
        )

        email_res = self.send_email(
            subject=subj,
            html_body=email_html,
            text_body=txt_body,
            inline_images=inline_images or None,
        )
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
        rn = summary.get("rival_negotiations") or {}
        if rn.get("incoming_rival_offers") or rn.get("counter_offers") or rn.get("outgoing_bids"):
            lines.append("\nNegociaciones con Rivales:")
            for ro in (rn.get("incoming_rival_offers") or []):
                lines.append(f"  - Oferta recibida de {ro.get('from_name')} por tu {ro.get('nombre')}: {int(ro.get('amount', 0)):,} € ({ro.get('action_label', '')})")
            for co in (rn.get("counter_offers") or []):
                lines.append(f"  - Contraoferta de {co.get('from_name')} por {co.get('nombre')}: {int(co.get('amount', 0)):,} € ({co.get('action_label') or co.get('verdict_label', '')})")
            for ob in (rn.get("outgoing_bids") or []):
                lines.append(f"  - Puja abierta a {ob.get('to_name')} por {ob.get('nombre')}: {int(ob.get('amount', 0)):,} € ({ob.get('status_label', '')}) [ID {ob.get('offer_id')}]")
            if rn.get("freed_credit_eur"):
                lines.append(f"  - Crédito liberado hoy: +{int(rn.get('freed_credit_eur', 0)):,} € | Puja máx operativa: {int(rn.get('effective_available_max_bid', 0)):,} €")
        blt = summary.get("league_bulletin") or {}
        if blt.get("clauses") or blt.get("manager_deals") or blt.get("my_standings"):
            lines.append("\nBoletín de Noticias de tu Liga:")
            ms = blt.get("my_standings") or {}
            if ms:
                lines.append(
                    f"  - Clasificación: #{ms.get('rank')}/{ms.get('total_managers')} ({ms.get('points')} pts, "
                    f"a {ms.get('points_to_leader', 0)} pts de {ms.get('leader_name', '')})"
                )
            for c in (blt.get("clauses") or [])[:4]:
                lines.append(f"  - Clausulazo: {c.get('to_name')} pagó {int(c.get('amount', 0)):,} € por {c.get('nombre')} ({c.get('from_name')})")
            for d in (blt.get("manager_deals") or [])[:3]:
                lines.append(f"  - Fichaje: {d.get('to_name')} -> {d.get('nombre')} desde {d.get('from_name')} ({int(d.get('amount', 0)):,} €)")
        if pending:
            lines.append("\n⚠️ REQUIERE TU CONFIRMACIÓN (Cláusulas / Ventas Importantes):")
            for p in pending:
                lines.append(
                    f"  - [{p['decision_id']}] {p['action_type']}: {p['nombre']} por {int(p['amount_eur']):,} € -> "
                    f"Cambia status a 'APROBAR' en Google Sheets (Decisiones_Pendientes), usa GitHub Actions (Run workflow), o '/aprobar {p['decision_id']}' en Telegram"
                )
        if summary.get("briefing"):
            lines.append("\n--- BRIEFING DIRECTOR DEPORTIVO ---\n" + NotificationHub._normalize_llm_text_to_markdown(str(summary["briefing"])))
        if summary.get("easy_summary"):
            lines.append("\n--- RESUMEN FÁCIL DE LO QUE SE HA HECHO HOY ---\n" + NotificationHub._normalize_llm_text_to_markdown(str(summary["easy_summary"])))
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
        if summary.get("high_value_clause_alerts"):
            for hvc in summary["high_value_clause_alerts"]:
                out.append(
                    f"🚨 <b>CLAUSULAZO AUTÓNOMO ALTO IMPORTE:</b> <b>{esc(hvc['nombre'])}</b> "
                    f"({int(hvc['amount']):,} € a {esc(hvc.get('seller', ''))}, +{hvc.get('delta_xp_xi', 0):.2f} xP)"
                )
        posture_info = summary.get("strategy_posture") or {}
        if posture_info.get("posture"):
            out.append(
                f"🧭 Postura: <b>{esc(str(posture_info['posture']))}</b> "
                f"(#{posture_info.get('my_rank', '?')}/{posture_info.get('total_managers', '?')}, "
                f"{posture_info.get('gap_to_leader', 0):+.0f} pts)"
            )
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
                buyer_tag = f" → <i>{esc(str(s['buyer_name']))}</i>" if (s.get("is_rival_buyer") and s.get("buyer_name")) else ""
                out.append(f"  💸 Venta ejecutada: <b>{esc(s['nombre'])}</b>{buyer_tag} → <code>{int(s['sale_price']):,} €</code> ({esc(s['reason'])})")

        rn_tg = summary.get("rival_negotiations") or {}
        if rn_tg.get("counter_offers") or rn_tg.get("incoming_rival_offers"):
            for ro in (rn_tg.get("incoming_rival_offers") or []):
                out.append(
                    f"  🤝 Oferta de <b>{esc(str(ro.get('from_name', '')))}</b> por <b>{esc(str(ro.get('nombre', '')))}</b>: "
                    f"<code>{int(ro.get('amount', 0)):,} €</code> ({esc(str(ro.get('action_label', '')))})"
                )
            for co in (rn_tg.get("counter_offers") or []):
                out.append(
                    f"  📩 Contraoferta de <b>{esc(str(co.get('from_name', '')))}</b> por <b>{esc(str(co.get('nombre', '')))}</b>: "
                    f"<code>{int(co.get('amount', 0)):,} €</code> ({esc(str(co.get('action_label') or co.get('verdict_label', '')))})"
                )
            if rn_tg.get("freed_credit_eur"):
                out.append(f"  🔓 Crédito liberado: <code>+{int(rn_tg.get('freed_credit_eur', 0)):,} €</code> (Puja máx: <code>{int(rn_tg.get('effective_available_max_bid', 0)):,} €</code>)")

        blt_tg = summary.get("league_bulletin") or {}
        if blt_tg.get("clauses") or blt_tg.get("manager_deals"):
            out.append("\n📰 <b>Boletín de la Liga:</b>")
            for c in (blt_tg.get("clauses") or [])[:3]:
                out.append(f"  💥 Clausulazo: <b>{esc(str(c.get('to_name')))}</b> → {esc(str(c.get('nombre')))} ({esc(str(c.get('from_name')))}) <code>{int(c.get('amount', 0)):,} €</code>")
            for d in (blt_tg.get("manager_deals") or [])[:2]:
                out.append(f"  🔄 {esc(str(d.get('to_name')))} fichó a {esc(str(d.get('nombre')))} (<code>{int(d.get('amount', 0)):,} €</code>)")

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
    def _normalize_llm_text_to_markdown(raw_text: str) -> str:
        """Converts raw markdown or structured JSON LLM responses into clean human-readable markdown."""
        txt = str(raw_text or "").strip()
        if not txt:
            return ""
        candidate = txt
        if candidate.startswith("```"):
            candidate = re.sub(r"^```(?:json|markdown|md)?\s*", "", candidate, flags=re.IGNORECASE)
            candidate = re.sub(r"\s*```$", "", candidate).strip()

        if candidate.startswith("{") or candidate.startswith("["):
            try:
                parsed = json.loads(candidate)
            except Exception:
                parsed = None
            if parsed is not None:
                title_map = {
                    "1_diagnostico_financiero_fase_y_escasez": "1. Diagnóstico Financiero, Fase y Escasez",
                    "2_operaciones_autonomas_ejecutadas": "2. Operaciones Autónomas Ejecutadas",
                    "3_operaciones_de_alto_impacto": "3. Operaciones de Alto Impacto",
                    "4_continuidad_estrategica_medio_plazo": "4. Continuidad Estratégica a Medio Plazo",
                }
                lines: List[str] = []

                def _clean_item(val: Any) -> str:
                    item_s = str(val or "").strip()
                    item_s = re.sub(r"^[•\-*]+\s+", "", item_s)
                    item_s = re.sub(r"^([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 /+()%-]{2,42}):\s+", r"**\1:** ", item_s)
                    return item_s

                if isinstance(parsed, dict):
                    for k, v in parsed.items():
                        k_str = str(k or "").strip()
                        k_low = k_str.lower()
                        if k_low in ("resumen", "easy_summary", "puntos", "summary") and isinstance(v, list):
                            for entry in v:
                                c = _clean_item(entry)
                                if c:
                                    lines.append(f"- {c}")
                            continue
                        if k_low == "sesion" and isinstance(v, str):
                            lines.append(f"**Sesión:** {v.strip()}")
                            continue

                        heading = title_map.get(k_low)
                        if not heading:
                            heading = re.sub(r"^(\d+)_", r"\1. ", k_str).replace("_", " ").strip().capitalize()
                        lines.append(f"#### {heading}")
                        if isinstance(v, list):
                            for entry in v:
                                c = _clean_item(entry)
                                if c:
                                    lines.append(f"- {c}")
                        elif isinstance(v, dict):
                            for sub_k, sub_v in v.items():
                                c = _clean_item(f"{sub_k}: {sub_v}")
                                if c:
                                    lines.append(f"- {c}")
                        else:
                            c = _clean_item(v)
                            if c:
                                lines.append(c)
                    return "\n".join(lines).strip()

                if isinstance(parsed, list):
                    for entry in parsed:
                        c = _clean_item(entry)
                        if c:
                            lines.append(f"- {c}")
                    return "\n".join(lines).strip()

        return candidate

    @staticmethod
    def _format_markdown_blocks_html(
        raw_text: str,
        text_color: str = "#1e293b",
        heading_color: str = "#065f46",
        bullet_color: str = "#10b981",
        code_bg: str = "#f1f5f9",
        code_fg: str = "#0f172a",
    ) -> str:
        """Converts markdown bullets, bold spans, inline code, and section headers into clean email-safe HTML."""
        esc = html.escape
        txt = NotificationHub._normalize_llm_text_to_markdown(raw_text)
        if not txt:
            return ""

        def _inline_fmt(line_s: str) -> str:
            s = esc(line_s)
            s = re.sub(r"\*\*(.+?)\*\*", r"<strong style='font-weight:700;'>\1</strong>", s)
            s = re.sub(
                r"`([^`]+?)`",
                rf"<code style='background:{code_bg};color:{code_fg};padding:1px 6px;border-radius:5px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;'>\1</code>",
                s,
            )
            return s

        blocks: List[str] = []
        bullet_rows: List[str] = []

        def _flush_bullets() -> None:
            if bullet_rows:
                blocks.append(
                    "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' border='0' style='margin:4px 0 10px 0;'>"
                    + "".join(bullet_rows)
                    + "</table>"
                )
                bullet_rows.clear()

        for raw_line in txt.splitlines():
            line = raw_line.strip()
            if not line:
                _flush_bullets()
                continue
            if re.match(r"^(#{1,4})\s+", line):
                _flush_bullets()
                hd = re.sub(r"^#{1,4}\s+", "", line)
                blocks.append(
                    f"<div style='margin:12px 0 5px 0;font-size:14px;font-weight:800;color:{heading_color};'>{_inline_fmt(hd)}</div>"
                )
                continue
            bullet_m = re.match(r"^(?:[-*•]+|\d+\.)\s+(.*)$", line)
            if bullet_m:
                content = bullet_m.group(1).strip()
                is_numbered = bool(re.match(r"^\d+\.", line))
                if is_numbered and len(content) < 75 and ":" not in content and not content.endswith("."):
                    _flush_bullets()
                    blocks.append(
                        f"<div style='margin:12px 0 5px 0;font-size:13.5px;font-weight:800;color:{heading_color};'>{_inline_fmt(line)}</div>"
                    )
                else:
                    bullet_rows.append(
                        "<tr>"
                        f"<td valign='top' style='width:18px;padding:4px 6px 4px 0;color:{bullet_color};font-weight:800;font-size:14px;line-height:1.5;'>•</td>"
                        f"<td style='padding:4px 0;color:{text_color};font-size:13.5px;line-height:1.6;'>{_inline_fmt(content)}</td>"
                        "</tr>"
                    )
                continue

            _flush_bullets()
            blocks.append(
                f"<p style='margin:0 0 8px 0;color:{text_color};font-size:13.5px;line-height:1.62;'>{_inline_fmt(line)}</p>"
            )

        _flush_bullets()
        return "".join(blocks)

    @staticmethod
    def _build_bulletin_html(bulletin: Dict[str, Any], league_name: str) -> str:
        """Builds the 'Boletín de la Liga' newsletter card for email HTML."""
        if not isinstance(bulletin, dict) or not any(bulletin.values()):
            return ""
        esc = html.escape
        sections: List[str] = []

        # 1. Standings & Last Round Pulse
        st_top = bulletin.get("standings_top") or []
        ms = bulletin.get("my_standings") or {}
        rnd = bulletin.get("latest_round") or {}
        if st_top or ms or rnd:
            podium_medals = {0: "🥇", 1: "🥈", 2: "🥉"}
            chips = "".join(
                f"<span style='display:inline-block;background:{'#ecfdf5' if r.get('is_me') else '#f8fafc'};"
                f"border:1px solid {'#10b981' if r.get('is_me') else '#cbd5e1'};color:#0f172a;"
                f"padding:4px 9px;border-radius:8px;font-size:11.5px;margin:0 5px 5px 0;'>"
                f"{podium_medals.get(idx, '#' + str(r.get('rank')))} <b>{esc(str(r.get('name')))}</b> · {int(r.get('points', 0))} pts</span>"
                for idx, r in enumerate(st_top[:3])
            )
            my_line = ""
            if ms:
                nxt = f" · Próximo rival: <b>{esc(str(ms.get('next_rival_above')))}</b> (-{int(ms.get('points_to_next', 0))} pts)" if ms.get("next_rival_above") else ""
                my_line = (
                    f"<div style='font-size:12px;color:#334155;margin-top:3px;'>"
                    f"Tu posición: <b>#{int(ms.get('rank', 0))}/{int(ms.get('total_managers', 0))}</b> ({int(ms.get('points', 0))} pts"
                    f" · a <b>-{int(ms.get('points_to_leader', 0))} pts</b> del líder {esc(str(ms.get('leader_name', '')))}){nxt}</div>"
                )
            rnd_line = ""
            if rnd and rnd.get("round_name"):
                my_rnd_part = f" · Tú: <b>{int(rnd.get('my_points', 0))} pts</b>" if rnd.get("my_points") else ""
                rnd_line = (
                    f"<div style='font-size:11.5px;color:#475569;margin-top:3px;'>"
                    f"🏅 <b>{esc(str(rnd.get('round_name')))}:</b> MVP <b>{esc(str(rnd.get('top_scorer_name', '')))}</b> "
                    f"({int(rnd.get('top_points', 0))} pts · +{int(rnd.get('top_bonus', 0))/1e6:.2f}M bonus){my_rnd_part}</div>"
                )
            sections.append(
                "<div style='margin-bottom:11px;'>"
                "<div style='font-size:11.5px;font-weight:800;color:#4c1d95;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:5px;'>🏁 Pulso de la Clasificación</div>"
                f"<div>{chips}</div>{my_line}{rnd_line}"
                "</div>"
            )

        # 2. Recent Release Clauses in the League
        clauses = bulletin.get("clauses") or []
        if clauses:
            cl_rows = "".join(
                f"<tr style='background:{'#fff1f2' if c.get('involves_me') else 'transparent'};'>"
                f"<td style='padding:6px;border-bottom:1px solid #f1f5f9;'>"
                f"<span style='background:{'#ffe4e6' if c.get('involves_me') else '#fef3c7'};color:{'#be123c' if c.get('involves_me') else '#92400e'};padding:1px 6px;border-radius:999px;font-size:10px;font-weight:800;'>"
                f"{'TE AFECTA' if c.get('involves_me') else 'CLAUSULAZO'}</span> "
                f"<b>{esc(str(c.get('nombre')))}</b>"
                f" <span style='color:#64748b;font-size:11.5px;'>({esc(str(c.get('pos', '')))} · {esc(str(c.get('from_name', '')))} → <b>{esc(str(c.get('to_name', '')))}</b>)</span></td>"
                f"<td align='right' style='padding:6px;border-bottom:1px solid #f1f5f9;font-weight:800;color:#be123c;white-space:nowrap;'>{int(c.get('amount', 0)):,} €</td>"
                f"</tr>"
                for c in clauses[:5]
            )
            sections.append(
                "<div style='margin-bottom:11px;'>"
                "<div style='font-size:11.5px;font-weight:800;color:#be123c;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;'>💥 Clausulazos Recientes</div>"
                f"<table role='presentation' width='100%' cellspacing='0' cellpadding='0' border='0' style='font-size:12.5px;'>{cl_rows}</table>"
                "</div>"
            )

        # 3. Rival Deals, Competitive Auctions, Defensive Shields & Position Updates
        deals = bulletin.get("manager_deals") or []
        shields = bulletin.get("clause_shields") or []
        pos_chg = bulletin.get("position_changes") or []
        if deals or shields or pos_chg:
            deal_items: List[str] = []
            for d in deals[:5]:
                dtp = str(d.get("deal_type") or "")
                badge = "🤝 TRASPASO" if dtp == "MANAGER_TRADE" else ("💸 VENTA" if dtp == "RIVAL_SALE" else "🏷️ SUBASTA")
                b_cnt = int(d.get("bid_count", 1) or 1)
                extra = f" · superó {b_cnt - 1} puja{'s' if b_cnt > 2 else ''}" if b_cnt > 1 else ""
                deal_items.append(
                    f"<tr>"
                    f"<td style='padding:5px 6px;border-bottom:1px dashed #e2e8f0;font-size:12px;'>"
                    f"<span style='color:#4f46e5;font-weight:800;font-size:10.5px;'>{badge}</span> "
                    f"<b>{esc(str(d.get('to_name')))}</b> · {esc(str(d.get('nombre')))} "
                    f"<span style='color:#64748b;'>(desde {esc(str(d.get('from_name')))}{extra})</span></td>"
                    f"<td align='right' style='padding:5px 6px;border-bottom:1px dashed #e2e8f0;font-weight:700;color:#0f172a;font-size:12px;white-space:nowrap;'>{int(d.get('amount', 0)):,} €</td>"
                    f"</tr>"
                )
            for sh in shields[:2]:
                deal_items.append(
                    f"<tr>"
                    f"<td style='padding:5px 6px;border-bottom:1px dashed #e2e8f0;font-size:12px;'>"
                    f"<span style='color:#0284c7;font-weight:800;font-size:10.5px;'>🛡️ BLINDAJE</span> "
                    f"<b>{esc(str(sh.get('user_name')))}</b> subió cláusula de {esc(str(sh.get('nombre')))}</td>"
                    f"<td align='right' style='padding:5px 6px;border-bottom:1px dashed #e2e8f0;font-weight:700;color:#0284c7;font-size:12px;white-space:nowrap;'>→ {int(sh.get('release_clause', 0)):,} €</td>"
                    f"</tr>"
                )
            pos_note = ""
            if pos_chg:
                pos_txt = ", ".join(f"{esc(str(p.get('nombre')))} ({esc(str(p.get('from_pos')))}→{esc(str(p.get('to_pos')))})" for p in pos_chg[:4])
                pos_note = f"<div style='font-size:11.5px;color:#475569;margin-top:5px;'>🔁 <b>Cambios oficiales de posición:</b> {pos_txt}</div>"
            sections.append(
                "<div style='margin-bottom:11px;'>"
                "<div style='font-size:11.5px;font-weight:800;color:#4338ca;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;'>🔄 Fichajes, Subastas y Blindajes</div>"
                f"<table role='presentation' width='100%' cellspacing='0' cellpadding='0' border='0'>{''.join(deal_items)}</table>"
                f"{pos_note}</div>"
            )

        # 4. LaLiga Medical Radar & Top 24h Movers
        inj = bulletin.get("injury_alerts") or []
        risers = bulletin.get("market_risers") or []
        fallers = bulletin.get("market_fallers") or []
        if inj or risers or fallers:
            st_lbl = {"injured": "Lesionado", "suspended": "Sancionado", "sanctioned": "Sancionado", "doubtful": "Duda"}
            inj_chips = "".join(
                f"<span style='display:inline-block;background:#fef2f2;color:#991b1b;border:1px solid #fecaca;"
                f"padding:3px 7px;border-radius:7px;font-size:11px;margin:0 4px 4px 0;'>"
                f"🏥 <b>{esc(str(x.get('nombre')))}</b> ({esc(str(x.get('team')))}) · {esc(st_lbl.get(str(x.get('status')), str(x.get('status'))))}</span>"
                for x in inj[:4]
            )
            risers_txt = ", ".join(f"<b>{esc(str(m.get('nombre')))}</b> (+{int(m.get('subida_24h', 0))//1000}k)" for m in risers[:3]) or "—"
            fallers_txt = ", ".join(f"<b>{esc(str(m.get('nombre')))}</b> ({int(m.get('subida_24h', 0))//1000}k)" for m in fallers[:3]) or "—"
            inj_block = f"<div style='margin-bottom:5px;'>{inj_chips}</div>" if inj_chips else ""
            sections.append(
                "<div>"
                "<div style='font-size:11.5px;font-weight:800;color:#0f766e;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;'>📡 Radar LaLiga — Parte Médico y Mercado 24h</div>"
                f"{inj_block}"
                "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' border='0' style='font-size:11.5px;'>"
                f"<tr><td width='50%' style='padding:4px 6px;background:#ecfdf5;border-radius:8px 0 0 8px;color:#065f46;'>📈 <b>Suben:</b> {risers_txt}</td>"
                f"<td width='50%' style='padding:4px 6px;background:#fff1f2;border-radius:0 8px 8px 0;color:#9f1239;'>📉 <b>Bajan:</b> {fallers_txt}</td></tr>"
                "</table>"
                "</div>"
            )

        if not sections:
            return ""
        return f"""
        <div class="fc-card" style="background:#ffffff;border:1px solid #ddd6fe;border-left:4px solid #7c3aed;border-radius:14px;padding:16px 18px;margin-top:14px;box-shadow:0 2px 10px rgba(124,58,237,0.06);">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border-bottom:1px solid #f3e8ff;padding-bottom:8px;margin-bottom:10px;">
            <tr>
              <td style="font-size:15px;font-weight:800;color:#4c1d95;">📰 Boletín de la Liga — Últimas Noticias</td>
              <td align="right"><span style="background:#f5f3ff;color:#6d28d9;border:1px solid #ddd6fe;padding:2px 8px;border-radius:999px;font-size:10px;font-weight:800;">{esc(league_name.upper())}</span></td>
            </tr>
          </table>
          {''.join(sections)}
        </div>
        """

    @staticmethod
    def _build_email_html(
        summary: Dict[str, Any],
        pending: List[Dict[str, Any]],
        team_name: str,
        league_name: str,
        trigger_label: str,
        phase_name: str,
        inline_cids: Optional[Set[str]] = None,
    ) -> str:
        esc = html.escape
        cids = inline_cids or set()
        plan = summary.get("plan") or {}
        lu = plan.get("lineup") or {}
        phase_ctx = summary.get("phase_context") or {}
        scarcity = plan.get("scarcity_summary") or {}
        posture_info = summary.get("strategy_posture") or {}
        sup_rep = summary.get("supervisor_report") or {}

        mode_str = str(summary.get("mode", "FULL_AUTO")).upper()
        is_full_auto = (mode_str == "FULL_AUTO")
        is_solvent = bool(summary.get("is_solvent", True))
        j_num = summary.get("target_jornada_num", "?")
        formation = str(lu.get("formation", "4-4-2"))
        expected_xp = float(lu.get("expected_points", 0.0) or 0.0)
        start_cash = int(summary.get("starting_cash", 0) or 0)
        proj_cash = int(summary.get("projected_cash", 0) or 0)
        proj_pend_cash = int(summary.get("projected_cash_with_pending_sales", proj_cash) or proj_cash)
        eff_max_bid = int(summary.get("available_max_bid_effective", 0) or 0)
        cap_eur = int(summary.get("autonomous_clause_cap_eur", 0) or StrategySettings.AUTONOMOUS_CLAUSE_HARD_CAP_EUR)
        archetype_id = StrategySettings.ACTIVE_ARCHETYPE

        posture_name = str(posture_info.get("posture") or "BALANCED")
        posture_palette = {
            "PROTECT_LEAD": ("#ecfdf5", "#065f46", "#10b981"),
            "TACTICAL_PRESS": ("#eff6ff", "#1e40af", "#3b82f6"),
            "ASYMMETRIC_CHASER": ("#fff7ed", "#9a3412", "#f97316"),
        }
        p_bg, p_fg, p_bd = posture_palette.get(posture_name, ("#eef2ff", "#3730a3", "#6366f1"))
        rank_badge = (
            f"#{posture_info.get('my_rank', '?')}/{posture_info.get('total_managers', '?')} · "
            f"{posture_info.get('gap_to_leader', 0):+.0f} pts"
            if posture_info.get("my_rank")
            else "Calibración inicial"
        )

        # Hidden inbox preheader text for Gmail / Apple Mail list preview
        preheader = (
            f"J{j_num} ({phase_name}) · Caja {start_cash:,}€ → {proj_cash:,}€ · "
            f"Once {formation} ({expected_xp:.1f} xP) · Postura {posture_name}"
        )

        # Header block: Animated GIF hero if available, plus sleek metadata sub-rail
        hero_media_html = ""
        if "hero_banner" in cids:
            hero_media_html = (
                "<img src='cid:hero_banner' width='680' alt='Fantasy Champion Autopilot' "
                "style='width:100%;max-width:680px;height:auto;display:block;border-radius:16px 16px 0 0;border:0;'/>"
            )
        else:
            hero_media_html = f"""
            <div style="background:linear-gradient(135deg,#07111e 0%,#0b2930 60%,#064e3b 100%);padding:22px 24px;border-radius:16px 16px 0 0;color:#f8fafc;">
              <div style="font-size:11px;font-weight:800;letter-spacing:0.08em;color:#34d399;text-transform:uppercase;">FANTASY CHAMPION AUTOPILOT · {esc(mode_str)}</div>
              <div style="font-size:24px;font-weight:800;margin:4px 0 2px 0;color:#ffffff;">🏆 {esc(team_name)}</div>
              <div style="font-size:13px;color:#cbd5e1;">{esc(league_name)} · Jornada Objetivo <b>J{esc(str(j_num))}</b> · {esc(str(summary.get('date', '')))}</div>
            </div>
            """

        hero_card_html = f"""
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#080f1d;border-radius:16px;overflow:hidden;box-shadow:0 12px 30px rgba(8,15,29,0.22);">
          <tr><td style="padding:0;">{hero_media_html}</td></tr>
          <tr>
            <td style="background:#0c172a;padding:10px 18px;border-top:1px solid #1e293b;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                <tr>
                  <td style="font-size:11.5px;color:#94a3b8;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">
                    Arquetipo: <strong style="color:#f5b942;">{esc(archetype_id)}</strong>
                  </td>
                  <td align="center" style="font-size:11.5px;color:#94a3b8;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">
                    Fase: <strong style="color:#38bdf8;">{esc(trigger_label)}</strong>
                  </td>
                  <td align="right" style="font-size:11.5px;color:#94a3b8;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">
                    Techo Auto: <strong style="color:#34d399;">{cap_eur/1e6:.1f}M€</strong>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
        """

        # 3-Column KPI Strip (email-safe table)
        solvency_pill = (
            "<span style='display:inline-block;background:#dcfce7;color:#166534;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700;'>✅ 100% Solvente</span>"
            if is_solvent
            else "<span style='display:inline-block;background:#fee2e2;color:#991b1b;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700;'>⚠️ Revisar Liquidez</span>"
        )
        clauses_badge = (
            "<span style='color:#059669;font-weight:700;'>🟢 Cláusulas Abiertas</span>"
            if plan.get("clauses_open", True)
            else "<span style='color:#d97706;font-weight:700;'>🔒 Cerradas (48h/J)</span>"
        )
        kickoff_short = esc(str(phase_ctx.get("next_kickoff_ts", "N/D")))[:16]
        kpi_strip_html = f"""
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin-top:14px;">
          <tr>
            <td class="kpi-col" width="32%" valign="top" style="background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;padding:13px 14px;">
              <div style="font-size:10.5px;font-weight:700;letter-spacing:0.06em;color:#64748b;text-transform:uppercase;">💰 Tesorería</div>
              <div style="font-size:16px;font-weight:800;color:#0f172a;margin:5px 0 3px 0;">{start_cash:,} €</div>
              <div style="font-size:12px;color:#475569;margin-bottom:3px;">Proy: <b>{proj_cash:,} €</b>{f' <span style="color:#0284c7;font-size:11px;">({proj_pend_cash:,} € tras OK)</span>' if proj_pend_cash > proj_cash + 10_000 else ''}</div>
              {f'<div style="font-size:11px;color:#475569;margin-bottom:5px;">Puja máx: <b>{eff_max_bid:,} €</b></div>' if eff_max_bid > 0 else ''}
              {solvency_pill}
            </td>
            <td width="2%" style="font-size:0;">&nbsp;</td>
            <td class="kpi-col" width="32%" valign="top" style="background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;padding:13px 14px;">
              <div style="font-size:10.5px;font-weight:700;letter-spacing:0.06em;color:#64748b;text-transform:uppercase;">🧭 Postura Causal</div>
              <div style="margin:6px 0 5px 0;">
                <span style="display:inline-block;background:{p_bg};color:{p_fg};border:1px solid {p_bd};padding:3px 9px;border-radius:999px;font-size:11px;font-weight:800;">{esc(posture_name)}</span>
              </div>
              <div style="font-size:12px;color:#334155;"><b>{esc(rank_badge)}</b></div>
            </td>
            <td width="2%" style="font-size:0;">&nbsp;</td>
            <td class="kpi-col" width="32%" valign="top" style="background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;padding:13px 14px;">
              <div style="font-size:10.5px;font-weight:700;letter-spacing:0.06em;color:#64748b;text-transform:uppercase;">⏱️ Ventana &amp; Mercado</div>
              <div style="font-size:12.5px;margin:5px 0 3px 0;">{clauses_badge}</div>
              <div style="font-size:11.5px;color:#475569;">Kickoff: <b>{kickoff_short}</b></div>
              <div style="font-size:11.5px;color:#475569;margin-top:2px;">Libres ≥5M: <b>{scarcity.get('free_ge_5m', 0)}</b> · ≥10M: <b>{scarcity.get('free_ge_10m', 0)}</b></div>
            </td>
          </tr>
        </table>
        """

        # Conditional Alerts & Supervisor Card
        alerts_blocks: List[str] = []
        if pending:
            p_rows = "".join(
                f"<tr>"
                f"<td style='padding:9px 8px;border-bottom:1px solid #fecaca;'><code style='background:#fee2e2;color:#991b1b;padding:2px 6px;border-radius:5px;font-weight:700;'>{esc(p['decision_id'])}</code></td>"
                f"<td style='padding:9px 8px;border-bottom:1px solid #fecaca;font-size:12px;'>{esc(p['action_type'])}</td>"
                f"<td style='padding:9px 8px;border-bottom:1px solid #fecaca;'><b>{esc(p['nombre'])}</b></td>"
                f"<td style='padding:9px 8px;border-bottom:1px solid #fecaca;font-weight:700;'>{int(p['amount_eur']):,} €</td>"
                f"<td style='padding:9px 8px;border-bottom:1px solid #fecaca;font-size:12px;color:#475569;'>{esc(str(p.get('rationale', '')))}<br/>"
                f"<span style='color:#991b1b;font-weight:600;'>👉 Telegram: <code>/aprobar {esc(p['decision_id'])}</code> · Sheets: <code>APROBAR</code></span></td>"
                f"</tr>"
                for p in pending
            )
            alerts_blocks.append(f"""
            <div style="background:#fff1f2;border:1px solid #fda4af;border-left:4px solid #e11d48;border-radius:12px;padding:15px 16px;margin-top:14px;">
              <div style="font-size:14px;font-weight:800;color:#9f1239;margin-bottom:8px;">🔔 Decisiones Pendientes de Tu Confirmación ({len(pending)})</div>
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="font-size:12.5px;">
                <tr style="background:#ffe4e6;color:#881337;text-align:left;">
                  <th style="padding:7px 8px;">ID</th><th style="padding:7px 8px;">Tipo</th>
                  <th style="padding:7px 8px;">Jugador</th><th style="padding:7px 8px;">Importe</th><th style="padding:7px 8px;">Tesis &amp; Acción</th>
                </tr>
                {p_rows}
              </table>
            </div>
            """)

        hv_alerts = summary.get("high_value_clause_alerts") or []
        if hv_alerts:
            hv_li = "".join(
                f"<li style='margin:4px 0;'><b>{esc(a['nombre'])}</b> — <b>{int(a['amount']):,} €</b> a {esc(a.get('seller', ''))} "
                f"<span style='color:#047857;font-weight:700;'>(+{a.get('delta_xp_xi', 0):.2f} xP)</span></li>"
                for a in hv_alerts
            )
            alerts_blocks.append(f"""
            <div style="background:#fffbeb;border:1px solid #fcd34d;border-left:4px solid #d97706;border-radius:12px;padding:13px 16px;margin-top:14px;font-size:13px;color:#78350f;">
              <div style="font-weight:800;margin-bottom:4px;">🚨 Clausulazo Autónomo de Alto Importe (&gt;6M €, dentro del tope FULL_AUTO)</div>
              <ul style="margin:4px 0 0 0;padding-left:18px;">{hv_li}</ul>
            </div>
            """)

        sup_ov = (sup_rep.get("overrides") or {}) if isinstance(sup_rep, dict) else {}
        sup_reason = str(sup_ov.get("strategic_rationale") or "").strip()
        if sup_rep and (sup_rep.get("llm_called") or sup_rep.get("triggered") or sup_reason):
            prio_pos = ", ".join(sup_ov.get("priority_positions") or [])
            display_reason = sup_reason or str(posture_info.get("rationale") or "")
            sup_model = str(sup_rep.get("model_used") or GeneralSettings.LLM_MODEL_DAILY)
            sup_status = "Supervisor Estratégico Activado" if (sup_rep.get("llm_called") or sup_rep.get("triggered")) else "Directriz Estratégica Vigente"
            alerts_blocks.append(f"""
            <div style="background:#eef2ff;border:1px solid #c7d2fe;border-left:4px solid #4f46e5;border-radius:12px;padding:12px 16px;margin-top:14px;font-size:12.5px;color:#312e81;">
              <div style="font-weight:800;margin-bottom:3px;">
                🧠 {esc(sup_status)} · <code>{esc(str(sup_rep.get('trigger', 'AUTO')))}</code>
                <span style="font-weight:600;color:#4338ca;">({esc(sup_model)}{' · Prioridad: ' + esc(prio_pos) if prio_pos else ''})</span>
              </div>
              <div style="color:#3730a3;line-height:1.5;">{esc(display_reason)}</div>
            </div>
            """)

        alerts_html = "".join(alerts_blocks)

        # Once Titular Section (Animated Pitch GIF + Clean Color-Coded Pills)
        line_pill_styles = {
            "GK": ("#fffbeb", "#92400e", "#fcd34d"),
            "DF": ("#f0f9ff", "#075985", "#bae6fd"),
            "MF": ("#ecfdf5", "#065f46", "#a7f3d0"),
            "FW": ("#fff1f2", "#9f1239", "#fecdd3"),
        }
        xi_pills = "".join(
            f"<span style='display:inline-block;background:{line_pill_styles.get(s.get('linea','MF'), ('#ecfdf5','#065f46','#a7f3d0'))[0]};"
            f"color:{line_pill_styles.get(s.get('linea','MF'), ('#ecfdf5','#065f46','#a7f3d0'))[1]};"
            f"border:1px solid {line_pill_styles.get(s.get('linea','MF'), ('#ecfdf5','#065f46','#a7f3d0'))[2]};"
            f"padding:4px 9px;margin:3px;border-radius:999px;font-size:12px;font-weight:600;'>"
            f"<b>{esc(str(s.get('linea', 'MF')))}</b> · {esc(s['nombre'])} <span style='opacity:0.85;'>({s['xp']:.1f} xP)</span></span>"
            for s in (lu.get("slots") or [])
        ) or "<span style='font-size:12.5px;color:#64748b;'>Alineación sin slots detallados en esta vista.</span>"

        pitch_img_html = (
            "<div style='margin:10px 0 12px 0;border-radius:12px;overflow:hidden;'>"
            "<img src='cid:tactical_pitch' width='648' alt='Campo Táctico MILP' style='width:100%;max-width:648px;height:auto;display:block;border-radius:12px;border:0;'/>"
            "</div>"
            if "tactical_pitch" in cids
            else ""
        )

        lineup_card_html = f"""
        <div class="fc-card" style="background:#ffffff;border:1px solid #e2e8f0;border-radius:14px;padding:16px 18px;margin-top:14px;box-shadow:0 2px 10px rgba(15,23,42,0.04);">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border-bottom:1px solid #f1f5f9;padding-bottom:8px;margin-bottom:8px;">
            <tr>
              <td style="font-size:15px;font-weight:800;color:#065f46;">⚽ Once Titular Óptimo MILP · {esc(formation)}</td>
              <td align="right">
                <span style="background:#ecfdf5;color:#047857;border:1px solid #a7f3d0;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:800;">{expected_xp:.1f} xP proyectados</span>
              </td>
            </tr>
          </table>
          {pitch_img_html}
          <div style="margin-top:4px;">{xi_pills}</div>
        </div>
        """

        # Unified Daily Operations Card (Executions + Market Bids + Preventive Showcase)
        is_dry_run = (mode_str == "DRY_RUN")
        dry_tag = " <span style='color:#d97706;font-size:11px;font-weight:700;'>[Simulacro]</span>" if is_dry_run else ""
        exec_ops_items: List[str] = []
        for s in (plan.get("all_sales") or []):
            if is_full_auto or is_dry_run or not s.get("requires_human_approval"):
                buyer_badge = ""
                if s.get("is_rival_buyer") and s.get("buyer_name"):
                    prem_s = (
                        f" (+{int(s['rival_premium_eur']):,} € vs Máquina)"
                        if int(s.get("rival_premium_eur", 0) or 0) > 0
                        else ""
                    )
                    buyer_badge = (
                        f" <span style='background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;"
                        f"padding:1px 6px;border-radius:999px;font-size:11px;font-weight:700;'>"
                        f"→ {esc(str(s['buyer_name']))}{esc(prem_s)}</span>"
                    )
                exec_ops_items.append(
                    f"<tr><td style='padding:6px 0;border-bottom:1px dashed #e2e8f0;font-size:13px;color:#1e293b;'>"
                    f"💸 <b>Venta:</b> {esc(s['nombre'])}{buyer_badge} <span style='color:#64748b;'>({esc(s['reason'])})</span>{dry_tag}</td>"
                    f"<td align='right' style='padding:6px 0;border-bottom:1px dashed #e2e8f0;font-size:13px;font-weight:800;color:#059669;'>+{int(s['sale_price']):,} €</td></tr>"
                )
        clause_exec_map: Dict[str, str] = {}
        for _ex in (summary.get("executions") or []):
            _tp = str(_ex.get("type") or "")
            _tg = str(_ex.get("target") or "")
            if _tp in ("CLAUSE_DEFERRED", "CLAUSE_SNIPE") and _tg:
                clause_exec_map[_tg] = str((_ex.get("res") or {}).get("status") or "")
        if is_full_auto or is_dry_run:
            for c in (plan.get("clause_snipes") or []):
                _cst = clause_exec_map.get(str(c.get("nombre") or ""), "")
                _is_def = (_cst == "DEFERRED_TO_PRE_LOCK")
                _tag = " <span style='background:#fef3c7;color:#92400e;border:1px solid #f59e0b;padding:1px 6px;border-radius:999px;font-size:10px;font-weight:800;'>DIFERIDA PRE-CIERRE</span>" if _is_def else dry_tag
                _amt_col = f"<span style='color:#92400e;'>{int(c['amount']):,} € (en espera)</span>" if _is_def else f"-{int(c['amount']):,} €"
                exec_ops_items.append(
                    f"<tr><td style='padding:6px 0;border-bottom:1px dashed #e2e8f0;font-size:13px;color:#1e293b;'>"
                    f"⚡ <b>Cláusula:</b> {esc(c['nombre'])} a {esc(str(c.get('seller', '')))}{_tag} "
                    f"<span style='color:#0284c7;font-weight:700;'>(+{c['delta_xp_xi']:.2f} xP)</span></td>"
                    f"<td align='right' style='padding:6px 0;border-bottom:1px dashed #e2e8f0;font-size:13px;font-weight:800;color:#dc2626;'>{_amt_col}</td></tr>"
                )
            for sh in (plan.get("defensive_shields") or []):
                exec_ops_items.append(
                    f"<tr><td style='padding:6px 0;border-bottom:1px dashed #e2e8f0;font-size:13px;color:#1e293b;'>"
                    f"🛡️ <b>Blindaje:</b> {esc(sh['nombre'])}{dry_tag}</td>"
                    f"<td align='right' style='padding:6px 0;border-bottom:1px dashed #e2e8f0;font-size:13px;font-weight:800;color:#4f46e5;'>+{int(sh['recommended_cash_spend']):,} €</td></tr>"
                )

        ops_ledger_html = (
            f"<table role='presentation' width='100%' cellspacing='0' cellpadding='0' border='0' style='margin-bottom:12px;'>{''.join(exec_ops_items)}</table>"
            if exec_ops_items
            else "<div style='font-size:12.5px;color:#64748b;background:#f8fafc;padding:8px 12px;border-radius:8px;margin-bottom:12px;'>Sin ventas ni clausulazos ejecutados hoy — preservando liquidez y titulares.</div>"
        )

        def _bid_row(b: Dict[str, Any]) -> str:
            p_win = max(0, min(100, int(round(float(b.get("win_prob_est", 0.5) or 0.5) * 100))))
            bar_col = "#10b981" if p_win >= 65 else ("#f59e0b" if p_win >= 40 else "#ef4444")
            seller_b = str(b.get("seller", "Mercado") or "Mercado").strip()
            seller_pill = (
                f" <span style='color:#2563eb;font-size:11px;font-weight:600;'>→ {esc(seller_b)}</span>"
                if seller_b not in ("Mercado", "Libre", "None", "")
                else ""
            )
            return (
                f"<tr>"
                f"<td style='padding:8px 6px;border-bottom:1px solid #f1f5f9;'><b>{esc(b['nombre'])}</b> <span style='color:#64748b;font-size:11.5px;'>({esc(str(b.get('position', '')))})</span>{seller_pill}</td>"
                f"<td align='right' style='padding:8px 6px;border-bottom:1px solid #f1f5f9;color:#64748b;font-size:12.5px;'>{int(b['market_price']):,} €</td>"
                f"<td align='right' style='padding:8px 6px;border-bottom:1px solid #f1f5f9;font-weight:800;color:#0f172a;'>{int(b['amount']):,} €</td>"
                f"<td style='padding:8px 6px;border-bottom:1px solid #f1f5f9;'>"
                f"<span style='background:#ecfdf5;color:#047857;padding:2px 7px;border-radius:999px;font-size:11.5px;font-weight:700;'>+{b['delta_xp_xi']:.2f} xP</span> "
                f"<span style='font-size:11px;color:#64748b;'>{esc(str(b.get('regime', '')))}</span></td>"
                f"<td align='right' style='padding:8px 6px;border-bottom:1px solid #f1f5f9;width:82px;'>"
                f"<div style='font-size:11px;font-weight:700;color:#334155;'>{p_win}%</div>"
                f"<div style='background:#e2e8f0;height:5px;border-radius:4px;overflow:hidden;margin-top:2px;'>"
                f"<div style='background:{bar_col};width:{p_win}%;height:5px;border-radius:4px;'></div></div>"
                f"</td></tr>"
            )

        bids_rows = "".join(_bid_row(b) for b in (plan.get("market_bids") or [])) or (
            "<tr><td colspan='5' style='padding:10px 6px;color:#64748b;font-size:12.5px;'>Ninguna puja supera hoy el umbral rentabilidad/riesgo.</td></tr>"
        )

        listings_chips = "".join(
            f"<span style='display:inline-block;background:#f8fafc;border:1px solid #cbd5e1;color:#334155;"
            f"padding:4px 9px;margin:3px;border-radius:8px;font-size:12px;'>"
            f"<b>{esc(p['nombre'])}</b> · {int(p['asking_price']):,} € <span style='color:#64748b;'>({esc(str(p.get('listing_reason', '')))})</span></span>"
            for p in (plan.get("preventive_listings") or [])
        ) or "<span style='font-size:12.5px;color:#64748b;'>Ninguno</span>"

        operations_card_html = f"""
        <div class="fc-card" style="background:#ffffff;border:1px solid #e2e8f0;border-radius:14px;padding:16px 18px;margin-top:14px;box-shadow:0 2px 10px rgba(15,23,42,0.04);">
          <div style="font-size:15px;font-weight:800;color:#0f172a;border-bottom:1px solid #f1f5f9;padding-bottom:8px;margin-bottom:12px;">
            📊 Operaciones y Mercado del Día
          </div>
          {ops_ledger_html}
          <div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:0.05em;margin:8px 0 6px 0;">🎯 Pujas Autónomas en Subasta</div>
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="font-size:12.5px;margin-bottom:14px;">
            <tr style="background:#f8fafc;color:#475569;text-align:left;">
              <th style="padding:7px 6px;font-size:11px;">Jugador</th>
              <th align="right" style="padding:7px 6px;font-size:11px;">VM</th>
              <th align="right" style="padding:7px 6px;font-size:11px;">Puja</th>
              <th style="padding:7px 6px;font-size:11px;">Impacto</th>
              <th align="right" style="padding:7px 6px;font-size:11px;">Éxito</th>
            </tr>
            {bids_rows}
          </table>
          <div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:0.05em;margin:6px 0 4px 0;">
            🏷️ Escaparate Preventivo Top-{len(plan.get('preventive_listings') or [])} (+5% VM · coste 0 €)
          </div>
          <div>{listings_chips}</div>
        </div>
        """

        # Rival Negotiations Card (Incoming rival bids, counter-offers, open outgoing bids locking credit)
        rn = summary.get("rival_negotiations") or {}
        rn_inc = rn.get("incoming_rival_offers") or []
        rn_co = rn.get("counter_offers") or []
        rn_out = rn.get("outgoing_bids") or []
        rival_card_html = ""
        if rn_inc or rn_co or rn_out:
            rn_sections: List[str] = []
            if rn_inc:
                ro_rows = "".join(
                    f"<tr>"
                    f"<td style='padding:7px 6px;border-bottom:1px solid #f1f5f9;'><b>{esc(str(r.get('nombre')))}</b>"
                    f" <span style='color:#2563eb;font-size:11.5px;font-weight:700;'>← {esc(str(r.get('from_name')))}</span></td>"
                    f"<td align='right' style='padding:7px 6px;border-bottom:1px solid #f1f5f9;font-weight:800;color:#059669;'>{int(r.get('amount', 0)):,} €</td>"
                    f"<td style='padding:7px 6px;border-bottom:1px solid #f1f5f9;font-size:12px;color:#475569;'>"
                    f"{'<b>+' + format(int(r.get('rival_premium_eur', 0)), ',') + ' € vs Máquina</b> · ' if int(r.get('rival_premium_eur', 0) or 0) > 0 else ''}"
                    f"{r.get('over_vm_pct', 0):+.1f}% vs VM · <span style='color:#0f172a;font-weight:600;'>{esc(str(r.get('action_label', '')))}</span></td>"
                    f"</tr>"
                    for r in rn_inc
                )
                rn_sections.append(
                    "<div style='font-size:11.5px;font-weight:800;color:#065f46;text-transform:uppercase;letter-spacing:0.05em;margin:4px 0 4px 0;'>📥 Ofertas de Rivales por Tus Jugadores</div>"
                    f"<table role='presentation' width='100%' cellspacing='0' cellpadding='0' border='0' style='font-size:12.5px;margin-bottom:10px;'>{ro_rows}</table>"
                )
            if rn_co:
                co_rows = "".join(
                    f"<tr>"
                    f"<td style='padding:7px 6px;border-bottom:1px solid #f1f5f9;'><b>{esc(str(c.get('nombre')))}</b>"
                    f" <span style='color:#9a3412;font-size:11.5px;font-weight:700;'>← {esc(str(c.get('from_name')))}</span></td>"
                    f"<td align='right' style='padding:7px 6px;border-bottom:1px solid #f1f5f9;font-weight:800;color:#b45309;'>{int(c.get('amount', 0)):,} €</td>"
                    f"<td style='padding:7px 6px;border-bottom:1px solid #f1f5f9;font-size:12px;color:#475569;'>"
                    f"VM {int(c.get('market_price', 0)):,} € ({c.get('over_vm_pct', 0):+.1f}% · {int(c.get('subida_24h', 0)):+,}€/d)<br/>"
                    f"<span style='font-weight:700;color:#991b1b;'>{esc(str(c.get('action_label') or c.get('verdict_label', '')))}</span></td>"
                    f"</tr>"
                    for c in rn_co
                )
                rn_sections.append(
                    "<div style='font-size:11.5px;font-weight:800;color:#9a3412;text-transform:uppercase;letter-spacing:0.05em;margin:6px 0 4px 0;'>📩 Contraofertas Recibidas</div>"
                    f"<table role='presentation' width='100%' cellspacing='0' cellpadding='0' border='0' style='font-size:12.5px;margin-bottom:10px;'>{co_rows}</table>"
                )
            if rn_out:
                out_rows = "".join(
                    f"<tr>"
                    f"<td style='padding:7px 6px;border-bottom:1px solid #f1f5f9;'><b>{esc(str(o.get('nombre')))}</b>"
                    f" <span style='color:#475569;font-size:11.5px;'>→ {esc(str(o.get('to_name')))}</span></td>"
                    f"<td align='right' style='padding:7px 6px;border-bottom:1px solid #f1f5f9;font-weight:700;color:#0f172a;'>{int(o.get('amount', 0)):,} €</td>"
                    f"<td style='padding:7px 6px;border-bottom:1px solid #f1f5f9;font-size:12px;color:#475569;'>"
                    f"{esc(str(o.get('status_label', '')))} · <code>/cancelar_puja {esc(str(o.get('offer_id', '')))}</code></td>"
                    f"</tr>"
                    for o in rn_out
                )
                locked_eur = int(rn.get("remaining_locked_credit_eur", rn.get("total_locked_credit_eur", 0)) or 0)
                freed_eur = int(rn.get("freed_credit_eur", 0) or 0)
                eff_mb = int(rn.get("effective_available_max_bid", eff_max_bid) or 0)
                credit_banner = (
                    f"🔓 Crédito liberado: +{freed_eur:,} € · Bloqueado restante: {locked_eur:,} €"
                    if freed_eur > 0
                    else f"Crédito bloqueado: {locked_eur:,} €"
                )
                if eff_mb > 0:
                    credit_banner += f" · Puja máx efectiva: {eff_mb:,} €"
                rn_sections.append(
                    f"<div style='font-size:11.5px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:0.05em;margin:6px 0 4px 0;'>"
                    f"📤 Pujas Abiertas a Rivales ({esc(credit_banner)})</div>"
                    f"<table role='presentation' width='100%' cellspacing='0' cellpadding='0' border='0' style='font-size:12.5px;'>{out_rows}</table>"
                )

            rival_card_html = f"""
            <div class="fc-card" style="background:#ffffff;border:1px solid #bfdbfe;border-left:4px solid #2563eb;border-radius:14px;padding:16px 18px;margin-top:14px;box-shadow:0 2px 10px rgba(37,99,235,0.06);">
              <div style="font-size:15px;font-weight:800;color:#1e3a8a;border-bottom:1px solid #eff6ff;padding-bottom:8px;margin-bottom:10px;">
                🤝 Negociaciones y Ofertas con Rivales
              </div>
              {''.join(rn_sections)}
            </div>
            """

        bulletin_card_html = NotificationHub._build_bulletin_html(
            summary.get("league_bulletin") or {},
            league_name=league_name,
        )

        # Executive Briefing Card (Formatted Markdown)
        brief_raw = str(summary.get("briefing") or "").strip()
        brief_fmt = (
            NotificationHub._format_markdown_blocks_html(
                brief_raw,
                text_color="#334155",
                heading_color="#065f46",
                bullet_color="#10b981",
            )
            or "<p style='color:#64748b;font-size:13px;margin:0;'>Sin briefing textual en esta ejecución.</p>"
        )
        briefing_card_html = f"""
        <div class="fc-card" style="background:#ffffff;border:1px solid #e2e8f0;border-radius:14px;padding:16px 18px;margin-top:14px;box-shadow:0 2px 10px rgba(15,23,42,0.04);">
          <div style="font-size:15px;font-weight:800;color:#065f46;border-bottom:1px solid #f1f5f9;padding-bottom:8px;margin-bottom:10px;">
            🧠 Informe del Director Deportivo
          </div>
          {brief_fmt}
        </div>
        """

        # Featured Plain-Language Closing Card at the end
        easy_raw = str(summary.get("easy_summary") or "").strip()
        easy_summary_html = ""
        if easy_raw:
            easy_fmt = NotificationHub._format_markdown_blocks_html(
                easy_raw,
                text_color="#e2e8f0",
                heading_color="#fcd34d",
                bullet_color="#34d399",
                code_bg="#1e293b",
                code_fg="#f8fafc",
            )
            easy_summary_html = f"""
            <div style="background:linear-gradient(135deg,#0b172a 0%,#0f2933 100%);border:1px solid #1e3a5f;border-top:3px solid #f5b942;border-radius:14px;padding:18px 20px;margin-top:16px;color:#f8fafc;box-shadow:0 10px 26px rgba(8,15,29,0.18);">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin-bottom:10px;">
                <tr>
                  <td style="font-size:16px;font-weight:800;color:#ffffff;">💬 En resumen: ¿qué se ha hecho hoy?</td>
                  <td align="right">
                    <span style="background:#132f3a;color:#34d399;border:1px solid #10b981;padding:3px 9px;border-radius:999px;font-size:10.5px;font-weight:700;">LECTURA FÁCIL</span>
                  </td>
                </tr>
              </table>
              {easy_fmt}
            </div>
            """

        return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <style type="text/css">
    @keyframes fcFadeUp {{
      from {{ opacity: 0.92; transform: translateY(3px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    .fc-card {{ animation: fcFadeUp 0.45s ease-out; }}
    @media only screen and (max-width: 620px) {{
      .kpi-col {{ display: block !important; width: 100% !important; margin-bottom: 10px !important; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background:#edf2f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#0f172a;-webkit-font-smoothing:antialiased;">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{esc(preheader)}</div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#edf2f7;padding:18px 10px 30px 10px;">
    <tr>
      <td align="center">
        <table role="presentation" width="680" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:680px;">
          <tr>
            <td>
              {hero_card_html}
              {kpi_strip_html}
              {alerts_html}
              {lineup_card_html}
              {operations_card_html}
              {rival_card_html}
              {bulletin_card_html}
              {briefing_card_html}
              {easy_summary_html}
              <div style="text-align:center;font-size:11.5px;color:#64748b;margin-top:18px;padding:0 12px;">
                Fantasy Champion Autopilot · Estrategia <b>{esc(archetype_id)}</b> · Motor Causal + Gemini 4 Hydrogen<br/>
                ¿Necesitas intervenir? Responde por Telegram con <code>/estado</code>, <code>/once</code> o <code>/tesis</code>.
              </div>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
