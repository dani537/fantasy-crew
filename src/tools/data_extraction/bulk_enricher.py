import os
import time
import random
import datetime
import requests
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed

POSITION_MAP = {
    1: "Portero",
    2: "Defensa",
    3: "Centrocampista",
    4: "Delantero"
}

COUNTRY_MAP = {
    "ES": "España", "DO": "República Dominicana", "FR": "Francia", "BR": "Brasil",
    "AR": "Argentina", "PT": "Portugal", "UY": "Uruguay", "CO": "Colombia",
    "DE": "Alemania", "IT": "Italia", "NL": "Países Bajos", "BE": "Bélgica",
    "GB": "Reino Unido", "EN": "Inglaterra", "SN": "Senegal", "MA": "Marruecos",
    "DZ": "Argelia", "NG": "Nigeria", "GH": "Ghana", "CM": "Camerún",
    "CI": "Costa de Marfil", "CL": "Chile", "EC": "Ecuador", "PY": "Paraguay",
    "VE": "Venezuela", "MX": "México", "US": "Estados Unidos", "JP": "Japón",
    "KR": "Corea del Sur", "HR": "Croacia", "RS": "Serbia", "DK": "Dinamarca",
    "SE": "Suecia", "NO": "Noruega", "PL": "Polonia", "UA": "Ucrania",
    "CZ": "República Checa", "AT": "Austria", "CH": "Suiza", "TR": "Turquía",
    "GR": "Grecia", "RO": "Rumanía", "GE": "Georgia"
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"
]

def get_random_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "es-ES,es;q=0.9,ca;q=0.8",
        "Origin": "https://biwenger.as.com",
        "Referer": "https://biwenger.as.com/"
    }

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

def fetch_player_detail_light(player_id: int):
    # Polite human-speed delay per request
    time.sleep(random.uniform(0.12, 0.25))
    url = f"https://cf.biwenger.com/api/v2/players/la-liga/{player_id}?fields=id,name,birthday,country,analysis,prices,reports"
    for attempt in range(3):
        try:
            r = requests.get(url, headers=get_random_headers(), timeout=8)
            if r.status_code == 200:
                return player_id, r.json().get("data", {})
            elif r.status_code == 429:
                time.sleep(2.0 + attempt * 2.0)
        except Exception:
            time.sleep(1.0)
    return player_id, None

def run_bulk_player_extraction(max_workers: int = 4):
    print("🚀 INICIANDO EXTRACCIÓN MASIVA DE JUGADORES (100% ANÓNIMO Y SEGURO)...")
    start_time = time.time()

    # Step 1: Fetch global competition data (1 request)
    t0_comp = time.time()
    comp_url = "https://cf.biwenger.com/api/v2/competitions/la-liga/data?score=5"
    
    r_comp = None
    for attempt in range(5):
        try:
            r_comp = requests.get(comp_url, headers=get_random_headers(), timeout=10)
            if r_comp.status_code == 200:
                break
            elif r_comp.status_code == 429:
                print(f"⏳ Esperando enfriamiento de rate-limit ({attempt+1}/5)...")
                time.sleep(5.0)
        except Exception:
            time.sleep(2.0)

    if not r_comp or r_comp.status_code != 200:
        raise RuntimeError(f"Error fetching competition data: {r_comp.status_code if r_comp else 'No response'}")
    
    comp_data = r_comp.json().get("data", {})
    raw_players = comp_data.get("players", {})
    raw_teams = comp_data.get("teams", {})
    team_map = {int(tid): t["name"] for tid, t in raw_teams.items()}
    total_players = len(raw_players)
    print(f"✅ Obtenidos {total_players} jugadores y {len(raw_teams)} equipos de LaLiga en {time.time() - t0_comp:.2f}s.")

    # Step 2: Concurrently fetch light details for all players
    print(f"⏳ Extrayendo datos complementarios (mercado, historial de precios, informes) con {max_workers} hilos...")
    player_ids = [int(pid) for pid in raw_players.keys()]
    player_details_map = {}
    completed_count = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_player_detail_light, pid): pid for pid in player_ids}
        for future in as_completed(futures):
            pid, data = future.result()
            if data:
                player_details_map[pid] = data
            completed_count += 1
            if completed_count % 50 == 0 or completed_count == total_players:
                elapsed = time.time() - start_time
                print(f"   Progreso: {completed_count}/{total_players} jugadores ({completed_count/total_players*100:.1f}%) — {elapsed:.1f}s transcurridos")

    total_time = time.time() - start_time
    success_count = len(player_details_map)
    print(f"\n🎉 EXTRACCIÓN COMPLETADA: {success_count}/{total_players} jugadores en {total_time:.2f} segundos ({total_players/total_time:.1f} jugadores/segundo)!")

    # Step 3: Process and build rich DataFrame
    print("⏳ Procesando métricas temporales, sentimiento de mercado y rendimiento...")
    rows = []

    comuniate_map = {}
    if os.path.exists("data/raw/comuniate.csv"):
        try:
            df_com = pd.read_csv("data/raw/comuniate.csv")
            for _, r in df_com.iterrows():
                pname = str(r.get("nombre", "")).strip().lower()
                comuniate_map[pname] = {
                    "COMUNIATE_TITULAR": r.get("titularidad"),
                    "COMUNIATE_SUPLENTE": r.get("suplente"),
                    "COMUNIATE_DUDA": r.get("duda"),
                    "COMUNIATE_APERCIBIDO": r.get("apercibido")
                }
        except Exception:
            pass

    for pid_str, p_base in raw_players.items():
        pid = int(pid_str)
        p_detail = player_details_map.get(pid, {})

        # Base fields
        name = p_base.get("name", "")
        slug = p_base.get("slug", "")
        pos_id = p_base.get("position", 0)
        pos_name = POSITION_MAP.get(pos_id, f"Posición {pos_id}")
        team_id = p_base.get("teamID")
        team_name = team_map.get(team_id, "Sin Equipo")
        current_price = p_base.get("price", 0)
        price_inc_24h = p_base.get("priceIncrement", 0)
        status = p_base.get("status", "ok")
        fitness_list = p_base.get("fitness", [])
        fitness_str = ",".join(map(str, fitness_list)) if isinstance(fitness_list, list) else ""
        points_total = p_base.get("points", 0)
        points_home = p_base.get("pointsHome", 0) or 0
        points_away = p_base.get("pointsAway", 0) or 0
        played_home = p_base.get("playedHome", 0) or 0
        played_away = p_base.get("playedAway", 0) or 0
        games_played_total = played_home + played_away
        points_last_season = p_base.get("pointsLastSeason", 0)

        # Detailed analysis & market sentiment
        analysis = p_detail.get("analysis", {})
        pct_compras = analysis.get("purchases", 0) or 0
        pct_ventas = analysis.get("sales", 0) or 0
        pct_uso = analysis.get("owned", 0) or 0
        net_market_pressure = pct_compras - pct_ventas

        rankings = analysis.get("ranking", {})
        ranking_global = rankings.get("global")
        ranking_posicion = rankings.get("position")
        ranking_last_season = rankings.get("lastSeason")

        # Demographics
        age = calculate_age(p_detail.get("birthday") or p_base.get("birthday"))
        c_code = p_detail.get("country")
        nationality = COUNTRY_MAP.get(str(c_code).upper(), c_code) if c_code else "Desconocida"

        # Price history calculations
        prices_history = p_detail.get("prices", [])
        min_1y = min((p[1] for p in prices_history), default=current_price) if prices_history else current_price
        max_1y = max((p[1] for p in prices_history), default=current_price) if prices_history else current_price

        def get_past_price(days_back):
            if not prices_history:
                return current_price
            idx = max(0, len(prices_history) - 1 - days_back)
            return prices_history[idx][1]

        p_1d = get_past_price(1)
        p_7d = get_past_price(7)
        p_14d = get_past_price(14)
        p_30d = get_past_price(30)
        p_90d = get_past_price(90)
        p_1y = get_past_price(365)

        diff_1d = current_price - p_1d
        pct_1d = round((diff_1d / p_1d * 100.0), 2) if p_1d > 0 else 0.0

        diff_7d = current_price - p_7d
        pct_7d = round((diff_7d / p_7d * 100.0), 2) if p_7d > 0 else 0.0

        diff_14d = current_price - p_14d
        pct_14d = round((diff_14d / p_14d * 100.0), 2) if p_14d > 0 else 0.0

        diff_30d = current_price - p_30d
        pct_30d = round((diff_30d / p_30d * 100.0), 2) if p_30d > 0 else 0.0

        diff_90d = current_price - p_90d
        pct_90d = round((diff_90d / p_90d * 100.0), 2) if p_90d > 0 else 0.0

        diff_1y = current_price - p_1y
        pct_1y = round((diff_1y / p_1y * 100.0), 2) if p_1y > 0 else 0.0

        # Season gain from recent minimum floor
        recent_window = prices_history[-30:] if len(prices_history) >= 30 else prices_history
        season_floor = min((p[1] for p in recent_window), default=current_price) if recent_window else current_price
        season_gain = current_price - season_floor
        season_gain_pct = round((season_gain / season_floor * 100.0), 2) if season_floor > 0 else 0.0

        # Reports & match stats
        reports = p_detail.get("reports", [])
        total_goals = 0
        total_assists = 0
        total_yellows = 0
        total_reds = 0
        total_mins = 0
        picas_list = []
        sofascore_list = []

        for rep in reports:
            raw_stats = rep.get("rawStats", {})
            total_goals += int(raw_stats.get("goals", 0) or 0)
            total_assists += int(raw_stats.get("assists", 0) or 0)
            total_yellows += int(raw_stats.get("yellowCard", 0) or 0)
            total_reds += int(raw_stats.get("redCard", 0) or 0)
            total_mins += int(raw_stats.get("minutesPlayed", 0) or 0)
            
            picas_val = raw_stats.get("picas")
            if picas_val is not None:
                try:
                    picas_list.append(float(picas_val))
                except (ValueError, TypeError):
                    pass
            
            sofascore_val = raw_stats.get("sofascore")
            if sofascore_val is not None:
                try:
                    sofascore_list.append(float(sofascore_val))
                except (ValueError, TypeError):
                    pass

        avg_points = round(points_total / games_played_total, 2) if games_played_total > 0 else 0.0
        avg_picas = round(sum(picas_list) / len(picas_list), 2) if picas_list else 0.0
        avg_sofascore = round(sum(sofascore_list) / len(sofascore_list), 2) if sofascore_list else 0.0

        com_info = comuniate_map.get(name.lower(), {})

        rows.append({
            "PLAYER_ID": pid,
            "PLAYER_NAME": name,
            "PLAYER_SLUG": slug,
            "TEAM_NAME": team_name,
            "POSITION": pos_name,
            "AGE": age,
            "NATIONALITY": nationality,
            "STATUS": status,
            # Market & Value
            "PRICE": current_price,
            "PRICE_INCREMENT_24H": price_inc_24h,
            "PCT_INC_24H": pct_1d,
            "MIN_PRICE_1Y": min_1y,
            "MAX_PRICE_1Y": max_1y,
            "SEASON_FLOOR": season_floor,
            "SEASON_GAIN": season_gain,
            "SEASON_GAIN_PCT": season_gain_pct,
            # Historical Variations
            "PRICE_7D_AGO": p_7d,
            "DIFF_7D": diff_7d,
            "PCT_7D": pct_7d,
            "PRICE_14D_AGO": p_14d,
            "DIFF_14D": diff_14d,
            "PCT_14D": pct_14d,
            "PRICE_30D_AGO": p_30d,
            "DIFF_30D": diff_30d,
            "PCT_30D": pct_30d,
            "PRICE_90D_AGO": p_90d,
            "DIFF_90D": diff_90d,
            "PCT_90D": pct_90d,
            "PRICE_1Y_AGO": p_1y,
            "DIFF_1Y": diff_1y,
            "PCT_1Y": pct_1y,
            # Market Sentiment (Community)
            "PCT_COMPRAS_24H": pct_compras,
            "PCT_VENTAS_24H": pct_ventas,
            "PCT_USO_LIGAS": pct_uso,
            "NET_MARKET_PRESSURE": net_market_pressure,
            # Rankings
            "RANKING_GLOBAL": ranking_global,
            "RANKING_POSICION": ranking_posicion,
            "RANKING_LAST_SEASON": ranking_last_season,
            # Performance Stats
            "POINTS_TOTAL": points_total,
            "POINTS_LAST_SEASON": points_last_season,
            "GAMES_PLAYED": games_played_total,
            "AVG_POINTS_PER_GAME": avg_points,
            "FITNESS_RECENT": fitness_str,
            "GOALS": total_goals,
            "ASSISTS": total_assists,
            "MINUTES_PLAYED": total_mins,
            "YELLOW_CARDS": total_yellows,
            "RED_CARDS": total_reds,
            "AVG_PICAS": avg_picas,
            "AVG_SOFASCORE": avg_sofascore,
            # Tactical Context
            "COMUNIATE_STARTER": com_info.get("COMUNIATE_TITULAR"),
            "COMUNIATE_DOUBT": com_info.get("COMUNIATE_DUDA"),
        })

    df_result = pd.DataFrame(rows)
    
    csv_path = "data/test_full_players_dataset.csv"
    xlsx_path = "data/test_full_players_dataset.xlsx"
    df_result.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df_result.to_excel(xlsx_path, index=False)

    print(f"\n💾 Guardados datasets completos en:\n   - {csv_path} ({os.path.getsize(csv_path)/1024:.1f} KB)\n   - {xlsx_path} ({os.path.getsize(xlsx_path)/1024:.1f} KB)")
    return df_result, total_time

if __name__ == "__main__":
    df, duration = run_bulk_player_extraction(max_workers=4)
    print("\n📊 TOP 10 JUGADORES CON MAYOR PRESIÓN COMPRADORA NETA (COMPRAS - VENTAS):")
    top_demand = df.sort_values(by="NET_MARKET_PRESSURE", ascending=False)[["PLAYER_NAME", "TEAM_NAME", "PRICE", "PRICE_INCREMENT_24H", "PCT_COMPRAS_24H", "PCT_VENTAS_24H", "NET_MARKET_PRESSURE", "PCT_USO_LIGAS", "RANKING_GLOBAL"]].head(10)
    print(top_demand.to_string(index=False))
