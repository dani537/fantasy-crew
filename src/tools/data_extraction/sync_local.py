"""
Cloud-to-Local Warehouse & Live Metadata Synchronizer (`sync_local.py`)
=======================================================================
Synchronizes all updated worksheets from Google Sheets (`fantasy_tracker`) directly into
the local SQLite Data Warehouse (`data/warehouse/biwenger_history.db`) and CSV mirrors,
plus fetches a lightweight 2-call live snapshot from Biwenger API (`/league/{id}?fields=*,settings`
and `/user?fields=*,players(*,owner),lineup,market,offers`) to hydrate exact league rules,
real purchase prices, active firm offers (with `offer_id`), and `clauseLockedUntil` timestamps.

Usage:
    .venv/bin/python -m src.tools.data_extraction.sync_local
"""

import io
import os
import json
import argparse
from typing import Dict, List, Optional, Any
import pandas as pd

from src.config import Credentials, GoogleSheetsConfig
from src.tools.data_extraction.warehouse import BiwengerWarehouse
from src.tools.strategy_engine.league_rules import LeagueRulesEngine, SETTINGS_CACHE_PATH


WAREHOUSE_TAB_PKS: Dict[str, List[str]] = {
    "Historico_Continuo": ["fecha", "player_id"],
    "Historico_Equipos_Diario": ["fecha", "user_id"],
    "Mercado_Diario_Historico": ["id_registro"],
    "Movimientos": ["id_unico"],
    "Pujas_Historico": ["id_puja"],
    "Alineaciones_Jornadas": ["round_id", "user_id"],
    "Rendimiento_Jornadas": ["match_id", "player_id"],
    "Primas_Jornadas": ["Jornada", "User ID"],
    "Partidos_Calendario": ["round_id", "match_id"],
    "Equipos_LaLiga": ["team_id"],
    "Comuniate_Historico": ["id_jornada", "equipo", "nombre"],
}

MEMORY_TAB_PKS: Dict[str, List[str]] = {
    "Tesis_Activos": ["player_id"],
    "Memoria_Estrategica": ["entry_id"],
    "Decisiones_Pendientes": ["decision_id"],
    "Eventos_Jornada": ["event_key"],
}

PULSE_LIGHT_TAB_PKS: Dict[str, List[str]] = {
    "Partidos_Calendario": ["round_id", "match_id"],
    "Tesis_Activos": ["player_id"],
    "Memoria_Estrategica": ["entry_id"],
    "Decisiones_Pendientes": ["decision_id"],
    "Eventos_Jornada": ["event_key"],
}


def _values_to_typed_df(values: List[List[Any]]) -> pd.DataFrame:
    """Converts raw gspread string matrix to a pandas DataFrame with clean CSV-equivalent type inference."""
    if not values or len(values) < 2:
        return pd.DataFrame(columns=values[0] if values else [])
    headers = [str(h).strip() for h in values[0]]
    n_cols = len(headers)
    normalized_rows = []
    for row in values[1:]:
        if not any(str(c).strip() for c in row):
            continue
        padded = list(row[:n_cols]) + [""] * max(0, n_cols - len(row))
        normalized_rows.append(padded)
    if not normalized_rows:
        return pd.DataFrame(columns=headers)

    raw_df = pd.DataFrame(normalized_rows, columns=headers)
    buf = io.StringIO()
    raw_df.to_csv(buf, index=False)
    buf.seek(0)
    return pd.read_csv(buf, low_memory=False)


def refresh_live_biwenger_metadata() -> Dict[str, Any]:
    """
    Executes 2 fast read-only API calls (< 2s) to cache:
      - `./data/raw/live_league_settings.json` (official league settings)
      - `./data/raw/live_league_profile.json` (active user & league identity)
      - `./data/raw/live_user_state.json` (balance, squad purchase prices, clause locks, offers, listings)
      - `./data/user_info.csv`
    """
    if not (Credentials.BIWENGER_TOKEN or (Credentials.BIWENGER_USERNAME and Credentials.BIWENGER_PASSWORD)):
        return {}

    from src.tools.data_extraction.auth import BiwengerAuth
    auth = BiwengerAuth(
        email=Credentials.BIWENGER_USERNAME,
        password=Credentials.BIWENGER_PASSWORD,
        token=Credentials.BIWENGER_TOKEN,
    )
    auth.run()
    pinfo = auth.player_info
    if not pinfo:
        return {}

    lid = Credentials.BIWENGER_LEAGUE_ID or pinfo.league_id
    uid = Credentials.BIWENGER_USER_ID or pinfo.team_id
    headers = {
        "Authorization": f"Bearer {auth.token}",
        "X-League": str(lid),
        "X-User": str(uid),
        "X-Lang": "es",
        "X-Version": "640",
        "Accept": "application/json",
    }

    os.makedirs("./data/raw", exist_ok=True)

    # 1. Official League Settings & Standings
    r_lg = auth.session.get(
        f"https://biwenger.as.com/api/v2/league/{lid}?fields=*,settings,standings,rounds",
        headers=headers,
        timeout=15,
    )
    league_data = r_lg.json().get("data", {}) if r_lg.status_code == 200 else {}
    if league_data:
        rules = LeagueRulesEngine.from_api_payload(league_data)
        rules.save_cache(league_data, SETTINGS_CACHE_PATH)

    # 2. Live User Balance, Squad Purchase Prices, Clause Locks, Offers & Market Listings
    r_us = auth.session.get(
        "https://biwenger.as.com/api/v2/user?fields=*,account(id),players(*,fitness,team,owner),lineup,market,offers",
        headers=headers,
        timeout=15,
    )
    user_data = r_us.json().get("data", {}) if r_us.status_code == 200 else {}
    live_balance = float(user_data.get("balance", pinfo.balance or 0.0))

    profile_payload = {
        "user_id": int(pinfo.user_id),
        "user_name": str(pinfo.user_name),
        "league_id": int(lid),
        "league_name": str(league_data.get("name") or pinfo.league_name),
        "score_id": int(league_data.get("scoreID") or 5),
        "team_id": int(uid),
        "team_name": str(user_data.get("name") or pinfo.team_name),
        "balance": live_balance,
    }
    with open("./data/raw/live_league_profile.json", "w", encoding="utf-8") as f:
        json.dump(profile_payload, f, ensure_ascii=False, indent=2)

    if user_data:
        with open("./data/raw/live_user_state.json", "w", encoding="utf-8") as f:
            json.dump(user_data, f, ensure_ascii=False, indent=2)

    df_u = pd.DataFrame([{
        "user_id": profile_payload["user_id"],
        "user_name": profile_payload["user_name"],
        "league_id": profile_payload["league_id"],
        "league_name": profile_payload["league_name"],
        "team_id": profile_payload["team_id"],
        "team_name": profile_payload["team_name"],
        "balance": int(round(live_balance)),
    }])
    df_u.to_csv("./data/user_info.csv", index=False)
    print(
        f"✅ Metadatos vivos sincronizados: {profile_payload['team_name']} en '{profile_payload['league_name']}' "
        f"(Saldo: {int(round(live_balance)):,} €, Jugadores: {len(user_data.get('players', []))})"
    )
    return {"profile": profile_payload, "league_data": league_data, "user_data": user_data}


def sync_sheets_to_local(
    refresh_live_api: bool = True,
    memory_only: bool = False,
    pulse_light: bool = False,
) -> Dict[str, int]:
    """Downloads operational, historical, and stateful v3 memory tables from Google Sheets to local SQLite & CSVs."""
    wh = BiwengerWarehouse()
    synced_counts: Dict[str, int] = {}

    if memory_only:
        target_tabs = MEMORY_TAB_PKS
    elif pulse_light:
        target_tabs = PULSE_LIGHT_TAB_PKS
    else:
        target_tabs = {**WAREHOUSE_TAB_PKS, **MEMORY_TAB_PKS}

    sh = wh.spreadsheet
    if sh is None:
        print("ℹ️ Google Sheets no configurado o deshabilitado; manteniendo almacén SQLite local.")
    else:
        mode_lbl = "memoria persistente" if memory_only else ("pulso ligero" if pulse_light else "completo")
        print(f"☁️ Conectado a Google Sheets '{sh.title}' (modo {mode_lbl}). Descargando tablas...")
        existing_ws = {ws.title: ws for ws in sh.worksheets()}

        for tab_name, pk_cols in target_tabs.items():
            ws = existing_ws.get(tab_name)
            if ws is None:
                continue
            vals = ws.get_all_values()
            df = _values_to_typed_df(vals)
            if not df.empty:
                wh.upsert_table(tab_name, df, pk_cols=pk_cols, replace_all=True)
                synced_counts[tab_name] = len(df)
                print(f"  ✓ {tab_name:26s}: {len(df):6,d} filas -> SQLite + CSV")

                if tab_name == "Historico_Continuo":
                    os.makedirs("./data/history", exist_ok=True)
                    df.to_csv("./data/history/market_sentiment_timeseries.csv", index=False)

        if not memory_only and not pulse_light:
            # Also sync Predicciones_IA if available
            if "Predicciones_IA" in existing_ws:
                df_pred = _values_to_typed_df(existing_ws["Predicciones_IA"].get_all_values())
                if not df_pred.empty:
                    os.makedirs("./data/predictions", exist_ok=True)
                    df_pred.to_csv("./data/predictions/predicciones_mercado_hoy.csv", index=False, encoding="utf-8-sig")
                    synced_counts["Predicciones_IA"] = len(df_pred)
                    print(f"  ✓ {'Predicciones_IA':26s}: {len(df_pred):6,d} filas -> data/predictions/predicciones_mercado_hoy.csv")

            if "Saldos_Estimados" in existing_ws:
                df_saldos = _values_to_typed_df(existing_ws["Saldos_Estimados"].get_all_values())
                if not df_saldos.empty:
                    df_saldos.to_csv("./data/rival_financials.csv", index=False)
                    synced_counts["Saldos_Estimados"] = len(df_saldos)

    if refresh_live_api:
        try:
            refresh_live_biwenger_metadata()
        except Exception as e:
            print(f"⚠️ Aviso al refrescar metadatos vivos de API: {e}")

    return synced_counts


def main():
    parser = argparse.ArgumentParser(description="Synchronize Cloud Google Sheets & Live League Rules to Local Warehouse")
    parser.add_argument("--no-api", action="store_true", help="Skip 2-request live Biwenger metadata refresh")
    parser.add_argument("--memory-only", action="store_true", help="Sync only stateful v3 memory tables")
    parser.add_argument("--pulse-light", action="store_true", help="Fast <2s sync of calendar + memory tables for 30-min tactical pulse")
    args = parser.parse_args()
    counts = sync_sheets_to_local(
        refresh_live_api=not args.no_api,
        memory_only=args.memory_only,
        pulse_light=args.pulse_light,
    )
    print(f"\n🏁 Sincronización local completada ({len(counts)} tablas actualizadas).")


if __name__ == "__main__":
    main()
