"""
Honest 94-Day Historical Market & League Simulator (`AZFinanceMarketEnv` v3)
============================================================================
Reproduces any Biwenger league (defaulting dynamically to active user/league via
`resolve_active_user_identity()` and `LeagueRulesEngine`) with **100% official rules parity**:
- Official `marketMaxUserSales` enforcement (`<= 5` simultaneous players listed on market!)
- Official `clauseRoundDisabledHours` enforcement (`48h` pre-kickoff freeze + in-round freeze!)
- Official `clauseActivationDelay` (`5`-day post-purchase clause immunity) & `1:1` shield ratio
- Official `daysForSale` (`24h` post-purchase sale lock) & `25%` credit line
- Exact `scoreID=5` points, multiposition out-of-position goal bonuses, and matchday bonuses
- Supports both `v3` (`PortfolioOptimizer` v3 + `StrategyMemoryLedger` + `ReplacementCost` NPV)
  and `legacy_v2_audit` mode (testing legacy naive stop-loss / unprioritized 5-slot truncation
  under real honest rules for transparent before/after comparison).
"""

import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Any, Optional, Set, Tuple
import numpy as np
import pandas as pd

from src.config import resolve_active_user_identity
from src.tools.data_extraction.warehouse import compute_official_clause
from src.tools.strategy_engine.league_rules import LeagueRulesEngine, RoundPhaseContext
from src.tools.strategy_engine.xp_model import (
    ExpectedPointsEngine,
    GOAL_BONUS_BY_LINE,
    normalize_position,
)
from src.tools.strategy_engine.strategy_memory import StrategyMemoryLedger
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
    Deterministic / Seeded Monte-Carlo Market Simulator with 100% Official Biwenger Rule Parity.
    """

    def __init__(
        self,
        db_path: str = "./data/warehouse/biwenger_history.db",
        target_manager: Optional[str] = None,
        target_user_id: Optional[int] = None,
        market_mode: str = "monte_carlo_20slot",
        seed: int = 42,
        credit_limit_ratio: Optional[float] = None,
        max_squad_size: int = 21,
        rules: Optional[LeagueRulesEngine] = None,
    ):
        ident = resolve_active_user_identity()
        self.rules = rules or LeagueRulesEngine.load()
        self.db_path = db_path
        self.target_manager = str(target_manager or ident.get("team_name") or "Dani SR")
        self.target_user_id = int(target_user_id or ident.get("team_id") or 0)
        self.market_mode = market_mode  # "strict_observed" or "monte_carlo_20slot"
        self.seed = int(seed)
        self.rng = np.random.default_rng(self.seed)
        self.credit_limit_ratio = (
            float(credit_limit_ratio) if credit_limit_ratio is not None else float(self.rules.credit_limit_ratio)
        )
        self.max_squad_size = min(int(max_squad_size), int(self.rules.team_max_size))

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
        before settlement timestamps).
        """
        # Determine officially settled rounds present in `Primas_Jornadas` (e.g. J1..J10)
        self.payout_date_to_rounds: Dict[str, List[int]] = {}
        self.rival_round_points: Dict[int, Dict[str, int]] = {}
        settled_j_set: Set[int] = set()
        for _, r in self._df_primas.iterrows():
            j_label = str(r.get("Jornada", ""))
            digits = "".join(ch for ch in j_label if ch.isdigit())
            if not digits:
                continue
            jn = int(digits)
            settled_j_set.add(jn)
            p_date = str(r.get("Fecha", ""))[:10]
            mgr = str(r.get("Manager", "")).strip()
            pts = int(safe_num(r.get("Puntos", 0), 0))
            if mgr != self.target_manager:
                self.rival_round_points.setdefault(jn, {})[mgr] = pts
            if p_date and jn not in self.payout_date_to_rounds.get(p_date, []):
                self.payout_date_to_rounds.setdefault(p_date, []).append(jn)

        self.settled_jornada_nums: List[int] = sorted(settled_j_set) if settled_j_set else list(range(1, 11))
        self.max_settled_jornada: int = max(self.settled_jornada_nums)

        # Primary kickoff dates (`part == 1`) for J1..J12
        self.kickoff_date_to_round: Dict[str, int] = {}
        self.round_to_kickoff_date: Dict[int, str] = {}
        cal_p1 = self.view._df_calendar[self.view._df_calendar["part_int"] == 1]
        for jn, grp in cal_p1.groupby("jornada_num_int"):
            jn_int = int(jn)
            if 1 <= jn_int <= self.max_settled_jornada + 2:
                valid_dts = sorted([s[:10] for s in grp["fecha_hora"].tolist() if len(s) >= 10])
                if valid_dts:
                    kd = valid_dts[0]
                    self.round_to_kickoff_date[jn_int] = kd
                    self.kickoff_date_to_round[kd] = jn_int

        # Exact `scoreID=5` Ground-Truth Player Points per Round (J1..J11)
        pts_piv_dates = [
            "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-09",
            "2026-09-11", "2026-09-15", "2026-09-18", "2026-09-22",
            "2026-10-09", "2026-10-14", "2026-10-16", "2026-10-20",
            "2026-10-21", "2026-10-23", "2026-10-28", "2026-10-30", "2026-11-02",
        ]
        sub_hc = self.view._df_market[self.view._df_market["fecha"].isin(pts_piv_dates)].copy()
        sub_hc["pts_tot"] = pd.to_numeric(sub_hc["puntos_totales"], errors="coerce").fillna(0).astype(int)
        piv = sub_hc.pivot_table(index="player_id", columns="fecha", values="pts_tot", aggfunc="last").fillna(0).astype(int)

        self.ground_truth_round_pts: Dict[int, Dict[int, int]] = {jn: {} for jn in range(1, 12)}
        self.ground_truth_goals: Dict[int, Dict[int, int]] = {jn: {} for jn in range(1, 12)}
        self.ground_truth_awards: Dict[int, Dict[int, Tuple[int, int, int]]] = {jn: {} for jn in range(1, 12)}

        rep_all = self.view._df_reports
        for _, rr in rep_all.iterrows():
            jn = int(rr["jornada_num_int"])
            if not (1 <= jn <= 11):
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

        delta_windows = {
            4: [("2026-09-04", "2026-09-09")],
            5: [("2026-09-11", "2026-09-15")],
            6: [("2026-09-03", "2026-09-04"), ("2026-09-15", "2026-09-18"), ("2026-10-21", "2026-10-23")],
            7: [("2026-09-18", "2026-09-22")],
            8: [("2026-10-09", "2026-10-14")],
            9: [("2026-10-16", "2026-10-20")],
            10: [("2026-10-23", "2026-10-28")],
            11: [("2026-10-30", "2026-11-02")],
        }
        for jn, wins in delta_windows.items():
            s = pd.Series(0, index=piv.index, dtype=int)
            for d0, d1 in wins:
                if d0 in piv.columns and d1 in piv.columns:
                    s = s + (piv[d1] - piv[d0])
            self.ground_truth_round_pts[jn] = s.to_dict()

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

        latest_mkt = self.view._df_market.drop_duplicates(subset=["player_id"], keep="last")
        self.player_meta: Dict[int, Dict[str, str]] = {}
        for _, r in latest_mkt.iterrows():
            pid = int(r["player_id"])
            self.player_meta[pid] = {
                "nombre": str(r.get("nombre", f"ID {pid}")),
                "posicion": str(r.get("posicion", "")),
                "natural_pos": normalize_position(r.get("posicion", "")),
            }

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

        self._df_mkt_hist["fecha_d"] = self._df_mkt_hist["fecha"].astype(str).str.slice(0, 10)
        self._df_mkt_hist["pid_int"] = pd.to_numeric(self._df_mkt_hist["player_id"], errors="coerce").fillna(0).astype(int)

        self.en_venta_by_date: Dict[str, pd.DataFrame] = {}
        ev = self._df_mkt_hist[self._df_mkt_hist["tipo_registro"] == "En Venta Mercado"]
        for d, grp in ev.groupby("fecha_d"):
            self.en_venta_by_date[str(d)] = grp.copy()

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
        """Resets portfolio to `target_manager`'s exact `2026-08-01` initial draw and cash."""
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
        init_sq_val = 0
        for pid in init_pids:
            p_info = mkt_d1.get(pid, self.player_meta.get(pid, {}))
            vm = int(safe_num(p_info.get("precio", 150_000), 150_000))
            init_sq_val += vm
            self.squad[pid] = OwnedPlayerState(
                player_id=pid,
                nombre=str(p_info.get("nombre", f"ID {pid}")),
                posicion=str(p_info.get("posicion", "Centrocampista")),
                acquired_date="2026-07-25",
                purchase_price=vm,
                extra_shield=0,
            )
        self.initial_net_worth: int = int(round(self.cash + init_sq_val))

        self.pending_bids: List[Dict[str, Any]] = []
        self.listed_pids: Set[int] = set()
        self.active_offers: Dict[int, Tuple[int, str]] = {}
        self.recent_sold_dates: Dict[int, str] = {}

        self.locked_round_lineups: Dict[int, Dict[str, Any]] = {}
        self.settled_rounds: Dict[int, Dict[str, Any]] = {}

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
        return int(self.rules.compute_official_clause(base, float(st.extra_shield)))

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
                "precio_compra": st.purchase_price,
            })
        return pd.DataFrame(rows)

    def _build_daily_market_listings(self, date_str: str, next_date_str: Optional[str], mkt_today: pd.DataFrame) -> pd.DataFrame:
        mkt_idx = mkt_today.set_index("player_id").to_dict("index")
        listings: List[Dict[str, Any]] = []
        seen_pids: Set[int] = set()
        k_free_slots = max(5, int(self.rules.market_free_slots))

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

        if self.market_mode == "monte_carlo_20slot" and n_mercado_slots < k_free_slots:
            free_pool = mkt_today[
                (mkt_today["propietario_liga"].astype(str).isin(["Libre", "Mercado", ""]))
                & (~mkt_today["player_id"].isin(seen_pids | set(self.squad.keys())))
                & (mkt_today["precio"] > 0)
            ]
            need = min(k_free_slots - n_mercado_slots, len(free_pool))
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
        lock = self.locked_round_lineups.get(jornada_num)
        if not lock:
            return {"points": 0, "optimal_points": 0, "rank": 15, "bonus_eur": 0, "is_solvent": True}

        pts_map = self.ground_truth_round_pts.get(jornada_num, {})
        gls_map = self.ground_truth_goals.get(jornada_num, {})
        awd_map = self.ground_truth_awards.get(jornada_num, {})

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

        rivals = self.rival_round_points.get(jornada_num, {})
        higher_rivals = sum(1 for r_pts in rivals.values() if r_pts > total_pts)
        rank = 1 + higher_rivals

        if total_pts <= 0 or jornada_num > self.max_settled_jornada:
            total_bonus = 0
        else:
            prima_pts = total_pts * int(self.rules.bonus_per_point_eur)
            prima_pos = int(round(prima_pts * 0.06 * (rank - 3))) if (4 <= rank <= 14) else 0
            prima_awards = (
                mvp_m_cnt * int(self.rules.bonus_game_mvp_eur)
                + mvp_j_cnt * int(self.rules.bonus_round_mvp_eur)
                + ideal_cnt * int(self.rules.bonus_ideal_lineup_eur)
            )
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

    def _apply_legacy_v2_decisions(
        self,
        opt: PortfolioOptimizer,
        date_str: str,
        squad_df: pd.DataFrame,
        mkt_today: pd.DataFrame,
        listings_df: pd.DataFrame,
        xp_table: pd.DataFrame,
        efr_map: Dict[int, float],
        offer_price_map: Dict[int, float],
        sell_locked_ids: Set[int],
        clause_locked_ids: Set[int],
        is_kickoff_day: bool,
        days_to_kickoff: int,
        clauses_open: bool,
        shock_map: Dict[int, float],
        indebted_rivals: Set[str],
        league_med_max_bid: float,
        max_rival_power: float,
    ) -> Dict[str, Any]:
        """
        Simulates the legacy `v2` behaviors under HONEST official rules (`<=5` listing slots + `48h` clause lock):
          - Naive toxic stop-loss (sells ANY injured/falling player >= 1.2M without replacement-cost NPV!)
          - Aggressive indiscriminate clause policy (ignores free-market scarcity)
          - Unranked arbitrary 5-slot listing truncation (lists first 5 unlocked players by ID instead of Top-5 priority!)
          - Zero multi-day offer-roll patience.
        """
        old_policy = opt.clause_sniper.policy
        opt.clause_sniper.policy = "AGGRESSIVE" if opt.clause_sniper.policy != "DISABLED" else "DISABLED"

        # Run plan without memory ledger
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
            clauses_open=clauses_open,
            recent_match_shock_map=shock_map,
            indebted_rival_managers=indebted_rivals,
            league_median_max_bid=league_med_max_bid,
            max_rival_purchasing_power=max_rival_power,
            memory_ledger=None,
        )

        # Override sales & listings with legacy v2 naive toxic stop-loss + arbitrary first-5 truncation!
        xp_idx = xp_table.set_index("player_id").to_dict("index") if not xp_table.empty else {}
        sold_already = {int(s["player_id"]) for s in plan["all_sales"]}
        extra_naive_sales = []
        for _, r in squad_df.iterrows():
            pid = int(r["player_id"])
            if pid in sold_already or pid in sell_locked_ids or pid not in offer_price_map:
                continue
            price = safe_num(r.get("precio", 0), 0.0)
            efr_v = float(efr_map.get(pid, 0.0))
            is_av = bool(xp_idx.get(pid, {}).get("is_available", True))
            if (not is_av and price >= 1_200_000 and efr_v <= -20_000) or (efr_v <= -120_000):
                extra_naive_sales.append({
                    "player_id": pid,
                    "nombre": str(r.get("nombre", "")),
                    "sale_price": int(round(offer_price_map[pid])),
                    "reason": "LEGACY_V2_NAIVE_STOP_LOSS",
                })
                sold_already.add(pid)
        plan["all_sales"] = plan["all_sales"] + extra_naive_sales

        # Legacy v2 listed ALL players -> Biwenger server accepts ONLY the first 5 (`marketMaxUserSales=5`)!
        naive_listings = []
        for _, r in squad_df.sort_values("player_id").iterrows():
            pid = int(r["player_id"])
            if pid in sold_already or pid in sell_locked_ids:
                continue
            naive_listings.append({
                "player_id": pid,
                "nombre": str(r.get("nombre", "")),
                "asking_price": int(round(safe_num(r.get("precio", 150_000), 150_000.0) * 1.05, -4)),
            })
            if len(naive_listings) >= int(self.rules.market_max_user_sales):
                break
        plan["preventive_listings"] = naive_listings
        opt.clause_sniper.policy = old_policy
        return plan

    def run_simulation(
        self,
        optimizer: Optional[PortfolioOptimizer] = None,
        passive_lineup_only: bool = False,
        enable_leverage: bool = True,
        enable_clauses: bool = True,
        enable_preventive_listing: bool = True,
        clause_policy: str = "SURGICAL_SCARCITY",
        legacy_v2_mode: bool = False,
        start_date: str = "2026-08-01",
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes the full day-by-day simulation under 100% honest official Biwenger rules
        (`<= 5` market listing slots, `48h` clause lock + active-round freeze, `1:1` shields).
        """
        self.reset()
        max_avail_date = self.view.all_dates[-1] if self.view.all_dates else "2026-11-02"
        eff_end_date = end_date or max_avail_date

        mem_ledger = StrategyMemoryLedger(in_memory_only=True)
        opt = optimizer or PortfolioOptimizer(
            xp_engine=self.xp_engine,
            credit_limit_ratio=self.credit_limit_ratio if enable_leverage else 0.0,
            max_squad_size=self.max_squad_size,
            preventive_listing=enable_preventive_listing,
            rules=self.rules,
        )
        opt.credit_limit_ratio = self.credit_limit_ratio if enable_leverage else 0.0
        opt.preventive_listing = enable_preventive_listing
        opt.clause_sniper.policy = (clause_policy if enable_clauses else "DISABLED")

        sim_dates = [d for d in self.view.all_dates if start_date <= d <= eff_end_date]
        max_sales_slots = int(self.rules.market_max_user_sales)
        clause_delay_days = int(self.rules.clause_activation_delay_days)

        for idx_d, date_str in enumerate(sim_dates):
            next_date_str = sim_dates[idx_d + 1] if (idx_d + 1) < len(sim_dates) else None
            self.view.set_date(date_str)
            mkt_today = self.view.get_market_snapshot(date_str)
            mkt_idx = mkt_today.set_index("player_id").to_dict("index")

            # Resolve exact calendar phase at 12:00 daytime decision hour (`48h` clause freeze & active round!)
            phase_ctx: RoundPhaseContext = self.rules.compute_round_phase(
                now_ts=pd.Timestamp(f"{date_str} 12:00:00"),
                df_calendar=self.view._df_calendar,
            )
            is_kickoff_day = (date_str in self.kickoff_date_to_round)
            clauses_open_today = bool(phase_ctx.clauses_open) and (not is_kickoff_day)

            # ==================================================================
            # PHASE A: Morning 06:00 Settlement
            # ==================================================================
            for jn_pay in self.payout_date_to_rounds.get(date_str, []):
                if jn_pay not in self.settled_rounds and jn_pay in self.locked_round_lineups:
                    res_j = self._score_locked_round(jn_pay)
                    self.settled_rounds[jn_pay] = res_j
                    if res_j["bonus_eur"] > 0:
                        self.cash += float(res_j["bonus_eur"])
                        self.bonuses_earned_eur += int(res_j["bonus_eur"])

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

            # Incoming rival clause steals (only possible when clauses are open & >5d lock!)
            if clauses_open_today:
                for atk in self.rival_clause_attacks_by_day.get(date_str, []):
                    pid = int(atk["player_id"])
                    if pid in self.squad:
                        st = self.squad[pid]
                        days_held = (pd.Timestamp(date_str) - pd.Timestamp(st.acquired_date)).days
                        if days_held >= clause_delay_days:
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

            # Refresh Computer (`Mercado`) offers ONLY on our `<= 5` listed players!
            valid_offers: Dict[int, Tuple[int, str]] = {}
            for pid, (o_amt, exp_d) in self.active_offers.items():
                if pid in self.squad and exp_d >= date_str:
                    valid_offers[pid] = (o_amt, exp_d)
            self.active_offers = valid_offers

            exp_tomorrow = (pd.Timestamp(date_str) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            for pid in sorted(self.listed_pids)[:max_sales_slots]:
                if pid not in self.squad:
                    continue
                if (date_str, pid) in self.observed_offers_by_date:
                    off_eur = self.observed_offers_by_date[(date_str, pid)]
                else:
                    vm_now = float(mkt_idx.get(pid, {}).get("precio", self.squad[pid].purchase_price))
                    mult = float(self.rng.uniform(0.95, 1.05))
                    off_eur = max(150_000, int(round((vm_now * mult) / 10_000.0) * 10_000))
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
            target_jn, next_kd = upcoming_rounds[0] if upcoming_rounds else (12, date_str)
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
            scarcity_free_5m = int(
                ((mkt_today["precio"] >= 5_000_000) & (mkt_today["propietario_liga"].astype(str).isin(["Libre", "Mercado", ""]))).sum()
            )
            scarcity_free_10m = int(
                ((mkt_today["precio"] >= 10_000_000) & (mkt_today["propietario_liga"].astype(str).isin(["Libre", "Mercado", ""]))).sum()
            )

            if passive_lineup_only:
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
                    mp_col = "max_puja_disponible" if "max_puja_disponible" in rivals_today.columns else "puja_maxima_permitida"
                    if mp_col in rivals_today.columns:
                        mp_s = pd.to_numeric(rivals_today[mp_col], errors="coerce").dropna()
                        if len(mp_s) > 0:
                            league_med_max_bid = float(mp_s.median())
                            max_rival_power = float(mp_s.max())

                sell_locked_ids = {
                    pid for pid, st in self.squad.items()
                    if (pd.Timestamp(date_str) - pd.Timestamp(st.acquired_date)).days < int(self.rules.days_for_sale)
                }

                clause_locked_ids: Set[int] = set()
                for lookback_i in range(clause_delay_days):
                    d_lb = (pd.Timestamp(date_str) - pd.Timedelta(days=lookback_i)).strftime("%Y-%m-%d")
                    for rpid in self.rival_acquisitions_by_day.get(d_lb, []):
                        clause_locked_ids.add(rpid)
                for spid, sdate in self.recent_sold_dates.items():
                    if (pd.Timestamp(date_str) - pd.Timestamp(sdate)).days < (clause_delay_days + 2):
                        clause_locked_ids.add(spid)

                listings_df = self._build_daily_market_listings(date_str, next_date_str, mkt_today)
                offer_price_map = {
                    pid: min(amt, round(float(mkt_idx.get(pid, {}).get("precio", amt)) * 1.05, -4))
                    for pid, (amt, _) in self.active_offers.items()
                }

                if legacy_v2_mode:
                    plan = self._apply_legacy_v2_decisions(
                        opt=opt,
                        date_str=date_str,
                        squad_df=squad_df,
                        mkt_today=mkt_today,
                        listings_df=listings_df,
                        xp_table=xp_table,
                        efr_map=efr_map,
                        offer_price_map=offer_price_map,
                        sell_locked_ids=sell_locked_ids,
                        clause_locked_ids=clause_locked_ids,
                        is_kickoff_day=is_kickoff_day,
                        days_to_kickoff=days_to_kickoff,
                        clauses_open=clauses_open_today,
                        shock_map=shock_map,
                        indebted_rivals=indebted_rivals,
                        league_med_max_bid=league_med_max_bid,
                        max_rival_power=max_rival_power,
                    )
                else:
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
                        clauses_open=clauses_open_today,
                        recent_match_shock_map=shock_map,
                        indebted_rival_managers=indebted_rivals,
                        league_median_max_bid=league_med_max_bid,
                        max_rival_purchasing_power=max_rival_power,
                        df_calendar=cal_causal,
                        memory_ledger=mem_ledger,
                    )

                # B1. Execute Immediate Sales (Accepting firm offers)
                for s_act in plan["all_sales"]:
                    pid = int(s_act["player_id"])
                    if pid not in self.squad or pid in sell_locked_ids:
                        continue
                    if pid in self.active_offers:
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

                # B2. Execute Instant Offensive Clause Snipes (STRICTLY ENFORCING 48h & Active-Round Freeze!)
                claused_today = False
                if clauses_open_today and enable_clauses:
                    for c_act in plan["clause_snipes"]:
                        pid = int(c_act["player_id"])
                        c_amt = int(c_act["amount"])
                        if pid in self.squad or pid in clause_locked_ids or len(self.squad) >= self.max_squad_size:
                            continue
                        if (self.cash - c_amt) < 0:
                            # Positive surplus discipline
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

                # B3. Execute Defensive Clause Shields (1:1 ratio, ONLY when `clauses_open_today`)
                if clauses_open_today and enable_clauses:
                    inc_ratio = float(self.rules.clause_increment_ratio)
                    for sh_act in plan["defensive_shields"]:
                        pid = int(sh_act["player_id"])
                        spend = int(sh_act["recommended_cash_spend"])
                        if pid in self.squad and spend > 0 and (self.cash - spend) >= 0:
                            self.cash -= float(spend)
                            self.squad[pid].extra_shield += int(round(spend * inc_ratio))
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

                # B5. Update Preventive Market Listings (STRICTLY CAPPED AT `marketMaxUserSales` = 5!)
                capped_listings = list(plan["preventive_listings"])[:max_sales_slots]
                self.listed_pids = {int(x["player_id"]) for x in capped_listings}

                if claused_today:
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
            # PHASE C: Evening Primary Kickoff Lock & Daily Telemetry
            # ==================================================================
            if is_kickoff_day:
                jn_lock = self.kickoff_date_to_round[date_str]
                if 1 <= jn_lock <= 11:
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
            cum_pts = sum(
                r["points"] for j_k, r in self.settled_rounds.items() if j_k in self.settled_jornada_nums
            )
            self.daily_curve.append({
                "fecha": date_str,
                "cash": round(self.cash),
                "squad_value": round(eod_squad_val),
                "net_worth": round(self.cash + eod_squad_val),
                "credit_limit_eur": round((self.credit_limit_ratio if enable_leverage else 0.0) * eod_squad_val),
                "num_players": len(self.squad),
                "listed_count": len(self.listed_pids),
                "clauses_open": clauses_open_today,
                "free_ge_5m": scarcity_free_5m,
                "free_ge_10m": scarcity_free_10m,
                "settled_points": int(cum_pts),
                "is_kickoff_day": is_kickoff_day,
            })

        # Ensure all officially settled rounds (`J1..J10`) + partial J11 (tracked separately) are scored
        for jn in range(1, 12):
            if jn not in self.settled_rounds and jn in self.locked_round_lineups:
                self.settled_rounds[jn] = self._score_locked_round(jn)

        # Official closed-round totals (`J1..J10` matching `Primas_Jornadas` 15-manager leaderboard!)
        total_pts_official = sum(
            self.settled_rounds[jn]["points"] for jn in self.settled_jornada_nums if jn in self.settled_rounds
        )
        total_opt_official = sum(
            self.settled_rounds[jn]["optimal_points"] for jn in self.settled_jornada_nums if jn in self.settled_rounds
        )
        j11_live_pts = self.settled_rounds.get(11, {}).get("points", 0)
        insolvent_rounds = sum(
            1 for jn, r in self.settled_rounds.items() if jn <= 11 and not r["is_solvent"]
        )
        lineup_eff_pct = round((total_pts_official / total_opt_official * 100.0), 2) if total_opt_official > 0 else 100.0

        # Out-of-time regime split points: J1..J5 (Abundance Aug-Sep 15) vs J6..J10 (+J11) (Scarcity Sep 16-Nov 02)
        pts_h1_abundance = sum(self.settled_rounds.get(jn, {}).get("points", 0) for jn in range(1, 6))
        pts_h2_scarcity = sum(self.settled_rounds.get(jn, {}).get("points", 0) for jn in range(6, 11))

        final_day = self.daily_curve[-1] if self.daily_curve else {"cash": 0, "squad_value": 0, "net_worth": 0}
        trading_alpha_eur = int(
            final_day["net_worth"] + self.shields_spent_eur - self.initial_net_worth - self.bonuses_earned_eur
        )

        return {
            "total_points_j1_j10": int(total_pts_official),
            "j11_partial_points": int(j11_live_pts),
            "total_points_incl_j11": int(total_pts_official + j11_live_pts),
            "points_h1_j1_j5": int(pts_h1_abundance),
            "points_h2_j6_j10": int(pts_h2_scarcity),
            "optimal_points_j1_j10": int(total_opt_official),
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
