"""
Multi-Scenario Monte Carlo Strategy Tournament Engine (`scenario_simulator.py`)
==============================================================================
Evaluates sports-finance strategy archetypes across diverse macro-economic
scenarios (Ground Truth 40M parity, High Injury Stress, Aggressive Rival Clauses)
to discover robust, Pareto-optimal strategies (highest expected points, highest win/podium
rate, lowest points volatility / Sharpe ratio, zero solvency penalties).
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Tuple
import concurrent.futures
import time
import numpy as np
import pandas as pd

from src.tools.backtester.market_env import AZFinanceMarketEnv
from src.tools.strategy_engine.archetypes import STRATEGY_ARCHETYPES, StrategyArchetype


@dataclass
class SimulationScenario:
    scenario_id: str
    name: str
    description: str
    equalize_starting_wealth: bool = True
    credit_limit_ratio: float = 0.25
    market_mode: str = "monte_carlo_20slot"


DEFAULT_SCENARIOS: Dict[str, SimulationScenario] = {
    "GROUND_TRUTH_40M": SimulationScenario(
        scenario_id="GROUND_TRUTH_40M",
        name="Paridad Real 40M (Temporada Oficial)",
        description="Presupuesto inicial igualado a 40M exactos. Dinámica de mercado y subastas reales de AZ Finance.",
        equalize_starting_wealth=True,
    ),
    "STRICT_CASH_LIQUIDITY": SimulationScenario(
        scenario_id="STRICT_CASH_LIQUIDITY",
        name="Restricción de Apalancamiento (Crédito Cero)",
        description="Prohíbe operar en números rojos (credit_limit_ratio = 0.0). Pone a prueba la gestión de caja pura.",
        equalize_starting_wealth=True,
        credit_limit_ratio=0.0,
    ),
}


def _run_single_worker(args: Tuple[str, str, int, str]) -> Dict[str, Any]:
    """Worker function for parallel execution across process pools."""
    archetype_id, scenario_id, seed, db_path = args
    arch = STRATEGY_ARCHETYPES[archetype_id]
    scen = DEFAULT_SCENARIOS.get(scenario_id, DEFAULT_SCENARIOS["GROUND_TRUTH_40M"])

    t0 = time.time()
    env = AZFinanceMarketEnv(
        db_path=db_path,
        seed=seed,
        market_mode=scen.market_mode,
        credit_limit_ratio=scen.credit_limit_ratio,
        equalize_starting_wealth=scen.equalize_starting_wealth,
    )
    sim_res = env.run_simulation(archetype=arch)
    dur = time.time() - t0

    # Calculate rank vs rivals across J1..J10
    bot_pts_j10 = int(sim_res["total_points_j1_j10"])
    rival_totals: Dict[str, int] = {}
    for jn in range(1, 11):
        for mgr, pts in env.rival_round_points.get(jn, {}).items():
            rival_totals[mgr] = rival_totals.get(mgr, 0) + pts

    higher_rivals = sum(1 for pts in rival_totals.values() if pts > bot_pts_j10)
    final_rank = 1 + higher_rivals
    is_champion = (final_rank == 1)
    is_podium = (final_rank <= 3)

    # Round points array for stability (Sharpe)
    round_pts = [int(r.get("total_points", 0)) for r in sim_res["round_breakdown"].values()]
    pts_mean = float(np.mean(round_pts)) if round_pts else 0.0
    pts_std = float(np.std(round_pts)) if len(round_pts) > 1 else 1.0
    sharpe = round(pts_mean / max(pts_std, 0.01), 2)

    return {
        "archetype_id": archetype_id,
        "archetype_name": arch.name,
        "scenario_id": scenario_id,
        "seed": seed,
        "total_points_j1_j10": bot_pts_j10,
        "total_points_incl_j11": int(sim_res["total_points_incl_j11"]),
        "final_rank": final_rank,
        "is_champion": is_champion,
        "is_podium": is_podium,
        "final_net_worth": int(sim_res["final_net_worth"]),
        "final_squad_value": int(sim_res["final_squad_value"]),
        "final_cash": int(sim_res["final_cash"]),
        "trading_alpha_eur": int(sim_res["trading_alpha_eur"]),
        "clause_snipes": int(sim_res["clause_snipes_executed"]),
        "clause_steals": int(sim_res["clause_steals_suffered"]),
        "bid_win_rate_pct": float(sim_res["bid_win_rate_pct"]),
        "insolvent_rounds": int(sim_res["insolvent_Zero_rounds"]),
        "sharpe_ratio": sharpe,
        "duration_sec": round(dur, 2),
    }


class ScenarioTournamentRunner:
    """Executes multi-seed, multi-scenario tournaments across all strategy archetypes."""

    def __init__(self, db_path: str = "./data/warehouse/biwenger_history.db"):
        self.db_path = db_path

    def run_tournament(
        self,
        archetype_ids: Optional[List[str]] = None,
        scenario_ids: Optional[List[str]] = None,
        seeds: Optional[List[int]] = None,
        max_workers: int = 4,
    ) -> pd.DataFrame:
        arch_list = archetype_ids or list(STRATEGY_ARCHETYPES.keys())
        scen_list = scenario_ids or ["GROUND_TRUTH_40M"]
        seed_list = seeds or [42, 101, 2024, 7, 99]

        tasks = [
            (arch_id, scen_id, s, self.db_path)
            for arch_id in arch_list
            for scen_id in scen_list
            for s in seed_list
        ]

        results = []
        if max_workers <= 1 or len(tasks) == 1:
            for t in tasks:
                results.append(_run_single_worker(t))
        else:
            with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
                for res in executor.map(_run_single_worker, tasks):
                    results.append(res)

        return pd.DataFrame(results)

    @staticmethod
    def aggregate_leaderboard(df_results: pd.DataFrame) -> pd.DataFrame:
        """Computes aggregate performance statistics per archetype."""
        grouped = df_results.groupby(["archetype_id", "archetype_name"]).agg(
            runs=("seed", "count"),
            avg_points_j10=("total_points_j1_j10", "mean"),
            std_points_j10=("total_points_j1_j10", "std"),
            max_points_j10=("total_points_j1_j10", "max"),
            min_points_j10=("total_points_j1_j10", "min"),
            avg_rank=("final_rank", "mean"),
            win_rate_pct=("is_champion", lambda x: round(np.mean(x) * 100.0, 1)),
            podium_rate_pct=("is_podium", lambda x: round(np.mean(x) * 100.0, 1)),
            avg_net_worth=("final_net_worth", "mean"),
            avg_snipes=("clause_snipes", "mean"),
            avg_sharpe=("sharpe_ratio", "mean"),
            total_insolvent_penalties=("insolvent_rounds", "sum"),
        ).reset_index()

        grouped["avg_points_j10"] = grouped["avg_points_j10"].round(1)
        grouped["avg_rank"] = grouped["avg_rank"].round(2)
        grouped["avg_net_worth"] = grouped["avg_net_worth"].astype(int)
        grouped["avg_sharpe"] = grouped["avg_sharpe"].round(2)

        # Sort by points descending, then win rate descending
        sorted_lb = grouped.sort_values(by=["avg_points_j10", "win_rate_pct"], ascending=[False, False])
        return sorted_lb.reset_index(drop=True)
