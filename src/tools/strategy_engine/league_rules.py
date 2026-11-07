"""
Dynamic League Rules Engine & Calendar Phase State Machine (`LeagueRulesEngine`)
===============================================================================
Zero-hardcoding rules & timing engine that:
1. Auto-discovers official Biwenger league settings from `/api/v2/league/{id}?fields=*,settings`
   (or cached `./data/raw/live_league_settings.json`), with `.env` override support for any user.
2. Computes exact matchday temporal phases (`RoundPhaseContext`) from real kickoff timestamps:
   - `T_clause_lock = T_first_kickoff - clauseRoundDisabledHours` (e.g. 48h before kickoff)
   - `T_solvency_check = T_first_kickoff - 2.5h`
   - `T_final_lineup_guard = T_first_kickoff - 30m`
   - `IN_ROUND_NEXT_CYCLE`: once `now > T_first_kickoff` (e.g., Saturday/Sunday/Monday),
     recognizes that the current round's XI & solvency are ALREADY locked, automatically
     re-enabling 25% credit leverage for `J+1` while keeping clauses locked until round end.
"""

import os
import json
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Tuple
import pandas as pd

from src.config import _get_config_var, StrategySettings, resolve_active_user_identity


DEFAULT_CLAUSE_RANGES: List[Tuple[float, float, str]] = [
    (2_000_000.0, 225.0, "%"),
    (3_000_000.0, 5_000_000.0, "abs"),
    (5_000_000.0, 200.0, "%"),
    (6_500_000.0, 10_000_000.0, "abs"),
    (9_000_000.0, 175.0, "%"),
    (12_000_000.0, 16_000_000.0, "abs"),
    (15_000_000.0, 150.0, "%"),
    (20_000_000.0, 24_000_000.0, "abs"),
    (25_000_000.0, 135.0, "%"),
    (30_000_000.0, 35_000_000.0, "abs"),
    (float("inf"), 125.0, "%"),
]

SETTINGS_CACHE_PATH = "./data/raw/live_league_settings.json"


@dataclass
class RoundPhaseContext:
    """Exact calendar & rule state at any timestamp `now_ts`."""
    now_ts: pd.Timestamp
    phase_name: str  # OPEN_TRADING | PRE_CLAUSE_LOCK_WINDOW | CLAUSES_FROZEN_PRE_KICKOFF | KICKOFF_IMMINENT | IN_ROUND_ACTIVE | INTERNATIONAL_BREAK
    target_jornada_num: int
    active_in_progress_jornada: Optional[int]
    next_kickoff_ts: pd.Timestamp
    clause_lock_ts: pd.Timestamp
    active_round_end_ts: Optional[pd.Timestamp]
    hours_to_kickoff: float
    days_to_kickoff: int
    hours_to_clause_lock: float
    is_kickoff_day: bool
    must_be_solvent_now: bool
    clauses_open: bool
    can_use_credit_leverage: bool
    is_international_break: bool
    is_schedule_placeholder: bool

    # Tactical trigger windows for DynamicRoundDispatcher
    in_pre_clause_lock_window: bool = False
    in_pre_kickoff_solvency_window: bool = False
    in_final_lineup_guard_window: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "now_ts": self.now_ts.strftime("%Y-%m-%d %H:%M:%S"),
            "phase_name": self.phase_name,
            "target_jornada_num": self.target_jornada_num,
            "active_in_progress_jornada": self.active_in_progress_jornada,
            "next_kickoff_ts": self.next_kickoff_ts.strftime("%Y-%m-%d %H:%M:%S"),
            "clause_lock_ts": self.clause_lock_ts.strftime("%Y-%m-%d %H:%M:%S"),
            "active_round_end_ts": self.active_round_end_ts.strftime("%Y-%m-%d %H:%M:%S") if self.active_round_end_ts is not None else None,
            "hours_to_kickoff": round(self.hours_to_kickoff, 2),
            "days_to_kickoff": self.days_to_kickoff,
            "hours_to_clause_lock": round(self.hours_to_clause_lock, 2),
            "is_kickoff_day": self.is_kickoff_day,
            "must_be_solvent_now": self.must_be_solvent_now,
            "clauses_open": self.clauses_open,
            "can_use_credit_leverage": self.can_use_credit_leverage,
            "is_international_break": self.is_international_break,
            "is_schedule_placeholder": self.is_schedule_placeholder,
            "in_pre_clause_lock_window": self.in_pre_clause_lock_window,
            "in_pre_kickoff_solvency_window": self.in_pre_kickoff_solvency_window,
            "in_final_lineup_guard_window": self.in_final_lineup_guard_window,
        }


@dataclass
class LeagueRulesEngine:
    """
    Universal Biwenger League Rules Engine.
    Hydrates dynamically from API `settings` dict and supports `.env` overrides.
    """
    league_id: int = 0
    league_name: str = "Liga Biwenger"
    score_id: int = 5
    credit_limit_ratio: float = 0.25
    credit_fixed_eur: float = 0.0
    clause_enabled: bool = True
    clause_ranges: List[Tuple[float, float, str]] = field(default_factory=lambda: list(DEFAULT_CLAUSE_RANGES))
    clause_disabled_hours: int = 48
    clause_activation_delay_days: int = 5
    clause_increment_ratio: float = 1.0       # 1.0 => 1€ cash raises clause by 1€
    clause_decrement_refund_pct: float = 0.50  # 0.50 => lowering clause refunds 50% cash (50% deadweight friction)
    market_max_user_sales: int = 5            # max simultaneous players listed on market by user
    market_free_slots: int = 20               # daily free players placed on market by Computer
    days_for_sale: int = 1                    # days a newly bought player is locked before selling
    immediate_sale_pct: float = 0.50          # instant non-offer sale payout %
    team_max_size: int = 25
    team_max_club_players: int = 0
    lineup_multipos: bool = True
    lineup_round_changes: int = 0
    lineup_reserves: bool = False
    lineup_captain: bool = False
    lineup_striker: bool = False
    lineup_coach: bool = False
    bonus_per_point_eur: int = 50_000
    bonus_ideal_lineup_eur: int = 250_000
    bonus_game_mvp_eur: int = 100_000
    bonus_round_mvp_eur: int = 500_000

    @classmethod
    def from_api_payload(cls, league_data: Dict[str, Any], apply_env_overrides: bool = True) -> "LeagueRulesEngine":
        """Constructs `LeagueRulesEngine` directly from `/api/v2/league/{id}?fields=*,settings` JSON `data`."""
        settings = league_data.get("settings", league_data) if isinstance(league_data, dict) else {}
        ident = resolve_active_user_identity()

        lid = int(league_data.get("id") or ident.get("league_id") or 0)
        lname = str(league_data.get("name") or ident.get("league_name") or "Liga Biwenger")
        score_id = int(league_data.get("scoreID") or ident.get("score_id") or 5)

        # Parse credit limit (`maximumBid`)
        max_bid_setting = settings.get("maximumBid", "quarterTeam")
        cred_ratio = 0.25
        cred_fixed = 0.0
        if max_bid_setting == "quarterTeam":
            cred_ratio = 0.25
        elif max_bid_setting == "halfTeam":
            cred_ratio = 0.50
        elif max_bid_setting == "team":
            cred_ratio = 1.00
        elif max_bid_setting in ("zero", "balance", 0, "0", False, None):
            cred_ratio = 0.0
        else:
            try:
                val_num = float(max_bid_setting)
                if val_num > 10.0:
                    cred_ratio = 0.0
                    cred_fixed = val_num
                else:
                    cred_ratio = val_num
            except (TypeError, ValueError):
                cred_ratio = 0.25

        # Parse clause brackets (`clauseRanges`)
        clause_mode = str(settings.get("clause", "steal") or "").lower()
        clause_enabled = clause_mode not in ("disabled", "false", "0", "none", "")
        raw_ranges = settings.get("clauseRanges")
        parsed_ranges: List[Tuple[float, float, str]] = []
        if isinstance(raw_ranges, list) and len(raw_ranges) > 0:
            for item in raw_ranges:
                if isinstance(item, (list, tuple)) and len(item) >= 3:
                    thr_raw, val_raw, mode_raw = item[0], item[1], str(item[2])
                    thr = float("inf") if str(thr_raw).lower() == "inf" else float(thr_raw)
                    parsed_ranges.append((thr, float(val_raw), mode_raw))
        if not parsed_ranges:
            parsed_ranges = list(DEFAULT_CLAUSE_RANGES)

        raw_max_sales = int(settings.get("marketMaxUserSales", 5) or 0)
        max_user_sales = raw_max_sales if raw_max_sales > 0 else 25

        raw_team_max = int(settings.get("teamMaxSize", 25) or 0)
        team_max = raw_team_max if raw_team_max > 0 else 25

        inc_setting = float(settings.get("clauseIncrement", 1) or 1)
        inc_ratio = inc_setting if inc_setting > 0 else 1.0

        dec_setting = float(settings.get("clauseDecrement", 50) or 50)
        dec_pct = dec_setting / 100.0 if dec_setting > 1.0 else dec_setting

        imm_setting = float(settings.get("immediateSales", 50) or 50)
        imm_pct = imm_setting / 100.0 if imm_setting > 1.0 else imm_setting

        engine = cls(
            league_id=lid,
            league_name=lname,
            score_id=score_id,
            credit_limit_ratio=cred_ratio,
            credit_fixed_eur=cred_fixed,
            clause_enabled=clause_enabled,
            clause_ranges=parsed_ranges,
            clause_disabled_hours=int(settings.get("clauseRoundDisabledHours", 48) or 0),
            clause_activation_delay_days=int(settings.get("clauseActivationDelay", 5) or 0),
            clause_increment_ratio=inc_ratio,
            clause_decrement_refund_pct=dec_pct,
            market_max_user_sales=max_user_sales,
            market_free_slots=int(settings.get("marketSize", 20) or 20),
            days_for_sale=int(settings.get("daysForSale", 1) or 0),
            immediate_sale_pct=imm_pct,
            team_max_size=team_max,
            team_max_club_players=int(settings.get("teamMaxClubPlayers", 0) or 0),
            lineup_multipos=bool(settings.get("lineupMultiPos", True)),
            lineup_round_changes=int(settings.get("lineupRoundChanges", 0) or 0),
            lineup_reserves=bool(settings.get("lineupReserves", False)),
            lineup_captain=bool(settings.get("lineupCaptain", False)),
            lineup_striker=bool(settings.get("lineupStriker", False)),
            lineup_coach=bool(settings.get("lineupCoach", False)),
            bonus_per_point_eur=int(settings.get("bonusPoint", 50_000) or 0),
            bonus_ideal_lineup_eur=int(settings.get("bonusIdealLineup", 250_000) or 0),
            bonus_game_mvp_eur=int(settings.get("bonusGameMVP", 100_000) or 0),
            bonus_round_mvp_eur=int(settings.get("bonusRoundMVP", 500_000) or 0),
        )

        if apply_env_overrides:
            engine._apply_env_overrides()
        return engine

    def _apply_env_overrides(self) -> None:
        """Applies optional user `.env` overrides so any player can customize rules effortlessly."""
        ov_cred = _get_config_var("OVERRIDE_CREDIT_LIMIT_RATIO") or _get_config_var("CREDIT_LIMIT_RATIO")
        if ov_cred:
            try:
                self.credit_limit_ratio = float(ov_cred)
            except ValueError:
                pass

        ov_c_hrs = _get_config_var("OVERRIDE_CLAUSE_DISABLED_HOURS")
        if ov_c_hrs != "" and ov_c_hrs.isdigit():
            self.clause_disabled_hours = int(ov_c_hrs)

        ov_max_sales = _get_config_var("OVERRIDE_MARKET_MAX_USER_SALES")
        if ov_max_sales != "" and ov_max_sales.isdigit():
            self.market_max_user_sales = int(ov_max_sales)

        ov_clauses_on = _get_config_var("OVERRIDE_CLAUSES_ENABLED")
        if ov_clauses_on != "":
            self.clause_enabled = ov_clauses_on.lower() in ("1", "true", "yes")

    @classmethod
    def load(cls, cache_path: str = SETTINGS_CACHE_PATH) -> "LeagueRulesEngine":
        """Loads cached league rules from disk, falling back cleanly to defaults + `.env` overrides."""
        if cache_path and os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                return cls.from_api_payload(payload, apply_env_overrides=True)
            except Exception:
                pass
        ident = resolve_active_user_identity()
        eng = cls(
            league_id=int(ident.get("league_id") or 0),
            league_name=str(ident.get("league_name") or "Liga Biwenger"),
            score_id=int(ident.get("score_id") or 5),
        )
        eng._apply_env_overrides()
        return eng

    def save_cache(self, raw_api_league_data: Dict[str, Any], cache_path: str = SETTINGS_CACHE_PATH) -> None:
        os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(raw_api_league_data, f, ensure_ascii=False, indent=2)

    def compute_official_clause(self, base_value: float, extra_shield_eur: float = 0.0) -> int:
        """Computes exact release clause from `max(purchase_price, market_price)` + manual shield."""
        base = float(base_value or 0.0)
        if base <= 0:
            return 0
        clause_val = base * 2.0
        for threshold, rule_val, mode in self.clause_ranges:
            if base <= float(threshold):
                if mode == "%":
                    clause_val = base * (float(rule_val) / 100.0)
                else:
                    clause_val = float(rule_val)
                break
        return int(round(clause_val + float(extra_shield_eur or 0.0)))

    def compute_max_official_bid(self, current_cash: float, squad_market_value: float) -> float:
        """Maximum single bid allowed by Biwenger (`cash + credit_allowance`)."""
        credit = max(self.credit_fixed_eur, self.credit_limit_ratio * float(squad_market_value))
        return float(current_cash) + credit

    def compute_max_bid(self, current_cash: float, squad_market_value: float) -> float:
        """Alias for `compute_max_official_bid`."""
        return self.compute_max_official_bid(current_cash, squad_market_value)

    def resolve_round_phase(
        self,
        df_calendar: pd.DataFrame,
        now_ts: Optional[pd.Timestamp] = None,
    ) -> RoundPhaseContext:
        """Convenience wrapper resolving `RoundPhaseContext` with optional `now_ts` (defaults to Europe/Madrid now)."""
        eff_now = pd.Timestamp(now_ts) if now_ts is not None else pd.Timestamp.now(tz="Europe/Madrid")
        return self.compute_round_phase(now_ts=eff_now, df_calendar=df_calendar)

    def cash_required_for_clause_increase(self, desired_clause_delta_eur: float) -> int:
        """Exact EUR cash required to raise a player's clause by `desired_clause_delta_eur`."""
        if desired_clause_delta_eur <= 0:
            return 0
        return int(round(float(desired_clause_delta_eur) / max(self.clause_increment_ratio, 0.01)))

    def compute_round_phase(
        self,
        now_ts: pd.Timestamp,
        df_calendar: pd.DataFrame,
    ) -> RoundPhaseContext:
        """
        Determines exact tactical phase, target matchday, clause lock status, and solvency urgency
        dynamically from the real match schedule (`Partidos_Calendario`).
        """
        ts = pd.Timestamp(now_ts)
        if ts.tzinfo is not None:
            ts = ts.tz_convert("Europe/Madrid").tz_localize(None)

        if df_calendar is None or df_calendar.empty:
            fallback_ko = (ts + pd.Timedelta(days=3)).replace(hour=21, minute=0, second=0)
            cl_ts = fallback_ko - pd.Timedelta(hours=self.clause_disabled_hours)
            return RoundPhaseContext(
                now_ts=ts,
                phase_name="OPEN_TRADING",
                target_jornada_num=1,
                active_in_progress_jornada=None,
                next_kickoff_ts=fallback_ko,
                clause_lock_ts=cl_ts,
                active_round_end_ts=None,
                hours_to_kickoff=(fallback_ko - ts).total_seconds() / 3600.0,
                days_to_kickoff=3,
                hours_to_clause_lock=(cl_ts - ts).total_seconds() / 3600.0,
                is_kickoff_day=False,
                must_be_solvent_now=False,
                clauses_open=self.clause_enabled,
                can_use_credit_leverage=(self.credit_limit_ratio > 0 or self.credit_fixed_eur > 0),
                is_international_break=False,
                is_schedule_placeholder=False,
            )

        cal = df_calendar.copy()
        if "jornada_num_int" in cal.columns:
            cal["_jn"] = pd.to_numeric(cal["jornada_num_int"], errors="coerce").fillna(-1).astype(int)
        elif "jornada_num" in cal.columns:
            cal["_jn"] = pd.to_numeric(cal["jornada_num"], errors="coerce").fillna(-1).astype(int)
        elif "jornada" in cal.columns:
            cal["_jn"] = cal["jornada"].astype(str).str.extract(r"(\d+)", expand=False).pipe(pd.to_numeric, errors="coerce").fillna(-1).astype(int)
        else:
            cal["_jn"] = -1

        if "part_int" in cal.columns:
            cal["_part"] = pd.to_numeric(cal["part_int"], errors="coerce").fillna(1).astype(int)
        elif "part" in cal.columns:
            cal["_part"] = pd.to_numeric(cal["part"], errors="coerce").fillna(1).astype(int)
        else:
            cal["_part"] = 1

        # Primary round windows (`part == 1`), ignoring isolated postponed single matches months later
        cal_prim = cal[(cal["_jn"] > 0) & (cal["_part"] == 1)].copy()
        if cal_prim.empty:
            cal_prim = cal[cal["_jn"] > 0].copy()

        # `Partidos_Calendario` stores official Biwenger API match timestamps in UTC
        # (identifiable by `match_id` / `round_id`), whereas synthetic test tables pass naive Madrid time.
        if "match_id" in cal_prim.columns or "round_id" in cal_prim.columns:
            cal_prim["_dt"] = (
                pd.to_datetime(cal_prim["fecha_hora"].astype(str).str[:19], errors="coerce", utc=True)
                .dt.tz_convert("Europe/Madrid")
                .dt.tz_localize(None)
            )
        else:
            cal_prim["_dt"] = pd.to_datetime(cal_prim["fecha_hora"].astype(str).str[:19], errors="coerce")
        cal_prim = cal_prim.dropna(subset=["_dt"])

        round_windows: List[Tuple[int, pd.Timestamp, pd.Timestamp]] = []
        for jn, grp in cal_prim.groupby("_jn"):
            first_ko = grp["_dt"].min()
            # Filter out isolated postponed outlier matches > 6 days after round start for `round_end`
            in_window = grp[grp["_dt"] <= first_ko + pd.Timedelta(days=5)]
            last_ko = in_window["_dt"].max() if not in_window.empty else grp["_dt"].max()
            round_end = last_ko + pd.Timedelta(hours=2, minutes=30)
            round_windows.append((int(jn), first_ko, round_end))

        round_windows.sort(key=lambda x: x[1])

        # Check if a round is currently in progress (`first_ko <= ts <= round_end`)
        active_jn: Optional[int] = None
        active_end: Optional[pd.Timestamp] = None
        for jn, f_ko, r_end in round_windows:
            if f_ko <= ts <= r_end:
                active_jn = jn
                active_end = r_end
                break

        # Find next upcoming round whose `first_ko > ts`
        future_rounds = [(jn, f_ko, r_end) for (jn, f_ko, r_end) in round_windows if f_ko > ts]
        if future_rounds:
            next_jn, next_ko, _ = future_rounds[0]
        else:
            last_jn, last_ko, _ = round_windows[-1]
            next_jn, next_ko = last_jn, last_ko

        clause_lock_ts = next_ko - pd.Timedelta(hours=self.clause_disabled_hours)
        hrs_to_ko = max(0.0, (next_ko - ts).total_seconds() / 3600.0)
        # Calendar day difference between kickoff date and today's date
        days_to_ko = max(0, (next_ko.normalize() - ts.normalize()).days)
        hrs_to_cl = (clause_lock_ts - ts).total_seconds() / 3600.0
        is_placeholder = (next_ko.hour == 0 and next_ko.minute <= 5)
        is_intl_break = (hrs_to_ko >= 192.0)  # >= 8 days until next round kickoff

        # Same-calendar-day kickoff (BEFORE `next_ko`! Once `ts >= next_ko`, `next_ko` already moved to `J+1`!)
        is_ko_day = (next_ko.strftime("%Y-%m-%d") == ts.strftime("%Y-%m-%d")) and (ts < next_ko)

        # Clauses are disabled BOTH while an active round is still being played AND inside the 48h pre-kickoff lock!
        clauses_open = (
            bool(self.clause_enabled)
            and (active_jn is None)
            and (ts < clause_lock_ts)
        )

        # Tactical intraday windows (only active on real confirmed schedules, not `00:01` placeholders)
        in_pre_clause = (
            bool(self.clause_enabled)
            and (active_jn is None)
            and (not is_placeholder)
            and (0.15 <= hrs_to_cl <= 2.25)
        )
        in_pre_solvency = (not is_placeholder) and (1.05 <= hrs_to_ko <= 3.25)
        in_final_lineup = (not is_placeholder) and (0.10 <= hrs_to_ko <= 1.00)

        if active_jn is not None:
            phase = "IN_ROUND_ACTIVE"
            must_solvent = False  # Lineup & solvency for `active_jn` are ALREADY locked!
            can_leverage = (not is_ko_day) and (self.credit_limit_ratio > 0 or self.credit_fixed_eur > 0)
        elif is_ko_day or hrs_to_ko <= 4.0:
            phase = "KICKOFF_IMMINENT"
            must_solvent = True
            can_leverage = False
        elif not clauses_open:
            phase = "CLAUSES_FROZEN_PRE_KICKOFF"
            must_solvent = False
            can_leverage = (self.credit_limit_ratio > 0 or self.credit_fixed_eur > 0)
        elif in_pre_clause:
            phase = "PRE_CLAUSE_LOCK_WINDOW"
            must_solvent = False
            can_leverage = (self.credit_limit_ratio > 0 or self.credit_fixed_eur > 0)
        elif is_intl_break:
            phase = "INTERNATIONAL_BREAK"
            must_solvent = False
            can_leverage = (self.credit_limit_ratio > 0 or self.credit_fixed_eur > 0)
        else:
            phase = "OPEN_TRADING"
            must_solvent = False
            can_leverage = (self.credit_limit_ratio > 0 or self.credit_fixed_eur > 0)

        return RoundPhaseContext(
            now_ts=ts,
            phase_name=phase,
            target_jornada_num=int(next_jn),
            active_in_progress_jornada=active_jn,
            next_kickoff_ts=next_ko,
            clause_lock_ts=clause_lock_ts,
            active_round_end_ts=active_end,
            hours_to_kickoff=hrs_to_ko,
            days_to_kickoff=days_to_ko,
            hours_to_clause_lock=hrs_to_cl,
            is_kickoff_day=is_ko_day,
            must_be_solvent_now=must_solvent,
            clauses_open=clauses_open,
            can_use_credit_leverage=can_leverage,
            is_international_break=is_intl_break,
            is_schedule_placeholder=is_placeholder,
            in_pre_clause_lock_window=in_pre_clause,
            in_pre_kickoff_solvency_window=in_pre_solvency,
            in_final_lineup_guard_window=in_final_lineup,
        )
