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

    def test_milp_micro_deficit_bin_and_high_debt_collateral_recovery(self):
        rules = LeagueRulesEngine(market_max_user_sales=5)
        opt = PortfolioOptimizer(rules=rules)

        # 1. Sub-thousand-euro Friday deficit (-417 EUR) must force a sale rather than taking bankruptcy slack
        sq_micro = pd.DataFrame([
            {"player_id": i, "nombre": f"U_{i}", "posicion": pos, "precio": pr}
            for i, pos, pr in [
                (1, "Portero", 3_000_000),
                (2, "Defensa", 4_000_000), (3, "Defensa", 3_500_000), (4, "Defensa", 3_200_000), (5, "Defensa", 3_000_000),
                (6, "Centrocampista", 4_200_000), (7, "Centrocampista", 3_800_000), (8, "Centrocampista", 3_100_000),
                (9, "Delantero", 5_000_000), (10, "Delantero", 4_100_000), (11, "Delantero", 2_800_000),
                (12, "Centrocampista", 900_000),
            ]
        ])
        xp_micro = pd.DataFrame([
            {"player_id": i, "xp_GK": 4.0 if i == 1 else 0.0, "xp_DF": 4.0, "xp_MF": 4.0, "xp_FW": 4.0, "xp_best": 4.0, "xp_healthy": 4.0, "p_start": 0.9, "is_available": True}
            for i in range(1, 13)
        ])
        sol = opt.solve_joint_lineup_and_solvency_milp(
            squad_df=sq_micro,
            xp_table=xp_micro,
            efr_map={i: 20_000.0 for i in range(1, 13)},
            current_cash=-417.0,
            must_be_solvent=True,
            offer_map={12: 920_000.0, 11: 2_850_000.0},
        )
        self.assertTrue(sol["is_solvent"], "MILP opted for -417 EUR bankruptcy instead of liquidating a bench offer!")
        self.assertGreaterEqual(sol["projected_cash"], 0.0)
        self.assertEqual(len(sol["sales_to_execute"]), 1)

        # 2. High debt (-9.2M EUR) exceeding bench/flip value must unlock STRUCTURAL_STARTER collateral instead of wasting slots on 0.15M dust
        sq_debt = pd.DataFrame([
            {"player_id": 201, "nombre": "CoreAnchor", "posicion": "Centrocampista", "precio": 16_000_000},
            {"player_id": 202, "nombre": "StarterA", "posicion": "Defensa", "precio": 5_600_000},
            {"player_id": 203, "nombre": "StarterB", "posicion": "Centrocampista", "precio": 5_500_000},
            {"player_id": 204, "nombre": "StarterC", "posicion": "Defensa", "precio": 4_500_000},
            {"player_id": 205, "nombre": "DustA", "posicion": "Delantero", "precio": 150_000},
            {"player_id": 206, "nombre": "DustB", "posicion": "Delantero", "precio": 150_000},
            {"player_id": 207, "nombre": "DustC", "posicion": "Portero", "precio": 150_000},
        ])
        roles_debt = {201: "FRANCHISE_CORE", 202: "STRUCTURAL_STARTER", 203: "STRUCTURAL_STARTER", 204: "STRUCTURAL_STARTER", 205: "BENCH_PATCH", 206: "BENCH_PATCH", 207: "BENCH_PATCH"}
        listings_debt = opt.select_top_k_preventive_listings(
            final_sq=sq_debt,
            role_by_pid=roles_debt,
            npv_by_pid={},
            efr_map={i: 0.0 for i in range(201, 208)},
            projected_max_debt_eur=9_200_000.0,
            sell_locked_ids=set(),
            untouchable_ids=set(),
            max_slots=5,
        )
        coll_debt = sum(float(x["market_price"]) * 0.955 for x in listings_debt)
        self.assertGreaterEqual(coll_debt, 9_200_000.0 * 1.15)
        self.assertNotIn(201, {int(x["player_id"]) for x in listings_debt})

        # 3. Extreme impossibility without FRANCHISE_CORE must still spare untouchable_ids and unlock non-untouchable core
        sq_emg = pd.DataFrame([
            {"player_id": 301, "nombre": "UntouchCore", "posicion": "Delantero", "precio": 15_000_000},
            {"player_id": 302, "nombre": "LiquidCore", "posicion": "Centrocampista", "precio": 14_000_000},
            {"player_id": 303, "nombre": "TinyA", "posicion": "Defensa", "precio": 200_000},
        ])
        listings_emg = opt.select_top_k_preventive_listings(
            final_sq=sq_emg,
            role_by_pid={301: "FRANCHISE_CORE", 302: "FRANCHISE_CORE", 303: "BENCH_PATCH"},
            npv_by_pid={},
            efr_map={301: 0.0, 302: 0.0, 303: 0.0},
            projected_max_debt_eur=6_000_000.0,
            sell_locked_ids=set(),
            untouchable_ids={301},
            max_slots=5,
        )
        emg_pids = {int(x["player_id"]) for x in listings_emg}
        self.assertIn(302, emg_pids)
        self.assertNotIn(301, emg_pids)

    def test_xp_model_sub_shrinkage_and_postponed_match_zeroing(self):
        from src.tools.strategy_engine.xp_model import ExpectedPointsEngine

        xp_eng = ExpectedPointsEngine()
        mkt = pd.DataFrame([
            {"player_id": 1, "nombre": "SubCameo", "equipo": "ClubA", "posicion": "Delantero", "estado": "ok", "precio": 5_000_000, "media_puntos": 4.5, "puntos_totales": 45, "partidos_jugados": 10, "minutos": 650, "comuniate_titular": 1, "comuniate_duda": 0},
            {"player_id": 2, "nombre": "PostponedStar", "equipo": "ClubB", "posicion": "Centrocampista", "estado": "ok", "precio": 10_000_000, "media_puntos": 6.5, "puntos_totales": 65, "partidos_jugados": 10, "minutos": 900, "comuniate_titular": 1, "comuniate_duda": 0},
            {"player_id": 3, "nombre": "FullStarter", "equipo": "ClubA", "posicion": "Delantero", "estado": "ok", "precio": 5_000_000, "media_puntos": 4.5, "puntos_totales": 45, "partidos_jugados": 10, "minutos": 850, "comuniate_titular": 1, "comuniate_duda": 0},
        ])
        rep = pd.DataFrame(
            [{"player_id": 1, "jornada_num": j, "minutos_jugados": 15, "es_titular": 0, "puntos_biwenger": 8.0} for j in (7, 8, 9)]
            + [{"player_id": 2, "jornada_num": j, "minutos_jugados": 90, "es_titular": 1, "puntos_biwenger": 7.0} for j in (7, 8, 9)]
            + [{"player_id": 3, "jornada_num": j, "minutos_jugados": 90, "es_titular": 1, "puntos_biwenger": 8.0} for j in (7, 8, 9)]
        )
        cal = pd.DataFrame([
            {"jornada_num": 10, "part": 1, "local": "ClubA", "visitante": "ClubC", "estado_partido": "scheduled"},
            {"jornada_num": 10, "part": 1, "local": "ClubB", "visitante": "ClubD", "estado_partido": "postponed"},
        ])
        out = xp_eng.compute_xp_table(mkt, rep, cal, target_jornada_num=10).set_index("player_id")
        self.assertGreater(float(out.loc[3, "xp_best"]), float(out.loc[1, "xp_best"]) + 0.5)
        self.assertEqual(float(out.loc[2, "xp_best"]), 0.0)
        self.assertEqual(float(out.loc[2, "p_start"]), 0.0)

    def test_clause_sniper_gk_overpay_guard_and_official_clause_brackets_vec(self):
        from src.tools.strategy_engine.replacement_cost import _compute_official_clause_vec
        from src.tools.strategy_engine.clause_sniper import ClauseSniper

        rules = LeagueRulesEngine.load()
        prices = pd.Series([500_000, 1_500_000, 5_500_000, 15_000_000]).to_numpy()
        vec_clauses = _compute_official_clause_vec(prices)
        for p_val, vc in zip(prices, vec_clauses):
            self.assertEqual(int(vc), int(rules.compute_official_clause(float(p_val))))

        sniper = ClauseSniper(policy="SURGICAL_SCARCITY", rules=rules)
        mkt = pd.DataFrame([
            {"player_id": 401, "nombre": "OverpricedGK", "equipo": "ClubA", "propietario_liga": "RivalA", "precio": 5_000_000, "subida_24h": 40_000, "clausula_actual": 9_500_000, "media_puntos": "4.8"},
            {"player_id": 402, "nombre": "ScarceForward", "equipo": "ClubB", "propietario_liga": "RivalB", "precio": 10_000_000, "subida_24h": 80_000, "clausula_actual": 13_500_000, "media_puntos": "7.1"},
        ])
        xp_tbl = pd.DataFrame([
            {"player_id": 401, "best_line": "GK", "natural_pos": "GK", "valid_lines": "GK", "xp_best": 4.9, "xp_healthy": 4.9, "p_start": 0.98, "is_available": True},
            {"player_id": 402, "best_line": "FW", "natural_pos": "FW", "valid_lines": "FW", "xp_best": 7.2, "xp_healthy": 7.2, "p_start": 0.98, "is_available": True},
        ])
        targets = sniper.scan_offensive_targets(
            df_market_today=mkt,
            xp_table=xp_tbl,
            efr_map={401: 90_000.0, 402: 200_000.0},
            my_manager_name="MyClub",
            my_current_xi_floor_by_pos={"GK": 3.5, "DF": 3.2, "MF": 3.5, "FW": 3.0},
            max_budget=20_000_000.0,
            idle_cash_eur=18_000_000.0,
            clauses_open=True,
        )
        t_pids = {int(t["player_id"]) for t in targets}
        self.assertNotIn(401, t_pids, "Should not pay >1.50x clause on GK when starting GK is healthy!")
        self.assertIn(402, t_pids)

    def test_posture_schema_compat_and_env_j1_j3_nonzero_anchor(self):
        from src.tools.backtester.market_env import AZFinanceMarketEnv

        rules = LeagueRulesEngine.load()
        opt = PortfolioOptimizer(rules=rules)
        mkt = pd.DataFrame([
            {"player_id": 10 + i, "nombre": f"Free_{i}", "equipo": "ClubC", "posicion": "Defensa", "estado": "ok", "precio": 2_000_000, "subida_24h": 50_000, "clausula_actual": 4_500_000, "propietario_liga": "Libre", "media_puntos": "4.0"}
            for i in range(4)
        ])
        sq = pd.DataFrame([
            {"player_id": 500 + i, "nombre": f"Sq_{i}", "posicion": pos, "precio": 3_000_000, "subida_24h": 10_000}
            for i, pos in enumerate(["Portero"] + ["Defensa"] * 4 + ["Centrocampista"] * 4 + ["Delantero"] * 2)
        ])
        xp_tbl = pd.DataFrame([
            {"player_id": int(r["player_id"]), "best_line": r["posicion"][:2], "natural_pos": r["posicion"][:2], "valid_lines": "GK" if r["posicion"] == "Portero" else ("DF" if r["posicion"] == "Defensa" else ("MF" if r["posicion"] == "Centrocampista" else "FW")), "xp_best": 4.2, "xp_healthy": 4.2, "p_start": 0.9, "is_available": True}
            for r in pd.concat([mkt, sq], ignore_index=True).to_dict("records")
        ])
        df_cal = pd.DataFrame([
            {"jornada": "Jornada 8", "part": 1, "fecha_hora": "2026-10-16 21:00:00", "estado_partido": "pending"},
        ])
        plan = opt.generate_daily_plan(
            current_date="2026-10-14",
            my_manager_name="TestTeam",
            current_cash=5_000_000.0,
            squad_df=sq,
            df_market_all=mkt,
            market_listings_df=pd.DataFrame(),
            xp_table=xp_tbl,
            efr_map={},
            phase_ctx=rules.compute_round_phase(pd.Timestamp("2026-10-14 10:00:00"), df_cal),
            causal_standings_ctx={"my_rank": 10, "total_managers": 15, "leader_points": 450, "my_points": 320, "rounds_settled": 7},
        )
        self.assertEqual(plan["posture_state"]["posture"], "ASYMMETRIC_CHASER")

        env = AZFinanceMarketEnv()
        j1_j3_nonzero = [sum(1 for v in env.ground_truth_round_pts.get(j, {}).values() if v >= 8) for j in (1, 2, 3)]
        self.assertTrue(all(cnt >= 10 for cnt in j1_j3_nonzero), f"J1-J3 anchor wiped player points: {j1_j3_nonzero}")

    def test_pydantic_ai_agent_builds_with_custom_gemini_and_fallback(self):
        from unittest.mock import patch
        from pydantic_ai import Agent
        from pydantic_ai.models.fallback import FallbackModel
        from src.llm_endpoints.router import TieredLLMRouter
        from src.agent.pydantic_biwenger_agent import create_biwenger_agent

        fake_candidates = [
            {
                "provider": "gemini",
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                "model": "gemini-4-hydrogen",
                "api_key": "fake-gem-key",
            },
            {
                "provider": "openrouter",
                "base_url": "https://openrouter.ai/api/v1",
                "model": "openai/gpt-6-luna-pro",
                "api_key": "fake-or-key",
            },
        ]
        fake_ctx = {
            "user_id": 1,
            "user_name": "Tester",
            "league_id": 1,
            "league_name": "TestLeague",
            "team_id": 1,
            "team_name": "TestFC",
            "balance": 5_000_000.0,
        }
        with patch.object(TieredLLMRouter, "resolve_candidates", return_value=fake_candidates), \
             patch("src.agent.pydantic_biwenger_agent.get_active_user_context", return_value=fake_ctx):
            model_obj = TieredLLMRouter.get_pydantic_ai_model_object("daily")
            self.assertIsInstance(model_obj, FallbackModel)
            ag = create_biwenger_agent()
            self.assertIsInstance(ag, Agent)


if __name__ == "__main__":
    unittest.main()
