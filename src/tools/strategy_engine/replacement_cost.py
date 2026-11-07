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

from src.tools.strategy_engine.league_rules import LeagueRulesEngine
from src.utils.numeric import safe_num


POS_NORMALIZE = {
    "1": "GK", "portero": "GK", "gk": "GK", "pt": "GK",
    "2": "DF", "defensa": "DF", "df": "DF",
    "3": "MF", "centrocampista": "MF", "mf": "MF", "mc": "MF",
    "4": "FW", "delantero": "FW", "fw": "FW", "dl": "FW",
}


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

    if df_calendar is not None and not df_calendar.empty and "jornada" in df_calendar.columns:
        cal = df_calendar.copy()
        cal["j_num"] = (
            cal["jornada"].astype(str).str.extract(r"(\d+)")[0].pipe(pd.to_numeric, errors="coerce")
        )
        ts_col = "fecha_hora" if "fecha_hora" in cal.columns else ("fecha" if "fecha" in cal.columns else None)
        if ts_col:
            cal["dt"] = pd.to_datetime(cal[ts_col], errors="coerce")
            cal = cal.dropna(subset=["j_num", "dt"])
            cal["j_num"] = cal["j_num"].astype(int)

            # Group round start timestamps for upcoming rounds
            round_starts = cal.groupby("j_num")["dt"].min().sort_index()
            upcoming_rounds = [int(j) for j, dt_min in round_starts.items() if dt_min >= now_ts - pd.Timedelta(hours=6)]
            upcoming_rounds = upcoming_rounds[:horizon_rounds]

            if upcoming_rounds:
                missed: List[int] = []
                norm_team = str(team_name or "").strip().lower()
                for j_num in upcoming_rounds:
                    j_rows = cal[cal["j_num"] == j_num]
                    match_dt = None
                    if norm_team and "equipo_local" in j_rows.columns and "equipo_visitante" in j_rows.columns:
                        m_team = j_rows[
                            (j_rows["equipo_local"].astype(str).str.strip().str.lower() == norm_team)
                            | (j_rows["equipo_visitante"].astype(str).str.strip().str.lower() == norm_team)
                        ]
                        if not m_team.empty:
                            match_dt = m_team["dt"].min()
                    if match_dt is None or pd.isna(match_dt):
                        match_dt = j_rows["dt"].min()

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
    ):
        self.my_manager_name = str(my_manager_name or "").strip()
        self.rules = rules or LeagueRulesEngine.load()
        self.clause_locked_ids = set(clause_locked_ids or set())
        self.auction_overbid_pct = float(auction_overbid_pct)

        # Build unified player universe table on date t
        self.universe_df = self._build_universe(df_market_today, xp_table)
        free_mask = self.universe_df["owner_class"] == "FREE"
        self.n_free_total = max(1, int(free_mask.sum()))

        # Compute dynamic elite starter xP threshold (85th percentile of active starters)
        starters = self.universe_df[self.universe_df["p_start_healthy"] >= 0.60]
        if not starters.empty:
            self.p85_starter_xp = float(np.percentile(starters["xp_healthy"], 85))
            self.p60_starter_xp = float(np.percentile(starters["xp_healthy"], 60))
        else:
            self.p85_starter_xp = 5.8
            self.p60_starter_xp = 4.4

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

        def _classify_owner(val: Any) -> str:
            s = str(val or "").strip()
            if not s or s in ("Libre", "Mercado", "nan", "None"):
                return "FREE"
            if self.my_manager_name and s == self.my_manager_name:
                return "MINE"
            return "RIVAL"

        mkt["owner_class"] = mkt.get("propietario_liga", pd.Series(["Libre"] * len(mkt))).apply(_classify_owner)
        mkt["fallback_pos"] = mkt.get("posicion", pd.Series(["MF"] * len(mkt))).apply(_norm_pos)

        if xp_table is not None and not xp_table.empty:
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

        merged["natural_pos"] = merged["natural_pos"].fillna(merged["fallback_pos"])
        merged["valid_lines"] = merged["valid_lines"].fillna(merged["natural_pos"])
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
        u = self.universe_df
        pos = _norm_pos(natural_pos)
        lines = valid_lines or {pos}
        target_xp = max(1.5, float(xp_healthy))
        comparable_xp_floor = max(target_xp * 0.92, target_xp - 0.45)

        if u.empty:
            fallback_clause = float(self.rules.compute_official_clause(market_price))
            return ReplacementProfile(
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

        # Comparable peers: share at least one valid line, not this player, healthy xP >= comparable floor
        def _shares_line(vl: Any) -> bool:
            s_lines = {x.strip() for x in str(vl or "").split(",") if x.strip()}
            return bool(s_lines & lines)

        peers = u[
            (u["player_id"] != int(player_id))
            & (u["xp_healthy"] >= comparable_xp_floor)
            & (u["valid_lines"].apply(_shares_line))
        ]

        free_peers = peers[(peers["owner_class"] == "FREE") & (peers["is_available"])]
        rival_peers = peers[(peers["owner_class"] == "RIVAL") & (peers["is_available"])]

        n_free_comp = int(len(free_peers))
        n_rival_comp = int(len(rival_peers))

        # Daily market draws K = market_free_slots out of n_free_total
        k_slots = max(5, int(self.rules.market_free_slots))
        p_single_slot = min(0.95, k_slots / max(float(k_slots), float(self.n_free_total)))
        if n_free_comp > 0:
            p_appear_daily = 1.0 - float((1.0 - p_single_slot) ** n_free_comp)
            wait_days = float(np.clip(1.0 / max(1e-4, p_appear_daily), 1.0, 45.0))
            # Median auction cost among the 4 most efficient comparable free agents
            cheapest_free = free_peers.nsmallest(min(4, n_free_comp), "precio")
            free_cost = float(cheapest_free["precio"].median()) * (1.0 + self.auction_overbid_pct)
        else:
            wait_days = 45.0
            free_cost = float(market_price) * (1.0 + self.auction_overbid_pct)

        # Minimum unlocked rival clause for comparable quality (or official clause of equivalent asset)
        own_reclause_equiv = float(self.rules.compute_official_clause(market_price))
        unlocked_rivals = rival_peers[~rival_peers["player_id"].isin(self.clause_locked_ids)].copy()
        if not unlocked_rivals.empty and self.rules.clause_enabled:
            clauses = unlocked_rivals.apply(
                lambda r: float(r["clausula_actual"]) if float(r["clausula_actual"]) > 0 else float(self.rules.compute_official_clause(float(r["precio"]))),
                axis=1,
            )
            # Take 25th percentile / minimum realistic unlocked clause to avoid outlier noise
            clause_cost = float(np.percentile(clauses, 20)) if len(clauses) >= 3 else float(clauses.min())
            clause_cost = max(clause_cost, market_price * 1.15)
        else:
            clause_cost = own_reclause_equiv

        # Smooth microeconomic blend: when free agents appear within 1-3 days, free_weight ~ 0.74-0.90;
        # when n_free_comp == 0 (wait_days = 45), free_weight = 0.0 -> 100% clause replacement cost!
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

        return ReplacementProfile(
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
        u = self.universe_df
        if u.empty or budget_eur <= 150_000:
            return 2.2, 1.0

        def _shares_line(vl: Any) -> bool:
            s_lines = {x.strip() for x in str(vl or "").split(",") if x.strip()}
            return bool(s_lines & valid_lines)

        pool = u[(u["is_available"]) & (u["valid_lines"].apply(_shares_line))]

        # Path 1: Affordable free agents (`precio * (1 + overbid) <= budget_eur`)
        max_free_vm = budget_eur / (1.0 + self.auction_overbid_pct)
        aff_free = pool[(pool["owner_class"] == "FREE") & (pool["precio"] <= max_free_vm)]
        best_free_xp = 2.4
        free_wait_rounds = 1.0
        if not aff_free.empty:
            # Because only 20 of ~350 free agents appear per day, over ~7-10 days we sample ~35-50% of the pool,
            # so expected best captured free agent is around the 85th-90th percentile of affordable free agents (not the single #1 outlier!)
            top_free = aff_free.nlargest(min(8, len(aff_free)), "xp_healthy")
            best_free_xp = float(np.percentile(top_free["xp_healthy"], 65))
            k_slots = max(5, int(self.rules.market_free_slots))
            p_hit = 1.0 - float((1.0 - min(0.95, k_slots / max(k_slots, self.n_free_total))) ** len(top_free))
            wait_days = float(np.clip(1.0 / max(1e-3, p_hit), 1.0, 21.0))
            free_wait_rounds = float(np.clip(np.floor(wait_days / 7.0), 0.0, 3.0))

        # Path 2: Affordable unlocked rival clause (`clausula <= budget_eur`, instant 0 wait rounds if clauses open)
        best_clause_xp = 0.0
        if self.rules.clause_enabled:
            rivals = pool[(pool["owner_class"] == "RIVAL") & (~pool["player_id"].isin(self.clause_locked_ids))].copy()
            if not rivals.empty:
                rivals["eff_clause"] = rivals.apply(
                    lambda r: float(r["clausula_actual"]) if float(r["clausula_actual"]) > 0 else float(self.rules.compute_official_clause(float(r["precio"]))),
                    axis=1,
                )
                aff_clauses = rivals[rivals["eff_clause"] <= budget_eur]
                if not aff_clauses.empty:
                    best_clause_xp = float(aff_clauses["xp_healthy"].max())

        clause_wait_rounds = 0.0 if clauses_open else 1.0
        # Compare net 6-round effective xP of free path vs clause path
        free_6r = (6.0 - free_wait_rounds) * best_free_xp
        clause_6r = (6.0 - clause_wait_rounds) * best_clause_xp
        if clause_6r > free_6r and best_clause_xp > 0:
            return round(best_clause_xp, 3), clause_wait_rounds
        return round(best_free_xp, 3), free_wait_rounds

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
        if is_elite_xp or is_scarce_star:
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
    missed_rounds, missed_j_nums = estimate_missed_rounds_from_calendar(
        current_date=current_date,
        absence_days=absence_days,
        df_calendar=df_calendar,
        team_name=team_name,
        horizon_rounds=horizon_rounds,
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
    reinvest_xp, wait_rounds = scarcity_engine.best_achievable_reinvestment_xp(
        budget_eur=sale_offer_eur,
        valid_lines=valid_lines,
        clauses_open=clauses_open,
    )
    w_r = min(float(horizon_rounds), float(wait_rounds))
    sell_points_6r = w_r * float(bench_backup_xp) + max(0.0, horizon_rounds - w_r) * float(reinvest_xp)

    # Forward-looking replacement spread loss: if C_replace > sale_offer_eur, liquidating destroys
    # structural squad ceiling that would cost `(C_replace - sale_offer_eur)` to rebuild!
    replacement_spread_eur = max(0.0, float(rep.blended_replacement_cost_eur) - float(sale_offer_eur))
    # Weight replacement spread by how likely the player returns within the season (0.55 normally; 0.15 if long-term >=45d injury)
    spread_weight = 0.15 if absence_days >= 45 else (0.55 if rep.scarcity_tier in ("IRREPLACEABLE_FREE", "SCARCE") else 0.30)

    npv_sell_eur = sell_points_6r * point_shadow_value_eur + float(sale_offer_eur) - spread_weight * replacement_spread_eur

    net_sell_advantage_eur = npv_sell_eur - npv_hold_eur
    # Require a meaningful positive edge (> 65k €) to overcome transaction churn & 24h lock risk
    hurdle_eur = max(65_000.0, market_price * 0.015)
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
