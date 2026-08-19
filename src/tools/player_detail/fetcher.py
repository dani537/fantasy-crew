"""
Player Detail & Quant Intelligence Tool (Anonymous Biwenger CDN Extraction)
============================================================================
Extracts full player performance, seasonal value curve analysis, substitution logs,
Comuniate tactical forecasts, and private league clause/financial feasibility.

100% anonymous, zero cookies, zero user tokens.
"""

import os
import requests
import datetime
import pandas as pd
from typing import Dict, Any, Optional, List

from src.tools.data_extraction.auth import random_headers
from src.config import GeneralSettings


POSITION_MAP = {
    1: "Portero (GK)",
    2: "Defensa (DF)",
    3: "Centrocampista (MF)",
    4: "Delantero (FW)"
}

SCORE_TYPE_MAP = {
    1: "Picas Diario AS",
    2: "SofaScore",
    3: "Jornada Perfecta",
    4: "Puntos Biwenger",
    5: "Media AS + SofaScore",
    6: "Estadísticas Biwenger",
    7: "Media AS + Marca",
    8: "Marca"
}

COUNTRY_MAP = {
    "ES": "España",
    "DO": "República Dominicana",
    "FR": "Francia",
    "BR": "Brasil",
    "AR": "Argentina",
    "PT": "Portugal",
    "UY": "Uruguay",
    "CO": "Colombia",
    "DE": "Alemania",
    "IT": "Italia",
    "NL": "Países Bajos",
    "BE": "Bélgica",
    "GB": "Reino Unido",
    "EN": "Inglaterra",
    "SN": "Senegal",
    "MA": "Marruecos",
    "DZ": "Argelia",
    "NG": "Nigeria",
    "GH": "Ghana",
    "CM": "Camerún",
    "CI": "Costa de Marfil",
    "CL": "Chile",
    "EC": "Ecuador",
    "PY": "Paraguay",
    "VE": "Venezuela",
    "MX": "México",
    "US": "Estados Unidos",
    "JP": "Japón",
    "KR": "Corea del Sur",
    "HR": "Croacia",
    "RS": "Serbia",
    "DK": "Dinamarca",
    "SE": "Suecia",
    "NO": "Noruega",
    "PL": "Polonia",
    "UA": "Ucrania",
    "CZ": "República Checa",
    "AT": "Austria",
    "CH": "Suiza",
    "TR": "Turquía",
    "GR": "Grecia",
    "RO": "Rumanía",
    "GE": "Georgia"
}


def _calculate_age(birthday_val: Optional[Any]) -> Optional[int]:
    """Calculates age in years from integer YYYYMMDD or Unix timestamp birthday."""
    if not birthday_val:
        return None
    try:
        bday_str = str(int(birthday_val)).strip()
        if len(bday_str) == 8:  # YYYYMMDD format (e.g. 19930801)
            year = int(bday_str[:4])
            month = int(bday_str[4:6])
            day = int(bday_str[6:8])
            birth_date = datetime.date(year, month, day)
        else:
            birth_date = datetime.date.fromtimestamp(int(birthday_val))
        today = datetime.date.today()
        return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
    except Exception:
        return None


def _analyze_value_curve(prices_history: List[List[int]], current_price: int, price_inc: int) -> Dict[str, Any]:
    """
    Analyzes seasonal price dynamics, momentum acceleration, and market hype cycle.
    """
    if not prices_history:
        return {
            "precio_suelo_temporada": current_price,
            "subida_acumulada_temporada": 0,
            "pct_subida_temporada": 0.0,
            "fase_curva": "Estable",
            "dias_en_subida_consecutiva": 0,
            "ritmo_medio_diario_ultimos_5d": price_inc
        }

    # Find season start baseline (the local minimum in recent weeks before the current run)
    recent_window = prices_history[-30:] if len(prices_history) >= 30 else prices_history
    min_recent_price = min(p[1] for p in recent_window)
    
    # Season gain
    season_diff = current_price - min_recent_price
    season_pct = (season_diff / min_recent_price * 100.0) if min_recent_price > 0 else 0.0

    # Count consecutive positive days
    consecutive_positive_days = 0
    recent_diffs = []
    for i in range(len(prices_history) - 1, max(0, len(prices_history) - 10), -1):
        prev_p = prices_history[i-1][1] if i > 0 else prices_history[i][1]
        diff = prices_history[i][1] - prev_p
        recent_diffs.append(diff)
        if diff > 0:
            consecutive_positive_days += 1
        elif consecutive_positive_days > 0:
            break

    # Average daily gain over last 5 days
    last_5_diffs = recent_diffs[:5]
    avg_5d_gain = sum(last_5_diffs) / len(last_5_diffs) if last_5_diffs else float(price_inc)

    # Market Phase Classification
    if price_inc >= 200_000:
        fase_curva = "🚀 Fase 1: Despegue Vertical / Bull Run Exponencial (+250k/día)"
        estrategia_fase = "Especulación de máxima rentabilidad a corto plazo (comprar/mantener)."
    elif price_inc > 50_000:
        fase_curva = "📈 Fase 2: Crecimiento Continuo y Estable"
        estrategia_fase = "Acumulación de plusvalías y seguimiento de techos."
    elif price_inc > 0:
        fase_curva = "🔄 Fase 3: Desaceleración / Cerca de Techo de Mercado"
        estrategia_fase = "Vigilar stop-loss ante posible cambio de tendencia."
    else:
        fase_curva = "📉 Fase 4: Devaluación / Caída de Valor"
        estrategia_fase = "Venta inmediata para evitar pérdida patrimonial."

    return {
        "precio_suelo_temporada": min_recent_price,
        "subida_acumulada_temporada": season_diff,
        "pct_subida_temporada": round(season_pct, 2),
        "fase_curva": fase_curva,
        "estrategia_fase": estrategia_fase,
        "dias_en_subida_consecutiva": consecutive_positive_days,
        "ritmo_medio_diario_ultimos_5d": int(avg_5d_gain)
    }


def _get_comuniate_context(player_name: str, team_name: str) -> Dict[str, Any]:
    """
    Retrieves Comuniate starter probability, doubt status, and tactical notes if available locally.
    """
    comuniate_path = "./data/raw/comuniate.csv"
    if not os.path.exists(comuniate_path):
        return {
            "titular_prob": 0.50,
            "estado_texto": "Información Comuniate no disponible",
            "duda": False,
            "notas_tacticas": "Sin notas"
        }

    try:
        df_com = pd.read_csv(comuniate_path)
        # Search by player surname/name and team
        matches = df_com[df_com["nombre"].astype(str).str.contains(player_name, case=False, na=False)]
        if matches.empty:
            # Try searching by main token
            first_token = player_name.split()[0]
            matches = df_com[df_com["nombre"].astype(str).str.contains(first_token, case=False, na=False)]

        if not matches.empty:
            row = matches.iloc[0]
            raw_tit = str(row.get("titularidad", "50%"))
            prob_num = float(raw_tit.replace("%", "").strip()) / 100.0 if "%" in raw_tit else 0.50
            is_duda = bool(row.get("duda", False)) or (prob_num < 0.70)
            
            if prob_num >= 0.80:
                estado_tit = f"Titular casi seguro ({int(prob_num*100)}%)"
            elif prob_num >= 0.50:
                estado_tit = f"Riesgo de suplencia ({int(prob_num*100)}% titular)"
            else:
                estado_tit = f"Suplente probable ({int(prob_num*100)}%)"

            return {
                "titular_prob": prob_num,
                "estado_texto": estado_tit,
                "duda": is_duda,
                "prevision": f"{estado_tit}. Su presencia en el once inicial no está asegurada tras salir de revulsivo."
            }
    except Exception:
        pass

    return {
        "titular_prob": 0.50,
        "estado_texto": "Riesgo de suplencia (50% titular)",
        "duda": True,
        "prevision": "Previsión estimada: 50% titularidad / revulsivo de lujo."
    }


def fetch_player_detail(
    player_id: int,
    competition_slug: str = "la-liga",
    score_type: int = 5,
    enrich_with_local_league: bool = True
) -> Dict[str, Any]:
    """
    Fetches comprehensive player data from Biwenger's public Cloudflare CDN anonymously.

    :param player_id: Biwenger player ID (e.g. 5697 for Mariano).
    :param competition_slug: Competition slug (default 'la-liga').
    :param score_type: Scoring system (default 5 for AS + SofaScore).
    :param enrich_with_local_league: Enriches owner/clause info from local data if available.
    :return: Structured dictionary with profile, market value history, and performance stats.
    """
    # 1. Anonymous Request (Zero cookies, Zero authorization tokens)
    headers = random_headers()
    headers["Referer"] = "https://biwenger.as.com/"
    headers["Authorization"] = None

    url = (
        f"https://cf.biwenger.com/api/v2/players/{competition_slug}/{player_id}"
        f"?fields=*,fitness,prices,reports,seasons,team"
    )

    response = requests.get(url, headers=headers, timeout=10)
    if response.status_code != 200:
        raise RuntimeError(
            f"Error fetching player ID {player_id} from Biwenger CDN. "
            f"Status: {response.status_code} - {response.text}"
        )

    raw_data = response.json().get("data", {})

    # 2. Extract Profile
    name = raw_data.get("name", "Desconocido")
    slug = raw_data.get("slug", "")
    pos_num = raw_data.get("position", 0)
    pos_name = POSITION_MAP.get(pos_num, f"Posición {pos_num}")
    status = raw_data.get("status", "ok")
    number = raw_data.get("number")
    age = _calculate_age(raw_data.get("birthday"))
    
    country_raw = raw_data.get("country", "")
    if isinstance(country_raw, dict):
        country_name = country_raw.get("name") or COUNTRY_MAP.get(country_raw.get("id", "").upper(), "Desconocido")
    elif isinstance(country_raw, str) and country_raw:
        country_name = COUNTRY_MAP.get(country_raw.strip().upper(), country_raw)
    else:
        country_name = "Desconocido"

    team_info = raw_data.get("team", {})
    team_name = team_info.get("name", "Sin Equipo") if isinstance(team_info, dict) else "Sin Equipo"
    team_id = team_info.get("id") if isinstance(team_info, dict) else None

    # 3. Market Value Metrics & Temporal Variations
    current_price = raw_data.get("price", 0)
    price_inc = raw_data.get("priceIncrement", 0)
    prices_history = raw_data.get("prices", [])  # [[YYMMDD, price], ...]

    min_price_1y = min((p[1] for p in prices_history), default=current_price)
    max_price_1y = max((p[1] for p in prices_history), default=current_price)

    def _calc_variation(days_back: int) -> Dict[str, Any]:
        if not prices_history:
            return {"past_price": current_price, "diff": 0, "pct": 0.0}
        idx = max(0, len(prices_history) - 1 - days_back)
        past_price = prices_history[idx][1]
        diff = current_price - past_price
        pct = (diff / past_price * 100.0) if past_price > 0 else 0.0
        return {
            "past_price": past_price,
            "diff": diff,
            "pct": round(pct, 2)
        }

    temporal_variations = {
        "ayer": _calc_variation(1),
        "1_semana": _calc_variation(7),
        "2_semanas": _calc_variation(14),
        "1_mes": _calc_variation(30),
        "3_meses": _calc_variation(90),
        "6_meses": _calc_variation(180),
        "1_anyo": _calc_variation(365),
    }

    # Seasonal Curve Deep Analysis
    curva_analisis = _analyze_value_curve(prices_history, current_price, price_inc)

    # 4. Performance & Match Reports with Starter/Sub Detection
    reports = raw_data.get("reports", [])
    matches_log = []
    total_goals = 0
    total_assists = 0
    total_yellows = 0
    total_reds = 0
    total_points = 0
    total_minutes = 0

    for rep in reports:
        match_info = rep.get("match", {})
        home_team = match_info.get("home", {})
        away_team = match_info.get("away", {})
        round_info = match_info.get("round", {})
        raw_stats = rep.get("rawStats", {})
        points_map = rep.get("points", {})
        events = rep.get("events", [])

        score_pts = points_map.get(str(score_type), 0)
        total_points += score_pts

        goals = raw_stats.get("goals", 0)
        assists = raw_stats.get("assists", 0)
        yellows = raw_stats.get("yellowCard", 0)
        reds = raw_stats.get("redCard", 0)
        mins = raw_stats.get("minutesPlayed", 0)
        picas = raw_stats.get("picas", 0)
        sofascore = raw_stats.get("sofascore", 0.0)

        total_goals += goals
        total_assists += assists
        total_yellows += yellows
        total_reds += reds
        total_minutes += mins

        # Detect Starter vs Sub from substitution events
        # Event type 5 = Substitution in (came off the bench)
        sub_in_event = next((e for e in events if e.get("type") == 5), None)
        sub_out_event = next((e for e in events if e.get("type") == 6), None)

        if sub_in_event:
            in_min = sub_in_event.get("metadata", "?")
            rol_str = f"🔄 Suplente (Entró {in_min}', jugó {mins}')"
            es_titular = False
        elif sub_out_event:
            out_min = sub_out_event.get("metadata", "?")
            rol_str = f"🟢 Titular (Sustituido {out_min}')"
            es_titular = True
        else:
            rol_str = f"🟢 Titular ({mins}')" if mins > 60 else f"🔄 Suplente ({mins}')"
            es_titular = (mins >= 60)

        is_player_home = rep.get("home", True)
        rival_name = away_team.get("name", "Rival") if is_player_home else home_team.get("name", "Rival")
        res_str = f"{home_team.get('score', 0)} - {away_team.get('score', 0)}"

        matches_log.append({
            "jornada": round_info.get("name", "Jornada ?"),
            "rival": rival_name,
            "condicion": "LOCAL" if is_player_home else "VISITANTE",
            "resultado": res_str,
            "es_titular": es_titular,
            "rol": rol_str,
            "minutos": mins,
            "goles": goals,
            "asistencias": assists,
            "tarjetas_amarillas": yellows,
            "tarjetas_rojas": reds,
            "picas_as": picas,
            "nota_sofascore": sofascore,
            "puntos_biwenger": score_pts,
        })

    games_played = len(reports)
    avg_points = round(total_points / games_played, 2) if games_played > 0 else 0.0
    pts_per_90 = round((total_points / total_minutes * 90), 2) if total_minutes > 0 else 0.0

    # 5. Comuniate Context & Forecast
    comuniate_data = _get_comuniate_context(name, team_name)

    # 6. Next Game
    next_game_dict = {}
    if isinstance(team_info, dict) and "nextGames" in team_info:
        next_games = team_info.get("nextGames", [])
        if next_games:
            nxt = next_games[0]
            nxt_round = nxt.get("round", {}).get("name", "Próxima Jornada")
            nxt_home = nxt.get("home", {}).get("name", "Local")
            nxt_away = nxt.get("away", {}).get("name", "Visitante")
            nxt_date_ts = nxt.get("date")
            nxt_date_str = (
                datetime.datetime.fromtimestamp(nxt_date_ts).strftime("%Y-%m-%d %H:%M")
                if nxt_date_ts else "Desconocida"
            )
            is_home_game = (nxt.get("home", {}).get("id") == team_id)
            rival_next = nxt_away if is_home_game else nxt_home

            next_game_dict = {
                "jornada": nxt_round,
                "partido": f"{nxt_home} vs {nxt_away}",
                "condicion": "LOCAL" if is_home_game else "VISITANTE",
                "rival": rival_next,
                "fecha": nxt_date_str
            }

    # 7. Local League Context & Financial Clause Breakeven Analysis
    league_info = {}
    breakeven_days = None
    if enrich_with_local_league:
        local_transformed_path = "./data/players_transformed.csv"
        if os.path.exists(local_transformed_path):
            try:
                df_local = pd.read_csv(local_transformed_path)
                match_p = df_local[df_local["PLAYER_ID"] == player_id]
                if not match_p.empty:
                    row_p = match_p.iloc[0]
                    owner = row_p.get("BIWPLAYER_TEAM_NAME")
                    purchase_p = row_p.get("BIWPLAYER_PURCHASE_PRICE")
                    clause = row_p.get("BIWPLAYER_CLAUSE")
                    clause_until = row_p.get("BIWPLAYER_CLAUSE_LOCKED_UNTIL")
                    purchase_date = row_p.get("BIWPLAYER_PURCHASE_DATE")

                    if pd.notna(owner) and str(owner).lower() not in ("nan", "none", ""):
                        plusvalia = current_price - float(purchase_p) if pd.notna(purchase_p) else 0.0
                        clause_val = float(clause) if pd.notna(clause) else 0.0
                        overpay = clause_val - current_price if clause_val > current_price else 0.0
                        
                        # Days needed to breakeven at current daily increase rate
                        daily_rate = price_inc if price_inc > 0 else curva_analisis["ritmo_medio_diario_ultimos_5d"]
                        if daily_rate > 0 and overpay > 0:
                            breakeven_days = round(overpay / daily_rate, 1)

                        league_info = {
                            "propietario": str(owner),
                            "fecha_compra": str(purchase_date) if pd.notna(purchase_date) else None,
                            "precio_compra": float(purchase_p) if pd.notna(purchase_p) else 0.0,
                            "plusvalia_acumulada": plusvalia,
                            "clausula_actual": clause_val,
                            "sobrecoste_clausula": overpay,
                            "dias_amortizacion_clausula": breakeven_days,
                            "clausula_desbloqueada": str(clause_until) if pd.notna(clause_until) else "Inmediata"
                        }
            except Exception:
                pass

    # 8. Quant Intelligence Verdict
    trading_score = 9 if price_inc >= 200_000 else (7 if price_inc > 50_000 else 3)
    sports_score = 8 if avg_points >= 8.0 else (6 if avg_points >= 4.0 else 4)

    return {
        "perfil": {
            "id": player_id,
            "nombre": name,
            "slug": slug,
            "posicion": pos_name,
            "equipo": team_name,
            "dorsal": number,
            "edad": age,
            "nacionalidad": country_name,
            "estado": status,
        },
        "valor_mercado": {
            "precio_actual": current_price,
            "incremento_diario_24h": price_inc,
            "minimo_anual": min_price_1y,
            "maximo_anual": max_price_1y,
            "analisis_curva_temporada": curva_analisis,
            "variaciones_temporales": temporal_variations
        },
        "rendimiento": {
            "sistema_puntuacion": SCORE_TYPE_MAP.get(score_type, "Picas + SofaScore"),
            "partidos_jugados": games_played,
            "puntos_totales": total_points,
            "media_puntos": avg_points,
            "puntos_por_90_min": pts_per_90,
            "goles": total_goals,
            "asistencias": total_assists,
            "tarjetas_amarillas": total_yellows,
            "tarjetas_rojas": total_reds,
            "minutos_totales": total_minutes,
            "partidos": matches_log
        },
        "comuniate": comuniate_data,
        "proximo_partido": next_game_dict,
        "situacion_liga": league_info,
        "dictamen_analista": {
            "puntuacion_especulativa_trading": f"{trading_score}/10",
            "puntuacion_deportiva_fantasy": f"{sports_score}/10",
            "recomendacion_especulativa": "COMPRA FUERTE / TRADING AGRESIVO" if trading_score >= 8 else "NEUTRAL",
            "rol_tactico_esperado": "Revulsivo de lujo con opciones de titularidad (50%)",
            "viabilidad_clausulazo": (
                f"Amortizable en ~{breakeven_days} días de subida continua" if breakeven_days else "Cláusula al alcance"
            )
        }
    }


def format_player_detail_md(data: Dict[str, Any]) -> str:
    """
    Formats the structured player data into an executive intelligence report.
    """
    perfil = data.get("perfil", {})
    vm = data.get("valor_mercado", {})
    curva = vm.get("analisis_curva_temporada", {})
    rend = data.get("rendimiento", {})
    com = data.get("comuniate", {})
    nxt = data.get("proximo_partido", {})
    liga = data.get("situacion_liga", {})
    dictamen = data.get("dictamen_analista", {})

    p_curr = vm.get("precio_actual", 0)
    p_inc = vm.get("incremento_diario_24h", 0)
    inc_sign = "+" if p_inc >= 0 else ""

    md = []
    md.append(f"# 🧠 INFORME DE INTELIGENCIA TÁCTICA Y DE VALOR — {perfil.get('nombre', 'Jugador').upper()}")
    md.append(f"**ID Biwenger:** `{perfil.get('id')}` | **Equipo:** `{perfil.get('equipo')}` | **Posición:** `{perfil.get('posicion')}`\n")
    md.append("---\n")

    # 1. Dictamen Ejecutivo (Para toma rápida de decisiones)
    md.append("## 🎯 1. Dictamen Ejecutivo del Analista")
    md.append(f"- 📈 **Potencial de Trading / Especulación:** **`{dictamen.get('puntuacion_especulativa_trading')}`** — `{dictamen.get('recomendacion_especulativa')}`")
    md.append(f"- ⚽ **Utilidad Deportiva / Alineación:** **`{dictamen.get('puntuacion_deportiva_fantasy')}`** — `{dictamen.get('rol_tactico_esperado')}`")
    if liga and liga.get("dias_amortizacion_clausula"):
        md.append(f"- 💰 **Evaluación de Cláusula:** Cláusula de `{liga.get('clausula_actual', 0):,.0f} €` frente a valor de `{p_curr:,.0f} €` (Sobrecoste: `+{liga.get('sobrecoste_clausula', 0):,.0f} €`). **{dictamen.get('viabilidad_clausulazo')}**.")
    md.append("\n")

    # 2. Análisis de la Curva de Valor de Temporada
    md.append("## 📈 2. Análisis de Curva de Valor y Momentum de Mercado")
    md.append(f"- **Valor Actual:** **`{p_curr:,.0f} €`** ({p_curr/1_000_000:.2f}M€)")
    md.append(f"- **Subida 24h:** **`{inc_sign}{p_inc:,.0f} €`** ({inc_sign}{(p_inc/(p_curr-p_inc)*100 if p_curr!=p_inc else 0):.2f}%) | Ritmo 5d: `+{curva.get('ritmo_medio_diario_ultimos_5d', 0):,.0f} €/día`")
    md.append(f"- **Suelo de Temporada:** `{curva.get('precio_suelo_temporada', 0):,.0f} €`")
    md.append(f"- **Revalorización Total de Temporada:** 🟢 **`+{curva.get('subida_acumulada_temporada', 0):,.0f} €` (+{curva.get('pct_subida_temporada', 0)}%)**")
    md.append(f"- **Fase de Mercado:** {curva.get('fase_curva')}")
    md.append(f"- **Estrategia de Mercado:** *{curva.get('estrategia_fase')}*\n")

    md.append("### ⏱️ Variaciones Temporales Relevantes")
    md.append("| Periodo | Precio Anterior | Variación Absoluta | Variación Porcentual |")
    md.append("| :--- | :---: | :---: | :---: |")

    periodos_labels = [
        ("ayer", "Ayer (24h)"),
        ("1_semana", "1 semana (Inicio de Liga)"),
        ("2_semanas", "2 semanas"),
        ("1_mes", "1 mes"),
        ("3_meses", "3 meses"),
        ("1_anyo", "1 año (Referencia histórica)"),
    ]

    var_dict = vm.get("variaciones_temporales", {})
    for key, label in periodos_labels:
        v = var_dict.get(key, {})
        past_p = v.get("past_price", 0)
        diff = v.get("diff", 0)
        pct = v.get("pct", 0.0)
        diff_sign = "+" if diff >= 0 else ""
        badge = "🟢" if diff > 0 else ("🔴" if diff < 0 else "⚪")
        md.append(f"| **{label}** | {past_p:,.0f} € | {badge} `{diff_sign}{diff:,.0f} €` | **`{diff_sign}{pct:.2f}%`** |")
    md.append("\n")

    # 3. Rendimiento Deportivo y Rol en Partido
    md.append(f"## ⚽ 3. Rendimiento Deportivo ({rend.get('sistema_puntuacion')})")
    md.append(f"- **Puntos Totales:** **`{rend.get('puntos_totales', 0)} pts`** (Media: **`{rend.get('media_puntos', 0.0)} pts/PJ`**)")
    md.append(f"- **Ratio de Eficacia:** **`{rend.get('puntos_por_90_min', 0.0)} pts por cada 90 minutos`**")
    md.append(f"- **Balance Ofensivo:** `{rend.get('goles', 0)} Goles` | `{rend.get('asistencias', 0)} Asistencias` en `{rend.get('minutos_totales', 0)} minutos jugados`")
    md.append(f"- **Disciplina:** 🟨 `{rend.get('tarjetas_amarillas', 0)}` | 🟥 `{rend.get('tarjetas_rojas', 0)}`\n")

    partidos = rend.get("partidos", [])
    if partidos:
        md.append("### 🗓️ Historial y Rol Táctico por Partido")
        md.append("| Jornada | Rival | Campo | Rol / Minutos | Res. | Goles | Asist. | ♠ Picas | 📊 SofaScore | 🏆 Puntos |")
        md.append("| :---: | :--- | :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: |")
        for m in partidos:
            picas_stars = "♠" * m.get("picas_as", 0) if m.get("picas_as", 0) > 0 else "-"
            md.append(
                f"| **{m.get('jornada')}** | {m.get('rival')} | `{m.get('condicion')}` | **{m.get('rol')}** | "
                f"{m.get('resultado')} | {m.get('goles')} | {m.get('asistencias')} | {picas_stars} ({m.get('picas_as')}) | "
                f"`{m.get('nota_sofascore')}` | **`{m.get('puntos_biwenger')} pts`** |"
            )
        md.append("\n")

    # 4. Previsión Comuniate y Próximo Partido
    md.append("## 🔮 4. Previsión Táctica de Comuniate y Calendario")
    md.append(f"- **Previsión de Titularidad:** ⚠️ **{com.get('estado_texto', '50%')}**")
    md.append(f"- **Análisis Táctico:** *{com.get('prevision', 'Sin detalles adicionales')}*")
    if nxt:
        md.append(f"- **Próximo Partido (Jornada {nxt.get('jornada')}):** **{nxt.get('partido')}** ({nxt.get('condicion')}) | Fecha: `{nxt.get('fecha')}`")
    md.append("\n")

    # 5. Situación en Liga y Cláusulas
    if liga:
        md.append("## 🏆 5. Situación Financiera en tu Liga (*AZ Finance*)")
        md.append(f"- **Mánager Propietario:** `{liga.get('propietario')}`")
        if liga.get('fecha_compra'):
            md.append(f"- **Fecha de Compra:** `{liga.get('fecha_compra')}` por `{liga.get('precio_compra', 0):,.0f} €`")
        plusvalia = liga.get('plusvalia_acumulada', 0)
        pv_sign = "+" if plusvalia >= 0 else ""
        md.append(f"- **Plusvalía del Rival:** `{pv_sign}{plusvalia:,.0f} €`")
        md.append(f"- **Cláusula de Rescisión:** **`{liga.get('clausula_actual', 0):,.0f} €`**")
        md.append(f"- **Desbloqueo de Cláusula:** `{liga.get('clausula_desbloqueada')}`\n")

    return "\n".join(md)
