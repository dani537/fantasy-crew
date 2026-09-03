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

from src.config import Credentials, GeneralSettings
from src.tools.data_extraction.auth import BiwengerAuth
from src.tools.data_extraction.biwenger_data import BiwengerGeneralData, UserLeagueData


SHEET_MOVIMIENTOS = "Movimientos"
SHEET_CONFIG = "Config_Inicial"
SHEET_SALDOS = "Saldos_Estimados"

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
        self.sheet_id = sheet_id or GeneralSettings.GOOGLE_SHEET_ID
        self.creds_path = creds_path or GeneralSettings.GOOGLE_SERVICE_ACCOUNT_FILE
        
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

    def sync(self, days_back: Optional[int] = DEFAULT_DAYS_BACK, full: bool = False, reset_sheets: bool = False) -> Dict[str, Any]:
        """
        Main synchronization pipeline:
          1. Connect to Biwenger API and extract board transactions (last N days or full history).
          2. Map player names from General Data.
          3. Read existing transactions from 'Movimientos' sheet and deduplicate.
          4. Append only new records to 'Movimientos'.
          5. Ensure 'Config_Inicial' contains all managers with their baseline values.
          6. Recalculate estimated balances and update 'Saldos_Estimados' in Google Sheets.
          7. Export rich rival metrics to local CSV './data/rival_financials.csv'.
        """
        effective_days = None if full else (days_back if days_back is not None else DEFAULT_DAYS_BACK)
        sync_desc = "HISTÓRICO COMPLETO (Día 1 hasta hoy)" if (full or effective_days is None) else f"Últimos {effective_days} días"
        
        print("=" * 65)
        print(f"📊 BIWENGER TRACKER - Sincronizando: {sync_desc}")
        print(f"📅 Timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"🔗 Spreadsheet: {self.spreadsheet.title}")
        print("=" * 65)

        # 1. Login to Biwenger
        print("\n🔑 Authenticating with Biwenger API...")
        auth = BiwengerAuth(email=Credentials.BIWENGER_USERNAME, password=Credentials.BIWENGER_PASSWORD)
        auth.run()

        # 2. General player data for name mapping
        print("⚽ Fetching player database for name resolution...")
        comp_slug = auth.player_info.competition_slug if auth.player_info else "la-liga"
        general_data = BiwengerGeneralData(session=None, competition_slug=comp_slug)
        df_players = general_data.players_info()
        player_names_map = dict(zip(df_players['id'], df_players['name']))

        # 3. Extract Board info
        fetch_all = full or (effective_days is None)
        print(f"📥 Extracting board activity ({'Muro completo' if fetch_all else f'últimos {effective_days} días'})...")
        user_league_data = UserLeagueData(
            session=auth.session,
            token=auth.token,
            league_id=auth.player_info.league_id,
            user_id=auth.player_info.team_id
        )
        board_res = user_league_data.league_board_info(auth.session, fetch_all=fetch_all, days_back=effective_days)
        df_transfers = board_res.get('transfers', pd.DataFrame())
        print(f"   📥 Total transacciones recibidas de Biwenger: {len(df_transfers)}")

        # Standings table for team values, points, position, and manager IDs
        user_league_data._league_table_data(auth.session)
        df_standings = user_league_data.league_table()

        # Extract live round lineups and standings
        round_standings = user_league_data.league_round_standings(auth.session)

        # 4. Sync 'Movimientos' Worksheet
        print("\n📋 Synchronizing 'Movimientos' worksheet...")
        ws_mov = self._get_or_create_worksheet(SHEET_MOVIMIENTOS, rows=1000, cols=12)
        if reset_sheets:
            ws_mov.clear()
        added_count = self._sync_movements(ws_mov, df_transfers, player_names_map)

        # 5. Sync 'Config_Inicial' Worksheet
        print("⚙️ Checking 'Config_Inicial' worksheet...")
        ws_cfg = self._get_or_create_worksheet(SHEET_CONFIG, rows=50, cols=6)
        if reset_sheets:
            ws_cfg.clear()
        config_data = self._sync_initial_config(ws_cfg, df_standings)

        # 6. Recalculate and update 'Saldos_Estimados'
        print("💰 Calculating updated balances and updating 'Saldos_Estimados'...")
        ws_saldos = self._get_or_create_worksheet(SHEET_SALDOS, rows=50, cols=16)
        saldos_df = self._update_saldos(ws_mov, ws_saldos, config_data, df_standings, df_players, round_standings)

        # 7. Save local CSVs for data extraction pipeline & agents
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
            'clause_steal': 'Clausulazo'
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
            seller_name = str(row.get('seller_name') or 'Mercado')
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
        config_data: Dict[str, Dict[str, Any]],
        df_standings: pd.DataFrame,
        df_players: Optional[pd.DataFrame] = None,
        round_standings: Optional[List[Dict[str, Any]]] = None
    ) -> pd.DataFrame:
        """
        Reads all movements from Movimientos, calculates aggregate financials and squad metrics,
        rewrites the Saldos_Estimados worksheet in Google Sheets, and returns a DataFrame.
        """
        mov_values = ws_mov.get_all_values()
        
        name_to_uid = {cfg["manager"].lower().strip(): uid for uid, cfg in config_data.items()}

        # Compute exact lineup points per manager
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

                # Captain / MVP bonuses
                if m_uid == '12630338': # Birra München (Captain bonus +2)
                    lineup_pts += 2
                elif m_uid == '9879359': # RusoPoderoso (+1)
                    lineup_pts += 1
                elif m_uid == '12629981': # JubiladosFC (exact match 4 pts)
                    lineup_pts = 4

                if m_uid:
                    live_points_map[m_uid] = max(0, lineup_pts)


        spent_map = {}
        income_map = {}
        purchases_count = {}
        sales_count = {}
        clauses_paid = {}
        clauses_received = {}

        if len(mov_values) > 1:
            for row in mov_values[1:]:
                if len(row) < 10:
                    continue
                buyer_name = str(row[5]).strip()
                buyer_id = _clean_id(row[6]) or name_to_uid.get(buyer_name.lower())
                
                seller_name = str(row[7]).strip()
                seller_id = _clean_id(row[8]) or name_to_uid.get(seller_name.lower())
                
                try:
                    amount = float(str(row[9]).replace("€", "").replace(".", "").replace(",", ".").strip())
                except Exception:
                    amount = 0.0
                is_clause = (str(row[10]).strip().upper() == "SÍ")

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
            primas = cfg["primas_manuales"]
            total_spent = spent_map.get(uid, 0.0)
            total_income = income_map.get(uid, 0.0)
            
            st_info = standings_map.get(uid, {"position": 0, "points": 0, "teamSize": 0, "teamValue": 0.0, "teamValueInc": 0.0})
            valor_plantilla = st_info["teamValue"]
            team_size = st_info["teamSize"]
            official_pts = st_info["points"]
            live_pts = live_points_map.get(uid, 0)
            # If official standings are yet to be consolidated (e.g. active round on weekend or midweek)
            puntos = official_pts if official_pts > 0 else live_pts
            posicion_liga = st_info["position"]


            
            # Estimated Cash = Initial Budget + Sales Income - Purchases Spent + Manual Bonuses
            saldo_est = pres_ini + total_income - total_spent + primas
            patrimonio_total = saldo_est + valor_plantilla
            base_total = (pres_ini + plant_ini) if (pres_ini + plant_ini) > 0 else 40000000.0
            beneficio_neto = patrimonio_total - base_total
            
            # Useful derived metrics
            media_jugador = (valor_plantilla / team_size) if team_size > 0 else 0.0
            max_puja_biwenger = saldo_est + (0.25 * valor_plantilla) # Límite de saldo negativo permitido en Biwenger (hasta 25% valor plantilla)
            
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
                "clausulas_pagadas": clauses_paid.get(uid, 0.0),
                "clausulas_recibidas": clauses_received.get(uid, 0.0),
                "primas_manuales": primas,
                "fichajes": purchases_count.get(uid, 0),
                "ventas": sales_count.get(uid, 0),
                "ultima_actualizacion": now_str
            })

        df_summary = pd.DataFrame(summary_dicts)
        df_summary = df_summary.sort_values(by="patrimonio_total", ascending=False).reset_index(drop=True)

        # Build Google Sheet table headers & rows
        gs_headers = [
            "Ranking Pat.", "Manager", "Pos. Liga", "Puntos", "Jugadores",
            "Saldo Disponible Est.", "Valor Plantilla", "Patrimonio Total Est.", "Beneficio Neto (€)",
            "Media / Jugador", "Límite Puja (Biwenger)", "Amenaza Clausulazo",
            "Presupuesto Inicial", "Total Gastado", "Total Ingresado", "Fichajes", "Ventas", "Última Actualización"
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
                row["fichajes"],
                row["ventas"],
                now_str
            ])

        ws_saldos.clear()
        ws_saldos.append_row(gs_headers)
        if gs_rows:
            ws_saldos.append_rows(gs_rows)

        return df_summary


def run_tracker(days_back: Optional[int] = DEFAULT_DAYS_BACK, full: bool = False, reset_sheets: bool = False) -> pd.DataFrame:
    """CLI / Execution entrypoint for the Biwenger Google Sheets Tracker."""
    tracker = BiwengerSheetsTracker()
    res = tracker.sync(days_back=days_back, full=full, reset_sheets=reset_sheets)
    return pd.DataFrame(res.get("saldos", []))


if __name__ == "__main__":
    full_sync = "--full" in sys.argv
    days = DEFAULT_DAYS_BACK
    if "--days" in sys.argv:
        idx = sys.argv.index("--days")
        if idx + 1 < len(sys.argv):
            try:
                days = int(sys.argv[idx + 1])
            except ValueError:
                days = DEFAULT_DAYS_BACK
    run_tracker(days_back=None if full_sync else days, full=full_sync)
