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
from typing import Dict, Set, Optional, Tuple
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


def _norm_text(s: object) -> str:
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return ""
    txt = str(s).strip().lower()
    txt = "".join(
        c for c in unicodedata.normalize("NFD", txt) if unicodedata.category(c) != "Mn"
    )
    return " ".join(txt.split())


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
        mkt["natural_pos"] = mkt["posicion"].apply(normalize_position)
        mkt = mkt[mkt["natural_pos"].isin(["GK", "DF", "MF", "FW"])].copy()

        # 1. Build Historical Rolling Performance Stats (Strictly Past Matches!)
        hist_stats: Dict[int, Dict[str, float]] = {}
        if df_reports_past is not None and not df_reports_past.empty:
            rep = df_reports_past.copy()
            rep["player_id"] = pd.to_numeric(rep["player_id"], errors="coerce").fillna(0).astype(int)
            rep["puntos_biwenger"] = pd.to_numeric(rep["puntos_biwenger"], errors="coerce").fillna(0.0)
            rep["puntos_sofascore"] = pd.to_numeric(rep.get("puntos_sofascore", 0), errors="coerce").fillna(0.0)
            rep["minutos_jugados"] = pd.to_numeric(rep.get("minutos_jugados", 0), errors="coerce").fillna(0.0)
            rep["goles"] = pd.to_numeric(rep.get("goles", 0), errors="coerce").fillna(0.0)
            rep["es_titular_num"] = rep.get("es_titular", pd.Series(0, index=rep.index)).apply(
                lambda v: 1.0 if str(v).strip().upper() in ("1", "TRUE", "SÍ", "SI") else 0.0
            )
            if "fecha_partido" in rep.columns:
                rep = rep.sort_values("fecha_partido")

            for pid, grp in rep.groupby("player_id"):
                played = grp[grp["minutos_jugados"] > 0]
                n_apps = len(played)
                n_total_rows = len(grp)
                recent3 = grp.tail(3)
                recent3_start_rate = float(recent3["es_titular_num"].mean()) if len(recent3) > 0 else 0.0
                recent3_sub_rate = float(
                    ((recent3["minutos_jugados"] > 0) & (recent3["es_titular_num"] == 0)).mean()
                ) if len(recent3) > 0 else 0.25

                if n_apps == 0:
                    hist_stats[int(pid)] = {
                        "n_apps": 0.0,
                        "ema_pts_starter": 0.0,
                        "ema_pts_sub": 1.6,
                        "goals_per_app": 0.0,
                        "recent_start_rate": 0.0,
                        "recent_sub_rate": 0.15,
                    }
                    continue

                pts_arr = played["puntos_biwenger"].values
                sofa_arr = played["puntos_sofascore"].values
                # Blend official AS+SofaScore points (85%) with SofaScore stability (15%) to denoise Picas
                blended_arr = 0.85 * pts_arr + 0.15 * np.where(sofa_arr != 0, sofa_arr, pts_arr)

                starters_only = played[played["es_titular_num"] > 0.5]
                subs_only = played[played["es_titular_num"] <= 0.5]

                if len(starters_only) > 0:
                    s_pts = starters_only["puntos_biwenger"].values
                    w = np.exp(np.linspace(-1.0, 0.0, len(s_pts)))
                    ema_starter = float(np.average(s_pts, weights=w))
                else:
                    w = np.exp(np.linspace(-1.0, 0.0, len(blended_arr)))
                    ema_starter = float(np.average(blended_arr, weights=w)) * 1.15

                if len(subs_only) > 0:
                    ema_sub = float(np.clip(subs_only["puntos_biwenger"].mean(), 0.8, 5.0))
                else:
                    ema_sub = float(np.clip(ema_starter * 0.42, 1.2, 4.2))

                tot_mins = float(played["minutos_jugados"].sum())
                tot_goals = float(played["goles"].sum())
                goals_per_90 = (tot_goals / max(tot_mins, 90.0)) * 90.0

                hist_stats[int(pid)] = {
                    "n_apps": float(n_apps),
                    "n_rows": float(n_total_rows),
                    "ema_pts_starter": ema_starter,
                    "ema_pts_sub": ema_sub,
                    "goals_per_90": float(np.clip(goals_per_90, 0.0, 1.1)),
                    "recent_start_rate": recent3_start_rate,
                    "recent_sub_rate": max(0.20, recent3_sub_rate),
                }

        # 2. Build Matchup Context for `target_jornada_num` from `Partidos_Calendario`
        team_match_ctx: Dict[str, Tuple[bool, float, float, float]] = {}
        # Maps normalized team_name -> (is_home, win_prob, xg_for, xg_against)
        if df_calendar is not None and not df_calendar.empty and target_jornada_num > 0:
            cal = df_calendar.copy()
            cal["jn"] = pd.to_numeric(cal["jornada_num"], errors="coerce").fillna(-1).astype(int)
            # Only primary schedule (`part == 1`) or all matches of that jornada
            cal_j = cal[cal["jn"] == int(target_jornada_num)]
            for _, mr in cal_j.iterrows():
                loc = _norm_text(mr.get("local", ""))
                vis = _norm_text(mr.get("visitante", ""))
                p1 = safe_num(mr.get("prob_local_1", 42), 42.0) / 100.0
                p2 = safe_num(mr.get("prob_visitante_2", 30), 30.0) / 100.0
                xgl = safe_num(mr.get("xg_local_eci", 1.35), 1.35)
                xgv = safe_num(mr.get("xg_visitante_eci", 1.10), 1.10)
                if loc:
                    team_match_ctx[loc] = (True, p1, xgl, xgv)
                if vis:
                    team_match_ctx[vis] = (False, p2, xgv, xgl)

        # 3. Build Comuniate Starter Map for `target_jornada_num`
        comuniate_map: Dict[Tuple[str, str], Tuple[float, bool]] = {}
        comuniate_by_name: Dict[str, Tuple[float, bool]] = {}
        if df_comuniate_round is not None and not df_comuniate_round.empty:
            for _, cr in df_comuniate_round.iterrows():
                nm = _norm_text(cr.get("nombre", ""))
                eq = _norm_text(cr.get("equipo", ""))
                raw_tit = str(cr.get("titularidad", "")).replace("%", "").strip()
                try:
                    p_st = float(raw_tit)
                    if p_st > 1.0:
                        p_st /= 100.0
                except ValueError:
                    p_st = 0.75 if not str(cr.get("suplente", "")).strip() else 0.55
                is_duda = str(cr.get("duda", "0")).strip() in ("1", "True", "true", "SÍ")
                if nm:
                    comuniate_map[(eq, nm)] = (float(np.clip(p_st, 0.0, 1.0)), is_duda)
                    # Keep highest if unique name
                    if nm not in comuniate_by_name:
                        comuniate_by_name[nm] = (float(np.clip(p_st, 0.0, 1.0)), is_duda)

        # 4. Score Every Player & Multiposition Line
        rows = []
        for _, r in mkt.iterrows():
            pid = int(r["player_id"])
            nat_pos = r["natural_pos"]
            valid_lines = self.get_valid_lines(pid, nat_pos)
            nombre = str(r.get("nombre", ""))
            equipo = str(r.get("equipo", ""))
            norm_nm = _norm_text(nombre)
            norm_eq = _norm_text(equipo)

            estado = str(r.get("estado", "ok") or "ok").strip().lower()
            is_avail = estado not in UNAVAILABLE_STATES

            precio = safe_num(r.get("precio", 150_000), 150_000.0)
            pts_last_yr = safe_num(r.get("puntos_last_season", 0), 0.0)

            # Prior starter points per match (Empirical Bayes shrinkage anchor)
            price_prior = 2.0 + 4.4 * (np.log1p(max(0.0, precio - 150_000) / 1_200_000.0) / np.log1p(14.0))
            if pts_last_yr > 30:
                last_yr_per_game = np.clip(pts_last_yr / 33.0, 2.2, 8.5)
                prior_starter_pts = 0.55 * price_prior + 0.45 * last_yr_per_game
            else:
                prior_starter_pts = price_prior

            hs = hist_stats.get(pid, {})
            n_apps = hs.get("n_apps", 0.0)
            if n_apps > 0:
                w_obs = n_apps / (n_apps + 2.8)
                exp_pts_if_start = w_obs * hs["ema_pts_starter"] + (1.0 - w_obs) * prior_starter_pts
                exp_pts_if_sub = hs["ema_pts_sub"]
            else:
                exp_pts_if_start = prior_starter_pts * 0.92
                exp_pts_if_sub = max(1.1, prior_starter_pts * 0.35)

            # Determine starter probability (`p_start`) without lookahead
            is_duda = (estado in ("doubt", "duda", "doubtful"))
            p_start: Optional[float] = None

            if (norm_eq, norm_nm) in comuniate_map:
                p_start, c_duda = comuniate_map[(norm_eq, norm_nm)]
                is_duda = is_duda or c_duda
            elif norm_nm in comuniate_by_name:
                p_start, c_duda = comuniate_by_name[norm_nm]
                is_duda = is_duda or c_duda
            else:
                raw_ct = r.get("comuniate_titular", "")
                if pd.notna(raw_ct) and str(raw_ct).strip() not in ("", "nan", "None"):
                    try:
                        val_ct = float(str(raw_ct).replace("%", "").strip())
                        p_start = val_ct / 100.0 if val_ct > 1.0 else val_ct
                    except ValueError:
                        p_start = None

            if p_start is None:
                # Fallback when Comuniate didn't list player (either bench or historical gap)
                if df_comuniate_round is not None and not df_comuniate_round.empty:
                    # Comuniate only publishes probable XI (starters): unlisted players are subs (~8-20% spot start)
                    p_start = 0.18 * hs.get("recent_start_rate", 0.0) + 0.06
                elif n_apps > 0:
                    p_start = 0.65 * hs.get("recent_start_rate", 0.5) + 0.35 * np.clip(precio / 6_000_000.0, 0.05, 0.90)
                else:
                    p_start = float(np.clip((precio - 250_000.0) / 4_500_000.0, 0.05, 0.85))

            p_start = float(np.clip(p_start, 0.0, 1.0))
            if is_duda:
                p_start *= 0.72

            # Baseline healthy starter probability & neutral-matchup xP when fit (for multi-round NPV & replacement cost)
            if n_apps > 0:
                p_start_healthy = float(np.clip(
                    max(p_start if is_avail else 0.0, 0.70 * hs.get("recent_start_rate", 0.5) + 0.30 * np.clip(precio / 6_000_000.0, 0.08, 0.95)),
                    0.05, 0.98
                ))
            else:
                p_start_healthy = float(np.clip(max(p_start if is_avail else 0.0, (precio - 250_000.0) / 4_500_000.0), 0.05, 0.92))
            p_sub_healthy = 0.0 if nat_pos == "GK" else hs.get("recent_sub_rate", 0.30) * float(np.clip(precio / 2_500_000.0, 0.25, 1.0))
            xp_healthy = float(np.clip(
                p_start_healthy * exp_pts_if_start + (1.0 - p_start_healthy) * p_sub_healthy * exp_pts_if_sub,
                0.5, 13.0
            ))

            if not is_avail:
                rows.append({
                    "player_id": pid,
                    "nombre": nombre,
                    "equipo": equipo,
                    "natural_pos": nat_pos,
                    "valid_lines": ",".join(sorted(valid_lines)),
                    "is_available": False,
                    "estado": estado,
                    "p_start": 0.0,
                    "p_start_healthy": round(p_start_healthy, 4),
                    "xp_healthy": round(xp_healthy, 4),
                    "match_multiplier": 1.0,
                    "xp_base": 0.0,
                    "xp_GK": 0.0,
                    "xp_DF": 0.0,
                    "xp_MF": 0.0,
                    "xp_FW": 0.0,
                    "xp_best": 0.0,
                    "best_line": nat_pos,
                })
                continue

            # Matchup context
            is_home, win_prob, xg_for, xg_against = team_match_ctx.get(
                norm_eq, (True, 0.38, 1.25, 1.25)
            )
            home_adj = 1.05 if is_home else 0.96
            if nat_pos in ("GK", "DF"):
                clean_sheet_prob = float(np.exp(-max(0.3, xg_against)))
                match_mult = home_adj * (0.88 + 0.22 * win_prob + 0.20 * (clean_sheet_prob - 0.28))
            else:
                clean_sheet_prob = float(np.exp(-max(0.3, xg_against)))
                match_mult = home_adj * (0.86 + 0.20 * win_prob + 0.10 * (xg_for - 1.25))
            match_mult = float(np.clip(match_mult, 0.78, 1.28))

            # Expected minutes & sub probability
            p_sub_play = 0.0 if nat_pos == "GK" else hs.get("recent_sub_rate", 0.30) * float(np.clip(precio / 2_500_000.0, 0.25, 1.0))
            raw_xp = (
                p_start * exp_pts_if_start
                + (1.0 - p_start) * p_sub_play * exp_pts_if_sub
            ) * match_mult
            raw_xp = float(np.clip(raw_xp, 0.0, 13.5))

            # Expected goals in this match (for multiposition out-of-position bonus!)
            default_g90 = {"GK": 0.0, "DF": 0.04, "MF": 0.11, "FW": 0.28}.get(nat_pos, 0.08)
            g90 = 0.6 * hs.get("goals_per_90", default_g90) + 0.4 * default_g90 * np.clip(precio / 3_500_000.0, 0.4, 1.8)
            exp_mins_frac = p_start * 0.88 + (1.0 - p_start) * p_sub_play * 0.28
            exp_goals = g90 * exp_mins_frac * (xg_for / 1.25)

            line_xps = {"GK": 0.0, "DF": 0.0, "MF": 0.0, "FW": 0.0}
            best_line = nat_pos
            best_val = -1.0
            for ln in ("GK", "DF", "MF", "FW"):
                if ln not in valid_lines:
                    continue
                goal_diff_bonus = max(0.0, GOAL_BONUS_BY_LINE[ln] - GOAL_BONUS_BY_LINE[nat_pos]) * exp_goals
                cs_bonus = 0.35 * clean_sheet_prob * p_start if (ln == "DF" and nat_pos in ("MF", "FW")) else 0.0
                val = round(raw_xp + goal_diff_bonus + cs_bonus, 4)
                line_xps[ln] = val
                if val > best_val:
                    best_val = val
                    best_line = ln

            rows.append({
                "player_id": pid,
                "nombre": nombre,
                "equipo": equipo,
                "natural_pos": nat_pos,
                "valid_lines": ",".join(sorted(valid_lines)),
                "is_available": True,
                "estado": estado,
                "p_start": round(p_start, 4),
                "p_start_healthy": round(p_start_healthy, 4),
                "xp_healthy": round(xp_healthy, 4),
                "match_multiplier": round(match_mult, 4),
                "xp_base": round(raw_xp, 4),
                "xp_GK": line_xps["GK"],
                "xp_DF": line_xps["DF"],
                "xp_MF": line_xps["MF"],
                "xp_FW": line_xps["FW"],
                "xp_best": round(max(0.0, best_val), 4),
                "best_line": best_line,
            })

        return pd.DataFrame(rows)
