#!/usr/bin/env python3
"""
CLI Runner for the 94-Day Strict Causal & Honest Rules Backtest (`2026-08-01` -> `2026-11-02`).
Usage:
    .venv/bin/python test/07_strategy_backtest/run_backtest.py --monte-carlo-seeds 20 --iteration-tag iter1_baseline_v3
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
    parser = argparse.ArgumentParser(description="Run 94-Day Honest Causal Backtest")
    parser.add_argument("--db-path", default="./data/warehouse/biwenger_history.db", help="Path to SQLite warehouse")
    parser.add_argument("--output-dir", default="./data/backtest", help="Output directory for reports")
    parser.add_argument("--target-manager", default=None, help="Optional manager override (auto-detected by default)")
    parser.add_argument("--monte-carlo-seeds", type=int, default=20, help="Number of Monte Carlo market seeds")
    parser.add_argument("--iteration-tag", default="v3_final", help="Iteration tag recorded in iterations_comparison.csv")
    args = parser.parse_args()

    t0 = time.time()
    suite = BacktestBenchmarkSuite(
        db_path=args.db_path,
        output_dir=args.output_dir,
        target_manager=args.target_manager,
    )
    print(
        f"🚀 Iniciando Backtesting Causal Honesto de 94 Días [{args.iteration_tag}] "
        f"para '{suite.target_manager}' en '{suite.league_name}'..."
    )
    res = suite.run_full_benchmark(
        monte_carlo_seeds=args.monte_carlo_seeds,
        iteration_tag=args.iteration_tag,
    )
    elapsed = time.time() - t0

    print(f"\n✅ Backtest [{args.iteration_tag}] completado en {elapsed:.1f}s (0 tokens LLM consumidos).")
    print("\n=== ESTUDIO DE ABLACIÓN HONESTO (REGLAS REALES: <=5 VENTAS, BLOQUEO 48H CLÁUSULAS) ===")
    cols = [
        "strategy_id",
        "points_mean",
        "points_std",
        "points_h1_j1_j5",
        "points_h2_j6_j10",
        "j11_partial_points",
        "simulated_rank",
        "net_worth_mean_eur",
        "trading_alpha_eur",
        "lineup_efficiency_pct",
        "insolvent_rounds",
        "win_league_prob_pct",
        "podium_top3_prob_pct",
    ]
    print(res["ablation_df"][cols].to_string(index=False))

    print("\n=== VALIDACIÓN OUT-OF-TIME POR RÉGIMEN DE ESCASEZ (H1 ABUNDANCIA vs H2 ESCASEZ) ===")
    reg_cols = [
        "strategy_id",
        "h1_pts_per_round",
        "h2_pts_per_round",
        "j11_scarcity_live_pts",
        "h2_vs_real_delta_pts",
        "h1_avg_free_players_ge_5m",
        "h2_avg_free_players_ge_5m",
    ]
    print(res["regime_split_df"][reg_cols].to_string(index=False))

    print("\n=== TOP 12 CLASIFICACIÓN GENERAL SIMULADA (J1-J10 + J11 EN VIVO) ===")
    st_cols = [
        "rank",
        "manager",
        "points_j1_j10",
        "j11_partial_points",
        "points_incl_j11",
        "net_worth_eur",
        "trading_alpha_eur",
        "lineup_efficiency_pct",
    ]
    print(res["standings_df"][st_cols].head(12).to_string(index=False))
    print(f"\n📄 Informe Markdown: {res['report_md_path']}")
    print(f"📊 Auditoría Excel:  {res['excel_path']}")


if __name__ == "__main__":
    main()
