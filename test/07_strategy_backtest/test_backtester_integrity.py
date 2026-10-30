"""
Automated Backtester & Strategy Integrity Unit Tests (`test_backtester_integrity.py`)
=====================================================================================
Verifies:
1. Strict Zero-Temporal-Leakage (`StrictCausalDataView` + `CausalEFRPredictor` causal invariance).
2. Official AZ Finance J1-J10 Standings & Bonus Accounting (`0 €` / `0 pt` drift, no `ajornada` duplication).
3. Exact Joint MILP Friday Solvency (`balance >= 0 €` from `-4,338,451 €`) & Legal Multiposition Formations.
4. Official AZ Finance Clause Brackets & 5-Day Immunity Enforcement.
5. `TieredLLMRouter` Google AI Pro ($0 Gemini) Priority & OpenRouter 2026 Tiered Model Routing.
"""

import os
import sys
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.tools.data_extraction.warehouse import compute_official_clause
from src.tools.backtester.causal_clock import StrictCausalDataView, CausalEFRPredictor
from src.tools.backtester.benchmark_suite import BacktestBenchmarkSuite
from src.tools.strategy_engine.xp_model import ExpectedPointsEngine
from src.tools.strategy_engine.clause_sniper import ClauseSniper
from src.tools.strategy_engine.portfolio_optimizer import PortfolioOptimizer, OFFICIAL_FORMATIONS
from src.llm_endpoints.router import TieredLLMRouter


DB_PATH = os.path.join(PROJECT_ROOT, "data", "warehouse", "biwenger_history.db")


class TestFantasyChampionIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = StrictCausalDataView(db_path=DB_PATH)
        cls.xp_engine = ExpectedPointsEngine(
            multipos_csv_path=os.path.join(PROJECT_ROOT, "data", "players_transformed.csv")
        )

    def test_zero_temporal_leakage_and_causal_invariance(self):
        self.view.set_date("2026-08-15")
        # Future date query MUST raise ValueError
        with self.assertRaises(ValueError):
            self.view.get_market_snapshot("2026-08-16")
        with self.assertRaises(ValueError):
            self.view.get_past_bids("2026-08-16")
        with self.assertRaises(ValueError):
            self.view.get_past_match_reports("2026-08-16 00:00:00")

        # On J1 kickoff day (2026-08-15 18:00 cutoff), 0 matches of J1 (kickoff 19:30) may be visible
        rep_aug15 = self.view.get_past_match_reports()
        self.assertEqual(len(rep_aug15), 0, "Kickoff evening matches leaked before 18:00 decision cutoff!")

        # Calendar must mask future scores
        cal = self.view.get_calendar_causal()
        j1_cal = cal[cal["jornada_num_int"] == 1]
        self.assertTrue((j1_cal["goles_local"] == "").all(), "Future match goals leaked in causal calendar!")

        # Causal invariance test on EFR predictor: corrupting future prices (> 2026-09-10)
        # must produce BIT-IDENTICAL EFR predictions on 2026-09-10!
        self.view.set_date("2026-09-10")
        pred1 = CausalEFRPredictor(self.view)
        efr1, _ = pred1.predict_efr_72h_and_shocks()

        pred2 = CausalEFRPredictor(self.view)
        for fut_d in [d for d in self.view.all_dates if d > "2026-09-10"]:
            pred2._price_by_date[fut_d] = {pid: 99_999_999.0 for pid in pred2._price_by_date[fut_d]}
            pred2._subida_by_date[fut_d] = {pid: 5_000_000.0 for pid in pred2._subida_by_date[fut_d]}
        efr2, _ = pred2.predict_efr_72h_and_shocks()
        self.assertEqual(efr1, efr2, "CausalEFRPredictor leaked future prices!")

    def test_exact_official_standings_and_bonus_accounting(self):
        suite = BacktestBenchmarkSuite(db_path=DB_PATH, output_dir=os.path.join(PROJECT_ROOT, "data", "backtest"))
        df_real, _ = suite.load_real_league_benchmarks()

        birra = df_real[df_real["manager"] == "Birra München"].iloc[0]
        ruso = df_real[df_real["manager"] == "RusoPoderoso"].iloc[0]
        dani = df_real[df_real["manager"] == "Dani SR"].iloc[0]

        self.assertEqual(int(birra["points_j1_j10"]), 521)
        self.assertEqual(int(ruso["points_j1_j10"]), 484)
        self.assertEqual(int(dani["points_j1_j10"]), 387)
        self.assertEqual(int(dani["bonuses_eur"]), 26_073_000)
        self.assertEqual(int(dani["cash_eur"]), -4_338_451)

    def test_milp_friday_solvency_on_real_dani_sr_debt_today(self):
        """
        Tests the MILP solver on Dani SR's REAL 2026-10-30 squad and `-4,338,451 €` debt
        ahead of Jornada 11!
        """
        self.view.set_date("2026-10-30")
        mkt_today = self.view.get_market_snapshot("2026-10-30")
        mgr_today = self.view.get_manager_snapshots("2026-10-30")
        dani_row = mgr_today[mgr_today["manager"] == "Dani SR"].iloc[0]
        real_cash = float(dani_row["saldo_disponible_exacto"])
        self.assertLess(real_cash, -4_000_000)

        dani_pids = [int(x) for x in str(dani_row["plantilla_ids"]).split(",") if x.strip().isdigit()]
        squad_df = mkt_today[mkt_today["player_id"].isin(dani_pids)].copy()

        xp_table = self.xp_engine.compute_xp_table(
            df_market_today=mkt_today,
            df_reports_past=self.view.get_past_match_reports(),
            df_calendar=self.view.get_calendar_causal(),
            target_jornada_num=11,
            df_comuniate_round=self.view.get_comuniate_for_round(11),
        )
        efr_map, _ = CausalEFRPredictor(self.view).predict_efr_72h_and_shocks()

        # Simulate active Computer offers at 100% VM on unlocked players
        offers = {int(r["player_id"]): float(r["precio"]) for _, r in squad_df.iterrows()}

        opt = PortfolioOptimizer(xp_engine=self.xp_engine)
        sol = opt.solve_joint_lineup_and_solvency_milp(
            squad_df=squad_df,
            xp_table=xp_table,
            efr_map=efr_map,
            current_cash=real_cash,
            must_be_solvent=True,
            offer_map=offers,
        )

        self.assertTrue(sol["is_solvent"], f"MILP failed to clear Friday debt! Projected cash: {sol['projected_cash']}")
        self.assertGreaterEqual(sol["projected_cash"], 0)
        valid_formations = {f[0] for f in OFFICIAL_FORMATIONS}
        self.assertIn(sol["formation"], valid_formations)
        self.assertEqual(len(sol["lineup_ids"]), 11)

        sold_pids = {s["player_id"] for s in sol["sales_to_execute"]}
        self.assertTrue(sold_pids.isdisjoint(set(sol["lineup_ids"])), "MILP fielded a player it just sold!")

        for sl in sol["lineup_slots"]:
            valid_lns = self.xp_engine.get_valid_lines(sl["player_id"])
            self.assertIn(sl["linea"], valid_lns, f"Illegal multiposition assignment for {sl['nombre']}")

    def test_official_clause_brackets_and_5day_lock(self):
        self.assertEqual(compute_official_clause(1_000_000), 2_250_000)
        self.assertEqual(compute_official_clause(2_500_000), 5_000_000)
        self.assertEqual(compute_official_clause(4_000_000), 8_000_000)
        self.assertEqual(compute_official_clause(6_000_000), 10_000_000)
        self.assertEqual(compute_official_clause(14_000_000), 21_000_000)
        self.assertEqual(compute_official_clause(32_000_000), 40_000_000)

        sniper = ClauseSniper(policy="AGGRESSIVE")
        self.view.set_date("2026-10-20")
        mkt = self.view.get_market_snapshot("2026-10-20")
        xp_tbl = self.xp_engine.compute_xp_table(
            mkt, self.view.get_past_match_reports(), self.view.get_calendar_causal(), 10
        )
        all_pids = set(mkt["player_id"].astype(int).tolist())
        locked_targets = sniper.scan_offensive_targets(
            df_market_today=mkt,
            xp_table=xp_tbl,
            efr_map={},
            my_manager_name="Dani SR",
            my_current_xi_floor_by_pos={"GK": 2.0, "DF": 2.0, "MF": 2.0, "FW": 2.0},
            locked_player_ids=all_pids,
            max_budget=50_000_000,
        )
        self.assertEqual(len(locked_targets), 0, "ClauseSniper violated 5-day post-purchase lock!")

    def test_tiered_llm_router_candidates(self):
        daily_chain = TieredLLMRouter.resolve_candidates(tier="daily")
        hs_chain = TieredLLMRouter.resolve_candidates(tier="high_stakes")
        if not daily_chain and not hs_chain:
            self.skipTest("No LLM API keys configured (GEMINI_API_KEY / OPENROUTER_API_KEY)")
        self.assertGreaterEqual(len(daily_chain), 1)
        self.assertGreaterEqual(len(hs_chain), 1)
        # Gemini ($0 Google AI Pro) must take priority whenever its key exists
        from src.config import Credentials
        if Credentials.GEMINI_API_KEY:
            self.assertEqual(daily_chain[0]["provider"], "gemini")
            self.assertEqual(hs_chain[0]["provider"], "gemini")
        all_models = [c["model"] for c in daily_chain + hs_chain]
        self.assertTrue(
            any(("luna" in m or "gemini" in m or "haiku" in m or "terra" in m) for m in all_models),
            f"Unexpected candidate models: {all_models}",
        )


if __name__ == "__main__":
    unittest.main()
