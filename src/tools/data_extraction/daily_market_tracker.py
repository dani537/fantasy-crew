"""
Daily Biwenger Master Market & Intelligence Tracker
====================================================
Captures the complete 40-dimensional snapshot of ALL LaLiga players (577+):
- Demographics, Identity, Position, Status
- Pricing, 24h/7d/14d/30d Variations, 1y Min/Max, Season Gain
- Community Market Sentiment (% Compras, % Ventas, % Uso, Presión Neta)
- Official Rankings (Global, Posición, Temporada Anterior)
- Detailed Performance (Puntos, Medias, Goles, Asistencias, Minutos, Picas, SofaScore, Fitness)
- Tactical & Private League Context (Comuniate titular/duda, Propietario, Cláusula, Mercado)

Syncs seamlessly to Google Sheets:
- "Historico_Continuo": Master cumulative time-series database.
- "Mercado_Hoy": Current day dashboard.
And saves local daily CSV snapshots + master timeseries backup.
"""

import os
import json
import time
import random
import datetime
import requests
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

POSITION_MAP = {1: "Portero", 2: "Defensa", 3: "Centrocampista", 4: "Delantero"}
DEFAULT_SPREADSHEET_ID = "1FsuSJr5k7BkPJa6vIL1zRK0qvIJGlaoSFIPxAUx8wr0"
SPREADSHEET_ID = os.getenv("GOOGLE_SHEET_ID_MARKET") or DEFAULT_SPREADSHEET_ID
CREDS_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or "credentials_google.json"
TAB_HISTORICO = "Historico_Continuo"
TAB_HOY = "Mercado_Hoy"

COUNTRY_MAP = {
    "ES": "España", "DO": "Rep. Dominicana", "FR": "Francia", "BR": "Brasil",
    "AR": "Argentina", "PT": "Portugal", "UY": "Uruguay", "CO": "Colombia",
    "DE": "Alemania", "IT": "Italia", "NL": "Países Bajos", "BE": "Bélgica",
    "GB": "Reino Unido", "EN": "Inglaterra", "SN": "Senegal", "MA": "Marruecos",
    "DZ": "Argelia", "NG": "Nigeria", "GH": "Ghana", "CM": "Camerún",
    "CI": "Costa de Marfil", "CL": "Chile", "EC": "Ecuador", "PY": "Paraguay",
    "VE": "Venezuela", "MX": "México", "US": "Estados Unidos", "JP": "Japón",
    "KR": "Corea del Sur", "HR": "Croacia", "RS": "Serbia", "DK": "Dinamarca",
    "SE": "Suecia", "NO": "Noruega", "PL": "Polonia", "UA": "Ucrania",
    "CZ": "Rep. Checa", "AT": "Austria", "CH": "Suiza", "TR": "Turquía",
    "GR": "Grecia", "RO": "Rumanía", "GE": "Georgia"
}

HEADERS = [
    # 1. Identificación & Perfil
    "fecha", "player_id", "nombre", "equipo", "posicion", "edad", "nacionalidad", "estado",
    # 2. Mercado & Valor
    "precio", "subida_24h", "pct_subida_24h", "min_precio_1y", "max_precio_1y", "ganancia_temporada", "pct_ganancia_temporada",
    # 3. Momentum Temporal
    "diff_7d", "pct_7d", "diff_14d", "pct_14d", "diff_30d", "pct_30d",
    # 4. Sentimiento de Mercado (Biwenger Global)
    "pct_compras_24h", "pct_ventas_24h", "pct_uso_ligas", "presion_neta",
    # 5. Rankings Oficiales
    "ranking_global", "ranking_posicion", "ranking_last_season",
    # 6. Rendimiento Deportivo
    "puntos_totales", "puntos_last_season", "partidos_jugados", "media_puntos", "racha_fitness",
    "goles", "asistencias", "minutos", "media_picas", "media_sofascore",
    # 7. Contexto Táctico & Tu Liga
    "comuniate_titular", "comuniate_duda", "propietario_liga", "clausula_actual", "en_venta_mercado"
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
]

def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "es-ES,es;q=0.9,ca;q=0.8",
        "Origin": "https://biwenger.as.com",
        "Referer": "https://biwenger.as.com/"
    }

def get_gspread_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds_json_str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if creds_json_str and creds_json_str.strip():
        try:
            print("🔑 Cargando credenciales de Google Sheets desde GOOGLE_SERVICE_ACCOUNT_JSON...")
            creds_dict = json.loads(creds_json_str.strip())
            creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
            return gspread.authorize(creds)
        except Exception as e:
            print(f"⚠️ Error cargando GOOGLE_SERVICE_ACCOUNT_JSON env var: {e}")
    if os.path.exists(CREDS_FILE):
        try:
            print(f"🔑 Cargando credenciales de Google Sheets desde archivo local: {CREDS_FILE}...")
            creds = Credentials.from_service_account_file(CREDS_FILE, scopes=scopes)
            return gspread.authorize(creds)
        except Exception as e:
            print(f"⚠️ Error cargando {CREDS_FILE}: {e}")
    print("❌ No se encontraron credenciales válidas (GOOGLE_SERVICE_ACCOUNT_JSON o credentials_google.json).")
    return None

def calculate_age(birthday_val):
    if not birthday_val:
        return None
    try:
        bday_str = str(int(birthday_val)).strip()
        if len(bday_str) == 8:
            year, month, day = int(bday_str[:4]), int(bday_str[4:6]), int(bday_str[6:8])
            birth_date = datetime.date(year, month, day)
        else:
            birth_date = datetime.date.fromtimestamp(int(birthday_val))
        today = datetime.date.today()
        return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
    except Exception:
        return None

def run_daily_market_capture(max_players: int = None):
    print(f"🎬 [{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Iniciando captura maestra de mercado y rendimiento...")
    t0 = time.time()
    today_str = datetime.date.today().strftime("%Y-%m-%d")

    # 1. Fetch general competition data
    comp_url = "https://cf.biwenger.com/api/v2/competitions/la-liga/data?score=5"
    r_comp = requests.get(comp_url, headers=get_headers(), timeout=10)
    if r_comp.status_code != 200:
        print(f"⚠️ Error obteniendo datos de competición de Biwenger: HTTP {r_comp.status_code}")
        print(f"Respuesta: {r_comp.text[:200]}")
        return False

    comp_data = r_comp.json().get("data", {})
    raw_players = comp_data.get("players", {})
    raw_teams = comp_data.get("teams", {})
    team_map = {int(tid): t["name"] for tid, t in raw_teams.items()}
    total_comp_players = len(raw_players)
    print(f"✅ Obtenidos {total_comp_players} jugadores base y {len(raw_teams)} equipos de LaLiga.")

    # 2. Local League & Comuniate context
    league_owners = {}
    league_clauses = {}
    market_sales_set = set()
    comuniate_map = {}

    df_trans_path = "data/players_transformed.csv"
    priority_ids = set()
    if os.path.exists(df_trans_path):
        try:
            df_trans = pd.read_csv(df_trans_path)
            for _, r in df_trans.iterrows():
                pid = int(r["PLAYER_ID"]) if pd.notna(r.get("PLAYER_ID")) else None
                if not pid: continue
                owner = str(r.get("BIWPLAYER_TEAM_NAME", "")).strip()
                if owner and owner.lower() not in ("nan", "none", ""):
                    league_owners[pid] = owner
                    priority_ids.add(pid)
                if pd.notna(r.get("BIWPLAYER_CLAUSE")):
                    league_clauses[pid] = float(r["BIWPLAYER_CLAUSE"])
                if pd.notna(r.get("MARKET_SALE_PRICE")):
                    market_sales_set.add(pid)
                    priority_ids.add(pid)
        except Exception:
            pass

    if os.path.exists("data/raw/comuniate.csv"):
        try:
            df_com = pd.read_csv("data/raw/comuniate.csv")
            for _, r in df_com.iterrows():
                pname = str(r.get("nombre", "")).strip().lower()
                comuniate_map[pname] = {
                    "titular": r.get("titularidad"),
                    "duda": r.get("duda")
                }
        except Exception:
            pass

    # 3. Target players: ALL 577 players of LaLiga
    sorted_pids = sorted(
        raw_players.keys(),
        key=lambda k: (int(k) in priority_ids, raw_players[k].get("price", 0)),
        reverse=True
    )
    if max_players is not None and max_players > 0:
        target_pids = [int(p) for p in sorted_pids[:max_players]]
    else:
        target_pids = [int(p) for p in sorted_pids]

    print(f"⏳ Extrayendo datos exhaustivos (mercado, curva, actas) para TODOS los {len(target_pids)} jugadores de LaLiga...")
    details_map = {}
    for idx, pid in enumerate(target_pids, 1):
        time.sleep(random.uniform(0.10, 0.18))
        url = f"https://cf.biwenger.com/api/v2/players/la-liga/{pid}?fields=id,name,birthday,country,analysis,prices,reports"
        for attempt in range(3):
            try:
                r = requests.get(url, headers=get_headers(), timeout=5)
                if r.status_code == 200:
                    details_map[pid] = r.json().get("data", {})
                    break
                elif r.status_code == 429:
                    time.sleep(1.5 + attempt * 1.5)
            except Exception:
                time.sleep(0.5)
        
        if idx % 50 == 0 or idx == len(target_pids):
            print(f"   Progreso: {idx}/{len(target_pids)} ({idx/len(target_pids)*100:.1f}%) — {time.time()-t0:.1f}s")

    print(f"✅ {len(details_map)}/{len(target_pids)} fichas exhaustivas extraídas con éxito.")

    # 4. Build comprehensive master rows
    rows_to_append = []
    for pid in target_pids:
        p_base = raw_players.get(str(pid), {})
        p_detail = details_map.get(pid, {})
        analysis = p_detail.get("analysis", {})
        rk = analysis.get("ranking", {})
        prices_history = p_detail.get("prices", [])
        reports = p_detail.get("reports", [])

        name = p_base.get("name", "")
        current_p = p_base.get("price", 0)
        inc_24h = p_base.get("priceIncrement", 0)
        pct_inc_24h = round(inc_24h / (current_p - inc_24h) * 100.0, 2) if (current_p - inc_24h) > 0 else 0.0

        # Demographics
        age = calculate_age(p_detail.get("birthday") or p_base.get("birthday"))
        c_code = str(p_detail.get("country", "")).upper()
        nationality = COUNTRY_MAP.get(c_code, c_code) if c_code else "Desconocida"

        # Price Curves & Temporal Variations
        min_1y = min((p[1] for p in prices_history), default=current_p) if prices_history else current_p
        max_1y = max((p[1] for p in prices_history), default=current_p) if prices_history else current_p

        recent_window = prices_history[-30:] if len(prices_history) >= 30 else prices_history
        season_floor = min((p[1] for p in recent_window), default=current_p) if recent_window else current_p
        season_gain = current_p - season_floor
        season_gain_pct = round((season_gain / season_floor * 100.0), 2) if season_floor > 0 else 0.0

        def get_past_p(days):
            if not prices_history: return current_p
            idx = max(0, len(prices_history) - 1 - days)
            return prices_history[idx][1]

        p_7d = get_past_p(7)
        p_14d = get_past_p(14)
        p_30d = get_past_p(30)

        diff_7d = current_p - p_7d
        pct_7d = round(diff_7d / p_7d * 100.0, 2) if p_7d > 0 else 0.0

        diff_14d = current_p - p_14d
        pct_14d = round(diff_14d / p_14d * 100.0, 2) if p_14d > 0 else 0.0

        diff_30d = current_p - p_30d
        pct_30d = round(diff_30d / p_30d * 100.0, 2) if p_30d > 0 else 0.0

        # Community Market Sentiment
        p_buy = analysis.get("purchases", 0)
        p_sell = analysis.get("sales", 0)
        p_use = analysis.get("owned", 0)
        net_press = (p_buy - p_sell) if (p_buy is not None and p_sell is not None) else None

        # Sporting Performance & Reports
        tot_goals = 0
        tot_assists = 0
        tot_mins = 0
        picas_list = []
        sofascore_list = []

        for rep in reports:
            raw_stats = rep.get("rawStats", {})
            tot_goals += int(raw_stats.get("goals", 0) or 0)
            tot_assists += int(raw_stats.get("assists", 0) or 0)
            tot_mins += int(raw_stats.get("minutesPlayed", 0) or 0)
            
            p_val = raw_stats.get("picas")
            if p_val is not None:
                try: picas_list.append(float(p_val))
                except (ValueError, TypeError): pass
            
            s_val = raw_stats.get("sofascore")
            if s_val is not None:
                try: sofascore_list.append(float(s_val))
                except (ValueError, TypeError): pass

        played_games = (p_base.get("playedHome", 0) or 0) + (p_base.get("playedAway", 0) or 0)
        points_tot = p_base.get("points", 0)
        avg_points = round(points_tot / played_games, 2) if played_games > 0 else 0.0
        avg_picas = round(sum(picas_list) / len(picas_list), 2) if picas_list else 0.0
        avg_sofascore = round(sum(sofascore_list) / len(sofascore_list), 2) if sofascore_list else 0.0

        fitness_list = p_base.get("fitness", [])
        fitness_str = ",".join(map(str, fitness_list)) if isinstance(fitness_list, list) else ""

        # Comuniate & League
        com_data = comuniate_map.get(name.lower(), {})
        owner_name = league_owners.get(pid, "Libre")
        clause_val = league_clauses.get(pid)
        in_market = "SÍ" if pid in market_sales_set else "NO"

        row = [
            today_str,
            pid,
            name,
            team_map.get(p_base.get("teamID"), "Sin Equipo"),
            POSITION_MAP.get(p_base.get("position"), f"Pos {p_base.get('position')}"),
            age,
            nationality,
            p_base.get("status", "ok"),
            # Mercado & Valor
            current_p,
            inc_24h,
            pct_inc_24h,
            min_1y,
            max_1y,
            season_gain,
            season_gain_pct,
            # Momentum
            diff_7d,
            pct_7d,
            diff_14d,
            pct_14d,
            diff_30d,
            pct_30d,
            # Sentimiento
            p_buy,
            p_sell,
            p_use,
            net_press,
            # Rankings
            rk.get("global"),
            rk.get("position"),
            rk.get("lastSeason"),
            # Rendimiento
            points_tot,
            p_base.get("pointsLastSeason", 0),
            played_games,
            avg_points,
            fitness_str,
            tot_goals,
            tot_assists,
            tot_mins,
            avg_picas,
            avg_sofascore,
            # Contexto
            com_data.get("titular"),
            com_data.get("duda"),
            owner_name,
            clause_val,
            in_market
        ]
        rows_to_append.append(row)

    # 5. Save to Google Sheets
    gc = get_gspread_client()
    if gc:
        try:
            print(f"☁️ Abriendo libro Google Sheets: {SPREADSHEET_ID}...")
            sh = gc.open_by_key(SPREADSHEET_ID)

            # Ensure tabs exist
            ws_titles = [w.title for w in sh.worksheets()]
            if TAB_HISTORICO not in ws_titles:
                ws_hist = sh.add_worksheet(title=TAB_HISTORICO, rows=10000, cols=len(HEADERS)+2)
                ws_hist.append_row(HEADERS)
            else:
                ws_hist = sh.worksheet(TAB_HISTORICO)

            if TAB_HOY not in ws_titles:
                ws_hoy = sh.add_worksheet(title=TAB_HOY, rows=1000, cols=len(HEADERS)+2)
                ws_hoy.append_row(HEADERS)
            else:
                ws_hoy = sh.worksheet(TAB_HOY)

            # 5.1 Append to Historico_Continuo
            ws_hist.append_rows(rows_to_append)
            print(f"☁️ Añadidos {len(rows_to_append)} registros completos a '{TAB_HISTORICO}'")

            # 5.2 Refresh Mercado_Hoy
            ws_hoy.clear()
            ws_hoy.append_row(HEADERS)
            ws_hoy.append_rows(rows_to_append)
            ws_hoy.freeze(rows=1)
            ws_hoy.format('A1:AN1', {
                'backgroundColor': {'red': 0.18, 'green': 0.45, 'blue': 0.25},
                'textFormat': {'bold': True, 'foregroundColor': {'red': 1.0, 'green': 1.0, 'blue': 1.0}}
            })
            print(f"☁️ Actualizada pestaña '{TAB_HOY}' con {len(rows_to_append)} registros del día")
        except Exception as e:
            print(f"⚠️ Error sincronizando con Google Sheets: {e}")
            raise e
    else:
        print("ℹ️ No se configuraron credenciales de Google Sheets, omitiendo subida a la nube.")

    # 6. Save local CSVs
    os.makedirs("data/history/snapshots", exist_ok=True)
    daily_snapshot_file = f"data/history/snapshots/{today_str}.csv"
    local_history_path = "data/history/market_sentiment_timeseries.csv"

    df_today = pd.DataFrame(rows_to_append, columns=HEADERS)
    df_today.to_csv(daily_snapshot_file, index=False, encoding="utf-8-sig")

    if os.path.exists(local_history_path):
        df_today.to_csv(local_history_path, mode="a", header=False, index=False, encoding="utf-8-sig")
    else:
        df_today.to_csv(local_history_path, mode="w", header=True, index=False, encoding="utf-8-sig")

    print(f"💾 Guardados backups locales ({len(rows_to_append)} registros)")
    print(f"⏱️ Proceso completado con éxito en {time.time() - t0:.2f} segundos.")
    return True

if __name__ == "__main__":
    run_daily_market_capture()
