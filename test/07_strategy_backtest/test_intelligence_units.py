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
    assert ctx["max_simultaneous_bids"] == 2


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
    assert len(STRATEGY_ARCHETYPES) == 5
    for key, arch in STRATEGY_ARCHETYPES.items():
        assert isinstance(arch, StrategyArchetype)
        assert 13 <= arch.target_roster_size <= 21
        assert 0.0 <= arch.clause_cash_reserve_pct <= 0.95
        assert 1.50 <= arch.clause_ratio_cap <= 3.00


def test_market_env_starting_parity():
    # Test that equalize_starting_wealth produces exactly 40M initial net worth
    env = AZFinanceMarketEnv(
        db_path="./data/warehouse/biwenger_history.db",
        equalize_starting_wealth=True,
    )
    assert env.initial_net_worth == 40_000_000
    sq_val = sum(st.purchase_price for st in env.squad.values())
    assert int(round(env.cash + sq_val)) == 40_000_000


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
    print("ALL INTELLIGENCE UNIT TESTS PASSED!")
