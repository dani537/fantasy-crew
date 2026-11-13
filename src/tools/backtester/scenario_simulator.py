"""
Multi-Scenario Monte Carlo Strategy Tournament Engine (`scenario_simulator.py`)
==============================================================================
Evaluates sports-finance strategy archetypes (and parametric candidates) across
diverse macro-economic regimes (Ground Truth 40M parity, Strict Observed scarcity,
1.25x Auction Competition, Liquidity Haircuts, 2x Injury Crisis, Predatory Rivals)
to discover Pareto-optimal strategies (maximizing #1 Win Rate + Total Settled
Points subject to zero solvency penalties across H1/H2 regimes).
"""

from dataclasses import dataclass, replace
from typing import Dict, List, Any, Optional, Tuple
import concurrent.futures
import time
import numpy as np
import pandas as pd

from src.tools.backtester.market_env import AZFinanceMarketEnv
from src.tools.strategy_engine.archetypes import STRATEGY_ARCHETYPES, StrategyArchetype


@dataclass
class SimulationScenario:
    """Macro/market stress regime configuration for Monte Carlo simulation."""
    scenario_id: str
    name: str
    description: str
    equalize_starting_wealth: bool = True
    credit_limit_ratio: float = 0.25
    market_mode: str = "monte_carlo_20slot"
    offer_mult_range: Tuple[float, float] = (0.95, 1.05)
    overbid_inflation_factor: float = 1.0
    injury_stress_mult: float = 1.0
    aggressive_rival_clause_prob: float = 0.0
    initial_manager_override: Optional[str] = None
    rank_weight_in_pareto: float = 1.0


DEFAULT_SCENARIOS: Dict[str, SimulationScenario] = {
    "GROUND_TRUTH_40M": SimulationScenario(
        scenario_id="GROUND_TRUTH_40M",
        name="Paridad Real 40M (Baseline MC20)",
        description="Presupuesto inicial igualado a 40M exactos con mercado completo de 20 slots.",
        equalize_starting_wealth=True,
        credit_limit_ratio=0.25,
        market_mode="monte_carlo_20slot",
        rank_weight_in_pareto=1.5,
    ),
    "STRICT_OBSERVED": SimulationScenario(
        scenario_id="STRICT_OBSERVED",
        name="Escasez Estricta Observada",
        description="Solo jugadores observados históricamente en el tablón de mercado (sin relleno MC20).",
        equalize_starting_wealth=True,
        credit_limit_ratio=0.25,
        market_mode="strict_observed",
        rank_weight_in_pareto=1.2,
    ),
    "HIGH_COMPETITION_125": SimulationScenario(
        scenario_id="HIGH_COMPETITION_125",
        name="Subastas Inflacionistas (1.25x)",
        description="Los rivales pujan un +25% más agresivamente sobre el VM en subastas competidas.",
        equalize_starting_wealth=True,
        credit_limit_ratio=0.25,
        market_mode="monte_carlo_20slot",
        overbid_inflation_factor=1.25,
        rank_weight_in_pareto=1.2,
    ),
    "SEVERE_LIQUIDITY_HAIRCUT": SimulationScenario(
        scenario_id="SEVERE_LIQUIDITY_HAIRCUT",
        name="Restricción Severa de Liquidez",
        description="Crédito cero (0%) y ofertas de mercado deprimidas (0.90x-0.96x) para liquidar activos.",
        equalize_starting_wealth=True,
        credit_limit_ratio=0.0,
        market_mode="monte_carlo_20slot",
        offer_mult_range=(0.90, 0.96),
        rank_weight_in_pareto=1.0,
    ),
    "INJURY_CRISIS_2X": SimulationScenario(
        scenario_id="INJURY_CRISIS_2X",
        name="Crisis de Lesiones (2x)",
        description="Duplica la frecuencia de lesiones en plantilla para auditar resiliencia y rotaciones.",
        equalize_starting_wealth=True,
        credit_limit_ratio=0.25,
        market_mode="monte_carlo_20slot",
        injury_stress_mult=2.0,
        rank_weight_in_pareto=1.0,
    ),
    "PREDATORY_RIVALS": SimulationScenario(
        scenario_id="PREDATORY_RIVALS",
        name="Rivales Predatorios (Cláusulas + Inflación)",
        description="Aumenta la presión de clausulazos rivales sobre gangas y eleva competencia de pujas (+15%).",
        equalize_starting_wealth=True,
        credit_limit_ratio=0.25,
        market_mode="monte_carlo_20slot",
        overbid_inflation_factor=1.15,
        aggressive_rival_clause_prob=0.08,
        rank_weight_in_pareto=1.1,
    ),
    "STRICT_CASH_LIQUIDITY": SimulationScenario(
        scenario_id="STRICT_CASH_LIQUIDITY",
        name="Restricción de Apalancamiento (Crédito Cero)",
        description="Prohíbe operar en números rojos (credit_limit_ratio = 0.0). Alias de auditoría de caja pura.",
        equalize_starting_wealth=True,
        credit_limit_ratio=0.0,
        market_mode="monte_carlo_20slot",
        rank_weight_in_pareto=1.0,
    ),
}

M2_STRESS_SCENARIOS: List[str] = [
    "GROUND_TRUTH_40M",
    "STRICT_OBSERVED",
    "HIGH_COMPETITION_125",
    "SEVERE_LIQUIDITY_HAIRCUT",
    "INJURY_CRISIS_2X",
    "PREDATORY_RIVALS",
]

SCENARIO_ALIASES: Dict[str, str] = {
    "baseline_mc20": "GROUND_TRUTH_40M",
    "strict_observed": "STRICT_OBSERVED",
    "high_competition_1.25x": "HIGH_COMPETITION_125",
    "severe_liquidity_haircut": "SEVERE_LIQUIDITY_HAIRCUT",
    "injury_crisis_2x": "INJURY_CRISIS_2X",
    "predatory_rivals": "PREDATORY_RIVALS",
}


def resolve_scenario(scenario_or_id: Any) -> SimulationScenario:
    """Resolves a SimulationScenario object or ID/alias into a concrete SimulationScenario."""
    if isinstance(scenario_or_id, SimulationScenario):
        return scenario_or_id
    key = str(scenario_or_id or "GROUND_TRUTH_40M").strip()
    mapped = SCENARIO_ALIASES.get(key.lower(), key)
    return DEFAULT_SCENARIOS.get(mapped, DEFAULT_SCENARIOS.get(mapped.upper(), DEFAULT_SCENARIOS["GROUND_TRUTH_40M"]))


# Per-process cache so worker processes instantiate AZFinanceMarketEnv + SQLite + daily caches ONCE.
_WORKER_ENV_CACHE: Dict[str, AZFinanceMarketEnv] = {}


def _init_worker(db_path: str) -> None:
    """Pool initializer: warm up per-worker AZFinanceMarketEnv once per worker process."""
    if db_path not in _WORKER_ENV_CACHE:
        _WORKER_ENV_CACHE[db_path] = AZFinanceMarketEnv(db_path=db_path, seed=42)


def _get_worker_env(db_path: str) -> AZFinanceMarketEnv:
    env = _WORKER_ENV_CACHE.get(db_path)
    if env is None:
        env = AZFinanceMarketEnv(db_path=db_path, seed=42)
        _WORKER_ENV_CACHE[db_path] = env
    return env


def _evaluate_task(task: Tuple[Any, Any, int, str, Optional[str]]) -> Dict[str, Any]:
    """
    Runs one (archetype, scenario, seed, db_path, manager_override) evaluation
    reusing the worker's cached AZFinanceMarketEnv and strict causal caches.
    """
    arch_or_id, scen_or_id, seed, db_path, mgr_override = task
    arch = arch_or_id if isinstance(arch_or_id, StrategyArchetype) else STRATEGY_ARCHETYPES[str(arch_or_id)]
    scen = resolve_scenario(scen_or_id)

    t0 = time.time()
    env = _get_worker_env(db_path)
    # Apply scenario stress modifiers prior to reset() & run_simulation()
    env.market_mode = scen.market_mode
    env.credit_limit_ratio = float(scen.credit_limit_ratio)
    env.equalize_starting_wealth = bool(scen.equalize_starting_wealth)
    env.offer_mult_range = (float(scen.offer_mult_range[0]), float(scen.offer_mult_range[1]))
    env.overbid_inflation_factor = float(scen.overbid_inflation_factor)
    env.injury_stress_mult = float(scen.injury_stress_mult)
    env.aggressive_rival_clause_prob = float(scen.aggressive_rival_clause_prob)

    eff_manager = mgr_override or scen.initial_manager_override or env.base_target_manager
    env.reset(manager_override=eff_manager, seed_override=int(seed))
    sim_res = env.run_simulation(archetype=arch)
    dur = time.time() - t0

    settled_js = list(getattr(env, "settled_jornada_nums", list(range(1, 11))))
    bot_pts_settled = int(sim_res.get("total_points_settled", sim_res.get("total_points_j1_j10", 0)))
    bot_pts_j10 = int(sim_res.get("total_points_j1_j10", bot_pts_settled))
    pts_h1 = int(sim_res.get("points_h1_j1_j5", 0))
    pts_h2 = int(sim_res.get("points_h2", sim_res.get("points_h2_j6_j10", 0)))
    pts_j11_j12 = int(sim_res.get("j11_j12_points", sim_res.get("j11_partial_points", 0)))

    # Dynamic rank vs rivals across ALL officially settled rounds (e.g. J1..J12)
    rival_totals: Dict[str, int] = {}
    for jn in settled_js:
        for mgr, pts in env.rival_round_points.get(jn, {}).items():
            rival_totals[mgr] = rival_totals.get(mgr, 0) + pts

    higher_rivals = sum(1 for pts in rival_totals.values() if pts > bot_pts_settled)
    final_rank = 1 + higher_rivals
    is_champion = (final_rank == 1)
    is_podium = (final_rank <= 3)

    # Settled round points stability (Sharpe ratio using exact `"points"` key)
    round_bd = sim_res.get("round_breakdown", {}) or {}
    settled_set = set(settled_js)
    round_pts = [
        int(r.get("points", r.get("total_points", 0)))
        for jn, r in sorted(round_bd.items())
        if (not settled_set or jn in settled_set)
    ]
    pts_mean = float(np.mean(round_pts)) if round_pts else 0.0
    pts_std = float(np.std(round_pts)) if len(round_pts) > 1 else 1.0
    sharpe = round(pts_mean / max(pts_std, 0.01), 2)

    insolvent_cnt = int(sim_res.get("insolvent_rounds", sim_res.get("insolvent_Zero_rounds", 0)))

    return {
        "archetype_id": arch.archetype_id,
        "archetype_name": arch.name,
        "scenario_id": scen.scenario_id,
        "manager_start": eff_manager,
        "seed": int(seed),
        "settled_jornadas": len(settled_js),
        "total_points_settled": bot_pts_settled,
        "total_points_j1_j10": bot_pts_j10,
        "total_points_incl_j11": int(sim_res.get("total_points_incl_j11", bot_pts_j10)),
        "points_h1_j1_j5": pts_h1,
        "points_h2": pts_h2,
        "j11_j12_points": pts_j11_j12,
        "final_rank": final_rank,
        "is_champion": is_champion,
        "is_podium": is_podium,
        "final_net_worth": int(sim_res.get("final_net_worth", 0)),
        "final_squad_value": int(sim_res.get("final_squad_value", 0)),
        "final_cash": int(sim_res.get("final_cash", 0)),
        "trading_alpha_eur": int(sim_res.get("trading_alpha_eur", 0)),
        "clause_snipes": int(sim_res.get("clause_snipes_executed", 0)),
        "clause_steals": int(sim_res.get("clause_steals_suffered", 0)),
        "total_sales": int(sim_res.get("total_sales", 0)),
        "shields_spent_eur": int(sim_res.get("shields_spent_eur", 0)),
        "bid_win_rate_pct": float(sim_res.get("bid_win_rate_pct", 0.0)),
        "insolvent_rounds": insolvent_cnt,
        "sharpe_ratio": sharpe,
        "pareto_weight": float(scen.rank_weight_in_pareto),
        "duration_sec": round(dur, 2),
    }


def _run_single_worker(args: Tuple[Any, ...]) -> Dict[str, Any]:
    """Backwards-compatible wrapper accepting 4-tuple or 5-tuple task arguments."""
    if len(args) == 4:
        a, s, seed, db = args
        return _evaluate_task((a, s, seed, db, None))
    return _evaluate_task((args[0], args[1], args[2], args[3], args[4]))


class ScenarioTournamentRunner:
    """Executes multi-seed, multi-scenario parallel tournaments across strategy candidates."""

    def __init__(self, db_path: str = "./data/warehouse/biwenger_history.db"):
        self.db_path = db_path

    def run_tournament(
        self,
        archetype_ids: Optional[List[str]] = None,
        scenario_ids: Optional[List[Any]] = None,
        seeds: Optional[List[int]] = None,
        max_workers: int = 4,
        custom_archetypes: Optional[Dict[str, StrategyArchetype]] = None,
        manager_overrides: Optional[List[Optional[str]]] = None,
    ) -> pd.DataFrame:
        arch_objs: List[StrategyArchetype] = []
        if custom_archetypes is not None:
            if archetype_ids:
                for aid in archetype_ids:
                    if aid in custom_archetypes:
                        arch_objs.append(custom_archetypes[aid])
                    elif aid in STRATEGY_ARCHETYPES:
                        arch_objs.append(STRATEGY_ARCHETYPES[aid])
            else:
                arch_objs = list(custom_archetypes.values())
        else:
            for aid in (archetype_ids or list(STRATEGY_ARCHETYPES.keys())):
                arch_objs.append(STRATEGY_ARCHETYPES[aid])

        scen_list = [resolve_scenario(s) for s in (scenario_ids or ["GROUND_TRUTH_40M"])]
        seed_list = list(seeds or [42, 101, 2024, 7, 99])
        mgr_list: List[Optional[str]] = list(manager_overrides) if manager_overrides else [None]

        tasks: List[Tuple[StrategyArchetype, SimulationScenario, int, str, Optional[str]]] = [
            (arch_obj, scen_obj, int(s), self.db_path, mgr)
            for arch_obj in arch_objs
            for scen_obj in scen_list
            for mgr in mgr_list
            for s in seed_list
        ]
        if not tasks:
            return pd.DataFrame()

        eff_workers = min(max(1, int(max_workers)), 12, len(tasks))
        results: List[Dict[str, Any]] = []
        if eff_workers <= 1:
            _init_worker(self.db_path)
            for t in tasks:
                results.append(_evaluate_task(t))
        else:
            with concurrent.futures.ProcessPoolExecutor(
                max_workers=eff_workers,
                initializer=_init_worker,
                initargs=(self.db_path,),
            ) as executor:
                for res in executor.map(_evaluate_task, tasks):
                    results.append(res)

        return pd.DataFrame(results)

    @staticmethod
    def aggregate_leaderboard(df_results: pd.DataFrame) -> pd.DataFrame:
        """
        Computes aggregate Pareto performance metrics per archetype/candidate.
        Strict solvency constraint: candidates with >0 insolvent rounds are demoted
        below all strictly solvent strategies.
        """
        if df_results is None or df_results.empty:
            return pd.DataFrame()

        df = df_results.copy()
        if "total_points_settled" not in df.columns:
            df["total_points_settled"] = df["total_points_j1_j10"]
        if "points_h2" not in df.columns:
            df["points_h2"] = 0
        if "j11_j12_points" not in df.columns:
            df["j11_j12_points"] = 0
        if "clause_steals" not in df.columns:
            df["clause_steals"] = 0

        grouped = df.groupby(["archetype_id", "archetype_name"], as_index=False).agg(
            runs=("seed", "count"),
            avg_points_settled=("total_points_settled", "mean"),
            std_points_settled=("total_points_settled", "std"),
            max_points_settled=("total_points_settled", "max"),
            min_points_settled=("total_points_settled", "min"),
            avg_points_h2=("points_h2", "mean"),
            avg_points_j11_j12=("j11_j12_points", "mean"),
            avg_points_j10=("total_points_j1_j10", "mean"),
            std_points_j10=("total_points_j1_j10", "std"),
            avg_rank=("final_rank", "mean"),
            win_rate_pct=("is_champion", lambda x: round(float(np.mean(x)) * 100.0, 1)),
            podium_rate_pct=("is_podium", lambda x: round(float(np.mean(x)) * 100.0, 1)),
            avg_net_worth=("final_net_worth", "mean"),
            avg_snipes=("clause_snipes", "mean"),
            avg_steals=("clause_steals", "mean"),
            avg_sharpe=("sharpe_ratio", "mean"),
            total_insolvent_penalties=("insolvent_rounds", "sum"),
        )

        for col in ("std_points_settled", "std_points_j10"):
            grouped[col] = grouped[col].fillna(0.0).round(1)
        grouped["avg_points_settled"] = grouped["avg_points_settled"].round(1)
        grouped["avg_points_h2"] = grouped["avg_points_h2"].round(1)
        grouped["avg_points_j11_j12"] = grouped["avg_points_j11_j12"].round(1)
        grouped["avg_points_j10"] = grouped["avg_points_j10"].round(1)
        grouped["avg_rank"] = grouped["avg_rank"].round(2)
        grouped["avg_net_worth"] = grouped["avg_net_worth"].astype(int)
        grouped["avg_snipes"] = grouped["avg_snipes"].round(2)
        grouped["avg_steals"] = grouped["avg_steals"].round(2)
        grouped["avg_sharpe"] = grouped["avg_sharpe"].round(2)
        grouped["is_solvent"] = grouped["total_insolvent_penalties"] == 0

        # Pareto Champion criterion: zero insolvencies, maximizing #1 Win Rate + Total Settled Points (+ H2 edge)
        grouped["pareto_score"] = (
            grouped["avg_points_settled"]
            + grouped["win_rate_pct"] * 1.5
            + grouped["podium_rate_pct"] * 0.25
            + grouped["avg_points_h2"] * 0.20
            - grouped["total_insolvent_penalties"] * 1000.0
        ).round(2)

        sorted_lb = grouped.sort_values(
            by=["is_solvent", "pareto_score", "win_rate_pct", "avg_points_settled", "avg_sharpe"],
            ascending=[False, False, False, False, False],
        )
        return sorted_lb.reset_index(drop=True)
