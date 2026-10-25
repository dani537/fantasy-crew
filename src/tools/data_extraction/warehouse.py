"""
Biwenger Dual-Sink Data Warehouse (SQLite + CSV + Google Sheets)
================================================================
Provides high-performance local analytical storage (SQLite + CSV) for sub-second
backtesting and strategy simulation, coupled with automated batch synchronization
to Google Sheets (`fantasy_tracker`) for cloud visibility and GitHub Actions persistence.
"""

import os
import json
import time
import sqlite3
from typing import List, Optional, Dict, Any
import numpy as np
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials as GoogleCredentials

from src.config import GoogleSheetsConfig

DEFAULT_SPREADSHEET_ID = "1V3lDapPrpGgLGVl-rvNi3Ishy70dAo22UEn24toa4kk"
WAREHOUSE_DIR = "./data/warehouse"
WAREHOUSE_DB_PATH = os.path.join(WAREHOUSE_DIR, "biwenger_history.db")

# Canonical Day-1 (2026-08-01) exact baseline balances & squad values (Total = 40.00M € each)
EXACT_INITIAL_BALANCES: List[Dict[str, Any]] = [
    {"user_id": 9879571, "Manager": "Joan GM", "Saldo Inicial": 23890000, "Valor Plantilla Inicial (01/08)": 16110000},
    {"user_id": 14002970, "Manager": "Ponceneta FC", "Saldo Inicial": 23900000, "Valor Plantilla Inicial (01/08)": 16100000},
    {"user_id": 12630338, "Manager": "Birra München", "Saldo Inicial": 23900000, "Valor Plantilla Inicial (01/08)": 16100000},
    {"user_id": 11457293, "Manager": "La Vinotinto", "Saldo Inicial": 23860000, "Valor Plantilla Inicial (01/08)": 16140000},
    {"user_id": 9879359, "Manager": "RusoPoderoso", "Saldo Inicial": 24050000, "Valor Plantilla Inicial (01/08)": 15950000},
    {"user_id": 14053609, "Manager": "SCR United", "Saldo Inicial": 23840000, "Valor Plantilla Inicial (01/08)": 16160000},
    {"user_id": 12629981, "Manager": "JubiladosFC", "Saldo Inicial": 23880000, "Valor Plantilla Inicial (01/08)": 16120000},
    {"user_id": 12638598, "Manager": "Solvency FCST_v80", "Saldo Inicial": 23890000, "Valor Plantilla Inicial (01/08)": 16110000},
    {"user_id": 12630135, "Manager": "C.F. Montcada", "Saldo Inicial": 23890000, "Valor Plantilla Inicial (01/08)": 16110000},
    {"user_id": 9879871, "Manager": "Tesoreros del gol", "Saldo Inicial": 24060000, "Valor Plantilla Inicial (01/08)": 15940000},
    {"user_id": 12637492, "Manager": "Palancas FC", "Saldo Inicial": 23920000, "Valor Plantilla Inicial (01/08)": 16080000},
    {"user_id": 11476314, "Manager": "Piedras FC", "Saldo Inicial": 23900000, "Valor Plantilla Inicial (01/08)": 16100000},
    {"user_id": 9881550, "Manager": "Palmeroks", "Saldo Inicial": 23940000, "Valor Plantilla Inicial (01/08)": 16060000},
    {"user_id": 9878097, "Manager": "Dani SR", "Saldo Inicial": 23900000, "Valor Plantilla Inicial (01/08)": 16100000},
    {"user_id": 9960368, "Manager": "Tri Tranquils", "Saldo Inicial": 23870000, "Valor Plantilla Inicial (01/08)": 16130000},
]

# Official AZ Finance Clause Ranges (from /api/v2/league/1643248?fields=*,settings)
LEAGUE_CLAUSE_RANGES = [
    (2_000_000, 225, "%"),
    (3_000_000, 5_000_000, "abs"),
    (5_000_000, 200, "%"),
    (6_500_000, 10_000_000, "abs"),
    (9_000_000, 175, "%"),
    (12_000_000, 16_000_000, "abs"),
    (15_000_000, 150, "%"),
    (20_000_000, 24_000_000, "abs"),
    (25_000_000, 135, "%"),
    (30_000_000, 35_000_000, "abs"),
    (float("inf"), 125, "%"),
]


def compute_official_clause(base_value: float, extra_increment: float = 0.0) -> int:
    """
    Computes the exact release clause according to AZ Finance's official `clauseRanges` rules:
    base_value = max(purchase_price or 0, current_market_price).
    """
    base = float(base_value or 0.0)
    if base <= 0:
        return 0
    clause_val = base * 2.0
    for threshold, rule_val, mode in LEAGUE_CLAUSE_RANGES:
        if base <= float(threshold):
            if mode == "%":
                clause_val = base * (float(rule_val) / 100.0)
            else:
                clause_val = float(rule_val)
            break
    return int(round(clause_val + float(extra_increment or 0.0)))


def sanitize_sheet_cell(val: Any) -> Any:
    """Guarantees 100% JSON & gspread compliance: eliminates NaNs, Infs, None, and complex objects."""
    if val is None:
        return ""
    if isinstance(val, (list, dict, tuple, set)):
        return json.dumps(list(val) if isinstance(val, set) else val, ensure_ascii=False)
    try:
        if pd.isna(val):
            return ""
    except Exception:
        pass
    if isinstance(val, (float, np.floating)):
        if np.isnan(val) or np.isinf(val):
            return ""
        # Keep integers clean if float has .0
        if float(val).is_integer():
            return int(val)
        return round(float(val), 2)
    if isinstance(val, (int, np.integer)):
        return int(val)
    if isinstance(val, (pd.Timestamp,)):
        return val.strftime("%Y-%m-%d %H:%M:%S")
    return str(val)


def get_authorized_gspread_client(creds_path: Optional[str] = None) -> Optional[gspread.Client]:
    """
    Authenticates with Google Sheets API supporting both:
      1. GOOGLE_SERVICE_ACCOUNT_JSON environment variable (GitHub Actions / Cloud)
      2. Local service account JSON file (credentials_google.json)
    """
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_json_str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if creds_json_str and creds_json_str.strip():
        try:
            creds_dict = json.loads(creds_json_str.strip())
            creds = GoogleCredentials.from_service_account_info(creds_dict, scopes=scopes)
            return gspread.authorize(creds)
        except Exception as e:
            print(f"⚠️ Error autenticando con GOOGLE_SERVICE_ACCOUNT_JSON: {e}")

    resolved_path = creds_path or os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or GoogleSheetsConfig.SERVICE_ACCOUNT_FILE or "credentials_google.json"
    if resolved_path and os.path.exists(resolved_path):
        try:
            creds = GoogleCredentials.from_service_account_file(resolved_path, scopes=scopes)
            return gspread.authorize(creds)
        except Exception as e:
            print(f"⚠️ Error autenticando con archivo {resolved_path}: {e}")

    return None


def ensure_exact_balances_csv(path: str = "data/history_exact_balances.csv") -> pd.DataFrame:
    """Ensures data/history_exact_balances.csv exists on disk (even in fresh CI runners)."""
    if os.path.exists(path):
        try:
            df = pd.read_csv(path)
            if not df.empty:
                return df
        except Exception:
            pass
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    df = pd.DataFrame(EXACT_INITIAL_BALANCES)
    df.to_csv(path, index=False)
    return df


class BiwengerWarehouse:
    """
    Manages the Dual-Sink analytical database (`data/warehouse/biwenger_history.db` + CSVs)
    and synchronizes worksheets with Google Sheets.
    """

    def __init__(
        self,
        db_path: str = WAREHOUSE_DB_PATH,
        sheet_id: Optional[str] = None,
        creds_path: Optional[str] = None
    ):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.sheet_id = (
            sheet_id
            or os.getenv("GOOGLE_SHEET_ID")
            or os.getenv("GOOGLE_SHEET_ID_MARKET")
            or GoogleSheetsConfig.SHEET_ID
            or DEFAULT_SPREADSHEET_ID
        )
        self.creds_path = creds_path
        self._gc = None
        self._spreadsheet = None

    @property
    def spreadsheet(self) -> Optional[gspread.Spreadsheet]:
        if self._spreadsheet is None:
            self._gc = get_authorized_gspread_client(self.creds_path)
            if self._gc and self.sheet_id:
                self._spreadsheet = self._gc.open_by_key(self.sheet_id)
        return self._spreadsheet

    def get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def read_table(self, table_name: str, fallback_sheets: bool = True) -> pd.DataFrame:
        """Reads a table from SQLite warehouse (or falls back to CSV mirror / Google Sheets if needed)."""
        sql_table = self.TABLE_MAP.get(table_name, table_name)
        try:
            with self.get_connection() as conn:
                for cand in (sql_table, table_name):
                    cursor = conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                        (cand,)
                    )
                    if cursor.fetchone():
                        df_res = pd.read_sql_query(f'SELECT * FROM "{cand}"', conn)
                        if not df_res.empty:
                            return df_res
        except Exception:
            pass
        csv_path = os.path.join(WAREHOUSE_DIR, f"{table_name}.csv")
        if os.path.exists(csv_path):
            try:
                df_csv = pd.read_csv(csv_path)
                if not df_csv.empty:
                    return df_csv
            except Exception:
                pass
        if fallback_sheets and self.spreadsheet is not None:
            try:
                ws = self.spreadsheet.worksheet(table_name)
                vals = ws.get_all_values()
                if len(vals) > 1:
                    return pd.DataFrame(vals[1:], columns=vals[0])
            except Exception:
                pass
        return pd.DataFrame()

    def upsert_table(
        self,
        table_name: str,
        df_new: pd.DataFrame,
        pk_cols: Optional[List[str]] = None,
        sort_cols: Optional[List[str]] = None,
        replace_all: bool = False
    ) -> pd.DataFrame:
        """
        Idempotently merges `df_new` into the SQLite table and CSV mirror in `data/warehouse/`.
        Deduplicates by `pk_cols` (keeping latest values from `df_new`).
        """
        if df_new is None or df_new.empty:
            return self.read_table(table_name)

        df_clean = df_new.copy()
        for col in df_clean.columns:
            if df_clean[col].dtype == "object":
                df_clean[col] = df_clean[col].apply(
                    lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
                )

        if not replace_all:
            df_existing = self.read_table(table_name)
            if not df_existing.empty:
                df_combined = pd.concat([df_existing, df_clean], ignore_index=True)
            else:
                df_combined = df_clean
        else:
            df_combined = df_clean

        if pk_cols:
            valid_pks = [c for c in pk_cols if c in df_combined.columns]
            if valid_pks:
                # Normalize PK types to string for reliable deduplication
                for pk in valid_pks:
                    df_combined[f"__pk_{pk}"] = df_combined[pk].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
                pk_temp_cols = [f"__pk_{pk}" for pk in valid_pks]
                df_combined = df_combined.drop_duplicates(subset=pk_temp_cols, keep="last").drop(columns=pk_temp_cols)

        effective_sort = sort_cols or pk_cols
        if effective_sort:
            valid_sort = [c for c in effective_sort if c in df_combined.columns]
            if valid_sort:
                df_combined = df_combined.sort_values(by=valid_sort).reset_index(drop=True)

        # Save to SQLite with indexes
        with self.get_connection() as conn:
            df_combined.to_sql(table_name, conn, if_exists="replace", index=False)
            if pk_cols:
                valid_pks = [c for c in pk_cols if c in df_combined.columns]
                if valid_pks:
                    cols_sql = ", ".join(f'"{c}"' for c in valid_pks)
                    idx_name = f"idx_{table_name}_{'_'.join(valid_pks)}"
                    conn.execute(f'CREATE INDEX IF NOT EXISTS "{idx_name}" ON "{table_name}" ({cols_sql})')
            conn.commit()

        # Save CSV mirror in data/warehouse/
        csv_path = os.path.join(WAREHOUSE_DIR, f"{table_name}.csv")
        df_combined.to_csv(csv_path, index=False, encoding="utf-8-sig")
        return df_combined

    def sync_dataframe_to_sheet(
        self,
        worksheet_title: str,
        df: pd.DataFrame,
        freeze_header: bool = True
    ) -> bool:
        """
        Synchronizes a DataFrame to a Google Sheets worksheet using a single atomic batch `update`
        with exponential backoff on rate limits (HTTP 429 / 5xx).
        """
        if df is None or df.empty:
            return False

        sh = self.spreadsheet
        if sh is None:
            print(f"⚠️ Google Sheets no disponible; omitiendo subida de '{worksheet_title}'.")
            return False

        headers = list(df.columns)
        rows = [[sanitize_sheet_cell(v) for v in row] for row in df.to_numpy().tolist()]
        payload = [headers] + rows
        n_rows = len(payload)
        n_cols = len(headers)

        for attempt in range(4):
            try:
                try:
                    ws = sh.worksheet(worksheet_title)
                except gspread.WorksheetNotFound:
                    print(f"📄 Creando nueva pestaña '{worksheet_title}' ({n_rows}x{n_cols}) en Google Sheets...")
                    ws = sh.add_worksheet(title=worksheet_title, rows=max(n_rows + 50, 100), cols=max(n_cols + 2, 10))

                # Resize if current grid is smaller than payload
                if ws.row_count < n_rows or ws.col_count < n_cols:
                    ws.resize(rows=max(n_rows + 100, ws.row_count), cols=max(n_cols + 2, ws.col_count))

                ws.clear()
                # Write in chunks of 15,000 rows if very large (like Historico_Continuo) to avoid HTTP payload limits
                CHUNK_ROWS = 15000
                if n_rows <= CHUNK_ROWS:
                    ws.update(values=payload, range_name="A1", value_input_option="RAW")
                else:
                    for start_idx in range(0, n_rows, CHUNK_ROWS):
                        chunk = payload[start_idx : start_idx + CHUNK_ROWS]
                        start_row_num = start_idx + 1
                        ws.update(values=chunk, range_name=f"A{start_row_num}", value_input_option="RAW")
                        if start_idx + CHUNK_ROWS < n_rows:
                            time.sleep(1.5)

                if freeze_header:
                    try:
                        ws.freeze(rows=1)
                    except Exception:
                        pass

                print(f"☁️ Sincronizada hoja '{worksheet_title}' en Google Sheets ({len(rows)} filas x {n_cols} cols).")
                return True
            except Exception as e:
                wait_s = 12.0 * (attempt + 1)
                print(f"⚠️ Intento {attempt+1}/4 en '{worksheet_title}' falló ({e}). Reintentando en {wait_s:.0f}s...")
                time.sleep(wait_s)

        return False
