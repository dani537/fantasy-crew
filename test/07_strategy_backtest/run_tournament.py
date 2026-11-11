#!/usr/bin/env python3
"""
Multi-Scenario Quantitative Strategy Tournament Runner (`run_tournament.py`)
==========================================================================
Runs competing strategy archetypes across Monte Carlo seeds and scenarios,
ranks them by points, win rate, net worth growth, and points stability, and
optionally writes the winning Pareto champion configuration to `src/config.py`.

Usage:
    PYTHONPATH=. .venv/bin/python test/07_strategy_backtest/run_tournament.py --seeds 3 --cores 6
    PYTHONPATH=. .venv/bin/python test/07_strategy_backtest/run_tournament.py --apply-best
"""

import argparse
import sys
import os
import re
import pandas as pd
from tabulate import tabulate

from src.tools.strategy_engine.archetypes import STRATEGY_ARCHETYPES, StrategyArchetype
from src.tools.backtester.scenario_simulator import (
    ScenarioTournamentRunner,
    DEFAULT_SCENARIOS,
)


def apply_champion_to_config(best_arch: StrategyArchetype, config_path: str = "src/config.py", env_path: str = ".env") -> None:
    """Updates StrategySettings in src/config.py and .env with the champion strategy parameters."""
    env_vars = {
        "ACTIVE_ARCHETYPE": best_arch.archetype_id,
        "CLAUSE_POLICY": "COMPETITIVE_VORP",
        "CLAUSE_RATIO_CAP": str(best_arch.clause_ratio_cap),
        "CLAUSE_MIN_AVG_PTS": str(best_arch.clause_min_avg_pts),
        "CLAUSE_MIN_DELTA_XP": str(best_arch.clause_min_delta_xp),
        "PROTECT_PILLARS_ABSOLUTE": "true" if best_arch.protect_pillars_absolute else "false",
    }

    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        new_lines = []
        found_keys = set()
        for line in lines:
            stripped = line.strip()
            if "=" in stripped and not stripped.startswith("#"):
                k = stripped.split("=", 1)[0].strip()
                if k in env_vars:
                    new_lines.append(f"{k}={env_vars[k]}\n")
                    found_keys.add(k)
                    continue
            new_lines.append(line)
        for k, v in env_vars.items():
            if k not in found_keys:
                new_lines.append(f"{k}={v}\n")
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        print(f"[OK] .env updated with champion parameters for {best_arch.archetype_id}.")

    print(f"\n[OK] Configuration successfully applied with Champion Archetype '{best_arch.archetype_id}'.")


def main():
    parser = argparse.ArgumentParser(description="Monte Carlo Strategy Tournament Runner")
    parser.add_argument("--archetypes", nargs="+", default=list(STRATEGY_ARCHETYPES.keys()), help="Archetype IDs to evaluate")
    parser.add_argument("--scenarios", nargs="+", default=["GROUND_TRUTH_40M"], help="Scenarios to run")
    parser.add_argument("--seeds", type=int, default=3, help="Number of Monte Carlo seeds per archetype (e.g. 3, 5)")
    parser.add_argument("--cores", type=int, default=6, help="Parallel worker processes")
    parser.add_argument("--output-csv", type=str, default="./data/warehouse/tournament_results.csv", help="CSV output path")
    parser.add_argument("--apply-best", action="store_true", help="Apply the champion archetype parameters to src/config.py")
    args = parser.parse_args()

    seed_pool = [42, 101, 7, 2024, 99, 13, 77, 888, 555, 333]
    active_seeds = seed_pool[: max(1, args.seeds)]

    print("=" * 80)
    print(" FANTASY CHAMPION - QUANTITATIVE TOURNAMENT RUNNER")
    print("=" * 80)
    print(f" Archetypes ({len(args.archetypes)}): {', '.join(args.archetypes)}")
    print(f" Scenarios ({len(args.scenarios)}): {', '.join(args.scenarios)}")
    print(f" Seeds ({len(active_seeds)}): {active_seeds}")
    print(f" Parallel Cores: {args.cores}")
    print(f" Total Simulation Runs: {len(args.archetypes) * len(args.scenarios) * len(active_seeds)}")
    print("=" * 80)

    runner = ScenarioTournamentRunner()
    df_raw = runner.run_tournament(
        archetype_ids=args.archetypes,
        scenario_ids=args.scenarios,
        seeds=active_seeds,
        max_workers=args.cores,
    )

    # Save detailed runs
    os.makedirs(os.path.dirname(args.output_csv), exist_ok=True)
    df_raw.to_csv(args.output_csv, index=False)
    print(f"\nSaved raw simulation runs to {args.output_csv}")

    # Compute aggregate leaderboard
    lb = runner.aggregate_leaderboard(df_raw)

    print("\n" + "=" * 90)
    print(" TOURNAMENT LEADERBOARD (AGGREGATE ACROSS SEEDS & SCENARIOS)")
    print("=" * 90)

    table_data = []
    for rank, (_, row) in enumerate(lb.iterrows(), 1):
        table_data.append([
            f"#{rank}",
            row["archetype_id"],
            f"{row['avg_points_j10']:.1f} pts",
            f"±{row['std_points_j10']:.1f}",
            f"{row['avg_rank']:.2f}",
            f"{row['win_rate_pct']:.1f}%",
            f"{row['podium_rate_pct']:.1f}%",
            f"{int(row['avg_net_worth']):,} €",
            f"{row['avg_sharpe']:.2f}",
            int(row['total_insolvent_penalties']),
        ])

    headers = [
        "Pos",
        "Arquetipo",
        "Pts Medios (J1-10)",
        "Volatilidad",
        "Puesto Medio",
        "Win Rate",
        "Podio Rate",
        "Patrimonio Medio",
        "Sharpe",
        "Insolvencias",
    ]
    print(tabulate(table_data, headers=headers, tablefmt="github"))

    best_arch_id = str(lb.iloc[0]["archetype_id"])
    best_arch = STRATEGY_ARCHETYPES[best_arch_id]

    print("\n" + "=" * 90)
    print(f" CAMPEÓN DEL TORNEO: {best_arch.name} ({best_arch_id})")
    print(f" Descripción: {best_arch.description}")
    print(f" Puntos Medios: {lb.iloc[0]['avg_points_j10']:.1f} pts | Puesto Medio: {lb.iloc[0]['avg_rank']:.2f} | Win Rate: {lb.iloc[0]['win_rate_pct']:.1f}% | Podio: {lb.iloc[0]['podium_rate_pct']:.1f}%")
    print(f" Patrimonio Final Medio: {int(lb.iloc[0]['avg_net_worth']):,} € | Penalizaciones de Insolvencia: {int(lb.iloc[0]['total_insolvent_penalties'])}")
    print("=" * 90)

    if args.apply_best:
        apply_champion_to_config(best_arch)
    else:
        print("\nPara aplicar automáticamente este arquetipo como configuración activa del bot:")
        print("  PYTHONPATH=. .venv/bin/python test/07_strategy_backtest/run_tournament.py --apply-best")


if __name__ == "__main__":
    main()
