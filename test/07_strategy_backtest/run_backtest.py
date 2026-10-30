#!/usr/bin/env python3
"""
CLI Runner for the 91-Day Strict Causal Backtest (`2026-08-01` -> `2026-10-30`).
Usage:
    .venv/bin/python test/07_strategy_backtest/run_backtest.py --monte-carlo-seeds 20
"""

import os
import sys

# Prevent OpenMP / BLAS thread oversubscription and spinlock deadlocks on Linux
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import time
import argparse

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.tools.backtester.benchmark_suite import BacktestBenchmarkSuite


def main() -> None:
    parser = argparse.ArgumentParser(description="Run 91-Day Causal Backtest on AZ Finance")
    parser.add_argument("--db-path", default="./data/warehouse/biwenger_history.db", help="Path to SQLite warehouse")
    parser.add_argument("--output-dir", default="./data/backtest", help="Output directory for reports")
    parser.add_argument("--monte-carlo-seeds", type=int, default=20, help="Number of Monte Carlo market seeds")
    args = parser.parse_args()

    t0 = time.time()
    print("🚀 Iniciando Backtesting Causal de 91 Días (2026-08-01 -> 2026-10-30) contra AZ Finance...")
    suite = BacktestBenchmarkSuite(db_path=args.db_path, output_dir=args.output_dir)
    res = suite.run_full_benchmark(monte_carlo_seeds=args.monte_carlo_seeds)
    elapsed = time.time() - t0

    print(f"\n✅ Backtest completado en {elapsed:.1f}s (0 tokens LLM consumidos).")
    print("\n=== ESTUDIO DE ABLACIÓN ===")
    cols = [
        "strategy_id",
        "points_mean",
        "points_std",
        "simulated_rank",
        "net_worth_mean_eur",
        "trading_alpha_eur",
        "lineup_efficiency_pct",
        "insolvent_rounds",
        "win_league_prob_pct",
        "podium_top3_prob_pct",
    ]
    print(res["ablation_df"][cols].to_string(index=False))

    print("\n=== TOP 10 CLASIFICACIÓN GENERAL SIMULADA (J1-J10) ===")
    st_cols = ["rank", "manager", "points_j1_j10", "net_worth_eur", "trading_alpha_eur", "lineup_efficiency_pct"]
    print(res["standings_df"][st_cols].head(12).to_string(index=False))
    print(f"\n📄 Informe Markdown: {res['report_md_path']}")
    print(f"📊 Auditoría Excel:  {res['excel_path']}")


if __name__ == "__main__":
    main()
