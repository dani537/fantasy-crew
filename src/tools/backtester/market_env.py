"""
AZ Finance 91-Day Historical Market & League Environment (`AZFinanceMarketEnv`)
===============================================================================
Reproduces Biwenger league `AZ Finance` (`league_id=1643248`, 15 managers,
`scoreID=5` "Mitjana AS i SofaScore", `superPicaExtraPoints=False`,
`lineupMultiPos=True`, `clauseIncrement=1`, 25% credit line, +-5% Computer
offers, 24h sale lock, 5-day post-purchase clause immunity, postponed-round
initial lock & recalculation, and exact reverse-handicap matchday bonuses)
from Day 1 (`2026-08-01`) through `2026-10-30`.
"""

import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Any, Optional, Set, Tuple
import numpy as np
import pandas as pd

from src.tools.data_extraction.warehouse import compute_official_clause
from src.tools.strategy_engine.xp_model import (
    ExpectedPointsEngine,
    GOAL_BONUS_BY_LINE,
    normalize_position,
)
from src.tools.strategy_engine.portfolio_optimizer import (
    PortfolioOptimizer,
    OFFICIAL_FORMATIONS,
)
from src.tools.backtester.causal_clock import StrictCausalDataView, CausalEFRPredictor
from src.utils.numeric import safe_num


@dataclass
class OwnedPlayerState:
    player_id: int
    nombre: str
    posicion: str
    acquired_date: str
    purchase_price: int
    extra_shield: int = 0


class AZFinanceMarketEnv:
    """
    Deterministic / Seeded Monte-Carlo Market Simulator for AZ Finance.
    """

    def __init__(
        self,
        db_path: str = "./data/warehouse/biwenger_history.db",
        target_manager: str = "Dani SR",
        target_user_id: int = 9878097,
        market_mode: str = "monte_carlo_20slot",
        seed: int = 42,
        credit_limit_ratio: float = 0.25,
        max_squad_size: int = 21,
    ):
        self.db_path = db_path
        self.target_manager = target_manager
        self.target_user_id = int(target_user_id)
        self.market_mode = market_mode  # "strict_observed" or "monte_carlo_20slot"
        self.seed = int(seed)
        self.rng = np.random.default_rng(self.seed)
        self.credit_limit_ratio = float(credit_limit_ratio)
        self.max_squad_size = int(max_squad_size)

        self.view = StrictCausalDataView(db_path=db_path)
        self.efr_predictor = CausalEFRPredictor(self.view)
        self.xp_engine = ExpectedPointsEngine()

        conn = sqlite3.connect(db_path)
        try:
            self._df_mkt_hist = pd.read_sql("SELECT * FROM Mercado_Diario_Historico", conn)
            self._df_movs = pd.read_sql("SELECT * FROM Movimientos", conn)
            self._df_primas = pd.read_sql("SELECT * FROM Primas_Jornadas", conn)
            self._df_lineups = pd.read_sql("SELECT * FROM Alineaciones_Jornadas", conn)
        finally:
            conn.close()

        self._prepare_environment_ground_truth()
        self.reset()

    def _prepare_environment_ground_truth(self) -> None:
        """
        Precomputes environment-only settlement tables (NEVER exposed to the strategy
        before settlement timestamps):
          1. Exact `scoreID=5` player round points, goals, and MVP/Ideal-XI flags for J1..J10.
          2. Primary kickoff dates (`part == 1`) and official `Primas_Jornadas` payout dates.
          3. Rival auction winning bids (`manager_pujador != target_manager`) by resolution date.
          4. Daily observed market listings & 5-day post-purchase clause locks from `Movimientos`.
        """
        # 1. Primary kickoff dates (part == 1) for J1..J11
        self.kickoff_date_to_round: Dict[str, int] = {}
        self.round_to_kickoff_date: Dict[int, str] = {}
        cal_p1 = self.view._df_calendar[self.view._df_calendar["part_int"] == 1]
        for jn, grp in cal_p1.groupby("jornada_num_int"):
            jn_int = int(jn)
            if 1 <= jn_int <= 11:
                valid_dts = sorted([s[:10] for s in grp["fecha_hora"].tolist() if len(s) >= 10])
                if valid_dts:
                    kd = valid_dts[0]
                    self.round_to_kickoff_date[jn_int] = kd
                    self.kickoff_date_to_round[kd] = jn_int

        # 2. Official bonus payout dates & rival round scores from `Primas_Jornadas`
        self.payout_date_to_rounds: Dict[str, List[int]] = {}
        self.rival_round_points: Dict[int, Dict[str, int]] = {}
        for _, r in self._df_primas.iterrows():
            j_label = str(r.get("Jornada", ""))
            digits = "".join(ch for ch in j_label if ch.isdigit())
            if not digits:
                continue
            jn = int(digits)
            p_date = str(r.get("Fecha", ""))[:10]
            mgr = str(r.get("Manager", "")).strip()
            pts = int(safe_num(r.get("Puntos", 0), 0))
            if mgr != self.target_manager:
                self.rival_round_points.setdefault(jn, {})[mgr] = pts
            if p_date and jn not in self.payout_date_to_rounds.get(p_date, []):
                self.payout_date_to_rounds.setdefault(p_date, []).append(jn)

        # 3. Exact `scoreID=5` Ground-Truth Player Points per Round (J1..J10)
        # Reconstructed from exact `Historico_Continuo.puntos_totales` match-window deltas
        # for J4..J10 (verified 15/15 exact match with AZ Finance official scores) and
        # `Rendimiento_Jornadas` calibrated to `2026-09-02` cumulative `scoreID=5` totals for J1..J3.
        pts_piv_dates = [
            "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-09",
            "2026-09-11", "2026-09-15", "2026-09-18", "2026-09-22",
            "2026-10-09", "2026-10-14", "2026-10-16", "2026-10-20",
            "2026-10-21", "2026-10-23", "2026-10-28",
        ]
        sub_hc = self.view._df_market[self.view._df_market["fecha"].isin(pts_piv_dates)].copy()
        sub_hc["pts_tot"] = pd.to_numeric(sub_hc["puntos_totales"], errors="coerce").fillna(0).astype(int)
        piv = sub_hc.pivot_table(index="player_id", columns="fecha", values="pts_tot", aggfunc="last").fillna(0).astype(int)

        self.ground_truth_round_pts: Dict[int, Dict[int, int]] = {jn: {} for jn in range(1, 11)}
        self.ground_truth_goals: Dict[int, Dict[int, int]] = {jn: {} for jn in range(1, 11)}
        self.ground_truth_awards: Dict[int, Dict[int, Tuple[int, int, int]]] = {jn: {} for jn in range(1, 11)}

        # Populate goals & MVP/Ideal-XI flags from Rendimiento_Jornadas
        rep_all = self.view._df_reports
        for _, rr in rep_all.iterrows():
            jn = int(rr["jornada_num_int"])
            if not (1 <= jn <= 10):
                continue
            pid = int(safe_num(rr.get("player_id", 0), 0))
            if pid <= 0:
                continue
            gls = int(safe_num(rr.get("goles", 0), 0))
            self.ground_truth_goals[jn][pid] = gls
            mvp_m = 1 if str(rr.get("es_mvp_partido", "")).upper() in ("SÍ", "SI", "1", "TRUE") else 0
            mvp_j = 1 if str(rr.get("es_mvp_jornada", "")).upper() in ("SÍ", "SI", "1", "TRUE") else 0
            once_i = 1 if str(rr.get("en_once_ideal", "")).upper() in ("SÍ", "SI", "1", "TRUE") else 0
            self.ground_truth_awards[jn][pid] = (mvp_m, mvp_j, once_i)

        # J4..J10 exact `scoreID=5` deltas from `Historico_Continuo`
        delta_windows = {
            4: [("2026-09-04", "2026-09-09")],
            5: [("2026-09-11", "2026-09-15")],
            6: [("2026-09-03", "2026-09-04"), ("2026-09-15", "2026-09-18"), ("2026-10-21", "2026-10-23")],
            7: [("2026-09-18", "2026-09-22")],
            8: [("2026-10-09", "2026-10-14")],
            9: [("2026-10-16", "2026-10-20")],
            10: [("2026-10-23", "2026-10-28")],
        }
        for jn, wins in delta_windows.items():
            s = pd.Series(0, index=piv.index, dtype=int)
            for d0, d1 in wins:
                if d0 in piv.columns and d1 in piv.columns:
                    s = s + (piv[d1] - piv[d0])
            self.ground_truth_round_pts[jn] = s.to_dict()

        # J1..J3: start from Rendimiento_Jornadas and reconcile integer residual with exact `2026-09-02` `scoreID=5` total
        rj_123: Dict[int, Dict[int, int]] = {1: {}, 2: {}, 3: {}}
        played_123: Dict[int, List[int]] = {}
        for _, rr in rep_all[rep_all["jornada_num_int"].isin([1, 2, 3])].iterrows():
            jn = int(rr["jornada_num_int"])
            pid = int(safe_num(rr.get("player_id", 0), 0))
            pts = int(safe_num(rr.get("puntos_biwenger", 0), 0))
            mins = int(safe_num(rr.get("minutos_jugados", 0), 0))
            if pid > 0:
                rj_123[jn][pid] = pts
                if mins > 0 or pts != 0:
                    played_123.setdefault(pid, []).append(jn)

        pts_0902_map = piv["2026-09-02"].to_dict() if "2026-09-02" in piv.columns else {}
        all_pids = set(pts_0902_map.keys()) | set(played_123.keys())
        for pid in all_pids:
            j_vals = {1: rj_123[1].get(pid, 0), 2: rj_123[2].get(pid, 0), 3: rj_123[3].get(pid, 0)}
            active_js = sorted(set(played_123.get(pid, [])))
            if pid in pts_0902_map and active_js:
                target_sum = int(pts_0902_map[pid])
                curr_sum = sum(j_vals.values())
                diff = target_sum - curr_sum
                if diff != 0:
                    base_add = int(diff / len(active_js))
                    rem = diff - base_add * len(active_js)
                    for idx_j, jn_act in enumerate(active_js):
                        extra = (1 if rem > 0 and idx_j < rem else (-1 if rem < 0 and idx_j < abs(rem) else 0))
                        j_vals[jn_act] += base_add + extra
            for jn in (1, 2, 3):
                self.ground_truth_round_pts[jn][pid] = int(j_vals[jn])

        # Overwrite the (mis-scored) `Rendimiento_Jornadas.puntos_biwenger` column inside the causal
        # view with the verified AZ Finance `scoreID=5` points, so the xP model learns from the real
        # league scoring. Rows only become visible after `fecha_partido < decision_cutoff_ts`, so
        # this introduces no lookahead (it is the true score of an already-played match).
        rep_df = self.view._df_reports
        rep_pids = pd.to_numeric(rep_df["player_id"], errors="coerce").fillna(0).astype(int)
        corrected = [
            self.ground_truth_round_pts.get(int(jn), {}).get(int(pid), None)
            for jn, pid in zip(rep_df["jornada_num_int"].values, rep_pids.values)
        ]
        orig_pts = pd.to_numeric(rep_df["puntos_biwenger"], errors="coerce").fillna(0).astype(int)
        rep_df["puntos_biwenger"] = [
            int(c) if c is not None else int(o) for c, o in zip(corrected, orig_pts.values)
        ]

        # Player static natural position lookup
        latest_mkt = self.view._df_market.drop_duplicates(subset=["player_id"], keep="last")
        self.player_meta: Dict[int, Dict[str, str]] = {}
        for _, r in latest_mkt.iterrows():
            pid = int(r["player_id"])
            self.player_meta[pid] = {
                "nombre": str(r.get("nombre", f"ID {pid}")),
                "posicion": str(r.get("posicion", "")),
                "natural_pos": normalize_position(r.get("posicion", "")),
            }

        # 4. Rival Auction Bids indexed by (resolution_date, player_id) -> max rival bid (excluding target_manager!)
        self.max_rival_bid_by_res_date: Dict[Tuple[str, int], int] = {}
        for _, br in self.view._df_bids.iterrows():
            mgr = str(br.get("manager_pujador", "")).strip()
            if mgr == self.target_manager:
                continue
            d_res = str(br.get("bid_day", ""))[:10]
            pid = int(safe_num(br.get("player_id", 0), 0))
            amt = int(safe_num(br.get("importe_pujado", 0), 0))
            if d_res and pid > 0 and amt > 0:
                key = (d_res, pid)
                if amt > self.max_rival_bid_by_res_date.get(key, 0):
                    self.max_rival_bid_by_res_date[key] = amt

        # 5. Observed daily market listings & observed Computer offers
        self._df_mkt_hist["fecha_d"] = self._df_mkt_hist["fecha"].astype(str).str.slice(0, 10)
        self._df_mkt_hist["pid_int"] = pd.to_numeric(self._df_mkt_hist["player_id"], errors="coerce").fillna(0).astype(int)

        self.en_venta_by_date: Dict[str, pd.DataFrame] = {}
        ev = self._df_mkt_hist[self._df_mkt_hist["tipo_registro"] == "En Venta Mercado"]
        for d, grp in ev.groupby("fecha_d"):
            self.en_venta_by_date[str(d)] = grp.copy()

        # Subasta Resuelta on `t+1` was listed on the market during day `t`!
        self.resolved_auctions_by_res_date: Dict[str, pd.DataFrame] = {}
        sr = self._df_mkt_hist[self._df_mkt_hist["tipo_registro"] == "Subasta Resuelta"]
        for d, grp in sr.groupby("fecha_d"):
            self.resolved_auctions_by_res_date[str(d)] = grp.copy()

        self.observed_offers_by_date: Dict[Tuple[str, int], int] = {}
        of_df = self._df_mkt_hist[self._df_mkt_hist["tipo_registro"].astype(str).str.startswith("Oferta")]
        for _, orow in of_df.iterrows():
            d = str(orow["fecha_d"])
            pid = int(orow["pid_int"])
            amt = int(safe_num(orow.get("precio_salida_o_oferta", 0), 0))
            if pid > 0 and amt > 0:
                self.observed_offers_by_date[(d, pid)] = amt

        # 6. Historical Movimientos for 5-day rival clause locks & incoming rival clause steals
        self._df_movs["mov_day"] = self._df_movs["fecha"].astype(str).str.slice(0, 10)
        self._df_movs["pid_int"] = pd.to_numeric(self._df_movs["player_id"], errors="coerce").fillna(0).astype(int)
        self._df_movs["precio_int"] = pd.to_numeric(self._df_movs["precio"], errors="coerce").fillna(0).astype(int)

        self.rival_acquisitions_by_day: Dict[str, List[int]] = {}
        self.rival_clause_attacks_by_day: Dict[str, List[Dict[str, Any]]] = {}
        for _, mv in self._df_movs.iterrows():
            d = str(mv["mov_day"])
            pid = int(mv["pid_int"])
            comp = str(mv.get("comprador", "")).strip()
            tipo = str(mv.get("tipo", "")).strip()
            if pid <= 0 or not d:
                continue
            if comp and comp not in ("Mercado", self.target_manager):
                self.rival_acquisitions_by_day.setdefault(d, []).append(pid)
                if tipo == "Clausulazo":
                    self.rival_clause_attacks_by_day.setdefault(d, []).append({
                        "player_id": pid,
                        "attacker": comp,
                        "paid_clause": int(mv["precio_int"]),
                    })

    def reset(self) -> None:
        """Resets portfolio to `Dani SR`'s exact `2026-08-01` initial draw and `23,900,000 €` cash."""
        self.rng = np.random.default_rng(self.seed)
        self.view.set_date("2026-08-01")
        self.efr_predictor = CausalEFRPredictor(self.view)

        mgr_d1 = self.view.get_manager_snapshots("2026-08-01")
        my_row = mgr_d1[mgr_d1["manager"] == self.target_manager]
        if my_row.empty:
            raise RuntimeError(f"Manager '{self.target_manager}' not found on 2026-08-01 in Historico_Equipos_Diario")

        row0 = my_row.iloc[0]
        self.cash: float = safe_num(row0["saldo_disponible_exacto"], 23_900_000.0)
        init_pids = [int(x) for x in str(row0["plantilla_ids"]).split(",") if x.strip().isdigit()]

        mkt_d1 = self.view.get_market_snapshot("2026-08-01").set_index("player_id").to_dict("index")
        self.squad: Dict[int, OwnedPlayerState] = {}
        for pid in init_pids:
            p_info = mkt_d1.get(pid, self.player_meta.get(pid, {}))
            vm = int(safe_num(p_info.get("precio", 150_000), 150_000))
            self.squad[pid] = OwnedPlayerState(
                player_id=pid,
                nombre=str(p_info.get("nombre", f"ID {pid}")),
                posicion=str(p_info.get("posicion", "Centrocampista")),
                acquired_date="2026-07-25",  # Initial draw unlocked on 2026-08-01
                purchase_price=vm,
                extra_shield=0,
            )

        self.pending_bids: List[Dict[str, Any]] = []
        self.listed_pids: Set[int] = set()
        self.active_offers: Dict[int, Tuple[int, str]] = {}  # pid -> (offer_eur, expiry_date)
        self.recent_sold_dates: Dict[int, str] = {}

        # Round lock & scoring ledger
        self.locked_round_lineups: Dict[int, Dict[str, Any]] = {}
        self.settled_rounds: Dict[int, Dict[str, Any]] = {}

        # Telemetry & audit logs
        self.daily_curve: List[Dict[str, Any]] = []
        self.trade_log: List[Dict[str, Any]] = []
        self.bid_attempts: int = 0
        self.bid_wins: int = 0
        self.clause_snipes_executed: int = 0
        self.clause_steals_suffered: int = 0
        self.shields_spent_eur: int = 0
        self.bonuses_earned_eur: int = 0

    def _get_player_clause(self, st: OwnedPlayerState, current_vm: float) -> int:
        base = max(float(st.purchase_price), float(current_vm))
        return int(compute_official_clause(base, float(st.extra_shield)))

    def _build_squad_df(self, mkt_idx: Dict[int, Dict[str, Any]]) -> pd.DataFrame:
        rows = []
        for pid, st in sorted(self.squad.items()):
            m = mkt_idx.get(pid, {})
            vm = float(m.get("precio", st.purchase_price))
            sub = float(m.get("subida_24h", 0.0))
            rows.append({
                "player_id": pid,
                "nombre": st.nombre,
                "posicion": m.get("posicion", st.posicion),
                "equipo": m.get("equipo", ""),
                "estado": m.get("estado", "ok"),
                "precio": vm,
                "subida_24h": sub,
                "clausula_actual": self._get_player_clause(st, vm),
                "acquired_date": st.acquired_date,
                "purchase_price": st.purchase_price,
            })
        return pd.DataFrame(rows)

    def _build_daily_market_listings(self, date_str: str, next_date_str: Optional[str], mkt_today: pd.DataFrame) -> pd.DataFrame:
        """
        Constructs today's transfer market listings without leaking future prices:
          - If `date_str` has full `En Venta Mercado` rows (`2026-10-25..30`), use them directly.
          - Else take auctions that resolved on `next_date_str` morning (`Subasta Resuelta`),
            evaluating ask price strictly at `date_str`'s own market price (`precio` on `date_str`!).
          - In `monte_carlo_20slot` mode, fill remaining `Mercado` slots up to 20 free agents
            sampled uniformly via `self.rng` from `propietario_liga == 'Libre'` on `date_str`,
            so the bot always faces a realistic 20-player board and cannot infer which free agents
            rivals will contest!
        """
        mkt_idx = mkt_today.set_index("player_id").to_dict("index")
        listings: List[Dict[str, Any]] = []
        seen_pids: Set[int] = set()

        if date_str in self.en_venta_by_date:
            ev = self.en_venta_by_date[date_str]
            for _, r in ev.iterrows():
                pid = int(r["pid_int"])
                if pid <= 0 or pid in seen_pids or pid in self.squad:
                    continue
                vm_today = float(mkt_idx.get(pid, {}).get("precio", r.get("precio_mercado_dia", 150_000)))
                ask = safe_num(r.get("precio_salida_o_oferta", vm_today), vm_today)
                seller = str(r.get("vendedor_o_origen", "Mercado") or "Mercado").strip()
                if seller == self.target_manager:
                    continue
                listings.append({
                    "player_id": pid,
                    "jugador": str(mkt_idx.get(pid, {}).get("nombre", r.get("jugador", ""))),
                    "seller_name": seller,
                    "precio_mercado_dia": vm_today,
                    "ask_price": ask,
                })
                seen_pids.add(pid)
            return pd.DataFrame(listings)

        # Historical days (`2026-08-01..2026-10-24`): include auctions resolving tomorrow morning
        # (using ONLY today's `vm_today` as asking price for `Mercado` free agents -> zero future price leak!)
        n_mercado_slots = 0
        if next_date_str and next_date_str in self.resolved_auctions_by_res_date:
            sr = self.resolved_auctions_by_res_date[next_date_str]
            for _, r in sr.iterrows():
                pid = int(r["pid_int"])
                if pid <= 0 or pid in seen_pids or pid in self.squad:
                    continue
                if pid not in mkt_idx:
                    continue
                seller = str(r.get("vendedor_o_origen", "Mercado") or "Mercado").strip()
                if seller == self.target_manager:
                    continue
                vm_today = float(mkt_idx[pid].get("precio", 150_000))
                listings.append({
                    "player_id": pid,
                    "jugador": str(mkt_idx[pid].get("nombre", r.get("jugador", ""))),
                    "seller_name": seller,
                    "precio_mercado_dia": vm_today,
                    "ask_price": vm_today,
                })
                seen_pids.add(pid)
                if seller == "Mercado":
                    n_mercado_slots += 1

        if self.market_mode == "monte_carlo_20slot" and n_mercado_slots < 20:
            free_pool = mkt_today[
                (mkt_today["propietario_liga"].astype(str).isin(["Libre", "Mercado", ""]))
                & (~mkt_today["player_id"].isin(seen_pids | set(self.squad.keys())))
                & (mkt_today["precio"] > 0)
            ]
            need = min(20 - n_mercado_slots, len(free_pool))
            if need > 0:
                sampled_pids = self.rng.choice(free_pool["player_id"].values, size=need, replace=False)
                for spid in sampled_pids:
                    pid = int(spid)
                    p_info = mkt_idx[pid]
                    vm_today = float(p_info["precio"])
                    listings.append({
                        "player_id": pid,
                        "jugador": str(p_info.get("nombre", "")),
                        "seller_name": "Mercado",
                        "precio_mercado_dia": vm_today,
                        "ask_price": vm_today,
                    })
                    seen_pids.add(pid)

        return pd.DataFrame(listings)

    def _score_locked_round(self, jornada_num: int) -> Dict[str, Any]:
        """
        Scores our locked XI for `jornada_num` under official AZ Finance rules:
          - If `not is_solvent` (`cash < 0` at primary kickoff): 0 points & 0 € bonus.
          - Else sum exact `scoreID=5` base points + out-of-position multiposition goal bonus
            minus 4 pts per empty slot (< 11 players).
          - Compute exact ex-post optimal possible XI points across `owned_at_kickoff` for
            Coach Efficiency % tracking.
          - Rank against the 14 real rivals in `Primas_Jornadas` and compute exact bonus.
        """
        lock = self.locked_round_lineups.get(jornada_num)
        if not lock:
            return {"points": 0, "optimal_points": 0, "rank": 15, "bonus_eur": 0, "is_solvent": True}

        pts_map = self.ground_truth_round_pts.get(jornada_num, {})
        gls_map = self.ground_truth_goals.get(jornada_num, {})
        awd_map = self.ground_truth_awards.get(jornada_num, {})

        # Compute ex-post optimal possible XI points from `owned_at_kickoff`
        owned_pids: List[int] = list(lock["owned_at_kickoff"])
        opt_pts = self._compute_expost_optimal_xi_points(owned_pids, pts_map, gls_map)

        if not lock["is_solvent"]:
            return {
                "points": 0,
                "optimal_points": opt_pts,
                "rank": 15,
                "bonus_eur": 0,
                "is_solvent": False,
                "formation": lock["formation"],
            }

        slots: List[Dict[str, Any]] = lock["slots"]
        total_pts = 0
        mvp_m_cnt = 0
        mvp_j_cnt = 0
        ideal_cnt = 0

        for sl in slots[:11]:
            pid = int(sl["player_id"])
            ln = str(sl.get("linea", "MF"))
            base_p = int(pts_map.get(pid, 0))
            nat_pos = self.player_meta.get(pid, {}).get("natural_pos", ln) or ln
            goals = int(gls_map.get(pid, 0))
            multipos_goal_bonus = int(max(0.0, GOAL_BONUS_BY_LINE.get(ln, 3.0) - GOAL_BONUS_BY_LINE.get(nat_pos, 3.0)) * goals)
            total_pts += base_p + multipos_goal_bonus

            m_m, m_j, o_i = awd_map.get(pid, (0, 0, 0))
            mvp_m_cnt += m_m
            mvp_j_cnt += m_j
            ideal_cnt += o_i

        n_empty = max(0, 11 - len(slots[:11]))
        total_pts -= 4 * n_empty
        opt_pts = max(opt_pts, total_pts)

        # Rank against the 14 real rivals
        rivals = self.rival_round_points.get(jornada_num, {})
        higher_rivals = sum(1 for r_pts in rivals.values() if r_pts > total_pts)
        rank = 1 + higher_rivals  # 1..15

        if total_pts <= 0:
            total_bonus = 0
        else:
            prima_pts = total_pts * 50_000
            prima_pos = int(round(prima_pts * 0.06 * (rank - 3))) if (4 <= rank <= 14) else 0
            prima_awards = mvp_m_cnt * 100_000 + mvp_j_cnt * 500_000 + ideal_cnt * 250_000
            total_bonus = int(prima_pts + prima_pos + prima_awards)

        return {
            "points": int(total_pts),
            "optimal_points": int(opt_pts),
            "rank": int(rank),
            "bonus_eur": int(total_bonus),
            "is_solvent": True,
            "formation": lock["formation"],
        }

    def _compute_expost_optimal_xi_points(
        self,
        owned_pids: List[int],
        pts_map: Dict[int, int],
        gls_map: Dict[int, int],
    ) -> int:
        if not owned_pids:
            return 0
        # Fast greedy/assignment check across the 7 official formations
        p_scores_by_line: Dict[int, Dict[str, int]] = {}
        for pid in owned_pids:
            nat_pos = self.player_meta.get(pid, {}).get("natural_pos", "MF") or "MF"
            valid_lns = self.xp_engine.get_valid_lines(pid, nat_pos)
            base_p = int(pts_map.get(pid, 0))
            gls = int(gls_map.get(pid, 0))
            p_scores_by_line[pid] = {}
            for ln in valid_lns:
                g_bon = int(max(0.0, GOAL_BONUS_BY_LINE.get(ln, 3.0) - GOAL_BONUS_BY_LINE.get(nat_pos, 3.0)) * gls)
                p_scores_by_line[pid][ln] = base_p + g_bon

        best_total = -999
        for _, n_df, n_mf, n_fw in OFFICIAL_FORMATIONS:
            used: Set[int] = set()
            tot = 0
            # Pick GK, then FW/MF/DF greedily
            for ln, req in [("GK", 1), ("DF", n_df), ("MF", n_mf), ("FW", n_fw)]:
                cands = [
                    (p_scores_by_line[pid][ln], pid)
                    for pid in owned_pids
                    if pid not in used and ln in p_scores_by_line[pid]
                ]
                cands.sort(reverse=True)
                picked = cands[:req]
                for sc, pid in picked:
                    tot += sc
                    used.add(pid)
                if len(picked) < req:
                    tot -= 4 * (req - len(picked))
            if tot > best_total:
                best_total = tot
        return max(0, best_total)

    def run_simulation(
        self,
        optimizer: Optional[PortfolioOptimizer] = None,
        passive_lineup_only: bool = False,
        enable_leverage: bool = True,
        enable_clauses: bool = True,
        enable_preventive_listing: bool = True,
        start_date: str = "2026-08-01",
        end_date: str = "2026-10-30",
    ) -> Dict[str, Any]:
        """
        Executes the full day-by-day 91-day simulation from `start_date` to `end_date`.
        """
        self.reset()
        opt = optimizer or PortfolioOptimizer(
            xp_engine=self.xp_engine,
            credit_limit_ratio=self.credit_limit_ratio if enable_leverage else 0.0,
            max_squad_size=self.max_squad_size,
            preventive_listing=enable_preventive_listing,
        )
        opt.credit_limit_ratio = self.credit_limit_ratio if enable_leverage else 0.0
        opt.preventive_listing = enable_preventive_listing
        opt.clause_sniper.policy = "AGGRESSIVE" if enable_clauses else "DISABLED"

        sim_dates = [d for d in self.view.all_dates if start_date <= d <= end_date]

        for idx_d, date_str in enumerate(sim_dates):
            next_date_str = sim_dates[idx_d + 1] if (idx_d + 1) < len(sim_dates) else None
            self.view.set_date(date_str)
            mkt_today = self.view.get_market_snapshot(date_str)
            mkt_idx = mkt_today.set_index("player_id").to_dict("index")

            # ==================================================================
            # PHASE A: Morning 06:00 Settlement (Bonuses, Auctions, Offers, Rival Steals)
            # ==================================================================
            # A1. Official Matchday Bonus Payouts (`Primas_Jornadas` dates)
            for jn_pay in self.payout_date_to_rounds.get(date_str, []):
                if jn_pay not in self.settled_rounds and jn_pay in self.locked_round_lineups:
                    res_j = self._score_locked_round(jn_pay)
                    self.settled_rounds[jn_pay] = res_j
                    if res_j["bonus_eur"] > 0:
                        self.cash += float(res_j["bonus_eur"])
                        self.bonuses_earned_eur += int(res_j["bonus_eur"])

            # A2. Resolve Pending Bids placed yesterday ($t-1$)
            if self.pending_bids:
                for b in self.pending_bids:
                    self.bid_attempts += 1
                    pid = int(b["player_id"])
                    b_amt = int(b["amount"])
                    seller = str(b.get("seller", "Mercado"))
                    ask = int(b.get("ask_price", b_amt))

                    if pid in self.squad or len(self.squad) >= self.max_squad_size:
                        continue

                    curr_squad_val = sum(
                        float(mkt_idx.get(p, {}).get("precio", st.purchase_price))
                        for p, st in self.squad.items()
                    )
                    max_allowed_now = self.cash + (self.credit_limit_ratio if enable_leverage else 0.0) * curr_squad_val
                    if b_amt > max_allowed_now:
                        continue

                    rival_win_bid = self.max_rival_bid_by_res_date.get((date_str, pid), 0)
                    won = False
                    if rival_win_bid > 0:
                        won = (b_amt > rival_win_bid)
                    else:
                        if seller == "Mercado":
                            won = (b_amt >= ask)
                        else:
                            vm_now = float(mkt_idx.get(pid, {}).get("precio", ask))
                            won = (b_amt >= max(ask, int(vm_now * 0.98)))

                    if won:
                        self.bid_wins += 1
                        self.cash -= float(b_amt)
                        p_info = mkt_idx.get(pid, self.player_meta.get(pid, {}))
                        self.squad[pid] = OwnedPlayerState(
                            player_id=pid,
                            nombre=str(p_info.get("nombre", b.get("nombre", f"ID {pid}"))),
                            posicion=str(p_info.get("posicion", "Centrocampista")),
                            acquired_date=date_str,
                            purchase_price=b_amt,
                            extra_shield=0,
                        )
                        self.trade_log.append({
                            "date": date_str,
                            "type": "AUCTION_BUY",
                            "player_id": pid,
                            "nombre": self.squad[pid].nombre,
                            "price": b_amt,
                            "market_price": int(mkt_idx.get(pid, {}).get("precio", b_amt)),
                            "rival_max_bid": rival_win_bid,
                            "regime": b.get("regime", ""),
                        })
                self.pending_bids = []

            # A3. Incoming Rival Clause Attacks (if a real rival claused a player we own & can afford our clause!)
            for atk in self.rival_clause_attacks_by_day.get(date_str, []):
                pid = int(atk["player_id"])
                if pid in self.squad:
                    st = self.squad[pid]
                    days_held = (pd.Timestamp(date_str) - pd.Timestamp(st.acquired_date)).days
                    if days_held >= 5:
                        vm_now = float(mkt_idx.get(pid, {}).get("precio", st.purchase_price))
                        our_clause = self._get_player_clause(st, vm_now)
                        if int(atk["paid_clause"]) >= our_clause:
                            self.cash += float(our_clause)
                            self.clause_steals_suffered += 1
                            self.recent_sold_dates[pid] = date_str
                            self.trade_log.append({
                                "date": date_str,
                                "type": "CLAUSE_STOLEN_BY_RIVAL",
                                "player_id": pid,
                                "nombre": st.nombre,
                                "price": our_clause,
                                "market_price": int(vm_now),
                                "pnl": our_clause - st.purchase_price,
                            })
                            del self.squad[pid]
                            self.active_offers.pop(pid, None)
                            self.listed_pids.discard(pid)

            # A4. Refresh Computer (`Mercado`) Offers on Listed Players
            # Purge expired offers or offers for players no longer owned
            valid_offers: Dict[int, Tuple[int, str]] = {}
            for pid, (o_amt, exp_d) in self.active_offers.items():
                if pid in self.squad and exp_d >= date_str:
                    valid_offers[pid] = (o_amt, exp_d)
            self.active_offers = valid_offers

            exp_tomorrow = (pd.Timestamp(date_str) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            for pid in list(self.listed_pids):
                if pid not in self.squad:
                    continue
                if (date_str, pid) in self.observed_offers_by_date:
                    off_eur = self.observed_offers_by_date[(date_str, pid)]
                else:
                    vm_now = float(mkt_idx.get(pid, {}).get("precio", self.squad[pid].purchase_price))
                    mult = float(self.rng.uniform(0.95, 1.05))
                    off_eur = max(150_000, int(round((vm_now * mult) / 10_000.0) * 10_000))
                # Keep the best valid active offer if Biwenger 48h offer window overlaps
                prev_amt = self.active_offers.get(pid, (0, ""))[0]
                if off_eur >= prev_amt:
                    self.active_offers[pid] = (off_eur, exp_tomorrow)

            # ==================================================================
            # PHASE B: Daytime Strategy Decision (`09:00..17:59`, Strictly Causal!)
            # ==================================================================
            upcoming_rounds = [
                (jn, kd) for jn, kd in self.round_to_kickoff_date.items() if kd >= date_str
            ]
            upcoming_rounds.sort(key=lambda x: x[1])
            target_jn, next_kd = upcoming_rounds[0] if upcoming_rounds else (11, date_str)
            is_kickoff_day = (date_str in self.kickoff_date_to_round)
            active_kickoff_jn = self.kickoff_date_to_round.get(date_str, target_jn)
            days_to_kickoff = max(0, (pd.Timestamp(next_kd) - pd.Timestamp(date_str)).days)

            past_reports = self.view.get_past_match_reports()
            cal_causal = self.view.get_calendar_causal()
            comuniate_df = self.view.get_comuniate_for_round(active_kickoff_jn if is_kickoff_day else target_jn)

            xp_table = self.xp_engine.compute_xp_table(
                df_market_today=mkt_today,
                df_reports_past=past_reports,
                df_calendar=cal_causal,
                target_jornada_num=(active_kickoff_jn if is_kickoff_day else target_jn),
                df_comuniate_round=comuniate_df,
            )
            efr_map, shock_map = self.efr_predictor.predict_efr_72h_and_shocks()

            squad_df = self._build_squad_df(mkt_idx)

            if passive_lineup_only:
                # Variant 1: Passive Buy-and-Hold + Optimal MILP Starting XI (0 trades)
                milp_xi = opt.solve_joint_lineup_and_solvency_milp(
                    squad_df=squad_df,
                    xp_table=xp_table,
                    efr_map=efr_map,
                    current_cash=self.cash,
                    must_be_solvent=False,
                )
                lineup_plan = {
                    "formation": milp_xi["formation"],
                    "slots": milp_xi["lineup_slots"],
                }
            else:
                # Fit causal auction model on past resolved auctions + today's manager power
                past_bids = self.view.get_past_bids(date_str)
                mgrs_today = self.view.get_manager_snapshots(date_str)
                opt.auction_model.fit_causal(past_bids, mgrs_today)

                rivals_today = mgrs_today[mgrs_today["manager"] != self.target_manager] if not mgrs_today.empty else pd.DataFrame()
                indebted_rivals: Set[str] = set()
                league_med_max_bid = 25_000_000.0
                max_rival_power = 30_000_000.0
                if not rivals_today.empty:
                    saldos = pd.to_numeric(rivals_today["saldo_disponible_exacto"], errors="coerce").fillna(0.0)
                    indebted_rivals = set(rivals_today.loc[saldos < 0, "manager"].astype(str).tolist())
                    mp = pd.to_numeric(rivals_today["max_puja_disponible"], errors="coerce").dropna()
                    if len(mp) > 0:
                        league_med_max_bid = float(mp.median())
                        max_rival_power = float(mp.max())

                # 24h post-purchase sale lock
                sell_locked_ids = {
                    pid for pid, st in self.squad.items()
                    if (pd.Timestamp(date_str) - pd.Timestamp(st.acquired_date)).days < 1
                }

                # 5-day post-purchase clause lock across league + recently sold cooldown
                clause_locked_ids: Set[int] = set()
                for lookback_i in range(5):
                    d_lb = (pd.Timestamp(date_str) - pd.Timedelta(days=lookback_i)).strftime("%Y-%m-%d")
                    for rpid in self.rival_acquisitions_by_day.get(d_lb, []):
                        clause_locked_ids.add(rpid)
                for spid, sdate in self.recent_sold_dates.items():
                    if (pd.Timestamp(date_str) - pd.Timestamp(sdate)).days < 7:
                        clause_locked_ids.add(spid)

                listings_df = self._build_daily_market_listings(date_str, next_date_str, mkt_today)
                offer_price_map = {
                    pid: min(amt, round(float(mkt_idx.get(pid, {}).get("precio", amt)) * 1.05, -4))
                    for pid, (amt, _) in self.active_offers.items()
                }

                plan = opt.generate_daily_plan(
                    current_date=date_str,
                    my_manager_name=self.target_manager,
                    current_cash=self.cash,
                    squad_df=squad_df,
                    df_market_all=mkt_today,
                    market_listings_df=listings_df,
                    xp_table=xp_table,
                    efr_map=efr_map,
                    active_offers_map=offer_price_map,
                    sell_locked_ids=sell_locked_ids,
                    clause_locked_ids=clause_locked_ids,
                    is_kickoff_day=is_kickoff_day,
                    days_to_kickoff=days_to_kickoff,
                    recent_match_shock_map=shock_map,
                    indebted_rival_managers=indebted_rivals,
                    league_median_max_bid=league_med_max_bid,
                    max_rival_purchasing_power=max_rival_power,
                )

                # B1. Execute Immediate Sales (Accepting firm Computer offers)
                for s_act in plan["all_sales"]:
                    pid = int(s_act["player_id"])
                    if pid not in self.squad or pid in sell_locked_ids:
                        continue
                    if pid in self.active_offers:
                        # Conservative: a stale offer can never be cashed above +5% of TODAY's value
                        vm_cap = int(round(float(mkt_idx.get(pid, {}).get("precio", 0)) * 1.05 / 10_000.0) * 10_000)
                        proceeds = int(min(self.active_offers[pid][0], vm_cap)) if vm_cap > 0 else int(self.active_offers[pid][0])
                    else:
                        continue
                    st = self.squad[pid]
                    self.cash += float(proceeds)
                    self.recent_sold_dates[pid] = date_str
                    self.trade_log.append({
                        "date": date_str,
                        "type": "MARKET_SALE",
                        "player_id": pid,
                        "nombre": st.nombre,
                        "price": proceeds,
                        "market_price": int(mkt_idx.get(pid, {}).get("precio", proceeds)),
                        "pnl": proceeds - st.purchase_price,
                        "reason": s_act.get("reason", ""),
                    })
                    del self.squad[pid]
                    self.active_offers.pop(pid, None)
                    self.listed_pids.discard(pid)

                # B2. Execute Instant Offensive Clause Snipes ("A Machete")
                claused_today = False
                for c_act in plan["clause_snipes"]:
                    pid = int(c_act["player_id"])
                    c_amt = int(c_act["amount"])
                    if pid in self.squad or pid in clause_locked_ids or len(self.squad) >= self.max_squad_size:
                        continue
                    curr_sq_val = sum(
                        float(mkt_idx.get(p, {}).get("precio", st.purchase_price))
                        for p, st in self.squad.items()
                    )
                    max_cred = self.cash + (self.credit_limit_ratio if enable_leverage else 0.0) * curr_sq_val
                    if c_amt > max_cred:
                        continue
                    if is_kickoff_day and (self.cash - c_amt) < 0:
                        continue

                    self.cash -= float(c_amt)
                    self.clause_snipes_executed += 1
                    claused_today = True
                    p_info = mkt_idx.get(pid, self.player_meta.get(pid, {}))
                    self.squad[pid] = OwnedPlayerState(
                        player_id=pid,
                        nombre=str(p_info.get("nombre", c_act.get("nombre", f"ID {pid}"))),
                        posicion=str(p_info.get("posicion", "Centrocampista")),
                        acquired_date=date_str,
                        purchase_price=c_amt,
                        extra_shield=0,
                    )
                    self.trade_log.append({
                        "date": date_str,
                        "type": "CLAUSE_SNIPE_BUY",
                        "player_id": pid,
                        "nombre": self.squad[pid].nombre,
                        "price": c_amt,
                        "market_price": int(mkt_idx.get(pid, {}).get("precio", c_amt)),
                        "victim": c_act.get("seller", ""),
                        "regime": c_act.get("regime", ""),
                    })

                # B3. Execute Defensive Clause Shields
                for sh_act in plan["defensive_shields"]:
                    pid = int(sh_act["player_id"])
                    spend = int(sh_act["recommended_cash_spend"])
                    if pid in self.squad and spend > 0 and (self.cash - spend) >= 0:
                        self.cash -= float(spend)
                        self.squad[pid].extra_shield += spend
                        self.shields_spent_eur += spend
                        self.trade_log.append({
                            "date": date_str,
                            "type": "DEFENSIVE_SHIELD",
                            "player_id": pid,
                            "nombre": self.squad[pid].nombre,
                            "price": spend,
                            "market_price": int(mkt_idx.get(pid, {}).get("precio", 0)),
                        })

                # B4. Queue Sealed Market Bids for Tomorrow 06:00 Clearing
                self.pending_bids = list(plan["market_bids"])

                # B5. Update Preventive Market Listings (0€ cost to receive tomorrow's +-5% offers)
                self.listed_pids = {int(x["player_id"]) for x in plan["preventive_listings"]}

                # Re-solve XI if we instant-sniped a player on kickoff day so he enters tonight's XI!
                if claused_today and is_kickoff_day:
                    upd_sq_df = self._build_squad_df(mkt_idx)
                    upd_milp = opt.solve_joint_lineup_and_solvency_milp(
                        squad_df=upd_sq_df,
                        xp_table=xp_table,
                        efr_map=efr_map,
                        current_cash=self.cash,
                        must_be_solvent=False,
                    )
                    lineup_plan = {
                        "formation": upd_milp["formation"],
                        "slots": upd_milp["lineup_slots"],
                    }
                else:
                    lineup_plan = plan["lineup"]

            # ==================================================================
            # PHASE C: Evening Primary Kickoff Lock (`19:00..21:00`) & Daily Valuation
            # ==================================================================
            if is_kickoff_day:
                jn_lock = self.kickoff_date_to_round[date_str]
                if 1 <= jn_lock <= 10:
                    self.locked_round_lineups[jn_lock] = {
                        "jornada_num": jn_lock,
                        "kickoff_date": date_str,
                        "formation": lineup_plan["formation"],
                        "slots": list(lineup_plan["slots"]),
                        "is_solvent": (self.cash >= -0.5),
                        "cash_at_kickoff": round(self.cash),
                        "owned_at_kickoff": set(self.squad.keys()),
                    }

            eod_squad_val = sum(
                float(mkt_idx.get(p, {}).get("precio", st.purchase_price))
                for p, st in self.squad.items()
            )
            cum_pts = sum(r["points"] for r in self.settled_rounds.values())
            self.daily_curve.append({
                "fecha": date_str,
                "cash": round(self.cash),
                "squad_value": round(eod_squad_val),
                "net_worth": round(self.cash + eod_squad_val),
                "num_players": len(self.squad),
                "settled_points": int(cum_pts),
                "is_kickoff_day": is_kickoff_day,
            })

        # Ensure all J1..J10 rounds locked during simulation are scored
        for jn in range(1, 11):
            if jn not in self.settled_rounds and jn in self.locked_round_lineups:
                self.settled_rounds[jn] = self._score_locked_round(jn)

        total_pts_j1_j10 = sum(self.settled_rounds[jn]["points"] for jn in range(1, 11) if jn in self.settled_rounds)
        total_opt_j1_j10 = sum(self.settled_rounds[jn]["optimal_points"] for jn in range(1, 11) if jn in self.settled_rounds)
        insolvent_rounds = sum(1 for r in self.settled_rounds.values() if not r["is_solvent"])
        lineup_eff_pct = round((total_pts_j1_j10 / total_opt_j1_j10 * 100.0), 2) if total_opt_j1_j10 > 0 else 100.0

        final_day = self.daily_curve[-1] if self.daily_curve else {"cash": 0, "squad_value": 0, "net_worth": 0}
        # Pure trading alpha = Final Net Worth + Shields Spent - Initial Net Worth (36,410,000 €) - Matchday Bonuses
        initial_net_worth = 36_410_000
        trading_alpha_eur = int(final_day["net_worth"] + self.shields_spent_eur - initial_net_worth - self.bonuses_earned_eur)

        return {
            "total_points_j1_j10": int(total_pts_j1_j10),
            "optimal_points_j1_j10": int(total_opt_j1_j10),
            "lineup_efficiency_pct": lineup_eff_pct,
            "insolvent_Zero_rounds": insolvent_rounds,
            "final_cash": int(final_day["cash"]),
            "final_squad_value": int(final_day["squad_value"]),
            "final_net_worth": int(final_day["net_worth"]),
            "bonuses_earned_eur": int(self.bonuses_earned_eur),
            "shields_spent_eur": int(self.shields_spent_eur),
            "trading_alpha_eur": int(trading_alpha_eur),
            "bid_attempts": int(self.bid_attempts),
            "bid_wins": int(self.bid_wins),
            "bid_win_rate_pct": round((self.bid_wins / self.bid_attempts * 100.0), 1) if self.bid_attempts > 0 else 0.0,
            "clause_snipes_executed": int(self.clause_snipes_executed),
            "clause_steals_suffered": int(self.clause_steals_suffered),
            "total_sales": sum(1 for t in self.trade_log if t["type"] == "MARKET_SALE"),
            "round_breakdown": self.settled_rounds,
            "daily_curve": pd.DataFrame(self.daily_curve),
            "trade_log": pd.DataFrame(self.trade_log),
        }
