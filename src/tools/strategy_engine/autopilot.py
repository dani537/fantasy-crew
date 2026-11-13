"""
Autonomous Competitive Strategy & Autopilot Engine (`AutopilotRunner` v3)
=========================================================================
Executes the Two-Speed Quantitative MILP + Microeconomic NPV + Stateful Memory cycle:
1. Auto-discovers user/league identity & official rules (`LeagueRulesEngine` + `RoundPhaseContext`).
2. Overlays real-time `<12s` live state (`FastDeltaRefresh`: live balance, real `offer_id`s,
   currently listed slots, 5-day clause locks, and live injury/suspension statuses).
3. Loads persistent cross-session directives & theses (`StrategyMemoryLedger`).
4. Runs `ExpectedPointsEngine`, `CausalEFRPredictor`, and `PortfolioOptimizer` v3
   (dynamic replacement cost, 6-round calendar NPV, `<= 5` market slots, 48h clause freeze).
5. Enforces Guardrails v3 (`validate_autopilot_plan_v2`) and executes according to mode:
   - `DRY_RUN`: 0 live mutations, full telemetry + executive briefing.
   - `SEMI_AUTO`:
       * Autonomous (always notifying user): Top-K (`<=5`) market listings, optimal market bids,
         minor flip/patch sales (`< IMPORTANT_SALE_MIN_EUR`), pre-kickoff solvency rescue, and MILP XI.
       * Human-Only (queued in `Decisiones_Pendientes` for `/aprobar <ID>`): ALL release clauses,
         ALL defensive shields, and ALL important/core sales (`FRANCHISE_CORE`, `STRUCTURAL_STARTER`, `>= 4M€`).
   - `FULL_AUTO`: 100% autonomous execution of all guardrail-approved operations.
6. Dispatches dual notifications via **HTML Email** and **Telegram Bot** (`NotificationHub`).
"""

import os
import json
import datetime
from typing import Dict, List, Any, Optional, Set
import pandas as pd

from src.config import (
    StrategySettings,
    GeneralSettings,
    GoogleSheetsConfig,
    resolve_active_user_identity,
)
from src.tools.data_extraction.fast_delta_refresh import FastDeltaRefresh
from src.tools.strategy_engine.league_rules import LeagueRulesEngine, RoundPhaseContext
from src.tools.strategy_engine.xp_model import ExpectedPointsEngine
from src.tools.strategy_engine.auction_model import AuctionBidModel
from src.tools.strategy_engine.clause_sniper import ClauseSniper
from src.tools.strategy_engine.strategy_memory import StrategyMemoryLedger
from src.tools.strategy_engine.portfolio_optimizer import PortfolioOptimizer
from src.tools.strategy_engine.posture_controller import StandingsPostureController
from src.tools.strategy_engine.llm_supervisor import LLMStrategicSupervisor
from src.tools.backtester.causal_clock import StrictCausalDataView, CausalEFRPredictor
from src.tools.coach_analytic.guardrails import validate_autopilot_plan_v2
from src.tools.biwenger_actions.market_actions import BiwengerMarketActions
from src.tools.notifications.notifier import NotificationHub
from src.llm_endpoints.router import TieredLLMRouter


VALID_TRIGGER_PHASES = (
    "MORNING_FULL",
    "POST_ROUND_SETTLEMENT",
    "PRE_CLAUSE_LOCK",
    "PRE_KICKOFF_SOLVENCY",
    "FINAL_LINEUP_GUARD",
)


class AutopilotRunner:
    """End-to-end autonomous controller for Fantasy Champion v3 (Zero Hardcoding)."""

    def __init__(
        self,
        mode: Optional[str] = None,
        db_path: str = "./data/warehouse/biwenger_history.db",
        dry_run: Optional[bool] = None,
        session_auth: Optional[Any] = None,
        today_date: Optional[str] = None,
        trigger_phase: str = "MORNING_FULL",
        use_live_delta: bool = True,
    ):
        raw_mode = (mode or StrategySettings.AUTOPILOT_MODE).upper()
        self.mode = raw_mode if raw_mode in ("DRY_RUN", "SEMI_AUTO", "FULL_AUTO") else "FULL_AUTO"
        self.dry_run = (self.mode == "DRY_RUN" or GeneralSettings.DRY_RUN) if dry_run is None else bool(dry_run)
        self.db_path = db_path
        self.session_auth = session_auth
        self.trigger_phase = trigger_phase if trigger_phase in VALID_TRIGGER_PHASES else "MORNING_FULL"
        self.use_live_delta = bool(use_live_delta) and (today_date is None)

        self.identity = resolve_active_user_identity()
        self.rules = LeagueRulesEngine.load()
        self.memory = StrategyMemoryLedger(
            db_path=self.db_path,
            sync_sheets=GoogleSheetsConfig.ENABLED and not self.dry_run,
        )
        self.notifier = NotificationHub()

        self.view = StrictCausalDataView(db_path=self.db_path)
        if today_date:
            self.today_date = str(today_date)[:10]
            self.view.set_date(self.today_date)
        else:
            self.today_date = self.view.all_dates[-1] if self.view.all_dates else datetime.date.today().isoformat()
            self.view.set_date(self.today_date)

        self.xp_engine = ExpectedPointsEngine()
        self.auction_model = AuctionBidModel()
        self.optimizer = PortfolioOptimizer(
            xp_engine=self.xp_engine,
            auction_model=self.auction_model,
            preventive_listing=StrategySettings.PREVENTIVE_MARKET_LISTING,
            rules=self.rules,
        )
        self.clause_sniper = self.optimizer.clause_sniper

    def load_active_context(self) -> Dict[str, Any]:
        """Loads market warehouse snapshot, overlays live `<12s` delta state, and resolves calendar phase."""
        delta_state: Dict[str, Any] = {}
        delta_refresher = FastDeltaRefresh(session_auth=self.session_auth)
        if self.use_live_delta:
            refresh_com = 1 if self.trigger_phase == "FINAL_LINEUP_GUARD" else None
            delta_state = delta_refresher.fetch(
                refresh_comuniate_jornada=refresh_com,
                max_cache_age_seconds=0 if not self.dry_run else 120,
            )
            if delta_refresher.session_auth is not None:
                self.session_auth = delta_refresher.session_auth
            # Reload rules in case /league updated settings
            self.rules = LeagueRulesEngine.load()
            self.optimizer.rules = self.rules
            self.clause_sniper.rules = self.rules

        team_name = str(delta_state.get("team_name") or self.identity.get("team_name") or "Mi Equipo")
        league_name = str(delta_state.get("league_name") or self.identity.get("league_name") or self.rules.league_name)
        league_id = int(delta_state.get("league_id") or self.identity.get("league_id") or self.rules.league_id or 0)
        team_id = int(delta_state.get("team_id") or self.identity.get("team_id") or 0)

        mkt_today = self.view.get_market_snapshot(self.today_date)
        if delta_state:
            mkt_today = FastDeltaRefresh.overlay_live_status_on_market_df(mkt_today, delta_state)

        mgrs_today = self.view.get_manager_snapshots(self.today_date)
        my_row = mgrs_today[mgrs_today["manager"] == team_name] if not mgrs_today.empty else pd.DataFrame()

        # Balance priority: 1) Live API delta_state, 2) user_info.csv / identity, 3) Warehouse snapshot
        if delta_state.get("is_live") and delta_state.get("balance") is not None:
            current_cash = float(delta_state["balance"])
        elif self.use_live_delta and self.identity.get("balance") is not None:
            current_cash = float(self.identity["balance"])
        elif not my_row.empty:
            current_cash = float(my_row.iloc[0]["saldo_disponible_exacto"])
        else:
            current_cash = float(self.identity.get("balance") or 0.0)

        # Owned squad resolution: Live API owned_players_meta if live, else warehouse snapshot / propietario_liga
        owned_meta = delta_state.get("owned_players_meta") or {}
        if delta_state.get("is_live") and owned_meta:
            owned_pids = [int(k) for k in owned_meta.keys() if str(k).isdigit()]
        elif not my_row.empty and str(my_row.iloc[0].get("plantilla_ids", "")).strip():
            owned_pids = [int(x) for x in str(my_row.iloc[0]["plantilla_ids"]).split(",") if x.strip().isdigit()]
        else:
            owned_pids = mkt_today[mkt_today["propietario_liga"] == team_name]["player_id"].astype(int).tolist()

        squad_df = mkt_today[mkt_today["player_id"].isin(owned_pids)].copy()

        # Extract sell_locked_ids (24h post-purchase) and clause_locked_ids (5-day post-purchase)
        sell_locked_ids: Set[int] = set()
        clause_locked_ids: Set[int] = set()
        for pid_s, meta in owned_meta.items():
            if meta.get("sell_locked"):
                sell_locked_ids.add(int(pid_s))
            if meta.get("clause_locked"):
                clause_locked_ids.add(int(pid_s))

        # Resolve exact calendar phase (`RoundPhaseContext`)
        full_cal = self.view.df_calendar
        now_ts = (
            pd.Timestamp.now(tz="Europe/Madrid").tz_localize(None)
            if self.use_live_delta
            else pd.Timestamp(f"{self.today_date} 09:00:00")
        )
        phase_ctx: RoundPhaseContext = self.rules.resolve_round_phase(full_cal, now_ts=now_ts)

        # Active waiting offers (with real `offer_id` when live!)
        offers_map: Dict[int, float] = {}
        offer_id_by_pid: Dict[int, int] = {}
        offers_meta_by_pid: Dict[int, Dict[str, Any]] = {}
        live_offers = delta_state.get("waiting_offers_by_pid") or {}
        if delta_state.get("is_live"):
            for pid_s, of_obj in live_offers.items():
                pid_i = int(pid_s)
                offers_map[pid_i] = float(of_obj["amount"])
                offer_id_by_pid[pid_i] = int(of_obj["offer_id"])
                offers_meta_by_pid[pid_i] = dict(of_obj)
        else:
            try:
                import sqlite3
                with sqlite3.connect(self.view.db_path) as c:
                    of_df = pd.read_sql(
                        f"SELECT player_id, precio_salida_o_oferta FROM Mercado_Diario_Historico "
                        f"WHERE fecha LIKE '{self.today_date}%' AND tipo_registro LIKE 'Oferta%'",
                        c,
                    )
                    for _, r in of_df.iterrows():
                        pid = int(pd.to_numeric(r["player_id"], errors="coerce") or 0)
                        amt = float(pd.to_numeric(r["precio_salida_o_oferta"], errors="coerce") or 0)
                        if pid > 0 and amt > 0:
                            offers_map[pid] = amt
            except Exception:
                pass
            # Only synthesize hypothetical 100% VM offers in offline DRY_RUN when DB has none
            if self.dry_run and not offers_map and not squad_df.empty:
                for _, sr in squad_df.iterrows():
                    offers_map[int(sr["player_id"])] = float(sr.get("precio", 150_000))

        # Market listings (`sales`)
        if delta_state.get("is_live") and delta_state.get("market_sales"):
            listings_df = pd.DataFrame(delta_state["market_sales"])
        else:
            listings_df = pd.DataFrame()
            try:
                import sqlite3
                with sqlite3.connect(self.view.db_path) as c:
                    listings_df = pd.read_sql(
                        f"SELECT player_id, jugador, vendedor_o_origen as seller_name, precio_mercado_dia, "
                        f"precio_salida_o_oferta as ask_price FROM Mercado_Diario_Historico "
                        f"WHERE fecha LIKE '{self.today_date}%' AND tipo_registro = 'En Venta Mercado'",
                        c,
                    )
            except Exception:
                pass

        # Indebted rivals & rival purchasing power from manager snapshots
        indebted_rivals: Set[str] = set()
        league_median_max_bid: Optional[float] = None
        max_rival_pp = 25_000_000.0
        if not mgrs_today.empty:
            rivals_df = mgrs_today[mgrs_today["manager"] != team_name]
            if not rivals_df.empty and "saldo_disponible_exacto" in rivals_df.columns:
                indebted_rivals = set(
                    rivals_df[pd.to_numeric(rivals_df["saldo_disponible_exacto"], errors="coerce") < 0]["manager"].astype(str)
                )
            if not rivals_df.empty and "puja_maxima_permitida" in rivals_df.columns:
                p_vals = pd.to_numeric(rivals_df["puja_maxima_permitida"], errors="coerce").dropna()
                if not p_vals.empty:
                    league_median_max_bid = float(p_vals.median())
                    max_rival_pp = float(p_vals.max())

        directives = self.memory.resolve_active_directives(catalog_df=mkt_today)
        standings_snapshot = self._load_causal_standings_snapshot(
            team_name=team_name,
            mgrs_today=mgrs_today,
            squad_df=squad_df,
            current_cash=current_cash,
        )
        is_solvent_now = (current_cash >= 0.0) if phase_ctx.must_be_solvent_now else (
            current_cash + float(squad_df["precio"].sum() if not squad_df.empty else 0.0) > 0.0
        )

        return {
            "date": self.today_date,
            "team_name": team_name,
            "team_id": team_id,
            "league_name": league_name,
            "league_id": league_id,
            "current_cash": current_cash,
            "is_solvent": bool(is_solvent_now),
            "standings_snapshot": standings_snapshot,
            "squad_df": squad_df,
            "market_today_df": mkt_today,
            "listings_df": listings_df,
            "offers_map": offers_map,
            "offer_id_by_pid": offer_id_by_pid,
            "offers_meta_by_pid": offers_meta_by_pid,
            "rival_incoming_offers": list(delta_state.get("rival_incoming_offers") or []),
            "incoming_counter_offers": list(delta_state.get("incoming_counter_offers") or []),
            "outgoing_bids": list(delta_state.get("outgoing_bids") or []),
            "available_max_bid": int(delta_state.get("available_max_bid") or 0),
            "committed_bids_eur": int(delta_state.get("committed_bids_eur") or 0),
            "league_bulletin": dict(delta_state.get("league_bulletin") or {}),
            "currently_listed_pids": set(delta_state.get("currently_listed_pids") or []),
            "manager_id_by_name": delta_state.get("manager_id_by_name") or {},
            "sell_locked_ids": sell_locked_ids,
            "clause_locked_ids": clause_locked_ids,
            "phase_ctx": phase_ctx,
            "target_jornada_num": phase_ctx.target_jornada_num,
            "is_kickoff_day": phase_ctx.must_be_solvent_now,
            "days_to_kickoff": phase_ctx.days_to_kickoff,
            "indebted_rivals": indebted_rivals,
            "league_median_max_bid": league_median_max_bid,
            "max_rival_pp": max_rival_pp,
            "directives": directives,
            "is_live_api": bool(delta_state.get("is_live")),
        }

    def _load_causal_standings_snapshot(
        self,
        team_name: str,
        mgrs_today: pd.DataFrame,
        squad_df: pd.DataFrame,
        current_cash: float,
    ) -> Dict[str, Any]:
        """Builds causal league standings context (`rank`, `gap_to_leader`, `rounds_settled`) from warehouse."""
        sq_val = float(pd.to_numeric(squad_df["precio"], errors="coerce").fillna(0).sum()) if not squad_df.empty else 0.0
        my_nw = float(current_cash) + sq_val
        standings: Dict[str, float] = {}
        rounds_settled = 0
        try:
            import sqlite3
            with sqlite3.connect(self.db_path) as conn:
                df_pr = pd.read_sql("SELECT Jornada, Manager, Puntos FROM Primas_Jornadas", conn)
            if not df_pr.empty:
                j_nums = set()
                for _, r in df_pr.iterrows():
                    dig = "".join(c for c in str(r.get("Jornada", "")) if c.isdigit())
                    if dig:
                        j_nums.add(int(dig))
                    mgr = str(r.get("Manager", "")).strip()
                    if mgr:
                        standings[mgr] = standings.get(mgr, 0.0) + float(pd.to_numeric(r.get("Puntos"), errors="coerce") or 0.0)
                rounds_settled = len(j_nums)
        except Exception:
            pass

        if not standings:
            return {
                "my_rank": 1,
                "total_managers": max(2, len(mgrs_today) if not mgrs_today.empty else 15),
                "my_points": 0.0,
                "leader_points": 0.0,
                "second_points": 0.0,
                "podium_points": 0.0,
                "rounds_settled": rounds_settled,
                "my_net_worth": my_nw,
                "leader_net_worth": my_nw,
            }

        ranked = sorted(standings.items(), key=lambda x: x[1], reverse=True)
        my_pts = float(standings.get(team_name, 0.0))
        my_rank = next((i + 1 for i, (m, _) in enumerate(ranked) if m == team_name), len(ranked))
        leader_mgr, leader_pts = ranked[0]
        second_pts = ranked[1][1] if len(ranked) > 1 else leader_pts
        podium_pts = ranked[min(2, len(ranked) - 1)][1]

        leader_nw = my_nw
        if not mgrs_today.empty and "patrimonio_total_estimado" in mgrs_today.columns:
            lrow = mgrs_today[mgrs_today["manager"] == leader_mgr]
            if not lrow.empty:
                leader_nw = float(pd.to_numeric(lrow.iloc[0]["patrimonio_total_estimado"], errors="coerce") or my_nw)

        return {
            "my_rank": int(my_rank),
            "total_managers": int(len(ranked)),
            "my_points": round(my_pts, 1),
            "leader_points": round(float(leader_pts), 1),
            "second_points": round(float(second_pts), 1),
            "podium_points": round(float(podium_pts), 1),
            "rounds_settled": int(rounds_settled),
            "my_net_worth": round(my_nw, 0),
            "leader_net_worth": round(leader_nw, 0),
        }

    def execute_cycle(
        self,
        generate_briefing: bool = True,
        send_notifications: bool = False,
    ) -> Dict[str, Any]:
        """Executes a complete phase-aware Autopilot v3 decision & execution cycle."""
        ctx = self.load_active_context()
        phase_ctx: RoundPhaseContext = ctx["phase_ctx"]
        directives = ctx["directives"]

        market_actions = BiwengerMarketActions(
            session=(self.session_auth.session if (self.session_auth and hasattr(self.session_auth, "session")) else None),
            league_id=ctx["league_id"] or None,
            user_id=ctx["team_id"] or None,
            dry_run=self.dry_run,
        )

        # 1. Quantitative Projections
        past_reports = self.view.get_past_match_reports()
        cal_causal = self.view.get_calendar_causal()
        comuniate_df = self.view.get_comuniate_for_round(ctx["target_jornada_num"])

        xp_table = self.xp_engine.compute_xp_table(
            df_market_today=ctx["market_today_df"],
            df_reports_past=past_reports,
            df_calendar=cal_causal,
            target_jornada_num=ctx["target_jornada_num"],
            df_comuniate_round=comuniate_df,
        )

        efr_predictor = CausalEFRPredictor(self.view)
        efr_map, shock_map = efr_predictor.predict_efr_72h_and_shocks()

        # 2. Fit Causal Auction Model
        past_bids = self.view.get_past_bids(ctx["date"])
        mgrs_today = self.view.get_manager_snapshots(ctx["date"])
        self.auction_model.fit_causal(past_bids, mgrs_today)

        # 2B. Adaptive Causal Standings Posture + Event-Driven Pre-Decision LLM Supervisor
        posture_ctrl = StandingsPostureController(enabled=StrategySettings.POSTURE_ADAPTATION_ENABLED)
        st_snap = ctx.get("standings_snapshot") or {}
        base_mods = posture_ctrl.compute_modifiers(
            my_rank=int(st_snap.get("my_rank", 1)),
            total_managers=int(st_snap.get("total_managers", 15)),
            my_points=float(st_snap.get("my_points", 0.0)),
            leader_points=float(st_snap.get("leader_points", 0.0)),
            second_points=float(st_snap.get("second_points", 0.0)),
            podium_points=float(st_snap.get("podium_points", 0.0)),
            rounds_settled=int(st_snap.get("rounds_settled", 0)),
            my_net_worth=float(st_snap.get("my_net_worth", 0.0)),
            leader_net_worth=float(st_snap.get("leader_net_worth", 0.0)),
        )
        mkt_free = ctx["market_today_df"]
        if not mkt_free.empty and "propietario_liga" in mkt_free.columns:
            free_mask = mkt_free["propietario_liga"].astype(str).isin(["Mercado", "Libre", "", "nan", "None"])
            free_prices = pd.to_numeric(mkt_free.loc[free_mask, "precio"], errors="coerce").fillna(0)
            scarcity_pre = {
                "free_ge_5m": int((free_prices >= 5_000_000).sum()),
                "free_ge_10m": int((free_prices >= 10_000_000).sum()),
            }
        else:
            scarcity_pre = {"free_ge_5m": 0, "free_ge_10m": 0}

        squad_inj_core: List[Dict[str, Any]] = []
        if not ctx["squad_df"].empty:
            for _, sr in ctx["squad_df"].iterrows():
                est = str(sr.get("estado", "ok")).strip().lower()
                pr_v = float(pd.to_numeric(sr.get("precio"), errors="coerce") or 0.0)
                if est not in ("ok", "fit", "available", "") and pr_v >= 3_000_000:
                    squad_inj_core.append({
                        "player_id": int(sr["player_id"]),
                        "nombre": str(sr.get("nombre", "")),
                        "estado": est,
                        "precio": int(pr_v),
                    })

        sup_client = None
        if self.trigger_phase in ("MORNING_FULL", "POST_ROUND_SETTLEMENT", "PRE_CLAUSE_LOCK"):
            try:
                sup_client = TieredLLMRouter.get_client(tier="daily")
            except Exception:
                sup_client = None

        supervisor = LLMStrategicSupervisor(llm_client=sup_client, tier="daily")
        supervisor_res = supervisor.evaluate_or_reuse(
            current_date=ctx["date"],
            standings_snapshot=st_snap,
            posture_modifiers=base_mods.to_dict(),
            scarcity_summary=scarcity_pre,
            squad_injuries_core=squad_inj_core,
            days_to_kickoff=ctx["days_to_kickoff"],
            hours_to_clause_lock=phase_ctx.hours_to_clause_lock,
            clauses_open=phase_ctx.clauses_open,
            target_jornada_num=ctx["target_jornada_num"],
            memory_ledger=self.memory,
        )
        final_mods = posture_ctrl.apply_llm_overrides(base_mods, supervisor_res.get("overrides"))
        causal_standings_ctx = {**st_snap, "posture_modifiers": final_mods.to_dict()}

        # 3. Generate Daily Plan via MILP + Replacement Cost NPV Solver
        raw_plan = self.optimizer.generate_daily_plan(
            current_date=ctx["date"],
            my_manager_name=ctx["team_name"],
            current_cash=ctx["current_cash"],
            squad_df=ctx["squad_df"],
            df_market_all=ctx["market_today_df"],
            market_listings_df=ctx["listings_df"],
            xp_table=xp_table,
            efr_map=efr_map,
            active_offers_map=ctx["offers_map"],
            sell_locked_ids=ctx["sell_locked_ids"],
            clause_locked_ids=ctx["clause_locked_ids"],
            untouchable_ids=directives["untouchable_ids"],
            is_kickoff_day=ctx["is_kickoff_day"],
            days_to_kickoff=ctx["days_to_kickoff"],
            recent_match_shock_map=shock_map,
            indebted_rival_managers=ctx["indebted_rivals"],
            league_median_max_bid=ctx["league_median_max_bid"],
            max_rival_purchasing_power=ctx["max_rival_pp"],
            phase_ctx=phase_ctx,
            df_calendar=self.view.df_calendar,
            memory_ledger=self.memory,
            force_sell_ids=directives["force_sell_ids"],
            blacklist_buy_ids=directives["blacklist_buy_ids"],
            priority_target_ids=directives["priority_target_ids"],
            causal_standings_ctx=causal_standings_ctx,
            llm_overrides=supervisor_res.get("overrides"),
        )

        # 4. Guardrails v3 Validation
        squad_val = float(pd.to_numeric(ctx["squad_df"]["precio"], errors="coerce").fillna(0).sum()) if not ctx["squad_df"].empty else 0.0
        p_start_map = dict(zip(xp_table["player_id"].values, xp_table["p_start"].values)) if not xp_table.empty else {}
        validated = validate_autopilot_plan_v2(
            plan=raw_plan,
            squad_value=squad_val,
            unlocked_collateral=float(raw_plan.get("top_k_liquid_collateral", squad_val * 0.5)),
            untouchable_ids=directives["untouchable_ids"],
            efr_map=efr_map,
            p_start_map=p_start_map,
            max_user_sales=self.rules.market_max_user_sales,
            credit_limit_ratio=self.rules.credit_limit_ratio,
            auto_execute_important_sales=(self.mode != "SEMI_AUTO"),
        )
        approved_plan = validated["approved_plan"]
        offers_meta_map: Dict[int, Dict[str, Any]] = ctx.get("offers_meta_by_pid") or {}
        for _sk in ("solvency_sales", "routine_sales", "all_sales"):
            for _sale in approved_plan.get(_sk, []):
                _om = offers_meta_map.get(int(_sale.get("player_id", 0) or 0), {})
                if _om:
                    _sale["buyer_name"] = str(_om.get("from_name") or "Mercado")
                    _sale["is_rival_buyer"] = bool(_om.get("is_rival", False))
                    _sale["machine_offer_amount"] = int(round(float(_om.get("machine_amount", 0) or 0)))
                    _sale["rival_premium_eur"] = int(_om.get("rival_premium_eur", 0) or 0)

        # 5. Phase-Aware Execution & SEMI_AUTO Human-Gate Queue
        execution_results: List[Dict[str, Any]] = []
        queued_decisions: List[str] = []
        offer_id_map: Dict[int, int] = ctx["offer_id_by_pid"]
        mgr_id_map: Dict[str, int] = ctx["manager_id_by_name"]
        mgr_id_map_ci: Dict[str, int] = {str(k).strip().lower(): int(v) for k, v in mgr_id_map.items() if k and v}

        is_lineup_only_phase = (self.trigger_phase == "FINAL_LINEUP_GUARD")
        is_solvency_only_phase = (self.trigger_phase == "PRE_KICKOFF_SOLVENCY")
        is_clause_window_phase = (self.trigger_phase == "PRE_CLAUSE_LOCK")

        # 5.0 Evaluate & Handle Rival Negotiations (reject overpriced counterOffers, free stale bid credit)
        rival_negotiations = self._evaluate_rival_negotiations(
            ctx=ctx,
            approved_plan=approved_plan,
            xp_table=xp_table,
        )
        base_max_bid = float(ctx.get("available_max_bid") or 0.0)
        if base_max_bid <= 0 and not ctx.get("is_live_api"):
            base_max_bid = max(
                0.0,
                float(ctx["current_cash"]) + squad_val * float(self.rules.credit_limit_ratio)
                - float(rival_negotiations.get("total_locked_credit_eur", 0) or 0),
            )
        freed_credit_eur = 0.0
        spent_cash_neg = 0.0
        spent_credit_neg = 0.0
        cancelled_outgoing_ids: Set[int] = set()
        acquired_pids_now: Set[int] = set()

        if self.trigger_phase in ("MORNING_FULL", "POST_ROUND_SETTLEMENT", "PRE_CLAUSE_LOCK"):
            neg_res = self._execute_rival_negotiations_cleanup(
                rival_negotiations=rival_negotiations,
                ctx=ctx,
                approved_plan=approved_plan,
                market_actions=market_actions,
                phase_ctx=phase_ctx,
                base_max_bid=base_max_bid,
                execution_results=execution_results,
                queued_decisions=queued_decisions,
            )
            freed_credit_eur = float(neg_res["freed_credit_eur"])
            spent_cash_neg = float(neg_res["spent_cash"])
            spent_credit_neg = float(neg_res["spent_credit"])
            cancelled_outgoing_ids = set(neg_res["cancelled_outgoing_ids"])
            acquired_pids_now = set(neg_res["acquired_pids"])

        executed_sales_cash_gain = 0.0

        # 5A. Pre-Kickoff Solvency Sales
        is_solvency_emergency = (
            phase_ctx.must_be_solvent_now
            and StrategySettings.FRIDAY_EMERGENCY_SOLVENCY
            and ctx["current_cash"] < 0
        )
        if not is_lineup_only_phase and not is_clause_window_phase:
            if self.mode == "FULL_AUTO" or (self.mode == "SEMI_AUTO" and is_solvency_emergency) or self.dry_run:
                for s in approved_plan.get("solvency_sales", []):
                    try:
                        pid = int(s["player_id"])
                        oid = int(offer_id_map.get(pid, pid if self.dry_run else 0))
                        if oid > 0:
                            res = market_actions.accept_offer(oid)
                            if not isinstance(res, dict) or res.get("status") != "ERROR":
                                executed_sales_cash_gain += float(s.get("sale_price", 0) or 0)
                            execution_results.append({"type": "SOLVENCY_SALE", "target": s["nombre"], "res": res})
                    except Exception as e:
                        execution_results.append({"type": "SOLVENCY_SALE", "target": s.get("nombre", str(s)), "res": {"status": "ERROR", "detail": str(e)}})

        # 5B. Routine Sales vs Important Sales (Human-Gated in SEMI_AUTO!)
        if not is_lineup_only_phase and not is_solvency_only_phase and not is_clause_window_phase:
            for s in approved_plan.get("routine_sales", []):
                try:
                    pid = int(s["player_id"])
                    oid = int(offer_id_map.get(pid, pid if self.dry_run else 0))
                    needs_human = bool(s.get("requires_human_approval", False))

                    if self.mode == "SEMI_AUTO" and needs_human:
                        dec_id = self.memory.queue_pending_decision(
                            action_type="IMPORTANT_SALE",
                            player_id=pid,
                            nombre=s["nombre"],
                            amount_eur=int(s["sale_price"]),
                            market_price_eur=int(s.get("market_price", s["sale_price"])),
                            counterparty=str(s.get("buyer_name") or "Mercado"),
                            offer_id=oid,
                            rationale=(
                                f"Rol={s.get('asset_role')} | {s['reason']} | "
                                f"NPV_Sell={int(s.get('npv_sell_eur', 0))/1e6:.2f}M > NPV_Hold={int(s.get('npv_hold_eur', 0))/1e6:.2f}M"
                            ),
                            payload=s,
                        )
                        queued_decisions.append(dec_id)
                    elif self.mode == "FULL_AUTO" or (self.mode == "SEMI_AUTO" and StrategySettings.AUTONOMOUS_ROUTINE_SALES_IN_SEMI_AUTO) or self.dry_run:
                        if oid > 0:
                            res = market_actions.accept_offer(oid)
                            if not isinstance(res, dict) or res.get("status") != "ERROR":
                                executed_sales_cash_gain += float(s.get("sale_price", 0) or 0)
                            execution_results.append({"type": "ROUTINE_SALE", "target": s["nombre"], "res": res})
                except Exception as e:
                    execution_results.append({"type": "ROUTINE_SALE", "target": s.get("nombre", str(s)), "res": {"status": "ERROR", "detail": str(e)}})

        # 5C. Release Clauses & Defensive Shields
        #   - SEMI_AUTO: Always human-gated in `Decisiones_Pendientes`.
        #   - FULL_AUTO: Autonomous up to `min(HARD_CAP_25M, max(6M, 45% Net Equity))` when solvent,
        #     respecting `clause_timing_mode` (IMMEDIATE | PRE_LOCK_SURGICAL | HYBRID).
        high_value_clause_alerts: List[Dict[str, Any]] = []
        eff_timing = final_mods.clause_timing_preference if final_mods.clause_timing_preference != "AUTO" else StrategySettings.CLAUSE_TIMING_MODE
        net_equity = max(0.0, float(ctx["current_cash"]) + squad_val)
        dyn_clause_cap = min(
            StrategySettings.AUTONOMOUS_CLAUSE_HARD_CAP_EUR,
            max(StrategySettings.AUTONOMOUS_CLAUSE_MAX_EUR, int(net_equity * StrategySettings.AUTONOMOUS_CLAUSE_MAX_PCT)),
        )
        running_cash_for_clauses = float(ctx["current_cash"]) + float(executed_sales_cash_gain) - float(spent_cash_neg)
        live_available_max_bid = max(
            0.0,
            float(base_max_bid) + float(freed_credit_eur) + float(executed_sales_cash_gain) - float(spent_credit_neg),
        )
        if self.trigger_phase in ("MORNING_FULL", "POST_ROUND_SETTLEMENT", "PRE_CLAUSE_LOCK"):
            for c in approved_plan.get("clause_snipes", []):
                try:
                    pid = int(c["player_id"])
                    if pid in acquired_pids_now:
                        continue
                    seller_s = str(c.get("seller", "") or "")
                    owner_uid = int(mgr_id_map.get(seller_s, 0) or mgr_id_map_ci.get(seller_s.strip().lower(), 0))
                    amt = int(c["amount"])

                    # Strategy-driven clause cadence gate before PRE_CLAUSE_LOCK
                    if self.trigger_phase != "PRE_CLAUSE_LOCK" and float(phase_ctx.hours_to_clause_lock) > 52.0:
                        high_conv = (
                            float(c.get("delta_xp_xi", 0.0)) >= 1.50
                            or scarcity_pre.get("free_ge_5m", 99) <= 6
                        )
                        if eff_timing == "PRE_LOCK_SURGICAL" and float(c.get("delta_xp_xi", 0.0)) < 2.20:
                            execution_results.append({
                                "type": "CLAUSE_DEFERRED",
                                "target": c["nombre"],
                                "amount": amt,
                                "res": {"status": "DEFERRED_TO_PRE_LOCK", "timing": eff_timing, "hours_to_lock": round(phase_ctx.hours_to_clause_lock, 1)},
                            })
                            continue
                        if eff_timing == "HYBRID" and not high_conv:
                            execution_results.append({
                                "type": "CLAUSE_DEFERRED",
                                "target": c["nombre"],
                                "amount": amt,
                                "res": {"status": "DEFERRED_TO_PRE_LOCK", "timing": eff_timing, "hours_to_lock": round(phase_ctx.hours_to_clause_lock, 1)},
                            })
                            continue

                    is_solvent_clause = bool(ctx.get("is_solvent", True)) and ((running_cash_for_clauses - amt) >= 0.0)
                    requires_approval = (
                        self.mode == "SEMI_AUTO"
                        or amt > dyn_clause_cap
                        or not is_solvent_clause
                    )
                    if requires_approval and not self.dry_run:
                        dec_id = self.memory.queue_pending_decision(
                            action_type="CLAUSE_SNIPE",
                            player_id=pid,
                            nombre=c["nombre"],
                            amount_eur=amt,
                            market_price_eur=int(c["market_price"]),
                            counterparty=seller_s,
                            owner_user_id=owner_uid,
                            rationale=(
                                f"Cláusula a {seller_s} (+{c['delta_xp_xi']:.2f} xP, {c.get('scarcity_tier', 'SCARCE')}, "
                                f"Alpha neto +{int(c['expected_surplus']):,} €)"
                            ),
                            expires_hours=max(1, int(max(1.0, phase_ctx.hours_to_clause_lock))),
                            payload=c,
                        )
                        queued_decisions.append(dec_id)
                    elif self.mode == "FULL_AUTO" or self.dry_run:
                        if owner_uid <= 0 and not self.dry_run:
                            execution_results.append({
                                "type": "CLAUSE_SNIPE",
                                "target": c["nombre"],
                                "amount": amt,
                                "res": {"status": "ERROR", "detail": f"OWNER_USER_ID_NOT_FOUND ({seller_s})"},
                            })
                            continue
                        res = market_actions.pay_clause(player_id=pid, amount=amt, owner_user_id=owner_uid)
                        if not isinstance(res, dict) or res.get("status") != "ERROR":
                            running_cash_for_clauses -= amt
                            live_available_max_bid = max(0.0, live_available_max_bid - amt)
                            acquired_pids_now.add(pid)
                        is_hi_val = amt > StrategySettings.AUTONOMOUS_CLAUSE_MAX_EUR
                        if is_hi_val:
                            high_value_clause_alerts.append({
                                "nombre": c["nombre"],
                                "amount": amt,
                                "seller": seller_s,
                                "delta_xp_xi": float(c.get("delta_xp_xi", 0.0)),
                            })
                        execution_results.append({
                            "type": "CLAUSE_SNIPE",
                            "target": c["nombre"],
                            "amount": amt,
                            "seller": seller_s,
                            "high_value_alert": is_hi_val,
                            "res": res,
                        })
                except Exception as e:
                    execution_results.append({"type": "CLAUSE_SNIPE", "target": c.get("nombre", str(c)), "res": {"status": "ERROR", "detail": str(e)}})

            for sh in approved_plan.get("defensive_shields", []):
                try:
                    pid = int(sh["player_id"])
                    spend = int(sh["recommended_cash_spend"])
                    if self.mode == "SEMI_AUTO":
                        dec_id = self.memory.queue_pending_decision(
                            action_type="DEFENSIVE_SHIELD",
                            player_id=pid,
                            nombre=sh["nombre"],
                            amount_eur=spend,
                            market_price_eur=int(sh["market_price"]),
                            rationale=sh["reason"],
                            expires_hours=max(1, int(max(1.0, phase_ctx.hours_to_clause_lock))),
                            payload=sh,
                        )
                        queued_decisions.append(dec_id)
                    elif self.mode == "FULL_AUTO" or self.dry_run:
                        res = market_actions.raise_clause(player_id=pid, increment_eur=spend)
                        if not isinstance(res, dict) or res.get("status") != "ERROR":
                            running_cash_for_clauses -= spend
                            live_available_max_bid = max(0.0, live_available_max_bid - spend)
                        execution_results.append({"type": "DEFENSIVE_SHIELD", "target": sh["nombre"], "res": res})
                except Exception as e:
                    execution_results.append({"type": "DEFENSIVE_SHIELD", "target": sh.get("nombre", str(sh)), "res": {"status": "ERROR", "detail": str(e)}})

        # 5D. Autonomous Market Bids (Guarded against duplicate outgoing bids & live available_max_bid!)
        if self.trigger_phase in ("MORNING_FULL", "POST_ROUND_SETTLEMENT"):
            can_bid = (
                self.mode == "FULL_AUTO"
                or (self.mode == "SEMI_AUTO" and StrategySettings.AUTONOMOUS_BIDS_IN_SEMI_AUTO)
                or self.dry_run
            )
            if can_bid:
                remaining_outgoing_by_pid = {
                    int(ob.get("player_id", 0) or 0): int(ob.get("amount", 0) or 0)
                    for ob in (ctx.get("outgoing_bids") or [])
                    if int(ob.get("offer_id", 0) or 0) not in cancelled_outgoing_ids
                }
                for b in approved_plan.get("market_bids", []):
                    try:
                        b_pid = int(b["player_id"])
                        b_amt = int(b["amount"])
                        if b_pid in acquired_pids_now:
                            continue
                        prev_bid = remaining_outgoing_by_pid.get(b_pid, 0)
                        if prev_bid > 0 and prev_bid >= int(b_amt * 0.90):
                            execution_results.append({
                                "type": "MARKET_BID",
                                "target": b["nombre"],
                                "res": {"status": "SKIPPED_DUPLICATE_OUTGOING_BID", "existing_amount": prev_bid},
                            })
                            continue
                        if b_amt > live_available_max_bid:
                            execution_results.append({
                                "type": "MARKET_BID",
                                "target": b["nombre"],
                                "res": {
                                    "status": "BLOCKED_AVAILABLE_MAX_BID",
                                    "amount": b_amt,
                                    "available_max_bid": int(live_available_max_bid),
                                },
                            })
                            continue
                        seller_name = str(b.get("seller", "Mercado"))
                        seller_uid = mgr_id_map.get(seller_name) if seller_name not in ("Mercado", "Libre", "") else None
                        res = market_actions.place_bid(
                            player_id=b_pid,
                            amount=b_amt,
                            seller_user_id=seller_uid,
                        )
                        if not isinstance(res, dict) or res.get("status") != "ERROR":
                            live_available_max_bid = max(0.0, live_available_max_bid - b_amt)
                        execution_results.append({"type": "MARKET_BID", "target": b["nombre"], "res": res})
                    except Exception as e:
                        execution_results.append({"type": "MARKET_BID", "target": b.get("nombre", str(b)), "res": {"status": "ERROR", "detail": str(e)}})

        rival_negotiations["effective_available_max_bid"] = int(max(0.0, live_available_max_bid))

        # 5E. Optimal MILP Starting XI Sync (Daily morning + Pre-kickoff Solvency + Final 30m Anti-Injury Guard!)
        lineup_info = approved_plan.get("lineup", {})
        if lineup_info and lineup_info.get("player_ids") and self.trigger_phase != "PRE_CLAUSE_LOCK":
            if self.mode in ("SEMI_AUTO", "FULL_AUTO") or self.dry_run:
                try:
                    res = market_actions.set_lineup(
                        formation=lineup_info["formation"],
                        ordered_player_ids=lineup_info["player_ids"],
                    )
                    execution_results.append({"type": "LINEUP_SYNC", "formation": lineup_info["formation"], "res": res})
                except Exception as e:
                    execution_results.append({"type": "LINEUP_SYNC", "formation": lineup_info.get("formation", "UNKNOWN"), "res": {"status": "ERROR", "detail": str(e)}})

        # 5F. Top-K (`<= 5`) Preventive Market Listings Sync (Unlist stale -> List top-K)
        if self.trigger_phase == "MORNING_FULL" and StrategySettings.PREVENTIVE_MARKET_LISTING:
            if self.mode in ("SEMI_AUTO", "FULL_AUTO") or self.dry_run:
                try:
                    sync_res = market_actions.sync_market_listings(
                        desired_listings=approved_plan.get("preventive_listings", []),
                        currently_listed_pids=ctx["currently_listed_pids"],
                        max_slots=self.rules.market_max_user_sales,
                    )
                    for sr in sync_res:
                        execution_results.append({"type": "MARKET_LISTING_SYNC", "res": sr})
                except Exception as e:
                    execution_results.append({"type": "MARKET_LISTING_SYNC", "res": {"status": "ERROR", "detail": str(e)}})

        # 6. Persist Cross-Session Strategic Journal Note
        scarcity_s = approved_plan.get("scarcity_summary") or {}
        self.memory.record_strategic_note(
            jornada_objetivo=ctx["target_jornada_num"],
            fase=f"{self.trigger_phase}:{phase_ctx.phase_name}",
            regimen_mercado=f"Libres>=5M:{scarcity_s.get('free_ge_5m', 0)},Libres>=10M:{scarcity_s.get('free_ge_10m', 0)}",
            plan_deuda_colateral=f"Caja={int(ctx['current_cash']):,}€ | ColateralTop5={int(approved_plan.get('top_k_liquid_collateral', 0)):,}€",
            objetivos_prioritarios=", ".join(b["nombre"] for b in approved_plan.get("market_bids", [])[:3]),
            notas_estrategicas=(
                f"XI {lineup_info.get('formation')} ({lineup_info.get('expected_points', 0):.1f} xP) | "
                f"Pujas={len(approved_plan.get('market_bids', []))} | PendientesHumano={len(queued_decisions)}"
            ),
            origen="AUTOPILOT",
        )

        if not self.dry_run and self.trigger_phase == "MORNING_FULL":
            self.memory.mark_round_event_fired(
                event_key=f"{ctx['date']}_MORNING_FULL",
                jornada_num=ctx["target_jornada_num"],
                trigger_type="MORNING_FULL",
                status="OK",
                summary=(
                    f"Modo={self.mode} | Pujas={len(approved_plan.get('market_bids', []))} | "
                    f"Escaparate={len(approved_plan.get('preventive_listings', []))}"
                ),
            )

        pending_all = self.memory.get_pending_decisions("PENDING")
        league_bulletin = ctx.get("league_bulletin") or {}

        # 7. Executive Briefing & Plain-Language Summary via TieredLLMRouter
        briefing_text = ""
        easy_summary_text = ""
        if generate_briefing:
            briefing_text = self._generate_executive_briefing(
                ctx=ctx,
                plan=approved_plan,
                blocked=validated["blocked"],
                pending=pending_all,
                posture=final_mods.to_dict(),
                supervisor_res=supervisor_res,
                rival_negotiations=rival_negotiations,
            )
            easy_summary_text = self._generate_plain_execution_summary(
                ctx=ctx,
                plan=approved_plan,
                execution_results=execution_results,
                pending=pending_all,
                projected_cash=validated["projected_cash"],
                projected_cash_with_pending=validated.get("projected_cash_with_pending_sales", validated["projected_cash"]),
                is_solvent=validated["is_safe"],
                posture=final_mods.to_dict(),
                supervisor_res=supervisor_res,
                rival_negotiations=rival_negotiations,
            )

        cycle_summary = {
            "date": ctx["date"],
            "trigger_phase": self.trigger_phase,
            "mode": self.mode,
            "dry_run": self.dry_run,
            "team_name": ctx["team_name"],
            "league_name": ctx["league_name"],
            "is_live_api": ctx["is_live_api"],
            "is_kickoff_day": ctx["is_kickoff_day"],
            "target_jornada_num": ctx["target_jornada_num"],
            "phase_context": phase_ctx.to_dict(),
            "strategy_posture": final_mods.to_dict(),
            "supervisor_report": supervisor_res,
            "rival_negotiations": rival_negotiations,
            "league_bulletin": league_bulletin,
            "available_max_bid_initial": int(base_max_bid),
            "available_max_bid_effective": int(max(0.0, live_available_max_bid)),
            "high_value_clause_alerts": high_value_clause_alerts,
            "autonomous_clause_cap_eur": int(dyn_clause_cap),
            "starting_cash": ctx["current_cash"],
            "projected_cash": validated["projected_cash"],
            "projected_cash_with_pending_sales": validated.get("projected_cash_with_pending_sales", validated["projected_cash"]),
            "is_solvent": validated["is_safe"],
            "plan": approved_plan,
            "pending_decisions": pending_all,
            "blocked_actions": validated["blocked"],
            "executions": execution_results,
            "briefing": briefing_text,
            "easy_summary": easy_summary_text,
        }

        os.makedirs("./data/autopilot", exist_ok=True)
        with open("./data/autopilot/daily_plan_latest.json", "w", encoding="utf-8") as f:
            json.dump(cycle_summary, f, ensure_ascii=False, indent=2, default=str)

        if send_notifications:
            cycle_summary["notifications"] = self.notifier.dispatch_cycle_report(
                cycle_summary=cycle_summary,
                pending_decisions=pending_all,
                trigger_label=self.trigger_phase,
            )

        return cycle_summary

    def _evaluate_rival_negotiations(
        self,
        ctx: Dict[str, Any],
        approved_plan: Dict[str, Any],
        xp_table: pd.DataFrame,
    ) -> Dict[str, Any]:
        """Summarizes rival incoming offers, counter-offers, and outgoing bids with quantitative verdicts."""
        xp_idx = ClauseSniper._build_xp_idx(xp_table) if (xp_table is not None and not xp_table.empty) else {}
        sold_pids = {int(s.get("player_id", 0) or 0) for s in (approved_plan.get("all_sales") or [])}
        pending_sale_pids = {
            int(s.get("player_id", 0) or 0)
            for s in (approved_plan.get("routine_sales") or [])
            if self.mode == "SEMI_AUTO" and s.get("requires_human_approval")
        }
        xi_floor_by_pos: Dict[str, float] = {"GK": 2.5, "DF": 3.0, "MF": 3.2, "FW": 3.2}
        slots_by_line: Dict[str, List[float]] = {"GK": [], "DF": [], "MF": [], "FW": []}
        for sl in ((approved_plan.get("lineup") or {}).get("slots") or []):
            slots_by_line.setdefault(str(sl.get("linea", "MF")), []).append(float(sl.get("xp", 0.0)))
        for ln, vals in slots_by_line.items():
            if vals:
                xi_floor_by_pos[ln] = min(vals)

        outgoing_by_pid: Dict[int, Dict[str, Any]] = {}
        for ob in (ctx.get("outgoing_bids") or []):
            opid = int(ob.get("player_id", 0) or 0)
            if opid > 0:
                outgoing_by_pid[opid] = ob

        incoming_rival: List[Dict[str, Any]] = []
        for ro in (ctx.get("rival_incoming_offers") or []):
            pid = int(ro.get("player_id", 0) or 0)
            amt = int(ro.get("amount", 0) or 0)
            vm = int(ro.get("market_price", 0) or 0)
            m_amt = int(ro.get("machine_amount", 0) or 0)
            prem = int(ro.get("rival_premium_eur", 0) or 0)
            over_vm_pct = round(((amt - vm) / vm) * 100.0, 1) if vm > 0 else 0.0
            if pid in pending_sale_pids:
                bot_action = "PENDIENTE_OK"
                action_label = "⏳ Venta importante en espera de /aprobar"
            elif pid in sold_pids:
                bot_action = "ACEPTADA_SIMULACRO" if self.dry_run else "ACEPTADA"
                action_label = "🤝 Venta asignada al rival [Simulacro]" if self.dry_run else "✅ Oferta aceptada (NPV_SELL > NPV_HOLD)"
            else:
                bot_action = "RECHAZADA_SE_CONSERVA"
                action_label = "🛡️ Descartada — NPV_HOLD superior, se conserva"

            incoming_rival.append({
                "offer_id": int(ro.get("offer_id", 0) or 0),
                "player_id": pid,
                "nombre": str(ro.get("nombre") or f"ID {pid}"),
                "from_name": str(ro.get("from_name") or "Rival"),
                "amount": amt,
                "market_price": vm,
                "machine_amount": m_amt,
                "rival_premium_eur": prem,
                "over_vm_pct": over_vm_pct,
                "age_hours": ro.get("age_hours"),
                "bot_action": bot_action,
                "action_label": action_label,
            })

        counter_pids: Set[int] = set()
        counter_offers: List[Dict[str, Any]] = []
        for co in (ctx.get("incoming_counter_offers") or []):
            pid = int(co.get("player_id", 0) or 0)
            counter_pids.add(pid)
            amt = int(co.get("amount", 0) or 0)
            vm = int(co.get("market_price", 0) or 0)
            sub24 = int(co.get("subida_24h", 0) or 0)
            over_vm_pct = round(((amt - vm) / vm) * 100.0, 1) if vm > 0 else 0.0
            xp_row = xp_idx.get(pid, {})
            xp_best = float(xp_row.get("xp_best", 0.0))
            best_ln = str(xp_row.get("best_line", "MF"))
            delta_xp_xi = round(xp_best - float(xi_floor_by_pos.get(best_ln, 3.2)), 2) if xp_row else 0.0
            is_avail = bool(xp_row.get("is_available", True)) if xp_row else True
            paired_ob = outgoing_by_pid.get(pid, {})

            if (vm > 0 and (over_vm_pct >= 20.0 or (over_vm_pct >= 12.0 and sub24 < 0))) or not is_avail:
                verdict = "SOBREPRECIO_RECHAZAR"
                verdict_label = f"⛔ Sobreprecio ({over_vm_pct:+.1f}% vs VM) — Conviene rechazar"
            elif xp_best >= 4.0 and delta_xp_xi >= 0.45 and over_vm_pct <= 12.0 and sub24 >= -25_000:
                verdict = "ACEPTABLE_SI_MEJORA_XI"
                verdict_label = f"✅ Contraoferta competitiva (+{delta_xp_xi:.2f} xP XI, {over_vm_pct:+.1f}% vs VM)"
            elif xp_best >= 3.2 and over_vm_pct <= 12.0 and sub24 >= -40_000:
                verdict = "RAZONABLE_VALORAR"
                verdict_label = f"👀 Precio competitivo ({over_vm_pct:+.1f}% vs VM · {xp_best:.1f} xP)"
            else:
                verdict = "CARO_DESCARTAR"
                verdict_label = f"⚠️ Poco margen ({over_vm_pct:+.1f}% vs VM) — Descartar"

            counter_offers.append({
                "offer_id": int(co.get("offer_id", 0) or 0),
                "player_id": pid,
                "nombre": str(co.get("nombre") or f"ID {pid}"),
                "from_name": str(co.get("from_name") or "Rival"),
                "amount": amt,
                "market_price": vm,
                "subida_24h": sub24,
                "over_vm_pct": over_vm_pct,
                "xp_best": round(xp_best, 2),
                "delta_xp_xi": delta_xp_xi,
                "age_hours": co.get("age_hours"),
                "matching_outgoing_offer_id": int(paired_ob.get("offer_id", 0) or 0),
                "matching_outgoing_amount": int(paired_ob.get("amount", 0) or 0),
                "verdict": verdict,
                "verdict_label": verdict_label,
                "bot_action": "EVALUADA",
                "action_label": verdict_label,
            })

        outgoing_list: List[Dict[str, Any]] = []
        total_locked = 0
        for ob in (ctx.get("outgoing_bids") or []):
            pid = int(ob.get("player_id", 0) or 0)
            amt = int(ob.get("amount", 0) or 0)
            total_locked += amt
            vm = int(ob.get("market_price", 0) or 0)
            over_vm_pct = round(((amt - vm) / vm) * 100.0, 1) if vm > 0 else 0.0
            age_h = ob.get("age_hours")
            has_co = pid in counter_pids
            if has_co:
                st_lbl = "Con contraoferta rival"
            elif age_h is not None and float(age_h) >= 48.0:
                st_lbl = f"Estancada ({int(float(age_h) // 24)}d · bloquea margen)"
            else:
                st_lbl = "Esperando respuesta"
            outgoing_list.append({
                "offer_id": int(ob.get("offer_id", 0) or 0),
                "player_id": pid,
                "nombre": str(ob.get("nombre") or f"ID {pid}"),
                "to_name": str(ob.get("to_name") or "Mercado"),
                "amount": amt,
                "market_price": vm,
                "over_vm_pct": over_vm_pct,
                "age_hours": age_h,
                "has_counter_offer": has_co,
                "bot_action": "ACTIVA",
                "status_label": st_lbl,
            })

        return {
            "incoming_rival_offers": incoming_rival,
            "counter_offers": counter_offers,
            "outgoing_bids": outgoing_list,
            "total_locked_credit_eur": int(total_locked),
            "freed_credit_eur": 0,
            "remaining_locked_credit_eur": int(total_locked),
        }

    def _execute_rival_negotiations_cleanup(
        self,
        rival_negotiations: Dict[str, Any],
        ctx: Dict[str, Any],
        approved_plan: Dict[str, Any],
        market_actions: BiwengerMarketActions,
        phase_ctx: RoundPhaseContext,
        base_max_bid: float,
        execution_results: List[Dict[str, Any]],
        queued_decisions: List[str],
    ) -> Dict[str, Any]:
        """Executes safe negotiation actions: reject overpriced counterOffers, accept high-alpha deals, cancel stale bids."""
        can_clean_bids = (
            self.mode == "FULL_AUTO"
            or (self.mode == "SEMI_AUTO" and StrategySettings.AUTONOMOUS_BIDS_IN_SEMI_AUTO)
            or self.dry_run
        )
        freed_credit_eur = 0.0
        spent_cash = 0.0
        spent_credit = 0.0
        cancelled_outgoing_ids: Set[int] = set()
        acquired_pids: Set[int] = set()
        net_squad_size = (
            len(ctx.get("squad_df", pd.DataFrame()))
            - len(approved_plan.get("all_sales") or [])
        )
        max_squad = int(getattr(self.rules, "team_max_size", 22) or 22)

        # 1. Process incoming counterOffers from rivals
        for co in (rival_negotiations.get("counter_offers") or []):
            co_oid = int(co.get("offer_id", 0) or 0)
            co_pid = int(co.get("player_id", 0) or 0)
            co_amt = int(co.get("amount", 0) or 0)
            orig_oid = int(co.get("matching_outgoing_offer_id", 0) or 0)
            orig_amt = int(co.get("matching_outgoing_amount", 0) or 0)
            verdict = str(co.get("verdict") or "")

            if verdict == "SOBREPRECIO_RECHAZAR" and can_clean_bids and co_oid > 0:
                try:
                    res = market_actions.reject_offer(co_oid)
                    execution_results.append({
                        "type": "RIVAL_COUNTER_REJECT",
                        "target": co["nombre"],
                        "amount": co_amt,
                        "res": res,
                    })
                except Exception as e:
                    execution_results.append({"type": "RIVAL_COUNTER_REJECT", "target": co["nombre"], "res": {"status": "ERROR", "detail": str(e)}})
                if orig_oid > 0 and orig_oid not in cancelled_outgoing_ids:
                    try:
                        c_res = market_actions.cancel_offer(orig_oid)
                        if not isinstance(c_res, dict) or c_res.get("status") != "ERROR":
                            cancelled_outgoing_ids.add(orig_oid)
                            freed_credit_eur += float(orig_amt)
                        execution_results.append({
                            "type": "RIVAL_BID_CANCEL",
                            "target": co["nombre"],
                            "amount": orig_amt,
                            "res": c_res,
                        })
                    except Exception as e:
                        execution_results.append({"type": "RIVAL_BID_CANCEL", "target": co["nombre"], "res": {"status": "ERROR", "detail": str(e)}})
                co["bot_action"] = "RECHAZADA_SIMULACRO" if self.dry_run else "RECHAZADA_AUTO"
                co["action_label"] = (
                    "⛔ Contraoferta rechazada y puja cancelada [Simulacro]"
                    if self.dry_run
                    else "⛔ Contraoferta rechazada y puja cancelada (+crédito liberado)"
                )

            elif verdict == "ACEPTABLE_SI_MEJORA_XI" and co_oid > 0:
                avail_now = float(base_max_bid) + freed_credit_eur - spent_credit
                has_slot = (net_squad_size + len(acquired_pids)) < max_squad
                if has_slot and co_amt <= avail_now:
                    if self.mode == "SEMI_AUTO" and not self.dry_run:
                        dec_id = self.memory.queue_pending_decision(
                            action_type="RIVAL_COUNTER_OFFER",
                            player_id=co_pid,
                            nombre=co["nombre"],
                            amount_eur=co_amt,
                            market_price_eur=int(co.get("market_price", co_amt)),
                            counterparty=str(co.get("from_name", "Rival")),
                            offer_id=co_oid,
                            rationale=co.get("verdict_label", "Contraoferta rival competitiva"),
                            expires_hours=max(4, min(24, int(max(4.0, phase_ctx.hours_to_kickoff)))),
                            payload=co,
                        )
                        queued_decisions.append(dec_id)
                        co["bot_action"] = "PENDIENTE_OK"
                        co["action_label"] = "⏳ Contraoferta favorable en espera de /aprobar"
                    elif self.mode == "FULL_AUTO" or self.dry_run:
                        try:
                            a_res = market_actions.accept_offer(co_oid)
                            if not isinstance(a_res, dict) or a_res.get("status") != "ERROR":
                                spent_cash += float(co_amt)
                                spent_credit += float(co_amt)
                                acquired_pids.add(co_pid)
                                if orig_oid > 0 and orig_oid not in cancelled_outgoing_ids:
                                    cancelled_outgoing_ids.add(orig_oid)
                                    freed_credit_eur += float(orig_amt)
                            execution_results.append({
                                "type": "RIVAL_COUNTER_ACCEPT",
                                "target": co["nombre"],
                                "amount": co_amt,
                                "res": a_res,
                            })
                            co["bot_action"] = "ACEPTADA_SIMULACRO" if self.dry_run else "ACEPTADA_AUTO"
                            co["action_label"] = "✅ Contraoferta aceptada [Simulacro]" if self.dry_run else "✅ Contraoferta aceptada automáticamente"
                        except Exception as e:
                            execution_results.append({"type": "RIVAL_COUNTER_ACCEPT", "target": co["nombre"], "res": {"status": "ERROR", "detail": str(e)}})

        # 2. Cancel stale unanswered outgoing bids (>= 48h) that lock available_max_bid
        for ob in (rival_negotiations.get("outgoing_bids") or []):
            ob_oid = int(ob.get("offer_id", 0) or 0)
            ob_amt = int(ob.get("amount", 0) or 0)
            if ob_oid in cancelled_outgoing_ids:
                ob["bot_action"] = "CANCELADA_SIMULACRO" if self.dry_run else "CANCELADA_AUTO"
                ob["status_label"] = "🧹 Puja cancelada tras contraoferta cara [Simulacro]" if self.dry_run else "🧹 Puja cancelada tras contraoferta cara"
                continue
            age_h = ob.get("age_hours")
            if can_clean_bids and ob_oid > 0 and not ob.get("has_counter_offer") and age_h is not None and float(age_h) >= 48.0:
                try:
                    c_res = market_actions.cancel_offer(ob_oid)
                    if not isinstance(c_res, dict) or c_res.get("status") != "ERROR":
                        cancelled_outgoing_ids.add(ob_oid)
                        freed_credit_eur += float(ob_amt)
                    execution_results.append({
                        "type": "RIVAL_BID_CANCEL",
                        "target": ob["nombre"],
                        "amount": ob_amt,
                        "res": c_res,
                    })
                    ob["bot_action"] = "CANCELADA_STALE_SIMULACRO" if self.dry_run else "CANCELADA_STALE"
                    ob["status_label"] = (
                        f"🧹 Puja estancada ({int(float(age_h) // 24)}d) cancelada [Simulacro · +{ob_amt/1e6:.2f}M margen]"
                        if self.dry_run
                        else f"🧹 Puja estancada ({int(float(age_h) // 24)}d) cancelada (+{ob_amt/1e6:.2f}M margen)"
                    )
                except Exception as e:
                    execution_results.append({"type": "RIVAL_BID_CANCEL", "target": ob["nombre"], "res": {"status": "ERROR", "detail": str(e)}})

        rival_negotiations["freed_credit_eur"] = int(freed_credit_eur)
        rival_negotiations["remaining_locked_credit_eur"] = max(
            0, int(rival_negotiations.get("total_locked_credit_eur", 0) or 0) - int(freed_credit_eur)
        )
        return {
            "freed_credit_eur": freed_credit_eur,
            "spent_cash": spent_cash,
            "spent_credit": spent_credit,
            "cancelled_outgoing_ids": cancelled_outgoing_ids,
            "acquired_pids": acquired_pids,
        }

    @staticmethod
    def _format_bulletin_prompt_lines(league_bulletin: Optional[Dict[str, Any]]) -> str:
        if not isinstance(league_bulletin, dict) or not any(league_bulletin.values()):
            return "Sin novedades recientes registradas en el muro de la liga."
        lines: List[str] = []
        ms = league_bulletin.get("my_standings") or {}
        if ms:
            lines.append(
                f"Clasificación: #{ms.get('rank')}/{ms.get('total_managers')} ({ms.get('points')} pts, "
                f"a {ms.get('points_to_leader', 0)} pts del líder {ms.get('leader_name', '')})."
            )
        for c in (league_bulletin.get("clauses") or [])[:4]:
            tag = " [TE AFECTA]" if c.get("involves_me") else ""
            lines.append(
                f"Clausulazo{tag}: {c.get('to_name')} fichó a {c.get('nombre')} ({c.get('from_name')}) por {int(c.get('amount', 0)):,}€ ({c.get('date_iso', '')})."
            )
        for d in (league_bulletin.get("manager_deals") or [])[:4]:
            tag = " [TUYO]" if d.get("involves_me") else ""
            bids_txt = f" ({d.get('bid_count')} pujas)" if int(d.get("bid_count", 1) or 1) > 1 else ""
            lines.append(
                f"Fichaje{tag}: {d.get('to_name')} -> {d.get('nombre')} desde {d.get('from_name')} por {int(d.get('amount', 0)):,}€{bids_txt}."
            )
        for sh in (league_bulletin.get("clause_shields") or [])[:2]:
            lines.append(f"Blindaje rival: {sh.get('user_name')} subió cláusula de {sh.get('nombre')} hasta {int(sh.get('release_clause', 0)):,}€.")
        for inj in (league_bulletin.get("injury_alerts") or [])[:3]:
            lines.append(f"Parte médico LaLiga: {inj.get('nombre')} ({inj.get('team')}, {int(inj.get('price', 0))//1000}k€) -> {inj.get('status')}.")
        return " | ".join(lines) or "Sin novedades destacadas."

    @staticmethod
    def _format_rival_neg_prompt_lines(rival_negotiations: Optional[Dict[str, Any]]) -> str:
        if not isinstance(rival_negotiations, dict):
            return "Ninguna negociación activa con rivales."
        parts: List[str] = []
        for ro in (rival_negotiations.get("incoming_rival_offers") or []):
            extra = f" (+{ro['rival_premium_eur']:,}€ vs Máquina)" if ro.get("rival_premium_eur") else ""
            parts.append(
                f"Oferta de {ro['from_name']} por tu {ro['nombre']}: {ro['amount']:,}€{extra} -> {ro.get('action_label', '')}"
            )
        for co in (rival_negotiations.get("counter_offers") or []):
            parts.append(
                f"Contraoferta de {co['from_name']} por {co['nombre']}: pide {co['amount']:,}€ "
                f"(VM {co['market_price']:,}€, {co['over_vm_pct']:+.1f}%) -> {co.get('action_label') or co.get('verdict_label', '')}"
            )
        for ob in (rival_negotiations.get("outgoing_bids") or []):
            parts.append(
                f"Puja tuya hacia {ob['to_name']} por {ob['nombre']}: {ob['amount']:,}€ ({ob.get('status_label', '')})"
            )
        if rival_negotiations.get("freed_credit_eur"):
            parts.append(
                f"Crédito liberado al limpiar pujas/contraofertas: +{rival_negotiations['freed_credit_eur']:,}€ "
                f"(Puja máxima efectiva restante: {rival_negotiations.get('effective_available_max_bid', 0):,}€)"
            )
        elif rival_negotiations.get("total_locked_credit_eur"):
            parts.append(f"Crédito total comprometido en pujas abiertas: {rival_negotiations['total_locked_credit_eur']:,}€")
        return " | ".join(parts) if parts else "Ninguna negociación activa con rivales."

    def _generate_executive_briefing(
        self,
        ctx: Dict[str, Any],
        plan: Dict[str, Any],
        blocked: List[Dict[str, Any]],
        pending: List[Dict[str, Any]],
        posture: Optional[Dict[str, Any]] = None,
        supervisor_res: Optional[Dict[str, Any]] = None,
        rival_negotiations: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Calls TieredLLMRouter to synthesize a personalized, memory-aware executive briefing."""
        phase_ctx: RoundPhaseContext = ctx["phase_ctx"]
        tier = "high_stakes" if (ctx["is_kickoff_day"] or self.trigger_phase != "MORNING_FULL") else "daily"

        is_semi = (self.mode == "SEMI_AUTO")
        sales_str = ", ".join(
            f"{s['nombre']} ({s['sale_price']:,}€"
            f"{' a ' + str(s['buyer_name']) if s.get('is_rival_buyer') and s.get('buyer_name') else ''}"
            f" · {s['reason']}"
            f"{' [Requiere OK]' if (is_semi and s.get('requires_human_approval')) else ''})"
            for s in plan.get("all_sales", [])
        ) or "Ninguna"
        clauses_str = ", ".join(
            f"{c['nombre']} ({c['amount']:,}€ a {c['seller']}, +{c['delta_xp_xi']} xP "
            f"{'[En cola OK humano]' if is_semi else '[Ejecutada autónomamente]'})"
            for c in plan.get("clause_snipes", [])
        ) or "Ninguno"
        shields_str = ", ".join(
            f"{sh['nombre']} (+{sh['recommended_cash_spend']:,}€)"
            for sh in plan.get("defensive_shields", [])
        ) or "Ninguno"
        bids_str = ", ".join(
            f"{b['nombre']} ({b['amount']:,}€ a {b['seller']}, +{b['delta_xp_xi']} xP)"
            for b in plan.get("market_bids", [])
        ) or "Ninguna"
        listings_str = ", ".join(
            f"{p['nombre']} ({p['asking_price']:,}€)" for p in plan.get("preventive_listings", [])
        ) or "Ninguno"
        rival_str = self._format_rival_neg_prompt_lines(rival_negotiations)
        bulletin_str = self._format_bulletin_prompt_lines(ctx.get("league_bulletin"))
        lineup_str = f"Formación {plan['lineup']['formation']} (xP esperados: {plan['lineup']['expected_points']:.1f} pts)"
        mem_brief = self.memory.build_context_brief_for_llm()
        sec3_title = (
            "3. Decisiones Reservadas para Tu Confirmación (Cláusulas / Ventas Importantes)"
            if is_semi
            else "3. Operaciones de Alto Impacto (Cláusulas, Blindajes y Ventas Estructurales — 100% Autónomas)"
        )

        sup_note = ""
        if posture:
            sup_note = (
                f"- Postura Clasificatoria Dinámica: {posture.get('posture')} (#{posture.get('my_rank')}/{posture.get('total_managers')}, "
                f"brecha {posture.get('gap_to_leader', 0):+.0f} pts | Supervisor {(supervisor_res or {}).get('trigger', 'CACHED')}): "
                f"{posture.get('rationale', '')}\n"
            )
        prompt = f"""
Actúa como Director Deportivo Cuantitativo para '{ctx['team_name']}' en la liga Biwenger '{ctx['league_name']}'.
Genera un briefing ejecutivo conciso en texto limpio (máximo 4 bloques cortos con viñetas) para la sesión `{self.trigger_phase}` ({ctx['date']}):

- Modo: {self.mode} (Dry Run: {self.dry_run}) | Fase Calendario J{ctx['target_jornada_num']}: {phase_ctx.phase_name}
{sup_note}- Inicio J{ctx['target_jornada_num']}: {phase_ctx.next_kickoff_ts} ({phase_ctx.hours_to_kickoff:.1f}h) | Cláusulas abiertas ({self.rules.clause_disabled_hours}h): {phase_ctx.clauses_open} | Parón FIFA: {phase_ctx.is_international_break}
- Caja Actual: {ctx['current_cash']:,} € -> Proyectada hoy tras todas las ventas del plan: {plan['post_sales_cash']:,} € | Puja máx operativa hoy: {int((rival_negotiations or {}).get('effective_available_max_bid', ctx.get('available_max_bid', 0))):,} € (Nota Biwenger: las pujas son a sobre cerrado respaldadas por nuestro Colateral Líquido Top-{self.rules.market_max_user_sales}: {plan.get('top_k_liquid_collateral', 0):,} €)
- Pujas Autónomas Lanzadas: {bids_str}
- Ventas Ejecutadas Hoy (NPV / Arbitraje / Solvencia): {sales_str}
- Negociaciones Directas con Rivales (Ofertas recibidas / Contraofertas / Pujas abiertas y crédito liberado): {rival_str}
- Cláusulas Quirúrgicas Evaluadas: {clauses_str} | Blindajes Defensivos: {shields_str}
- Últimas Noticias de la Liga (Clausulazos, fichajes rivales y bajas LaLiga): {bulletin_str}
- Escaparate Preventivo Top-{len(plan.get('preventive_listings', []))} (+5% VM, máx oficial {self.rules.market_max_user_sales}): {listings_str} (Nota: listar flips/parches en el escaparate cuesta 0€ y es intencional para recibir ofertas diarias ±5% de la Máquina como colateral y opción de venta gratis; NO implica venderlos salvo oferta pico o necesidad de solvencia)
- Once Titular Óptimo MILP: {lineup_str}
- Decisiones Esperando Confirmación Humana (`/aprobar`): {len(pending)}

{mem_brief}

Estructura:
1. Diagnóstico Financiero, Noticias de la Liga y Escasez
2. Operaciones Autónomas Ejecutadas (Pujas, Limpieza con Rivales, Escaparate y Once)
{sec3_title}
4. Continuidad Estratégica y Próximos Movimientos
"""
        try:
            client = TieredLLMRouter.get_client(tier=tier)
            resp = client.call(prompt=prompt, system_prompt="Eres un Director Deportivo analítico, prudente con activos escasos y letal en arbitraje.")
            return str(resp).strip()
        except Exception as e:
            return (
                f"(Briefing sintético local — LLM no disponible: {e})\n\n"
                f"• Estado J{ctx['target_jornada_num']} ({phase_ctx.phase_name}): Caja {ctx['current_cash']:,} € | Cláusulas abiertas: {phase_ctx.clauses_open}.\n"
                f"• Once MILP: {lineup_str}.\n"
                f"• Pujas autónomas: {bids_str} | Escaparate Top-{len(plan.get('preventive_listings', []))}: {listings_str}.\n"
                f"• Ventas: {sales_str} | Rivales: {rival_str} | Cláusulas: {clauses_str}."
            )

    def _generate_plain_execution_summary(
        self,
        ctx: Dict[str, Any],
        plan: Dict[str, Any],
        execution_results: List[Dict[str, Any]],
        pending: List[Dict[str, Any]],
        projected_cash: float,
        is_solvent: bool,
        posture: Optional[Dict[str, Any]] = None,
        supervisor_res: Optional[Dict[str, Any]] = None,
        rival_negotiations: Optional[Dict[str, Any]] = None,
        projected_cash_with_pending: Optional[float] = None,
    ) -> str:
        """Generates a short, plain-language LLM summary of everything done in this run for the email footer."""
        lu = plan.get("lineup") or {}
        xi_names = ", ".join(s["nombre"] for s in (lu.get("slots") or [])) or "Sin cambios"
        bids = ", ".join(f"{b['nombre']} ({int(b['amount']):,} €)" for b in (plan.get("market_bids") or [])) or "Ninguna"
        sales = ", ".join(
            f"{s['nombre']} ({int(s['sale_price']):,} €"
            f"{' a ' + str(s['buyer_name']) if s.get('is_rival_buyer') and s.get('buyer_name') else ''})"
            for s in (plan.get("all_sales") or [])
        ) or "Ninguna"
        clause_status_by_target: Dict[str, str] = {}
        for ex in execution_results:
            tp = str(ex.get("type") or "")
            tgt = str(ex.get("target") or "")
            if tp in ("CLAUSE_DEFERRED", "CLAUSE_SNIPE") and tgt:
                st = str((ex.get("res") or {}).get("status") or "")
                if st == "DEFERRED_TO_PRE_LOCK":
                    clause_status_by_target[tgt] = "DIFERIDO A VENTANA PRE-CIERRE"
                elif st == "ERROR":
                    clause_status_by_target[tgt] = "ERROR AL EJECUTAR"
                elif st in ("DRY_RUN", "DRY_RUN_SIMULATED"):
                    clause_status_by_target[tgt] = "SIMULADO"
                else:
                    clause_status_by_target[tgt] = "EJECUTADO"
        clauses = ", ".join(
            f"{c['nombre']} ({int(c['amount']):,} € a {c.get('seller', '')} · "
            f"{clause_status_by_target.get(str(c['nombre']), 'EN COLA /aprobar' if self.mode == 'SEMI_AUTO' else 'PLANIFICADO')})"
            for c in (plan.get("clause_snipes") or [])
        ) or "Ninguno"
        shields = ", ".join(f"{sh['nombre']} (+{int(sh['recommended_cash_spend']):,} €)" for sh in (plan.get("defensive_shields") or [])) or "Ninguno"
        listings = ", ".join(f"{p['nombre']} ({int(p['asking_price']):,} €)" for p in (plan.get("preventive_listings") or [])) or "Ninguno"
        rival_str = self._format_rival_neg_prompt_lines(rival_negotiations)
        bulletin_str = self._format_bulletin_prompt_lines(ctx.get("league_bulletin"))
        cash_pend_txt = ""
        if projected_cash_with_pending is not None and abs(float(projected_cash_with_pending) - float(projected_cash)) > 10_000:
            cash_pend_txt = f" (o {int(projected_cash_with_pending):,} € si apruebas las ventas importantes)"
        errors = [
            f"{ex.get('type')}: {ex.get('target', '')} -> {ex.get('res', {}).get('detail', '')}"
            for ex in execution_results
            if isinstance(ex.get("res"), dict) and ex["res"].get("status") == "ERROR"
        ]

        prompt = f"""
Resume en español muy claro, cercano y fácil de leer (máximo 5-6 puntos cortos con viñetas, SIN jerga técnica como NPV, MILP, xP, EFR o roles internos) todo lo que acaba de hacer hoy el bot de Biwenger para '{ctx['team_name']}':

- Fecha / Fase: {ctx['date']} ({self.trigger_phase}) | Modo: {self.mode} (Simulacro={self.dry_run})
- Enfoque estratégico hoy: {(posture or {}).get('posture', 'AUTO')} (#{(posture or {}).get('my_rank', '?')}/{(posture or {}).get('total_managers', '?')}, brecha {(posture or {}).get('gap_to_leader', 0):+.0f} pts) — {(posture or {}).get('extra', {}).get('llm_rationale') or (posture or {}).get('rationale', 'Equilibrado')}
- Dinero en caja ahora: {int(ctx['current_cash']):,} € | Saldo proyectado tras ventas: {int(projected_cash):,} €{cash_pend_txt} | Puja máx operativa actual: {int((rival_negotiations or {}).get('effective_available_max_bid', ctx.get('available_max_bid', 0))):,} € | Solvente: {'Sí' if is_solvent else 'No'}
- Alineación guardada para J{ctx['target_jornada_num']}: {lu.get('formation', '4-4-2')} ({xi_names})
- Pujas realizadas en el mercado: {bids}
- Jugadores vendidos: {sales}
- Ofertas y negociaciones con rivales (ofertas recibidas, contraofertas y pujas abiertas): {rival_str}
- Clausulazos: {clauses}
- Blindajes de cláusula propia: {shields}
- Jugadores puestos en venta en el escaparate (solo para recibir ofertas de la Máquina, sin obligación de vender): {listings}
- Últimas noticias destacadas de tu liga (clausulazos entre rivales, fichajes y parte médico): {bulletin_str}
- Decisiones esperando tu aprobación manual: {len(pending)}
- Errores durante la ejecución: {', '.join(errors) if errors else 'Ninguno'}

Redacta directamente el resumen final para el usuario: incluye en 1-2 viñetas lo más sonado del boletín de la liga (clausulazos y movimientos de rivales), qué se ha hecho hoy con tu equipo (incluyendo contraofertas o limpieza de pujas bloqueadas), cómo queda tu saldo y si tienes que aprobar algo manualmente.
"""
        try:
            client = TieredLLMRouter.get_client(tier="daily")
            resp = client.call(
                prompt=prompt,
                system_prompt="Eres un asistente claro y directo que explica en lenguaje cotidiano lo que ha hecho hoy el gestor automático de Biwenger.",
            )
            return str(resp).strip()
        except Exception:
            return (
                f"• Alineación J{ctx['target_jornada_num']}: {lu.get('formation', '4-4-2')} ({xi_names}).\n"
                f"• Caja: {int(ctx['current_cash']):,} € (proyectado {int(projected_cash):,} €{cash_pend_txt}).\n"
                f"• Pujas: {bids} | Ventas: {sales} | Cláusulas: {clauses} | Escaparate: {listings}.\n"
                f"• Negociaciones con rivales: {rival_str}\n"
                f"• Noticias de la liga: {bulletin_str}\n"
                f"• Pendientes de tu aprobación: {len(pending)}."
            )


def main():
    import sys
    import argparse
    import traceback
    parser = argparse.ArgumentParser(description="Run Fantasy Champion v3 Autopilot")
    parser.add_argument("--mode", default=None, choices=["DRY_RUN", "SEMI_AUTO", "FULL_AUTO"])
    parser.add_argument("--phase", default="MORNING_FULL", choices=list(VALID_TRIGGER_PHASES))
    parser.add_argument("--date", default=None, help="Historical/override date YYYY-MM-DD (disables live API delta)")
    parser.add_argument("--notify", action="store_true", help="Send Email + Telegram report after execution")
    parser.add_argument("--no-briefing", action="store_true", help="Skip LLM text synthesis")
    args = parser.parse_args()

    eff_mode = args.mode or StrategySettings.AUTOPILOT_MODE
    print(f"🚀 Iniciando Autopilot v3 | Modo: {eff_mode} | Fase: {args.phase} | Fecha: {args.date or 'LIVE'}...")
    try:
        runner = AutopilotRunner(
            mode=eff_mode,
            today_date=args.date,
            trigger_phase=args.phase,
            use_live_delta=(args.date is None),
        )
        res = runner.execute_cycle(
            generate_briefing=(not args.no_briefing),
            send_notifications=args.notify,
        )

        print("\n=== RESUMEN EJECUTIVO AUTOPILOT v3 ===")
        print(f"Equipo: {res['team_name']} ({res['league_name']}) | Fecha: {res['date']} | Fase: {res['phase_context']['phase_name']}")
        print(f"Modo: {res['mode']} (DryRun={res['dry_run']}) | Solvente: {res['is_solvent']} | Cláusulas Abiertas: {res['plan']['clauses_open']}")
        print(f"Saldo Inicial: {res['starting_cash']:,} € -> Saldo Proyectado: {res['projected_cash']:,} €")
        print(f"Once Titular MILP J{res['target_jornada_num']}: {res['plan']['lineup']['formation']} ({res['plan']['lineup']['expected_points']:.1f} xP)")
        print(
            f"Ventas: {len(res['plan']['all_sales'])} | Pujas: {len(res['plan']['market_bids'])} | "
            f"Escaparate: {len(res['plan']['preventive_listings'])}/{runner.rules.market_max_user_sales} | "
            f"Pendientes OK Humano: {len(res['pending_decisions'])}"
        )
        if res.get("notifications"):
            print(f"Notificaciones: {res['notifications']}")
        if res.get("briefing"):
            print("\n" + res["briefing"])
    except Exception as e:
        print(f"\n❌ ERROR CRÍTICO EN AUTOPILOT v3: {e}")
        traceback.print_exc()
        if args.notify:
            try:
                NotificationHub().send_email(
                    subject="⚠️ [FALLO AUTOPILOT v3] Error en ciclo matinal",
                    html_body=f"<h3>Error en ciclo matinal Autopilot</h3><p><b>{e}</b></p><pre>{traceback.format_exc()}</pre>",
                    text_body=f"Error en ciclo matinal Autopilot: {e}\n\n{traceback.format_exc()}",
                )
            except Exception:
                pass
        sys.exit(1)


if __name__ == "__main__":
    main()
