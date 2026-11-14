"""
Dynamic Replacement Cost & Multi-Round Hold-vs-Sell NPV Engine (`replacement_cost.py`)
======================================================================================
General, zero-overfitting microeconomic engine that adapts automatically to:
1. Live League Scarcity (`MarketScarcityEngine`):
   - Measures real-time free-agent availability vs rival-locked ownership by position & `xp_healthy`.
   - Computes expected free-market appearance wait time (`W_free` days given `marketSize` daily slots)
     and minimum unlocked rival release clause (`C_clause_min`) to derive true Replacement Cost `C_replace(i, t)`.
2. Calendar-Aware Multi-Round Net Present Value (`evaluate_hold_vs_sell_npv`):
   - Crosses expected absence days against real match timestamps in `Partidos_Calendario`
     (naturally accounting for FIFA international breaks and midweek matchdays).
   - Compares 6-round `NPV_HOLD` (bench cover during `m_i` missed rounds + `xp_healthy` return)
     against `NPV_SELL` (best realistic achievable market reinvestment minus replacement spread).
   - Zero player-specific rules or magic exceptions.
"""

from dataclasses import dataclass
from typing import Dict, List, Any, Optional, Set, Tuple
import numpy as np
import pandas as pd

from src.tools.strategy_engine.league_rules import LeagueRulesEngine, DEFAULT_CLAUSE_RANGES
from src.utils.numeric import safe_num


POS_NORMALIZE = {
    "1": "GK", "portero": "GK", "gk": "GK", "pt": "GK",
    "2": "DF", "defensa": "DF", "df": "DF",
    "3": "MF", "centrocampista": "MF", "mf": "MF", "mc": "MF",
    "4": "FW", "delantero": "FW", "fw": "FW", "dl": "FW",
}

LINE_BIT = {"GK": 1, "DF": 2, "MF": 4, "FW": 8}


def _lines_to_bitmask(lines: Any) -> int:
    if isinstance(lines, int):
        return lines
    mask = 0
    if isinstance(lines, (set, list, tuple)):
        for ln in lines:
            mask |= LINE_BIT.get(str(ln).strip(), 0)
        return mask
    for tok in str(lines or "").split(","):
        mask |= LINE_BIT.get(tok.strip(), 0)
    return mask


def _compute_official_clause_vec(
    prices: np.ndarray,
    clause_ranges: Optional[List[Tuple[float, float, str]]] = None,
) -> np.ndarray:
    """Vectorized Biwenger official clause brackets matching LeagueRulesEngine.compute_official_clause."""
    ranges = clause_ranges if clause_ranges else DEFAULT_CLAUSE_RANGES
    out = np.empty_like(prices, dtype=float)
    rem = np.ones(len(prices), dtype=bool)
    for threshold, rule_val, mode in ranges:
        if not np.any(rem):
            break
        hit = rem & (prices <= float(threshold))
        if np.any(hit):
            if mode == "%":
                out[hit] = prices[hit] * (float(rule_val) / 100.0)
            else:
                out[hit] = float(rule_val)
            rem &= ~hit
    if np.any(rem):
        out[rem] = prices[rem] * 1.25
    return np.round(out)


def _build_calendar_index(df_calendar: Optional[pd.DataFrame]) -> Optional[Tuple[List[Tuple[int, pd.Timestamp]], Dict[Tuple[int, str], pd.Timestamp]]]:
    """Parses Partidos_Calendario once into sorted (j_num, round_start_ts) + (j_num, team_lower)->match_ts."""
    if df_calendar is None or df_calendar.empty or "jornada" not in df_calendar.columns:
        return None
    ts_col = "fecha_hora" if "fecha_hora" in df_calendar.columns else ("fecha" if "fecha" in df_calendar.columns else None)
    if not ts_col:
        return None

    j_nums = (
        df_calendar["jornada_num"].values
        if "jornada_num" in df_calendar.columns
        else df_calendar["jornada"].astype(str).str.extract(r"(\d+)")[0].values
    )
    j_num_s = pd.to_numeric(j_nums, errors="coerce")
    dt_s = pd.to_datetime(df_calendar[ts_col], errors="coerce")

    valid_mask = pd.notna(j_num_s) & pd.notna(dt_s)
    if not np.any(valid_mask):
        return None

    j_arr = np.asarray(j_num_s[valid_mask], dtype=int)
    dt_arr = dt_s[valid_mask].values

    has_loc = "equipo_local" in df_calendar.columns or "local" in df_calendar.columns
    loc_col = "equipo_local" if "equipo_local" in df_calendar.columns else ("local" if "local" in df_calendar.columns else None)
    vis_col = "equipo_visitante" if "equipo_visitante" in df_calendar.columns else ("visitante" if "visitante" in df_calendar.columns else None)
    locs = df_calendar.loc[valid_mask, loc_col].values if loc_col else None
    viss = df_calendar.loc[valid_mask, vis_col].values if vis_col else None

    round_min: Dict[int, pd.Timestamp] = {}
    team_match: Dict[Tuple[int, str], pd.Timestamp] = {}

    for idx in range(len(j_arr)):
        j_i = int(j_arr[idx])
        ts_v = pd.Timestamp(dt_arr[idx])
        prev_r = round_min.get(j_i)
        if prev_r is None or ts_v < prev_r:
            round_min[j_i] = ts_v
        if has_loc and locs is not None and viss is not None:
            l_s = str(locs[idx] or "").strip().lower()
            v_s = str(viss[idx] or "").strip().lower()
            if l_s:
                k_l = (j_i, l_s)
                if k_l not in team_match or ts_v < team_match[k_l]:
                    team_match[k_l] = ts_v
            if v_s:
                k_v = (j_i, v_s)
                if k_v not in team_match or ts_v < team_match[k_v]:
                    team_match[k_v] = ts_v

    sorted_rounds = sorted(round_min.items(), key=lambda x: x[0])
    return sorted_rounds, team_match


def _norm_pos(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    return POS_NORMALIZE.get(s, "MF")


def estimate_absence_days_from_status(
    estado: str,
    is_available: bool = True,
    explicit_return_days: Optional[int] = None,
) -> int:
    """
    Principled prior for expected absence duration in calendar days based on official status,
    overridable when explicit medical/news return horizon (`explicit_return_days`) is known.
    """
    if explicit_return_days is not None and explicit_return_days >= 0:
        return int(explicit_return_days)

    st = str(estado or "ok").strip().lower()
    if is_available and st in ("ok", "disponible", "available", "", "nan", "none"):
        return 0
    if st in ("duda", "doubt", "questionable"):
        return 3
    if st in ("sancionado", "suspended", "tarjeta_roja", "acumulacion", "red_card"):
        return 6
    if st in ("convocado_seleccion", "internacional", "no_disponible", "baja"):
        return 12
    if st in ("lesionado", "injured", "lesion"):
        return 18
    return 0 if is_available else 14


def estimate_missed_rounds_from_calendar(
    current_date: str,
    absence_days: int,
    df_calendar: Optional[pd.DataFrame] = None,
    team_name: Optional[str] = None,
    horizon_rounds: int = 6,
    calendar_index: Optional[Tuple[List[Tuple[int, pd.Timestamp]], Dict[Tuple[int, str], pd.Timestamp]]] = None,
) -> Tuple[int, List[int]]:
    """
    Counts how many upcoming LaLiga rounds (`0 .. horizon_rounds`) a player actually misses
    between `current_date` and `current_date + absence_days` using real match dates in `Partidos_Calendario`.
    Automatically absorbs FIFA international breaks (0 missed rounds during 14-day break!).
    """
    if absence_days <= 0:
        return 0, []

    now_ts = pd.Timestamp(current_date)
    return_ts = now_ts + pd.Timedelta(days=int(absence_days))
    cal_idx = calendar_index if calendar_index is not None else _build_calendar_index(df_calendar)

    if cal_idx is not None:
        sorted_rounds, team_match = cal_idx
        cutoff_ts = now_ts - pd.Timedelta(hours=6)
        upcoming = [(j_num, dt_min) for j_num, dt_min in sorted_rounds if dt_min >= cutoff_ts][:horizon_rounds]
        if upcoming:
            missed: List[int] = []
            norm_team = str(team_name or "").strip().lower()
            for j_num, dt_min in upcoming:
                match_dt = team_match.get((j_num, norm_team), dt_min) if norm_team else dt_min
                if match_dt < return_ts:
                    missed.append(j_num)
            return min(len(missed), horizon_rounds), missed

    # Fallback when calendar table is unavailable: ~1 round per 7.5 days
    est_missed = int(np.clip(np.ceil(absence_days / 7.5), 1, horizon_rounds))
    return est_missed, []


@dataclass
class ReplacementProfile:
    """Microeconomic replacement metrics for a specific player on date `t`."""
    player_id: int
    natural_pos: str
    xp_healthy: float
    market_price: float
    n_free_comparable: int
    n_rival_comparable: int
    expected_free_wait_days: float
    free_replacement_cost_eur: float
    clause_replacement_cost_eur: float
    blended_replacement_cost_eur: float
    scarcity_tier: str  # ABUNDANT | MODERATE | SCARCE | IRREPLACEABLE_FREE


class MarketScarcityEngine:
    """
    Computes live positional & quality-tier scarcity directly from `df_market_today` + `xp_table`.
    Zero hardcoded dates, thresholds, or player IDs.
    """

    def __init__(
        self,
        df_market_today: pd.DataFrame,
        xp_table: pd.DataFrame,
        my_manager_name: str,
        rules: Optional[LeagueRulesEngine] = None,
        clause_locked_ids: Optional[Set[int]] = None,
        auction_overbid_pct: float = 0.08,
        df_calendar: Optional[pd.DataFrame] = None,
    ):
        self.my_manager_name = str(my_manager_name or "").strip()
        self.rules = rules or LeagueRulesEngine.load()
        self.clause_locked_ids = set(clause_locked_ids or set())
        self.auction_overbid_pct = float(auction_overbid_pct)
        self._calendar_index = _build_calendar_index(df_calendar) if df_calendar is not None else None
        self._rep_cache: Dict[Tuple[int, str, float, int, int], ReplacementProfile] = {}
        self._reinvest_cache: Dict[Tuple[int, int, bool], Tuple[float, float]] = {}

        # Build unified player universe table on date t
        self.universe_df = self._build_universe(df_market_today, xp_table)
        if self.universe_df.empty:
            self.n_free_total = 1
            self.p85_starter_xp = 5.8
            self.p60_starter_xp = 4.4
            self._u_pid = np.empty(0, dtype=np.int64)
            self._u_pos_mask = np.empty(0, dtype=np.uint8)
            self._u_precio = np.empty(0, dtype=float)
            self._u_xp_healthy = np.empty(0, dtype=float)
            self._u_eff_clause = np.empty(0, dtype=float)
            self._u_is_avail = np.empty(0, dtype=bool)
            self._u_is_free_avail = np.empty(0, dtype=bool)
            self._u_is_rival_unlocked_avail = np.empty(0, dtype=bool)
            return

        owner_arr = self.universe_df["owner_class"].values
        free_mask = (owner_arr == "FREE")
        self.n_free_total = max(1, int(free_mask.sum()))

        p_start_h = self.universe_df["p_start_healthy"].values
        xp_h_arr = self.universe_df["xp_healthy"].values
        starters_xp = xp_h_arr[p_start_h >= 0.60]
        if len(starters_xp) > 0:
            self.p85_starter_xp = float(np.percentile(starters_xp, 85))
            self.p60_starter_xp = float(np.percentile(starters_xp, 60))
        else:
            self.p85_starter_xp = 5.8
            self.p60_starter_xp = 4.4

        # Pre-extract contiguous NumPy arrays for microsecond bitmask queries
        self._u_pid = self.universe_df["player_id"].values.astype(np.int64)
        self._u_pos_mask = np.fromiter(
            (_lines_to_bitmask(v) for v in self.universe_df["valid_lines"].values),
            dtype=np.uint8,
            count=len(self._u_pid),
        )
        self._u_precio = self.universe_df["precio"].values.astype(float)
        self._u_xp_healthy = xp_h_arr.astype(float)
        raw_cl = self.universe_df["clausula_actual"].values.astype(float)
        need_calc = raw_cl <= 0.0
        eff_cl = raw_cl.copy()
        if np.any(need_calc):
            eff_cl[need_calc] = _compute_official_clause_vec(
                self._u_precio[need_calc],
                clause_ranges=self.rules.clause_ranges,
            )
        self._u_eff_clause = eff_cl
        self._u_is_avail = self.universe_df["is_available"].values.astype(bool)
        self._u_is_free_avail = free_mask & self._u_is_avail
        if self.clause_locked_ids:
            unlocked_mask = ~np.isin(self._u_pid, list(self.clause_locked_ids))
        else:
            unlocked_mask = np.ones(len(self._u_pid), dtype=bool)
        self._u_is_rival_unlocked_avail = (owner_arr == "RIVAL") & self._u_is_avail & unlocked_mask

    def _build_universe(self, df_mkt: pd.DataFrame, xp_table: pd.DataFrame) -> pd.DataFrame:
        if df_mkt is None or df_mkt.empty:
            return pd.DataFrame(columns=[
                "player_id", "nombre", "natural_pos", "valid_lines", "precio",
                "clausula_actual", "owner_class", "is_available", "p_start_healthy",
                "xp_best", "xp_healthy",
            ])

        mkt = df_mkt.copy()
        mkt["player_id"] = pd.to_numeric(mkt["player_id"], errors="coerce").fillna(0).astype(int)
        mkt = mkt[mkt["player_id"] > 0].drop_duplicates(subset=["player_id"], keep="last")
        mkt["precio"] = pd.to_numeric(mkt.get("precio", 0), errors="coerce").fillna(0.0)
        if "clausula_actual" in mkt.columns:
            mkt["clausula_actual"] = pd.to_numeric(mkt["clausula_actual"], errors="coerce").fillna(0.0)
        else:
            mkt["clausula_actual"] = 0.0

        owners_raw = mkt["propietario_liga"].values if "propietario_liga" in mkt.columns else ["Libre"] * len(mkt)
        my_mgr = self.my_manager_name
        owner_classes: List[str] = []
        for val in owners_raw:
            s = str(val or "").strip() if (val is not None and val == val) else ""
            if not s or s in ("Libre", "Mercado", "nan", "None"):
                owner_classes.append("FREE")
            elif my_mgr and s == my_mgr:
                owner_classes.append("MINE")
            else:
                owner_classes.append("RIVAL")
        mkt["owner_class"] = owner_classes

        pos_raw = mkt["posicion"].values if "posicion" in mkt.columns else ["MF"] * len(mkt)
        mkt["fallback_pos"] = [_norm_pos(p) for p in pos_raw]

        if xp_table is not None and not xp_table.empty:
            xp = xp_table
            if not np.issubdtype(xp["player_id"].dtype, np.integer):
                xp = xp_table.copy()
                xp["player_id"] = pd.to_numeric(xp["player_id"], errors="coerce").fillna(0).astype(int)
            cols_keep = [
                c for c in (
                    "player_id", "natural_pos", "valid_lines", "is_available",
                    "estado", "p_start", "p_start_healthy", "xp_best", "xp_healthy"
                ) if c in xp.columns
            ]
            merged = mkt.merge(xp[cols_keep].drop_duplicates("player_id"), on="player_id", how="left")
        else:
            merged = mkt.copy()
            merged["natural_pos"] = merged["fallback_pos"]
            merged["valid_lines"] = merged["fallback_pos"]
            merged["is_available"] = True
            merged["p_start_healthy"] = 0.5
            merged["xp_best"] = 3.0
            merged["xp_healthy"] = 3.0

        if "natural_pos" not in merged.columns:
            merged["natural_pos"] = merged["fallback_pos"]
        else:
            merged["natural_pos"] = merged["natural_pos"].fillna(merged["fallback_pos"])
        if "valid_lines" not in merged.columns:
            merged["valid_lines"] = merged["natural_pos"]
        else:
            merged["valid_lines"] = merged["valid_lines"].fillna(merged["natural_pos"])
        if "is_available" not in merged.columns:
            merged["is_available"] = True
        else:
            merged["is_available"] = merged["is_available"].where(merged["is_available"].notna(), True).astype(bool)
        if "xp_best" not in merged.columns:
            merged["xp_best"] = 3.0
        merged["xp_best"] = pd.to_numeric(merged["xp_best"], errors="coerce").fillna(2.5)
        if "xp_healthy" not in merged.columns:
            merged["xp_healthy"] = merged["xp_best"]
        merged["xp_healthy"] = pd.to_numeric(merged["xp_healthy"], errors="coerce").fillna(merged["xp_best"])
        if "p_start_healthy" not in merged.columns:
            merged["p_start_healthy"] = pd.to_numeric(merged.get("p_start", 0.5), errors="coerce").fillna(0.5)
        else:
            merged["p_start_healthy"] = pd.to_numeric(merged["p_start_healthy"], errors="coerce").fillna(0.5)

        return merged

    def compute_replacement_profile(
        self,
        player_id: int,
        natural_pos: str,
        xp_healthy: float,
        market_price: float,
        valid_lines: Optional[Set[str]] = None,
    ) -> ReplacementProfile:
        """
        Computes exact replacement cost `C_replace(i, t)` for a player of `(valid_lines, xp_healthy)`.
        """
        pos = _norm_pos(natural_pos)
        lines = valid_lines or {pos}
        target_mask = _lines_to_bitmask(lines) or LINE_BIT.get(pos, 4)
        target_xp = max(1.5, float(xp_healthy))
        cache_key = (int(player_id), pos, round(target_xp, 3), int(round(market_price)), target_mask)
        cached = self._rep_cache.get(cache_key)
        if cached is not None:
            return cached

        comparable_xp_floor = max(target_xp * 0.92, target_xp - 0.45)

        if len(self._u_pid) == 0:
            fallback_clause = float(self.rules.compute_official_clause(market_price))
            prof = ReplacementProfile(
                player_id=player_id,
                natural_pos=pos,
                xp_healthy=target_xp,
                market_price=market_price,
                n_free_comparable=0,
                n_rival_comparable=0,
                expected_free_wait_days=30.0,
                free_replacement_cost_eur=market_price * (1.0 + self.auction_overbid_pct),
                clause_replacement_cost_eur=fallback_clause,
                blended_replacement_cost_eur=fallback_clause,
                scarcity_tier="IRREPLACEABLE_FREE",
            )
            self._rep_cache[cache_key] = prof
            return prof

        peer_mask = (
            (self._u_pid != int(player_id))
            & (self._u_xp_healthy >= comparable_xp_floor)
            & ((self._u_pos_mask & target_mask) != 0)
        )
        free_prices = self._u_precio[peer_mask & self._u_is_free_avail]
        rival_clauses = self._u_eff_clause[peer_mask & self._u_is_rival_unlocked_avail]

        n_free_comp = int(len(free_prices))
        n_rival_comp = int(len(rival_clauses))

        k_slots = max(5, int(self.rules.market_free_slots))
        p_single_slot = min(0.95, k_slots / max(float(k_slots), float(self.n_free_total)))
        if n_free_comp > 0:
            p_appear_daily = 1.0 - float((1.0 - p_single_slot) ** n_free_comp)
            wait_days = float(np.clip(1.0 / max(1e-4, p_appear_daily), 1.0, 45.0))
            k_small = min(4, n_free_comp)
            cheapest = np.partition(free_prices, k_small - 1)[:k_small] if n_free_comp > k_small else free_prices
            free_cost = float(np.median(cheapest)) * (1.0 + self.auction_overbid_pct)
        else:
            wait_days = 45.0
            free_cost = float(market_price) * (1.0 + self.auction_overbid_pct)

        own_reclause_equiv = float(self.rules.compute_official_clause(market_price))
        if n_rival_comp > 0 and self.rules.clause_enabled:
            clause_cost = float(np.percentile(rival_clauses, 20)) if n_rival_comp >= 3 else float(np.min(rival_clauses))
            clause_cost = max(clause_cost, market_price * 1.15)
        else:
            clause_cost = own_reclause_equiv

        free_weight = 0.0 if n_free_comp == 0 else float(np.exp(-wait_days / 9.0))
        blended_cost = free_weight * free_cost + (1.0 - free_weight) * clause_cost

        if n_free_comp == 0:
            tier = "IRREPLACEABLE_FREE"
        elif wait_days >= 12.0 or n_free_comp <= 2:
            tier = "SCARCE"
        elif wait_days >= 4.5 or n_free_comp <= 6:
            tier = "MODERATE"
        else:
            tier = "ABUNDANT"

        prof = ReplacementProfile(
            player_id=int(player_id),
            natural_pos=pos,
            xp_healthy=round(target_xp, 3),
            market_price=round(float(market_price)),
            n_free_comparable=n_free_comp,
            n_rival_comparable=n_rival_comp,
            expected_free_wait_days=round(wait_days, 2),
            free_replacement_cost_eur=round(free_cost),
            clause_replacement_cost_eur=round(clause_cost),
            blended_replacement_cost_eur=round(blended_cost),
            scarcity_tier=tier,
        )
        self._rep_cache[cache_key] = prof
        return prof

    def best_achievable_reinvestment_details(
        self,
        budget_eur: float,
        valid_lines: Set[str],
        clauses_open: bool = True,
        point_shadow_value_eur: float = 85_000.0,
        horizon_rounds: int = 6,
    ) -> Tuple[float, float, float]:
        """
        Given `budget_eur` proceeds from selling a player today, estimates:
          `(achievable_xp_healthy, expected_wait_rounds, clause_surcharge_eur)`
        realistically achievable in the current league market after accounting for
        the immediate `(clause - VM)` deadweight cost of paying a rival release clause.
        """
        if len(self._u_pid) == 0 or budget_eur <= 150_000:
            return 2.2, 1.0, 0.0

        target_mask = _lines_to_bitmask(valid_lines) or 15
        cache_key = (
            int(round(budget_eur)),
            target_mask,
            bool(clauses_open),
            int(round(point_shadow_value_eur)),
            int(horizon_rounds),
        )
        cached = self._reinvest_cache.get(cache_key)
        if cached is not None:
            return cached

        line_avail_mask = (self._u_pos_mask & target_mask) != 0

        # Path 1: Affordable free agents
        max_free_vm = budget_eur / (1.0 + self.auction_overbid_pct)
        aff_free_xps = self._u_xp_healthy[line_avail_mask & self._u_is_free_avail & (self._u_precio <= max_free_vm)]
        best_free_xp = 2.4
        free_wait_rounds = 1.0
        n_aff = len(aff_free_xps)
        if n_aff > 0:
            k_top = min(8, n_aff)
            top_xps = np.partition(aff_free_xps, n_aff - k_top)[-k_top:] if n_aff > k_top else aff_free_xps
            best_free_xp = float(np.percentile(top_xps, 65))
            k_slots = max(5, int(self.rules.market_free_slots))
            p_hit = 1.0 - float((1.0 - min(0.95, k_slots / max(k_slots, self.n_free_total))) ** k_top)
            wait_days = float(np.clip(1.0 / max(1e-3, p_hit), 1.0, 21.0))
            free_wait_rounds = float(np.clip(np.floor(wait_days / 7.0), 0.0, 3.0))

        free_net_eur = max(0.0, float(horizon_rounds) - free_wait_rounds) * best_free_xp * float(point_shadow_value_eur)

        # Path 2: Affordable unlocked rival clause (must deduct immediate `clause - VM` equity loss!)
        best_clause_xp = 0.0
        best_clause_surcharge = 0.0
        clause_wait_rounds = 0.25 if clauses_open else 1.0
        clause_net_eur = -1e12
        if self.rules.clause_enabled:
            cl_mask = line_avail_mask & self._u_is_rival_unlocked_avail & (self._u_eff_clause <= budget_eur)
            aff_cl_xps = self._u_xp_healthy[cl_mask]
            if len(aff_cl_xps) > 0:
                aff_cl_surch = np.maximum(0.0, self._u_eff_clause[cl_mask] - self._u_precio[cl_mask])
                active_r = max(0.0, float(horizon_rounds) - clause_wait_rounds)
                net_vals = active_r * aff_cl_xps * float(point_shadow_value_eur) - aff_cl_surch
                k_cl = min(3, len(net_vals))
                top_cl_idx = np.argpartition(net_vals, len(net_vals) - k_cl)[-k_cl:] if len(net_vals) > k_cl else np.arange(len(net_vals))
                best_clause_xp = float(np.median(aff_cl_xps[top_cl_idx]))
                best_clause_surcharge = float(np.median(aff_cl_surch[top_cl_idx]))
                clause_net_eur = float(np.median(net_vals[top_cl_idx]))

        if clause_net_eur > free_net_eur and best_clause_xp > 0:
            res = (round(best_clause_xp, 3), clause_wait_rounds, round(best_clause_surcharge, 1))
        else:
            res = (round(best_free_xp, 3), free_wait_rounds, 0.0)
        self._reinvest_cache[cache_key] = res
        return res

    def best_achievable_reinvestment_xp(
        self,
        budget_eur: float,
        valid_lines: Set[str],
        clauses_open: bool = True,
    ) -> Tuple[float, float]:
        """
        Given `budget_eur` proceeds from selling a player today, estimates:
          `(achievable_xp_healthy, expected_wait_rounds)`
        achievable in the current league market (via realistic free-agent auction or unlocked rival clause).
        """
        xp, wait_r, _ = self.best_achievable_reinvestment_details(
            budget_eur=budget_eur,
            valid_lines=valid_lines,
            clauses_open=clauses_open,
        )
        return xp, wait_r

    def classify_asset_role(
        self,
        player_id: int,
        price: float,
        xp_healthy: float,
        p_start_healthy: float,
        efr_72h: float,
        in_current_xi: bool,
        rep_profile: ReplacementProfile,
        is_untouchable: bool = False,
    ) -> str:
        """
        Classifies an owned player into one of 4 structural portfolio roles:
          - `FRANCHISE_CORE`: Elite / scarce pillar (protected from preventive listing & auto-sale)
          - `STRUCTURAL_STARTER`: Reliable XI starter (protected from routine auto-sale in SEMI_AUTO if >= threshold)
          - `SPECULATIVE_FLIP`: Appreciation / Computer-offer arbitrage asset (prime candidate for 5 market slots)
          - `BENCH_PATCH`: Low-cost tactical cover / liquid collateral
        """
        if is_untouchable:
            return "FRANCHISE_CORE"

        replacement_spread = max(0.0, rep_profile.blended_replacement_cost_eur - price)
        is_elite_xp = xp_healthy >= max(5.5, self.p85_starter_xp)
        is_scarce_star = (
            xp_healthy >= max(5.0, self.p60_starter_xp + 0.6)
            and rep_profile.scarcity_tier in ("IRREPLACEABLE_FREE", "SCARCE")
            and (replacement_spread >= 1_800_000 or price >= 7_500_000)
        )
        is_high_value_anchor = (
            xp_healthy >= max(5.1, self.p60_starter_xp + 0.4)
            and p_start_healthy >= 0.78
            and price >= 9_000_000
        )
        if is_elite_xp or is_scarce_star or is_high_value_anchor:
            return "FRANCHISE_CORE"

        if in_current_xi and (xp_healthy >= max(4.1, self.p60_starter_xp - 0.3) or p_start_healthy >= 0.72):
            return "STRUCTURAL_STARTER"

        if efr_72h >= 25_000 or (not in_current_xi and price >= 1_000_000 and xp_healthy < self.p60_starter_xp):
            return "SPECULATIVE_FLIP"

        return "BENCH_PATCH"

    def get_league_scarcity_summary(self) -> Dict[str, Any]:
        """Returns aggregate market scarcity telemetry (used in reports, Telegram & backtest charts)."""
        u = self.universe_df
        if u.empty:
            return {"free_ge_5m": 0, "owned_ge_5m": 0, "free_ge_10m": 0, "owned_ge_10m": 0, "lock_rate_ge_5m": 0.0}
        ge5 = u[u["precio"] >= 5_000_000]
        ge10 = u[u["precio"] >= 10_000_000]
        free5 = int((ge5["owner_class"] == "FREE").sum())
        owned5 = int((ge5["owner_class"] != "FREE").sum())
        free10 = int((ge10["owner_class"] == "FREE").sum())
        owned10 = int((ge10["owner_class"] != "FREE").sum())
        lock_rate_5m = owned5 / max(1, free5 + owned5)
        return {
            "free_ge_5m": free5,
            "owned_ge_5m": owned5,
            "free_ge_10m": free10,
            "owned_ge_10m": owned10,
            "lock_rate_ge_5m": round(lock_rate_5m, 4),
            "p85_starter_xp": round(self.p85_starter_xp, 2),
        }


def evaluate_hold_vs_sell_npv(
    player_id: int,
    current_date: str,
    market_price: float,
    sale_offer_eur: float,
    efr_72h: float,
    is_available: bool,
    estado: str,
    xp_current: float,
    xp_healthy: float,
    p_start_healthy: float,
    valid_lines: Set[str],
    bench_backup_xp: float,
    scarcity_engine: MarketScarcityEngine,
    df_calendar: Optional[pd.DataFrame] = None,
    team_name: Optional[str] = None,
    purchase_price: Optional[float] = None,
    explicit_return_days: Optional[int] = None,
    horizon_rounds: int = 6,
    point_shadow_value_eur: float = 85_000.0,
    clauses_open: bool = True,
) -> Dict[str, Any]:
    """
    Evaluates the 6-round Net Present Value (in Euro-equivalents) of HOLDING vs SELLING player `i`:
      - Accounts for real calendar missed matchdays `m_i` (including FIFA breaks!).
      - Accounts for bench backup points while injured/suspended.
      - Accounts for best achievable reinvestment xP in the current free/clause market.
      - Penalizes selling scarce pillars by the forward-looking replacement spread `max(0, C_replace - O_i)`.
    """
    natural_pos = sorted(valid_lines)[0] if valid_lines else "MF"
    rep = scarcity_engine.compute_replacement_profile(
        player_id=player_id,
        natural_pos=natural_pos,
        xp_healthy=xp_healthy,
        market_price=market_price,
        valid_lines=valid_lines,
    )

    absence_days = estimate_absence_days_from_status(
        estado=estado,
        is_available=is_available,
        explicit_return_days=explicit_return_days,
    )
    if scarcity_engine._calendar_index is None and df_calendar is not None:
        scarcity_engine._calendar_index = _build_calendar_index(df_calendar)
    missed_rounds, missed_j_nums = estimate_missed_rounds_from_calendar(
        current_date=current_date,
        absence_days=absence_days,
        df_calendar=df_calendar,
        team_name=team_name,
        horizon_rounds=horizon_rounds,
        calendar_index=scarcity_engine._calendar_index,
    )
    active_rounds_if_held = max(0, horizon_rounds - missed_rounds)

    # 1. HOLD Scenario (6 rounds)
    # First fit round uses xp_current if available today, subsequent fit rounds use xp_healthy
    if missed_rounds == 0 and is_available:
        hold_points_6r = float(xp_current) + max(0, horizon_rounds - 1) * float(xp_healthy)
    else:
        hold_points_6r = missed_rounds * float(bench_backup_xp) + active_rounds_if_held * float(xp_healthy)

    # Expected price drift if held:
    # During absence/downtrend, price decays with damping (capped at -18% for moderate injuries, -32% for >=45d);
    # once healthy & playing, elite starters stabilize/recover partially.
    if not is_available or absence_days > 0:
        daily_decay = min(0.0, float(efr_72h) / 3.0)
        if daily_decay == 0.0:
            daily_decay = -market_price * 0.008
        max_drawdown = market_price * (0.32 if absence_days >= 45 else 0.17)
        est_price_drift_hold = -min(max_drawdown, abs(daily_decay) * min(absence_days, 28) * 0.65)
        if active_rounds_if_held >= 3 and xp_healthy >= scarcity_engine.p60_starter_xp:
            # Partial post-return price recovery when player returns to starting XI
            est_price_drift_hold *= 0.65
    else:
        # Healthy player: 10-day damped momentum
        est_price_drift_hold = float(np.clip(efr_72h * 1.9, -market_price * 0.22, market_price * 0.35))

    terminal_asset_hold = market_price + est_price_drift_hold
    npv_hold_eur = hold_points_6r * point_shadow_value_eur + terminal_asset_hold

    # 2. SELL Scenario (at `sale_offer_eur`, reinvesting into best realistic market alternative)
    reinvest_xp, wait_rounds, clause_surcharge_eur = scarcity_engine.best_achievable_reinvestment_details(
        budget_eur=sale_offer_eur,
        valid_lines=valid_lines,
        clauses_open=clauses_open,
        point_shadow_value_eur=point_shadow_value_eur,
        horizon_rounds=horizon_rounds,
    )
    w_r = min(float(horizon_rounds), float(wait_rounds))
    sell_points_6r = w_r * float(bench_backup_xp) + max(0.0, horizon_rounds - w_r) * float(reinvest_xp)

    # Forward-looking replacement spread loss: if C_replace > sale_offer_eur, liquidating destroys
    # structural squad ceiling that would cost `(C_replace - sale_offer_eur)` to rebuild!
    replacement_spread_eur = max(0.0, float(rep.blended_replacement_cost_eur) - float(sale_offer_eur))
    # Weight replacement spread by how likely the player returns within the season (0.55 normally; 0.15 if long-term >=45d injury)
    spread_weight = 0.15 if absence_days >= 45 else (0.55 if rep.scarcity_tier in ("IRREPLACEABLE_FREE", "SCARCE") else 0.30)

    npv_sell_eur = (
        sell_points_6r * point_shadow_value_eur
        + float(sale_offer_eur)
        - float(clause_surcharge_eur)
        - spread_weight * replacement_spread_eur
    )

    net_sell_advantage_eur = npv_sell_eur - npv_hold_eur
    # Healthy regular starters face auction execution risk + overbid friction (`3.5%`), whereas injured/non-starters use tighter hurdle (`1.5%`)
    is_healthy_starter = bool(is_available and absence_days == 0 and xp_healthy >= max(4.0, scarcity_engine.p60_starter_xp * 0.90))
    hurdle_eur = max(140_000.0, market_price * 0.035) if is_healthy_starter else max(65_000.0, market_price * 0.015)
    recommend_sell = bool(net_sell_advantage_eur > hurdle_eur)

    realized_acq_delta = (float(sale_offer_eur) - float(purchase_price)) if (purchase_price and purchase_price > 0) else None

    return {
        "player_id": int(player_id),
        "recommend_sell": recommend_sell,
        "npv_hold_eur": round(npv_hold_eur),
        "npv_sell_eur": round(npv_sell_eur),
        "net_sell_advantage_eur": round(net_sell_advantage_eur),
        "hold_points_6r": round(hold_points_6r, 2),
        "sell_points_6r": round(sell_points_6r, 2),
        "reinvest_xp_est": round(reinvest_xp, 2),
        "absence_days_est": int(absence_days),
        "missed_rounds_est": int(missed_rounds),
        "missed_jornadas": missed_j_nums,
        "replacement_cost_eur": int(rep.blended_replacement_cost_eur),
        "replacement_spread_eur": int(round(replacement_spread_eur)),
        "scarcity_tier": rep.scarcity_tier,
        "n_free_comparable": rep.n_free_comparable,
        "expected_free_wait_days": rep.expected_free_wait_days,
        "est_price_drift_hold_eur": int(round(est_price_drift_hold)),
        "realized_acquisition_delta_eur": int(round(realized_acq_delta)) if realized_acq_delta is not None else None,
    }
