"""
Fantasy Champion v3 Architecture & Anti-Overfitting Verification Suite (`test_v3_suite.py`)
===========================================================================================
Verifies all 5 structural pillars of `Fantasy Champion v3`:
1. `LeagueRulesEngine` & `RoundPhaseContext` dynamic matchday calendar adaptation:
   - Midweek Tuesday kickoff -> 48h clause freeze starts Sunday evening.
   - Standard Friday 21:00 kickoff -> 48h clause freeze starts Wednesday 21:00.
   - Saturday/Sunday active round (`IN_ROUND_ACTIVE`) -> clauses frozen, solvency NOT required,
     25% credit re-leveraging ENABLED.
   - FIFA International Break detection (`is_international_break == True`).
2. Zero-Overfitting Microeconomic `MarketScarcityEngine` & `evaluate_hold_vs_sell_npv`:
   - Synthetic scarce Tier-1 asset (no player names hardcoded!) bought above VM via clause,
     injured 18 days across a FIFA break -> `NPV_HOLD > NPV_SELL` (`recommend_sell == False`).
   - Synthetic replaceable mid-tier falling/injured asset -> `NPV_SELL > NPV_HOLD` (`recommend_sell == True`).
   - Synthetic severe 75-day long-term injury on expensive star -> `NPV_SELL > NPV_HOLD` (`recommend_sell == True`),
     proving pure economic NPV causality rather than blind "never sell expensive players".
3. Official `marketMaxUserSales` (`<= 5`) Top-K Preventive Listings & Collateral Debt Coverage:
   - Never exceeds `5` simultaneous listings, protects `FRANCHISE_CORE` and `untouchables`,
     and prioritizes liquid collateral covering `>= 1.25x` weekday debt.
4. Persistent Cross-Session `StrategyMemoryLedger`, `SEMI_AUTO` Human Queue & `Guardrails` v3:
   - Multi-day `offer_rolls` persistence in `Tesis_Activos`.
   - `Decisiones_Pendientes` lifecycle (`/aprobar`, `/rechazar`) & `Eventos_Jornada` idempotency.
   - Hard `untouchable_ids` veto in `validate_autopilot_plan_v2` (zero bypasses even on falling EFR).
5. Multi-User Plug & Play Zero-Hardcoding Source Audit across core operational modules.
"""

import os
import sys
import tempfile
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.tools.strategy_engine.league_rules import LeagueRulesEngine
from src.tools.strategy_engine.replacement_cost import (
    MarketScarcityEngine,
    evaluate_hold_vs_sell_npv,
)
from src.tools.strategy_engine.strategy_memory import StrategyMemoryLedger
from src.tools.strategy_engine.portfolio_optimizer import PortfolioOptimizer
from src.tools.coach_analytic.guardrails import validate_autopilot_plan_v2


class TestFantasyChampionV3Architecture(unittest.TestCase):
    def test_dynamic_calendar_48h_clause_lock_midweek_weekend_and_fifa_break(self):
        rules = LeagueRulesEngine(
            clause_disabled_hours=48,
            market_max_user_sales=5,
            credit_limit_ratio=0.25,
            clause_increment_ratio=1.0,
        )

        # Synthetic schedule covering:
        # - J11: Fri Oct 30 21:00 -> Mon Nov 02 21:00
        # - J12: Fri Nov 06 21:00 -> Sun Nov 08 21:00
        # - FIFA Break: Nov 09 .. Nov 20 -> J13 starts Fri Nov 20 21:00
        # - Midweek J14: Tue Nov 24 19:00 -> Thu Nov 26 21:00
        df_cal = pd.DataFrame([
            {"jornada": "Jornada 11", "part": 1, "fecha_hora": "2026-10-30 21:00:00", "estado_partido": "finished"},
            {"jornada": "Jornada 11", "part": 1, "fecha_hora": "2026-11-02 21:00:00", "estado_partido": "pending"},
            {"jornada": "Jornada 12", "part": 1, "fecha_hora": "2026-11-06 21:00:00", "estado_partido": "pending"},
            {"jornada": "Jornada 12", "part": 1, "fecha_hora": "2026-11-08 21:00:00", "estado_partido": "pending"},
            {"jornada": "Jornada 13", "part": 1, "fecha_hora": "2026-11-20 21:00:00", "estado_partido": "pending"},
            {"jornada": "Jornada 13", "part": 1, "fecha_hora": "2026-11-22 21:00:00", "estado_partido": "pending"},
            {"jornada": "Jornada 14", "part": 1, "fecha_hora": "2026-11-24 19:00:00", "estado_partido": "pending"},
            {"jornada": "Jornada 14", "part": 1, "fecha_hora": "2026-11-26 21:00:00", "estado_partido": "pending"},
        ])

        # Case A: Wednesday Nov 4 at 19:15 (1.75h before Wed 21:00 clause lock) -> Clauses OPEN & in pre-clause-lock window!
        ph_wed_open = rules.compute_round_phase(pd.Timestamp("2026-11-04 19:15:00"), df_cal)
        self.assertTrue(ph_wed_open.clauses_open)
        self.assertTrue(ph_wed_open.in_pre_clause_lock_window)

        # Case B: Wednesday Nov 4 at 21:30 (47.5h before Fri Nov 6 21:00 kickoff) -> Clauses LOCKED!
        ph_wed_locked = rules.compute_round_phase(pd.Timestamp("2026-11-04 21:30:00"), df_cal)
        self.assertFalse(ph_wed_locked.clauses_open)
        self.assertEqual(ph_wed_locked.phase_name, "CLAUSES_FROZEN_PRE_KICKOFF")

        # Case C: Saturday Oct 31 at 14:00 (inside active J11!) -> Clauses LOCKED, Solvency False, Credit Leverage TRUE
        ph_sat_active = rules.compute_round_phase(pd.Timestamp("2026-10-31 14:00:00"), df_cal)
        self.assertEqual(ph_sat_active.phase_name, "IN_ROUND_ACTIVE")
        self.assertFalse(ph_sat_active.clauses_open)
        self.assertFalse(ph_sat_active.must_be_solvent_now)
        self.assertTrue(ph_sat_active.can_use_credit_leverage)

        # Case D: Tuesday Nov 10 at 10:00 (after J12 ends, 10.5 days to J13) -> FIFA International Break detected!
        ph_fifa = rules.compute_round_phase(pd.Timestamp("2026-11-10 10:00:00"), df_cal)
        self.assertTrue(ph_fifa.is_international_break)
        self.assertTrue(ph_fifa.clauses_open)

        # Case E: Midweek J14 (kickoff Tue Nov 24 19:00 -> 48h clause lock at Sun Nov 22 19:00!)
        # Monday Nov 23 at 10:00 (33h before Tuesday kickoff) -> Clauses MUST be LOCKED!
        ph_midweek_mon = rules.compute_round_phase(pd.Timestamp("2026-11-23 10:00:00"), df_cal)
        self.assertFalse(ph_midweek_mon.clauses_open)

    def test_anti_overfitting_microeconomic_hold_vs_sell_npv(self):
        rules = LeagueRulesEngine(market_free_slots=20, market_max_user_sales=5)

        # Build synthetic scarce late-October league universe (ZERO player names hardcoded!):
        # - All elite forwards (xp_healthy >= 7.5) are owned by managers with 35M-45M clauses.
        # - Free market only has low/mid forwards (xp_healthy <= 3.8, price <= 3.5M).
        rows_mkt = [
            # Our synthetic scarce Tier-1 Star (#9001): VM 24M, bought via 35M clause, injured 18 days
            {"player_id": 9001, "nombre": "Asset_Alpha_9001", "posicion": "Delantero", "precio": 24_000_000, "propietario_liga": "MyTeam", "clausula_actual": 35_000_000},
            # Rival-owned elite forwards (high clauses 38M - 44M)
            {"player_id": 9002, "nombre": "Rival_Star_9002", "posicion": "Delantero", "precio": 22_000_000, "propietario_liga": "Rival_A", "clausula_actual": 38_000_000},
            {"player_id": 9003, "nombre": "Rival_Star_9003", "posicion": "Delantero", "precio": 25_000_000, "propietario_liga": "Rival_B", "clausula_actual": 44_000_000},
            # Our replaceable mid-tier falling midfielder (#9010): VM 4.2M
            {"player_id": 9010, "nombre": "Asset_Mid_9010", "posicion": "Centrocampista", "precio": 4_200_000, "propietario_liga": "MyTeam", "clausula_actual": 8_000_000},
        ]
        # Abundant free mid-tier midfielders & modest forwards
        for i in range(25):
            rows_mkt.append({
                "player_id": 9100 + i,
                "nombre": f"Free_Mid_{i}",
                "posicion": "Centrocampista",
                "precio": 3_500_000,
                "propietario_liga": "Libre",
                "clausula_actual": 0,
            })
            rows_mkt.append({
                "player_id": 9200 + i,
                "nombre": f"Free_Fwd_{i}",
                "posicion": "Delantero",
                "precio": 2_800_000,
                "propietario_liga": "Libre",
                "clausula_actual": 0,
            })
        df_mkt = pd.DataFrame(rows_mkt)

        xp_rows = [
            {"player_id": 9001, "natural_pos": "FW", "valid_lines": "FW", "is_available": False, "estado": "lesionado", "p_start_healthy": 0.95, "xp_best": 0.0, "xp_healthy": 9.4},
            {"player_id": 9002, "natural_pos": "FW", "valid_lines": "FW", "is_available": True, "estado": "ok", "p_start_healthy": 0.92, "xp_best": 8.8, "xp_healthy": 8.8},
            {"player_id": 9003, "natural_pos": "FW", "valid_lines": "FW", "is_available": True, "estado": "ok", "p_start_healthy": 0.94, "xp_best": 9.1, "xp_healthy": 9.1},
            {"player_id": 9010, "natural_pos": "MF", "valid_lines": "MF", "is_available": False, "estado": "lesionado", "p_start_healthy": 0.60, "xp_best": 0.0, "xp_healthy": 3.5},
        ]
        for i in range(25):
            xp_rows.append({"player_id": 9100 + i, "natural_pos": "MF", "valid_lines": "MF", "is_available": True, "estado": "ok", "p_start_healthy": 0.85, "xp_best": 4.8, "xp_healthy": 4.8})
            xp_rows.append({"player_id": 9200 + i, "natural_pos": "FW", "valid_lines": "FW", "is_available": True, "estado": "ok", "p_start_healthy": 0.75, "xp_best": 3.6, "xp_healthy": 3.6})
        df_xp = pd.DataFrame(xp_rows)

        # Calendar with FIFA break right after J12 (Nov 6), then J13 (Nov 20), J14 (Nov 27), J15 (Dec 4), J16 (Dec 11), J17 (Dec 18)
        df_cal = pd.DataFrame([
            {"jornada": "Jornada 12", "fecha_hora": "2026-11-06 21:00:00"},
            {"jornada": "Jornada 13", "fecha_hora": "2026-11-21 21:00:00"},
            {"jornada": "Jornada 14", "fecha_hora": "2026-11-28 21:00:00"},
            {"jornada": "Jornada 15", "fecha_hora": "2026-12-05 21:00:00"},
            {"jornada": "Jornada 16", "fecha_hora": "2026-12-12 21:00:00"},
            {"jornada": "Jornada 17", "fecha_hora": "2026-12-19 21:00:00"},
        ])

        scarcity = MarketScarcityEngine(df_market_today=df_mkt, xp_table=df_xp, my_manager_name="MyTeam", rules=rules)

        # 1. Moderate 18-day injury on scarce Tier-1 Star (#9001) on Nov 03 spanning FIFA break:
        #    Misses ONLY J12 (Nov 6) because Nov 3 + 18d = Nov 21! Must HOLD (`recommend_sell == False`).
        eval_star_hold = evaluate_hold_vs_sell_npv(
            player_id=9001,
            current_date="2026-11-03",
            market_price=24_000_000,
            sale_offer_eur=24_000_000,
            efr_72h=-240_000,
            is_available=False,
            estado="lesionado",
            xp_current=0.0,
            xp_healthy=9.4,
            p_start_healthy=0.95,
            valid_lines={"FW"},
            bench_backup_xp=3.2,
            scarcity_engine=scarcity,
            df_calendar=df_cal,
            purchase_price=35_000_000,
            explicit_return_days=18,
        )
        self.assertEqual(eval_star_hold["missed_rounds_est"], 1)
        self.assertFalse(eval_star_hold["recommend_sell"], f"Expected HOLD for scarce star across FIFA break, got: {eval_star_hold}")
        self.assertGreater(eval_star_hold["npv_hold_eur"], eval_star_hold["npv_sell_eur"])

        # 2. Replaceable mid-tier injured/falling midfielder (#9010) when abundant 4.8-xP free MFs exist:
        #    Must SELL (`recommend_sell == True`)!
        eval_mid_sell = evaluate_hold_vs_sell_npv(
            player_id=9010,
            current_date="2026-11-03",
            market_price=4_200_000,
            sale_offer_eur=4_250_000,
            efr_72h=-180_000,
            is_available=False,
            estado="lesionado",
            xp_current=0.0,
            xp_healthy=3.5,
            p_start_healthy=0.60,
            valid_lines={"MF"},
            bench_backup_xp=2.5,
            scarcity_engine=scarcity,
            df_calendar=df_cal,
            purchase_price=4_000_000,
            explicit_return_days=26,
        )
        self.assertTrue(eval_mid_sell["recommend_sell"], f"Expected SELL for replaceable injured mid-tier asset, got: {eval_mid_sell}")

        # 3. Severe 80-day injury (misses all 6/6 horizon rounds + 32% drawdown):
        #    Even a 24M star with 0 active rounds in horizon should trigger rational re-evaluation!
        eval_severe_acl = evaluate_hold_vs_sell_npv(
            player_id=9001,
            current_date="2026-11-03",
            market_price=24_000_000,
            sale_offer_eur=24_500_000,
            efr_72h=-450_000,
            is_available=False,
            estado="lesionado",
            xp_current=0.0,
            xp_healthy=9.4,
            p_start_healthy=0.95,
            valid_lines={"FW"},
            bench_backup_xp=3.0,
            scarcity_engine=scarcity,
            df_calendar=df_cal,
            purchase_price=35_000_000,
            explicit_return_days=80,
        )
        self.assertEqual(eval_severe_acl["missed_rounds_est"], 6)
        self.assertTrue(eval_severe_acl["recommend_sell"], f"Expected SELL on severe 80-day 6/6 missed-round injury, got: {eval_severe_acl}")

    def test_top_5_preventive_listings_and_collateral_coverage(self):
        rules = LeagueRulesEngine(market_max_user_sales=5)
        opt = PortfolioOptimizer(rules=rules)

        # 14-player squad with -4.0M € weekday debt: must pick <= 5 slots, never list FRANCHISE_CORE or untouchable,
        # and cover >= 1.25 * 4.0M = 5.0M € of liquid collateral!
        squad_df = pd.DataFrame([
            {"player_id": 100 + i, "nombre": f"P_{i}", "posicion": "Centrocampista", "precio": 3_000_000 + i * 500_000}
            for i in range(14)
        ])
        role_map = {100: "FRANCHISE_CORE", 101: "FRANCHISE_CORE"}
        for i in range(2, 8):
            role_map[100 + i] = "SPECULATIVE_FLIP"
        for i in range(8, 14):
            role_map[100 + i] = "BENCH_PATCH"

        listings = opt.select_top_k_preventive_listings(
            final_sq=squad_df,
            role_by_pid=role_map,
            npv_by_pid={102: {"recommend_sell": True, "net_sell_advantage_eur": 300_000}},
            efr_map={100 + i: 40_000.0 for i in range(14)},
            projected_max_debt_eur=4_000_000.0,
            sell_locked_ids=set(),
            untouchable_ids={103},
            max_slots=5,
        )
        self.assertLessEqual(len(listings), 5)
        listed_pids = {int(x["player_id"]) for x in listings}
        self.assertNotIn(100, listed_pids, "FRANCHISE_CORE was listed on market!")
        self.assertNotIn(101, listed_pids, "FRANCHISE_CORE was listed on market!")
        self.assertNotIn(103, listed_pids, "Untouchable player was listed on market!")
        self.assertIn(102, listed_pids, "NPV disposal candidate was not prioritized in top-5 listings!")
        collateral_sum = sum(float(squad_df.loc[squad_df["player_id"] == p, "precio"].iloc[0]) * 0.96 for p in listed_pids)
        self.assertGreaterEqual(collateral_sum, 5_000_000.0)

    def test_persistent_memory_ledger_and_guardrails_hard_untouchable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_p = os.path.join(tmpdir, "test_mem.db")
            dir_p = os.path.join(tmpdir, "directives.json")
            mem = StrategyMemoryLedger(db_path=db_p, directives_path=dir_p, in_memory_only=False, sync_sheets=False)

            # 1. Directives + fuzzy matching
            mem.add_user_directive("untouchable", "Estrella Intocable")
            cat = pd.DataFrame([{"player_id": 777, "nombre": "Estrella Intocable"}, {"player_id": 888, "nombre": "Parche Vendible"}])
            resolved = mem.resolve_active_directives(cat)
            self.assertIn(777, resolved["untouchable_ids"])

            # 2. Multi-session `offer_rolls` persistence in `Tesis_Activos`
            mem.update_squad_theses([{
                "player_id": 888, "nombre": "Parche Vendible", "rol": "SPECULATIVE_FLIP",
                "precio_actual": 3_000_000, "ultima_oferta": 2_900_000,
            }])
            # Session 2 across a fresh instance reading disk SQLite
            mem2 = StrategyMemoryLedger(db_path=db_p, directives_path=dir_p, in_memory_only=False, sync_sheets=False)
            mem2.update_squad_theses([{
                "player_id": 888, "nombre": "Parche Vendible", "rol": "SPECULATIVE_FLIP",
                "precio_actual": 3_000_000, "ultima_oferta": 2_920_000,
            }])
            t_map = mem2.get_asset_theses_map()
            self.assertGreaterEqual(int(t_map[888]["offer_rolls"]), 2)

            # 3. `SEMI_AUTO` Pending Decisions queue & `/aprobar`
            dec_id = mem2.queue_pending_decision(
                action_type="CLAUSE_SNIPE",
                player_id=555,
                nombre="Crack Rival",
                amount_eur=14_000_000,
                market_price_eur=11_000_000,
                counterparty="Rival FC",
                rationale="Escasez extrema +2.8 xP",
            )
            self.assertEqual(dec_id, "CL-555")
            self.assertEqual(len(mem2.get_pending_decisions("PENDING")), 1)
            approved_rec = mem2.resolve_pending_decision("CL-555", "APPROVED")
            self.assertIsNotNone(approved_rec)
            self.assertEqual(approved_rec["status"], "APPROVED")
            self.assertEqual(len(mem2.get_pending_decisions("PENDING")), 0)

            # 4. Event idempotency
            self.assertFalse(mem2.has_round_event_fired("J12_PRE_CLAUSE_LOCK"))
            mem2.mark_round_event_fired("J12_PRE_CLAUSE_LOCK", 12, "PRE_CLAUSE_LOCK", "OK", "Checked")
            self.assertTrue(mem2.has_round_event_fired("J12_PRE_CLAUSE_LOCK"))

            # 5. Guardrails v3 hard untouchable veto & 48h clause freeze & <=5 listings
            raw_plan = {
                "starting_cash": 10_000_000.0,
                "is_kickoff_day": False,
                "clauses_open": False,
                "can_use_credit_leverage": True,
                "all_sales": [{"player_id": 777, "nombre": "Estrella Intocable", "sale_price": 20_000_000}],
                "routine_sales": [{"player_id": 777, "nombre": "Estrella Intocable", "sale_price": 20_000_000}],
                "solvency_sales": [],
                "clause_snipes": [{"player_id": 555, "nombre": "Crack Rival", "amount": 8_000_000}],
                "defensive_shields": [{"player_id": 777, "nombre": "Estrella Intocable", "recommended_cash_spend": 1_000_000}],
                "market_bids": [],
                "preventive_listings": [{"player_id": 777, "nombre": "Estrella Intocable"}] + [
                    {"player_id": 200 + k, "nombre": f"L_{k}"} for k in range(7)
                ],
            }
            val = validate_autopilot_plan_v2(
                plan=raw_plan,
                squad_value=60_000_000.0,
                unlocked_collateral=12_000_000.0,
                untouchable_ids={777},
                efr_map={777: -500_000.0},
                p_start_map={777: 0.0},
                max_user_sales=5,
            )
            self.assertEqual(len(val["approved_plan"]["all_sales"]), 0, "Untouchable was sold despite hard veto!")
            self.assertEqual(len(val["approved_plan"]["clause_snipes"]), 0, "Clause snipe bypassed 48h lock!")
            self.assertEqual(len(val["approved_plan"]["defensive_shields"]), 0, "Shield bypassed 48h lock!")
            self.assertEqual(len(val["approved_plan"]["preventive_listings"]), 5, "Listings exceeded marketMaxUserSales=5!")

    def test_multi_user_zero_hardcoding_in_core_modules(self):
        core_files = [
            "src/config.py",
            "src/setup_wizard.py",
            "src/tools/data_extraction/warehouse.py",
            "src/tools/data_extraction/daily_market_tracker.py",
            "src/tools/data_extraction/historical_backfill.py",
            "src/tools/data_extraction/fast_delta_refresh.py",
            "src/tools/strategy_engine/league_rules.py",
            "src/tools/strategy_engine/replacement_cost.py",
            "src/tools/strategy_engine/strategy_memory.py",
            "src/tools/strategy_engine/portfolio_optimizer.py",
            "src/tools/strategy_engine/autopilot.py",
            "src/tools/notifications/notifier.py",
            "src/tools/notifications/telegram_bot.py",
            "src/tools/scheduler/dynamic_dispatcher.py",
            "src/tools/backtester/market_env.py",
            "src/tools/backtester/benchmark_suite.py",
        ]
        forbidden_literals = [
            "1V3lDapPrpGgLGVl-rvNi3Ishy70dAo22UEn24toa4kk",
            "9878097",
            "1495340",
        ]
        for rel in core_files:
            full_p = os.path.join(PROJECT_ROOT, rel)
            with open(full_p, "r", encoding="utf-8") as f:
                src_txt = f.read()
            for lit in forbidden_literals:
                self.assertNotIn(lit, src_txt, f"Hardcoded personal ID '{lit}' found in {rel}!")


if __name__ == "__main__":
    unittest.main()
