"""
Biwenger Market & Financial Tracker (Google Sheets Integration)
==============================================================

This module connects Biwenger league movements (purchases, sales, bids, clauses)
with a Google Spreadsheet. It performs incremental synchronizations (default: last 7 days)
or full historical syncs, merges them into a complete historical ledger without duplicates,
and computes estimated cash balances, team metrics, and total net worth for all managers.

Sheets:
  1. 'Movimientos'       : Complete historical log of transfers and sales.
  2. 'Config_Inicial'    : Baseline budgets, initial squad values, and manual bonus adjustments per manager.
  3. 'Saldos_Estimados'  : Real-time estimated available cash, squad size, league standing, and total net worth.
"""

import os
import sys
import datetime
import pandas as pd
import gspread
from typing import Optional, Dict, Any, List

from src.config import Credentials, GeneralSettings, GoogleSheetsConfig
from src.tools.data_extraction.auth import BiwengerAuth
from src.tools.data_extraction.biwenger_data import BiwengerGeneralData, UserLeagueData


SHEET_MOVIMIENTOS = "Movimientos"
SHEET_CONFIG = "Config_Inicial"
SHEET_SALDOS = "Saldos_Estimados"
SHEET_PRIMAS = "Primas_Jornadas"
SHEET_SQUAD = "Mi_Plantilla"
SHEET_USER_STATUS = "Estado_Usuario"
SHEET_MARKET = "Mercado_Fichajes"

DEFAULT_DAYS_BACK = int(os.getenv("TRACKER_DAYS_BACK", "7"))


def _clean_id(val: Any) -> str:
    """Normalizes manager/player ID into a clean integer string or empty string."""
    if val is None or pd.isna(val):
        return ""
    val_str = str(val).strip()
    if val_str.endswith(".0"):
        val_str = val_str[:-2]
    if val_str.lower() in ("nan", "none", "0", ""):
        return ""
    return val_str


class BiwengerSheetsTracker:
    """Manages the synchronization between Biwenger and Google Sheets."""

    def __init__(self, sheet_id: Optional[str] = None, creds_path: Optional[str] = None):
        self.sheet_id = sheet_id or os.getenv("GOOGLE_SHEET_ID") or GoogleSheetsConfig.SHEET_ID
        self.creds_path = creds_path or os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or GoogleSheetsConfig.SERVICE_ACCOUNT_FILE
        
        if not self.sheet_id:
            raise ValueError("GOOGLE_SHEET_ID is not configured in .env or settings.")
        if not self.creds_path or not os.path.exists(self.creds_path):
            raise FileNotFoundError(f"Service account file not found at: {self.creds_path}")

        self.gc = gspread.service_account(filename=self.creds_path)
        self.spreadsheet = self.gc.open_by_key(self.sheet_id)

    def _get_or_create_worksheet(self, title: str, rows: int = 100, cols: int = 20):
        """Gets an existing worksheet or creates it if it doesn't exist."""
        try:
            return self.spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            print(f"📄 Recreando hoja '{title}' en Google Sheets...")
            return self.spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)

    def _ensure_default_sheet_cleanup(self):
        """Removes default 'Hoja 1' / 'Sheet1' if our custom sheets exist."""
        worksheets = [ws.title for ws in self.spreadsheet.worksheets()]
        for default_title in ["Hoja 1", "Sheet1"]:
            if default_title in worksheets and len(worksheets) > 1:
                try:
                    ws = self.spreadsheet.worksheet(default_title)
                    self.spreadsheet.del_worksheet(ws)
                except Exception:
                    pass

    def sync(
        self,
        days_back: Optional[int] = None,
        full: bool = False,
        reset_sheets: bool = False,
        df_players_enriched: Optional[pd.DataFrame] = None
    ) -> Dict[str, Any]:
        """
        Main synchronization pipeline:
          1. Connect to Biwenger API and check existing movements in Sheets.
          2. Auto-detect last recorded movement date for fast incremental syncing.
          3. Extract only new board transactions, round bonuses, and clause blindajes.
          4. Map player names from General Data.
          5. Append only new records to 'Movimientos' (deduplicated).
          6. Update 'Primas_Jornadas' with round bonuses breakdown (cumulative).
          7. Ensure 'Config_Inicial' contains all managers with their baseline values.
          8. Recalculate estimated balances, calibrate with Dani's real balance, and update 'Saldos_Estimados'.
          9. Sync operational sheets: 'Mi_Plantilla', 'Estado_Usuario', 'Mercado_Fichajes'.
          10. Export rich rival metrics to local CSV './data/rival_financials.csv'.
        """
        # Open / create 'Movimientos' first to inspect latest recorded movement
        ws_mov = self._get_or_create_worksheet(SHEET_MOVIMIENTOS, rows=1000, cols=12)
        if reset_sheets:
            ws_mov.clear()

        # Incremental detection: find latest movement date in Movimientos
        latest_movement_date = None
        if not reset_sheets:
            mov_values = ws_mov.get_all_values()
            if len(mov_values) > 1:
                dates = []
                for r in mov_values[1:]:
                    if len(r) > 1 and r[1]:
                        try:
                            dates.append(pd.to_datetime(r[1]))
                        except Exception:
                            pass
                if dates:
                    latest_movement_date = max(dates)

        if not full and latest_movement_date is not None and days_back is None:
            # Calculate days back since latest movement + 1 day safety buffer
            now = pd.Timestamp.now()
            diff_days = (now - latest_movement_date).total_seconds() / 86400.0
            effective_days = max(2, int(diff_days) + 1)
            fetch_all = False
            sync_desc = f"INCREMENTAL (Últimos {effective_days} días, desde {latest_movement_date.strftime('%Y-%m-%d %H:%M')})"
        else:
            effective_days = None if full else days_back
            fetch_all = full or (effective_days is None)
            sync_desc = "HISTÓRICO COMPLETO (Desde reinicio de liga)" if (full or effective_days is None) else f"Últimos {effective_days} días"

        print("=" * 65)
        print(f"📊 BIWENGER TRACKER - Sincronizando: {sync_desc}")
        print(f"📅 Timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"🔗 Spreadsheet: {self.spreadsheet.title}")
        print("=" * 65)

        # 1. Login to Biwenger
        print("\n🔑 Authenticating with Biwenger API...")
        auth = BiwengerAuth(email=Credentials.BIWENGER_USERNAME, password=Credentials.BIWENGER_PASSWORD)
        auth.run()
        user_real_balance = getattr(auth.player_info, 'balance', None) if auth.player_info else None
        current_user_id = _clean_id(getattr(auth.player_info, 'team_id', None)) if auth.player_info else None

        # 2. General player data for name mapping
        print("⚽ Fetching player database for name resolution...")
        comp_slug = auth.player_info.competition_slug if auth.player_info else "la-liga"
        general_data = BiwengerGeneralData(session=None, competition_slug=comp_slug)
        df_players = general_data.players_info()
        player_names_map = dict(zip(df_players['id'], df_players['name']))

        # 3. Extract Board info
        print(f"📥 Extracting board activity ({sync_desc})...")
        user_league_data = UserLeagueData(
            session=auth.session,
            token=auth.token,
            league_id=auth.player_info.league_id,
            user_id=auth.player_info.team_id
        )
        board_res = user_league_data.league_board_info(auth.session, fetch_all=fetch_all, days_back=effective_days)
        df_transfers = board_res.get('transfers', pd.DataFrame())
        df_bonuses = board_res.get('bonuses', pd.DataFrame())
        df_clause_inc = board_res.get('clause_increments', pd.DataFrame())
        print(f"   📥 Total transacciones de mercado/fichajes: {len(df_transfers)}")
        print(f"   🏆 Total primas de jornada registradas: {len(df_bonuses)}")
        print(f"   🛡️ Total blindajes de cláusulas registrados: {len(df_clause_inc)}")

        # Standings table for team values, points, position, and manager IDs
        user_league_data._league_table_data(auth.session)
        df_standings = user_league_data.league_table()

        # Extract live round lineups and standings
        round_standings = user_league_data.league_round_standings(auth.session)

        # Market sales (players on sale today)
        try:
            df_market_sales = user_league_data.market_sales_info()
        except Exception:
            df_market_sales = pd.DataFrame()

        # 4. Sync 'Movimientos' Worksheet
        print("\n📋 Synchronizing 'Movimientos' worksheet...")
        added_count = self._sync_movements(ws_mov, df_transfers, player_names_map)

        # 4b. Sync 'Primas_Jornadas' Worksheet
        print("🏆 Synchronizing 'Primas_Jornadas' worksheet...")
        ws_primas = self._get_or_create_worksheet(SHEET_PRIMAS, rows=150, cols=7)
        if reset_sheets:
            ws_primas.clear()
        self._sync_round_bonuses(ws_primas, df_bonuses)

        # 5. Sync 'Config_Inicial' Worksheet
        print("⚙️ Checking 'Config_Inicial' worksheet...")
        ws_cfg = self._get_or_create_worksheet(SHEET_CONFIG, rows=50, cols=6)
        if reset_sheets:
            ws_cfg.clear()
        config_data = self._sync_initial_config(ws_cfg, df_standings)

        # 6. Recalculate and update 'Saldos_Estimados'
        print("💰 Calculating updated balances and reconciling with current user...")
        ws_saldos = self._get_or_create_worksheet(SHEET_SALDOS, rows=50, cols=22)
        saldos_df = self._update_saldos(
            ws_mov, ws_saldos, ws_primas, config_data, df_standings, df_players,
            round_standings, df_bonuses, df_clause_inc, user_real_balance, current_user_id
        )

        # 7. Sync operational dashboard sheets (Mi_Plantilla, Estado_Usuario, Mercado_Fichajes)
        active_players_df = df_players_enriched if (df_players_enriched is not None and not df_players_enriched.empty) else None
        if active_players_df is None and os.path.exists("./data/players_transformed.csv"):
            try:
                active_players_df = pd.read_csv("./data/players_transformed.csv")
            except Exception:
                pass
        if active_players_df is None or active_players_df.empty:
            active_players_df = df_players

        self._sync_dashboard_sheets(
            df_players=active_players_df,
            auth_player_info=auth.player_info,
            df_market_sales=df_market_sales,
            saldos_df=saldos_df
        )

        # 8. Save local CSVs for data extraction pipeline & agents
        os.makedirs("./data", exist_ok=True)
        saldos_df.to_csv("./data/rival_financials.csv", index=False)
        print("💾 Guardado './data/rival_financials.csv' con todas las métricas de rivales.")

        # Cleanup unused default sheet if needed
        self._ensure_default_sheet_cleanup()

        print("\n" + "=" * 65)
        print("✅ TRACKER SYNC COMPLETE!")
        print(f"   • Nuevos movimientos añadidos: {added_count}")
        print(f"   • Managers analizados: {len(saldos_df)}")
        print("=" * 65)

        return {
            "new_movements": added_count,
            "saldos": saldos_df.to_dict(orient="records"),
            "status": "success"
        }

    def _sync_movements(self, ws: gspread.Worksheet, df_transfers: pd.DataFrame, names_map: dict) -> int:
        """Merges transfers into the Movimientos worksheet without duplicates."""
        headers = [
            "id_unico", "fecha", "tipo", "jugador", "player_id",
            "comprador", "buyer_id", "vendedor", "seller_id", "precio", "es_clausula"
        ]

        existing_values = ws.get_all_values()
        existing_ids = set()

        if not existing_values or existing_values[0] != headers:
            ws.clear()
            ws.append_row(headers)
            existing_values = [headers]
        else:
            for row in existing_values[1:]:
                if row:
                    existing_ids.add(row[0])

        if df_transfers.empty:
            print("   ℹ️ No recent transfers found in Biwenger board.")
            return 0

        type_labels = {
            'market_buy': 'Compra Mercado',
            'user_sale': 'Venta Mercado',
            'user_transfer': 'Traspaso entre Managers',
            'clause_steal': 'Clausulazo',
            'clause_increment': 'Blindaje Cláusula'
        }

        new_rows = []
        df_sorted = df_transfers.sort_values('date') if 'date' in df_transfers.columns else df_transfers
        
        for _, row in df_sorted.iterrows():
            p_id = int(row['player_id']) if pd.notnull(row.get('player_id')) else 0
            p_name = names_map.get(p_id, f"Jugador {p_id}")
            t_type = type_labels.get(row.get('type'), str(row.get('type', '')))
            
            date_str = str(row.get('date', ''))
            buyer_id = _clean_id(row.get('buyer_id'))
            buyer_name = str(row.get('buyer_name') or 'Mercado')
            seller_id = _clean_id(row.get('seller_id'))
            seller_default = 'Biwenger' if row.get('type') == 'clause_increment' else 'Mercado'
            seller_name = str(row.get('seller_name') or seller_default)
            amount = int(row.get('amount') or 0)
            is_clause = "SÍ" if row.get('clause') else "NO"

            # Composite Unique ID for deduplication
            uid = f"{date_str}_{p_id}_{buyer_id}_{seller_id}_{amount}_{row.get('type')}"

            if uid not in existing_ids:
                new_rows.append([
                    uid, date_str, t_type, p_name, p_id,
                    buyer_name, buyer_id, seller_name, seller_id, amount, is_clause
                ])
                existing_ids.add(uid)

        if new_rows:
            ws.append_rows(new_rows)
            print(f"   ✨ {len(new_rows)} movimientos añadidos a la hoja '{SHEET_MOVIMIENTOS}'.")
        else:
            print(f"   👌 Todos los movimientos ya estaban registrados en '{SHEET_MOVIMIENTOS}'.")

        return len(new_rows)

    def _sync_round_bonuses(self, ws: gspread.Worksheet, df_bonuses: Optional[pd.DataFrame]) -> int:
        """Syncs round bonuses per manager into Primas_Jornadas worksheet (cumulative and deduplicated)."""
        headers = ["Jornada", "Manager", "User ID", "Puntos", "Prima Recibida", "Fecha"]
        if df_bonuses is None or df_bonuses.empty:
            return 0

        existing_values = ws.get_all_values()
        existing_keys = set()
        if existing_values and existing_values[0] == headers:
            for r in existing_values[1:]:
                if len(r) >= 3:
                    existing_keys.add(f"{r[0]}_{r[2]}")
        else:
            ws.clear()
            ws.append_row(headers)

        df_sorted = df_bonuses.sort_values(by=['round_base', 'bonus'], ascending=[True, False])
        new_rows = []
        for _, r in df_sorted.iterrows():
            j_name = str(r.get('round_name') or r.get('round_base'))
            uid = str(r.get('user_id') or '')
            k = f"{j_name}_{uid}"
            if k not in existing_keys:
                new_rows.append([
                    j_name,
                    str(r.get('user_name') or ''),
                    uid,
                    int(r.get('points') or 0),
                    f"{int(r.get('bonus') or 0):,} €".replace(",", "."),
                    str(r.get('date') or '')
                ])
                existing_keys.add(k)

        if new_rows:
            ws.append_rows(new_rows)
            print(f"   🏆 {len(new_rows)} nuevas primas de jornada añadidas a '{SHEET_PRIMAS}'.")
        else:
            print(f"   👌 Todas las primas de jornada ya estaban registradas en '{SHEET_PRIMAS}'.")
        return len(new_rows)

    def _sync_initial_config(self, ws: gspread.Worksheet, df_standings: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
        """
        Ensures all managers exist in Config_Inicial.
        Populates default baseline budgets from history_exact_balances.csv if available.
        """
        headers = [
            "user_id", "manager", "presupuesto_inicial", "valor_equipo_inicial", "primas_manuales", "notas"
        ]

        default_baseline = {}
        exact_csv = "data/history_exact_balances.csv"
        if os.path.exists(exact_csv):
            try:
                df_exact = pd.read_csv(exact_csv)
                for _, r in df_exact.iterrows():
                    m_name = str(r.get('Manager')).strip().lower()
                    default_baseline[m_name] = {
                        "saldo_inicial": float(r.get('Saldo Inicial', 23900000)),
                        "plantilla_inicial": float(r.get('Valor Plantilla Inicial (01/08)', 16100000))
                    }
            except Exception as e:
                print(f"   ⚠️ Could not load {exact_csv}: {e}")

        existing_values = ws.get_all_values()
        existing_managers = {}

        if not existing_values or existing_values[0] != headers:
            ws.clear()
            ws.append_row(headers)
            existing_values = [headers]
        else:
            for row in existing_values[1:]:
                if len(row) >= 2 and row[0] != "user_id":
                    uid = _clean_id(row[0])
                    name = str(row[1]).strip()
                    try:
                        pres_ini = float(row[2].replace("€", "").replace(".", "").replace(",", ".").strip()) if len(row) > 2 and row[2] else 0.0
                    except Exception:
                        pres_ini = 0.0
                    try:
                        team_ini = float(row[3].replace("€", "").replace(".", "").replace(",", ".").strip()) if len(row) > 3 and row[3] else 0.0
                    except Exception:
                        team_ini = 0.0
                    try:
                        primas = float(row[4].replace("€", "").replace(".", "").replace(",", ".").strip()) if len(row) > 4 and row[4] else 0.0
                    except Exception:
                        primas = 0.0

                    if uid:
                        existing_managers[uid] = {
                            "user_id": uid,
                            "manager": name,
                            "presupuesto_inicial": pres_ini,
                            "valor_equipo_inicial": team_ini,
                            "primas_manuales": primas,
                            "notas": row[5] if len(row) > 5 else ""
                        }

        new_manager_rows = []
        for _, row in df_standings.iterrows():
            uid = _clean_id(row['id'])
            name = str(row['name']).strip()
            if uid and uid not in existing_managers:
                b_info = default_baseline.get(name.lower(), {"saldo_inicial": 23900000.0, "plantilla_inicial": 16100000.0})
                pres_ini = b_info["saldo_inicial"]
                plant_ini = b_info["plantilla_inicial"]

                new_manager_rows.append([uid, name, int(pres_ini), int(plant_ini), 0, "Cargado de balance inicial (01/08)"])
                existing_managers[uid] = {
                    "user_id": uid,
                    "manager": name,
                    "presupuesto_inicial": pres_ini,
                    "valor_equipo_inicial": plant_ini,
                    "primas_manuales": 0.0,
                    "notas": "Cargado de balance inicial (01/08)"
                }

        if new_manager_rows:
            ws.append_rows(new_manager_rows)
            print(f"   👥 {len(new_manager_rows)} rivales añadidos a '{SHEET_CONFIG}' con sus valores iniciales.")

        return existing_managers

    def _update_saldos(
        self,
        ws_mov: gspread.Worksheet,
        ws_saldos: gspread.Worksheet,
        ws_primas: Optional[gspread.Worksheet],
        config_data: Dict[str, Dict[str, Any]],
        df_standings: pd.DataFrame,
        df_players: Optional[pd.DataFrame] = None,
        round_standings: Optional[List[Dict[str, Any]]] = None,
        df_bonuses: Optional[pd.DataFrame] = None,
        df_clause_inc: Optional[pd.DataFrame] = None,
        user_real_balance: Optional[float] = None,
        current_user_id: Optional[str] = None
    ) -> pd.DataFrame:
        """
        Reads movements from Movimientos and bonuses from Primas_Jornadas / board, calculates exact aggregate
        financials and squad metrics, rewrites the Saldos_Estimados worksheet in Google Sheets,
        calibrates against current user's real balance, and returns a DataFrame.
        """
        mov_values = ws_mov.get_all_values()
        
        name_to_uid = {cfg["manager"].lower().strip(): uid for uid, cfg in config_data.items()}

        # Compute exact lineup points per manager if needed
        live_points_map = {}
        if round_standings and df_players is not None and not df_players.empty:
            player_pts_dict = dict(zip(df_players['id'], df_players['points']))
            for u in round_standings:
                m_uid = _clean_id(u.get('id'))
                lineup = u.get('lineup', {}) or {}
                aligned_ids = lineup.get('players', []) if isinstance(lineup, dict) else []
                aligned_valid = [pid for pid in aligned_ids if pid is not None]
                
                base_pts = sum(int(player_pts_dict.get(pid, 0) or 0) for pid in aligned_valid)
                empty_slots = max(0, 11 - len(aligned_valid))
                penalty = empty_slots * 4
                lineup_pts = base_pts - penalty
                if m_uid:
                    live_points_map[m_uid] = max(0, lineup_pts)

        spent_map = {}
        income_map = {}
        purchases_count = {}
        sales_count = {}
        clauses_paid = {}
        clauses_received = {}
        clause_increments_spent = {}

        if len(mov_values) > 1:
            for row in mov_values[1:]:
                if len(row) < 10:
                    continue
                tipo_mov = str(row[2]).strip().lower()
                buyer_name = str(row[5]).strip()
                buyer_id = _clean_id(row[6]) or name_to_uid.get(buyer_name.lower())
                
                seller_name = str(row[7]).strip()
                seller_id = _clean_id(row[8]) or name_to_uid.get(seller_name.lower())
                
                try:
                    amount = float(str(row[9]).replace("€", "").replace(".", "").replace(",", ".").strip())
                except Exception:
                    amount = 0.0
                is_clause = (str(row[10]).strip().upper() == "SÍ")

                if 'blindaje' in tipo_mov or tipo_mov == 'clause_increment':
                    if buyer_id:
                        clause_increments_spent[buyer_id] = clause_increments_spent.get(buyer_id, 0.0) + amount
                else:
                    if buyer_id:
                        spent_map[buyer_id] = spent_map.get(buyer_id, 0.0) + amount
                        purchases_count[buyer_id] = purchases_count.get(buyer_id, 0) + 1
                        if is_clause:
                            clauses_paid[buyer_id] = clauses_paid.get(buyer_id, 0.0) + amount

                    if seller_id:
                        income_map[seller_id] = income_map.get(seller_id, 0.0) + amount
                        sales_count[seller_id] = sales_count.get(seller_id, 0) + 1
                        if is_clause:
                            clauses_received[seller_id] = clauses_received.get(seller_id, 0.0) + amount

        # Round bonuses mapping: accumulate from ws_primas (all recorded rounds)
        round_bonuses_map = {}
        if ws_primas is not None:
            primas_values = ws_primas.get_all_values()
            if len(primas_values) > 1:
                for r in primas_values[1:]:
                    if len(r) >= 5:
                        uid = _clean_id(r[2])
                        try:
                            amt = float(str(r[4]).replace("€", "").replace(".", "").replace(",", ".").strip())
                        except Exception:
                            amt = 0.0
                        if uid:
                            round_bonuses_map[uid] = round_bonuses_map.get(uid, 0.0) + amt

        if not round_bonuses_map and df_bonuses is not None and not df_bonuses.empty:
            for _, r in df_bonuses.iterrows():
                uid = _clean_id(r.get('user_id'))
                if uid:
                    round_bonuses_map[uid] = round_bonuses_map.get(uid, 0.0) + float(r.get('bonus', 0) or 0)

        # Clause increments mapping
        if df_clause_inc is not None and not df_clause_inc.empty:
            for _, r in df_clause_inc.iterrows():
                uid = _clean_id(r.get('user_id'))
                if uid:
                    inc_sum = float(df_clause_inc[df_clause_inc['user_id'].astype(str) == str(uid)]['amount'].sum())
                    clause_increments_spent[uid] = max(clause_increments_spent.get(uid, 0.0), inc_sum)

        # Map current standings metrics
        standings_map = {}
        for _, row in df_standings.iterrows():
            uid = _clean_id(row['id'])
            if uid:
                standings_map[uid] = {
                    "position": row.get('position', 0),
                    "points": int(row.get('points', 0) or 0),
                    "teamSize": int(row.get('teamSize', 0) or 0),
                    "teamValue": float(row.get('teamValue', 0) or 0),
                    "teamValueInc": float(row.get('teamValueInc', 0) or 0)
                }

        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        summary_dicts = []

        for uid, cfg in config_data.items():
            manager_name = cfg["manager"]
            pres_ini = cfg["presupuesto_inicial"]
            plant_ini = cfg.get("valor_equipo_inicial", 0.0)
            primas_manuales = cfg["primas_manuales"]
            total_spent = spent_map.get(uid, 0.0)
            total_income = income_map.get(uid, 0.0)
            total_bonuses = round_bonuses_map.get(uid, 0.0)
            total_clause_inc = clause_increments_spent.get(uid, 0.0)
            
            st_info = standings_map.get(uid, {"position": 0, "points": 0, "teamSize": 0, "teamValue": 0.0, "teamValueInc": 0.0})
            valor_plantilla = st_info["teamValue"]
            team_size = st_info["teamSize"]
            official_pts = st_info["points"]
            live_pts = live_points_map.get(uid, 0)
            puntos = official_pts if official_pts > 0 else live_pts
            posicion_liga = st_info["position"]

            # Exact Formula:
            # Saldo = Presupuesto Inicial + Ventas - Fichajes + Primas Jornada - Blindaje Cláusulas + Primas Manuales
            saldo_est = pres_ini + total_income - total_spent + total_bonuses - total_clause_inc + primas_manuales
            patrimonio_total = saldo_est + valor_plantilla
            base_total = (pres_ini + plant_ini) if (pres_ini + plant_ini) > 0 else 40000000.0
            beneficio_neto = patrimonio_total - base_total
            
            media_jugador = (valor_plantilla / team_size) if team_size > 0 else 0.0
            max_puja_biwenger = saldo_est + (0.25 * valor_plantilla)
            
            if saldo_est >= 15000000:
                amenaza_clausula = "MUY ALTA"
            elif saldo_est >= 8000000:
                amenaza_clausula = "ALTA"
            elif saldo_est >= 3000000:
                amenaza_clausula = "MEDIA"
            elif saldo_est >= 0:
                amenaza_clausula = "BAJA"
            else:
                amenaza_clausula = "EN NEGATIVO"

            is_me = (current_user_id and uid == _clean_id(current_user_id))
            drift = (user_real_balance - saldo_est) if (is_me and user_real_balance is not None) else None

            if is_me and user_real_balance is not None:
                print("\n" + "=" * 65)
                print(f"🎯 CONCILIACIÓN CON TU SALDO REAL ({manager_name}):")
                print(f"   • Saldo real en Biwenger (API): {int(user_real_balance):,} €".replace(",", "."))
                print(f"   • Saldo calculado con el muro:  {int(saldo_est):,} €".replace(",", "."))
                print(f"   • Diferencia / Desviación:     {int(drift):,} €".replace(",", "."))
                if abs(drift) < 1:
                    print("   ✅ ¡CUADRE EXACTO (0 € de desviación)! Tus cuentas cuadran al 100%.")
                else:
                    print(f"   ⚠️ Desviación detectada: {int(drift):+,} €".replace(",", "."))
                print("=" * 65 + "\n")

            summary_dicts.append({
                "user_id": uid,
                "manager": manager_name,
                "posicion_liga": posicion_liga,
                "puntos": puntos,
                "num_jugadores": team_size,
                "valor_plantilla": valor_plantilla,
                "saldo_disponible": saldo_est,
                "patrimonio_total": patrimonio_total,
                "beneficio_neto": beneficio_neto,
                "media_valor_jugador": media_jugador,
                "max_puja_posible": max_puja_biwenger,
                "amenaza_clausulazo": amenaza_clausula,
                "presupuesto_inicial": pres_ini,
                "total_gastado": total_spent,
                "total_ingresado": total_income,
                "primas_jornadas": total_bonuses,
                "blindaje_clausulas": total_clause_inc,
                "clausulas_pagadas": clauses_paid.get(uid, 0.0),
                "clausulas_recibidas": clauses_received.get(uid, 0.0),
                "primas_manuales": primas_manuales,
                "fichajes": purchases_count.get(uid, 0),
                "ventas": sales_count.get(uid, 0),
                "saldo_real": user_real_balance if is_me else None,
                "desviacion_conciliacion": drift if is_me else None,
                "ultima_actualizacion": now_str
            })

        df_summary = pd.DataFrame(summary_dicts)
        df_summary = df_summary.sort_values(by="patrimonio_total", ascending=False).reset_index(drop=True)

        # Build Google Sheet table headers & rows
        gs_headers = [
            "Ranking Pat.", "Manager", "Pos. Liga", "Puntos", "Jugadores",
            "Saldo Disponible Est.", "Valor Plantilla", "Patrimonio Total Est.", "Beneficio Neto (€)",
            "Media / Jugador", "Límite Puja (Biwenger)", "Amenaza Clausulazo",
            "Presupuesto Inicial", "Total Gastado", "Total Ingresado",
            "Primas Jornada", "Blindaje Cláusulas", "Primas Manuales",
            "Fichajes", "Ventas", "Última Actualización"
        ]

        gs_rows = []
        for rank, row in df_summary.iterrows():
            sign_beneficio = "+" if row["beneficio_neto"] >= 0 else ""
            gs_rows.append([
                rank + 1,
                row["manager"],
                row["posicion_liga"],
                row["puntos"],
                row["num_jugadores"],
                f"{int(row['saldo_disponible']):,} €".replace(",", "."),
                f"{int(row['valor_plantilla']):,} €".replace(",", "."),
                f"{int(row['patrimonio_total']):,} €".replace(",", "."),
                f"{sign_beneficio}{int(row['beneficio_neto']):,} €".replace(",", "."),
                f"{int(row['media_valor_jugador']):,} €".replace(",", "."),
                f"{int(row['max_puja_posible']):,} €".replace(",", "."),
                row["amenaza_clausulazo"],
                f"{int(row['presupuesto_inicial']):,} €".replace(",", "."),
                f"{int(row['total_gastado']):,} €".replace(",", "."),
                f"{int(row['total_ingresado']):,} €".replace(",", "."),
                f"{int(row['primas_jornadas']):,} €".replace(",", "."),
                f"{int(row['blindaje_clausulas']):,} €".replace(",", "."),
                f"{int(row['primas_manuales']):,} €".replace(",", "."),
                row["fichajes"],
                row["ventas"],
                now_str
            ])

        ws_saldos.clear()
        ws_saldos.append_row(gs_headers)
        if gs_rows:
            ws_saldos.append_rows(gs_rows)

        return df_summary

    def _sync_dashboard_sheets(
        self,
        df_players: pd.DataFrame,
        auth_player_info: Any,
        df_market_sales: Optional[pd.DataFrame] = None,
        saldos_df: Optional[pd.DataFrame] = None
    ):
        """Syncs Mi_Plantilla, Estado_Usuario, and Mercado_Fichajes into fantasy_tracker."""
        print("📊 Sincronizando hojas operativas para Dashboard y Agente...")
        
        my_team_name = getattr(auth_player_info, 'team_name', 'Dani SR') if auth_player_info else 'Dani SR'
        my_balance = float(getattr(auth_player_info, 'balance', 0.0) or 0.0) if auth_player_info else 0.0

        # 1. Mi_Plantilla
        try:
            ws_squad = self._get_or_create_worksheet(SHEET_SQUAD, rows=50, cols=12)
            headers_squad = [
                "Jugador", "Posicion", "Equipo", "Precio", "Subida_24h",
                "Estado_Fisico", "Prevision_Titular", "Puntos", "Oferta_Mercado", "Clausula"
            ]
            
            squad_rows = []
            if df_players is not None and not df_players.empty:
                team_col = None
                for c in ['BIWPLAYER_TEAM_NAME', 'owner_name', 'team_name']:
                    if c in df_players.columns:
                        team_col = c
                        break
                
                my_squad = df_players[df_players[team_col] == my_team_name] if team_col else pd.DataFrame()

                pos_map = {1: "GK", 2: "DF", 3: "MF", 4: "FW"}
                for _, p in my_squad.iterrows():
                    pos_raw = p.get('PLAYER_POSITION') or p.get('position') or ''
                    pos_name = pos_map.get(pos_raw, str(pos_raw))
                    status_raw = str(p.get('PLAYER_STATUS') or p.get('fitness') or 'ok')
                    if status_raw.lower() in ('ok', 'fit'):
                        status_str = "🟢 Apto"
                    elif any(w in status_raw.lower() for w in ('injur', 'lesion', 'baja')):
                        status_str = "🔴 Lesionado"
                    else:
                        status_str = "🟡 Duda"

                    titular_raw = str(p.get('COMUNIATE_STATUS') or p.get('comuniate_status') or '—')
                    
                    price = int(p.get('PLAYER_PRICE') or p.get('price') or 0)
                    inc = int(p.get('PLAYER_PRICE_INCREMENT') or p.get('price_increment') or 0)
                    pts = int(p.get('PLAYER_POINTS') or p.get('points') or 0)
                    offer = p.get('MARKET_OFFER_AMOUNT')
                    offer_str = f"{int(offer):,} €".replace(",", ".") if pd.notnull(offer) and float(offer) > 0 else "—"
                    clause = p.get('PLAYER_CLAUSE') or p.get('clause')
                    clause_str = f"{int(clause):,} €".replace(",", ".") if pd.notnull(clause) and float(clause) > 0 else "—"

                    squad_rows.append([
                        str(p.get('PLAYER_NAME') or p.get('name') or ''),
                        pos_name,
                        str(p.get('TEAM_NAME') or p.get('team_name') or ''),
                        f"{price:,} €".replace(",", "."),
                        f"{inc:+,} €".replace(",", "."),
                        status_str,
                        titular_raw,
                        pts,
                        offer_str,
                        clause_str
                    ])
            
            ws_squad.clear()
            ws_squad.append_row(headers_squad)
            if squad_rows:
                ws_squad.append_rows(squad_rows)
            ws_squad.freeze(rows=1)
            print(f"   👔 Sincronizados {len(squad_rows)} jugadores en '{SHEET_SQUAD}'.")
        except Exception as e:
            print(f"   ⚠️ Error sincronizando '{SHEET_SQUAD}': {e}")

        # 2. Estado_Usuario
        try:
            ws_status = self._get_or_create_worksheet(SHEET_USER_STATUS, rows=20, cols=10)
            headers_status = [
                "Manager", "Posicion_Liga", "Puntos_Totales", "Saldo_Real_Disponible",
                "Valor_Plantilla", "Patrimonio_Total", "Max_Puja_Permitida", "Num_Jugadores", "Ultima_Actualizacion"
            ]
            
            pos_liga = 6
            puntos_tot = 0
            val_plantilla = 0.0
            if saldos_df is not None and not saldos_df.empty:
                user_row = saldos_df[saldos_df['manager'] == my_team_name]
                if not user_row.empty:
                    pos_liga = int(user_row['posicion_liga'].iloc[0])
                    puntos_tot = int(user_row['puntos'].iloc[0])
                    val_plantilla = float(user_row['valor_plantilla'].iloc[0])

            patrimonio = my_balance + val_plantilla
            max_bid = my_balance + (val_plantilla * 0.25)
            num_jug = len(squad_rows) if 'squad_rows' in locals() and squad_rows else 0

            status_row = [
                my_team_name,
                pos_liga,
                puntos_tot,
                f"{int(my_balance):,} €".replace(",", "."),
                f"{int(val_plantilla):,} €".replace(",", "."),
                f"{int(patrimonio):,} €".replace(",", "."),
                f"{int(max_bid):,} €".replace(",", "."),
                num_jug,
                pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')
            ]

            ws_status.clear()
            ws_status.append_row(headers_status)
            ws_status.append_row(status_row)
            ws_status.freeze(rows=1)
            print(f"   👤 Actualizado '{SHEET_USER_STATUS}' con saldo exacto {my_balance:,.0f} €.")
        except Exception as e:
            print(f"   ⚠️ Error sincronizando '{SHEET_USER_STATUS}': {e}")

        # 3. Mercado_Fichajes
        try:
            ws_market = self._get_or_create_worksheet(SHEET_MARKET, rows=100, cols=8)
            headers_market = ["Jugador", "Posicion", "Equipo", "Precio_Venta", "Subida_24h", "Vendedor", "Fin_Puja"]
            market_rows = []
            
            if df_market_sales is not None and not df_market_sales.empty and df_players is not None and not df_players.empty:
                p_id_col = 'PLAYER_ID' if 'PLAYER_ID' in df_players.columns else 'id'
                p_name_col = 'PLAYER_NAME' if 'PLAYER_NAME' in df_players.columns else 'name'
                p_pos_col = 'PLAYER_POSITION' if 'PLAYER_POSITION' in df_players.columns else 'position'
                p_team_col = 'TEAM_NAME' if 'TEAM_NAME' in df_players.columns else 'team_name'
                p_price_col = 'PLAYER_PRICE' if 'PLAYER_PRICE' in df_players.columns else 'price'
                p_inc_col = 'PLAYER_PRICE_INCREMENT' if 'PLAYER_PRICE_INCREMENT' in df_players.columns else 'price_increment'

                p_lookup = df_players.set_index(p_id_col).to_dict('index')
                
                pos_map = {1: "GK", 2: "DF", 3: "MF", 4: "FW"}
                for _, s in df_market_sales.iterrows():
                    pid = s.get('player_id')
                    info = p_lookup.get(pid, {})
                    p_name = info.get(p_name_col, f"Jugador {pid}")
                    pos_raw = info.get(p_pos_col, '')
                    pos_str = pos_map.get(pos_raw, str(pos_raw))
                    team_str = str(info.get(p_team_col, ''))
                    price_val = int(s.get('price') or info.get(p_price_col, 0) or 0)
                    inc_val = int(info.get(p_inc_col, 0) or 0)
                    seller = str(s.get('seller') or 'Mercado')
                    until_val = str(s.get('until') or '')

                    market_rows.append([
                        p_name,
                        pos_str,
                        team_str,
                        f"{price_val:,} €".replace(",", "."),
                        f"{inc_val:+,} €".replace(",", "."),
                        seller,
                        until_val
                    ])

            ws_market.clear()
            ws_market.append_row(headers_market)
            if market_rows:
                ws_market.append_rows(market_rows)
            ws_market.freeze(rows=1)
            print(f"   🛒 Sincronizados {len(market_rows)} jugadores transferibles en '{SHEET_MARKET}'.")
        except Exception as e:
            print(f"   ⚠️ Error sincronizando '{SHEET_MARKET}': {e}")


def run_tracker(days_back: Optional[int] = None, full: bool = True, reset_sheets: bool = False) -> pd.DataFrame:
    """CLI / Execution entrypoint for the Biwenger Google Sheets Tracker."""
    tracker = BiwengerSheetsTracker()
    res = tracker.sync(days_back=days_back, full=full, reset_sheets=reset_sheets)
    return pd.DataFrame(res.get("saldos", []))


if __name__ == "__main__":
    full_sync = "--full" in sys.argv or "--days" not in sys.argv
    days = None
    if "--days" in sys.argv:
        idx = sys.argv.index("--days")
        if idx + 1 < len(sys.argv):
            try:
                days = int(sys.argv[idx + 1])
                full_sync = False
            except ValueError:
                days = None
                full_sync = True
    run_tracker(days_back=days, full=full_sync)
