"""
Strict Causal Data View & Walk-Forward EFR Price Predictor (`causal_clock.py`)
==============================================================================
Enforces hard temporal causality (`fecha <= current_date` and
`fecha_partido < f"{current_date} 18:00:00"`). Any attempt to access future
rows raises a `ValueError("Temporal Leakage Violation")`.
"""

import sqlite3
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from src.utils.numeric import safe_num


POST_MATCH_CALENDAR_COLS = [
    "goles_local",
    "goles_visitante",
    "posesion_local",
    "posesion_visitante",
    "tiros_local",
    "tiros_visitante",
    "tiros_puerta_local",
    "tiros_puerta_visitante",
    "corners_local",
    "corners_visitante",
    "entradas_local",
    "entradas_visitante",
    "precision_pase_local",
    "precision_pase_visitante",
    "mvp_partido_id",
    "mvp_partido_nombre",
]


class StrictCausalDataView:
    """
    Time-gated read-only view over `biwenger_history.db` that mathematically
    prevents any strategy component from reading future prices, bids, or matches.
    """

    def __init__(self, db_path: str = "./data/warehouse/biwenger_history.db"):
        self.db_path = db_path
        conn = sqlite3.connect(db_path)
        try:
            self._df_market = pd.read_sql("SELECT * FROM Historico_Continuo", conn)
            self._df_managers = pd.read_sql("SELECT * FROM Historico_Equipos_Diario", conn)
            self._df_bids = pd.read_sql("SELECT * FROM Pujas_Historico", conn)
            self._df_reports = pd.read_sql("SELECT * FROM Rendimiento_Jornadas", conn)
            self._df_calendar = pd.read_sql("SELECT * FROM Partidos_Calendario", conn)
            self._df_comuniate = pd.read_sql("SELECT * FROM Comuniate_Historico", conn)
        finally:
            conn.close()

        # Pre-normalize types & date keys once for fast O(1) slicing across 91 days
        self._df_market["fecha"] = self._df_market["fecha"].astype(str).str.slice(0, 10)
        self._df_market["player_id"] = pd.to_numeric(self._df_market["player_id"], errors="coerce").fillna(0).astype(int)
        for num_col in ("precio", "subida_24h", "diff_7d", "pct_compras_24h", "pct_ventas_24h", "presion_neta", "clausula_actual"):
            if num_col in self._df_market.columns:
                self._df_market[num_col] = pd.to_numeric(self._df_market[num_col], errors="coerce").fillna(0.0)

        self._market_by_date: Dict[str, pd.DataFrame] = {
            d: grp.copy() for d, grp in self._df_market.groupby("fecha", sort=True)
        }
        self.all_dates: List[str] = sorted(self._market_by_date.keys())

        self._df_managers["fecha"] = self._df_managers["fecha"].astype(str).str.slice(0, 10)
        self._managers_by_date: Dict[str, pd.DataFrame] = {
            d: grp.copy() for d, grp in self._df_managers.groupby("fecha", sort=True)
        }

        self._df_bids["bid_day"] = self._df_bids["fecha"].astype(str).str.slice(0, 10)
        self._df_reports["fecha_partido"] = self._df_reports["fecha_partido"].astype(str)
        self._df_reports["jornada_num_int"] = pd.to_numeric(self._df_reports["jornada_num"], errors="coerce").fillna(0).astype(int)

        self._df_calendar["fecha_hora"] = self._df_calendar["fecha_hora"].astype(str)
        self._df_calendar["jornada_num_int"] = pd.to_numeric(self._df_calendar["jornada_num"], errors="coerce").fillna(0).astype(int)
        self._df_calendar["part_int"] = pd.to_numeric(self._df_calendar["part"], errors="coerce").fillna(1).astype(int)

        self._df_comuniate["id_jornada_int"] = pd.to_numeric(self._df_comuniate["id_jornada"], errors="coerce").fillna(0).astype(int)
        self._comuniate_by_round: Dict[int, pd.DataFrame] = {
            int(jn): grp.copy() for jn, grp in self._df_comuniate.groupby("id_jornada_int")
        }

        # Map each jornada_num -> earliest match kickoff date so Comuniate probable XIs
        # are only exposed within the 6-day lead-up window before that round's kickoff!
        self._round_earliest_date: Dict[int, str] = {}
        cal_p1 = self._df_calendar[self._df_calendar["part_int"] == 1]
        for jn, grp in cal_p1.groupby("jornada_num_int"):
            valid_dts = [s[:10] for s in grp["fecha_hora"].tolist() if len(s) >= 10]
            if valid_dts:
                self._round_earliest_date[int(jn)] = min(valid_dts)

        self.current_date: str = self.all_dates[0] if self.all_dates else "2026-08-01"
        self.decision_cutoff_ts: str = f"{self.current_date} 12:00:00"

    def set_date(self, date_str: str) -> None:
        d = str(date_str)[:10]
        self.current_date = d
        # Daytime decision cutoff at 12:00:00 (strictly prior to afternoon/evening kickoffs in both UTC and Europe/Madrid)
        self.decision_cutoff_ts = f"{d} 12:00:00"

    def _assert_no_leakage(self, requested_date: Optional[str]) -> str:
        target = str(requested_date)[:10] if requested_date is not None else self.current_date
        if target > self.current_date:
            raise ValueError(
                f"Temporal Leakage Violation: requested date '{target}' > current causal clock '{self.current_date}'"
            )
        return target

    def get_market_snapshot(self, as_of_date: Optional[str] = None) -> pd.DataFrame:
        target = self._assert_no_leakage(as_of_date)
        df = self._market_by_date.get(target)
        if df is not None:
            return df.copy()
        # Fallback to latest snapshot <= target
        prior_dates = [d for d in self.all_dates if d <= target]
        if not prior_dates:
            return pd.DataFrame()
        return self._market_by_date[prior_dates[-1]].copy()

    def get_market_dates_up_to(self, as_of_date: Optional[str] = None) -> List[str]:
        target = self._assert_no_leakage(as_of_date)
        return [d for d in self.all_dates if d <= target]

    def get_manager_snapshots(self, as_of_date: Optional[str] = None) -> pd.DataFrame:
        target = self._assert_no_leakage(as_of_date)
        df = self._managers_by_date.get(target)
        return df.copy() if df is not None else pd.DataFrame()

    def get_past_bids(self, as_of_date: Optional[str] = None) -> pd.DataFrame:
        target = self._assert_no_leakage(as_of_date)
        # Morning auctions resolve at 05:00-06:00 UTC, strictly before daytime decisions
        return self._df_bids[self._df_bids["bid_day"] <= target].copy()

    def get_past_match_reports(self, as_of_timestamp: Optional[str] = None) -> pd.DataFrame:
        cutoff = str(as_of_timestamp) if as_of_timestamp is not None else self.decision_cutoff_ts
        if cutoff[:10] > self.current_date:
            raise ValueError(
                f"Temporal Leakage Violation: requested match timestamp '{cutoff}' > current causal clock '{self.current_date}'"
            )
        mask = (self._df_reports["fecha_partido"] != "") & (self._df_reports["fecha_partido"] < cutoff)
        return self._df_reports[mask].copy()

    @property
    def df_calendar(self) -> pd.DataFrame:
        """Causally masked season schedule (post-match scores hidden for future matches)."""
        return self.get_calendar_causal()

    def get_calendar_causal(self) -> pd.DataFrame:
        """
        Returns full season schedule (`Partidos_Calendario`) with post-match scores
        and match stats masked out for all matches with `fecha_hora >= decision_cutoff_ts`.
        """
        cal = self._df_calendar[self._df_calendar["part_int"] == 1].copy()
        future_mask = cal["fecha_hora"] >= self.decision_cutoff_ts
        for col in POST_MATCH_CALENDAR_COLS:
            if col in cal.columns:
                if cal[col].dtype != object:
                    cal[col] = cal[col].astype(object)
                cal.loc[future_mask, col] = ""
        if "estado_partido" in cal.columns:
            cal.loc[future_mask, "estado_partido"] = "pending"
        return cal

    def get_comuniate_for_round(self, jornada_num: int) -> pd.DataFrame:
        """
        Exposes Comuniate probable lineups for `jornada_num` ONLY if `current_date`
        is within 7 days of that round's kickoff (or after).
        """
        jn = int(jornada_num)
        r_date = self._round_earliest_date.get(jn)
        if r_date:
            days_until = (pd.Timestamp(r_date) - pd.Timestamp(self.current_date)).days
            if days_until > 7:
                return pd.DataFrame()
        df = self._comuniate_by_round.get(jn)
        return df.copy() if df is not None else pd.DataFrame()


class CausalEFRPredictor:
    """
    Fast Walk-Forward 72-Hour Expected Financial Return (`efr_72h`) Predictor.
    Combines:
      - Tier-1 Autoregressive Inertia & Acceleration (`subida_24h(t)`, `subida_24h(t-1)`,
        streak persistence, `diff_7d / 7`) available across all 91 days (`2026-08-01..2026-10-30`).
      - Tier-2 Order-Flow Sentiment (`presion_neta`, `pct_compras_24h`, `pct_ventas_24h`)
        available from `2026-09-02` onward.
      - Fresh 48-hour post-match performance shock (`puntos_biwenger` in matches finished
        within `[t - 2d, t 18:00)`), capturing post-matchday demand waves early.
      - Ridge regression refitted causally every 7 days strictly on realized `(P_{s} - P_{s-3})`
        pairs with `s <= current_date`, blended with Biwenger's empirical inertia curve
        and quantized to `10,000 €` steps above the `150,000 €` price floor.
    """

    def __init__(self, causal_view: StrictCausalDataView):
        self.view = causal_view
        # Pre-extract fast lookup tables indexed by date -> array/dict (accessed ONLY via <= view.current_date)
        self._price_by_date: Dict[str, Dict[int, float]] = {}
        self._subida_by_date: Dict[str, Dict[int, float]] = {}
        self._diff7_by_date: Dict[str, Dict[int, float]] = {}
        self._presion_by_date: Dict[str, Dict[int, float]] = {}
        self._compras_by_date: Dict[str, Dict[int, float]] = {}

        for d in self.view.all_dates:
            snap = self.view._market_by_date[d]
            pids = snap["player_id"].values
            self._price_by_date[d] = dict(zip(pids, snap["precio"].values))
            self._subida_by_date[d] = dict(zip(pids, snap["subida_24h"].values))
            self._diff7_by_date[d] = dict(zip(pids, snap["diff_7d"].values))
            self._presion_by_date[d] = dict(zip(pids, snap["presion_neta"].values))
            self._compras_by_date[d] = dict(zip(pids, snap["pct_compras_24h"].values))

        self._ridge_weights: Optional[np.ndarray] = None
        self._last_fit_date: Optional[str] = None

    def _build_features_for_date(
        self,
        d_curr: str,
        d_prev1: Optional[str],
        d_prev2: Optional[str],
        pids: List[int],
    ) -> np.ndarray:
        """
        Feature matrix X (N x 6) using ONLY dates `<= d_curr`:
          f0: subida_24h(t)
          f1: acceleration = subida_24h(t) - subida_24h(t-1)
          f2: 3-day streak bonus = subida_24h(t) if (subida(t)>0 and subida(t-1)>0 and subida(t-2)>0) else 0
          f3: avg_7d_velocity = diff_7d(t) / 7.0
          f4: presion_eur_signal = (presion_neta(t) / 100.0) * min(price, 10M) * 0.045
          f5: compras_surge_eur = max(0, pct_compras_24h(t) - 15.0) * 1_800.0
        """
        p_map = self._price_by_date.get(d_curr, {})
        s0_map = self._subida_by_date.get(d_curr, {})
        s1_map = self._subida_by_date.get(d_prev1, {}) if d_prev1 else {}
        s2_map = self._subida_by_date.get(d_prev2, {}) if d_prev2 else {}
        d7_map = self._diff7_by_date.get(d_curr, {})
        pr_map = self._presion_by_date.get(d_curr, {})
        co_map = self._compras_by_date.get(d_curr, {})

        X = np.zeros((len(pids), 6), dtype=float)
        has_sentiment = (d_curr >= "2026-09-02")
        for i, pid in enumerate(pids):
            pr = float(p_map.get(pid, 150_000.0))
            s0 = float(s0_map.get(pid, 0.0))
            s1 = float(s1_map.get(pid, s0))
            s2 = float(s2_map.get(pid, 0.0))
            accel = s0 - s1
            streak = s0 if (s0 > 0 and s1 > 0 and s2 > 0) else (-abs(s0) if (s0 < 0 and s1 < 0 and s2 < 0) else 0.0)
            v7 = float(d7_map.get(pid, 0.0)) / 7.0
            X[i, 0] = s0
            X[i, 1] = accel
            X[i, 2] = streak
            X[i, 3] = v7
            if has_sentiment:
                pnet = float(pr_map.get(pid, 0.0))
                comp = float(co_map.get(pid, 0.0))
                X[i, 4] = (pnet / 100.0) * min(pr, 10_000_000.0) * 0.045
                X[i, 5] = max(0.0, comp - 15.0) * 1_800.0
        return X

    def _maybe_refit_causal_ridge(self, valid_dates: List[str]) -> None:
        curr_d = valid_dates[-1]
        if len(valid_dates) < 8:
            return
        if self._last_fit_date is not None:
            if (pd.Timestamp(curr_d) - pd.Timestamp(self._last_fit_date)).days < 7:
                return

        # Train ONLY on pairs (u -> u+3) where BOTH u and u+3 are <= curr_d!
        X_blocks = []
        y_blocks = []
        start_idx = max(2, len(valid_dates) - 28)
        for idx in range(start_idx, len(valid_dates) - 3):
            d_u = valid_dates[idx]
            d_target = valid_dates[idx + 3]  # strictly <= valid_dates[-1] == curr_d!
            p_u = self._price_by_date[d_u]
            p_t = self._price_by_date[d_target]
            common_pids = [pid for pid, val in p_u.items() if pid in p_t and val > 150_000]
            if not common_pids:
                continue
            X_u = self._build_features_for_date(
                d_u,
                valid_dates[idx - 1],
                valid_dates[idx - 2],
                common_pids,
            )
            y_u = np.array([p_t[pid] - p_u[pid] for pid in common_pids], dtype=float)
            X_blocks.append(X_u)
            y_blocks.append(y_u)

        if not X_blocks:
            return
        X_all = np.vstack(X_blocks)
        y_all = np.concatenate(y_blocks)
        # Closed-form L2 Ridge: (X^T X + lambda I)^{-1} X^T y
        reg = 1e11 * np.eye(X_all.shape[1])
        try:
            w = np.linalg.solve(X_all.T @ X_all + reg, X_all.T @ y_all)
            # Sanity clip weights to prevent instability early in season
            self._ridge_weights = np.clip(w, -1.5, 4.0)
            self._last_fit_date = curr_d
        except np.linalg.LinAlgError:
            pass

    def predict_efr_72h_and_shocks(self) -> Tuple[Dict[int, float], Dict[int, float]]:
        """
        Computes causal `efr_72h` (EUR) and `recent_match_shock_map` (points in last 60h)
        strictly using data available at `self.view.current_date`.
        """
        valid_dates = self.view.get_market_dates_up_to()
        if not valid_dates:
            return {}, {}

        curr_d = valid_dates[-1]
        d_prev1 = valid_dates[-2] if len(valid_dates) >= 2 else None
        d_prev2 = valid_dates[-3] if len(valid_dates) >= 3 else None

        self._maybe_refit_causal_ridge(valid_dates)

        # Fresh 60-hour match shock map (strictly < decision_cutoff_ts)
        past_rep = self.view.get_past_match_reports()
        shock_map: Dict[int, float] = {}
        if not past_rep.empty:
            min_shock_ts = (pd.Timestamp(curr_d) - pd.Timedelta(hours=54)).strftime("%Y-%m-%d %H:%M:%S")
            fresh = past_rep[past_rep["fecha_partido"] >= min_shock_ts]
            if not fresh.empty:
                for _, rr in fresh.iterrows():
                    pid = int(safe_num(rr.get("player_id", 0), 0))
                    pts = safe_num(rr.get("puntos_biwenger", 0), 0.0)
                    if pid > 0:
                        shock_map[pid] = max(shock_map.get(pid, -99.0), pts)

        p_curr_map = self._price_by_date.get(curr_d, {})
        pids = list(p_curr_map.keys())
        X_curr = self._build_features_for_date(curr_d, d_prev1, d_prev2, pids)

        # Prior analytical Biwenger 72h inertia curve
        prior_w = np.array([2.55, 0.55, 0.30, 0.35, 0.75, 0.60], dtype=float)
        pred_prior = X_curr @ prior_w

        if self._ridge_weights is not None:
            pred_ridge = X_curr @ self._ridge_weights
            raw_efr = 0.55 * pred_ridge + 0.45 * pred_prior
        else:
            raw_efr = pred_prior

        efr_map: Dict[int, float] = {}
        for i, pid in enumerate(pids):
            price = float(p_curr_map.get(pid, 150_000.0))
            val = float(raw_efr[i])

            # Add fresh post-match shock impulse if market hasn't priced it in yet
            if pid in shock_map:
                sh_pts = shock_map[pid]
                s0 = X_curr[i, 0]
                if sh_pts >= 9.0 and s0 < 90_000:
                    val += (sh_pts - 6.0) * 18_000.0
                elif sh_pts <= -2.0 and s0 > -40_000:
                    val -= abs(sh_pts) * 14_000.0

            # Hard Biwenger constraints: price cannot drop below 150,000 € floor,
            # nor surge > +28% in 72h (except sub-600k micro breakouts up to +380k)
            min_delta = 150_000.0 - price
            max_delta = max(380_000.0, price * 0.28)
            clipped = float(np.clip(val, min_delta, max_delta))
            # Quantize to Biwenger's 10,000 € daily price grid
            quantized = round(clipped / 10_000.0) * 10_000.0
            efr_map[int(pid)] = float(quantized)

        return efr_map, shock_map
