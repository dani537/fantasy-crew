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
            "waiting_offers_by_pid": {},    # str(pid) -> {offer_id, amount, from_id, from_name, is_rival, ...}
            "rival_incoming_offers": [],    # list of incoming purchase offers from rival managers
            "incoming_counter_offers": [],  # list of rival counterOffers on players we bid on
            "outgoing_bids": [],            # list of outgoing purchase/clause offers locking bidding capacity
            "team_value": 0,                # estimated live squad value
            "max_credit": 0,                # 25% of team_value (AZ Finance 'quarterTeam')
            "committed_bids_eur": 0,        # sum of outgoing active purchase bids
            "available_max_bid": 0,         # balance + max_credit - committed_bids_eur
            "market_sales": [],             # list of dicts ready for listings_df
            "manager_id_by_name": {},       # manager_name -> user_id
            "live_player_status": {},       # str(pid) -> {status, price, subida_24h, name, team_id, position}
            "league_bulletin": {},          # Structured Liga news: clauses, deals, shields, standings, injuries
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
                    status_map_u = result.get("live_player_status") or {}
                    for off in (u_data.get("offers") or []):
                        from_obj = off.get("from") or {}
                        from_id = from_obj.get("id") if isinstance(from_obj, dict) else from_obj
                        if (
                            str(from_id) == str(team_id)
                            and str(off.get("status", "")).lower() == "waiting"
                            and off.get("type") in ("purchase", "clause")
                        ):
                            req = off.get("requestedPlayers") or []
                            first_req = req[0] if req else 0
                            req_pid = int(first_req.get("id", 0) if isinstance(first_req, dict) else first_req or 0)
                            to_obj = off.get("to")
                            to_id = int(to_obj.get("id", 0) or 0) if isinstance(to_obj, dict) else int(to_obj or 0)
                            to_name = (
                                str(to_obj.get("name", "Mercado"))
                                if isinstance(to_obj, dict) and to_obj.get("name")
                                else ("Mercado" if to_id == 0 else f"Manager_{to_id}")
                            )
                            created_ts = int(off.get("created") or off.get("date") or 0)
                            age_hours = round(max(0.0, (now_unix - created_ts) / 3600.0), 1) if created_ts > 0 else None
                            p_live_u = status_map_u.get(str(req_pid), {})
                            outgoing_bids.append({
                                "offer_id": int(off.get("id", 0)),
                                "amount": int(off.get("amount", 0)),
                                "player_id": req_pid,
                                "nombre": p_live_u.get("name") or f"ID {req_pid}",
                                "market_price": int(p_live_u.get("price", 0) or 0),
                                "subida_24h": int(p_live_u.get("subida_24h", 0) or 0),
                                "type": str(off.get("type", "purchase")),
                                "to": to_obj,
                                "to_id": to_id,
                                "to_name": to_name,
                                "created_ts": created_ts,
                                "age_hours": age_hours,
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
                        # Biwenger omits `user.name` on the authenticated user's own listings!
                        if seller_uid > 0 and seller_uid == int(team_id or 0):
                            seller_name = team_name or "Mío"
                        elif isinstance(u_obj, dict) and u_obj.get("name"):
                            seller_name = str(u_obj["name"])
                        elif seller_uid > 0:
                            seller_name = f"Manager_{seller_uid}"
                        else:
                            seller_name = "Mercado"

                        if seller_uid == team_id or (team_name and seller_name == team_name):
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

                    owned_set_m = {int(k) for k in (result.get("owned_players_meta") or {}).keys() if str(k).isdigit()}
                    machine_best_by_pid: Dict[int, float] = {}
                    counter_offers_list: List[Dict[str, Any]] = []
                    rival_incoming_list: List[Dict[str, Any]] = []

                    # Waiting offers received by us (strictly separate incoming purchase offers vs rival counterOffers!)
                    for off in (m_data.get("offers") or []):
                        st = str(off.get("status", "waiting") or "waiting").lower()
                        if st != "waiting":
                            continue
                        off_type = str(off.get("type", "purchase") or "purchase")
                        to_obj = off.get("to")
                        to_uid = int(to_obj.get("id", 0) or 0) if isinstance(to_obj, dict) else int(to_obj or 0)
                        from_obj = off.get("from")
                        from_uid = int(from_obj.get("id", 0) or 0) if isinstance(from_obj, dict) else 0
                        from_name = (
                            str(from_obj.get("name"))
                            if isinstance(from_obj, dict) and from_obj.get("name")
                            else ("Mercado" if from_uid == 0 else f"Manager_{from_uid}")
                        )

                        if from_uid == team_id:
                            # Outgoing bid placed by us, not an incoming offer
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
                        created_ts = int(off.get("created") or off.get("date") or 0)
                        if pid <= 0 or amt <= 0 or oid <= 0:
                            continue

                        p_live_o = result["live_player_status"].get(str(pid), {})
                        if off_type == "counterOffer":
                            counter_offers_list.append({
                                "offer_id": oid,
                                "player_id": pid,
                                "nombre": p_live_o.get("name") or f"ID {pid}",
                                "market_price": int(p_live_o.get("price", 0) or 0),
                                "subida_24h": int(p_live_o.get("subida_24h", 0) or 0),
                                "amount": int(amt),
                                "from_id": from_uid,
                                "from_name": from_name,
                                "created_ts": created_ts,
                                "age_hours": round(max(0.0, (now_unix - created_ts) / 3600.0), 1) if created_ts > 0 else None,
                            })
                            continue

                        # Incoming purchase offer must refer to a player we actually own (when roster is known)
                        if owned_set_m and pid not in owned_set_m:
                            continue

                        if from_uid == 0:
                            if amt > machine_best_by_pid.get(pid, 0.0):
                                machine_best_by_pid[pid] = amt
                        else:
                            rival_incoming_list.append({
                                "offer_id": oid,
                                "player_id": pid,
                                "nombre": p_live_o.get("name") or f"ID {pid}",
                                "market_price": int(p_live_o.get("price", 0) or 0),
                                "subida_24h": int(p_live_o.get("subida_24h", 0) or 0),
                                "amount": int(amt),
                                "from_id": from_uid,
                                "from_name": from_name,
                                "created_ts": created_ts,
                                "age_hours": round(max(0.0, (now_unix - created_ts) / 3600.0), 1) if created_ts > 0 else None,
                            })

                        prev_best = result["waiting_offers_by_pid"].get(str(pid))
                        if not prev_best or amt > float(prev_best["amount"]):
                            result["waiting_offers_by_pid"][str(pid)] = {
                                "offer_id": oid,
                                "amount": amt,
                                "from_id": from_uid,
                                "from_name": from_name,
                                "is_rival": bool(from_uid > 0),
                            }

                    for pid_s, of_meta in result["waiting_offers_by_pid"].items():
                        m_amt = float(machine_best_by_pid.get(int(pid_s), 0.0))
                        of_meta["machine_amount"] = m_amt
                        of_meta["rival_premium_eur"] = int(float(of_meta["amount"]) - m_amt) if (of_meta.get("is_rival") and m_amt > 0) else 0
                    for ro in rival_incoming_list:
                        m_amt = float(machine_best_by_pid.get(int(ro["player_id"]), 0.0))
                        ro["machine_amount"] = int(m_amt)
                        ro["rival_premium_eur"] = int(ro["amount"] - m_amt) if m_amt > 0 else 0

                    result["incoming_counter_offers"] = counter_offers_list
                    result["rival_incoming_offers"] = rival_incoming_list
            except Exception as e_m:
                print(f"⚠️ FastDeltaRefresh /market warning: {e_m}")

            # Call C: /api/v2/league/{id}?fields=*,standings,settings
            standings_rows: List[Dict[str, Any]] = []
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
                    standings_rows = list(lg_data.get("standings") or [])
                    for st_row in standings_rows:
                        uid = int(st_row.get("id", 0) or 0)
                        uname = str(st_row.get("name", "") or "").strip()
                        if uid > 0 and uname:
                            result["manager_id_by_name"][uname] = uid
                    os.makedirs("./data/raw", exist_ok=True)
                    with open("./data/raw/live_league_settings.json", "w", encoding="utf-8") as fl:
                        json.dump(lg_data, fl, ensure_ascii=False, indent=2)
            except Exception as e_l:
                print(f"⚠️ FastDeltaRefresh /league warning: {e_l}")

            # Call D: Lightweight /api/v2/league/{id}/board?limit=40&offset=0 for Liga News Bulletin
            result["league_bulletin"] = self._build_league_bulletin(
                auth=auth,
                hdrs=hdrs,
                league_id=league_id,
                team_id=team_id,
                team_name=team_name,
                now_unix=now_unix,
                status_map=result.get("live_player_status") or {},
                standings_rows=standings_rows,
            )
        else:
            result["league_bulletin"] = self._build_league_bulletin(
                auth=None,
                hdrs={},
                league_id=league_id,
                team_id=team_id,
                team_name=team_name,
                now_unix=int(time.time()),
                status_map=result.get("live_player_status") or {},
                standings_rows=[],
            )

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

        # Calculate team value, max credit, and available max bid using LeagueRulesEngine
        from src.tools.strategy_engine.league_rules import LeagueRulesEngine
        rules = LeagueRulesEngine.load()
        status_map = result.get("live_player_status") or {}
        owned_meta = result.get("owned_players_meta") or {}
        team_val = sum(
            int(status_map.get(str(pid), {}).get("price") or meta.get("purchase_price", 0))
            for pid, meta in owned_meta.items()
        )
        cur_bal = int(result.get("balance") or 0)
        max_official = int(round(rules.compute_max_official_bid(cur_bal, team_val)))
        max_credit = max(0, max_official - cur_bal)
        committed = sum(int(b.get("amount", 0)) for b in result.get("outgoing_bids", []))
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
    def _build_league_bulletin(
        auth: Optional[BiwengerAuth],
        hdrs: Dict[str, str],
        league_id: int,
        team_id: int,
        team_name: str,
        now_unix: int,
        status_map: Dict[str, Dict[str, Any]],
        standings_rows: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Builds a compact League News Bulletin (clauses, deals, shields, standings, LaLiga injuries/movers)."""
        pos_short_map = {
            "Portero": "GK",
            "Defensa": "DF",
            "Centrocampista": "MF",
            "Delantero": "FW",
        }

        def _pinfo(pid_val: Any) -> Dict[str, Any]:
            try:
                pid_i = int(pid_val or 0)
            except (TypeError, ValueError):
                pid_i = 0
            ps = status_map.get(str(pid_i), {}) if pid_i > 0 else {}
            pos_full = str(ps.get("position", "") or "")
            return {
                "player_id": pid_i,
                "nombre": str(ps.get("name") or f"Jugador #{pid_i}"),
                "pos": pos_short_map.get(pos_full, pos_full[:2].upper() if pos_full else ""),
                "team": str(ps.get("team") or ""),
                "price": int(ps.get("price", 0) or 0),
                "subida_24h": int(ps.get("subida_24h", 0) or 0),
                "status": str(ps.get("status", "ok") or "ok"),
            }

        # 1. Standings snapshot (live or cached live_league_settings.json)
        rows = list(standings_rows or [])
        if not rows and os.path.exists("./data/raw/live_league_settings.json"):
            try:
                with open("./data/raw/live_league_settings.json", "r", encoding="utf-8") as fl:
                    rows = list((json.load(fl) or {}).get("standings") or [])
            except Exception:
                rows = []

        norm_standings: List[Dict[str, Any]] = []
        rows_sorted = sorted(rows, key=lambda r: int(r.get("points", 0) or 0), reverse=True)
        for idx, sr in enumerate(rows_sorted, start=1):
            uid = int(sr.get("id", 0) or 0)
            uname = str(sr.get("name", "") or "").strip()
            pts = int(sr.get("points", 0) or 0)
            pos_rank = int(sr.get("position", 0) or idx)
            tv = int(sr.get("teamValue", 0) or 0)
            norm_standings.append({
                "rank": pos_rank,
                "id": uid,
                "name": uname,
                "points": pts,
                "team_value": tv,
                "is_me": (team_id > 0 and uid == team_id) or (bool(team_name) and uname == team_name),
            })
        norm_standings.sort(key=lambda r: (r["rank"], -r["points"]))

        my_standings: Dict[str, Any] = {}
        if norm_standings:
            leader = norm_standings[0]
            my_idx = next((i for i, r in enumerate(norm_standings) if r["is_me"]), None)
            if my_idx is not None:
                me = norm_standings[my_idx]
                above = norm_standings[my_idx - 1] if my_idx > 0 else None
                my_standings = {
                    "rank": me["rank"],
                    "points": me["points"],
                    "team_value": me["team_value"],
                    "total_managers": len(norm_standings),
                    "leader_name": leader["name"],
                    "leader_points": leader["points"],
                    "points_to_leader": max(0, leader["points"] - me["points"]),
                    "next_rival_above": above["name"] if above else "",
                    "points_to_next": max(0, above["points"] - me["points"]) if above else 0,
                }

        # 2. Live /api/v2/league/{id}/board feed (with SQLite fallback if offline)
        clauses: List[Dict[str, Any]] = []
        manager_deals: List[Dict[str, Any]] = []
        clause_shields: List[Dict[str, Any]] = []
        position_changes: List[Dict[str, Any]] = []
        latest_round: Dict[str, Any] = {}
        board_items: List[Dict[str, Any]] = []

        if auth and getattr(auth, "session", None) is not None and league_id > 0:
            try:
                board_hdrs = dict(hdrs)
                board_hdrs["Referer"] = "https://biwenger.as.com/board"
                r_b = auth.session.get(
                    f"{API_BASE}/league/{league_id}/board?limit=40&offset=0",
                    headers=board_hdrs,
                    timeout=12,
                )
                if r_b.status_code == 200:
                    board_items = list(r_b.json().get("data", []) or [])
            except Exception:
                board_items = []

        lookback_sec = 7 * 86400
        for item in board_items:
            ts = int(item.get("date", 0) or 0)
            age_h = round(max(0.0, (now_unix - ts) / 3600.0), 1) if ts > 0 else 999.0
            date_iso = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts > 0 else ""
            itype = str(item.get("type", "") or "")
            content = item.get("content")
            if content is None:
                continue

            if itype == "roundFinished" and not latest_round and isinstance(content, dict):
                rnd_name = str((content.get("round") or {}).get("name") or "")
                res_list = list(content.get("results") or [])
                best_r = max(res_list, key=lambda x: int(x.get("points", 0) or 0), default=None)
                my_r = next(
                    (
                        x for x in res_list
                        if int((x.get("user") or {}).get("id", 0) or 0) == team_id
                    ),
                    None,
                )
                latest_round = {
                    "round_name": rnd_name,
                    "top_scorer_name": str((best_r or {}).get("user", {}).get("name") or "") if isinstance(best_r, dict) else "",
                    "top_points": int((best_r or {}).get("points", 0) or 0) if isinstance(best_r, dict) else 0,
                    "top_bonus": int((best_r or {}).get("bonus", 0) or 0) if isinstance(best_r, dict) else 0,
                    "my_points": int((my_r or {}).get("points", 0) or 0) if isinstance(my_r, dict) else 0,
                    "my_bonus": int((my_r or {}).get("bonus", 0) or 0) if isinstance(my_r, dict) else 0,
                }
                continue

            if ts > 0 and (now_unix - ts) > lookback_sec:
                continue
            entries = content if isinstance(content, list) else [content]

            if itype == "transfer":
                for ent in entries:
                    if not isinstance(ent, dict):
                        continue
                    frm = ent.get("from")
                    to = ent.get("to")
                    frm_id = int(frm.get("id", 0) or 0) if isinstance(frm, dict) else 0
                    to_id = int(to.get("id", 0) or 0) if isinstance(to, dict) else 0
                    frm_name = str(frm.get("name") or team_name) if isinstance(frm, dict) else "Mercado"
                    to_name = str(to.get("name") or team_name) if isinstance(to, dict) else "Mercado"
                    amt = int(ent.get("amount", 0) or 0)
                    pi = _pinfo(ent.get("player"))
                    inv_me = (team_id > 0 and (frm_id == team_id or to_id == team_id))
                    if ent.get("type") == "clause":
                        clauses.append({
                            **pi,
                            "from_name": frm_name,
                            "to_name": to_name,
                            "amount": amt,
                            "date_iso": date_iso,
                            "hours_ago": age_h,
                            "involves_me": inv_me,
                        })
                    elif frm_id > 0 and to_id > 0:
                        manager_deals.append({
                            "deal_type": "MANAGER_TRADE",
                            **pi,
                            "from_name": frm_name,
                            "to_name": to_name,
                            "amount": amt,
                            "bid_count": 1,
                            "runner_up_amount": 0,
                            "date_iso": date_iso,
                            "hours_ago": age_h,
                            "involves_me": inv_me,
                        })
                    elif frm_id > 0 and to_id == 0 and amt >= 2_500_000:
                        manager_deals.append({
                            "deal_type": "RIVAL_SALE",
                            **pi,
                            "from_name": frm_name,
                            "to_name": "Mercado",
                            "amount": amt,
                            "bid_count": 1,
                            "runner_up_amount": 0,
                            "date_iso": date_iso,
                            "hours_ago": age_h,
                            "involves_me": inv_me,
                        })

            elif itype == "market":
                for ent in entries:
                    if not isinstance(ent, dict):
                        continue
                    to = ent.get("to")
                    to_id = int(to.get("id", 0) or 0) if isinstance(to, dict) else 0
                    if to_id <= 0:
                        continue
                    to_name = str(to.get("name") or team_name) if isinstance(to, dict) else "Manager"
                    amt = int(ent.get("amount", 0) or 0)
                    bids = [b for b in (ent.get("bids") or []) if isinstance(b, dict)]
                    runner_up = max(
                        (
                            int(b.get("amount", 0) or 0)
                            for b in bids
                            if int((b.get("user") or {}).get("id", 0) or 0) != to_id
                        ),
                        default=0,
                    )
                    inv_me = (team_id > 0 and to_id == team_id)
                    if amt >= 1_200_000 or len(bids) >= 2 or inv_me:
                        pi = _pinfo(ent.get("player"))
                        manager_deals.append({
                            "deal_type": "MARKET_AUCTION",
                            **pi,
                            "from_name": "Mercado",
                            "to_name": to_name,
                            "amount": amt,
                            "bid_count": len(bids),
                            "runner_up_amount": runner_up,
                            "date_iso": date_iso,
                            "hours_ago": age_h,
                            "involves_me": inv_me,
                        })

            elif itype == "clauseIncrement":
                for ent in entries:
                    if not isinstance(ent, dict):
                        continue
                    u = ent.get("user") if isinstance(ent.get("user"), dict) else {}
                    pi = _pinfo(ent.get("player"))
                    clause_shields.append({
                        **pi,
                        "user_name": str(u.get("name") or ""),
                        "amount": int(ent.get("amount", 0) or 0),
                        "release_clause": int(ent.get("releaseClause", 0) or 0),
                        "date_iso": date_iso,
                        "hours_ago": age_h,
                    })

            elif itype == "playerMovements":
                pos_code_map = {1: "GK", 2: "DF", 3: "MF", 4: "FW"}
                for ent in entries:
                    if not isinstance(ent, dict) or ent.get("type") != "position":
                        continue
                    pi = _pinfo(ent.get("player"))
                    fr_l = ent.get("from") if isinstance(ent.get("from"), list) else [ent.get("from")]
                    to_l = ent.get("to") if isinstance(ent.get("to"), list) else [ent.get("to")]
                    position_changes.append({
                        **pi,
                        "from_pos": "/".join(pos_code_map.get(int(x), str(x)) for x in fr_l if x is not None),
                        "to_pos": "/".join(pos_code_map.get(int(x), str(x)) for x in to_l if x is not None),
                        "date_iso": date_iso,
                    })

        # SQLite fallback when /board returned empty (offline / cache mode)
        if not board_items and os.path.exists("./data/warehouse/biwenger_history.db"):
            try:
                import sqlite3
                conn = sqlite3.connect("./data/warehouse/biwenger_history.db")
                since_dt = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
                rows_db = conn.execute(
                    "SELECT fecha, tipo, jugador, player_id, comprador, vendedor, precio, es_clausula "
                    "FROM Movimientos WHERE fecha >= ? ORDER BY fecha DESC LIMIT 40",
                    (since_dt,),
                ).fetchall()
                conn.close()
                for f_dt, tp, jug, pid_db, comp, vend, pr_db, es_cl in rows_db:
                    amt = int(pr_db or 0)
                    pi = _pinfo(pid_db)
                    if jug:
                        pi["nombre"] = str(jug)
                    inv_me = bool(team_name and (comp == team_name or vend == team_name))
                    if str(es_cl).upper() in ("SÍ", "SI", "1", "TRUE") or "Clausul" in str(tp):
                        clauses.append({
                            **pi,
                            "from_name": str(vend or ""),
                            "to_name": str(comp or ""),
                            "amount": amt,
                            "date_iso": str(f_dt)[:16],
                            "hours_ago": None,
                            "involves_me": inv_me,
                        })
                    elif amt >= 1_200_000 and str(comp or "") != "Mercado":
                        manager_deals.append({
                            "deal_type": "MANAGER_TRADE" if str(vend or "") not in ("Mercado", "Biwenger", "") else "MARKET_AUCTION",
                            **pi,
                            "from_name": str(vend or "Mercado"),
                            "to_name": str(comp or ""),
                            "amount": amt,
                            "bid_count": 1,
                            "runner_up_amount": 0,
                            "date_iso": str(f_dt)[:16],
                            "hours_ago": None,
                            "involves_me": inv_me,
                        })
            except Exception:
                pass

        # Sort manager deals concisely: manager trades & fresh auctions first, then size
        manager_deals.sort(
            key=lambda d: (
                1 if d.get("involves_me") else 0,
                1 if d.get("deal_type") == "MANAGER_TRADE" else 0,
                1 if (d.get("hours_ago") is not None and d["hours_ago"] <= 36.0) else 0,
                int(d.get("amount", 0)),
            ),
            reverse=True,
        )

        # 3. LaLiga Injury / Suspension Alerts & 24h Market Movers from CDN
        inj_rows: List[Dict[str, Any]] = []
        mover_rows: List[Dict[str, Any]] = []
        for pid_s, info in (status_map or {}).items():
            pr = int(info.get("price", 0) or 0)
            st = str(info.get("status", "ok") or "ok")
            sub = int(info.get("subida_24h", 0) or 0)
            pos_full = str(info.get("position", "") or "")
            pos_s = pos_short_map.get(pos_full, pos_full[:2].upper() if pos_full else "")
            if pr >= 3_500_000 and st in ("injured", "suspended", "doubtful", "sanctioned"):
                inj_rows.append({
                    "player_id": int(pid_s) if pid_s.isdigit() else 0,
                    "nombre": str(info.get("name") or ""),
                    "team": str(info.get("team") or ""),
                    "pos": pos_s,
                    "status": st,
                    "status_info": str(info.get("status_info") or ""),
                    "price": pr,
                })
            if abs(sub) >= 120_000 and pr >= 500_000:
                mover_rows.append({
                    "player_id": int(pid_s) if pid_s.isdigit() else 0,
                    "nombre": str(info.get("name") or ""),
                    "team": str(info.get("team") or ""),
                    "pos": pos_s,
                    "price": pr,
                    "subida_24h": sub,
                })

        inj_rows.sort(key=lambda x: x["price"], reverse=True)
        risers = sorted([m for m in mover_rows if m["subida_24h"] > 0], key=lambda x: x["subida_24h"], reverse=True)[:3]
        fallers = sorted([m for m in mover_rows if m["subida_24h"] < 0], key=lambda x: x["subida_24h"])[:3]

        return {
            "clauses": clauses[:6],
            "manager_deals": manager_deals[:6],
            "clause_shields": clause_shields[:4],
            "position_changes": position_changes[:4],
            "latest_round": latest_round,
            "standings_top": norm_standings[:4],
            "my_standings": my_standings,
            "injury_alerts": inj_rows[:5],
            "market_risers": risers,
            "market_fallers": fallers,
        }

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

            owned_mask = df["player_id"].isin(owned_set)
            if "precio_compra" not in df.columns:
                df["precio_compra"] = 0
            df.loc[owned_mask, "precio_compra"] = df.loc[owned_mask, "player_id"].apply(
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
