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
from src.tools.backtester.causal_clock import StrictCausalDataView, CausalEFRPredictor
from src.tools.coach_analytic.guardrails import validate_autopilot_plan_v2
from src.tools.biwenger_actions.market_actions import BiwengerMarketActions
from src.tools.notifications.notifier import NotificationHub
from src.llm_endpoints.router import TieredLLMRouter


VALID_TRIGGER_PHASES = (
    "MORNING_FULL",
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
        live_offers = delta_state.get("waiting_offers_by_pid") or {}
        if delta_state.get("is_live"):
            for pid_s, of_obj in live_offers.items():
                pid_i = int(pid_s)
                offers_map[pid_i] = float(of_obj["amount"])
                offer_id_by_pid[pid_i] = int(of_obj["offer_id"])
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

        return {
            "date": self.today_date,
            "team_name": team_name,
            "team_id": team_id,
            "league_name": league_name,
            "league_id": league_id,
            "current_cash": current_cash,
            "squad_df": squad_df,
            "market_today_df": mkt_today,
            "listings_df": listings_df,
            "offers_map": offers_map,
            "offer_id_by_pid": offer_id_by_pid,
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
        )
        approved_plan = validated["approved_plan"]

        # 5. Phase-Aware Execution & SEMI_AUTO Human-Gate Queue
        execution_results: List[Dict[str, Any]] = []
        queued_decisions: List[str] = []
        offer_id_map: Dict[int, int] = ctx["offer_id_by_pid"]
        mgr_id_map: Dict[str, int] = ctx["manager_id_by_name"]

        is_lineup_only_phase = (self.trigger_phase == "FINAL_LINEUP_GUARD")
        is_solvency_only_phase = (self.trigger_phase == "PRE_KICKOFF_SOLVENCY")
        is_clause_window_phase = (self.trigger_phase == "PRE_CLAUSE_LOCK")

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
                            execution_results.append({"type": "ROUTINE_SALE", "target": s["nombre"], "res": res})
                except Exception as e:
                    execution_results.append({"type": "ROUTINE_SALE", "target": s.get("nombre", str(s)), "res": {"status": "ERROR", "detail": str(e)}})

        # 5C. Release Clauses & Defensive Shields (ALWAYS Human-Gated in SEMI_AUTO!)
        if self.trigger_phase in ("MORNING_FULL", "PRE_CLAUSE_LOCK"):
            for c in approved_plan.get("clause_snipes", []):
                try:
                    pid = int(c["player_id"])
                    owner_uid = int(mgr_id_map.get(str(c.get("seller", "")), 0))
                    amt = int(c["amount"])
                    # Autonomous execution in FULL_AUTO up to configured cap (6M default) when cash is positive.
                    # Clauses > 6M or in SEMI_AUTO are queued for 1-tap confirmation on Telegram!
                    requires_approval = (
                        self.mode == "SEMI_AUTO"
                        or amt > StrategySettings.AUTONOMOUS_CLAUSE_MAX_EUR
                        or not ctx.get("is_solvent", True)
                    )
                    if requires_approval and not self.dry_run:
                        dec_id = self.memory.queue_pending_decision(
                            action_type="CLAUSE_SNIPE",
                            player_id=pid,
                            nombre=c["nombre"],
                            amount_eur=amt,
                            market_price_eur=int(c["market_price"]),
                            counterparty=str(c.get("seller", "")),
                            owner_user_id=owner_uid,
                            rationale=(
                                f"Cláusula a {c.get('seller')} (+{c['delta_xp_xi']:.2f} xP, {c.get('scarcity_tier', 'SCARCE')}, "
                                f"Alpha neto +{int(c['expected_surplus']):,} €)"
                            ),
                            expires_hours=max(1, int(max(1.0, phase_ctx.hours_to_clause_lock))),
                            payload=c,
                        )
                        queued_decisions.append(dec_id)
                    elif self.mode == "FULL_AUTO" or self.dry_run:
                        res = market_actions.pay_clause(player_id=pid, amount=amt, owner_user_id=owner_uid)
                        execution_results.append({"type": "CLAUSE_SNIPE", "target": c["nombre"], "res": res})
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
                        execution_results.append({"type": "DEFENSIVE_SHIELD", "target": sh["nombre"], "res": res})
                except Exception as e:
                    execution_results.append({"type": "DEFENSIVE_SHIELD", "target": sh.get("nombre", str(sh)), "res": {"status": "ERROR", "detail": str(e)}})

        # 5D. Autonomous Market Bids (Executed in SEMI_AUTO when AUTONOMOUS_BIDS_IN_SEMI_AUTO=True!)
        if self.trigger_phase == "MORNING_FULL":
            can_bid = (
                self.mode == "FULL_AUTO"
                or (self.mode == "SEMI_AUTO" and StrategySettings.AUTONOMOUS_BIDS_IN_SEMI_AUTO)
                or self.dry_run
            )
            if can_bid:
                for b in approved_plan.get("market_bids", []):
                    try:
                        seller_name = str(b.get("seller", "Mercado"))
                        seller_uid = mgr_id_map.get(seller_name) if seller_name not in ("Mercado", "Libre", "") else None
                        res = market_actions.place_bid(
                            player_id=int(b["player_id"]),
                            amount=int(b["amount"]),
                            seller_user_id=seller_uid,
                        )
                        execution_results.append({"type": "MARKET_BID", "target": b["nombre"], "res": res})
                    except Exception as e:
                        execution_results.append({"type": "MARKET_BID", "target": b.get("nombre", str(b)), "res": {"status": "ERROR", "detail": str(e)}})

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

        # 7. Executive Briefing via TieredLLMRouter
        briefing_text = ""
        if generate_briefing:
            briefing_text = self._generate_executive_briefing(
                ctx=ctx,
                plan=approved_plan,
                blocked=validated["blocked"],
                pending=pending_all,
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
            "starting_cash": ctx["current_cash"],
            "projected_cash": validated["projected_cash"],
            "is_solvent": validated["is_safe"],
            "plan": approved_plan,
            "pending_decisions": pending_all,
            "blocked_actions": validated["blocked"],
            "executions": execution_results,
            "briefing": briefing_text,
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

    def _generate_executive_briefing(
        self,
        ctx: Dict[str, Any],
        plan: Dict[str, Any],
        blocked: List[Dict[str, Any]],
        pending: List[Dict[str, Any]],
    ) -> str:
        """Calls TieredLLMRouter to synthesize a personalized, memory-aware executive briefing."""
        phase_ctx: RoundPhaseContext = ctx["phase_ctx"]
        tier = "high_stakes" if (ctx["is_kickoff_day"] or self.trigger_phase != "MORNING_FULL") else "daily"

        is_semi = (self.mode == "SEMI_AUTO")
        sales_str = ", ".join(
            f"{s['nombre']} ({s['sale_price']:,}€ · {s['reason']}"
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
        lineup_str = f"Formación {plan['lineup']['formation']} (xP esperados: {plan['lineup']['expected_points']:.1f} pts)"
        mem_brief = self.memory.build_context_brief_for_llm()
        sec3_title = (
            "3. Decisiones Reservadas para Tu Confirmación (Cláusulas / Ventas Importantes)"
            if is_semi
            else "3. Operaciones de Alto Impacto (Cláusulas, Blindajes y Ventas Estructurales — 100% Autónomas)"
        )

        prompt = f"""
Actúa como Director Deportivo Cuantitativo para '{ctx['team_name']}' en la liga Biwenger '{ctx['league_name']}'.
Genera un briefing ejecutivo conciso en texto limpio (máximo 4 bloques cortos con viñetas) para la sesión `{self.trigger_phase}` ({ctx['date']}):

- Modo: {self.mode} (Dry Run: {self.dry_run}) | Fase Calendario J{ctx['target_jornada_num']}: {phase_ctx.phase_name}
- Inicio J{ctx['target_jornada_num']}: {phase_ctx.next_kickoff_ts} ({phase_ctx.hours_to_kickoff:.1f}h) | Cláusulas abiertas ({self.rules.clause_disabled_hours}h): {phase_ctx.clauses_open} | Parón FIFA: {phase_ctx.is_international_break}
- Caja Actual: {ctx['current_cash']:,} € -> Proyectada hoy tras ventas: {plan['post_sales_cash']:,} € (Nota Biwenger: las pujas son a sobre cerrado y solo descuentan caja mañana a las 06:00 si se ganan, respaldadas por nuestro Colateral Líquido Top-{self.rules.market_max_user_sales}: {plan.get('top_k_liquid_collateral', 0):,} €)
- Pujas Autónomas Lanzadas: {bids_str}
- Ventas Ejecutadas Hoy (NPV / Arbitraje / Solvencia): {sales_str}
- Cláusulas Quirúrgicas Evaluadas: {clauses_str} | Blindajes Defensivos: {shields_str}
- Escaparate Preventivo Top-{len(plan.get('preventive_listings', []))} (+5% VM, máx oficial {self.rules.market_max_user_sales}): {listings_str} (Nota: listar flips/parches en el escaparate cuesta 0€ y es intencional para recibir ofertas diarias ±5% de la Máquina como colateral y opción de venta gratis; NO implica venderlos salvo oferta pico o necesidad de solvencia)
- Once Titular Óptimo MILP: {lineup_str}
- Decisiones Esperando Confirmación Humana (`/aprobar`): {len(pending)}

{mem_brief}

Estructura:
1. Diagnóstico Financiero, Fase de Jornada y Escasez
2. Operaciones Autónomas Ejecutadas (Pujas, Escaparate Top-5 y Once MILP)
{sec3_title}
4. Continuidad Estratégica de Medio Plazo
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
                f"• Ventas: {sales_str} | Cláusulas en cola humana: {clauses_str}."
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
