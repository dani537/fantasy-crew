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

import inspect
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
from src.tools.strategy_engine.archetypes import StrategyArchetype
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
        equalize_starting_wealth: bool = True,
        offer_mult_range: Tuple[float, float] = (0.95, 1.05),
        overbid_inflation_factor: float = 1.0,
        injury_stress_mult: float = 1.0,
        aggressive_rival_clause_prob: float = 0.0,
        initial_manager_override: Optional[str] = None,
    ):
        ident = resolve_active_user_identity()
        self.rules = rules or LeagueRulesEngine.load()
        self.db_path = db_path
        self.base_target_manager = str(target_manager or ident.get("team_name") or "Dani SR")
        self.target_manager = str(initial_manager_override or self.base_target_manager)
        self.target_user_id = int(target_user_id or ident.get("team_id") or 0)
        self.market_mode = market_mode  # "strict_observed" or "monte_carlo_20slot"
        self.seed = int(seed)
        self.rng = np.random.default_rng(self.seed)
        self.credit_limit_ratio = (
            float(credit_limit_ratio) if credit_limit_ratio is not None else float(self.rules.credit_limit_ratio)
        )
        self.max_squad_size = min(int(max_squad_size), int(self.rules.team_max_size))
        self.equalize_starting_wealth = bool(equalize_starting_wealth)

        # Scenario stress hooks (default = 100% baseline honest simulation)
        self.offer_mult_range = (float(offer_mult_range[0]), float(offer_mult_range[1]))
        self.overbid_inflation_factor = float(overbid_inflation_factor)
        self.injury_stress_mult = float(injury_stress_mult)
        self.aggressive_rival_clause_prob = float(aggressive_rival_clause_prob)

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

        # Per-worker read-only causal caches (strictly keyed by date_str, invariant across seeds/strategies)
        self._daily_signal_cache: Dict[str, Tuple[pd.DataFrame, Dict[int, float], Dict[int, float]]] = {}
        self._phase_ctx_cache: Dict[str, RoundPhaseContext] = {}
        self._auction_fit_cache: Dict[str, Tuple[Dict[str, Dict[str, float]], float, int]] = {}
        self._mkt_idx_cache: Dict[str, Dict[int, Dict[str, Any]]] = {}

        self._prepare_environment_ground_truth()
        self.reset()

    def _prepare_environment_ground_truth(self) -> None:
        """
        Precomputes environment-only settlement tables (NEVER exposed to the strategy
        before settlement timestamps). Dynamically covers J1..max_settled_jornada.
        """
        # Determine officially settled rounds present in `Primas_Jornadas` (e.g. J1..J12)
        self.payout_date_to_rounds: Dict[str, List[int]] = {}
        self.round_payout_date: Dict[int, str] = {}
        self.all_round_points_by_mgr: Dict[int, Dict[str, int]] = {}
        self.rival_round_points: Dict[int, Dict[str, int]] = {}
        settled_j_set: Set[int] = set()

        if not self._df_primas.empty:
            for j_raw, dt_raw, mgr_raw, pts_raw in zip(
                self._df_primas["Jornada"].values,
                self._df_primas["Fecha"].values,
                self._df_primas["Manager"].values,
                self._df_primas["Puntos"].values,
            ):
                j_label = str(j_raw)
                digits = "".join(ch for ch in j_label if ch.isdigit())
                if not digits:
                    continue
                jn = int(digits)
                settled_j_set.add(jn)
                p_date = str(dt_raw)[:10]
                mgr = str(mgr_raw).strip()
                pts = int(safe_num(pts_raw, 0))
                self.all_round_points_by_mgr.setdefault(jn, {})[mgr] = pts
                if p_date:
                    self.round_payout_date.setdefault(jn, p_date)
                    lst = self.payout_date_to_rounds.setdefault(p_date, [])
                    if jn not in lst:
                        lst.append(jn)

        self.settled_jornada_nums: List[int] = sorted(settled_j_set) if settled_j_set else list(range(1, 11))
        self.max_settled_jornada: int = max(self.settled_jornada_nums)

        # Primary kickoff dates (`part == 1`) dynamically mapped for J1..max_settled+2
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

        # Dynamic `scoreID=5` Ground-Truth Player Points across ALL rounds (`J1..max_settled_jornada`)
        hc_dates = self.view.all_dates
        max_hc = hc_dates[-1] if hc_dates else "2026-11-13"
        max_gt_j = max(11, self.max_settled_jornada)

        self.ground_truth_round_pts: Dict[int, Dict[int, int]] = {jn: {} for jn in range(1, max_gt_j + 1)}
        self.ground_truth_goals: Dict[int, Dict[int, int]] = {jn: {} for jn in range(1, max_gt_j + 1)}
        self.ground_truth_awards: Dict[int, Dict[int, Tuple[int, int, int]]] = {jn: {} for jn in range(1, max_gt_j + 1)}

        rep_all = self.view._df_reports
        if not rep_all.empty:
            mvp_m_col = rep_all["es_mvp_partido"].values if "es_mvp_partido" in rep_all.columns else [""] * len(rep_all)
            mvp_j_col = rep_all["es_mvp_jornada"].values if "es_mvp_jornada" in rep_all.columns else [""] * len(rep_all)
            once_col = rep_all["en_once_ideal"].values if "en_once_ideal" in rep_all.columns else [""] * len(rep_all)
            goles_col = rep_all["goles"].values if "goles" in rep_all.columns else [0] * len(rep_all)
            for jn_v, pid_v, gl_v, mm_v, mj_v, oi_v in zip(
                rep_all["jornada_num_int"].values,
                rep_all["player_id"].values,
                goles_col,
                mvp_m_col,
                mvp_j_col,
                once_col,
            ):
                jn = int(jn_v)
                if not (1 <= jn <= max_gt_j):
                    continue
                pid = int(pid_v) if pid_v is not None else 0
                if pid <= 0:
                    continue
                self.ground_truth_goals[jn][pid] = int(safe_num(gl_v, 0))
                mvp_m = 1 if str(mm_v).upper() in ("SÍ", "SI", "1", "TRUE") else 0
                mvp_j = 1 if str(mj_v).upper() in ("SÍ", "SI", "1", "TRUE") else 0
                once_i = 1 if str(oi_v).upper() in ("SÍ", "SI", "1", "TRUE") else 0
                self.ground_truth_awards[jn][pid] = (mvp_m, mvp_j, once_i)

        # Group contiguous match date blocks per round (`gap <= 3` days) for `J4..max_gt_j`
        blocks_by_j: Dict[int, List[List[str]]] = {}
        all_blocks: List[Tuple[str, str, int]] = []
        for jn in range(4, max_gt_j + 1):
            sub_cal = cal_p1[(cal_p1["jornada_num_int"] == jn)]
            dts_j = sorted({s[:10] for s in sub_cal["fecha_hora"].tolist() if len(s) >= 10 and s[:10] <= max_hc})
            blocks: List[List[str]] = []
            for d_m in dts_j:
                if not blocks or (pd.Timestamp(d_m) - pd.Timestamp(blocks[-1][-1])).days > 3:
                    blocks.append([d_m])
                else:
                    blocks[-1].append(d_m)
            blocks_by_j[jn] = blocks
            for b in blocks:
                all_blocks.append((b[0], b[-1], jn))
        all_blocks.sort()

        delta_windows: Dict[int, List[Tuple[str, str]]] = {}
        needed_piv_dates: Set[str] = set()
        if hc_dates:
            # Find earliest date AFTER J3 finishes where cumulative puntos_totales is actually populated (> 0)
            j3_dts = [s[:10] for s in cal_p1[cal_p1["jornada_num_int"] == 3]["fecha_hora"].tolist() if len(s) >= 10]
            j3_end = max(j3_dts) if j3_dts else ""
            mkt_df = self.view._df_market
            pts_num = pd.to_numeric(mkt_df["puntos_totales"], errors="coerce").fillna(0)
            pop_dates = sorted(
                mkt_df.loc[pts_num > 0, "fecha"].astype(str).str.slice(0, 10).unique().tolist()
            )
            j3_anchor_cands = [d for d in pop_dates if d > j3_end]
            if not j3_anchor_cands:
                j3_anchor_cands = [d for d in hc_dates if d > j3_end]
            j3_anchor_date = j3_anchor_cands[0] if j3_anchor_cands else hc_dates[0]
            needed_piv_dates.add(j3_anchor_date)
        else:
            j3_anchor_date = "2026-09-02"

        if hc_dates:
            for jn, blocks in blocks_by_j.items():
                wins: List[Tuple[str, str]] = []
                for b in blocks:
                    b_start, b_end = b[0], b[-1]
                    d0_cands = [d for d in hc_dates if d <= b_start]
                    d0 = d0_cands[-1] if d0_cands else hc_dates[0]
                    next_starts = [bs for bs, _, _ in all_blocks if bs > b_start]
                    upper_2d = (pd.Timestamp(b_end) + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
                    upper = min(upper_2d, next_starts[0]) if next_starts else upper_2d
                    d1_cands = [d for d in hc_dates if d <= upper]
                    d1 = d1_cands[-1] if d1_cands else hc_dates[-1]
                    if d1 >= d0:
                        wins.append((d0, d1))
                        needed_piv_dates.add(d0)
                        needed_piv_dates.add(d1)
                if wins:
                    delta_windows[jn] = wins

        sub_hc = self.view._df_market[self.view._df_market["fecha"].isin(sorted(needed_piv_dates))].copy()
        if not sub_hc.empty:
            sub_hc["pts_tot"] = pd.to_numeric(sub_hc["puntos_totales"], errors="coerce").fillna(0).astype(int)
            piv = sub_hc.pivot_table(index="player_id", columns="fecha", values="pts_tot", aggfunc="last").fillna(0).astype(int)
        else:
            piv = pd.DataFrame()

        for jn, wins in delta_windows.items():
            if piv.empty:
                continue
            s = pd.Series(0, index=piv.index, dtype=int)
            for d0, d1 in wins:
                if d0 in piv.columns and d1 in piv.columns:
                    s = s + (piv[d1] - piv[d0])
            self.ground_truth_round_pts[jn] = s.to_dict()

        # Reconcile J1..J3 Picas -> official scoreID=5 totals at post-J3 anchor date
        rj_123: Dict[int, Dict[int, int]] = {1: {}, 2: {}, 3: {}}
        played_123: Dict[int, List[int]] = {}
        rep_123 = rep_all[rep_all["jornada_num_int"].isin([1, 2, 3])] if not rep_all.empty else pd.DataFrame()
        if not rep_123.empty:
            for jn_v, pid_v, pts_v, mins_v in zip(
                rep_123["jornada_num_int"].values,
                rep_123["player_id"].values,
                rep_123["puntos_biwenger"].values,
                rep_123["minutos_jugados"].values,
            ):
                jn = int(jn_v)
                pid = int(pid_v) if pid_v is not None else 0
                pts = int(safe_num(pts_v, 0))
                mins = int(safe_num(mins_v, 0))
                if pid > 0:
                    rj_123[jn][pid] = pts
                    if mins > 0 or pts != 0:
                        played_123.setdefault(pid, []).append(jn)

        pts_anchor_map = piv[j3_anchor_date].to_dict() if (not piv.empty and j3_anchor_date in piv.columns) else {}
        all_pids_123 = set(pts_anchor_map.keys()) | set(played_123.keys())
        for pid in all_pids_123:
            j_vals = {1: rj_123[1].get(pid, 0), 2: rj_123[2].get(pid, 0), 3: rj_123[3].get(pid, 0)}
            active_js = sorted(set(played_123.get(pid, [])))
            if pid in pts_anchor_map and active_js:
                target_sum = int(pts_anchor_map[pid])
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

        if not rep_all.empty:
            rep_pids = rep_all["player_id"].values
            corrected = [
                self.ground_truth_round_pts.get(int(jn), {}).get(int(pid), None)
                for jn, pid in zip(rep_all["jornada_num_int"].values, rep_pids)
            ]
            orig_pts = pd.to_numeric(rep_all["puntos_biwenger"], errors="coerce").fillna(0).astype(int).values
            rep_all["puntos_biwenger"] = np.array(
                [float(c) if c is not None else float(o) for c, o in zip(corrected, orig_pts)],
                dtype=float,
            )

        latest_mkt = self.view._df_market.drop_duplicates(subset=["player_id"], keep="last")
        self.player_meta: Dict[int, Dict[str, str]] = {}
        if not latest_mkt.empty:
            for pid_v, nm_v, pos_v in zip(
                latest_mkt["player_id"].values,
                latest_mkt["nombre"].values if "nombre" in latest_mkt.columns else [""] * len(latest_mkt),
                latest_mkt["posicion"].values if "posicion" in latest_mkt.columns else [""] * len(latest_mkt),
            ):
                pid = int(pid_v)
                self.player_meta[pid] = {
                    "nombre": str(nm_v or f"ID {pid}"),
                    "posicion": str(pos_v or ""),
                    "natural_pos": normalize_position(pos_v),
                }

        # Store raw bid tuples per (res_date, pid) so switching initial_manager_override cleanly excludes target_manager
        self._bids_by_res_date: Dict[Tuple[str, int], List[Tuple[str, int]]] = {}
        if not self.view._df_bids.empty:
            for mgr_v, d_v, pid_v, amt_v in zip(
                self.view._df_bids["manager_pujador"].values,
                self.view._df_bids["bid_day"].values,
                self.view._df_bids["player_id"].values,
                self.view._df_bids["importe_pujado"].values,
            ):
                mgr = str(mgr_v or "").strip()
                d_res = str(d_v)[:10]
                pid = int(safe_num(pid_v, 0))
                amt = int(safe_num(amt_v, 0))
                if d_res and pid > 0 and amt > 0:
                    self._bids_by_res_date.setdefault((d_res, pid), []).append((mgr, amt))

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
        if not of_df.empty:
            for d_v, pid_v, off_v in zip(
                of_df["fecha_d"].values,
                of_df["pid_int"].values,
                of_df["precio_salida_o_oferta"].values,
            ):
                pid = int(pid_v)
                amt = int(safe_num(off_v, 0))
                if pid > 0 and amt > 0:
                    self.observed_offers_by_date[(str(d_v), pid)] = amt

        self._df_movs["mov_day"] = self._df_movs["fecha"].astype(str).str.slice(0, 10)
        self._df_movs["pid_int"] = pd.to_numeric(self._df_movs["player_id"], errors="coerce").fillna(0).astype(int)
        self._df_movs["precio_int"] = pd.to_numeric(self._df_movs["precio"], errors="coerce").fillna(0).astype(int)

        self._mov_events_by_day: Dict[str, List[Tuple[int, str, str, int]]] = {}
        if not self._df_movs.empty:
            for d_v, pid_v, comp_v, tipo_v, pr_v in zip(
                self._df_movs["mov_day"].values,
                self._df_movs["pid_int"].values,
                self._df_movs["comprador"].values if "comprador" in self._df_movs.columns else [""] * len(self._df_movs),
                self._df_movs["tipo"].values if "tipo" in self._df_movs.columns else [""] * len(self._df_movs),
                self._df_movs["precio_int"].values,
            ):
                d = str(d_v)
                pid = int(pid_v)
                if pid <= 0 or not d:
                    continue
                self._mov_events_by_day.setdefault(d, []).append((pid, str(comp_v or "").strip(), str(tipo_v or "").strip(), int(pr_v)))

        self._rebuild_rival_views()

    def _rebuild_rival_views(self) -> None:
        """Builds manager-specific rival points, max rival bids, and rival clause events for active `self.target_manager`."""
        self.rival_round_points = {
            jn: {m: p for m, p in mgrs.items() if m != self.target_manager}
            for jn, mgrs in self.all_round_points_by_mgr.items()
        }
        self.max_rival_bid_by_res_date = {}
        for key, bids in self._bids_by_res_date.items():
            mx = 0
            for mgr, amt in bids:
                if mgr != self.target_manager and amt > mx:
                    mx = amt
            if mx > 0:
                self.max_rival_bid_by_res_date[key] = mx

        self.rival_acquisitions_by_day = {}
        self.rival_clause_attacks_by_day = {}
        for d, evs in self._mov_events_by_day.items():
            for pid, comp, tipo, pr_i in evs:
                if comp and comp not in ("Mercado", self.target_manager):
                    self.rival_acquisitions_by_day.setdefault(d, []).append(pid)
                    if tipo == "Clausulazo":
                        self.rival_clause_attacks_by_day.setdefault(d, []).append({
                            "player_id": pid,
                            "attacker": comp,
                            "paid_clause": pr_i,
                        })

    def get_causal_standings_snapshot(
        self,
        date_str: str,
        mgrs_today: Optional[pd.DataFrame] = None,
        my_net_worth: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Computes strictly causal league standings on `date_str` using ONLY settled payouts
        with `payout_date <= date_str` and manager net worth on `date_str`. Zero lookahead!
        """
        totals: Dict[str, int] = {}
        for mgrs_map in self.all_round_points_by_mgr.values():
            for m in mgrs_map:
                if m != self.target_manager:
                    totals[m] = 0
        settled_so_far: List[int] = []
        for jn in self.settled_jornada_nums:
            p_date = self.round_payout_date.get(jn, "")
            if p_date and p_date <= date_str:
                settled_so_far.append(jn)
                for m, p in self.all_round_points_by_mgr.get(jn, {}).items():
                    if m != self.target_manager:
                        totals[m] = totals.get(m, 0) + int(p)

        my_pts = sum(int(self.settled_rounds.get(jn, {}).get("points", 0)) for jn in settled_so_far)
        totals[self.target_manager] = my_pts
        ordered = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
        my_rank = 1
        for idx, (m, _) in enumerate(ordered, start=1):
            if m == self.target_manager:
                my_rank = idx
                break
        leader_pts = ordered[0][1] if ordered else my_pts
        second_pts = ordered[1][1] if len(ordered) > 1 else leader_pts
        podium_pts = ordered[min(2, len(ordered) - 1)][1] if ordered else my_pts
        n_mgrs = max(1, len(ordered))
        n_settled = len(settled_so_far)
        top_rivals = [m for m, _ in ordered[:4] if m != self.target_manager]

        leader_nw = 0.0
        if mgrs_today is not None and not mgrs_today.empty and "patrimonio_total" in mgrs_today.columns:
            rivals_nw = pd.to_numeric(
                mgrs_today.loc[mgrs_today["manager"] != self.target_manager, "patrimonio_total"],
                errors="coerce",
            ).dropna()
            if len(rivals_nw) > 0:
                leader_nw = float(rivals_nw.max())
        eff_my_nw = float(my_net_worth) if my_net_worth is not None else float(self.cash)
        if leader_nw <= 0.0:
            leader_nw = eff_my_nw

        return {
            "rank": my_rank,
            "my_rank": my_rank,
            "n_managers": n_mgrs,
            "total_managers": n_mgrs,
            "my_points": my_pts,
            "leader_points": leader_pts,
            "second_points": second_pts,
            "podium_points": podium_pts,
            "gap_to_leader": max(0, leader_pts - my_pts),
            "gap_to_podium": max(0, podium_pts - my_pts),
            "settled_rounds_count": n_settled,
            "rounds_settled": n_settled,
            "remaining_rounds": max(1, 38 - (max(settled_so_far) if settled_so_far else 0)),
            "my_net_worth": eff_my_nw,
            "leader_net_worth": leader_nw,
            "top_rival_managers": top_rivals,
        }

    def _get_mkt_idx(self, date_str: str, mkt_today: Optional[pd.DataFrame] = None) -> Dict[int, Dict[str, Any]]:
        cached = self._mkt_idx_cache.get(date_str)
        if cached is not None:
            return cached
        df = mkt_today if mkt_today is not None else self.view.get_market_snapshot(date_str)
        if df.empty:
            self._mkt_idx_cache[date_str] = {}
            return {}
        pids = pd.to_numeric(df["player_id"], errors="coerce").fillna(0).astype(int).tolist()
        records = df.to_dict("records")
        idx_map = {pid: rec for pid, rec in zip(pids, records) if pid > 0}
        self._mkt_idx_cache[date_str] = idx_map
        return idx_map

    def reset(self, manager_override: Optional[str] = None, seed_override: Optional[int] = None) -> None:
        """Resets portfolio to `target_manager`'s (or `manager_override`) exact `2026-08-01` initial draw and cash."""
        if seed_override is not None:
            self.seed = int(seed_override)
        self.rng = np.random.default_rng(self.seed)
        if manager_override is not None and str(manager_override).strip() != self.target_manager:
            self.target_manager = str(manager_override).strip()
            self._rebuild_rival_views()
        self.view.set_date("2026-08-01")
        # Reset causal Ridge weights without reconstructing 105-day dictionaries every run
        self.efr_predictor._ridge_weights = None
        self.efr_predictor._last_fit_date = None

        mgr_d1 = self.view.get_manager_snapshots("2026-08-01")
        my_row = mgr_d1[mgr_d1["manager"] == self.target_manager]
        if my_row.empty:
            raise RuntimeError(f"Manager '{self.target_manager}' not found on 2026-08-01 in Historico_Equipos_Diario")

        row0 = my_row.iloc[0]
        init_pids = [int(x) for x in str(row0["plantilla_ids"]).split(",") if x.strip().isdigit()]

        mkt_d1 = self._get_mkt_idx("2026-08-01")
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

        if self.equalize_starting_wealth:
            self.cash = float(max(0, 40_000_000 - init_sq_val))
            self.initial_net_worth = 40_000_000
        else:
            self.cash = safe_num(row0["saldo_disponible_exacto"], 23_900_000.0)
            self.initial_net_worth = int(round(self.cash + init_sq_val))

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
        return self

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

    def _build_daily_market_listings(
        self,
        date_str: str,
        next_date_str: Optional[str],
        mkt_today: pd.DataFrame,
        mkt_idx: Optional[Dict[int, Dict[str, Any]]] = None,
    ) -> pd.DataFrame:
        m_idx = mkt_idx if mkt_idx is not None else self._get_mkt_idx(date_str, mkt_today)
        listings: List[Dict[str, Any]] = []
        seen_pids: Set[int] = set()
        k_free_slots = max(5, int(self.rules.market_free_slots))

        if date_str in self.en_venta_by_date:
            ev = self.en_venta_by_date[date_str]
            pids_col = ev["pid_int"].values
            pm_col = ev["precio_mercado_dia"].values if "precio_mercado_dia" in ev.columns else [150_000] * len(ev)
            ask_col = ev["precio_salida_o_oferta"].values if "precio_salida_o_oferta" in ev.columns else pm_col
            sel_col = ev["vendedor_o_origen"].values if "vendedor_o_origen" in ev.columns else ["Mercado"] * len(ev)
            jug_col = ev["jugador"].values if "jugador" in ev.columns else [""] * len(ev)
            for pid_v, pm_v, ask_v, sel_v, jug_v in zip(pids_col, pm_col, ask_col, sel_col, jug_col):
                pid = int(pid_v)
                if pid <= 0 or pid in seen_pids or pid in self.squad:
                    continue
                seller = str(sel_v or "Mercado").strip()
                if seller == self.target_manager:
                    continue
                mi = m_idx.get(pid, {})
                vm_today = float(mi.get("precio", pm_v or 150_000))
                ask = safe_num(ask_v, vm_today)
                listings.append({
                    "player_id": pid,
                    "jugador": str(mi.get("nombre", jug_v or "")),
                    "seller_name": seller,
                    "precio_mercado_dia": vm_today,
                    "ask_price": ask,
                })
                seen_pids.add(pid)
            return pd.DataFrame(listings)

        n_mercado_slots = 0
        if next_date_str and next_date_str in self.resolved_auctions_by_res_date:
            sr = self.resolved_auctions_by_res_date[next_date_str]
            pids_col = sr["pid_int"].values
            sel_col = sr["vendedor_o_origen"].values if "vendedor_o_origen" in sr.columns else ["Mercado"] * len(sr)
            jug_col = sr["jugador"].values if "jugador" in sr.columns else [""] * len(sr)
            for pid_v, sel_v, jug_v in zip(pids_col, sel_col, jug_col):
                pid = int(pid_v)
                if pid <= 0 or pid in seen_pids or pid in self.squad or pid not in m_idx:
                    continue
                seller = str(sel_v or "Mercado").strip()
                if seller == self.target_manager:
                    continue
                mi = m_idx[pid]
                vm_today = float(mi.get("precio", 150_000))
                listings.append({
                    "player_id": pid,
                    "jugador": str(mi.get("nombre", jug_v or "")),
                    "seller_name": seller,
                    "precio_mercado_dia": vm_today,
                    "ask_price": vm_today,
                })
                seen_pids.add(pid)
                if seller == "Mercado":
                    n_mercado_slots += 1

        if self.market_mode == "monte_carlo_20slot" and n_mercado_slots < k_free_slots:
            owners = mkt_today["propietario_liga"].astype(str).values
            pids_m = mkt_today["player_id"].values
            prices_m = mkt_today["precio"].values
            exclude = seen_pids | set(self.squad.keys())
            free_pids = [
                int(p) for p, ow, pr in zip(pids_m, owners, prices_m)
                if ow in ("Libre", "Mercado", "") and pr > 0 and int(p) not in exclude
            ]
            need = min(k_free_slots - n_mercado_slots, len(free_pids))
            if need > 0:
                sampled_pids = self.rng.choice(free_pids, size=need, replace=False)
                for spid in sampled_pids:
                    pid = int(spid)
                    p_info = m_idx[pid]
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

        owned_pids: List[int] = sorted(lock["owned_at_kickoff"])
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
        archetype: Optional[StrategyArchetype] = None,
        manager_override: Optional[str] = None,
        seed_override: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Executes the full day-by-day simulation under 100% honest official Biwenger rules
        (`<= 5` market listing slots, `48h` clause lock + active-round freeze, `1:1` shields).
        """
        self.reset(manager_override=manager_override, seed_override=seed_override)
        max_avail_date = self.view.all_dates[-1] if self.view.all_dates else "2026-11-13"
        eff_end_date = end_date or max_avail_date

        mem_ledger = StrategyMemoryLedger(in_memory_only=True)
        if optimizer is not None:
            opt = optimizer
        elif archetype is not None:
            opt = PortfolioOptimizer(
                xp_engine=self.xp_engine,
                credit_limit_ratio=self.credit_limit_ratio if enable_leverage else 0.0,
                max_squad_size=archetype.target_roster_size,
                preventive_listing=enable_preventive_listing,
                rules=self.rules,
                archetype=archetype,
                protect_pillars_absolute=archetype.protect_pillars_absolute,
            )
        else:
            opt = PortfolioOptimizer(
                xp_engine=self.xp_engine,
                credit_limit_ratio=self.credit_limit_ratio if enable_leverage else 0.0,
                max_squad_size=self.max_squad_size,
                preventive_listing=enable_preventive_listing,
                rules=self.rules,
            )
        opt.credit_limit_ratio = self.credit_limit_ratio if enable_leverage else 0.0
        opt.preventive_listing = enable_preventive_listing
        if not enable_clauses:
            opt.clause_sniper.policy = "DISABLED"
        elif archetype is not None:
            opt.clause_sniper.policy = str(getattr(archetype, "clause_policy", "COMPETITIVE_VORP") or "COMPETITIVE_VORP")
        else:
            opt.clause_sniper.policy = clause_policy

        arch_lifecycle = (opt.archetype is None) or bool(getattr(opt.archetype, "lifecycle_driven", False))
        soft_max = int(opt.max_squad_size) + (3 if arch_lifecycle else 2)
        opt_max_squad_cap = min(int(self.rules.team_max_size), max(int(opt.max_squad_size), soft_max))
        supports_standings = "causal_standings_ctx" in inspect.signature(opt.generate_daily_plan).parameters
        supports_phase_ctx = "phase_ctx" in inspect.signature(opt.generate_daily_plan).parameters
        stressed_injuries: Dict[int, str] = {}

        sim_dates = [d for d in self.view.all_dates if start_date <= d <= eff_end_date]
        max_sales_slots = int(self.rules.market_max_user_sales)
        clause_delay_days = int(self.rules.clause_activation_delay_days)
        offer_lo, offer_hi = self.offer_mult_range

        for idx_d, date_str in enumerate(sim_dates):
            next_date_str = sim_dates[idx_d + 1] if (idx_d + 1) < len(sim_dates) else None
            self.view.set_date(date_str)
            mkt_today = self.view.get_market_snapshot(date_str)
            mkt_idx = self._get_mkt_idx(date_str, mkt_today)

            # Optional M2 Stress Scenario: extra injury wave on owned assets
            if self.injury_stress_mult > 1.0 and self.squad:
                stressed_injuries = {p: u for p, u in stressed_injuries.items() if u > date_str}
                extra_prob = 0.012 * (self.injury_stress_mult - 1.0)
                for pid_s in list(self.squad.keys()):
                    if pid_s not in stressed_injuries and self.rng.random() < extra_prob:
                        u_dt = (pd.Timestamp(date_str) + pd.Timedelta(days=14)).strftime("%Y-%m-%d")
                        stressed_injuries[pid_s] = u_dt
                if stressed_injuries:
                    mkt_today = mkt_today.copy()
                    inj_mask = mkt_today["player_id"].isin(stressed_injuries.keys())
                    if inj_mask.any():
                        mkt_today.loc[inj_mask, "estado"] = "injured"
                        mkt_idx = dict(mkt_idx)
                        for pid_inj in stressed_injuries:
                            if pid_inj in mkt_idx:
                                rec_copy = dict(mkt_idx[pid_inj])
                                rec_copy["estado"] = "injured"
                                mkt_idx[pid_inj] = rec_copy

            # Resolve exact calendar phase at 12:00 daytime decision hour (cached per date)
            phase_ctx = self._phase_ctx_cache.get(date_str)
            if phase_ctx is None:
                phase_ctx = self.rules.compute_round_phase(
                    now_ts=pd.Timestamp(f"{date_str} 12:00:00"),
                    df_calendar=self.view._df_calendar,
                )
                self._phase_ctx_cache[date_str] = phase_ctx
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

                    if pid in self.squad or len(self.squad) >= opt_max_squad_cap:
                        continue

                    curr_squad_val = sum(
                        float(mkt_idx.get(p, {}).get("precio", st.purchase_price))
                        for p, st in self.squad.items()
                    )
                    max_allowed_now = self.cash + (self.credit_limit_ratio if enable_leverage else 0.0) * curr_squad_val
                    if b_amt > max_allowed_now:
                        continue

                    rival_win_bid = self.max_rival_bid_by_res_date.get((date_str, pid), 0)
                    vm_now = float(mkt_idx.get(pid, {}).get("precio", ask))
                    if rival_win_bid > 0:
                        if self.overbid_inflation_factor != 1.0 and rival_win_bid > vm_now:
                            eff_rival = int(round(vm_now + (rival_win_bid - vm_now) * self.overbid_inflation_factor))
                        else:
                            eff_rival = rival_win_bid
                        won = (b_amt > eff_rival)
                    else:
                        if seller == "Mercado":
                            eff_ask = ask
                            if self.overbid_inflation_factor > 1.0:
                                eff_ask = int(round(ask * (1.0 + 0.015 * (self.overbid_inflation_factor - 1.0))))
                            won = (b_amt >= eff_ask)
                        else:
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

            # Incoming rival clause steals (observed + optional stress rival snipes)
            if clauses_open_today:
                stolen_pids_today: Set[int] = set()
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
                                stolen_pids_today.add(pid)

                if self.aggressive_rival_clause_prob > 0.0 and self.squad:
                    for pid in list(self.squad.keys()):
                        if pid in stolen_pids_today or pid not in self.squad:
                            continue
                        st = self.squad[pid]
                        days_held = (pd.Timestamp(date_str) - pd.Timestamp(st.acquired_date)).days
                        if days_held < clause_delay_days:
                            continue
                        mi = mkt_idx.get(pid, {})
                        vm_now = float(mi.get("precio", st.purchase_price))
                        sub_now = float(mi.get("subida_24h", 0.0))
                        our_clause = self._get_player_clause(st, vm_now)
                        # Only high-momentum unlocked assets realistically trigger rival clause raids
                        if sub_now >= 40_000 and our_clause <= 22_000_000 and self.rng.random() < self.aggressive_rival_clause_prob:
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
                if (date_str, pid) in self.observed_offers_by_date and offer_lo >= 0.94:
                    off_eur = self.observed_offers_by_date[(date_str, pid)]
                else:
                    vm_now = float(mkt_idx.get(pid, {}).get("precio", self.squad[pid].purchase_price))
                    mult = float(self.rng.uniform(offer_lo, offer_hi))
                    off_eur = max(150_000, int(round((vm_now * mult) / 10_000.0) * 10_000))
                prev_amt = self.active_offers.get(pid, (0, ""))[0]
                if off_eur >= prev_amt:
                    self.active_offers[pid] = (off_eur, exp_tomorrow)

            # ==================================================================
            # PHASE B: Daytime Strategy Decision (`09:00..17:59`, Strictly Causal!)
            # ==================================================================
            target_jn = int(phase_ctx.target_jornada_num) if phase_ctx else 12
            days_to_kickoff = int(phase_ctx.days_to_kickoff) if phase_ctx else 4
            cal_causal = self.view.get_calendar_causal()

            if self.injury_stress_mult == 1.0 and date_str in self._daily_signal_cache:
                xp_table, efr_map, shock_map = self._daily_signal_cache[date_str]
            else:
                past_reports = self.view.get_past_match_reports()
                active_kickoff_jn = self.kickoff_date_to_round.get(date_str, target_jn)
                use_jn = active_kickoff_jn if is_kickoff_day else target_jn
                comuniate_df = self.view.get_comuniate_for_round(use_jn)

                xp_table = self.xp_engine.compute_xp_table(
                    df_market_today=mkt_today,
                    df_reports_past=past_reports,
                    df_calendar=cal_causal,
                    target_jornada_num=use_jn,
                    df_comuniate_round=comuniate_df,
                )
                efr_map, shock_map = self.efr_predictor.predict_efr_72h_and_shocks()
                if self.injury_stress_mult == 1.0:
                    self._daily_signal_cache[date_str] = (xp_table, efr_map, shock_map)

            squad_df = self._build_squad_df(mkt_idx)
            owners_arr = mkt_today["propietario_liga"].astype(str).values
            prices_arr = mkt_today["precio"].values
            free_m_mask = (owners_arr == "Libre") | (owners_arr == "Mercado") | (owners_arr == "")
            scarcity_free_5m = int(np.sum((prices_arr >= 5_000_000) & free_m_mask))
            scarcity_free_10m = int(np.sum((prices_arr >= 10_000_000) & free_m_mask))

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
                mgrs_today = self.view.get_manager_snapshots(date_str)
                if date_str in self._auction_fit_cache:
                    tq_c, bl_c, nf_c = self._auction_fit_cache[date_str]
                    opt.auction_model.tier_quantiles = {k: dict(v) for k, v in tq_c.items()}
                    opt.auction_model.baseline_league_max_bid = bl_c
                    opt.auction_model.n_fitted_auctions = nf_c
                else:
                    past_bids = self.view.get_past_bids(date_str)
                    opt.auction_model.fit_causal(past_bids, mgrs_today)
                    self._auction_fit_cache[date_str] = (
                        {k: dict(v) for k, v in opt.auction_model.tier_quantiles.items()},
                        opt.auction_model.baseline_league_max_bid,
                        opt.auction_model.n_fitted_auctions,
                    )

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

                curr_ts = pd.Timestamp(date_str)
                days_for_sale = int(self.rules.days_for_sale)
                sell_locked_ids = {
                    pid for pid, st in self.squad.items()
                    if (curr_ts - pd.Timestamp(st.acquired_date)).days < days_for_sale
                }

                clause_locked_ids: Set[int] = set()
                for lookback_i in range(clause_delay_days):
                    d_lb = (curr_ts - pd.Timedelta(days=lookback_i)).strftime("%Y-%m-%d")
                    for rpid in self.rival_acquisitions_by_day.get(d_lb, []):
                        clause_locked_ids.add(rpid)
                for spid, sdate in self.recent_sold_dates.items():
                    if (curr_ts - pd.Timestamp(sdate)).days < (clause_delay_days + 2):
                        clause_locked_ids.add(spid)

                listings_df = self._build_daily_market_listings(date_str, next_date_str, mkt_today, mkt_idx=mkt_idx)
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
                    dp_kwargs: Dict[str, Any] = dict(
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
                    if supports_phase_ctx:
                        dp_kwargs["phase_ctx"] = phase_ctx
                    if supports_standings:
                        curr_nw = self.cash + sum(
                            float(mkt_idx.get(p, {}).get("precio", st.purchase_price))
                            for p, st in self.squad.items()
                        )
                        dp_kwargs["causal_standings_ctx"] = self.get_causal_standings_snapshot(
                            date_str,
                            mgrs_today=mgrs_today,
                            my_net_worth=curr_nw,
                        )
                    plan = opt.generate_daily_plan(**dp_kwargs)

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
                        if pid in self.squad or pid in clause_locked_ids or len(self.squad) >= opt_max_squad_cap:
                            continue
                        if (self.cash - c_amt) < 0:
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
                if 1 <= jn_lock <= max(11, self.max_settled_jornada):
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

        # Ensure all settled rounds (`1..max_settled_jornada`) + any active next round are scored
        max_scorable_j = max(11, self.max_settled_jornada)
        for jn in range(1, max_scorable_j + 1):
            if jn not in self.settled_rounds and jn in self.locked_round_lineups:
                self.settled_rounds[jn] = self._score_locked_round(jn)

        # Official closed-round totals across ALL `settled_jornada_nums` (J1..J12!)
        total_pts_settled = sum(
            self.settled_rounds[jn]["points"] for jn in self.settled_jornada_nums if jn in self.settled_rounds
        )
        total_opt_settled = sum(
            self.settled_rounds[jn]["optimal_points"] for jn in self.settled_jornada_nums if jn in self.settled_rounds
        )
        total_pts_j1_j10 = sum(
            self.settled_rounds.get(jn, {}).get("points", 0) for jn in range(1, 11)
        )
        j11_pts = self.settled_rounds.get(11, {}).get("points", 0)
        j12_pts = self.settled_rounds.get(12, {}).get("points", 0)
        insolvent_rounds = sum(
            1 for jn, r in self.settled_rounds.items() if jn <= max_scorable_j and not r["is_solvent"]
        )
        lineup_eff_pct = round((total_pts_settled / total_opt_settled * 100.0), 2) if total_opt_settled > 0 else 100.0

        # Out-of-time regime split points: H1 (J1..J5 Abundance) vs H2 (J6..max_settled Scarcity)
        pts_h1_abundance = sum(self.settled_rounds.get(jn, {}).get("points", 0) for jn in range(1, 6))
        pts_h2_j6_j10 = sum(self.settled_rounds.get(jn, {}).get("points", 0) for jn in range(6, 11))
        pts_h2_full = sum(
            self.settled_rounds.get(jn, {}).get("points", 0)
            for jn in self.settled_jornada_nums if jn >= 6
        )

        final_day = self.daily_curve[-1] if self.daily_curve else {"cash": 0, "squad_value": 0, "net_worth": 0}
        trading_alpha_eur = int(
            final_day["net_worth"] + self.shields_spent_eur - self.initial_net_worth - self.bonuses_earned_eur
        )

        return {
            "total_points_settled": int(total_pts_settled),
            "total_points": int(total_pts_settled),
            "settled_jornadas_count": len(self.settled_jornada_nums),
            "max_settled_jornada": int(self.max_settled_jornada),
            "total_points_j1_j10": int(total_pts_j1_j10),
            "j11_partial_points": int(j11_pts),
            "j11_j12_points": int(j11_pts + j12_pts),
            "total_points_incl_j11": int(total_pts_j1_j10 + j11_pts),
            "points_h1_j1_j5": int(pts_h1_abundance),
            "points_h2_j6_j10": int(pts_h2_j6_j10),
            "points_h2": int(pts_h2_full),
            "optimal_points_settled": int(total_opt_settled),
            "optimal_points_j1_j10": int(total_opt_settled),
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
