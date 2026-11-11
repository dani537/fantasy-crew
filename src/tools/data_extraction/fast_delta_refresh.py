"""
Fast Intraday Delta Refresh (`FastDeltaRefresh`, <12 Seconds, <= 4 API Calls)
=============================================================================
Lightweight real-time state synchronizer designed for:
- Intraday tactical triggers (`PRE_CLAUSE_LOCK`, `PRE_KICKOFF_SOLVENCY`, `FINAL_LINEUP_GUARD`)
- Interactive Telegram Bot queries (`/estado`, `/once`, `/plan`, `/aprobar`, natural chat)
- Pre-execution live offer ID & listing slot resolution inside `AutopilotRunner`

Consumes at most 3 authenticated Biwenger API requests + 1 public CDN call (out of 200 req/h):
  1. `GET /api/v2/user?fields=*,lineup,players(id,owner),market,offers` (live balance, squad, locks)
  2. `GET /api/v2/market` (live `sales`, `currently_listed_pids`, and waiting `offer_id`s)
  3. `GET /api/v2/league/{id}?fields=*,standings,settings` (rules + `manager_name -> user_id`)
  4. `GET https://cf.biwenger.com/api/v2/competitions/la-liga/data` (live injury/suspension statuses & prices)
Plus optional Comuniate probable-XI refresh for `target_jornada_num`.
"""

import os
import json
import time
import datetime
from typing import Dict, List, Any, Optional, Set
import pandas as pd
import requests

from src.config import Credentials, resolve_active_user_identity
from src.tools.data_extraction.auth import BiwengerAuth
from src.tools.data_extraction.daily_market_tracker import POSITION_MAP, get_headers as get_cdn_headers


API_BASE = "https://biwenger.as.com/api/v2"
LIVE_DELTA_CACHE_PATH = "./data/raw/live_delta_state.json"


class FastDeltaRefresh:
    """Executes a <12s live delta refresh with minimal API quota footprint."""

    def __init__(self, session_auth: Optional[BiwengerAuth] = None):
        self.session_auth = session_auth

    def _ensure_auth(self) -> Optional[BiwengerAuth]:
        if self.session_auth is not None and getattr(self.session_auth, "session", None) is not None:
            return self.session_auth
        if not (Credentials.BIWENGER_TOKEN or (Credentials.BIWENGER_USERNAME and Credentials.BIWENGER_PASSWORD)):
            return None
        try:
            auth = BiwengerAuth(
                email=Credentials.BIWENGER_USERNAME,
                password=Credentials.BIWENGER_PASSWORD,
                token=Credentials.BIWENGER_TOKEN,
            )
            auth.run()
            self.session_auth = auth
            return auth
        except Exception as e:
            print(f"⚠️ FastDeltaRefresh: No se pudo autenticar en vivo ({e}). Usando caché local.")
            return None

    def fetch(
        self,
        refresh_comuniate_jornada: Optional[int] = None,
        max_cache_age_seconds: int = 0,
    ) -> Dict[str, Any]:
        """
        Fetches live delta state from Biwenger API + CDN (or returns recent cache if within `max_cache_age_seconds`).
        """
        t0 = time.time()
        if max_cache_age_seconds > 0 and os.path.exists(LIVE_DELTA_CACHE_PATH):
            try:
                age = time.time() - os.path.getmtime(LIVE_DELTA_CACHE_PATH)
                if age <= max_cache_age_seconds:
                    with open(LIVE_DELTA_CACHE_PATH, "r", encoding="utf-8") as f:
                        cached = json.load(f)
                    cached["from_cache"] = True
                    return cached
            except Exception:
                pass

        ident = resolve_active_user_identity()
        auth = self._ensure_auth()

        league_id = int(ident.get("league_id") or 0)
        team_id = int(ident.get("team_id") or 0)
        team_name = str(ident.get("team_name") or "Mi Equipo")
        league_name = str(ident.get("league_name") or "Liga Biwenger")
        score_id = int(ident.get("score_id") or 5)
        balance: Optional[float] = ident.get("balance")

        if auth and auth.player_info:
            league_id = int(auth.player_info.league_id or league_id)
            team_id = int(auth.player_info.team_id or team_id)
            team_name = str(auth.player_info.team_name or team_name)
            league_name = str(auth.player_info.league_name or league_name)
            if auth.player_info.balance is not None:
                balance = float(auth.player_info.balance)

        result: Dict[str, Any] = {
            "fetched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "is_live": False,
            "league_id": league_id,
            "league_name": league_name,
            "team_id": team_id,
            "team_name": team_name,
            "score_id": score_id,
            "balance": balance,
            "owned_players_meta": {},       # str(pid) -> {purchase_price, acquired_ts, clause, clause_locked_until}
            "current_lineup_ids": [],
            "currently_listed_pids": [],    # list[int] of our own players currently listed on market
            "waiting_offers_by_pid": {},    # str(pid) -> {offer_id, amount, from_id, from_name}
            "outgoing_bids": [],            # list of outgoing purchase/clause offers locking bidding capacity
            "team_value": 0,                # estimated live squad value
            "max_credit": 0,                # 25% of team_value (AZ Finance 'quarterTeam')
            "committed_bids_eur": 0,        # sum of outgoing active purchase bids
            "available_max_bid": 0,         # balance + max_credit - committed_bids_eur
            "market_sales": [],             # list of dicts ready for listings_df
            "manager_id_by_name": {},       # manager_name -> user_id
            "live_player_status": {},       # str(pid) -> {status, price, subida_24h, name, team_id, position}
            "rate_limit_remaining": None,
            "elapsed_sec": 0.0,
        }

        # 1. Public CDN call (0 user quota): live injury/suspension statuses + today's prices
        teams_map: Dict[int, str] = {}
        try:
            cdn_url = f"https://cf.biwenger.com/api/v2/competitions/la-liga/data?lang=es&score={score_id}"
            r_cdn = requests.get(cdn_url, headers=get_cdn_headers(), timeout=12)
            if r_cdn.status_code == 200:
                cdn_data = r_cdn.json().get("data", {})
                for tid_s, t_obj in (cdn_data.get("teams") or {}).items():
                    teams_map[int(tid_s)] = str(t_obj.get("name", ""))
                for pid_s, p_obj in (cdn_data.get("players") or {}).items():
                    result["live_player_status"][str(pid_s)] = {
                        "status": str(p_obj.get("status", "ok") or "ok"),
                        "status_info": str(p_obj.get("statusInfo", "") or ""),
                        "price": int(p_obj.get("price", 0) or 0),
                        "subida_24h": int(p_obj.get("priceIncrement", 0) or 0),
                        "name": str(p_obj.get("name", "")),
                        "team": teams_map.get(int(p_obj.get("teamID", 0) or 0), ""),
                        "position": POSITION_MAP.get(int(p_obj.get("position", 3) or 3), "Centrocampista"),
                    }
        except Exception as e_cdn:
            print(f"⚠️ FastDeltaRefresh CDN warning: {e_cdn}")

        # 2. Authenticated Biwenger API calls (3 lightweight calls)
        if auth and auth.session and league_id and team_id:
            hdrs = {
                "Authorization": f"Bearer {auth.token}",
                "X-League": str(league_id),
                "X-User": str(team_id),
                "Accept": "application/json",
            }
            now_unix = int(time.time())

            # Call A: /api/v2/user
            try:
                r_usr = auth.session.get(
                    f"{API_BASE}/user?fields=*,lineup,players(id,owner),market,offers",
                    headers=hdrs,
                    timeout=12,
                )
                rem = r_usr.headers.get("x-rate-limit-remaining")
                if rem and str(rem).isdigit():
                    result["rate_limit_remaining"] = int(rem)
                if r_usr.status_code == 200:
                    u_data = r_usr.json().get("data", {})
                    if u_data.get("balance") is not None:
                        result["balance"] = float(u_data["balance"])
                    result["is_live"] = True

                    lineup_obj = u_data.get("lineup") or {}
                    if isinstance(lineup_obj, dict) and isinstance(lineup_obj.get("playersID"), list):
                        result["current_lineup_ids"] = [int(x) for x in lineup_obj["playersID"] if str(x).isdigit()]

                    for p in (u_data.get("players") or []):
                        pid = int(p.get("id", 0) or 0)
                        if pid <= 0:
                            continue
                        own = p.get("owner") or {}
                        acq_ts = int(own.get("date", 0) or 0)
                        lock_until = int(own.get("clauseLockedUntil", 0) or 0)
                        result["owned_players_meta"][str(pid)] = {
                            "purchase_price": int(own.get("price", 0) or 0),
                            "acquired_date": (
                                datetime.datetime.fromtimestamp(acq_ts).strftime("%Y-%m-%d")
                                if acq_ts > 0 else ""
                            ),
                            "sell_locked": bool(acq_ts > 0 and (now_unix - acq_ts) < 86400),
                            "clause": int(own.get("clause", 0) or 0),
                            "clause_locked": bool(lock_until > now_unix),
                            "clause_locked_until": lock_until,
                        }

                    outgoing_bids: List[Dict[str, Any]] = []
                    for off in (u_data.get("offers") or []):
                        if (
                            str(off.get("from", {}).get("id")) == str(team_id)
                            and str(off.get("status", "")).lower() == "waiting"
                            and off.get("type") in ("purchase", "clause")
                        ):
                            req = off.get("requestedPlayers") or []
                            outgoing_bids.append({
                                "offer_id": int(off.get("id", 0)),
                                "amount": int(off.get("amount", 0)),
                                "player_id": int(req[0]) if req else 0,
                                "type": str(off.get("type", "purchase")),
                                "to": off.get("to"),
                            })
                    result["outgoing_bids"] = outgoing_bids

                    os.makedirs("./data/raw", exist_ok=True)
                    with open("./data/raw/live_user_state.json", "w", encoding="utf-8") as fu:
                        json.dump(u_data, fu, ensure_ascii=False, indent=2)
            except Exception as e_u:
                print(f"⚠️ FastDeltaRefresh /user warning: {e_u}")

            # Call B: /api/v2/market
            try:
                r_mkt = auth.session.get(f"{API_BASE}/market", headers=hdrs, timeout=12)
                rem = r_mkt.headers.get("x-rate-limit-remaining")
                if rem and str(rem).isdigit():
                    result["rate_limit_remaining"] = int(rem)
                if r_mkt.status_code == 200:
                    m_data = r_mkt.json().get("data", {})
                    listed_mine: List[int] = []
                    sales_rows: List[Dict[str, Any]] = []

                    for sale in (m_data.get("sales") or []):
                        p_obj = sale.get("player") or {}
                        pid = int(p_obj.get("id", 0) or 0)
                        if pid <= 0:
                            continue
                        u_obj = sale.get("user") or {}
                        seller_uid = int(u_obj.get("id", 0) or 0) if u_obj else 0
                        seller_name = str(u_obj.get("name", "Mercado")) if u_obj else "Mercado"
                        if seller_uid == team_id or seller_name == team_name:
                            listed_mine.append(pid)

                        p_live = result["live_player_status"].get(str(pid), {})
                        sales_rows.append({
                            "player_id": pid,
                            "jugador": p_live.get("name") or f"ID {pid}",
                            "seller_name": seller_name,
                            "seller_user_id": seller_uid if seller_uid > 0 else None,
                            "precio_mercado_dia": p_live.get("price", int(sale.get("price", 0) or 0)),
                            "ask_price": int(sale.get("price", 0) or 0),
                        })

                    result["currently_listed_pids"] = sorted(set(listed_mine))
                    result["market_sales"] = sales_rows

                    # Waiting offers received by us (with exact `offer_id`!)
                    for off in (m_data.get("offers") or []):
                        st = str(off.get("status", "waiting") or "waiting").lower()
                        if st != "waiting":
                            continue
                        # Ensure it is an incoming offer on our player (`to` is us, or `from` is None/rival and player is ours)
                        to_obj = off.get("to")
                        to_uid = int(to_obj.get("id", 0) or 0) if isinstance(to_obj, dict) else int(to_obj or 0)
                        from_obj = off.get("from")
                        from_uid = int(from_obj.get("id", 0) or 0) if isinstance(from_obj, dict) else 0
                        from_name = str(from_obj.get("name", "Mercado")) if isinstance(from_obj, dict) else "Mercado"

                        if from_uid == team_id:
                            # Outgoing bid placed by us, not an incoming offer to accept
                            continue
                        if to_uid not in (0, team_id):
                            continue

                        req = off.get("requestedPlayers") or []
                        if not req:
                            continue
                        first = req[0]
                        pid = int(first.get("id", 0) if isinstance(first, dict) else first)
                        amt = float(off.get("amount", 0) or 0)
                        oid = int(off.get("id", 0) or 0)
                        if pid <= 0 or amt <= 0 or oid <= 0:
                            continue

                        prev_best = result["waiting_offers_by_pid"].get(str(pid))
                        if not prev_best or amt > float(prev_best["amount"]):
                            result["waiting_offers_by_pid"][str(pid)] = {
                                "offer_id": oid,
                                "amount": amt,
                                "from_id": from_uid,
                                "from_name": from_name,
                            }
            except Exception as e_m:
                print(f"⚠️ FastDeltaRefresh /market warning: {e_m}")

            # Call C: /api/v2/league/{id}?fields=*,standings,settings
            try:
                r_lg = auth.session.get(
                    f"{API_BASE}/league/{league_id}?include=all,-lastAccess&fields=*,standings,settings",
                    headers=hdrs,
                    timeout=12,
                )
                rem = r_lg.headers.get("x-rate-limit-remaining")
                if rem and str(rem).isdigit():
                    result["rate_limit_remaining"] = int(rem)
                if r_lg.status_code == 200:
                    lg_data = r_lg.json().get("data", {})
                    for st_row in (lg_data.get("standings") or []):
                        uid = int(st_row.get("id", 0) or 0)
                        uname = str(st_row.get("name", "") or "").strip()
                        if uid > 0 and uname:
                            result["manager_id_by_name"][uname] = uid
                    os.makedirs("./data/raw", exist_ok=True)
                    with open("./data/raw/live_league_settings.json", "w", encoding="utf-8") as fl:
                        json.dump(lg_data, fl, ensure_ascii=False, indent=2)
            except Exception as e_l:
                print(f"⚠️ FastDeltaRefresh /league warning: {e_l}")

        # Optional Comuniate probable-XI refresh
        if refresh_comuniate_jornada is not None and refresh_comuniate_jornada > 0:
            try:
                from src.tools.data_extraction.external_data import ComuniateData
                com = ComuniateData()
                df_c = com.get_all_probable_lineups()
                if df_c is not None and not df_c.empty:
                    result["comuniate_refreshed_rows"] = len(df_c)
            except Exception:
                pass

        # Calculate team value, max credit, and available max bid
        status_map = result.get("live_player_status") or {}
        owned_meta = result.get("owned_players_meta") or {}
        team_val = sum(
            int(status_map.get(str(pid), {}).get("price") or meta.get("purchase_price", 0))
            for pid, meta in owned_meta.items()
        )
        max_credit = int(team_val * 0.25)
        committed = sum(int(b.get("amount", 0)) for b in result.get("outgoing_bids", []))
        cur_bal = int(result.get("balance") or 0)
        result["team_value"] = team_val
        result["max_credit"] = max_credit
        result["committed_bids_eur"] = committed
        result["available_max_bid"] = max(0, cur_bal + max_credit - committed)

        result["elapsed_sec"] = round(time.time() - t0, 2)
        os.makedirs(os.path.dirname(LIVE_DELTA_CACHE_PATH) or ".", exist_ok=True)
        with open(LIVE_DELTA_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        return result

    @staticmethod
    def overlay_live_status_on_market_df(
        df_market: pd.DataFrame,
        delta_state: Dict[str, Any],
    ) -> pd.DataFrame:
        """
        Patches `df_market` in-memory with real-time CDN `estado`, `precio`, `subida_24h`
        and live `clausula_actual` / `precio_compra` / `acquired_date` for our owned squad!
        """
        if df_market is None or df_market.empty or not delta_state:
            return df_market

        df = df_market.copy()
        df["player_id"] = pd.to_numeric(df["player_id"], errors="coerce").fillna(0).astype(int)
        live_status = delta_state.get("live_player_status") or {}
        owned_meta = delta_state.get("owned_players_meta") or {}
        team_name = str(delta_state.get("team_name") or "")

        if live_status:
            df["estado"] = df.apply(
                lambda r: live_status.get(str(int(r["player_id"])), {}).get("status", r.get("estado", "ok")),
                axis=1,
            )
            df["precio"] = df.apply(
                lambda r: live_status.get(str(int(r["player_id"])), {}).get("price") or r.get("precio", 0),
                axis=1,
            )
            df["subida_24h"] = df.apply(
                lambda r: live_status.get(str(int(r["player_id"])), {}).get("subida_24h", r.get("subida_24h", 0)),
                axis=1,
            )

        if owned_meta and delta_state.get("is_live"):
            owned_set = {int(k) for k in owned_meta.keys() if str(k).isdigit()}
            if team_name and "propietario_liga" in df.columns:
                # Clear stale ownership for players sold earlier today and mark newly acquired ones
                df.loc[(df["propietario_liga"] == team_name) & (~df["player_id"].isin(owned_set)), "propietario_liga"] = "Mercado"
                df.loc[df["player_id"].isin(owned_set), "propietario_liga"] = team_name

            df["precio_compra"] = df["player_id"].apply(
                lambda pid: owned_meta.get(str(int(pid)), {}).get("purchase_price", 0)
            )
            df["acquired_date"] = df["player_id"].apply(
                lambda pid: owned_meta.get(str(int(pid)), {}).get("acquired_date", "")
            )
            if "clausula_actual" in df.columns:
                df["clausula_actual"] = df.apply(
                    lambda r: owned_meta.get(str(int(r["player_id"])), {}).get("clause") or r.get("clausula_actual", 0),
                    axis=1,
                )

        return df
