"""
Unit tests for Real Macro/Microeconomic Strategy Intelligence Modules:
- LeagueLifecycleEngine (Early Expansion vs Mature Scarcity vs Endgame)
- InjuryTacticalDecisionEngine (Hold Pillar vs FIFA break absorption vs Cash out)
- Starting Parity in AZFinanceMarketEnv (40M total net worth)
- Coherent Archetypes definitions
"""

from src.tools.strategy_engine.lifecycle_manager import LeagueLifecycleEngine, SeasonPhase
from src.tools.strategy_engine.injury_tactician import InjuryTacticalDecisionEngine, InjuryAction
from src.tools.strategy_engine.archetypes import STRATEGY_ARCHETYPES, StrategyArchetype
from src.tools.backtester.market_env import AZFinanceMarketEnv


def test_lifecycle_early_expansion():
    ctx = LeagueLifecycleEngine.evaluate_macro_context(
        free_players_ge_5m=25,
        free_players_ge_10m=8,
        current_jornada=2,
        total_jornadas=38,
        days_to_next_match=5,
        is_international_break=False,
    )
    assert ctx["phase"] == SeasonPhase.EARLY_EXPANSION.value
    assert ctx["clause_budget_fraction"] <= 0.30
    assert ctx["max_simultaneous_bids"] == 5


def test_lifecycle_mature_scarcity():
    ctx = LeagueLifecycleEngine.evaluate_macro_context(
        free_players_ge_5m=3,
        free_players_ge_10m=0,
        current_jornada=14,
        total_jornadas=38,
        days_to_next_match=4,
        is_international_break=False,
    )
    assert ctx["phase"] == SeasonPhase.MATURE_SCARCITY.value
    assert ctx["clause_budget_fraction"] >= 0.70
    assert ctx["max_simultaneous_bids"] == 3
    assert ctx["xp_point_value_eur"] > 62_000.0


def test_lifecycle_fifa_break():
    ctx = LeagueLifecycleEngine.evaluate_macro_context(
        free_players_ge_5m=5,
        free_players_ge_10m=1,
        current_jornada=8,
        total_jornadas=38,
        days_to_next_match=13,
        is_international_break=True,
    )
    assert ctx["is_fifa_break"] is True
    assert ctx["fifa_break_hold_momentum"] is True


def test_lifecycle_endgame_runin():
    ctx = LeagueLifecycleEngine.evaluate_macro_context(
        free_players_ge_5m=1,
        free_players_ge_10m=0,
        current_jornada=32,
        total_jornadas=38,
        days_to_next_match=3,
        is_international_break=False,
    )
    assert ctx["phase"] == SeasonPhase.ENDGAME_RUNIN.value
    assert ctx["clause_budget_fraction"] == 1.00


def test_injury_tactician_minor_injury_hold():
    # Minor injury: 1 round missed, rising value, core starter
    dec = InjuryTacticalDecisionEngine.decide(
        player_id=101,
        nombre="Pedri",
        estado="duda",
        absence_days_est=6,
        missed_rounds_calendar=1,
        is_fifa_break=False,
        asset_role="FRANCHISE_CORE",
        efr_72h=15_000,
        market_price=12_000_000,
        best_market_alternative_xp=3.2,
        player_healthy_xp=6.8,
    )
    assert dec["action"] == InjuryAction.HOLD_PILLAR
    assert dec["should_sell"] is False


def test_injury_tactician_fifa_break_absorption():
    # Player out for 12 days, but it's a 14-day FIFA break -> 0 missed rounds!
    dec = InjuryTacticalDecisionEngine.decide(
        player_id=102,
        nombre="Lamine Yamal",
        estado="lesionado",
        absence_days_est=12,
        missed_rounds_calendar=0,
        is_fifa_break=True,
        asset_role="FRANCHISE_CORE",
        efr_72h=-20_000,
        market_price=16_000_000,
        best_market_alternative_xp=3.5,
        player_healthy_xp=7.5,
    )
    assert dec["action"] == InjuryAction.HOLD_FIFA_BREAK
    assert dec["should_sell"] is False


def test_injury_tactician_torn_acl_cash_out():
    # Catastrophic ACL injury: 180 days, 22 missed rounds -> Must cash out even for a star!
    dec = InjuryTacticalDecisionEngine.decide(
        player_id=103,
        nombre="Carvajal",
        estado="lesionado",
        absence_days_est=180,
        missed_rounds_calendar=22,
        is_fifa_break=False,
        asset_role="STRUCTURAL_STARTER",
        efr_72h=-150_000,
        market_price=7_500_000,
        best_market_alternative_xp=4.0,
        player_healthy_xp=5.5,
    )
    assert dec["action"] == InjuryAction.SELL_CASH_OUT


def test_injury_tactician_speculative_flip_depreciating():
    # Speculative flip getting injured and dropping in price -> Cut immediately
    dec = InjuryTacticalDecisionEngine.decide(
        player_id=104,
        nombre="Fodder Mid",
        estado="duda",
        absence_days_est=8,
        missed_rounds_calendar=1,
        is_fifa_break=False,
        asset_role="SPECULATIVE_FLIP",
        efr_72h=-45_000,
        market_price=1_200_000,
        best_market_alternative_xp=2.5,
        player_healthy_xp=3.0,
    )
    assert dec["action"] == InjuryAction.SELL_CASH_OUT


def test_archetypes_integrity():
    assert len(STRATEGY_ARCHETYPES) >= 11
    valid_policies = {"COMPETITIVE_VORP", "VALUE_COMPOUND_VORP", "SURGICAL_SCARCITY", "AGGRESSIVE", "DISABLED"}
    valid_timings = {"IMMEDIATE", "PRE_LOCK_SURGICAL", "HYBRID"}
    for key, arch in STRATEGY_ARCHETYPES.items():
        assert isinstance(arch, StrategyArchetype)
        assert 13 <= arch.target_roster_size <= 21
        assert 0.0 <= arch.clause_cash_reserve_pct <= 0.95
        assert 1.50 <= arch.clause_ratio_cap <= 3.00
        assert arch.clause_policy in valid_policies
        assert arch.clause_timing_mode in valid_timings
        assert 0.0 <= arch.shield_rival_threat_pct <= 0.60
        assert 0.5 <= arch.efr_weight <= 2.5
        assert 0.5 <= arch.credit_utilization_aggression <= 2.0
        assert arch.hold_decay_penalty_eur >= 0


def test_market_env_starting_parity():
    # Test that equalize_starting_wealth produces exactly 40M initial net worth
    env = AZFinanceMarketEnv(
        db_path="./data/warehouse/biwenger_history.db",
        equalize_starting_wealth=True,
    )
    assert env.initial_net_worth == 40_000_000
    sq_val = sum(st.purchase_price for st in env.squad.values())
    assert int(round(env.cash + sq_val)) == 40_000_000


def test_wiring_closure():
    """Verifies formerly-dead & extended StrategyArchetype fields produce concrete behavioral changes."""
    from dataclasses import replace
    import pandas as pd
    from src.tools.strategy_engine.portfolio_optimizer import PortfolioOptimizer
    from src.tools.strategy_engine.xp_model import ExpectedPointsEngine
    from src.tools.strategy_engine.strategy_memory import StrategyMemoryLedger

    base_arch = STRATEGY_ARCHETYPES["ADAPTIVE_LIFECYCLE"]
    # Build minimal coherent 14-player squad (1 GK, 4 DF, 5 MF, 4 FW) with 1 stale speculative bench asset
    squad_rows = []
    specs = [
        (1, "GK1", "Portero", 3_000_000, 4.5, 0.95, 20_000, "2026-09-20", 1),
        (2, "DF1", "Defensa", 4_000_000, 4.6, 0.95, 30_000, "2026-09-20", 1),
        (3, "DF2", "Defensa", 4_000_000, 4.5, 0.95, 25_000, "2026-09-20", 1),
        (4, "DF3", "Defensa", 4_000_000, 4.4, 0.95, 20_000, "2026-09-20", 1),
        (5, "DF4", "Defensa", 3_500_000, 4.1, 0.90, 10_000, "2026-09-20", 1),
        (6, "MF1", "Centrocampista", 5_000_000, 5.2, 0.95, 40_000, "2026-09-20", 1),
        (7, "MF2", "Centrocampista", 5_000_000, 5.0, 0.95, 35_000, "2026-09-20", 1),
        (8, "MF3", "Centrocampista", 4_500_000, 4.8, 0.95, 25_000, "2026-09-20", 1),
        (9, "MF4", "Centrocampista", 4_000_000, 4.3, 0.90, 15_000, "2026-09-20", 1),
        (10, "FW1", "Delantero", 8_000_000, 6.2, 0.95, 50_000, "2026-09-20", 1),
        (11, "FW2", "Delantero", 6_000_000, 5.4, 0.95, 40_000, "2026-09-20", 1),
        (12, "FW3", "Delantero", 5_500_000, 4.9, 0.95, 30_000, "2026-09-20", 1),
        # Bench GK/patch + stale speculative hold (pid 13 held 12 days, flat growth, bench quality)
        (13, "MF_STALE", "Centrocampista", 1_500_000, 2.0, 0.65, 5_000, "2026-09-10", 0),
    ]
    for pid, nm, pos, pr, xp_v, ps, sub, acq, _ in specs:
        squad_rows.append({
            "player_id": pid, "nombre": nm, "posicion": pos, "equipo": "ClubA",
            "precio": pr, "subida_24h": sub, "estado": "ok", "propietario_liga": "Dani SR",
            "clausula_actual": int(pr * 1.4), "media_puntos": xp_v, "acquired_date": acq,
            "precio_compra": int(pr * 0.92), "xp_v": xp_v, "p_start": ps,
        })

    # Market includes an attractive rival star for VALUE_COMPOUND_VORP clause snipe & 4 free appreciating players
    extra_mkt = [
        {"player_id": 201, "nombre": "Rival Crack", "posicion": "Delantero", "equipo": "ClubB",
         "precio": 6_000_000, "subida_24h": 110_000, "estado": "ok", "propietario_liga": "Birra München",
         "clausula_actual": 8_200_000, "media_puntos": 7.6, "xp_v": 7.4, "p_start": 0.95},
        {"player_id": 301, "nombre": "Free Rising 1", "posicion": "Centrocampista", "equipo": "ClubC",
         "precio": 3_000_000, "subida_24h": 120_000, "estado": "ok", "propietario_liga": "Mercado",
         "clausula_actual": 0, "media_puntos": 5.6, "xp_v": 5.5, "p_start": 0.92, "precio_venta": 3_000_000},
        {"player_id": 302, "nombre": "Free Rising 2", "posicion": "Defensa", "equipo": "ClubC",
         "precio": 2_200_000, "subida_24h": 95_000, "estado": "ok", "propietario_liga": "Mercado",
         "clausula_actual": 0, "media_puntos": 4.9, "xp_v": 4.8, "p_start": 0.90, "precio_venta": 2_200_000},
        {"player_id": 303, "nombre": "Free Rising 3", "posicion": "Centrocampista", "equipo": "ClubD",
         "precio": 1_800_000, "subida_24h": 85_000, "estado": "ok", "propietario_liga": "Mercado",
         "clausula_actual": 0, "media_puntos": 3.8, "xp_v": 3.5, "p_start": 0.85, "precio_venta": 1_800_000},
    ]

    all_rows = squad_rows + extra_mkt
    df_market = pd.DataFrame(all_rows)
    df_squad = pd.DataFrame(squad_rows)
    listings_df = pd.DataFrame([r for r in extra_mkt if r["propietario_liga"] == "Mercado"])
    pos_map = {"Portero": "GK", "Defensa": "DF", "Centrocampista": "MF", "Delantero": "FW"}
    xp_rows = []
    for r in all_rows:
        xp_v = float(r["xp_v"])
        pos = r["posicion"]
        npos = pos_map.get(pos, "MF")
        xp_rows.append({
            "player_id": r["player_id"], "nombre": r["nombre"], "posicion": pos,
            "natural_pos": npos, "valid_lines": npos,
            "precio": r["precio"], "subida_24h": r["subida_24h"], "estado": "ok",
            "media_puntos": r["media_puntos"], "xp_total": xp_v,
            "xp_best": xp_v, "xp_healthy": xp_v,
            "p_start": r["p_start"], "p_start_healthy": r["p_start"],
            "is_available": True,
            "xp_GK": xp_v if pos == "Portero" else 0.0,
            "xp_DF": xp_v if pos == "Defensa" else 0.0,
            "xp_MF": xp_v if pos == "Centrocampista" else 0.0,
            "xp_FW": xp_v if pos == "Delantero" else 0.0,
        })
    xp_table = pd.DataFrame(xp_rows)
    efr_map = {int(r["player_id"]): float(r["subida_24h"]) * 2.5 for r in all_rows}
    offers_map = {13: int(1_500_000 * 1.03)}
    mem = StrategyMemoryLedger(in_memory_only=True)

    # 1. Clause Policy toggle: VALUE_COMPOUND_VORP vs DISABLED
    arch_vorp = replace(base_arch, clause_policy="VALUE_COMPOUND_VORP", clause_min_delta_xp=0.5, clause_min_avg_pts=4.0)
    arch_disabled = replace(base_arch, clause_policy="DISABLED")
    plan_vorp = PortfolioOptimizer(ExpectedPointsEngine(), archetype=arch_vorp).generate_daily_plan(
        current_date="2026-09-22", my_manager_name="Dani SR", current_cash=14_000_000,
        squad_df=df_squad, df_market_all=df_market, market_listings_df=listings_df,
        xp_table=xp_table, efr_map=efr_map, active_offers_map=offers_map,
        clauses_open=True, days_to_kickoff=4, memory_ledger=mem,
    )
    plan_dis = PortfolioOptimizer(ExpectedPointsEngine(), archetype=arch_disabled).generate_daily_plan(
        current_date="2026-09-22", my_manager_name="Dani SR", current_cash=14_000_000,
        squad_df=df_squad, df_market_all=df_market, market_listings_df=listings_df,
        xp_table=xp_table, efr_map=efr_map, active_offers_map=offers_map,
        clauses_open=True, days_to_kickoff=4, memory_ledger=mem,
    )
    assert len(plan_vorp["clause_snipes"]) >= 1
    assert len(plan_dis["clause_snipes"]) == 0

    # 2. Credit utilization aggression + EFR weight toggle affects budgets/bids
    arch_low_cred = replace(base_arch, lifecycle_driven=False, credit_utilization_aggression=0.60, efr_weight=0.5, min_efr_trigger=400_000, max_simultaneous_bids=1)
    arch_hi_cred = replace(base_arch, lifecycle_driven=False, credit_utilization_aggression=1.30, efr_weight=1.6, min_efr_trigger=20_000, max_simultaneous_bids=4)
    plan_low = PortfolioOptimizer(ExpectedPointsEngine(), archetype=arch_low_cred).generate_daily_plan(
        current_date="2026-09-22", my_manager_name="Dani SR", current_cash=2_000_000,
        squad_df=df_squad, df_market_all=df_market, market_listings_df=listings_df,
        xp_table=xp_table, efr_map=efr_map, active_offers_map=offers_map,
        clauses_open=False, days_to_kickoff=4, memory_ledger=mem,
    )
    plan_hi = PortfolioOptimizer(ExpectedPointsEngine(), archetype=arch_hi_cred).generate_daily_plan(
        current_date="2026-09-22", my_manager_name="Dani SR", current_cash=2_000_000,
        squad_df=df_squad, df_market_all=df_market, market_listings_df=listings_df,
        xp_table=xp_table, efr_map=efr_map, active_offers_map=offers_map,
        clauses_open=False, days_to_kickoff=4, memory_ledger=mem,
    )
    assert plan_hi["safe_acquisition_budget"] > plan_low["safe_acquisition_budget"]
    assert len(plan_hi["market_bids"]) >= len(plan_low["market_bids"])
    assert len(plan_low["market_bids"]) <= 1

    # 3. Defensive shield toggle (`shield_rival_threat_pct` 0.0 vs 0.40 when FW1 is under threat)
    arch_no_shield = replace(base_arch, shield_rival_threat_pct=0.0, clause_policy="DISABLED")
    arch_yes_shield = replace(base_arch, shield_rival_threat_pct=0.40, clause_policy="DISABLED")
    plan_no_sh = PortfolioOptimizer(ExpectedPointsEngine(), archetype=arch_no_shield).generate_daily_plan(
        current_date="2026-09-22", my_manager_name="Dani SR", current_cash=10_000_000,
        squad_df=df_squad, df_market_all=df_market, market_listings_df=pd.DataFrame(),
        xp_table=xp_table, efr_map=efr_map, clauses_open=True, days_to_kickoff=4,
        max_rival_purchasing_power=20_000_000.0, memory_ledger=mem,
    )
    plan_yes_sh = PortfolioOptimizer(ExpectedPointsEngine(), archetype=arch_yes_shield).generate_daily_plan(
        current_date="2026-09-22", my_manager_name="Dani SR", current_cash=10_000_000,
        squad_df=df_squad, df_market_all=df_market, market_listings_df=pd.DataFrame(),
        xp_table=xp_table, efr_map=efr_map, clauses_open=True, days_to_kickoff=4,
        max_rival_purchasing_power=20_000_000.0, memory_ledger=mem,
    )
    assert len(plan_no_sh["defensive_shields"]) == 0
    assert len(plan_yes_sh["defensive_shields"]) >= len(plan_no_sh["defensive_shields"])

    # 4. Hold-decay rotation (`hold_decay_penalty_eur` + `speculative_hold_days_max`) forces stale sale vs keeping
    arch_stale_cut = replace(base_arch, clause_policy="DISABLED", speculative_hold_days_max=2, hold_decay_penalty_eur=350_000)
    arch_patient = replace(base_arch, clause_policy="DISABLED", speculative_hold_days_max=20, hold_decay_penalty_eur=0)
    plan_cut = PortfolioOptimizer(ExpectedPointsEngine(), archetype=arch_stale_cut).generate_daily_plan(
        current_date="2026-09-22", my_manager_name="Dani SR", current_cash=5_000_000,
        squad_df=df_squad, df_market_all=df_market, market_listings_df=pd.DataFrame(),
        xp_table=xp_table, efr_map=efr_map, active_offers_map=offers_map,
        clauses_open=False, days_to_kickoff=4, memory_ledger=mem,
    )
    plan_pat = PortfolioOptimizer(ExpectedPointsEngine(), archetype=arch_patient).generate_daily_plan(
        current_date="2026-09-22", my_manager_name="Dani SR", current_cash=5_000_000,
        squad_df=df_squad, df_market_all=df_market, market_listings_df=pd.DataFrame(),
        xp_table=xp_table, efr_map=efr_map, active_offers_map=offers_map,
        clauses_open=False, days_to_kickoff=4, memory_ledger=mem,
    )
    cut_pids = {s["player_id"] for s in plan_cut["routine_sales"]}
    pat_pids = {s["player_id"] for s in plan_pat["routine_sales"]}
    assert 13 in cut_pids and 13 not in pat_pids


def test_rank_posture_adaptation_and_causality():
    """Verifies causal posture shifts (#1 -> PROTECT_LEAD, #2 -> TACTICAL_PRESS, #10 -> ASYMMETRIC_CHASER) & zero leakage."""
    from src.tools.strategy_engine.posture_controller import (
        StandingsPostureController,
        POSTURE_PROTECT,
        POSTURE_PRESS,
        POSTURE_CHASER,
    )

    ctrl = StandingsPostureController(total_season_rounds=38, enabled=True)
    m_lead = ctrl.compute_modifiers(
        my_rank=1, total_managers=15, my_points=650, leader_points=650,
        second_points=590, podium_points=560, rounds_settled=12,
    )
    m_press = ctrl.compute_modifiers(
        my_rank=2, total_managers=15, my_points=620, leader_points=635,
        second_points=620, podium_points=580, rounds_settled=12,
    )
    m_chase = ctrl.compute_modifiers(
        my_rank=10, total_managers=15, my_points=469, leader_points=654,
        second_points=580, podium_points=540, rounds_settled=12,
    )
    assert m_lead.posture == POSTURE_PROTECT
    assert m_press.posture == POSTURE_PRESS
    assert m_chase.posture == POSTURE_CHASER
    assert m_chase.overbid_cap_mult > m_lead.overbid_cap_mult
    assert m_chase.credit_usage_mult > m_lead.credit_usage_mult
    assert m_lead.shield_threshold_mult < m_chase.shield_threshold_mult

    # Strict causality check on environment standings snapshot
    env = AZFinanceMarketEnv(db_path="./data/warehouse/biwenger_history.db", seed=42)
    early = env.get_causal_standings_snapshot("2026-08-22")
    late = env.get_causal_standings_snapshot("2026-11-12")
    assert early["settled_rounds_count"] <= 2
    assert late["settled_rounds_count"] >= 10
    assert early["leader_points"] < late["leader_points"]


def test_llm_supervisor_guardrails():
    """Verifies LLM JSON clamping guardrails, Memoria_Estrategica persistence, and shock triggers."""
    from src.tools.strategy_engine.llm_supervisor import LLMStrategicSupervisor, validate_and_clamp_overrides
    from src.tools.strategy_engine.strategy_memory import StrategyMemoryLedger

    extreme = validate_and_clamp_overrides({
        "posture_override": "ILLEGAL_YOLO",
        "bid_aggression_delta": 99.0,
        "overbid_cap_delta": -9.0,
        "clause_alpha_delta_eur": 50_000_000,
        "speculation_vs_points_tilt": -5.0,
        "shield_aggression_delta": 4.0,
        "credit_aggression_delta": -4.0,
        "clause_timing_preference": "INVALID_TIMING",
        "priority_positions": ["FW", "INVALID", "GK", "DF"],
        "strategic_rationale": "Test clamp",
    })
    assert extreme["posture_override"] == "AUTO"
    assert extreme["bid_aggression_delta"] == 0.15
    assert extreme["overbid_cap_delta"] == -0.10
    assert extreme["clause_alpha_delta_eur"] == 1_500_000
    assert extreme["speculation_vs_points_tilt"] == -0.15
    assert extreme["shield_aggression_delta"] == 0.15
    assert extreme["credit_aggression_delta"] == -0.15
    assert extreme["clause_timing_preference"] == "AUTO"
    assert extreme["priority_positions"] == ["FW", "GK"]

    class _MockLLM:
        def __init__(self):
            self.calls = 0

        def chat_completion(self, **kwargs):
            self.calls += 1
            return {
                "success": True,
                "model_used": "gemini-4-hydrogen",
                "content": '{"posture_override":"ASYMMETRIC_CHASER","bid_aggression_delta":0.08,"overbid_cap_delta":0.06,"priority_positions":["FW","MF"],"strategic_rationale":"Perseguir podio."}',
            }

    mock_llm = _MockLLM()
    mem = StrategyMemoryLedger(in_memory_only=True)
    sup = LLMStrategicSupervisor(llm_client=mock_llm, tier="daily")
    snap = {"my_rank": 10, "total_managers": 15, "my_points": 469, "leader_points": 654, "rounds_settled": 12}

    # Call 1: New settled round triggers LLM and persists state
    r1 = sup.evaluate_or_reuse(
        current_date="2026-11-12", standings_snapshot=snap, posture_modifiers={"posture": "ASYMMETRIC_CHASER"},
        scarcity_summary={"free_ge_5m": 4}, squad_injuries_core=[], memory_ledger=mem,
    )
    assert r1["llm_called"] is True
    assert r1["model_used"] == "gemini-4-hydrogen"
    assert r1["overrides"]["priority_positions"] == ["FW", "MF"]
    assert mock_llm.calls == 1

    # Call 2: Same day & state reuses persisted cache without burning tokens
    r2 = sup.evaluate_or_reuse(
        current_date="2026-11-12", standings_snapshot=snap, posture_modifiers={"posture": "ASYMMETRIC_CHASER"},
        scarcity_summary={"free_ge_5m": 4}, squad_injuries_core=[], memory_ledger=mem,
    )
    assert r2["llm_called"] is False
    assert r2["trigger"] == "CACHED_POSTURE"
    assert mock_llm.calls == 1

    # Call 3: New injury shock wakes up supervisor immediately
    r3 = sup.evaluate_or_reuse(
        current_date="2026-11-12", standings_snapshot=snap, posture_modifiers={"posture": "ASYMMETRIC_CHASER"},
        scarcity_summary={"free_ge_5m": 4},
        squad_injuries_core=[{"player_id": 999, "nombre": "Vinicius Jr", "estado": "injured", "precio": 18_000_000}],
        memory_ledger=mem,
    )
    assert r3["llm_called"] is True
    assert "KEY_INJURY_SHOCK" in r3["trigger"]
    assert mock_llm.calls == 2


def test_sharpe_ratio_nonzero_and_j12_benchmark():
    """Verifies J1..J12 benchmark totals (`Birra=654`, `Dani SR=469`) and non-zero Sharpe in simulator."""
    from src.tools.backtester.benchmark_suite import BacktestBenchmarkSuite
    from src.tools.backtester.scenario_simulator import ScenarioTournamentRunner

    suite = BacktestBenchmarkSuite(db_path="./data/warehouse/biwenger_history.db")
    df_real_standings, _ = suite.load_real_league_benchmarks()
    mgr_map = {row["manager"]: row for _, row in df_real_standings.iterrows()}
    assert int(mgr_map["Birra München"]["max_settled_jn"]) >= 12
    assert int(mgr_map["Dani SR"]["j11_partial_points"]) == 0
    assert int(mgr_map["Birra München"]["points_settled"]) == 654
    assert int(mgr_map["Dani SR"]["points_settled"]) == 469

    runner = ScenarioTournamentRunner(db_path="./data/warehouse/biwenger_history.db")
    df_sim = runner.run_tournament(
        archetype_ids=["ADAPTIVE_LIFECYCLE"],
        scenario_ids=["GROUND_TRUTH_40M"],
        seeds=[42],
        max_workers=1,
    )
    row = df_sim.iloc[0]
    assert row["settled_jornadas"] >= 12
    assert row["total_points_settled"] > 400
    assert row["sharpe_ratio"] > 0.0
    assert row["insolvent_rounds"] == 0


def test_full_auto_clause_autonomy():
    """Verifies FULL_AUTO executes solvent clauses <= min(25M, 45% equity) with alerts while SEMI_AUTO queues."""
    import pandas as pd
    from unittest.mock import patch, MagicMock
    from src.tools.strategy_engine.autopilot import AutopilotRunner
    from src.tools.strategy_engine.strategy_memory import StrategyMemoryLedger
    from src.tools.strategy_engine.league_rules import RoundPhaseContext

    # Net equity = 35M cash + 5M squad = 40M -> dyn_clause_cap = min(25M, max(6M, 0.45 * 40M)) = 18M
    phase_lock = RoundPhaseContext(
        now_ts=pd.Timestamp("2026-11-12 12:00:00"),
        phase_name="PRE_CLAUSE_LOCK_WINDOW",
        target_jornada_num=13,
        active_in_progress_jornada=None,
        next_kickoff_ts=pd.Timestamp("2026-11-13 21:00:00"),
        clause_lock_ts=pd.Timestamp("2026-11-12 21:00:00"),
        active_round_end_ts=None,
        hours_to_kickoff=33.0,
        days_to_kickoff=1,
        hours_to_clause_lock=9.0,
        is_kickoff_day=False,
        must_be_solvent_now=True,
        clauses_open=True,
        can_use_credit_leverage=False,
        is_international_break=False,
        is_schedule_placeholder=False,
        in_pre_clause_lock_window=True,
    )
    sq_df = pd.DataFrame([
        {"player_id": 10, "nombre": "Core", "posicion": "Delantero", "precio": 5_000_000, "subida_24h": 0, "estado": "ok", "propietario_liga": "Dani SR"}
    ])
    fake_ctx = {
        "date": "2026-11-12", "team_name": "Dani SR", "league_name": "AZ Finance", "is_live_api": False,
        "team_id": 1, "league_id": 1,
        "target_jornada_num": 13, "days_to_kickoff": 1, "is_kickoff_day": False,
        "phase_ctx": phase_lock, "current_cash": 35_000_000.0, "is_solvent": True,
        "squad_df": sq_df, "market_today_df": sq_df.copy(), "listings_df": pd.DataFrame(),
        "offers_map": {}, "offer_id_by_pid": {}, "sell_locked_ids": set(), "clause_locked_ids": set(),
        "indebted_rivals": set(), "manager_id_by_name": {"RivalA": 101, "RivalB": 102},
        "league_median_max_bid": 15_000_000.0, "max_rival_pp": 25_000_000.0,
        "directives": {"untouchable_ids": set(), "force_sell_ids": set(), "blacklist_buy_ids": set(), "priority_target_ids": set()},
        "standings_snapshot": {"my_rank": 10, "total_managers": 15, "my_points": 469, "leader_points": 654, "second_points": 580, "podium_points": 540, "rounds_settled": 12, "my_net_worth": 40_000_000, "leader_net_worth": 65_000_000},
    }
    fake_plan = {
        "starting_cash": 35_000_000,
        "clauses_open": True, "is_kickoff_day": False, "can_use_credit_leverage": False,
        "routine_sales": [], "solvency_sales": [], "market_bids": [],
        "clause_snipes": [
            {"player_id": 501, "nombre": "Snipe 12M", "seller": "RivalA", "amount": 12_000_000, "market_price": 8_000_000, "delta_xp_xi": 1.8, "expected_surplus": 2_500_000, "scarcity_tier": "SCARCE"},
            {"player_id": 502, "nombre": "Mega 20M", "seller": "RivalB", "amount": 20_000_000, "market_price": 13_000_000, "delta_xp_xi": 2.1, "expected_surplus": 3_000_000, "scarcity_tier": "ELITE"},
        ],
        "defensive_shields": [], "preventive_listings": [], "asset_theses": [],
        "lineup": {"formation": "4-4-2", "player_ids": [10], "slots": [], "expected_points": 50.0},
    }

    def _run_mode(mode_name: str):
        mock_actions = MagicMock()
        mock_actions.pay_clause.return_value = {"status": "EXECUTED_LIVE", "mode": mode_name}
        runner = AutopilotRunner(mode=mode_name, dry_run=False, today_date="2026-11-12", trigger_phase="PRE_CLAUSE_LOCK", use_live_delta=False)
        runner.memory = StrategyMemoryLedger(in_memory_only=True)
        with patch.object(runner, "load_active_context", return_value=fake_ctx), \
             patch.object(runner.optimizer, "generate_daily_plan", return_value=fake_plan), \
             patch("src.tools.strategy_engine.autopilot.BiwengerMarketActions", return_value=mock_actions), \
             patch("src.tools.strategy_engine.autopilot.TieredLLMRouter.get_client", return_value=None):
            res = runner.execute_cycle(generate_briefing=False, send_notifications=False)
        return res, mock_actions

    res_full, act_full = _run_mode("FULL_AUTO")
    assert res_full["autonomous_clause_cap_eur"] == 18_000_000
    assert act_full.pay_clause.call_count == 1
    assert act_full.pay_clause.call_args.kwargs["player_id"] == 501
    assert len(res_full["high_value_clause_alerts"]) == 1
    assert res_full["high_value_clause_alerts"][0]["nombre"] == "Snipe 12M"
    assert len(res_full["pending_decisions"]) == 1  # 20M exceeds 18M dynamic cap -> queued

    res_semi, act_semi = _run_mode("SEMI_AUTO")
    assert act_semi.pay_clause.call_count == 0
    assert len(res_semi["pending_decisions"]) == 2
    assert len(res_semi["high_value_clause_alerts"]) == 0


if __name__ == "__main__":
    test_lifecycle_early_expansion()
    test_lifecycle_mature_scarcity()
    test_lifecycle_fifa_break()
    test_lifecycle_endgame_runin()
    test_injury_tactician_minor_injury_hold()
    test_injury_tactician_fifa_break_absorption()
    test_injury_tactician_torn_acl_cash_out()
    test_injury_tactician_speculative_flip_depreciating()
    test_archetypes_integrity()
    test_market_env_starting_parity()
    test_wiring_closure()
    test_rank_posture_adaptation_and_causality()
    test_llm_supervisor_guardrails()
    test_sharpe_ratio_nonzero_and_j12_benchmark()
    test_full_auto_clause_autonomy()
    print("ALL INTELLIGENCE UNIT TESTS PASSED!")
