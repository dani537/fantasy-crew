"""
Autonomous Competitive Strategy & Autopilot Engine (`AutopilotRunner`)
======================================================================
Executes the hybrid Quantitative MILP + Multi-Tier LLM decision cycle:
1. Gathers current league state, balances, squads, and active market offers.
2. Runs Walk-Forward `ExpectedPointsEngine`, `CausalEFRPredictor`, and `PortfolioOptimizer`.
3. Validates decisions against Guardrails v2 (`validate_autopilot_plan_v2`).
4. Executes operations through `BiwengerMarketActions` adhering strictly to policy:
   - `DRY_RUN`: 0 live calls, full telemetry, and LLM executive briefing.
   - `SEMI_AUTO`: Routine preventive listings (0€ cost) + optimal lineup sync.
     If `FRIDAY_EMERGENCY_SOLVENCY=True` and today is kickoff day with `cash < 0`,
     automatically executes optimal solvency sales so the user never scores 0 points.
   - `FULL_AUTO`: 100% autonomous execution of approved sales, bids, and clause snipes.
5. Generates high-level executive briefing using `TieredLLMRouter` (Google AI Pro $0 tier prioritized).
"""

import os
import sys
import json
import datetime
from typing import Dict, List, Any, Optional, Set, Tuple
import pandas as pd

from src.config import StrategySettings, Credentials, GeneralSettings
from src.tools.strategy_engine.xp_model import ExpectedPointsEngine
from src.tools.strategy_engine.auction_model import AuctionBidModel
from src.tools.strategy_engine.clause_sniper import ClauseSniper
from src.tools.strategy_engine.portfolio_optimizer import PortfolioOptimizer
from src.tools.backtester.causal_clock import StrictCausalDataView, CausalEFRPredictor
from src.tools.coach_analytic.guardrails import validate_autopilot_plan_v2
from src.tools.biwenger_actions.market_actions import BiwengerMarketActions
from src.llm_endpoints.router import TieredLLMRouter


class AutopilotRunner:
    """End-to-end autonomous controller for Biwenger AZ Finance."""

    def __init__(
        self,
        mode: Optional[str] = None,
        db_path: str = "./data/warehouse/biwenger_history.db",
        dry_run: Optional[bool] = None,
        session_auth: Optional[Any] = None,
        today_date: Optional[str] = None,
    ):
        raw_mode = (mode or StrategySettings.AUTOPILOT_MODE).upper()
        self.mode = raw_mode if raw_mode in ("DRY_RUN", "SEMI_AUTO", "FULL_AUTO") else "SEMI_AUTO"
        self.dry_run = (self.mode == "DRY_RUN") if dry_run is None else bool(dry_run)
        self.db_path = db_path
        self.session_auth = session_auth
        self.today_date = str(today_date)[:10] if today_date else None

        self.view = StrictCausalDataView(db_path=self.db_path)
        if self.today_date:
            self.view.set_date(self.today_date)
        else:
            self.today_date = self.view.all_dates[-1] if self.view.all_dates else "2026-10-30"
            self.view.set_date(self.today_date)

        self.xp_engine = ExpectedPointsEngine()
        self.auction_model = AuctionBidModel()
        self.clause_sniper = ClauseSniper(policy=StrategySettings.CLAUSE_POLICY)
        self.optimizer = PortfolioOptimizer(
            xp_engine=self.xp_engine,
            auction_model=self.auction_model,
            clause_sniper=self.clause_sniper,
            credit_limit_ratio=StrategySettings.CREDIT_LIMIT_RATIO,
            max_squad_size=StrategySettings.MAX_SQUAD_SIZE,
            preventive_listing=StrategySettings.PREVENTIVE_MARKET_LISTING,
        )
        self.market_actions = BiwengerMarketActions(
            session=(self.session_auth.get_session() if self.session_auth else None),
            league_id=(self.session_auth.player_info.league_id if (self.session_auth and hasattr(self.session_auth, "player_info") and self.session_auth.player_info) else 1643248),
            user_id=(self.session_auth.player_info.team_id if (self.session_auth and hasattr(self.session_auth, "player_info") and self.session_auth.player_info) else 9878097),
            dry_run=self.dry_run,
        )

    def load_active_context(self) -> Dict[str, Any]:
        """Loads today's market, manager squad, active Computer offers, and schedule."""
        mkt_today = self.view.get_market_snapshot(self.today_date)
        mgrs_today = self.view.get_manager_snapshots(self.today_date)

        target_mgr = "Dani SR"
        my_row = mgrs_today[mgrs_today["manager"] == target_mgr] if not mgrs_today.empty else pd.DataFrame()

        current_cash = float(my_row.iloc[0]["saldo_disponible_exacto"]) if not my_row.empty else -4_338_451.0
        pids_str = str(my_row.iloc[0]["plantilla_ids"]) if not my_row.empty else ""
        owned_pids = [int(x) for x in pids_str.split(",") if x.strip().isdigit()]

        squad_df = mkt_today[mkt_today["player_id"].isin(owned_pids)].copy()

        # Check calendar to identify kickoff day and next round
        cal = self.view.get_calendar_causal()
        future_or_today_matches = cal[cal["fecha_hora"] >= f"{self.today_date} 00:00:00"]
        if not future_or_today_matches.empty:
            next_match_row = future_or_today_matches.iloc[0]
            target_jn = int(next_match_row["jornada_num_int"])
            next_kd = str(next_match_row["fecha_hora"])[:10]
        else:
            target_jn = 11
            next_kd = self.today_date

        is_kickoff_day = (next_kd == self.today_date)
        days_to_kickoff = max(0, (pd.Timestamp(next_kd) - pd.Timestamp(self.today_date)).days)

        # Active Computer & rival offers from Mercado_Diario_Historico
        conn = self.view.db_path
        offers_map: Dict[int, float] = {}
        try:
            import sqlite3
            with sqlite3.connect(conn) as c:
                of_df = pd.read_sql(
                    f"SELECT player_id, precio_salida_o_oferta FROM Mercado_Diario_Historico WHERE fecha LIKE '{self.today_date}%' AND tipo_registro LIKE 'Oferta%'",
                    c,
                )
                for _, r in of_df.iterrows():
                    pid = int(pd.to_numeric(r["player_id"], errors="coerce") or 0)
                    amt = float(pd.to_numeric(r["precio_salida_o_oferta"], errors="coerce") or 0)
                    if pid > 0 and amt > 0:
                        offers_map[pid] = amt
        except Exception:
            pass

        # If no offers in DB for today, synthesize current Computer offers on squad for simulation
        if not offers_map and not squad_df.empty:
            for _, sr in squad_df.iterrows():
                p_id = int(sr["player_id"])
                offers_map[p_id] = float(sr.get("precio", 150_000))

        # Market listings
        listings_df = pd.DataFrame()
        try:
            import sqlite3
            with sqlite3.connect(conn) as c:
                listings_df = pd.read_sql(
                    f"SELECT player_id, jugador, vendedor_o_origen as seller_name, precio_mercado_dia, precio_salida_o_oferta as ask_price FROM Mercado_Diario_Historico WHERE fecha LIKE '{self.today_date}%' AND tipo_registro = 'En Venta Mercado'",
                    c,
                )
        except Exception:
            pass

        return {
            "date": self.today_date,
            "target_manager": target_mgr,
            "current_cash": current_cash,
            "squad_df": squad_df,
            "market_today_df": mkt_today,
            "listings_df": listings_df,
            "offers_map": offers_map,
            "target_jornada_num": target_jn,
            "is_kickoff_day": is_kickoff_day,
            "days_to_kickoff": days_to_kickoff,
        }

    def execute_cycle(self, generate_briefing: bool = True) -> Dict[str, Any]:
        """Executes one complete daily autopilot decision cycle."""
        ctx = self.load_active_context()

        # 1. Quantitative Projections (Strict Causal)
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

        # 3. Generate Daily Plan via MILP Solver
        raw_plan = self.optimizer.generate_daily_plan(
            current_date=ctx["date"],
            my_manager_name=ctx["target_manager"],
            current_cash=ctx["current_cash"],
            squad_df=ctx["squad_df"],
            df_market_all=ctx["market_today_df"],
            market_listings_df=ctx["listings_df"],
            xp_table=xp_table,
            efr_map=efr_map,
            active_offers_map=ctx["offers_map"],
            is_kickoff_day=ctx["is_kickoff_day"],
            days_to_kickoff=ctx["days_to_kickoff"],
            recent_match_shock_map=shock_map,
        )

        # 4. Guardrails v2 Validation
        squad_val = float(ctx["squad_df"]["precio"].sum()) if not ctx["squad_df"].empty else 0.0
        p_start_map = dict(zip(xp_table["player_id"].values, xp_table["p_start"].values)) if not xp_table.empty else {}
        validated = validate_autopilot_plan_v2(
            plan=raw_plan,
            squad_value=squad_val,
            unlocked_collateral=squad_val * 0.75,
            untouchable_ids=set(),
            efr_map=efr_map,
            p_start_map=p_start_map,
        )
        approved_plan = validated["approved_plan"]

        # 5. Execution Router
        execution_results = []
        is_friday_emergency = (
            ctx["is_kickoff_day"]
            and StrategySettings.FRIDAY_EMERGENCY_SOLVENCY
            and (ctx["current_cash"] < 0)
        )

        should_execute_solvency = (self.mode == "FULL_AUTO") or (self.mode == "SEMI_AUTO" and is_friday_emergency)
        should_execute_discretionary = (self.mode == "FULL_AUTO")

        # Execute Solvency Sales if needed
        if should_execute_solvency:
            for s in approved_plan.get("solvency_sales", []):
                res = self.market_actions.accept_offer(int(s.get("offer_id", s["player_id"])))
                execution_results.append({"type": "SOLVENCY_SALE", "target": s["nombre"], "res": res})

        # Execute Discretionary Operations in FULL_AUTO
        if should_execute_discretionary:
            for s in approved_plan.get("routine_sales", []):
                res = self.market_actions.accept_offer(int(s.get("offer_id", s["player_id"])))
                execution_results.append({"type": "ROUTINE_SALE", "target": s["nombre"], "res": res})

            for c in approved_plan.get("clause_snipes", []):
                res = self.market_actions.pay_clause(
                    player_id=int(c["player_id"]),
                    amount=int(c["amount"]),
                    owner_user_id=int(c.get("victim_user_id", 0)),
                )
                execution_results.append({"type": "CLAUSE_SNIPE", "target": c["nombre"], "res": res})

            for b in approved_plan.get("market_bids", []):
                res = self.market_actions.place_bid(
                    player_id=int(b["player_id"]),
                    amount=int(b["amount"]),
                    seller_user_id=(int(b["seller_user_id"]) if b.get("seller_user_id") else None),
                )
                execution_results.append({"type": "MARKET_BID", "target": b["nombre"], "res": res})

            for sh in approved_plan.get("defensive_shields", []):
                res = self.market_actions.raise_clause(
                    player_id=int(sh["player_id"]),
                    increment_eur=int(sh["recommended_cash_spend"]),
                )
                execution_results.append({"type": "DEFENSIVE_SHIELD", "target": sh["nombre"], "res": res})

        # Lineup Sync (in SEMI_AUTO on kickoff day or FULL_AUTO)
        lineup_info = approved_plan.get("lineup", {})
        if lineup_info and ((self.mode == "SEMI_AUTO" and ctx["is_kickoff_day"]) or self.mode == "FULL_AUTO"):
            res = self.market_actions.set_lineup(
                formation=lineup_info["formation"],
                ordered_player_ids=lineup_info["player_ids"],
            )
            execution_results.append({"type": "LINEUP_SYNC", "formation": lineup_info["formation"], "res": res})

        # Preventive Listings (harvest +-5% Computer offers)
        if StrategySettings.PREVENTIVE_MARKET_LISTING and (self.mode in ("SEMI_AUTO", "FULL_AUTO")):
            for pl in approved_plan.get("preventive_listings", []):
                res = self.market_actions.list_player(
                    player_id=int(pl["player_id"]),
                    asking_price=int(pl["asking_price"]),
                )
                execution_results.append({"type": "PREVENTIVE_LISTING", "target": pl["nombre"], "res": res})

        # 6. Executive Briefing via TieredLLMRouter
        briefing_text = ""
        if generate_briefing:
            briefing_text = self._generate_executive_briefing(
                ctx=ctx,
                plan=approved_plan,
                blocked=validated["blocked"],
                execution_results=execution_results,
            )

        cycle_summary = {
            "date": ctx["date"],
            "mode": self.mode,
            "dry_run": self.dry_run,
            "is_kickoff_day": ctx["is_kickoff_day"],
            "target_jornada_num": ctx["target_jornada_num"],
            "starting_cash": ctx["current_cash"],
            "projected_cash": validated["projected_cash"],
            "is_solvent": validated["is_safe"],
            "plan": approved_plan,
            "blocked_actions": validated["blocked"],
            "executions": execution_results,
            "briefing": briefing_text,
        }

        # Persist latest plan
        os.makedirs("./data/autopilot", exist_ok=True)
        with open("./data/autopilot/daily_plan_latest.json", "w", encoding="utf-8") as f:
            json.dump(cycle_summary, f, ensure_ascii=False, indent=2, default=str)

        return cycle_summary

    def _generate_executive_briefing(
        self,
        ctx: Dict[str, Any],
        plan: Dict[str, Any],
        blocked: List[Dict[str, Any]],
        execution_results: List[Dict[str, Any]],
    ) -> str:
        """Calls TieredLLMRouter to synthesize an executive sports-director briefing."""
        client = TieredLLMRouter.get_client(tier="high_stakes" if ctx["is_kickoff_day"] else "daily")

        sales_str = ", ".join(f"{s['nombre']} ({s['sale_price']:,}€ - {s['reason']})" for s in plan.get("all_sales", [])) or "Ninguna"
        clauses_str = ", ".join(f"{c['nombre']} ({c['amount']:,}€ a {c['seller']}, +{c['delta_xp_xi']} xP)" for c in plan.get("clause_snipes", [])) or "Ninguno"
        bids_str = ", ".join(f"{b['nombre']} ({b['amount']:,}€ a {b['seller']}, {b['regime']})" for b in plan.get("market_bids", [])) or "Ninguna"
        lineup_str = f"Formación {plan['lineup']['formation']} (xP esperados: {plan['lineup']['expected_points']:.1f} pts)"

        prompt = f"""
Actúa como Director Deportivo de élite para 'Dani SR' en la liga Biwenger 'AZ Finance'.
Genera un briefing ejecutivo conciso en Markdown (máximo 4 párrafos con viñetas) sobre las decisiones de hoy ({ctx['date']}):

- Modo de Operación: {self.mode} (Dry Run: {self.dry_run})
- Situación de Caja: Saldo Inicial {ctx['current_cash']:,} € -> Saldo Proyectado {plan['post_sales_cash']:,} €
- ¿Día de Jornada {ctx['target_jornada_num']}?: {ctx['is_kickoff_day']} (Solvencia Obligatoria: {plan['is_solvent']})
- Ventas Planificadas: {sales_str}
- Cláusulas 'A Machete': {clauses_str}
- Pujas de Mercado: {bids_str}
- Once Titular Óptimo MILP: {lineup_str}
- Acciones Bloqueadas por Guardrails v2: {len(blocked)}

Estructura:
1. 🎯 Diagnóstico y Estado Financiero (con urgencia de solvencia si aplica)
2. ⚡ Cirugía de Mercado & Cláusulas
3. ⚽ Once Titular y Estrategia de Jornada
4. 🛡️ Auditoría y Nivel de Autonomía
"""
        try:
            resp = client.call(prompt=prompt, system_prompt="Eres un Director Deportivo analítico y cuantitativo.")
            return str(resp)
        except Exception as e:
            return f"*(Briefing generado en modo sintético local debido a: {e})*\n\n" \
                   f"**Diagnóstico:** Saldo {ctx['current_cash']:,} € con {len(ctx['squad_df'])} jugadores.\n" \
                   f"**Plan MILP:** Formación {plan['lineup']['formation']} con {plan['lineup']['expected_points']:.1f} xP.\n" \
                   f"**Ventas:** {sales_str}\n" \
                   f"**Cláusulas:** {clauses_str}\n" \
                   f"**Pujas:** {bids_str}\n"


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Run Fantasy Champion Autopilot")
    parser.add_argument("--mode", default="DRY_RUN", choices=["DRY_RUN", "SEMI_AUTO", "FULL_AUTO"])
    parser.add_argument("--date", default=None, help="Execution date (defaults to today)")
    args = parser.parse_args()

    print(f"🚀 Iniciando Autopilot en modo {args.mode} (Fecha: {args.date or 'Hoy'})...")
    runner = AutopilotRunner(mode=args.mode, today_date=args.date)
    res = runner.execute_cycle(generate_briefing=True)

    print("\n=== RESUMEN EJECUTIVO AUTOPILOT ===")
    print(f"Fecha: {res['date']} | Modo: {res['mode']} | Solvente: {res['is_solvent']}")
    print(f"Saldo Inicial: {res['starting_cash']:,} € -> Saldo Proyectado: {res['projected_cash']:,} €")
    print(f"Once Titular: {res['plan']['lineup']['formation']} ({res['plan']['lineup']['expected_points']:.1f} xP)")
    print(f"Ventas: {len(res['plan']['all_sales'])} | Pujas: {len(res['plan']['market_bids'])} | Cláusulas: {len(res['plan']['clause_snipes'])}")
    if res.get("briefing"):
        print("\n" + res["briefing"])


if __name__ == "__main__":
    main()
