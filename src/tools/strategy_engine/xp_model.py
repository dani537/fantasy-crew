"""
Walk-Forward Expected Points Engine (`ExpectedPointsEngine`)
============================================================
Calibrated specifically for AZ Finance's official scoring system:
`scoreID = 5` ("Mitjana AS i SofaScore") with active multiposition (`multiPos=True`)
and disabled coaches (`Pos 5` excluded).

Zero Lookahead Guarantee:
Every projection for date `t` and upcoming `jornada_num` ONLY consumes match
reports with `fecha_partido < t_cutoff` and daily market state `<= t`.
"""

import os
import unicodedata
from functools import lru_cache
from typing import Dict, List, Set, Optional, Tuple
import numpy as np
import pandas as pd
from src.utils.numeric import safe_num


POS_NORMALIZE = {
    "1": "GK",
    "2": "DF",
    "3": "MF",
    "4": "FW",
    "PORTERO": "GK",
    "DEFENSA": "DF",
    "CENTROCAMPISTA": "MF",
    "DELANTERO": "FW",
    "GK": "GK",
    "DF": "DF",
    "MF": "MF",
    "FW": "FW",
}

GOAL_BONUS_BY_LINE = {"GK": 6.0, "DF": 5.0, "MF": 4.0, "FW": 3.0}
UNAVAILABLE_STATES = {"injured", "suspended", "sanctioned", "lesionado", "sancionado", "baja"}
WARNED_STATES = {"warned", "apercibido", "yellow_card_risk"}


@lru_cache(maxsize=8192)
def _norm_text(s: object) -> str:
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return ""
    txt = str(s).strip().lower()
    txt = "".join(
        c for c in unicodedata.normalize("NFD", txt) if unicodedata.category(c) != "Mn"
    )
    return " ".join(txt.split())


@lru_cache(maxsize=256)
def normalize_position(pos_val: object) -> str:
    if pos_val is None:
        return ""
    raw = str(pos_val).strip().upper()
    if "," in raw:
        raw = raw.split(",")[0].strip()
    return POS_NORMALIZE.get(raw, "")


class ExpectedPointsEngine:
    """
    Computes player Expected Points (`xP`) across all valid multiposition lines
    (`xP_GK`, `xP_DF`, `xP_MF`, `xP_FW`, and `xP_best`) for an upcoming round.
    """

    def __init__(self, multipos_csv_path: str = "./data/players_transformed.csv"):
        self.multipos_map: Dict[int, Set[str]] = {}
        self.primary_pos_map: Dict[int, str] = {}
        self._load_multiposition_catalog(multipos_csv_path)

    def _load_multiposition_catalog(self, path: str) -> None:
        if not path or not os.path.exists(path):
            return
        try:
            df = pd.read_csv(path)
            for _, r in df.iterrows():
                try:
                    pid = int(r["PLAYER_ID"])
                except (KeyError, ValueError, TypeError):
                    continue
                prim = normalize_position(r.get("PLAYER_POSITION", ""))
                valid_set: Set[str] = set()
                if prim:
                    valid_set.add(prim)
                    self.primary_pos_map[pid] = prim
                alt = r.get("PLAYER_ALT_POSITIONS")
                if pd.notna(alt) and str(alt).strip():
                    for token in str(alt).split(","):
                        np_tok = normalize_position(token)
                        if np_tok:
                            valid_set.add(np_tok)
                if valid_set:
                    self.multipos_map[pid] = valid_set
        except Exception:
            pass

    def get_valid_lines(self, player_id: int, fallback_pos: object = None) -> Set[str]:
        prim = normalize_position(fallback_pos) or self.primary_pos_map.get(int(player_id), "")
        lines = set(self.multipos_map.get(int(player_id), set()))
        if prim:
            lines.add(prim)
        return {l for l in lines if l in ("GK", "DF", "MF", "FW")}

    def compute_xp_table(
        self,
        df_market_today: pd.DataFrame,
        df_reports_past: pd.DataFrame,
        df_calendar: pd.DataFrame,
        target_jornada_num: int,
        df_comuniate_round: Optional[pd.DataFrame] = None,
    ) -> pd.DataFrame:
        """
        Produces an enriched DataFrame indexed by `player_id` with:
        - `natural_pos`, `valid_lines`
        - `is_available`, `p_start`, `match_multiplier`, `expected_goals`
        - `xp_base`, `xp_GK`, `xp_DF`, `xp_MF`, `xp_FW`, `xp_best`, `best_line`
        """
        if df_market_today is None or df_market_today.empty:
            return pd.DataFrame()

        mkt = df_market_today.copy()
        mkt["player_id"] = pd.to_numeric(mkt["player_id"], errors="coerce").fillna(0).astype(int)
        mkt = mkt[mkt["player_id"] > 0].drop_duplicates(subset=["player_id"], keep="last")

        # Filter out coaches ("Pos 5")
        raw_pos_vals = mkt["posicion"].tolist()
        nat_pos_list = [normalize_position(v) for v in raw_pos_vals]
        keep_mask = [p in ("GK", "DF", "MF", "FW") for p in nat_pos_list]
        if not any(keep_mask):
            return pd.DataFrame()
        mkt = mkt.loc[keep_mask].copy()
        mkt["natural_pos"] = [p for p, k in zip(nat_pos_list, keep_mask) if k]

        # 1. Build Historical Rolling Performance Stats (Strictly Past Matches!) without pandas groupby
        hist_stats: Dict[int, Tuple[float, float, float, float, float, float]] = {}
        # Maps pid -> (n_apps, ema_pts_starter, ema_pts_sub, goals_per_90, recent_start_rate, recent_sub_rate)
        if df_reports_past is not None and not df_reports_past.empty:
            has_preparsed = (
                "es_titular_num" in df_reports_past.columns
                and np.issubdtype(df_reports_past["player_id"].dtype, np.integer)
                and np.issubdtype(df_reports_past["puntos_biwenger"].dtype, np.floating)
            )
            if has_preparsed:
                rep = df_reports_past
            else:
                rep = df_reports_past.copy()
                rep["player_id"] = pd.to_numeric(rep["player_id"], errors="coerce").fillna(0).astype(int)
                rep["puntos_biwenger"] = pd.to_numeric(rep["puntos_biwenger"], errors="coerce").fillna(0.0)
                rep["puntos_sofascore"] = pd.to_numeric(rep.get("puntos_sofascore", 0), errors="coerce").fillna(0.0)
                rep["minutos_jugados"] = pd.to_numeric(rep.get("minutos_jugados", 0), errors="coerce").fillna(0.0)
                rep["goles"] = pd.to_numeric(rep.get("goles", 0), errors="coerce").fillna(0.0)
                rep["es_titular_num"] = (
                    rep.get("es_titular", pd.Series(0, index=rep.index))
                    .astype(str)
                    .str.strip()
                    .str.upper()
                    .isin(["1", "TRUE", "SÍ", "SI"])
                    .astype(float)
                )

            if "fecha_partido" in rep.columns and not rep["fecha_partido"].is_monotonic_increasing:
                rep = rep.sort_values("fecha_partido", kind="mergesort")

            # Accumulate per-player arrays in a single linear O(N) pass
            acc_recent: Dict[int, List[Tuple[float, float]]] = {}
            acc_start_pts: Dict[int, List[float]] = {}
            acc_sub_pts: Dict[int, List[float]] = {}
            acc_all_blended: Dict[int, List[float]] = {}
            acc_mins: Dict[int, float] = {}
            acc_goals: Dict[int, float] = {}

            rep_pids = rep["player_id"].values
            rep_pts = rep["puntos_biwenger"].values
            rep_sofa = rep["puntos_sofascore"].values if "puntos_sofascore" in rep.columns else rep_pts
            rep_mins = rep["minutos_jugados"].values
            rep_goles = rep["goles"].values if "goles" in rep.columns else np.zeros(len(rep))
            rep_tits = rep["es_titular_num"].values

            for pid_r, pt_r, sf_r, mn_r, gl_r, tt_r in zip(rep_pids, rep_pts, rep_sofa, rep_mins, rep_goles, rep_tits):
                pid_i = int(pid_r)
                if pid_i <= 0:
                    continue
                mn_f = float(mn_r)
                tt_f = float(tt_r)

                lst_r = acc_recent.get(pid_i)
                if lst_r is None:
                    acc_recent[pid_i] = [(mn_f, tt_f)]
                else:
                    lst_r.append((mn_f, tt_f))

                if mn_f > 0.0:
                    pt_f = float(pt_r)
                    sf_f = float(sf_r)
                    blended = 0.85 * pt_f + 0.15 * (sf_f if sf_f != 0.0 else pt_f)
                    lst_b = acc_all_blended.get(pid_i)
                    if lst_b is None:
                        acc_all_blended[pid_i] = [blended]
                    else:
                        lst_b.append(blended)

                    if tt_f > 0.5:
                        lst_s = acc_start_pts.get(pid_i)
                        if lst_s is None:
                            acc_start_pts[pid_i] = [blended]
                        else:
                            lst_s.append(blended)
                    else:
                        lst_sub = acc_sub_pts.get(pid_i)
                        if lst_sub is None:
                            acc_sub_pts[pid_i] = [blended]
                        else:
                            lst_sub.append(blended)

                    acc_mins[pid_i] = acc_mins.get(pid_i, 0.0) + mn_f
                    acc_goals[pid_i] = acc_goals.get(pid_i, 0.0) + float(gl_r)

            def _ema(vals: List[float]) -> float:
                n = len(vals)
                if n == 1:
                    return vals[0]
                w = np.exp(np.linspace(-1.0, 0.0, n))
                return float(np.dot(vals, w) / w.sum())

            for pid_i, rec_list in acc_recent.items():
                recent3 = rec_list[-3:]
                n_rec = len(recent3)
                recent3_start = sum(t for _, t in recent3) / n_rec
                recent3_sub = sum(1.0 for m, t in recent3 if m > 0.0 and t == 0.0) / n_rec

                blended_list = acc_all_blended.get(pid_i)
                if not blended_list:
                    hist_stats[pid_i] = (0.0, 0.0, 1.6, 0.0, 0.0, 0.15)
                    continue

                n_apps = float(len(blended_list))
                start_list = acc_start_pts.get(pid_i)
                if start_list:
                    ema_starter = _ema(start_list)
                else:
                    ema_starter = _ema(blended_list) * 1.15

                sub_list = acc_sub_pts.get(pid_i)
                if sub_list:
                    ema_sub = float(np.clip(sum(sub_list) / len(sub_list), 0.8, 5.0))
                else:
                    ema_sub = float(np.clip(ema_starter * 0.42, 1.2, 4.2))

                tot_mins = acc_mins.get(pid_i, 0.0)
                tot_goals = acc_goals.get(pid_i, 0.0)
                goals_per_90 = float(np.clip((tot_goals / max(tot_mins, 90.0)) * 90.0, 0.0, 1.1))
                hist_stats[pid_i] = (n_apps, ema_starter, ema_sub, goals_per_90, recent3_start, max(0.20, recent3_sub))

        # 2. Build Matchup Context for `target_jornada_num` from `Partidos_Calendario`
        team_match_ctx: Dict[str, Tuple[bool, float, float, float]] = {}
        # Maps normalized team_name -> (is_home, win_prob, xg_for, xg_against)
        if df_calendar is not None and not df_calendar.empty and target_jornada_num > 0:
            jn_vals = pd.to_numeric(df_calendar["jornada_num"], errors="coerce").fillna(-1).astype(int).values
            cal_j = df_calendar[jn_vals == int(target_jornada_num)]
            if not cal_j.empty:
                locs = cal_j["local"].values if "local" in cal_j.columns else [""] * len(cal_j)
                viss = cal_j["visitante"].values if "visitante" in cal_j.columns else [""] * len(cal_j)
                p1s = cal_j["prob_local_1"].values if "prob_local_1" in cal_j.columns else [42] * len(cal_j)
                p2s = cal_j["prob_visitante_2"].values if "prob_visitante_2" in cal_j.columns else [30] * len(cal_j)
                xgls = cal_j["xg_local_eci"].values if "xg_local_eci" in cal_j.columns else [1.35] * len(cal_j)
                xgvs = cal_j["xg_visitante_eci"].values if "xg_visitante_eci" in cal_j.columns else [1.10] * len(cal_j)
                for l_r, v_r, p1_r, p2_r, xgl_r, xgv_r in zip(locs, viss, p1s, p2s, xgls, xgvs):
                    loc = _norm_text(l_r)
                    vis = _norm_text(v_r)
                    p1 = safe_num(p1_r, 42.0) / 100.0
                    p2 = safe_num(p2_r, 30.0) / 100.0
                    xgl = safe_num(xgl_r, 1.35)
                    xgv = safe_num(xgv_r, 1.10)
                    if loc:
                        team_match_ctx[loc] = (True, p1, xgl, xgv)
                    if vis:
                        team_match_ctx[vis] = (False, p2, xgv, xgl)

        # 3. Build Comuniate Starter Map for `target_jornada_num`
        comuniate_map: Dict[Tuple[str, str], Tuple[float, bool]] = {}
        comuniate_by_name: Dict[str, Tuple[float, bool]] = {}
        has_comuniate_round = (df_comuniate_round is not None and not df_comuniate_round.empty)
        if has_comuniate_round:
            c_n = df_comuniate_round["nombre"].values if "nombre" in df_comuniate_round.columns else [""] * len(df_comuniate_round)
            c_e = df_comuniate_round["equipo"].values if "equipo" in df_comuniate_round.columns else [""] * len(df_comuniate_round)
            c_t = df_comuniate_round["titularidad"].values if "titularidad" in df_comuniate_round.columns else [""] * len(df_comuniate_round)
            c_s = df_comuniate_round["suplente"].values if "suplente" in df_comuniate_round.columns else [""] * len(df_comuniate_round)
            c_d = df_comuniate_round["duda"].values if "duda" in df_comuniate_round.columns else ["0"] * len(df_comuniate_round)
            for nm_r, eq_r, tit_r, sup_r, dud_r in zip(c_n, c_e, c_t, c_s, c_d):
                nm = _norm_text(nm_r)
                if not nm:
                    continue
                eq = _norm_text(eq_r)
                raw_tit = str(tit_r).replace("%", "").strip()
                try:
                    p_st = float(raw_tit)
                    if p_st > 1.0:
                        p_st /= 100.0
                except ValueError:
                    p_st = 0.75 if not str(sup_r).strip() else 0.55
                is_duda = str(dud_r).strip() in ("1", "True", "true", "SÍ")
                val_tup = (float(min(1.0, max(0.0, p_st))), is_duda)
                comuniate_map[(eq, nm)] = val_tup
                if nm not in comuniate_by_name:
                    comuniate_by_name[nm] = val_tup

        # 4. Score Every Player & Multiposition Line via flat array iteration (zero iterrows)
        n_m = len(mkt)
        pids_arr = mkt["player_id"].values
        nat_pos_arr = mkt["natural_pos"].values
        nombre_arr = mkt["nombre"].values if "nombre" in mkt.columns else [""] * n_m
        equipo_arr = mkt["equipo"].values if "equipo" in mkt.columns else [""] * n_m
        estado_arr = mkt["estado"].values if "estado" in mkt.columns else ["ok"] * n_m
        precio_arr = pd.to_numeric(mkt["precio"], errors="coerce").fillna(150_000.0).values
        pts_last_arr = pd.to_numeric(mkt["puntos_last_season"], errors="coerce").fillna(0.0).values if "puntos_last_season" in mkt.columns else np.zeros(n_m)
        raw_ct_arr = mkt["comuniate_titular"].values if "comuniate_titular" in mkt.columns else [None] * n_m

        out_pid: List[int] = []
        out_nombre: List[str] = []
        out_equipo: List[str] = []
        out_nat_pos: List[str] = []
        out_valid_lines: List[str] = []
        out_is_avail: List[bool] = []
        out_estado: List[str] = []
        out_p_start: List[float] = []
        out_p_start_h: List[float] = []
        out_xp_h: List[float] = []
        out_mm: List[float] = []
        out_xp_base: List[float] = []
        out_xp_gk: List[float] = []
        out_xp_df: List[float] = []
        out_xp_mf: List[float] = []
        out_xp_fw: List[float] = []
        out_xp_best: List[float] = []
        out_best_line: List[str] = []

        log1p_14 = float(np.log1p(14.0))
        default_g90_map = {"GK": 0.0, "DF": 0.04, "MF": 0.11, "FW": 0.28}

        for i in range(n_m):
            pid = int(pids_arr[i])
            nat_pos = str(nat_pos_arr[i])
            valid_lines = self.get_valid_lines(pid, nat_pos)
            nombre = str(nombre_arr[i] or "")
            equipo = str(equipo_arr[i] or "")
            norm_nm = _norm_text(nombre)
            norm_eq = _norm_text(equipo)

            est_raw = estado_arr[i]
            estado = str(est_raw if est_raw is not None and est_raw == est_raw else "ok").strip().lower() or "ok"
            is_avail = estado not in UNAVAILABLE_STATES
            is_warned = (estado in WARNED_STATES) or ("apercib" in estado)

            precio = float(precio_arr[i]) if precio_arr[i] > 0 else 150_000.0
            pts_last_yr = float(pts_last_arr[i])

            # Prior starter points per match (Empirical Bayes shrinkage anchor)
            price_prior = 2.0 + 4.4 * (float(np.log1p(max(0.0, precio - 150_000.0) / 1_200_000.0)) / log1p_14)
            if pts_last_yr > 30.0:
                last_yr_per_game = min(8.5, max(2.2, pts_last_yr / 33.0))
                prior_starter_pts = 0.55 * price_prior + 0.45 * last_yr_per_game
            else:
                prior_starter_pts = price_prior

            hs = hist_stats.get(pid)
            if hs is not None:
                n_apps, ema_st, ema_sub, g90_hist, rec_st_rate, rec_sub_rate = hs
            else:
                n_apps, ema_st, ema_sub, g90_hist, rec_st_rate, rec_sub_rate = 0.0, 0.0, 1.6, 0.0, 0.0, 0.25

            if n_apps > 0.0:
                w_obs = n_apps / (n_apps + 2.8)
                exp_pts_if_start = w_obs * ema_st + (1.0 - w_obs) * prior_starter_pts
                exp_pts_if_sub = ema_sub
            else:
                exp_pts_if_start = prior_starter_pts * 0.92
                exp_pts_if_sub = max(1.1, prior_starter_pts * 0.35)

            # Determine starter probability (`p_start`) without lookahead
            is_duda = estado in ("doubt", "duda", "doubtful")
            p_start: Optional[float] = None

            c_hit = comuniate_map.get((norm_eq, norm_nm)) or comuniate_by_name.get(norm_nm)
            if c_hit is not None:
                p_start, c_duda = c_hit
                is_duda = is_duda or c_duda
            else:
                raw_ct = raw_ct_arr[i]
                if raw_ct is not None and raw_ct == raw_ct:
                    sct = str(raw_ct).replace("%", "").strip()
                    if sct and sct not in ("nan", "None"):
                        try:
                            val_ct = float(sct)
                            p_start = val_ct / 100.0 if val_ct > 1.0 else val_ct
                        except ValueError:
                            p_start = None

            if p_start is None:
                if has_comuniate_round:
                    p_start = 0.18 * rec_st_rate + 0.06
                elif n_apps > 0.0:
                    p_start = 0.65 * rec_st_rate + 0.35 * min(0.90, max(0.05, precio / 6_000_000.0))
                else:
                    p_start = min(0.85, max(0.05, (precio - 250_000.0) / 4_500_000.0))

            p_start = min(1.0, max(0.0, float(p_start)))
            if is_duda:
                p_start *= 0.72

            if n_apps > 0.0:
                base_h = 0.70 * rec_st_rate + 0.30 * min(0.95, max(0.08, precio / 6_000_000.0))
                p_start_healthy = min(0.98, max(0.05, max(p_start if is_avail else 0.0, base_h)))
            else:
                p_start_healthy = min(0.92, max(0.05, max(p_start if is_avail else 0.0, (precio - 250_000.0) / 4_500_000.0)))

            p_sub_healthy = 0.0 if nat_pos == "GK" else rec_sub_rate * min(1.0, max(0.25, precio / 2_500_000.0))
            xp_healthy = min(13.0, max(0.5, p_start_healthy * exp_pts_if_start + (1.0 - p_start_healthy) * p_sub_healthy * exp_pts_if_sub))
            if is_warned:
                # 4-yellow card suspension risk slightly discounts multi-round reliability (~1 missed match over next ~6)
                xp_healthy *= 0.94

            vl_str = ",".join(sorted(valid_lines))
            out_pid.append(pid)
            out_nombre.append(nombre)
            out_equipo.append(equipo)
            out_nat_pos.append(nat_pos)
            out_valid_lines.append(vl_str)
            out_is_avail.append(is_avail)
            out_estado.append(estado)
            out_p_start_h.append(round(p_start_healthy, 4))
            out_xp_h.append(round(xp_healthy, 4))

            if not is_avail:
                out_p_start.append(0.0)
                out_mm.append(1.0)
                out_xp_base.append(0.0)
                out_xp_gk.append(0.0)
                out_xp_df.append(0.0)
                out_xp_mf.append(0.0)
                out_xp_fw.append(0.0)
                out_xp_best.append(0.0)
                out_best_line.append(nat_pos)
                continue

            # Matchup context
            is_home, win_prob, xg_for, xg_against = team_match_ctx.get(norm_eq, (True, 0.38, 1.25, 1.25))
            home_adj = 1.05 if is_home else 0.96
            clean_sheet_prob = float(np.exp(-max(0.3, xg_against)))
            if nat_pos in ("GK", "DF"):
                match_mult = home_adj * (0.88 + 0.22 * win_prob + 0.20 * (clean_sheet_prob - 0.28))
            elif nat_pos == "FW":
                match_mult = home_adj * (0.86 + 0.18 * win_prob + 0.15 * (xg_for - 1.25))
            else:
                match_mult = home_adj * (0.86 + 0.20 * win_prob + 0.12 * (xg_for - 1.25))
            match_mult = min(1.28, max(0.78, float(match_mult)))

            raw_xp = (p_start * exp_pts_if_start + (1.0 - p_start) * p_sub_healthy * exp_pts_if_sub) * match_mult
            raw_xp = min(13.5, max(0.0, float(raw_xp)))

            default_g90 = default_g90_map.get(nat_pos, 0.08)
            g90_eff = g90_hist if n_apps > 0.0 else default_g90
            g90 = 0.6 * g90_eff + 0.4 * default_g90 * min(1.8, max(0.4, precio / 3_500_000.0))
            exp_mins_frac = p_start * 0.88 + (1.0 - p_start) * p_sub_healthy * 0.28
            exp_goals = g90 * exp_mins_frac * (xg_for / 1.25)

            line_xps = {"GK": 0.0, "DF": 0.0, "MF": 0.0, "FW": 0.0}
            best_line = nat_pos
            best_val = -1.0
            nat_goal_b = GOAL_BONUS_BY_LINE[nat_pos]
            # Canonical deterministic order: natural position first on exact ties, then GK/DF/MF/FW
            ordered_valid = ([nat_pos] if nat_pos in valid_lines else []) + [
                ln for ln in ("GK", "DF", "MF", "FW") if ln in valid_lines and ln != nat_pos
            ]
            for ln in ordered_valid:
                goal_diff_bonus = max(0.0, GOAL_BONUS_BY_LINE[ln] - nat_goal_b) * exp_goals
                cs_bonus = 0.35 * clean_sheet_prob * p_start if (ln == "DF" and nat_pos in ("MF", "FW")) else 0.0
                val = round(raw_xp + goal_diff_bonus + cs_bonus, 4)
                line_xps[ln] = val
                if val > best_val:
                    best_val = val
                    best_line = ln

            out_p_start.append(round(p_start, 4))
            out_mm.append(round(match_mult, 4))
            out_xp_base.append(round(raw_xp, 4))
            out_xp_gk.append(line_xps["GK"])
            out_xp_df.append(line_xps["DF"])
            out_xp_mf.append(line_xps["MF"])
            out_xp_fw.append(line_xps["FW"])
            out_xp_best.append(round(max(0.0, best_val), 4))
            out_best_line.append(best_line)

        return pd.DataFrame({
            "player_id": out_pid,
            "nombre": out_nombre,
            "equipo": out_equipo,
            "natural_pos": out_nat_pos,
            "valid_lines": out_valid_lines,
            "is_available": out_is_avail,
            "estado": out_estado,
            "p_start": out_p_start,
            "p_start_healthy": out_p_start_h,
            "xp_healthy": out_xp_h,
            "match_multiplier": out_mm,
            "xp_base": out_xp_base,
            "xp_GK": out_xp_gk,
            "xp_DF": out_xp_df,
            "xp_MF": out_xp_mf,
            "xp_FW": out_xp_fw,
            "xp_best": out_xp_best,
            "best_line": out_best_line,
        })
