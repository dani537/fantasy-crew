"""
Deterministic Schematic Email Builder
======================================
Builds the email body (sections, key figures, action list) directly from the
agents' structured JSON reports, so the manager ALWAYS sees a clear, schematic
picture regardless of what the LLM would write:

  - 👔 MÍSTER: what he diagnosed + what he recommends (needs).
  - 💼 DIRECTOR DEPORTIVO: the exact operations decided (bids, cancels, sales...).
  - ⚡ RESULTADOS: what the API actually did.
  - 📊 MERCADO / SUBasta: what happened (won/lost) and next actions.

The LLM is only used for a short headline/lede; the body is guaranteed.
"""

import os
from datetime import datetime
import pandas as pd


def _eur(value):
    """Compact currency: 4_690_000 -> '4,69M€', 1_600_000 -> '1,60M€', 150_000 -> '150.000€'."""
    try:
        value = float(value or 0)
    except (TypeError, ValueError):
        return "0€"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:,.2f}M€".replace(",", ".")
    return f"{value:,.0f}€".replace(",", ".")


def _pos(pos):
    return {"GK": "POR", "DF": "DEF", "MF": "MED", "FW": "DEL"}.get(pos, pos)


def _ul(items):
    if not items:
        return "<i>Ninguna.</i>"
    body = "".join(f"<li>{item}</li>" for item in items)
    return f"<ul style='margin:6px 0 0 18px;padding:0;'>{body}</ul>"


def _bold(text):
    return f"<b>{text}</b>" if text else text


def _squad_state(df_master, my_team):
    if df_master is None or df_master.empty or "BIWPLAYER_TEAM_NAME" not in df_master.columns:
        return None
    squad = df_master[df_master["BIWPLAYER_TEAM_NAME"] == my_team]
    if squad.empty:
        return None
    counts = {}
    if "PLAYER_POSITION" in squad.columns:
        for pos in ["GK", "DF", "MF", "FW"]:
            counts[pos] = int((squad["PLAYER_POSITION"] == pos).sum())
    return {
        "size": len(squad),
        "counts": counts,
        "missing": [p for p, c in counts.items() if c == 0] if counts else [],
    }


def _coach_section(coach_report):
    briefing = coach_report.get("briefing_direccion_deportiva", {}) if isinstance(coach_report, dict) else {}
    resumen = briefing.get("resumen_plantilla", {}) if isinstance(briefing, dict) else {}
    valoracion = resumen.get("valoracion_general") or coach_report.get("valoracion_general") or ""
    huecos = resumen.get("huecos_titulares_libres")
    needs = briefing.get("necesidades_fichaje") or []
    sales = briefing.get("lista_ventas") or []
    formation = None
    lineup = coach_report.get("alineacion_propuesta") if isinstance(coach_report, dict) else None
    if isinstance(lineup, dict):
        formation = lineup.get("formacion") or lineup.get("formacion")

    lines = []

    # Juicio del míster: esquema breve + pequeña explicación (NO estilo noticia)
    schema = []
    if formation:
        schema.append(f"Once: <b>{formation}</b>")
    if huecos is not None:
        schema.append(f"Huecos en el once: <b>{int(huecos)}</b>")
    if not formation and not needs:
        schema.append("<b>Sin once legal</b>")
    if schema:
        lines.append(f"<p style='margin:0 0 8px 0;'>🎯 <b>Juicio del míster:</b> " + " · ".join(schema) + "</p>")
    if valoracion:
        lines.append(f"<p style='margin:0 0 8px 0;'>💬 {valoracion}</p>")

    if needs:
        items = []
        for n in needs:
            pos = _pos(n.get("posicion_requerida"))
            prio = n.get("prioridad", "").title()
            items.append(f"Fichar <b>{pos}</b> · prioridad {prio}")
        lines.append("<div style='margin:4px 0 6px 0;'><b>Necesidades para el mercado:</b>" + _ul(items) + "</div>")

    if sales:
        names = ", ".join(str(s.get("nombre", s.get("id_jugador", "?"))) for s in sales)
        lines.append(f"<p style='margin:6px 0 0 0;'>Sugiere vender: <b>{names}</b> (reserva de liquidez)</p>")

    if not lines:
        lines.append("<p style='margin:0;'>Sin diagnóstico disponible.</p>")
    return {"title": "👔 EL MÍSTER", "body_html": "".join(lines)}


def _sd_section(sd_decisions, projected_balance=None, coach_report=None):
    if not isinstance(sd_decisions, dict):
        return {"title": "💼 DIRECTOR DEPORTIVO", "body_html": "<i>Sin decisiones.</i>"}

    buys = sd_decisions.get("operaciones_compra") or []
    cancels = sd_decisions.get("operaciones_cancelar_pujas") or []
    sales = sd_decisions.get("operaciones_venta") or []
    removals = sd_decisions.get("operaciones_retirar_mercado") or []

    # Mapa id_necesidad -> posicion/prioridad, para explicar el PORQUÉ de cada puja
    need_map = {}
    if isinstance(coach_report, dict):
        briefing = coach_report.get("briefing_direccion_deportiva", {})
        if isinstance(briefing, dict):
            for n in briefing.get("necesidades_fichaje") or []:
                need_map[n.get("id_necesidad")] = n

    lines = []

    if buys:
        items = []
        for b in buys:
            pos = _pos(b.get("posicion_requerida") or b.get("posicion") or b.get("pos"))
            amount = _bold(_eur(b.get("importe_oferta") or b.get("amount")))
            why = ""
            need = need_map.get(b.get("id_necesidad_coach"))
            if need:
                target = _pos(need.get("posicion_requerida"))
                why = f" — cubre <b>{target}</b> ({need.get('prioridad','').title()})"
            elif b.get("motivo"):
                why = f" — {b.get('motivo')}"
            tipo = b.get("tipo_puja") or ""
            tipo_txt = f" · <i>{tipo}</i>" if tipo else ""
            items.append(f"Puja por <b>{b.get('nombre')}</b> ({pos}) · {amount}{tipo_txt}{why}")
        lines.append("<b>Pujas decididas (con el porqué):</b>" + _ul(items))
    if cancels:
        items = [f"Cancelar puja de <b>{c.get('nombre')}</b> — {c.get('motivo', '')}" for c in cancels]
        lines.append("<b>Cancelaciones decididas:</b>" + _ul(items))
    if sales:
        items = [f"Vender <b>{s.get('nombre')}</b>" for s in sales]
        lines.append("<b>Ventas decididas:</b>" + _ul(items))
    if removals:
        items = [f"Retirar del mercado a <b>{r.get('nombre')}</b> — {r.get('motivo', '')}" for r in removals]
        lines.append("<b>Retiradas del mercado:</b>" + _ul(items))
    if not any([buys, cancels, sales, removals]):
        lines.append("<p style='margin:0;'>Sin operaciones decididas (no se toca nada).</p>")

    fin = sd_decisions.get("analisis_financiero_previo") or {}
    if isinstance(fin, dict) and fin.get("presupuesto_disponible") is not None:
        proj = projected_balance if projected_balance is not None else fin.get("saldo_proyectado_post_operaciones")
        proj_txt = f" · saldo previsto {_bold(_eur(proj))}" if proj is not None else ""
        lines.insert(0, f"<p style='margin:0 0 8px 0;'>Presupuesto disponible: <b>{_eur(fin.get('presupuesto_disponible'))}</b>{proj_txt}</p>")

    return {"title": "💼 DIRECTOR DEPORTIVO", "body_html": "".join(lines)}


def _results_section(execution_results):
    if not execution_results:
        return {"title": "⚡ RESULTADOS", "body_html": "<i>Sin resultados de ejecución.</i>"}
    items = []
    for res in execution_results:
        txt = str(res)
        if "✅" in txt or "SUCCESS" in txt:
            txt = "✅ " + txt.replace(": SUCCESS", "").replace(" ✅", "")
        elif "❌" in txt or "FAILED" in txt:
            txt = "❌ " + txt.replace(": FAILED", "").replace(" ❌", "")
        elif "🛡️" in txt or "BLOCKED" in txt or "SKIPPED" in txt or "⚠️" in txt:
            pass
        items.append(txt.replace("🚀", "").strip())
    return {"title": "⚡ RESULTADOS DE LA EJECUCIÓN", "body_html": _ul(items)}


def _stats_html(coach_report, sd_decisions, execution_results, df_master, my_team):
    fin = sd_decisions.get("analisis_financiero_previo", {}) if isinstance(sd_decisions, dict) else {}
    balance = fin.get("presupuesto_disponible")
    n_bids = len(sd_decisions.get("operaciones_compra", [])) if isinstance(sd_decisions, dict) else 0
    state = _squad_state(df_master, my_team)
    stats = []
    if balance is not None:
        stats.append(f"{_bold(_eur(balance))} disponibles")
    if state:
        stats.append(f"{_bold(state['size'])} jugadores")
    if n_bids:
        stats.append(f"{_bold(n_bids)} pujas")
    if state and state["missing"]:
        stats.append("sin " + " y ".join(_pos(p) for p in state["missing"]))
    if not stats:
        stats.append("Sin datos")
    return " · ".join(stats)


def _build_headline(sd_decisions, execution_results):
    if isinstance(execution_results, list):
        for res in execution_results:
            if "illegal XI" in str(res) or "no legal XI" in str(res) or "no goalkeeper" in str(res).lower():
                return "Sin portero en plantilla: once en espera", "Las pujas están en marcha pero todavía no hay guardameta; el once no se puede cerrar."
    if isinstance(sd_decisions, dict) and sd_decisions.get("operaciones_compra"):
        names = ", ".join(b.get("nombre", "?") for b in sd_decisions["operaciones_compra"][:2])
        return f"Mercado en marcha: pujas por {names}", "El director deportivo ha lanzado las operaciones; resultado en la resolución."
    return "Jornada de mercado", "Resumen de las decisiones del equipo."


def render_action_email(coach_report, sd_decisions, execution_results, df_master, my_team, projected_balance=None):
    """
    Builds the full schematic email for the ACTION mode (after agents ran).
    Returns a dict with keys for the HTML template: headline, lede, stats_html,
    sections, actions_html.
    """
    sections = [
        _coach_section(coach_report),
        _sd_section(sd_decisions, projected_balance=projected_balance, coach_report=coach_report),
        _results_section(execution_results),
    ]

    headline, lede = _build_headline(sd_decisions, execution_results)

    actions = []
    if isinstance(execution_results, list):
        for res in execution_results:
            txt = str(res)
            if "SKIPPED" in txt or "BLOCKED" in txt:
                actions.append(txt.replace("⚠️", "").replace("⏭️", "").replace("🛡️", "").strip())
            elif "SUCCESS" in txt or "FAILED" in txt:
                actions.append(txt.replace("✅", "").replace("❌", "").strip())
    actions_html = _ul(actions) if actions else "<i>Sin acciones destacadas.</i>"

    return {
        "headline": headline,
        "lede": lede,
        "stats_html": _stats_html(coach_report, sd_decisions, execution_results, df_master, my_team),
        "sections": sections,
        "actions_html": actions_html,
    }


def _auction_won_section(won, lost):
    items = []
    if won:
        items.append("<b>Ganadas:</b>" + _ul([f"{name} ({_pos(pos)})" for _, name, pos in won]))
    if lost:
        items.append("<b>Perdidas:</b>" + _ul([
            f"{info.get('name')} ({_pos(info.get('position'))}) — pujamos {_eur(info.get('amount'))}"
            for info in lost
        ]))
    if not items:
        items.append("<p style='margin:0;'>Ninguna puja nuestra se resolvió en este cierre.</p>")
    return {"title": "🔁 CIERRE DE SUBASTA (7:00)", "body_html": "".join(items)}


def render_auction_close_email(pre_master, post_master, won, lost, cleanup_results, df_master, my_team):
    """
    Builds the schematic email sent right after the 7:00 auction resolution:
    won/lost bids, squad state, market summary and the cleanup executed.
    """
    sections = [_auction_won_section(won, lost)]

    state = _squad_state(df_master, my_team)
    if state:
        missing = " y ".join(_pos(p) for p in state["missing"]) if state["missing"] else "ninguno"
        lines = [
            f"Plantilla actual: <b>{state['size']} jugadores</b> "
            f"(POR {state['counts'].get('GK',0)} · DEF {state['counts'].get('DF',0)} · "
            f"MED {state['counts'].get('MF',0)} · DEL {state['counts'].get('FW',0)}).",
            f"Posiciones sin cubrir: <b>{missing}</b>.",
        ]
        sections.append({"title": "📋 ESTADO DE LA PLANTILLA", "body_html": "<p style='margin:0 0 6px 0;'>" + "</p><p style='margin:0 0 6px 0;'>".join(lines) + "</p>"})

    # Market highlights after the reset (players to watch)
    market_lines = []
    if df_master is not None and "MARKET_SALE_PRICE" in df_master.columns:
        market = df_master[df_master["MARKET_SALE_PRICE"] > 0].copy()
        if not market.empty:
            if "PLAYER_PRICE" in market.columns:
                market = market.sort_values("PLAYER_PRICE", ascending=False)
            top = market.head(6)
            for _, r in top.iterrows():
                seller = r.get("MARKET_SALE_USER_NAME") or "Mercado"
                label = f"de {seller}" if seller and seller != "Mercado" else "libre"
                market_lines.append(
                    f"<b>{r.get('PLAYER_NAME')}</b> ({_pos(r.get('PLAYER_POSITION'))}) · "
                    f"{_eur(r.get('MARKET_SALE_PRICE'))} · {label}"
                )
    if market_lines:
        sections.append({
            "title": "🛒 MERCADO DEL DÍA",
            "body_html": "<b>Destacados en subasta:</b>" + _ul(market_lines[:6]),
        })

    if cleanup_results:
        sections.append({
            "title": "🧹 LIMPIEZA POST-SUBASTA",
            "body_html": _ul([str(c) for c in cleanup_results]),
        })

    balance = None
    if df_master is not None and os.path.exists("./data/user_info.csv"):
        try:
            balance = float(pd.read_csv("./data/user_info.csv")["balance"].iloc[0])
        except Exception:
            balance = None

    stats = []
    if balance is not None:
        stats.append(f"{_bold(_eur(balance))} disponibles")
    if state:
        stats.append(f"{_bold(state['size'])} jugadores")
    if won:
        stats.append(f"{_bold(len(won))} subastas ganadas")
    if lost:
        stats.append(f"{_bold(len(lost))} pujas perdidas")
    stats_html = " · ".join(stats) if stats else ""

    if won:
        headline, lede = "Fichaje cerrado", "Se resolvió la subasta con éxito; la plantilla gana efectivos."
    elif lost:
        headline, lede = "Sin éxito en la subasta", "Las pujas no se impusieron; se revisa el mercado tras el cierre."
    else:
        headline, lede = "Cierre de subasta", "Estado del mercado y de la plantilla tras el cierre de las 7:00."

    actions_html = _ul([
        f"Se revisan las necesidades pendientes: {', '.join(_pos(p) for p in state['missing']) if state and state['missing'] else 'ninguna'}."
    ]) if state else "<i>Sin acciones destacadas.</i>"

    return {
        "headline": headline,
        "lede": lede,
        "stats_html": stats_html,
        "sections": sections,
        "actions_html": actions_html,
    }
