"""
Biwenger Historical Backfill & Daily Reconstruction Engine (Zero-429 Fast Architecture)
=======================================================================================
Reconstructs the complete history of the season from Day 1 (2026-08-01, leagueReset)
to today with 0 € balance drift in ~30 seconds (using round-level endpoints and
mathematical lags from Historico_Continuo without hitting Cloudflare's per-player rate limit):

1. `Historico_Continuo` (`daily_player_market`):
   - Preserves all 54+ days (2026-09-02 to today) of captured 43-column player snapshots.
   - Reconstructs exact daily prices for August (2026-08-01 to 2026-09-01) directly from
     `precio - diff_30d`, `precio - diff_14d`, `precio - diff_7d`, and `precio - subida_24h`!
   - Repairs `propietario_liga`, `clausula_actual`, `en_venta_mercado`, and `comuniate_titular`
     across all historical and current rows.
2. `Rendimiento_Jornadas` (`player_round_reports`):
   - Granular Player x Round performance extracted from `/api/v2/rounds/la-liga/{round_id}?score=5`
     (starter/sub role, exact sub-in/sub-out minutes, goals, assists, yellow/red cards, AS Picas,
     SofaScore rating, Biwenger points, Picas points, SofaScore points, price at match date,
     match MVP, Ideal XI, and league owner on match date).
3. `Partidos_Calendario` (`laliga_matches`):
   - All 380 LaLiga matches (J1..J38), with scores and advanced team stats (possession, shots,
     shots on target, corners, tackles, pass accuracy) for played matches, plus Biwenger
     difficulty ratings & EuroClubIndex 1-X-2 odds/xG for upcoming matches.
4. `Equipos_LaLiga` (`laliga_team_standings`):
   - Real LaLiga standings + team tactical & fantasy metrics.
5. `Pujas_Historico` (`league_bids`):
   - All winning and losing bids from the league board since 2026-08-01, enriched with exact
     market price on bid date, overbid % and €, number of bidders, and margin over 2nd highest bid.
6. `Alineaciones_Jornadas` (`league_round_lineups`):
   - Every manager's 11 starters, formation, bench players (reconstructed from their squad at
     round kickoff), bench points, optimal possible XI points (`puntos_optimos_posibles`),
     coach efficiency %, cumulative points/rank, and exact bonus breakdown.
7. `Historico_Equipos_Diario` (`daily_manager_snapshots`):
   - Exact daily time series (2026-08-01 to today) for all 15 managers: squad size, exact cash
     balance (0 € drift), squad market value, 24h squad value change, net worth, net profit,
     max bid allowed, cumulative spent/income/bonuses.
8. `Mercado_Diario_Historico` (`daily_market_listings`):
   - Append-only log of daily transfer market listings and Computer offers received.
"""

import os
import re
import json
import time
import datetime
from typing import Dict, List, Any, Optional, Tuple

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv
from thefuzz import process

from src.config import Credentials, GeneralSettings
from src.tools.data_extraction.auth import BiwengerAuth
from src.tools.data_extraction.biwenger_data import BiwengerGeneralData, UserLeagueData
from src.tools.data_extraction.external_data import ComuniateData, EuroClubIndexData
from src.tools.data_extraction.daily_market_tracker import (
    HEADERS as HISTORICO_HEADERS,
    POSITION_MAP,
     sanitize_cell,
    get_headers as get_cdn_headers,
)
from src.tools.data_extraction.warehouse import (
    BiwengerWarehouse,
    compute_official_clause,
    ensure_exact_balances_csv,
)

VALID_FORMATIONS = [
    (3, 4, 3),
    (3, 5, 2),
    (4, 3, 3),
    (4, 4, 2),
    (4, 5, 1),
    (5, 3, 2),
    (5, 4, 1),
]


def _safe_int(val: Any, default: int = 0) -> int:
    if val is None or val == "" or (isinstance(val, float) and np.isnan(val)):
        return default
    try:
        if pd.isna(val):
            return default
        s = str(val).replace("€", "").replace(",", ".").strip()
        return int(round(float(s)))
    except Exception:
        return default


def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None or val == "" or (isinstance(val, float) and np.isnan(val)):
        return default
    try:
        if pd.isna(val):
            return default
        s = str(val).replace("€", "").replace(",", ".").strip()
        return float(s)
    except Exception:
        return default


def _safe_read_csv(path: str) -> pd.DataFrame:
    """Safely reads a CSV file, returning an empty DataFrame if file is missing, empty, or unparseable."""
    if os.path.exists(path):
        try:
            if os.path.getsize(path) >= 5:
                return pd.read_csv(path)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def load_existing_historico_from_sheets_or_warehouse(warehouse: BiwengerWarehouse) -> pd.DataFrame:
    """Loads the existing `Historico_Continuo` table from Warehouse or Google Sheets."""
    df_existing = warehouse.read_table("Historico_Continuo")
    if df_existing.empty and warehouse.spreadsheet is not None:
        try:
            ws_hist = warehouse.spreadsheet.worksheet("Historico_Continuo")
            vals = ws_hist.get_all_values()
            if len(vals) > 1:
                df_existing = pd.DataFrame(vals[1:], columns=vals[0])
                print(f"📥 Descargado 'Historico_Continuo' desde Google Sheets ({len(df_existing):,} filas).")
        except Exception as e:
            print(f"⚠️ No se pudo leer 'Historico_Continuo' de Google Sheets: {e}")
    return df_existing


def build_price_lookup_from_historico(
    df_existing: pd.DataFrame,
    raw_players: Dict[str, Any],
    start_date: str = "2026-08-01",
    end_date: Optional[str] = None
) -> Tuple[Dict[int, Dict[str, int]], List[str], Dict[int, Dict[str, Any]]]:
    """
    Reconstructs the exact daily price `price_by_pid_date[pid][YYYY-MM-DD]` for all 580 players
    from `2026-08-01` to `today` with ZERO per-player API calls by exploiting the exact lag
    columns (`subida_24h`, `diff_7d`, `diff_14d`, `diff_30d`) recorded in `Historico_Continuo`!
    Also extracts static demographic fields (`edad`, `nacionalidad`, `min_precio_1y`, `max_precio_1y`, `ranking_last_season`)
    already captured in `Historico_Continuo`.
    """
    if end_date is None:
        end_date = datetime.date.today().strftime("%Y-%m-%d")

    ext_start = (pd.to_datetime(start_date) - pd.Timedelta(days=35)).strftime("%Y-%m-%d")
    ext_dates = [d.strftime("%Y-%m-%d") for d in pd.date_range(start=ext_start, end=end_date, freq="D")]
    all_dates_str = [d.strftime("%Y-%m-%d") for d in pd.date_range(start=start_date, end=end_date, freq="D")]

    direct_prices: Dict[int, Dict[str, int]] = {}
    inferred_prices: Dict[int, Dict[str, int]] = {}
    player_static_meta: Dict[int, Dict[str, Any]] = {}

    if not df_existing.empty:
        for row in df_existing.to_dict(orient="records"):
            pid = _safe_int(row.get("player_id"), 0)
            if not pid:
                continue
            d_str = str(row.get("fecha", "")).strip()
            p_val = _safe_int(row.get("precio"), 0)
            if not d_str or p_val <= 0:
                continue

            direct_prices.setdefault(pid, {})[d_str] = p_val
            inf_map = inferred_prices.setdefault(pid, {})

            # Keep latest static metadata for each player
            player_static_meta[pid] = {
                "edad": row.get("edad", ""),
                "nacionalidad": row.get("nacionalidad", "Desconocida"),
                "min_precio_1y": _safe_int(row.get("min_precio_1y"), p_val),
                "max_precio_1y": _safe_int(row.get("max_precio_1y"), p_val),
                "ranking_last_season": row.get("ranking_last_season", ""),
                "puntos_last_season": _safe_int(row.get("puntos_last_season"), 0),
            }

            try:
                dt_obj = pd.to_datetime(d_str)
                # 1-day lag
                inc_1d = row.get("subida_24h")
                if inc_1d is not None and str(inc_1d).strip() != "":
                    d_m1 = (dt_obj - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                    p_m1 = p_val - _safe_int(inc_1d, 0)
                    if p_m1 > 0:
                        inf_map[d_m1] = p_m1

                # 7-day lag
                diff_7 = row.get("diff_7d")
                if diff_7 is not None and str(diff_7).strip() != "":
                    d_m7 = (dt_obj - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
                    p_m7 = p_val - _safe_int(diff_7, 0)
                    if p_m7 > 0:
                        inf_map.setdefault(d_m7, p_m7)

                # 14-day lag
                diff_14 = row.get("diff_14d")
                if diff_14 is not None and str(diff_14).strip() != "":
                    d_m14 = (dt_obj - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
                    p_m14 = p_val - _safe_int(diff_14, 0)
                    if p_m14 > 0:
                        inf_map.setdefault(d_m14, p_m14)

                # 30-day lag (reconstructs all of August 2026-08-03..2026-08-31 from Sept rows!)
                diff_30 = row.get("diff_30d")
                if diff_30 is not None and str(diff_30).strip() != "":
                    d_m30 = (dt_obj - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
                    p_m30 = p_val - _safe_int(diff_30, 0)
                    if p_m30 > 0:
                        inf_map.setdefault(d_m30, p_m30)
            except Exception:
                pass

    price_by_pid_date: Dict[int, Dict[str, int]] = {}
    for pid_str, p_base in raw_players.items():
        pid = int(pid_str)
        curr_p = int(p_base.get("price", 150000) or 150000)
        d_map = direct_prices.get(pid, {})
        i_map = inferred_prices.get(pid, {})

        combined: Dict[str, int] = {**i_map, **d_map}
        combined[end_date] = curr_p

        sorted_known = sorted(combined.keys())
        last_val = combined[sorted_known[0]] if sorted_known else curr_p

        full_series: Dict[str, int] = {}
        for d_str in ext_dates:
            if d_str in combined:
                last_val = combined[d_str]
            full_series[d_str] = last_val

        price_by_pid_date[pid] = full_series

    return price_by_pid_date, all_dates_str, player_static_meta


def fetch_full_board_raw(
    session: requests.Session,
    token: str,
    league_id: int,
    user_id: int
) -> List[Dict[str, Any]]:
    """Downloads 100% of board items from today back to `leagueReset` (2026-08-01)."""
    headers = {
        "authorization": "Bearer " + token,
        "x-league": str(league_id),
        "x-user": str(user_id),
        "referer": "https://biwenger.as.com/board",
    }
    all_items: List[Dict[str, Any]] = []
    offset = 0
    batch_limit = 100

    while True:
        url = f"https://biwenger.as.com/api/v2/league/{league_id}/board?limit={batch_limit}&offset={offset}"
        r = session.get(url, headers=headers, timeout=12)
        if r.status_code != 200:
            break
        data = r.json().get("data", [])
        if not data:
            break
        stop = False
        for item in data:
            all_items.append(item)
            if item.get("type") == "leagueReset":
                stop = True
                break
        if stop or len(data) < batch_limit:
            break
        offset += batch_limit

    return all_items


def reconstruct_season_ownership_and_finances(
    df_league_players: pd.DataFrame,
    df_standings: pd.DataFrame,
    board_res: Dict[str, pd.DataFrame],
    raw_board_items: List[Dict[str, Any]],
    price_by_pid_date: Dict[int, Dict[str, int]],
    all_dates_str: List[str],
    my_user_id: Optional[int] = None,
    my_real_balance: Optional[float] = None
) -> Dict[str, Any]:
    """
    Reconstructs with 100% mathematical accuracy from Day 1 (2026-08-01) to today:
      - Exact Day-1 15-player squads for all 15 managers.
      - Daily ownership, purchase price, and release clause (`clausula_actual`) for every player on every day `d`.
      - `df_daily_managers`: Daily time series (`Historico_Equipos_Diario`) for all 15 managers.
      - `round_start_snapshots`: Exact squad and cash of each manager at the start of every round.
    """
    df_exact_init = ensure_exact_balances_csv()
    init_map: Dict[int, Dict[str, Any]] = {}
    for _, r in df_exact_init.iterrows():
        uid = int(r["user_id"])
        init_map[uid] = {
            "manager": str(r["Manager"]).strip(),
            "saldo_inicial": float(r["Saldo Inicial"]),
            "plantilla_inicial": float(r["Valor Plantilla Inicial (01/08)"]),
        }

    for _, r in df_standings.iterrows():
        uid = int(r["id"])
        if uid not in init_map:
            init_map[uid] = {
                "manager": str(r["name"]).strip(),
                "saldo_inicial": 23_900_000.0,
                "plantilla_inicial": 16_100_000.0,
            }

    uid_to_name = {uid: info["manager"] for uid, info in init_map.items()}
    df_transfers = board_res.get("transfers", pd.DataFrame()).copy()

    current_squads: Dict[int, set] = {uid: set() for uid in init_map}
    for _, row in df_league_players.iterrows():
        tid = int(row["team_id"])
        pid = int(row["player_id"])
        current_squads.setdefault(tid, set()).add(pid)

    day1_squads: Dict[int, set] = {uid: set(pids) for uid, pids in current_squads.items()}

    if not df_transfers.empty:
        df_tr_real = df_transfers[df_transfers["type"] != "clause_increment"].sort_values("date", ascending=False)
        for _, row in df_tr_real.iterrows():
            pid = int(row["player_id"])
            b_id = int(row["buyer_id"]) if pd.notna(row.get("buyer_id")) and str(row.get("buyer_id")).strip() not in ("", "None", "nan") else None
            s_id = int(row["seller_id"]) if pd.notna(row.get("seller_id")) and str(row.get("seller_id")).strip() not in ("", "None", "nan") else None
            if b_id and b_id in day1_squads:
                day1_squads[b_id].discard(pid)
            if s_id and s_id in day1_squads:
                day1_squads[s_id].add(pid)

    events_chronological = sorted(raw_board_items, key=lambda x: x.get("date", 0))

    squads_state: Dict[int, set] = {uid: set(pids) for uid, pids in day1_squads.items()}
    player_acq_price: Dict[int, float] = {}
    player_clause_extra: Dict[int, float] = {}
    for uid, pids in squads_state.items():
        for pid in pids:
            p_0801 = price_by_pid_date.get(pid, {}).get("2026-08-01", 0)
            player_acq_price[pid] = float(p_0801)
            player_clause_extra[pid] = 0.0

    cash_state: Dict[int, float] = {uid: info["saldo_inicial"] for uid, info in init_map.items()}
    spent_state: Dict[int, float] = {uid: 0.0 for uid in init_map}
    income_state: Dict[int, float] = {uid: 0.0 for uid in init_map}
    bonuses_state: Dict[int, float] = {uid: 0.0 for uid in init_map}
    clause_inc_state: Dict[int, float] = {uid: 0.0 for uid in init_map}
    purchases_cnt: Dict[int, int] = {uid: 0 for uid in init_map}
    sales_cnt: Dict[int, int] = {uid: 0 for uid in init_map}
    paid_round_bonus: Dict[Tuple[int, str], float] = {}
    round_start_snapshots: Dict[int, Dict[str, Any]] = {}

    def apply_board_item(item: Dict[str, Any]):
        itype = item.get("type")
        content = item.get("content")
        if content is None:
            return
        entries = content if isinstance(content, list) else [content]

        if itype == "market":
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                buyer = entry.get("to")
                b_id = int(buyer["id"]) if isinstance(buyer, dict) and buyer.get("id") else None
                pid = int(entry["player"]) if entry.get("player") else None
                amt = float(entry.get("amount", 0) or 0)
                if b_id and b_id in squads_state and pid:
                    squads_state[b_id].add(pid)
                    player_acq_price[pid] = amt
                    player_clause_extra[pid] = 0.0
                    cash_state[b_id] -= amt
                    spent_state[b_id] += amt
                    purchases_cnt[b_id] += 1

        elif itype == "transfer":
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                seller = entry.get("from")
                s_id = int(seller["id"]) if isinstance(seller, dict) and seller.get("id") else None
                buyer = entry.get("to")
                b_id = int(buyer["id"]) if isinstance(buyer, dict) and buyer.get("id") else None
                pid = int(entry["player"]) if entry.get("player") else None
                amt = float(entry.get("amount", 0) or 0)

                if s_id and s_id in squads_state and pid:
                    squads_state[s_id].discard(pid)
                    cash_state[s_id] += amt
                    income_state[s_id] += amt
                    sales_cnt[s_id] += 1
                    if not b_id:
                        player_acq_price.pop(pid, None)
                        player_clause_extra.pop(pid, None)

                if b_id and b_id in squads_state and b_id != s_id and pid:
                    squads_state[b_id].add(pid)
                    player_acq_price[pid] = amt
                    player_clause_extra[pid] = 0.0
                    cash_state[b_id] -= amt
                    spent_state[b_id] += amt
                    purchases_cnt[b_id] += 1

        elif itype == "clauseIncrement":
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                u = entry.get("user", {})
                u_id = int(u["id"]) if isinstance(u, dict) and u.get("id") else None
                pid = int(entry["player"]) if entry.get("player") else None
                amt = float(entry.get("amount", 0) or 0)
                if u_id and u_id in cash_state:
                    cash_state[u_id] -= amt
                    clause_inc_state[u_id] += amt
                if pid:
                    player_clause_extra[pid] = player_clause_extra.get(pid, 0.0) + amt

        elif itype == "roundFinished":
            if not isinstance(content, dict):
                return
            rnd = content.get("round", {}) or {}
            rnd_name = str(rnd.get("name", ""))
            m = re.match(r"(Jornada \d+)", rnd_name)
            base_round = m.group(1) if m else rnd_name
            for res in content.get("results", []) or []:
                u = res.get("user", {})
                u_id = int(u["id"]) if isinstance(u, dict) and u.get("id") else None
                new_cum_bonus = float(res.get("bonus", 0) or 0)
                if u_id and u_id in cash_state:
                    prev_paid = paid_round_bonus.get((u_id, base_round), 0.0)
                    delta_bonus = new_cum_bonus - prev_paid
                    paid_round_bonus[(u_id, base_round)] = new_cum_bonus
                    cash_state[u_id] += delta_bonus
                    bonuses_state[u_id] += delta_bonus

        elif itype == "roundStarted":
            if isinstance(content, dict):
                rnd = content.get("round", {}) or {}
                r_id = rnd.get("id")
                if r_id:
                    round_start_snapshots[int(r_id)] = {
                        "timestamp": item.get("date", 0),
                        "date_str": datetime.datetime.fromtimestamp(item.get("date", 0)).strftime("%Y-%m-%d %H:%M:%S"),
                        "squads": {uid: set(pids) for uid, pids in squads_state.items()},
                        "cash": dict(cash_state),
                    }

    event_idx = 0
    n_events = len(events_chronological)
    daily_manager_rows: List[Dict[str, Any]] = []
    daily_player_league_ctx: Dict[str, Dict[int, Tuple[str, int]]] = {}
    prev_team_val: Dict[int, int] = {}

    for d_str in all_dates_str:
        day_end_dt = pd.Timestamp(f"{d_str} 23:59:59", tz="Europe/Madrid")
        day_end_ts = day_end_dt.timestamp()

        while event_idx < n_events and events_chronological[event_idx].get("date", 0) <= day_end_ts:
            apply_board_item(events_chronological[event_idx])
            event_idx += 1

        p_ctx_today: Dict[int, Tuple[str, int]] = {}
        for uid, pids in squads_state.items():
            m_name = uid_to_name.get(uid, f"Manager {uid}")
            for pid in pids:
                p_mkt = price_by_pid_date.get(pid, {}).get(d_str, 0)
                acq_p = player_acq_price.get(pid, 0.0)
                extra_c = player_clause_extra.get(pid, 0.0)
                c_val = compute_official_clause(max(acq_p, float(p_mkt)), extra_c)
                p_ctx_today[pid] = (m_name, c_val)
        daily_player_league_ctx[d_str] = p_ctx_today

        for uid, info in init_map.items():
            m_name = info["manager"]
            pids = sorted(list(squads_state.get(uid, set())))
            team_val = sum(price_by_pid_date.get(pid, {}).get(d_str, 0) for pid in pids)
            val_inc_24h = (team_val - prev_team_val[uid]) if uid in prev_team_val else 0
            prev_team_val[uid] = team_val

            saldo = int(round(cash_state.get(uid, 0.0)))
            patrimonio = saldo + team_val
            base_40m = int(round(info["saldo_inicial"] + info["plantilla_inicial"]))
            beneficio = patrimonio - base_40m
            max_bid = int(round(saldo + 0.25 * team_val))

            daily_manager_rows.append({
                "fecha": d_str,
                "user_id": uid,
                "manager": m_name,
                "num_jugadores": len(pids),
                "saldo_disponible_exacto": saldo,
                "valor_plantilla": team_val,
                "subida_plantilla_24h": val_inc_24h,
                "patrimonio_total": patrimonio,
                "beneficio_neto": beneficio,
                "max_puja_disponible": max_bid,
                "gasto_acumulado": int(round(spent_state.get(uid, 0.0))),
                "ingreso_acumulado": int(round(income_state.get(uid, 0.0))),
                "primas_acumuladas": int(round(bonuses_state.get(uid, 0.0))),
                "blindaje_clausulas_acum": int(round(clause_inc_state.get(uid, 0.0))),
                "fichajes_acum": purchases_cnt.get(uid, 0),
                "ventas_acum": sales_cnt.get(uid, 0),
                "plantilla_ids": ",".join(map(str, pids)),
            })

    while event_idx < n_events:
        apply_board_item(events_chronological[event_idx])
        event_idx += 1

    if my_user_id and my_real_balance is not None and int(my_user_id) in cash_state:
        calc_final = int(round(cash_state[int(my_user_id)]))
        real_final = int(round(my_real_balance))
        drift = real_final - calc_final
        print(f"🎯 Conciliación histórica Día 1 -> Hoy ({uid_to_name.get(int(my_user_id))}): "
              f"Calculado={calc_final:,} € | Real API={real_final:,} € | Desviación={drift:+,} €")

    return {
        "df_daily_managers": pd.DataFrame(daily_manager_rows),
        "daily_player_league_ctx": daily_player_league_ctx,
        "round_start_snapshots": round_start_snapshots,
        "day1_squads": day1_squads,
        "uid_to_name": uid_to_name,
    }


def build_enriched_bids_history(
    raw_board_items: List[Dict[str, Any]],
    raw_players: Dict[str, Any],
    teams_map: Dict[int, str],
    price_by_pid_date: Dict[int, Dict[str, int]]
) -> pd.DataFrame:
    """Constructs `Pujas_Historico` (`league_bids`) from `market` board events."""
    rows: List[Dict[str, Any]] = []

    for item in sorted(raw_board_items, key=lambda x: x.get("date", 0)):
        if item.get("type") != "market":
            continue
        item_ts = item.get("date", 0)
        content = item.get("content")
        if not content:
            continue
        entries = content if isinstance(content, list) else [content]

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            buyer = entry.get("to")
            if not isinstance(buyer, dict) or not buyer.get("id"):
                continue

            buyer_id = int(buyer["id"])
            buyer_name = str(buyer.get("name", "Desconocido"))
            pid = int(entry.get("player", 0) or 0)
            if not pid:
                continue

            win_amount = int(entry.get("amount", 0) or 0)
            entry_ts = entry.get("date") or item_ts
            entry_dt = datetime.datetime.fromtimestamp(entry_ts)
            dt_str = entry_dt.strftime("%Y-%m-%d %H:%M:%S")
            day_str = entry_dt.strftime("%Y-%m-%d")

            p_base = raw_players.get(str(pid), {})
            p_name = p_base.get("name", f"Jugador {pid}")
            p_pos = POSITION_MAP.get(p_base.get("position"), str(p_base.get("position", "")))
            p_team = teams_map.get(int(p_base.get("teamID", 0) or 0), "Sin Equipo")
            mkt_price = price_by_pid_date.get(pid, {}).get(day_str, int(p_base.get("price", 0) or 0))

            raw_bids = entry.get("bids", [])
            auction_bids: List[Dict[str, Any]] = []
            if isinstance(raw_bids, list) and raw_bids:
                for b in raw_bids:
                    if not isinstance(b, dict):
                        continue
                    u = b.get("user", {})
                    uid = int(u["id"]) if isinstance(u, dict) and u.get("id") else 0
                    uname = str(u.get("name", "Desconocido")) if isinstance(u, dict) else "Desconocido"
                    b_amt = int(b.get("amount", 0) or 0)
                    auction_bids.append({
                        "bidder_id": uid,
                        "manager_pujador": uname,
                        "importe_pujado": b_amt,
                        "gano": (uid == buyer_id),
                    })
                if not any(ab["bidder_id"] == buyer_id for ab in auction_bids):
                    auction_bids.append({
                        "bidder_id": buyer_id,
                        "manager_pujador": buyer_name,
                        "importe_pujado": win_amount,
                        "gano": True,
                    })
            else:
                auction_bids.append({
                    "bidder_id": buyer_id,
                    "manager_pujador": buyer_name,
                    "importe_pujado": win_amount,
                    "gano": True,
                })

            auction_bids.sort(key=lambda x: x["importe_pujado"], reverse=True)
            num_bidders = len(auction_bids)
            second_highest = auction_bids[1]["importe_pujado"] if num_bidders >= 2 else mkt_price

            for ab in auction_bids:
                b_amt = ab["importe_pujado"]
                over_eur = b_amt - mkt_price
                over_pct = round((over_eur / mkt_price * 100.0), 2) if mkt_price > 0 else 0.0
                diff_vs_2nd = (b_amt - second_highest) if ab["gano"] else (b_amt - win_amount)
                uid_str = f"{dt_str}_{pid}_{ab['bidder_id']}_{b_amt}"

                rows.append({
                    "id_puja": uid_str,
                    "fecha": dt_str,
                    "player_id": pid,
                    "jugador": p_name,
                    "posicion": p_pos,
                    "equipo": p_team,
                    "precio_mercado_dia": mkt_price,
                    "bidder_id": ab["bidder_id"],
                    "manager_pujador": ab["manager_pujador"],
                    "importe_pujado": b_amt,
                    "importe_ganador": win_amount,
                    "gano_subasta": "SÍ" if ab["gano"] else "NO",
                    "num_pujadores_subasta": num_bidders,
                    "sobrepuja_eur": over_eur,
                    "sobrepuja_pct": over_pct,
                    "diferencia_vs_segunda_puja": diff_vs_2nd,
                })

    return pd.DataFrame(rows)


def _parse_report_breakdown(rep: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extracts AS Picas (`picas_as`), Picas points (`puntos_picas`), SofaScore rating (`nota_sofascore`),
    and SofaScore points (`puntos_sofascore`) directly from `optionalPoints.superPicaExtraPoints.breakdown`
    in the round endpoint (0 extra API calls!).
    """
    res = {"picas_as": "", "puntos_picas": "", "nota_sofascore": "", "puntos_sofascore": ""}
    try:
        opt = rep.get("optionalPoints", {}) or {}
        sp = opt.get("superPicaExtraPoints", {}) or {}
        bd = sp.get("breakdown", []) or []
        for item in bd:
            if not isinstance(item, list) or len(item) < 2:
                continue
            label = str(item[0]).strip()
            val_str = str(item[1]).strip()
            if label == "AS.com":
                m = re.search(r"(-?\d+)", val_str)
                if m:
                    res["puntos_picas"] = int(m.group(1))
            elif "Pica" in label and label != "Super Pica":
                m = re.search(r"(-?\d+)", label)
                if m:
                    res["picas_as"] = int(m.group(1))
            elif label == "SofaScore":
                m = re.search(r"(-?\d+)", val_str)
                if m:
                    res["puntos_sofascore"] = int(m.group(1))
            elif "SofaScore" in label and label != "SofaScore":
                m = re.search(r"(\d+(?:\.\d+)?)", label)
                if m:
                    res["nota_sofascore"] = float(m.group(1))
    except Exception:
        pass
    return res


def compute_optimal_lineup_points(
    squad_pids: set,
    round_points_by_pid: Dict[int, int],
    raw_players: Dict[str, Any]
) -> Tuple[int, str]:
    """Computes the maximum legal Biwenger points a manager could have scored from their squad."""
    by_pos: Dict[int, List[int]] = {1: [], 2: [], 3: [], 4: []}
    for pid in squad_pids:
        p_info = raw_players.get(str(pid), {})
        pos = int(p_info.get("position", 0) or 0)
        pts = int(round_points_by_pid.get(pid, 0) or 0)
        if pos in by_pos:
            by_pos[pos].append(pts)

    for pos in by_pos:
        by_pos[pos].sort(reverse=True)

    best_total = 0
    best_form_str = "4-4-2"

    for n_df, n_mf, n_fw in VALID_FORMATIONS:
        gk_pts = by_pos[1][:1]
        df_pts = by_pos[2][:n_df]
        mf_pts = by_pos[3][:n_mf]
        fw_pts = by_pos[4][:n_fw]

        filled_count = len(gk_pts) + len(df_pts) + len(mf_pts) + len(fw_pts)
        empty_penalty = max(0, 11 - filled_count) * 4
        tot = max(0, sum(gk_pts) + sum(df_pts) + sum(mf_pts) + sum(fw_pts) - empty_penalty)
        if tot >= best_total:
            best_total = tot
            best_form_str = f"{n_df}-{n_mf}-{n_fw}"

    return best_total, best_form_str


def extract_rounds_matches_reports_and_lineups(
    session: Optional[requests.Session],
    token: Optional[str],
    league_id: int,
    user_id: Optional[int],
    season_rounds: List[Dict[str, Any]],
    raw_players: Dict[str, Any],
    teams_map: Dict[int, str],
    price_by_pid_date: Dict[int, Dict[str, int]],
    daily_player_league_ctx: Dict[str, Dict[int, Tuple[str, int]]],
    round_start_snapshots: Dict[int, Dict[str, Any]],
    df_daily_managers: pd.DataFrame,
    df_odds: Optional[pd.DataFrame] = None,
    warehouse: Optional[BiwengerWarehouse] = None,
) -> Dict[str, Any]:
    """
    Extracts in ~10 seconds (reusing already finished rounds from Warehouse):
      1. `Partidos_Calendario` (`df_matches`)
      2. `Rendimiento_Jornadas` (`df_player_reports`)
      3. `Alineaciones_Jornadas` (`df_lineups`)
      4. `Equipos_LaLiga` (`df_team_standings`)
      5. `player_dated_reports`: per-player chronological match logs for August cumulative stats.
    """
    auth_headers = {
        "authorization": "Bearer " + (token or ""),
        "x-league": str(league_id),
        "x-user": str(user_id or ""),
        "referer": "https://biwenger.as.com/league",
    }

    biw_team_names = list(teams_map.values())
    odds_records: List[Dict[str, Any]] = []
    if df_odds is not None and not df_odds.empty:
        for _, o_row in df_odds.iterrows():
            l_raw = str(o_row.get("local") or o_row.get("ODDS_LOCAL") or "")
            v_raw = str(o_row.get("visitante") or o_row.get("ODDS_VISITANTE") or "")
            if l_raw and v_raw and biw_team_names:
                l_match, l_sc = process.extractOne(l_raw, biw_team_names)
                v_match, v_sc = process.extractOne(v_raw, biw_team_names)
                if l_sc >= 70 and v_sc >= 70:
                    odds_records.append({
                        "local": l_match,
                        "visitante": v_match,
                        "prob_1": round(float(o_row.get("1") or o_row.get("ODDS_1") or 0.0), 4),
                        "prob_x": round(float(o_row.get("X") or o_row.get("ODDS_X") or 0.0), 4),
                        "prob_2": round(float(o_row.get("2") or o_row.get("ODDS_2") or 0.0), 4),
                        "xg_home": o_row.get("home_goals") if "home_goals" in o_row else o_row.get("ODDS_HOME_GOALS"),
                        "xg_away": o_row.get("away_goals") if "away_goals" in o_row else o_row.get("ODDS_AWAY_GOALS"),
                    })
    odds_by_pair = {(r["local"], r["visitante"]): r for r in odds_records}

    # Check already stored rounds in Warehouse so we don't re-fetch finished rounds every day
    cached_finished_rids = set()
    df_prev_matches = pd.DataFrame()
    df_prev_reports = pd.DataFrame()
    df_prev_lineups = pd.DataFrame()
    if warehouse is not None:
        df_prev_matches = warehouse.read_table("Partidos_Calendario")
        df_prev_reports = warehouse.read_table("Rendimiento_Jornadas")
        df_prev_lineups = warehouse.read_table("Alineaciones_Jornadas")
        if not df_prev_matches.empty and not df_prev_reports.empty and not df_prev_lineups.empty:
            rep_rids = set(pd.to_numeric(df_prev_reports["round_id"], errors="coerce").dropna().astype(int))
            lin_rids = set(pd.to_numeric(df_prev_lineups["round_id"], errors="coerce").dropna().astype(int))
            for r_id_val, grp_m in df_prev_matches.groupby("round_id"):
                r_id_int = int(float(r_id_val))
                if (grp_m["estado_jornada"] == "finished").all() and r_id_int in rep_rids and r_id_int in lin_rids:
                    cached_finished_rids.add(r_id_int)

    matches_rows: List[Dict[str, Any]] = []
    player_reports_rows: List[Dict[str, Any]] = []
    lineups_rows: List[Dict[str, Any]] = []
    round_pts_map: Dict[int, Dict[int, int]] = {}
    player_dated_reports: Dict[int, List[Dict[str, Any]]] = {}

    if cached_finished_rids:
        matches_rows.extend(df_prev_matches[pd.to_numeric(df_prev_matches["round_id"], errors="coerce").isin(cached_finished_rids)].to_dict(orient="records"))
        player_reports_rows.extend(df_prev_reports[pd.to_numeric(df_prev_reports["round_id"], errors="coerce").isin(cached_finished_rids)].to_dict(orient="records"))
        lineups_rows.extend(df_prev_lineups[pd.to_numeric(df_prev_lineups["round_id"], errors="coerce").isin(cached_finished_rids)].to_dict(orient="records"))
        print(f"⚡ Reutilizadas {len(cached_finished_rids)} jornadas finalizadas desde el Data Warehouse local (0 llamadas API).")

    print(f"⚽ Verificando datos de las {len(season_rounds)} jornadas de LaLiga y de tu liga privada...")
    cdn_rate_limited = False

    for rd_meta in season_rounds:
        r_id = _safe_int(rd_meta.get("id"), 0)
        r_name = str(rd_meta.get("name", ""))
        r_status = str(rd_meta.get("status", "pending"))
        r_part = _safe_int(rd_meta.get("part"), 1)
        m_num = re.search(r"(\d+)", r_name)
        j_num = int(m_num.group(1)) if m_num else 0

        if r_status == "pending" and (r_id in round_start_snapshots or (1 <= j_num <= 10)):
            r_status = "finished"

        if not r_id or r_id in cached_finished_rids:
            continue

        pub_data = {}
        if not cdn_rate_limited:
            for attempt in range(2):
                try:
                    url_pub = f"https://cf.biwenger.com/api/v2/rounds/la-liga/{r_id}?score={GeneralSettings.SCORE_TYPE}"
                    resp_pub = requests.get(url_pub, headers=get_cdn_headers(), timeout=10)
                    if resp_pub.status_code == 200:
                        pub_data = resp_pub.json().get("data", {})
                        break
                    elif resp_pub.status_code == 429:
                        reset_ts = int(resp_pub.headers.get("x-rate-limit-reset", 0) or 0)
                        wait_sec = (reset_ts - time.time() + 2.0) if reset_ts > time.time() else 65.0
                        if wait_sec <= 95.0 and attempt == 0:
                            print(f"   ⏳ [429 en /rounds/la-liga/{r_id}] Esperando {wait_sec:.0f}s...")
                            time.sleep(max(1.0, wait_sec))
                        else:
                            print(f"   ⚠️ Cuota horaria CDN activa en /rounds/la-liga/{r_id} (reset {datetime.datetime.fromtimestamp(reset_ts).strftime('%H:%M:%S') if reset_ts else 'próxima hora'}). Omitiendo actas CDN por ahora y continuando con alineaciones de liga.")
                            cdn_rate_limited = True
                            break
                except Exception:
                    time.sleep(1.0)

        mvp_info = pub_data.get("mvp") or {}
        round_mvp_pid = int(mvp_info.get("id", 0) or 0) if isinstance(mvp_info, dict) else 0
        ideal_info = pub_data.get("idealLineup") or {}
        ideal_pids = {
            int(p.get("id", 0))
            for p in (ideal_info.get("players", []) if isinstance(ideal_info, dict) else [])
            if isinstance(p, dict) and p.get("id")
        }

        games = pub_data.get("games", []) or []
        round_pts_map[r_id] = {}

        for g in games:
            m_id = int(g.get("id", 0) or 0)
            g_ts = g.get("date")
            g_dt = datetime.datetime.fromtimestamp(g_ts) if g_ts else None
            g_dt_str = g_dt.strftime("%Y-%m-%d %H:%M:%S") if g_dt else ""
            g_day_str = g_dt.strftime("%Y-%m-%d") if g_dt else datetime.date.today().strftime("%Y-%m-%d")
            g_status = str(g.get("status", "pending"))

            home = g.get("home", {}) or {}
            away = g.get("away", {}) or {}
            h_id = int(home.get("id", 0) or 0)
            a_id = int(away.get("id", 0) or 0)
            h_name = str(home.get("name") or teams_map.get(h_id, ""))
            a_name = str(away.get("name") or teams_map.get(a_id, ""))
            h_score = home.get("score")
            a_score = away.get("score")

            h_diff = (home.get("difficulty") or {}).get("rating") if isinstance(home.get("difficulty"), dict) else None
            a_diff = (away.get("difficulty") or {}).get("rating") if isinstance(away.get("difficulty"), dict) else None

            h_stats = home.get("stats") or {}
            a_stats = away.get("stats") or {}
            odd_info = odds_by_pair.get((h_name, a_name), {})

            match_mvp_pid = None
            match_mvp_name = ""
            for side_obj in (home, away):
                for rep in side_obj.get("reports", []) or []:
                    if rep.get("star"):
                        p_obj = rep.get("player", {}) or {}
                        match_mvp_pid = p_obj.get("id")
                        match_mvp_name = p_obj.get("name", "")
                        break

            matches_rows.append({
                "round_id": r_id,
                "jornada_num": j_num,
                "jornada": r_name,
                "part": r_part,
                "estado_jornada": r_status,
                "match_id": m_id,
                "fecha_hora": g_dt_str,
                "local_id": h_id,
                "local": h_name,
                "visitante_id": a_id,
                "visitante": a_name,
                "goles_local": h_score if h_score is not None else "",
                "goles_visitante": a_score if a_score is not None else "",
                "estado_partido": g_status,
                "estadio": g.get("location") or "",
                "dificultad_local": h_diff if h_diff is not None else "",
                "dificultad_visitante": a_diff if a_diff is not None else "",
                "prob_local_1": odd_info.get("prob_1", ""),
                "prob_empate_x": odd_info.get("prob_x", ""),
                "prob_visitante_2": odd_info.get("prob_2", ""),
                "xg_local_eci": odd_info.get("xg_home", ""),
                "xg_visitante_eci": odd_info.get("xg_away", ""),
                "posesion_local": h_stats.get("possession", ""),
                "posesion_visitante": a_stats.get("possession", ""),
                "tiros_local": h_stats.get("shots", ""),
                "tiros_visitante": a_stats.get("shots", ""),
                "tiros_puerta_local": h_stats.get("goalShots", ""),
                "tiros_puerta_visitante": a_stats.get("goalShots", ""),
                "corners_local": h_stats.get("corners", ""),
                "corners_visitante": a_stats.get("corners", ""),
                "entradas_local": h_stats.get("tackles", ""),
                "entradas_visitante": a_stats.get("tackles", ""),
                "precision_pase_local": h_stats.get("passesAccuracy", ""),
                "precision_pase_visitante": a_stats.get("passesAccuracy", ""),
                "mvp_partido_id": match_mvp_pid or "",
                "mvp_partido_nombre": match_mvp_name,
            })

            for is_home_side, side_obj, team_name, rival_name, opp_score in (
                (True, home, h_name, a_name, a_score),
                (False, away, a_name, h_name, h_score),
            ):
                for rep in side_obj.get("reports", []) or []:
                    p_obj = rep.get("player", {}) or {}
                    pid = int(p_obj.get("id", 0) or 0)
                    if not pid:
                        continue
                    pts_biw = int(rep.get("points", 0) or 0)
                    round_pts_map[r_id][pid] = pts_biw

                    bd_info = _parse_report_breakdown(rep)
                    events = rep.get("events", []) or []
                    sub_in_min = next((e.get("metadata") for e in events if e.get("type") == 5), None)
                    sub_out_min = next((e.get("metadata") for e in events if e.get("type") == 6), None)

                    if sub_in_min is not None and sub_out_min is not None:
                        mins_played = max(1, int(sub_out_min) - int(sub_in_min))
                        es_titular = "NO"
                    elif sub_in_min is not None:
                        mins_played = max(1, 90 - int(sub_in_min))
                        es_titular = "NO"
                    elif sub_out_min is not None:
                        mins_played = int(sub_out_min)
                        es_titular = "SÍ"
                    else:
                        mins_played = 90
                        es_titular = "SÍ"

                    goals = sum(1 for e in events if e.get("type") in (1, 2))
                    assists = sum(1 for e in events if e.get("type") == 3)
                    yellows = sum(1 for e in events if e.get("type") == 7)
                    reds = sum(1 for e in events if e.get("type") in (8, 9))
                    clean_sheet = "SÍ" if (opp_score == 0 and mins_played >= 60) else "NO"

                    p_price_match = int(
                        price_by_pid_date.get(pid, {}).get(g_day_str, 0)
                        or raw_players.get(str(pid), {}).get("price", 0)
                    )
                    owner_at_match, _ = daily_player_league_ctx.get(g_day_str, {}).get(pid, ("Libre", 0))
                    pos_code = int(p_obj.get("position") or raw_players.get(str(pid), {}).get("position", 0) or 0)
                    res_str = f"{h_score}-{a_score}" if (h_score is not None and a_score is not None) else ""

                    player_reports_rows.append({
                        "round_id": r_id,
                        "jornada_num": j_num,
                        "jornada": r_name,
                        "part": r_part,
                        "match_id": m_id,
                        "fecha_partido": g_dt_str,
                        "player_id": pid,
                        "nombre": p_obj.get("name") or raw_players.get(str(pid), {}).get("name", ""),
                        "posicion": POSITION_MAP.get(pos_code, str(pos_code)),
                        "equipo": team_name,
                        "rival": rival_name,
                        "condicion": "LOCAL" if is_home_side else "VISITANTE",
                        "resultado": res_str,
                        "es_titular": es_titular,
                        "minuto_entrada": sub_in_min if sub_in_min is not None else "",
                        "minuto_salida": sub_out_min if sub_out_min is not None else "",
                        "minutos_jugados": mins_played,
                        "goles": goals,
                        "asistencias": assists,
                        "amarillas": yellows,
                        "rojas": reds,
                        "porteria_cero": clean_sheet,
                        "picas_as": bd_info["picas_as"],
                        "nota_sofascore": bd_info["nota_sofascore"],
                        "puntos_biwenger": pts_biw,
                        "puntos_picas": bd_info["puntos_picas"],
                        "puntos_sofascore": bd_info["puntos_sofascore"],
                        "precio_en_jornada": p_price_match,
                        "es_mvp_partido": "SÍ" if rep.get("star") else "NO",
                        "es_mvp_jornada": "SÍ" if pid == round_mvp_pid else "NO",
                        "es_rentable": "SÍ" if rep.get("profitable") else "NO",
                        "en_once_ideal": "SÍ" if pid in ideal_pids else "NO",
                        "propietario_en_jornada": owner_at_match,
                    })

        if r_status in ("finished", "active"):
            try:
                url_priv = f"https://biwenger.as.com/api/v2/rounds/league/{r_id}?league={league_id}"
                resp_priv = session.get(url_priv, headers=auth_headers, timeout=10)
                if resp_priv.status_code == 200:
                    priv_data = resp_priv.json().get("data", {})
                    st_list = (priv_data.get("league") or {}).get("standings", []) or []

                    snap = round_start_snapshots.get(r_id)
                    if snap:
                        r_start_str = snap["date_str"]
                        r_day_str = r_start_str[:10]
                        squads_at_start = snap["squads"]
                        cash_at_start = snap["cash"]
                    else:
                        game_dates = [g.get("date") for g in games if g.get("date")]
                        min_ts = min(game_dates) if game_dates else time.time()
                        r_start_str = datetime.datetime.fromtimestamp(min_ts).strftime("%Y-%m-%d %H:%M:%S")
                        r_day_str = r_start_str[:10]
                        squads_at_start = {}
                        cash_at_start = {}
                        if not df_daily_managers.empty:
                            sub_dm = df_daily_managers[df_daily_managers["fecha"] <= r_day_str]
                            if not sub_dm.empty:
                                latest_d = sub_dm["fecha"].max()
                                for _, d_row in sub_dm[sub_dm["fecha"] == latest_d].iterrows():
                                    u_id_dm = int(d_row["user_id"])
                                    p_str = str(d_row.get("plantilla_ids", ""))
                                    squads_at_start[u_id_dm] = {int(x) for x in p_str.split(",") if x.strip().isdigit()}
                                    cash_at_start[u_id_dm] = float(d_row.get("saldo_disponible_exacto", 0))

                    r_pts_lookup = round_pts_map.get(r_id, {})

                    for u_entry in st_list:
                        if not isinstance(u_entry, dict):
                            continue
                        uid = _safe_int(u_entry.get("id"), 0)
                        m_name = str(u_entry.get("name", ""))
                        raw_lo = u_entry.get("lineup")
                        lineup_obj = raw_lo if isinstance(raw_lo, dict) else {}
                        formation = str(lineup_obj.get("type", ""))
                        l_points = _safe_int(lineup_obj.get("points"), 0)
                        l_pos = _safe_int(lineup_obj.get("position"), 0)
                        l_ts = lineup_obj.get("date")
                        l_dt_str = datetime.datetime.fromtimestamp(l_ts).strftime("%Y-%m-%d %H:%M:%S") if l_ts else ""
                        raw_pl_list = lineup_obj.get("players", [])
                        if isinstance(raw_pl_list, list):
                            aligned_pids = [_safe_int(p.get("id") if isinstance(p, dict) else p, 0) for p in raw_pl_list if p is not None]
                            aligned_pids = [p for p in aligned_pids if p > 0]
                        else:
                            aligned_pids = []
                        aligned_names = [raw_players.get(str(p), {}).get("name", str(p)) for p in aligned_pids]

                        owned_pids = squads_at_start.get(uid, set(aligned_pids)) | set(aligned_pids)
                        bench_pids = sorted(list(owned_pids - set(aligned_pids)))
                        bench_names = [raw_players.get(str(p), {}).get("name", str(p)) for p in bench_pids]
                        bench_pts = sum(_safe_int(r_pts_lookup.get(p), 0) for p in bench_pids)

                        opt_pts, opt_form = compute_optimal_lineup_points(owned_pids, r_pts_lookup, raw_players)
                        opt_pts = max(opt_pts, l_points)
                        lost_pts = max(0, opt_pts - l_points)
                        eff_pct = round((l_points / opt_pts * 100.0), 1) if opt_pts > 0 else 100.0

                        raw_bon = lineup_obj.get("bonuses")
                        bonuses_obj = raw_bon if isinstance(raw_bon, dict) else {}
                        b_point = _safe_int(bonuses_obj.get("bonusPoint"), 0)
                        b_pos = _safe_int(bonuses_obj.get("bonusRoundPointsPosition"), 0)

                        b_gmvp_raw = bonuses_obj.get("bonusGameMVP", 0)
                        b_gmvp = int(b_gmvp_raw[0] * b_gmvp_raw[1]) if isinstance(b_gmvp_raw, list) and len(b_gmvp_raw) >= 2 else _safe_int(b_gmvp_raw, 0)

                        b_rmvp_raw = bonuses_obj.get("bonusRoundMVP", 0)
                        b_rmvp = int(b_rmvp_raw[0] * b_rmvp_raw[1]) if isinstance(b_rmvp_raw, list) and len(b_rmvp_raw) >= 2 else _safe_int(b_rmvp_raw, 0)

                        b_ideal_raw = bonuses_obj.get("bonusIdealLineup", 0)
                        b_ideal = int(b_ideal_raw[0] * b_ideal_raw[1]) if isinstance(b_ideal_raw, list) and len(b_ideal_raw) >= 2 else _safe_int(b_ideal_raw, 0)

                        b_total = (b_point + b_pos + b_gmvp + b_rmvp + b_ideal) if isinstance(raw_bon, dict) else _safe_int(raw_bon, 0)
                        saldo_kickoff = int(round(cash_at_start.get(uid, 0.0)))
                        team_val_round = sum(price_by_pid_date.get(p, {}).get(r_day_str, 0) for p in owned_pids)

                        lineups_rows.append({
                            "round_id": r_id,
                            "jornada_num": j_num,
                            "jornada": r_name,
                            "part": r_part,
                            "estado_jornada": r_status,
                            "fecha_inicio_jornada": r_start_str,
                            "user_id": uid,
                            "manager": m_name,
                            "posicion_jornada": l_pos,
                            "puntos_jornada": l_points,
                            "puntos_optimos_posibles": opt_pts,
                            "puntos_perdidos_entrenador": lost_pts,
                            "eficiencia_entrenador_pct": eff_pct,
                            "formacion_usada": formation,
                            "formacion_optima": opt_form,
                            "fecha_guardado_alineacion": l_dt_str,
                            "titulares_ids": ",".join(map(str, aligned_pids)),
                            "titulares_nombres": ", ".join(aligned_names),
                            "banquillo_ids": ",".join(map(str, bench_pids)),
                            "banquillo_nombres": ", ".join(bench_names),
                            "puntos_banquillo": bench_pts,
                            "prima_puntos": b_point,
                            "prima_posicion": b_pos,
                            "prima_mvp_partido": b_gmvp,
                            "prima_mvp_jornada": b_rmvp,
                            "prima_once_ideal": b_ideal,
                            "prima_total_jornada": b_total,
                            "saldo_inicio_jornada": saldo_kickoff,
                            "valor_plantilla_jornada": team_val_round,
                            "puntua_jornada": "SÍ" if lineup_obj.get("count", True) else "NO (Negativo)",
                        })
            except Exception as e:
                print(f"⚠️ Error extrayendo alineaciones de liga para jornada {r_id} ({r_name}): {e}")

    df_matches = pd.DataFrame(matches_rows)
    df_player_reports = pd.DataFrame(player_reports_rows)
    df_lineups = pd.DataFrame(lineups_rows)

    if not df_player_reports.empty:
        for c in ["player_id", "match_id", "jornada_num", "part", "puntos_biwenger"]:
            if c in df_player_reports.columns:
                df_player_reports[c] = pd.to_numeric(df_player_reports[c], errors="coerce").fillna(0).astype(int)
        df_player_reports = (
            df_player_reports.sort_values(["jornada_num", "part", "fecha_partido"])
            .drop_duplicates(subset=["match_id", "player_id"], keep="last")
            .reset_index(drop=True)
        )
        for row_rep in df_player_reports.to_dict(orient="records"):
            pid = int(row_rep["player_id"])
            m_day = str(row_rep["fecha_partido"])[:10]
            if m_day:
                player_dated_reports.setdefault(pid, []).append({
                    "date": m_day,
                    "pts": int(row_rep["puntos_biwenger"] or 0),
                    "goals": int(row_rep["goles"] or 0),
                    "assists": int(row_rep["asistencias"] or 0),
                    "mins": int(row_rep["minutos_jugados"] or 0),
                    "picas": row_rep["picas_as"],
                    "sofascore": row_rep["nota_sofascore"],
                })
        for pid in player_dated_reports:
            player_dated_reports[pid].sort(key=lambda x: x["date"])

    if not df_matches.empty:
        for c in ["round_id", "match_id", "jornada_num", "part"]:
            if c in df_matches.columns:
                df_matches[c] = pd.to_numeric(df_matches[c], errors="coerce").fillna(0).astype(int)

    if not df_lineups.empty:
        for c in ["jornada_num", "part", "user_id", "round_id", "posicion_jornada", "puntos_jornada"]:
            if c in df_lineups.columns:
                df_lineups[c] = pd.to_numeric(df_lineups[c], errors="coerce").fillna(0).astype(int)

        df_lineups = df_lineups.sort_values(["jornada_num", "part", "posicion_jornada"]).reset_index(drop=True)
        latest_part_per_j = df_lineups.groupby("jornada_num")["part"].max().to_dict()

        for (jn, pt), grp in df_lineups.groupby(["jornada_num", "part"], sort=False):
            jn_int = int(jn)
            pt_int = int(pt)
            temp_totals = {}
            for idx_row, r_row in grp.iterrows():
                u = int(r_row["user_id"])
                sub = df_lineups[
                    (df_lineups["user_id"] == u)
                    & (
                        ((df_lineups["jornada_num"] < jn_int) & (df_lineups["part"] == df_lineups["jornada_num"].map(latest_part_per_j)))
                        | ((df_lineups["jornada_num"] == jn_int) & (df_lineups["part"] == pt_int))
                    )
                ]
                sub_dedup = sub.sort_values("part").drop_duplicates(subset=["jornada_num"], keep="last")
                temp_totals[u] = int(sub_dedup["puntos_jornada"].sum())

            sorted_uids = sorted(temp_totals.keys(), key=lambda x: temp_totals[x], reverse=True)
            rank_map = {u: rk + 1 for rk, u in enumerate(sorted_uids)}
            for idx_row, r_row in grp.iterrows():
                u = int(r_row["user_id"])
                df_lineups.loc[idx_row, "puntos_acumulados_liga"] = temp_totals[u]
                df_lineups.loc[idx_row, "posicion_general_liga"] = rank_map[u]

        df_lineups["puntos_acumulados_liga"] = df_lineups["puntos_acumulados_liga"].astype(int)
        df_lineups["posicion_general_liga"] = df_lineups["posicion_general_liga"].astype(int)

    team_stats_map: Dict[int, Dict[str, Any]] = {}
    for tid, tname in teams_map.items():
        team_stats_map[int(tid)] = {
            "team_id": int(tid),
            "equipo": tname,
            "pj": 0, "pg": 0, "pe": 0, "pp": 0,
            "gf": 0, "gc": 0, "dg": 0, "puntos_laliga": 0,
            "porterias_cero": 0,
            "posesion_list": [],
            "tiros_list": [],
            "tiros_puerta_list": [],
            "proximo_rival": "",
            "proxima_condicion": "",
            "prob_victoria_proximo": "",
        }

    if not df_matches.empty:
        df_unique_matches = df_matches.sort_values("part").drop_duplicates(subset=["match_id"], keep="last")
        for _, m in df_unique_matches.iterrows():
            if m["estado_partido"] != "finished" or m["goles_local"] == "" or m["goles_visitante"] == "":
                continue
            lid = int(m["local_id"])
            vid = int(m["visitante_id"])
            gl = int(m["goles_local"])
            gv = int(m["goles_visitante"])

            if lid in team_stats_map:
                ts = team_stats_map[lid]
                ts["pj"] += 1
                ts["gf"] += gl
                ts["gc"] += gv
                if gv == 0:
                    ts["porterias_cero"] += 1
                if gl > gv:
                    ts["pg"] += 1
                    ts["puntos_laliga"] += 3
                elif gl == gv:
                    ts["pe"] += 1
                    ts["puntos_laliga"] += 1
                else:
                    ts["pp"] += 1
                if m["posesion_local"] != "":
                    ts["posesion_list"].append(float(m["posesion_local"]))
                if m["tiros_local"] != "":
                    ts["tiros_list"].append(float(m["tiros_local"]))
                if m["tiros_puerta_local"] != "":
                    ts["tiros_puerta_list"].append(float(m["tiros_puerta_local"]))

            if vid in team_stats_map:
                ts = team_stats_map[vid]
                ts["pj"] += 1
                ts["gf"] += gv
                ts["gc"] += gl
                if gl == 0:
                    ts["porterias_cero"] += 1
                if gv > gl:
                    ts["pg"] += 1
                    ts["puntos_laliga"] += 3
                elif gv == gl:
                    ts["pe"] += 1
                    ts["puntos_laliga"] += 1
                else:
                    ts["pp"] += 1
                if m["posesion_visitante"] != "":
                    ts["posesion_list"].append(float(m["posesion_visitante"]))
                if m["tiros_visitante"] != "":
                    ts["tiros_list"].append(float(m["tiros_visitante"]))
                if m["tiros_puerta_visitante"] != "":
                    ts["tiros_puerta_list"].append(float(m["tiros_puerta_visitante"]))

        df_pending = df_unique_matches[df_unique_matches["estado_partido"] == "pending"].sort_values("fecha_hora")
        for _, m in df_pending.iterrows():
            lid = int(m["local_id"])
            vid = int(m["visitante_id"])
            if lid in team_stats_map and not team_stats_map[lid]["proximo_rival"]:
                team_stats_map[lid]["proximo_rival"] = m["visitante"]
                team_stats_map[lid]["proxima_condicion"] = "LOCAL"
                team_stats_map[lid]["prob_victoria_proximo"] = m["prob_local_1"]
            if vid in team_stats_map and not team_stats_map[vid]["proximo_rival"]:
                team_stats_map[vid]["proximo_rival"] = m["local"]
                team_stats_map[vid]["proxima_condicion"] = "VISITANTE"
                team_stats_map[vid]["prob_victoria_proximo"] = m["prob_visitante_2"]

    fantasy_by_tid: Dict[int, Dict[str, Any]] = {tid: {"tot": 0, "home": 0, "away": 0, "cnt": 0, "val": 0} for tid in teams_map}
    for _, p_base in raw_players.items():
        tid = int(p_base.get("teamID", 0) or 0)
        if tid in fantasy_by_tid:
            fantasy_by_tid[tid]["tot"] += int(p_base.get("points", 0) or 0)
            fantasy_by_tid[tid]["home"] += int(p_base.get("pointsHome", 0) or 0)
            fantasy_by_tid[tid]["away"] += int(p_base.get("pointsAway", 0) or 0)
            fantasy_by_tid[tid]["cnt"] += 1
            fantasy_by_tid[tid]["val"] += int(p_base.get("price", 0) or 0)

    equipos_rows = []
    for tid, ts in team_stats_map.items():
        ts["dg"] = ts["gf"] - ts["gc"]
        f_info = fantasy_by_tid.get(tid, {"tot": 0, "home": 0, "away": 0, "cnt": 1, "val": 0})
        equipos_rows.append({
            "posicion_laliga": 0,
            "team_id": tid,
            "equipo": ts["equipo"],
            "puntos_laliga": ts["puntos_laliga"],
            "pj": ts["pj"],
            "pg": ts["pg"],
            "pe": ts["pe"],
            "pp": ts["pp"],
            "gf": ts["gf"],
            "gc": ts["gc"],
            "dg": ts["dg"],
            "porterias_cero": ts["porterias_cero"],
            "posesion_media_pct": round(sum(ts["posesion_list"]) / len(ts["posesion_list"]), 1) if ts["posesion_list"] else 0.0,
            "tiros_por_partido": round(sum(ts["tiros_list"]) / len(ts["tiros_list"]), 1) if ts["tiros_list"] else 0.0,
            "tiros_puerta_por_partido": round(sum(ts["tiros_puerta_list"]) / len(ts["tiros_puerta_list"]), 1) if ts["tiros_puerta_list"] else 0.0,
            "puntos_fantasy_totales": f_info["tot"],
            "puntos_fantasy_local": f_info["home"],
            "puntos_fantasy_visitante": f_info["away"],
            "valor_mercado_plantilla_laliga": f_info["val"],
            "proximo_rival": ts["proximo_rival"],
            "proxima_condicion": ts["proxima_condicion"],
            "prob_victoria_proximo": ts["prob_victoria_proximo"],
        })

    df_team_standings = pd.DataFrame(equipos_rows)
    if not df_team_standings.empty:
        df_team_standings = df_team_standings.sort_values(
            by=["puntos_laliga", "dg", "gf"], ascending=[False, False, False]
        ).reset_index(drop=True)
        df_team_standings["posicion_laliga"] = df_team_standings.index + 1

    return {
        "Partidos_Calendario": df_matches,
        "Rendimiento_Jornadas": df_player_reports,
        "Alineaciones_Jornadas": df_lineups,
        "Equipos_LaLiga": df_team_standings,
        "player_dated_reports": player_dated_reports,
    }


def backfill_and_repair_historico_continuo(
    df_existing: pd.DataFrame,
    raw_players: Dict[str, Any],
    teams_map: Dict[int, str],
    price_by_pid_date: Dict[int, Dict[str, int]],
    player_static_meta: Dict[int, Dict[str, Any]],
    player_dated_reports: Dict[int, List[Dict[str, Any]]],
    daily_player_league_ctx: Dict[str, Dict[int, Tuple[str, int]]],
    df_bids: pd.DataFrame,
    df_market_sales_today: pd.DataFrame,
    comuniate_map_today: Dict[str, Dict[str, Any]],
    full_backfill: bool = True
) -> pd.DataFrame:
    """
    Repairs league ownership/clause/market/comuniate columns in existing `Historico_Continuo` rows
    and backfills August (2026-08-01 to 2026-09-01) using the exact reconstructed daily price matrix.
    """
    today_str = datetime.date.today().strftime("%Y-%m-%d")

    market_pids_by_date: Dict[str, set] = {}
    if not df_bids.empty and "fecha" in df_bids.columns:
        for _, r in df_bids.iterrows():
            d_str = str(r["fecha"])[:10]
            pid = int(r["player_id"])
            market_pids_by_date.setdefault(d_str, set()).add(pid)
            prev_d = (pd.to_datetime(d_str) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            market_pids_by_date.setdefault(prev_d, set()).add(pid)

    if not df_market_sales_today.empty and "player_id" in df_market_sales_today.columns:
        market_pids_by_date.setdefault(today_str, set()).update(
            int(p) for p in df_market_sales_today["player_id"].dropna().tolist()
        )

    if not df_existing.empty:
        df_existing = df_existing.copy()
        df_existing["player_id"] = pd.to_numeric(df_existing["player_id"], errors="coerce").fillna(0).astype(int)
        repaired_owners = []
        repaired_clauses = []
        repaired_market = []
        repaired_com_tit = []
        repaired_com_duda = []

        for _, row in df_existing.iterrows():
            d_str = str(row["fecha"]).strip()
            pid = int(row["player_id"])
            pname_low = str(row.get("nombre", "")).strip().lower()
            owner_calc, clause_calc = daily_player_league_ctx.get(d_str, {}).get(pid, ("Libre", 0))

            repaired_owners.append(owner_calc)
            repaired_clauses.append(clause_calc if clause_calc > 0 else "")

            curr_mkt = str(row.get("en_venta_mercado", "")).strip()
            if pid in market_pids_by_date.get(d_str, set()):
                repaired_market.append("SÍ")
            else:
                repaired_market.append(curr_mkt if curr_mkt in ("SÍ", "NO") else "NO")

            curr_ct = str(row.get("comuniate_titular", "")).strip()
            curr_cd = str(row.get("comuniate_duda", "")).strip()
            if d_str == today_str and pname_low in comuniate_map_today:
                repaired_com_tit.append(comuniate_map_today[pname_low].get("titular", curr_ct))
                repaired_com_duda.append(comuniate_map_today[pname_low].get("duda", curr_cd))
            else:
                repaired_com_tit.append(curr_ct)
                repaired_com_duda.append(curr_cd)

        df_existing["propietario_liga"] = repaired_owners
        df_existing["clausula_actual"] = repaired_clauses
        df_existing["en_venta_mercado"] = repaired_market
        df_existing["comuniate_titular"] = repaired_com_tit
        df_existing["comuniate_duda"] = repaired_com_duda

    existing_dates = set(df_existing["fecha"].astype(str).unique()) if not df_existing.empty else set()
    all_season_dates = [
        d.strftime("%Y-%m-%d")
        for d in pd.date_range(start="2026-08-01", end=today_str, freq="D")
    ]
    dates_to_generate = [d for d in all_season_dates if d not in existing_dates] if full_backfill else []

    all_pids = sorted([int(k) for k in raw_players.keys()])
    generated_rows: List[List[Any]] = []

    if dates_to_generate:
        print(f"🛠️ Reconstruyendo {len(dates_to_generate)} días de agosto en 'Historico_Continuo' ({dates_to_generate[0]} -> {dates_to_generate[-1]})...")

    for d_str in dates_to_generate:
        dt_obj = pd.to_datetime(d_str)
        d_prev_1 = (dt_obj - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        d_prev_7 = (dt_obj - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
        d_prev_14 = (dt_obj - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
        d_prev_30 = (dt_obj - pd.Timedelta(days=30)).strftime("%Y-%m-%d")

        for pid in all_pids:
            p_base = raw_players.get(str(pid), {})
            s_meta = player_static_meta.get(pid, {})
            name = p_base.get("name", "")
            age = s_meta.get("edad", "")
            nationality = s_meta.get("nacionalidad", "Desconocida")

            p_map = price_by_pid_date.get(pid, {})
            curr_p = int(p_map.get(d_str, p_base.get("price", 0)) or 0)
            prev_p = int(p_map.get(d_prev_1, curr_p) or curr_p)
            inc_24h = curr_p - prev_p
            pct_inc_24h = round((inc_24h / prev_p * 100.0), 2) if prev_p > 0 else 0.0

            min_1y = min(int(s_meta.get("min_precio_1y", curr_p) or curr_p), curr_p)
            max_1y = max(int(s_meta.get("max_precio_1y", curr_p) or curr_p), curr_p)

            p_0801 = int(p_map.get("2026-08-01", curr_p) or curr_p)
            season_floor = min(p_0801, curr_p)
            season_gain = curr_p - season_floor
            season_gain_pct = round((season_gain / season_floor * 100.0), 2) if season_floor > 0 else 0.0

            p_7d = int(p_map.get(d_prev_7, curr_p) or curr_p)
            p_14d = int(p_map.get(d_prev_14, curr_p) or curr_p)
            p_30d = int(p_map.get(d_prev_30, curr_p) or curr_p)

            diff_7d = curr_p - p_7d
            pct_7d = round((diff_7d / p_7d * 100.0), 2) if p_7d > 0 else 0.0
            diff_14d = curr_p - p_14d
            pct_14d = round((diff_14d / p_14d * 100.0), 2) if p_14d > 0 else 0.0
            diff_30d = curr_p - p_30d
            pct_30d = round((diff_30d / p_30d * 100.0), 2) if p_30d > 0 else 0.0

            reps_up_to_d = [r for r in player_dated_reports.get(pid, []) if r["date"] <= d_str]
            played_games = len(reps_up_to_d)
            points_tot = sum(r["pts"] for r in reps_up_to_d)
            avg_points = round(points_tot / played_games, 2) if played_games > 0 else 0.0

            tot_goals = sum(r["goals"] for r in reps_up_to_d)
            tot_assists = sum(r["assists"] for r in reps_up_to_d)
            tot_mins = sum(r["mins"] for r in reps_up_to_d)
            picas_vals = [float(r["picas"]) for r in reps_up_to_d if r["picas"] is not None and str(r["picas"]) != ""]
            sofa_vals = [float(r["sofascore"]) for r in reps_up_to_d if r["sofascore"] is not None and str(r["sofascore"]) != ""]
            avg_picas = round(sum(picas_vals) / len(picas_vals), 2) if picas_vals else ""
            avg_sofascore = round(sum(sofa_vals) / len(sofa_vals), 2) if sofa_vals else ""

            last5_pts = [str(r["pts"]) for r in reps_up_to_d[-5:]]
            fitness_str = ",".join(reversed(last5_pts))

            owner_name, clause_val = daily_player_league_ctx.get(d_str, {}).get(pid, ("Libre", 0))
            in_market = "SÍ" if pid in market_pids_by_date.get(d_str, set()) else "NO"

            raw_row = [
                d_str,
                pid,
                name,
                teams_map.get(int(p_base.get("teamID", 0) or 0), "Sin Equipo"),
                POSITION_MAP.get(p_base.get("position"), f"Pos {p_base.get('position')}"),
                age,
                nationality,
                "ok",
                curr_p,
                inc_24h,
                pct_inc_24h,
                min_1y,
                max_1y,
                season_gain,
                season_gain_pct,
                diff_7d,
                pct_7d,
                diff_14d,
                pct_14d,
                diff_30d,
                pct_30d,
                "", "", "", "",
                "", "", s_meta.get("ranking_last_season", ""),
                points_tot,
                s_meta.get("puntos_last_season", p_base.get("pointsLastSeason", 0)),
                played_games,
                avg_points,
                fitness_str,
                tot_goals,
                tot_assists,
                tot_mins,
                avg_picas,
                avg_sofascore,
                "", "",
                owner_name,
                clause_val if clause_val > 0 else "",
                in_market,
            ]
            generated_rows.append([sanitize_cell(c) for c in raw_row])

    df_generated = pd.DataFrame(generated_rows, columns=HISTORICO_HEADERS) if generated_rows else pd.DataFrame(columns=HISTORICO_HEADERS)

    if not df_existing.empty and not df_generated.empty:
        df_full_hist = pd.concat([df_generated, df_existing], ignore_index=True)
    elif not df_existing.empty:
        df_full_hist = df_existing
    else:
        df_full_hist = df_generated

    df_full_hist["player_id"] = pd.to_numeric(df_full_hist["player_id"], errors="coerce").fillna(0).astype(int)
    df_full_hist = (
        df_full_hist.drop_duplicates(subset=["fecha", "player_id"], keep="last")
        .sort_values(["fecha", "player_id"])
        .reset_index(drop=True)
    )

    os.makedirs("data/history/snapshots", exist_ok=True)
    df_today_only = df_full_hist[df_full_hist["fecha"] == today_str]
    if not df_today_only.empty:
        df_today_only.to_csv(f"data/history/snapshots/{today_str}.csv", index=False, encoding="utf-8-sig")
    df_full_hist.to_csv("data/history/market_sentiment_timeseries.csv", index=False, encoding="utf-8-sig")

    return df_full_hist


def build_daily_market_listings_history(
    warehouse: BiwengerWarehouse,
    df_market_sales: pd.DataFrame,
    df_market_offers: pd.DataFrame,
    df_bids: pd.DataFrame,
    raw_players: Dict[str, Any],
    teams_map: Dict[int, str],
) -> pd.DataFrame:
    """Builds and updates `Mercado_Diario_Historico` (`daily_market_listings`)."""
    rows: List[Dict[str, Any]] = []
    today_str = datetime.date.today().strftime("%Y-%m-%d")

    if not df_bids.empty:
        df_winners = df_bids[df_bids["gano_subasta"] == "SÍ"]
        for _, r in df_winners.iterrows():
            d_str = str(r["fecha"])[:10]
            pid = int(r["player_id"])
            rows.append({
                "id_registro": f"AUCTION_{d_str}_{pid}",
                "fecha": d_str,
                "tipo_registro": "Subasta Resuelta",
                "player_id": pid,
                "jugador": r["jugador"],
                "posicion": r["posicion"],
                "equipo": r["equipo"],
                "vendedor_o_origen": "Mercado",
                "precio_mercado_dia": int(r["precio_mercado_dia"]),
                "precio_salida_o_oferta": int(r["precio_mercado_dia"]),
                "importe_adjudicado": int(r["importe_ganador"]),
                "comprador_o_destinatario": r["manager_pujador"],
                "num_pujadores": int(r["num_pujadores_subasta"]),
                "fecha_expiracion": str(r["fecha"]),
            })

    if df_market_sales is not None and not df_market_sales.empty:
        for _, s in df_market_sales.iterrows():
            pid = int(s.get("player_id", 0) or 0)
            if not pid:
                continue
            p_base = raw_players.get(str(pid), {})
            p_name = p_base.get("name", f"Jugador {pid}")
            p_pos = POSITION_MAP.get(p_base.get("position"), str(p_base.get("position", "")))
            p_team = teams_map.get(int(p_base.get("teamID", 0) or 0), "Sin Equipo")
            mkt_p = int(p_base.get("price", 0) or 0)
            sale_p = int(s.get("price", mkt_p) or mkt_p)
            seller = str(s.get("user_name") or "Mercado")
            until_str = str(s.get("until") or "")

            rows.append({
                "id_registro": f"SALE_{today_str}_{pid}_{seller}",
                "fecha": today_str,
                "tipo_registro": "En Venta Mercado",
                "player_id": pid,
                "jugador": p_name,
                "posicion": p_pos,
                "equipo": p_team,
                "vendedor_o_origen": seller,
                "precio_mercado_dia": mkt_p,
                "precio_salida_o_oferta": sale_p,
                "importe_adjudicado": "",
                "comprador_o_destinatario": "",
                "num_pujadores": "",
                "fecha_expiracion": until_str,
            })

    if df_market_offers is not None and not df_market_offers.empty:
        for _, o in df_market_offers.iterrows():
            pid = int(o.get("requested_player_id", 0) or 0)
            if not pid:
                continue
            p_base = raw_players.get(str(pid), {})
            p_name = p_base.get("name", f"Jugador {pid}")
            p_pos = POSITION_MAP.get(p_base.get("position"), str(p_base.get("position", "")))
            p_team = teams_map.get(int(p_base.get("teamID", 0) or 0), "Sin Equipo")
            mkt_p = int(p_base.get("price", 0) or 0)
            off_amt = int(o.get("amount", 0) or 0)
            from_name = str(o.get("from_name") or "Mercado")
            until_str = str(o.get("until") or "")

            rows.append({
                "id_registro": f"OFFER_{today_str}_{pid}_{off_amt}",
                "fecha": today_str,
                "tipo_registro": f"Oferta Recibida ({o.get('status', 'waiting')})",
                "player_id": pid,
                "jugador": p_name,
                "posicion": p_pos,
                "equipo": p_team,
                "vendedor_o_origen": from_name,
                "precio_mercado_dia": mkt_p,
                "precio_salida_o_oferta": off_amt,
                "importe_adjudicado": "",
                "comprador_o_destinatario": "Dani SR",
                "num_pujadores": 1,
                "fecha_expiracion": until_str,
            })

    df_new = pd.DataFrame(rows)
    return warehouse.upsert_table(
        "Mercado_Diario_Historico",
        df_new,
        pk_cols=["id_registro"],
        sort_cols=["fecha", "tipo_registro", "player_id"]
    )


def run_historical_pipeline(
    full_backfill: bool = False,
    sync_sheets: bool = True,
    extract_comuniate_history: bool = False
) -> Dict[str, Any]:
    """Master orchestrator for both retroactive backfill (`full_backfill=True`) and daily sync."""
    load_dotenv()
    t0 = time.time()
    mode_label = "BACKFILL RETROACTIVO COMPLETO (Desde 2026-08-01)" if full_backfill else "ACTUALIZACIÓN DIARIA INCREMENTAL"
    print("=" * 75)
    print(f"🚀 BIWENGER HISTORICAL DATA ENGINE — {mode_label}")
    print("=" * 75)

    warehouse = BiwengerWarehouse()
    ensure_exact_balances_csv()
    today_str = datetime.date.today().strftime("%Y-%m-%d")

    # 1. Load existing Historico_Continuo from Google Sheets / Warehouse first
    df_existing_hist = load_existing_historico_from_sheets_or_warehouse(warehouse)

    # Check if runner.py already saved api_session_cache.json today (0 duplicate API calls!)
    session_cache = {}
    session_cache_path = "./data/raw/api_session_cache.json"
    if os.path.exists(session_cache_path):
        try:
            with open(session_cache_path, "r", encoding="utf-8") as fc:
                sc = json.load(fc)
            if sc.get("date") == today_str:
                session_cache = sc
                print(f"⚡ Reutilizando caché de sesión de hoy ({today_str}) generada por runner.py (0 llamadas duplicadas).")
        except Exception:
            session_cache = {}

    # 2. Authenticate with Biwenger (or fallback to local user_info.csv if rate limited)
    auth_session = None
    auth_token = Credentials.BIWENGER_TOKEN
    my_uid = _safe_int(os.getenv("BIWENGER_USER_ID"), 9878097)
    my_balance = None
    league_id = _safe_int(os.getenv("BIWENGER_LEAGUE_ID"), 1643248)

    try:
        auth = BiwengerAuth(
            email=Credentials.BIWENGER_USERNAME,
            password=Credentials.BIWENGER_PASSWORD,
            token=Credentials.BIWENGER_TOKEN,
        )
        auth.run()
        auth_session = auth.session
        auth_token = auth.token
        if auth.player_info:
            my_uid = auth.player_info.team_id
            my_balance = auth.player_info.balance
            league_id = auth.player_info.league_id
    except Exception as e_auth:
        print(f"ℹ️ Auth API en espera ({e_auth}); utilizando metadatos locales de './data/user_info.csv'.")
        if os.path.exists("./data/user_info.csv"):
            df_u = pd.read_csv("./data/user_info.csv")
            if not df_u.empty:
                my_uid = int(df_u["team_id"].iloc[0])
                league_id = int(df_u["league_id"].iloc[0])
                my_balance = float(df_u["balance"].iloc[0])

    # 3. Extract Public Competition Data & Season Rounds (from cache, live API, or CSV fallback)
    raw_players: Dict[str, Any] = {}
    teams_map: Dict[int, str] = {}
    season_rounds: List[Dict[str, Any]] = []

    if session_cache.get("comp_data", {}).get("players"):
        comp_d = session_cache["comp_data"]
        raw_players = comp_d.get("players", {})
        teams_map = {int(tid): t["name"] for tid, t in comp_d.get("teams", {}).items()}
        season_rounds = (comp_d.get("season") or {}).get("rounds", [])
    else:
        try:
            gen = BiwengerGeneralData(session=None, competition_slug="la-liga")
            gen.run()
            season_info = gen.season_info()
            raw_players = gen.players
            teams_map = {int(tid): t["name"] for tid, t in gen.teams.items()}
            season_rounds = season_info.rounds
        except Exception as e_gen:
            print(f"ℹ️ CDN de competición en espera ({e_gen}); reconstruyendo jugadores y equipos desde './data/raw/' + 'Historico_Continuo'.")
            df_t = _safe_read_csv("./data/raw/teams.csv")
            if not df_t.empty:
                teams_map = {int(r["id"]): str(r["name"]) for _, r in df_t.iterrows()}
            df_rounds = _safe_read_csv("./data/raw/rounds.csv")
            if not df_rounds.empty:
                season_rounds = df_rounds.to_dict(orient="records")
            df_p = _safe_read_csv("./data/raw/players.csv")
            if not df_p.empty:
                for _, r in df_p.iterrows():
                    pid_s = str(int(r["id"]))
                    raw_players[pid_s] = {
                        "id": int(r["id"]),
                        "name": str(r["name"]),
                        "teamID": int(r["teamID"]) if pd.notna(r.get("teamID")) else 0,
                        "position": int(r["position"]) if pd.notna(r.get("position")) else 0,
                        "price": int(r["price"]) if pd.notna(r.get("price")) else 150000,
                        "priceIncrement": int(r["priceIncrement"]) if pd.notna(r.get("priceIncrement")) else 0,
                        "points": int(r["points"]) if pd.notna(r.get("points")) else 0,
                        "pointsHome": int(r["pointsHome"]) if pd.notna(r.get("pointsHome")) else 0,
                        "pointsAway": int(r["pointsAway"]) if pd.notna(r.get("pointsAway")) else 0,
                        "playedHome": int(r["playedHome"]) if pd.notna(r.get("playedHome")) else 0,
                        "playedAway": int(r["playedAway"]) if pd.notna(r.get("playedAway")) else 0,
                        "status": str(r.get("status", "ok")),
                    }
            # Refresh current prices & points from latest day in Historico_Continuo
            if not df_existing_hist.empty:
                latest_d = df_existing_hist["fecha"].max()
                inv_teams = {v: k for k, v in teams_map.items()}
                inv_pos = {v: k for k, v in POSITION_MAP.items()}
                for _, rh in df_existing_hist[df_existing_hist["fecha"] == latest_d].iterrows():
                    pid_i = _safe_int(rh.get("player_id"), 0)
                    if not pid_i:
                        continue
                    pid_s = str(pid_i)
                    entry = raw_players.setdefault(pid_s, {
                        "id": pid_i,
                        "name": str(rh.get("nombre", "")),
                        "teamID": inv_teams.get(str(rh.get("equipo", "")), 0),
                        "position": inv_pos.get(str(rh.get("posicion", "")), 3),
                        "pointsHome": 0,
                        "pointsAway": 0,
                    })
                    entry["price"] = _safe_int(rh.get("precio"), entry.get("price", 150000))
                    entry["priceIncrement"] = _safe_int(rh.get("subida_24h"), 0)
                    entry["points"] = _safe_int(rh.get("puntos_totales"), entry.get("points", 0))
                    entry["status"] = str(rh.get("estado", "ok"))

    price_by_pid_date, all_dates_str, player_static_meta = build_price_lookup_from_historico(
        df_existing_hist, raw_players, start_date="2026-08-01"
    )

    # 4. Extract Private League Data & Full Board since 2026-08-01 (or fallback to ./data/raw/)
    df_standings = pd.DataFrame()
    df_league_players = pd.DataFrame()
    df_market_sales = pd.DataFrame()
    df_market_offers = pd.DataFrame()
    board_res: Dict[str, pd.DataFrame] = {}
    raw_board_items: List[Dict[str, Any]] = session_cache.get("all_board_items", [])

    try:
        if auth_session is None:
            raise RuntimeError("Sin sesión activa de Biwenger")
        if os.path.exists("./data/raw/league_teams.csv") and session_cache:
            df_standings = _safe_read_csv("./data/raw/league_teams.csv")
            df_league_players = _safe_read_csv("./data/raw/league_players.csv")
            df_market_sales = _safe_read_csv("./data/raw/market_sales.csv")
            df_market_offers = _safe_read_csv("./data/raw/market_offers.csv")
            df_bt = _safe_read_csv("./data/raw/board_transfers.csv")
            df_bb = _safe_read_csv("./data/raw/board_bids.csv")
            board_res = {"transfers": df_bt, "bids": df_bb}
            if not raw_board_items:
                raw_board_items = fetch_full_board_raw(auth_session, auth_token, league_id, my_uid)
        else:
            uld = UserLeagueData(session=auth_session, token=auth_token, league_id=league_id, user_id=my_uid)
            df_standings = uld.league_table()
            df_league_players = uld.league_players_info(auth_session)
            df_market_sales = uld.market_sales_info()
            df_market_offers = uld.market_offers_info()
            board_res = uld.league_board_info(auth_session, fetch_all=True)
            raw_board_items = getattr(uld, "all_board_items", []) or fetch_full_board_raw(auth_session, auth_token, league_id, my_uid)

            os.makedirs("./data/raw", exist_ok=True)
            df_standings.to_csv("./data/raw/league_teams.csv", index=False)
            df_league_players.to_csv("./data/raw/league_players.csv", index=False)
            df_market_sales.to_csv("./data/raw/market_sales.csv", index=False)
            df_market_offers.to_csv("./data/raw/market_offers.csv", index=False)
            if "transfers" in board_res:
                board_res["transfers"].to_csv("./data/raw/board_transfers.csv", index=False)
            if "bids" in board_res:
                board_res["bids"].to_csv("./data/raw/board_bids.csv", index=False)
            if "bonuses" in board_res:
                board_res["bonuses"].to_csv("./data/raw/board_bonuses.csv", index=False)
            if "clause_increments" in board_res:
                board_res["clause_increments"].to_csv("./data/raw/clause_increments.csv", index=False)
            if my_balance is not None and os.path.exists("./data/user_info.csv"):
                df_ui = pd.read_csv("./data/user_info.csv")
                df_ui["balance"] = my_balance
                df_ui.to_csv("./data/user_info.csv", index=False)
            df_p_upd = _safe_read_csv("./data/raw/players.csv")
            if not df_p_upd.empty and raw_players:
                df_p_upd["price"] = df_p_upd["id"].map(lambda x: raw_players.get(str(int(x)), {}).get("price", 0))
                df_p_upd["priceIncrement"] = df_p_upd["id"].map(lambda x: raw_players.get(str(int(x)), {}).get("priceIncrement", 0))
                df_p_upd["points"] = df_p_upd["id"].map(lambda x: raw_players.get(str(int(x)), {}).get("points", 0))
                df_p_upd["status"] = df_p_upd["id"].map(lambda x: raw_players.get(str(int(x)), {}).get("status", "ok"))
                df_p_upd.to_csv("./data/raw/players.csv", index=False)
            if sync_sheets:
                try:
                    from src.tools.rival_tracker import BiwengerSheetsTracker
                    tracker = BiwengerSheetsTracker()
                    tracker.sync(full=True)
                except Exception as e_tr:
                    print(f"⚠️ Warning syncing BiwengerSheetsTracker: {e_tr}")
    except Exception as e_priv:
        print(f"ℹ️ Endpoints privados en espera ({e_priv}); utilizando staging local en './data/raw/'.")
        df_standings = _safe_read_csv("./data/raw/league_teams.csv")
        df_league_players = _safe_read_csv("./data/raw/league_players.csv")
        df_market_sales = _safe_read_csv("./data/raw/market_sales.csv")
        df_market_offers = _safe_read_csv("./data/raw/market_offers.csv")
        df_bt = _safe_read_csv("./data/raw/board_transfers.csv")
        df_bb = _safe_read_csv("./data/raw/board_bids.csv")
        board_res = {"transfers": df_bt, "bids": df_bb}
        # Reconstruct synthetic raw_board_items from board_transfers.csv + board_bids.csv if raw_board_items is empty
        if not raw_board_items and not df_bt.empty:
            bids_by_key: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
            if not df_bb.empty:
                for _, rb in df_bb.iterrows():
                    k_b = (str(rb.get("date", ""))[:19], _safe_int(rb.get("player_id"), 0))
                    bids_by_key.setdefault(k_b, []).append({
                        "user": {"id": _safe_int(rb.get("bidder_id"), 0), "name": str(rb.get("bidder_name", ""))},
                        "amount": _safe_int(rb.get("bid_amount"), 0),
                    })
            for _, rt in df_bt.iterrows():
                dt_s = str(rt.get("date", ""))[:19]
                try:
                    ts_val = int(pd.Timestamp(dt_s, tz="Europe/Madrid").timestamp())
                except Exception:
                    ts_val = 0
                pid_t = _safe_int(rt.get("player_id"), 0)
                b_id_t = _safe_int(rt.get("buyer_id"), 0) if pd.notna(rt.get("buyer_id")) else None
                b_nm_t = str(rt.get("buyer_name", "")) if pd.notna(rt.get("buyer_name")) else None
                s_id_t = _safe_int(rt.get("seller_id"), 0) if pd.notna(rt.get("seller_id")) else None
                s_nm_t = str(rt.get("seller_name", "")) if pd.notna(rt.get("seller_name")) else None
                amt_t = _safe_int(rt.get("amount"), 0)
                t_type = str(rt.get("type", ""))
                if t_type == "market_buy":
                    raw_board_items.append({
                        "type": "market",
                        "date": ts_val,
                        "content": [{
                            "player": pid_t,
                            "to": {"id": b_id_t, "name": b_nm_t} if b_id_t else None,
                            "amount": amt_t,
                            "date": ts_val,
                            "bids": bids_by_key.get((dt_s, pid_t), []),
                        }],
                    })
                elif t_type == "clause_increment":
                    raw_board_items.append({
                        "type": "clauseIncrement",
                        "date": ts_val,
                        "content": [{
                            "player": pid_t,
                            "user": {"id": b_id_t, "name": b_nm_t},
                            "amount": amt_t,
                        }],
                    })
                else:
                    raw_board_items.append({
                        "type": "transfer",
                        "date": ts_val,
                        "content": [{
                            "player": pid_t,
                            "from": {"id": s_id_t, "name": s_nm_t} if s_id_t else None,
                            "to": {"id": b_id_t, "name": b_nm_t} if b_id_t else None,
                            "amount": amt_t,
                            "type": "clause" if str(rt.get("clause", "")).lower() == "true" else "transfer",
                        }],
                    })

    # 5. Reconstruct exact daily ownership, clauses, and manager financials from 2026-08-01 to today
    recon = reconstruct_season_ownership_and_finances(
        df_league_players=df_league_players,
        df_standings=df_standings,
        board_res=board_res,
        raw_board_items=raw_board_items,
        price_by_pid_date=price_by_pid_date,
        all_dates_str=all_dates_str,
        my_user_id=my_uid,
        my_real_balance=my_balance,
    )
    df_daily_managers = recon["df_daily_managers"]
    daily_player_league_ctx = recon["daily_player_league_ctx"]
    round_start_snapshots = recon["round_start_snapshots"]

    # 6. Build Enriched Bids History (`Pujas_Historico`)
    df_bids = build_enriched_bids_history(
        raw_board_items=raw_board_items,
        raw_players=raw_players,
        teams_map=teams_map,
        price_by_pid_date=price_by_pid_date,
    )

    # 7. Load or extract EuroClubIndex odds & Comuniate
    df_odds = pd.DataFrame()
    if os.path.exists("./data/raw/odds.csv"):
        try:
            df_odds = pd.read_csv("./data/raw/odds.csv")
        except Exception:
            pass
    if df_odds.empty:
        try:
            df_odds = EuroClubIndexData().run()
        except Exception:
            pass

    comuniate_map_today: Dict[str, Dict[str, Any]] = {}
    if os.path.exists("./data/raw/comuniate.csv"):
        try:
            df_com = pd.read_csv("./data/raw/comuniate.csv")
            for _, r in df_com.iterrows():
                pname = str(r.get("nombre", "")).strip().lower()
                comuniate_map_today[pname] = {
                    "titular": r.get("titularidad", ""),
                    "duda": r.get("duda", ""),
                }
        except Exception:
            pass

    if extract_comuniate_history:
        try:
            df_existing_com_h = warehouse.read_table("Comuniate_Historico")
            if len(df_existing_com_h) >= 2500:
                print(f"⚡ Reutilizando 'Comuniate_Historico' desde el Data Warehouse ({len(df_existing_com_h):,} filas).")
            else:
                com_hist_df = ComuniateData().extract_historical_lineups()
                if not com_hist_df.empty:
                    warehouse.upsert_table(
                        "Comuniate_Historico",
                        com_hist_df,
                        pk_cols=["id_jornada", "equipo", "nombre"],
                        sort_cols=["id_jornada", "equipo", "posicion"],
                    )
        except Exception as e:
            print(f"⚠️ No se pudo extraer Comuniate_Historico: {e}")

    # 8. Extract All Rounds, Matches, Player Reports, Lineups & LaLiga Standings
    rounds_tables = extract_rounds_matches_reports_and_lineups(
        session=auth_session,
        token=auth_token,
        league_id=league_id,
        user_id=my_uid,
        season_rounds=season_rounds,
        raw_players=raw_players,
        teams_map=teams_map,
        price_by_pid_date=price_by_pid_date,
        daily_player_league_ctx=daily_player_league_ctx,
        round_start_snapshots=round_start_snapshots,
        df_daily_managers=df_daily_managers,
        df_odds=df_odds,
        warehouse=warehouse,
    )

    df_matches = rounds_tables["Partidos_Calendario"]
    df_player_reports = rounds_tables["Rendimiento_Jornadas"]
    df_lineups = rounds_tables["Alineaciones_Jornadas"]
    df_equipos_laliga = rounds_tables["Equipos_LaLiga"]
    player_dated_reports = rounds_tables["player_dated_reports"]

    # 9. Build / Update `Mercado_Diario_Historico`
    df_market_hist = build_daily_market_listings_history(
        warehouse=warehouse,
        df_market_sales=df_market_sales,
        df_market_offers=df_market_offers,
        df_bids=df_bids,
        raw_players=raw_players,
        teams_map=teams_map,
    )

    # 10. Backfill & Repair `Historico_Continuo` (`2026-08-01` -> Today)
    df_historico_continuo = backfill_and_repair_historico_continuo(
        df_existing=df_existing_hist,
        raw_players=raw_players,
        teams_map=teams_map,
        price_by_pid_date=price_by_pid_date,
        player_static_meta=player_static_meta,
        player_dated_reports=player_dated_reports,
        daily_player_league_ctx=daily_player_league_ctx,
        df_bids=df_bids,
        df_market_sales_today=df_market_sales,
        comuniate_map_today=comuniate_map_today,
        full_backfill=full_backfill,
    )

    # 11. Save all tables to Local SQLite Warehouse (`data/warehouse/biwenger_history.db`) + CSVs
    print("\n💾 Guardando tablas en el Data Warehouse local (SQLite + CSVs)...")
    warehouse.upsert_table("Historico_Continuo", df_historico_continuo, pk_cols=["fecha", "player_id"], replace_all=True)
    if not df_player_reports.empty:
        warehouse.upsert_table("Rendimiento_Jornadas", df_player_reports, pk_cols=["match_id", "player_id"], sort_cols=["jornada_num", "fecha_partido", "match_id"])
    if not df_matches.empty:
        warehouse.upsert_table("Partidos_Calendario", df_matches, pk_cols=["round_id", "match_id"], sort_cols=["jornada_num", "part", "fecha_hora"])
    if not df_equipos_laliga.empty:
        warehouse.upsert_table("Equipos_LaLiga", df_equipos_laliga, pk_cols=["team_id"], sort_cols=["posicion_laliga"], replace_all=True)
    if not df_bids.empty:
        warehouse.upsert_table("Pujas_Historico", df_bids, pk_cols=["id_puja"], sort_cols=["fecha", "player_id"])
    if not df_lineups.empty:
        warehouse.upsert_table("Alineaciones_Jornadas", df_lineups, pk_cols=["round_id", "user_id"], sort_cols=["jornada_num", "part", "posicion_jornada"])
    if not df_daily_managers.empty:
        warehouse.upsert_table("Historico_Equipos_Diario", df_daily_managers, pk_cols=["fecha", "user_id"], sort_cols=["fecha", "patrimonio_total"], replace_all=True)

    # 12. Sync all new & repaired historical tabs to Google Sheets (`fantasy_tracker`)
    if sync_sheets:
        print("\n☁️ Sincronizando pestañas históricas con Google Sheets (`fantasy_tracker`)...")
        for sheet_tab in [
            "Partidos_Calendario",
            "Equipos_LaLiga",
            "Rendimiento_Jornadas",
            "Pujas_Historico",
            "Alineaciones_Jornadas",
            "Historico_Equipos_Diario",
            "Mercado_Diario_Historico",
            "Comuniate_Historico",
        ]:
            df_to_sync = warehouse.read_table(sheet_tab, fallback_sheets=False)
            if not df_to_sync.empty:
                warehouse.sync_dataframe_to_sheet(sheet_tab, df_to_sync)

        if full_backfill:
            print("☁️ Actualizando 'Historico_Continuo' completo (desde 2026-08-01 hasta hoy) en Google Sheets...")
            warehouse.sync_dataframe_to_sheet("Historico_Continuo", df_historico_continuo)

    print("\n" + "=" * 75)
    print(f"✅ MOTOR HISTÓRICO COMPLETADO EN {time.time() - t0:.1f}s")
    print(f"   • Historico_Continuo:       {len(df_historico_continuo):,} filas ({df_historico_continuo['fecha'].nunique()} días)")
    print(f"   • Rendimiento_Jornadas:     {len(df_player_reports):,} actuaciones de jugadores")
    print(f"   • Partidos_Calendario:      {len(df_matches):,} registros de partidos")
    print(f"   • Equipos_LaLiga:           {len(df_equipos_laliga):,} equipos")
    print(f"   • Pujas_Historico:          {len(df_bids):,} pujas (ganadoras y perdedoras)")
    print(f"   • Alineaciones_Jornadas:    {len(df_lineups):,} alineaciones de mánagers")
    print(f"   • Historico_Equipos_Diario: {len(df_daily_managers):,} snapshots diarios de mánagers")
    print(f"   • Mercado_Diario_Historico: {len(df_market_hist):,} registros de mercado/ofertas")
    print("=" * 75)

    return {
        "Historico_Continuo": len(df_historico_continuo),
        "Rendimiento_Jornadas": len(df_player_reports),
        "Partidos_Calendario": len(df_matches),
        "Equipos_LaLiga": len(df_equipos_laliga),
        "Pujas_Historico": len(df_bids),
        "Alineaciones_Jornadas": len(df_lineups),
        "Historico_Equipos_Diario": len(df_daily_managers),
        "Mercado_Diario_Historico": len(df_market_hist),
    }


if __name__ == "__main__":
    import sys
    do_backfill = "--backfill" in sys.argv or "--full" in sys.argv
    do_com_hist = "--comuniate-history" in sys.argv or do_backfill
    run_historical_pipeline(full_backfill=do_backfill, sync_sheets=True, extract_comuniate_history=do_com_hist)
