"""
Persistent Cross-Session Strategy Memory & Human-Approval Ledger (`strategy_memory.py`)
======================================================================================
Ensures the agent NEVER starts from zero across daily runs, intraday tactical triggers,
or Telegram conversations by persisting four structured ledgers:
1. `Tesis_Activos`: Per-player investment thesis (`rol`, `precio_compra`, `coste_reposicion`,
   `npv_hold`, `npv_sell`, `offer_rolls`, `intocable`, `tesis_resumen`).
2. `Memoria_Estrategica`: Rolling strategic journal (debt/collateral plan, scarcity regime,
   target positions, and conversational notes from user).
3. `Decisiones_Pendientes`: Human-gated action queue in `SEMI_AUTO` mode for ALL release
   clauses/shields (`CLAUSE_SNIPE`, `DEFENSIVE_SHIELD`) and important sales (`IMPORTANT_SALE`),
   approvable via Telegram (`/aprobar <ID>`) or CLI.
4. `Eventos_Jornada`: Idempotency ledger ensuring dynamic matchday triggers fire once per window.

Supports both persistent SQLite + optional Google Sheets mirroring (production) and
isolated `in_memory_only=True` operation (for fast, leak-free historical backtesting).
"""

import os
import re
import json
import sqlite3
import datetime
import unicodedata
from typing import Dict, List, Any, Optional, Set, Tuple
import pandas as pd

from src.config import GoogleSheetsConfig
from src.tools.data_extraction.warehouse import WAREHOUSE_DB_PATH, BiwengerWarehouse
from src.utils.instructions import get_instructions_for_recipient


DIRECTIVES_JSON_PATH = "./data/autopilot/user_directives.json"


def _strip_accents(text: str) -> str:
    s = unicodedata.normalize("NFD", str(text or ""))
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn").lower().strip()


class StrategyMemoryLedger:
    """Stateful memory manager for Fantasy Champion v3."""

    def __init__(
        self,
        db_path: str = WAREHOUSE_DB_PATH,
        directives_path: str = DIRECTIVES_JSON_PATH,
        in_memory_only: bool = False,
        sync_sheets: bool = False,
    ):
        self.in_memory_only = bool(in_memory_only)
        self.db_path = ":memory:" if self.in_memory_only else db_path
        self.directives_path = directives_path
        self.sync_sheets = bool(sync_sheets) and not self.in_memory_only

        self._mem_conn: Optional[sqlite3.Connection] = None
        if not self.in_memory_only:
            os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
            os.makedirs(os.path.dirname(self.directives_path) or ".", exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        if self.in_memory_only:
            if self._mem_conn is None:
                self._mem_conn = sqlite3.connect(":memory:")
            return self._mem_conn
        return sqlite3.connect(self.db_path)

    def _close_if_disk(self, conn: sqlite3.Connection) -> None:
        if not self.in_memory_only:
            conn.close()

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS Tesis_Activos (
                    player_id INTEGER PRIMARY KEY,
                    nombre TEXT,
                    rol TEXT,
                    fecha_compra TEXT,
                    precio_compra INTEGER,
                    precio_actual INTEGER,
                    clausula_actual INTEGER,
                    coste_reposicion INTEGER,
                    escasez_tier TEXT,
                    npv_hold INTEGER,
                    npv_sell INTEGER,
                    recomendacion_npv TEXT,
                    offer_rolls INTEGER DEFAULT 0,
                    ultima_oferta INTEGER DEFAULT 0,
                    intocable INTEGER DEFAULT 0,
                    forzar_venta INTEGER DEFAULT 0,
                    tesis_resumen TEXT,
                    updated_at TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS Memoria_Estrategica (
                    entry_id TEXT PRIMARY KEY,
                    ts TEXT,
                    jornada_objetivo INTEGER,
                    fase TEXT,
                    regimen_mercado TEXT,
                    plan_deuda_colateral TEXT,
                    objetivos_prioritarios TEXT,
                    notas_estrategicas TEXT,
                    origen TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS Decisiones_Pendientes (
                    decision_id TEXT PRIMARY KEY,
                    created_at TEXT,
                    expires_at TEXT,
                    action_type TEXT,
                    player_id INTEGER,
                    nombre TEXT,
                    amount_eur INTEGER,
                    market_price_eur INTEGER,
                    counterparty TEXT,
                    owner_user_id INTEGER,
                    offer_id INTEGER,
                    status TEXT,
                    rationale TEXT,
                    payload_json TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS Eventos_Jornada (
                    event_key TEXT PRIMARY KEY,
                    jornada_num INTEGER,
                    trigger_type TEXT,
                    executed_at TEXT,
                    status TEXT,
                    summary TEXT
                )
            """)
            conn.commit()
        finally:
            self._close_if_disk(conn)

    # ------------------------------------------------------------------ #
    # 1. Directives & User Instructions (Sheets + Telegram JSON)
    # ------------------------------------------------------------------ #
    def _load_local_directives_dict(self) -> Dict[str, Any]:
        default_d = {
            "untouchables": [],       # names or IDs
            "transferables": [],      # explicit removal from untouchables / allow sale
            "force_sell": [],
            "blacklist_buy": [],
            "priority_targets": [],
            "notes": [],
        }
        if self.in_memory_only or not os.path.exists(self.directives_path):
            return default_d
        try:
            with open(self.directives_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                default_d.update(loaded)
        except Exception:
            pass
        return default_d

    def _save_local_directives_dict(self, data: Dict[str, Any]) -> None:
        if self.in_memory_only:
            return
        os.makedirs(os.path.dirname(self.directives_path) or ".", exist_ok=True)
        with open(self.directives_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def add_user_directive(self, kind: str, target_text: str, source: str = "TELEGRAM") -> str:
        """
        Persists a live user directive (`untouchable`, `transferable`, `force_sell`, `blacklist_buy`, `target`, `note`).
        """
        d = self._load_local_directives_dict()
        clean = str(target_text or "").strip()
        if not clean:
            return "Directiva vacía."

        norm_c = _strip_accents(clean)
        if kind == "untouchable":
            d["transferables"] = [x for x in d["transferables"] if _strip_accents(x) != norm_c]
            d["force_sell"] = [x for x in d["force_sell"] if _strip_accents(x) != norm_c]
            if all(_strip_accents(x) != norm_c for x in d["untouchables"]):
                d["untouchables"].append(clean)
            msg = f"🛡️ Marcado como INTOCABLE en memoria persistente: {clean}"
        elif kind == "transferable":
            d["untouchables"] = [x for x in d["untouchables"] if _strip_accents(x) != norm_c]
            if all(_strip_accents(x) != norm_c for x in d["transferables"]):
                d["transferables"].append(clean)
            msg = f"♻️ Desbloqueado como TRANSFERIBLE (según NPV/colateral): {clean}"
        elif kind == "force_sell":
            d["untouchables"] = [x for x in d["untouchables"] if _strip_accents(x) != norm_c]
            if all(_strip_accents(x) != norm_c for x in d["force_sell"]):
                d["force_sell"].append(clean)
            msg = f"📤 Marcado para VENTA PRIORITARIA cuando llegue oferta firme: {clean}"
        elif kind == "blacklist_buy":
            if all(_strip_accents(x) != norm_c for x in d["blacklist_buy"]):
                d["blacklist_buy"].append(clean)
            msg = f"🚫 Añadido a lista de NO FICHAR: {clean}"
        elif kind == "target":
            if all(_strip_accents(x) != norm_c for x in d["priority_targets"]):
                d["priority_targets"].append(clean)
            msg = f"🎯 Añadido como OBJETIVO PRIORITARIO: {clean}"
        else:
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            d["notes"].append(f"[{ts}] {clean}")
            d["notes"] = d["notes"][-25:]
            msg = f"📝 Nota estratégica guardada en memoria: {clean}"

        self._save_local_directives_dict(d)
        self.record_strategic_note(
            jornada_objetivo=0,
            fase="DIRECTIVA_USUARIO",
            regimen_mercado="",
            plan_deuda_colateral="",
            objetivos_prioritarios=", ".join(d["priority_targets"][-5:]),
            notas_estrategicas=msg,
            origen=source,
        )
        return msg

    @staticmethod
    def _match_player_ids(queries: List[str], catalog_df: Optional[pd.DataFrame]) -> Set[int]:
        if not queries or catalog_df is None or catalog_df.empty or "player_id" not in catalog_df.columns:
            return set()

        matched: Set[int] = set()
        cat = catalog_df[["player_id", "nombre"]].drop_duplicates("player_id").copy()
        cat["pid"] = pd.to_numeric(cat["player_id"], errors="coerce").fillna(0).astype(int)
        cat["norm_name"] = cat["nombre"].apply(_strip_accents)

        for q in queries:
            q_str = str(q).strip()
            if not q_str:
                continue
            if q_str.isdigit():
                matched.add(int(q_str))
                continue
            nq = _strip_accents(q_str)
            if len(nq) < 3:
                continue
            exact_or_sub = cat[cat["norm_name"].str.contains(re.escape(nq), na=False)]
            if not exact_or_sub.empty:
                for pid in exact_or_sub["pid"].tolist():
                    matched.add(int(pid))
            else:
                try:
                    from thefuzz import process
                    choices = dict(zip(cat["pid"], cat["norm_name"]))
                    best = process.extractOne(nq, choices)
                    if best and best[1] >= 84:
                        matched.add(int(best[2]))
                except Exception:
                    pass
        return matched

    def resolve_active_directives(self, catalog_df: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
        """
        Combines Google Sheets `Instrucciones` + Telegram/local `user_directives.json` into resolved player ID sets.
        """
        d = self._load_local_directives_dict()
        untouch_names = list(d.get("untouchables", []))
        transf_names = list(d.get("transferables", []))
        sell_names = list(d.get("force_sell", []))
        black_names = list(d.get("blacklist_buy", []))
        target_names = list(d.get("priority_targets", []))
        free_notes = list(d.get("notes", []))

        if not self.in_memory_only:
            try:
                sheet_lines = get_instructions_for_recipient("agent")
            except Exception:
                sheet_lines = []

            for line in sheet_lines:
                s = str(line).strip()
                up = s.upper()
                if up.startswith("INTOCABLE:") or up.startswith("INTOCABLES:"):
                    vals = [x.strip() for x in s.split(":", 1)[1].split(",") if x.strip()]
                    untouch_names.extend(vals)
                elif up.startswith("VENDER:") or up.startswith("VENTA:"):
                    vals = [x.strip() for x in s.split(":", 1)[1].split(",") if x.strip()]
                    sell_names.extend(vals)
                elif up.startswith("NO_FICHAR:") or up.startswith("NO FICHAR:") or up.startswith("VETAR:"):
                    vals = [x.strip() for x in s.split(":", 1)[1].split(",") if x.strip()]
                    black_names.extend(vals)
                elif up.startswith("OBJETIVO:") or up.startswith("FICHAR:"):
                    vals = [x.strip() for x in s.split(":", 1)[1].split(",") if x.strip()]
                    target_names.extend(vals)
                else:
                    free_notes.append(s)

        untouchable_ids = self._match_player_ids(untouch_names, catalog_df)
        transferable_ids = self._match_player_ids(transf_names, catalog_df)
        untouchable_ids = untouchable_ids - transferable_ids

        force_sell_ids = self._match_player_ids(sell_names, catalog_df) - untouchable_ids
        blacklist_buy_ids = self._match_player_ids(black_names, catalog_df)
        priority_target_ids = self._match_player_ids(target_names, catalog_df) - blacklist_buy_ids

        return {
            "untouchable_ids": untouchable_ids,
            "force_sell_ids": force_sell_ids,
            "blacklist_buy_ids": blacklist_buy_ids,
            "priority_target_ids": priority_target_ids,
            "free_notes": free_notes[-20:],
        }

    # ------------------------------------------------------------------ #
    # 2. Asset Theses (`Tesis_Activos`) & Multi-Day Offer-Roll Patience
    # ------------------------------------------------------------------ #
    def get_asset_theses_map(self) -> Dict[int, Dict[str, Any]]:
        conn = self._connect()
        try:
            df = pd.read_sql_query("SELECT * FROM Tesis_Activos", conn)
        except Exception:
            df = pd.DataFrame()
        finally:
            self._close_if_disk(conn)
        if df.empty:
            return {}
        return {int(r["player_id"]): dict(r) for _, r in df.iterrows()}

    def update_squad_theses(
        self,
        theses_rows: List[Dict[str, Any]],
        current_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Upserts current squad's `Tesis_Activos` rows and prunes players no longer in squad,
        preserving multi-day `offer_rolls` continuity from previous sessions!
        """
        prev_map = self.get_asset_theses_map()
        ts_str = current_date or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        owned_pids: Set[int] = set()
        clean_rows: List[Dict[str, Any]] = []

        for row in theses_rows:
            pid = int(row["player_id"])
            owned_pids.add(pid)
            prev = prev_map.get(pid, {})

            # Preserve acquisition metadata if already stored and missing today
            fecha_compra = row.get("fecha_compra") or prev.get("fecha_compra") or ""
            precio_compra = int(row.get("precio_compra") or prev.get("precio_compra") or row.get("precio_actual", 0))

            # Offer roll counter: increments when a listed player receives a suboptimal (< +2.5% VM) offer and is held
            offer_amt = int(row.get("ultima_oferta") or 0)
            mkt_price = max(1, int(row.get("precio_actual") or 1))
            prev_rolls = int(prev.get("offer_rolls") or 0)
            if "offer_rolls" in row and row["offer_rolls"] is not None:
                rolls = int(row["offer_rolls"])
            elif offer_amt > 0 and (offer_amt - mkt_price) / mkt_price < 0.025:
                rolls = prev_rolls + 1
            elif offer_amt == 0:
                rolls = prev_rolls
            else:
                rolls = 0

            clean_rows.append({
                "player_id": pid,
                "nombre": str(row.get("nombre", prev.get("nombre", f"ID {pid}"))),
                "rol": str(row.get("rol", prev.get("rol", "BENCH_PATCH"))),
                "fecha_compra": str(fecha_compra),
                "precio_compra": precio_compra,
                "precio_actual": mkt_price,
                "clausula_actual": int(row.get("clausula_actual") or prev.get("clausula_actual") or 0),
                "coste_reposicion": int(row.get("coste_reposicion") or mkt_price),
                "escasez_tier": str(row.get("escasez_tier") or "MODERATE"),
                "npv_hold": int(row.get("npv_hold") or mkt_price),
                "npv_sell": int(row.get("npv_sell") or mkt_price),
                "recomendacion_npv": str(row.get("recomendacion_npv") or "HOLD"),
                "offer_rolls": rolls,
                "ultima_oferta": offer_amt,
                "intocable": 1 if row.get("intocable") else 0,
                "forzar_venta": 1 if row.get("forzar_venta") else 0,
                "tesis_resumen": str(row.get("tesis_resumen") or ""),
                "updated_at": ts_str,
            })

        df_out = pd.DataFrame(clean_rows)
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM Tesis_Activos")
            if not df_out.empty:
                df_out.to_sql("Tesis_Activos", conn, if_exists="append", index=False)
            conn.commit()
        finally:
            self._close_if_disk(conn)

        if self.sync_sheets and GoogleSheetsConfig.ENABLED and not df_out.empty:
            try:
                BiwengerWarehouse().sync_dataframe_to_sheet("Tesis_Activos", df_out)
            except Exception as e:
                print(f"⚠️ Aviso sincronizando Tesis_Activos a Google Sheets: {e}")
        return df_out

    # ------------------------------------------------------------------ #
    # 3. Strategic Journal (`Memoria_Estrategica`)
    # ------------------------------------------------------------------ #
    def record_strategic_note(
        self,
        jornada_objetivo: int,
        fase: str,
        regimen_mercado: str,
        plan_deuda_colateral: str,
        objetivos_prioritarios: str,
        notas_estrategicas: str,
        origen: str = "AUTOPILOT",
        ts_override: Optional[str] = None,
    ) -> None:
        ts = ts_override or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry_id = f"{ts}_{origen}_{jornada_objetivo}_{fase}"[:90]
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO Memoria_Estrategica
                (entry_id, ts, jornada_objetivo, fase, regimen_mercado, plan_deuda_colateral, objetivos_prioritarios, notas_estrategicas, origen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id, ts, int(jornada_objetivo), str(fase), str(regimen_mercado),
                    str(plan_deuda_colateral), str(objetivos_prioritarios), str(notas_estrategicas), str(origen),
                ),
            )
            conn.commit()
        finally:
            self._close_if_disk(conn)

        if self.sync_sheets and GoogleSheetsConfig.ENABLED:
            try:
                df_mem = self.get_recent_strategic_notes(limit=40)
                if not df_mem.empty:
                    BiwengerWarehouse().sync_dataframe_to_sheet("Memoria_Estrategica", df_mem)
            except Exception as e:
                print(f"⚠️ Aviso sincronizando Memoria_Estrategica a Google Sheets: {e}")

    def get_recent_strategic_notes(self, limit: int = 10) -> pd.DataFrame:
        conn = self._connect()
        try:
            return pd.read_sql_query(
                f"SELECT * FROM Memoria_Estrategica ORDER BY ts DESC LIMIT {int(limit)}",
                conn,
            )
        except Exception:
            return pd.DataFrame()
        finally:
            self._close_if_disk(conn)

    # ------------------------------------------------------------------ #
    # 4. Human-Gated Queue (`Decisiones_Pendientes` for SEMI_AUTO)
    # ------------------------------------------------------------------ #
    def queue_pending_decision(
        self,
        action_type: str,
        player_id: int,
        nombre: str,
        amount_eur: int,
        market_price_eur: int = 0,
        counterparty: str = "",
        owner_user_id: int = 0,
        offer_id: int = 0,
        rationale: str = "",
        expires_hours: int = 18,
        payload: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Enqueues a high-stakes decision (`CLAUSE_SNIPE`, `DEFENSIVE_SHIELD`, `IMPORTANT_SALE`)
        requiring explicit user approval in `SEMI_AUTO`. Idempotent for identical open player+action.
        """
        prefix = {
            "CLAUSE_SNIPE": "CL",
            "DEFENSIVE_SHIELD": "BL",
            "IMPORTANT_SALE": "VT",
            "RIVAL_COUNTER_OFFER": "CO",
        }.get(action_type, "OP")
        now = datetime.datetime.now()
        exp = now + datetime.timedelta(hours=int(expires_hours))

        conn = self._connect()
        try:
            cur = conn.cursor()
            #Expire old items first
            cur.execute(
                "UPDATE Decisiones_Pendientes SET status='EXPIRED' WHERE status='PENDING' AND expires_at < ?",
                (now.strftime("%Y-%m-%d %H:%M:%S"),),
            )
            # Reuse existing PENDING ID if same action_type & player_id
            cur.execute(
                "SELECT decision_id FROM Decisiones_Pendientes WHERE status='PENDING' AND action_type=? AND player_id=?",
                (action_type, int(player_id)),
            )
            existing = cur.fetchone()
            decision_id = existing[0] if existing else f"{prefix}-{int(player_id)}"

            cur.execute(
                """
                INSERT OR REPLACE INTO Decisiones_Pendientes
                (decision_id, created_at, expires_at, action_type, player_id, nombre, amount_eur,
                 market_price_eur, counterparty, owner_user_id, offer_id, status, rationale, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
                """,
                (
                    decision_id,
                    now.strftime("%Y-%m-%d %H:%M:%S"),
                    exp.strftime("%Y-%m-%d %H:%M:%S"),
                    action_type,
                    int(player_id),
                    str(nombre),
                    int(amount_eur),
                    int(market_price_eur),
                    str(counterparty),
                    int(owner_user_id or 0),
                    int(offer_id or 0),
                    str(rationale),
                    json.dumps(payload or {}, ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            self._close_if_disk(conn)

        self._sync_pending_to_sheets()
        return decision_id

    def get_pending_decisions(self, status: str = "PENDING") -> List[Dict[str, Any]]:
        now_s = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE Decisiones_Pendientes SET status='EXPIRED' WHERE status='PENDING' AND expires_at < ?",
                (now_s,),
            )
            conn.commit()
            df = pd.read_sql_query(
                "SELECT * FROM Decisiones_Pendientes WHERE status=? ORDER BY created_at DESC",
                conn,
                params=(status,),
            )
            return df.to_dict("records") if not df.empty else []
        except Exception:
            return []
        finally:
            self._close_if_disk(conn)

    def resolve_pending_decision(self, decision_id: str, new_status: str) -> Optional[Dict[str, Any]]:
        clean_id = str(decision_id or "").strip().upper()
        conn = self._connect()
        try:
            df = pd.read_sql_query(
                "SELECT * FROM Decisiones_Pendientes WHERE UPPER(decision_id)=?",
                conn,
                params=(clean_id,),
            )
            if df.empty:
                return None
            rec = dict(df.iloc[0])
            conn.execute(
                "UPDATE Decisiones_Pendientes SET status=? WHERE UPPER(decision_id)=?",
                (new_status, clean_id),
            )
            conn.commit()
            rec["status"] = new_status
        finally:
            self._close_if_disk(conn)
        self._sync_pending_to_sheets()
        return rec

    def get_decisions_by_statuses(self, statuses: List[str]) -> List[Dict[str, Any]]:
        if not statuses:
            return []
        norm = [str(s).strip().upper() for s in statuses if str(s).strip()]
        placeholders = ",".join("?" for _ in norm)
        conn = self._connect()
        try:
            df = pd.read_sql_query(
                f"SELECT * FROM Decisiones_Pendientes WHERE UPPER(TRIM(status)) IN ({placeholders}) ORDER BY created_at ASC",
                conn,
                params=tuple(norm),
            )
            return df.to_dict("records") if not df.empty else []
        except Exception:
            return []
        finally:
            self._close_if_disk(conn)

    def _sync_pending_to_sheets(self) -> None:
        if not (self.sync_sheets and GoogleSheetsConfig.ENABLED):
            return
        conn = self._connect()
        try:
            df = pd.read_sql_query("SELECT * FROM Decisiones_Pendientes ORDER BY created_at DESC LIMIT 50", conn)
            if not df.empty:
                BiwengerWarehouse().sync_dataframe_to_sheet("Decisiones_Pendientes", df)
        except Exception as e:
            print(f"⚠️ Aviso sincronizando Decisiones_Pendientes a Google Sheets: {e}")
        finally:
            self._close_if_disk(conn)

    # ------------------------------------------------------------------ #
    # 5. Dynamic Trigger Idempotency (`Eventos_Jornada`)
    # ------------------------------------------------------------------ #
    def has_round_event_fired(self, event_key: str) -> bool:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM Eventos_Jornada WHERE event_key=?", (str(event_key),))
            return cur.fetchone() is not None
        except Exception:
            return False
        finally:
            self._close_if_disk(conn)

    def mark_round_event_fired(
        self,
        event_key: str,
        jornada_num: int,
        trigger_type: str,
        status: str = "OK",
        summary: str = "",
    ) -> None:
        now_s = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO Eventos_Jornada
                (event_key, jornada_num, trigger_type, executed_at, status, summary)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(event_key), int(jornada_num), str(trigger_type), now_s, str(status), str(summary)),
            )
            conn.commit()
        finally:
            self._close_if_disk(conn)
        self._sync_round_events_to_sheets()

    def _sync_round_events_to_sheets(self) -> None:
        if not (self.sync_sheets and GoogleSheetsConfig.ENABLED):
            return
        conn = self._connect()
        try:
            df = pd.read_sql_query("SELECT * FROM Eventos_Jornada ORDER BY executed_at DESC LIMIT 100", conn)
            if not df.empty:
                BiwengerWarehouse().sync_dataframe_to_sheet("Eventos_Jornada", df)
        except Exception as e:
            print(f"⚠️ Aviso sincronizando Eventos_Jornada a Google Sheets: {e}")
        finally:
            self._close_if_disk(conn)

    def build_context_brief_for_llm(self) -> str:
        """Compact summary of asset theses, pending decisions, and recent strategic notes for LLM/Telegram."""
        theses = list(self.get_asset_theses_map().values())
        pending = self.get_pending_decisions("PENDING")
        notes_df = self.get_recent_strategic_notes(limit=5)

        lines = ["### Memoria Estratégica Persistente (`Fantasy Champion v3`)"]
        if theses:
            cores = [t["nombre"] for t in theses if t.get("rol") == "FRANCHISE_CORE"]
            flips = [f"{t['nombre']} (rolls={t.get('offer_rolls', 0)})" for t in theses if t.get("rol") == "SPECULATIVE_FLIP"]
            sells = [t["nombre"] for t in theses if t.get("recomendacion_npv") == "SELL"]
            lines.append(f"- **Pilares Franquicia (`FRANCHISE_CORE`):** {', '.join(cores) if cores else 'Ninguno'}")
            lines.append(f"- **Flips Activos (`SPECULATIVE_FLIP`):** {', '.join(flips) if flips else 'Ninguno'}")
            lines.append(f"- **Candidatos Venta por NPV (`NPV_SELL > NPV_HOLD`):** {', '.join(sells) if sells else 'Ninguno'}")

        if pending:
            lines.append("- **Decisiones Pendientes de Confirmación Humana (`SEMI_AUTO`):**")
            for p in pending:
                lines.append(
                    f"  - `[{p['decision_id']}]` {p['action_type']} -> **{p['nombre']}** por `{int(p['amount_eur']):,} €` ({p['rationale']})"
                )
        else:
            lines.append("- **Decisiones Pendientes:** Ninguna.")

        if not notes_df.empty:
            lines.append("- **Últimas Notas de Sesión:**")
            for _, nr in notes_df.iterrows():
                lines.append(f"  - `{nr['ts']}` [{nr['fase']}]: {nr['notas_estrategicas']}")

        return "\n".join(lines)
