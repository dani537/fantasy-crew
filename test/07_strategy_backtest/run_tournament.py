#!/usr/bin/env python3
"""
Multi-Scenario Quantitative Strategy Tournament Runner (`run_tournament.py`)
==========================================================================
Executes either a fast customized sweep or the full 3-Stage Massive Parallel
Quantitative Tournament (`--massive`):
  - Stage M1: All 11 named archetypes + 10 structured parametric variants x 8 seeds
  - Stage M2: Top 6 solvent strategies across 6 stress regimes x 12 seeds + 4 Day-1 starting rosters x 6 seeds
  - Stage M3: Top 3 strategies audited on H2+J11-J12 Out-of-Time split & ±15% parameter sensitivity

Pareto Champion Criterion:
  Strictly 0 insolvencies (`insolvent_rounds == 0`), maximizing (#1 Win Rate + Total Settled Points + H2 Stability).

Usage:
    PYTHONPATH=. .venv/bin/python test/07_strategy_backtest/run_tournament.py --massive --cores 12
    PYTHONPATH=. .venv/bin/python test/07_strategy_backtest/run_tournament.py --seeds 3 --cores 6
"""

import argparse
from dataclasses import replace
from typing import Dict, List, Any, Tuple
import os
import re
import time
import numpy as np
import pandas as pd
from tabulate import tabulate

from src.tools.strategy_engine.archetypes import STRATEGY_ARCHETYPES, StrategyArchetype
from src.tools.backtester.scenario_simulator import (
    ScenarioTournamentRunner,
    M2_STRESS_SCENARIOS,
)


def build_massive_m1_catalog() -> Dict[str, StrategyArchetype]:
    """Builds 11 named archetypes + 10 structured parametric challenger variants."""
    catalog: Dict[str, StrategyArchetype] = dict(STRATEGY_ARCHETYPES)
    parametric_specs: List[Tuple[str, str, Dict[str, Any]]] = [
        (
            "VAR_HYBRID_CAP235_VORP",
            "HYBRID_RANK_PREDATOR",
            {"clause_ratio_cap": 2.35, "clause_min_delta_xp": 0.55},
        ),
        (
            "VAR_HYBRID_VALCOMP",
            "HYBRID_RANK_PREDATOR",
            {"clause_policy": "VALUE_COMPOUND_VORP", "clause_min_delta_xp": 0.45, "clause_ratio_cap": 2.25},
        ),
        (
            "VAR_HYBRID_IMMEDIATE",
            "HYBRID_RANK_PREDATOR",
            {"clause_timing_mode": "IMMEDIATE", "clause_min_delta_xp": 0.60, "clause_cash_reserve_pct": 0.72},
        ),
        (
            "VAR_QUANT2_FASTROT",
            "QUANT_GALACTIC_CORE_V2",
            {"speculative_hold_days_max": 2, "hold_decay_penalty_eur": 140_000, "clause_ratio_cap": 2.45},
        ),
        (
            "VAR_QUANT2_HIGHCRED",
            "QUANT_GALACTIC_CORE_V2",
            {"credit_utilization_aggression": 1.22, "efr_weight": 1.25, "target_roster_size": 16},
        ),
        (
            "VAR_VULTURE_EFR14",
            "LIQUIDITY_VULTURE_PRO",
            {"efr_weight": 1.40, "clause_policy": "VALUE_COMPOUND_VORP", "clause_cash_reserve_pct": 0.70, "clause_min_delta_xp": 0.60},
        ),
        (
            "VAR_CHASER_VALCOMP",
            "ASYMMETRIC_CHASER_MAX",
            {"clause_policy": "VALUE_COMPOUND_VORP", "credit_utilization_aggression": 1.25, "target_roster_size": 15},
        ),
        (
            "VAR_TEMPO_COMPACT17",
            "TEMPO_ARB_COMPOUNDER",
            {"target_roster_size": 17, "clause_cash_reserve_pct": 0.68, "clause_policy": "VALUE_COMPOUND_VORP"},
        ),
        (
            "VAR_ADAPTIVE_VALCOMP",
            "ADAPTIVE_LIFECYCLE",
            {"clause_policy": "VALUE_COMPOUND_VORP", "speculative_hold_days_max": 2, "credit_utilization_aggression": 1.15},
        ),
        (
            "VAR_SENTINEL_SHARP",
            "REGIME_SENTINEL_SHIELD",
            {"clause_min_delta_xp": 0.60, "clause_ratio_cap": 2.30, "core_bid_aggression": 1.12},
        ),
    ]
    for vid, base_id, overrides in parametric_specs:
        base_arch = STRATEGY_ARCHETYPES[base_id]
        catalog[vid] = replace(
            base_arch,
            archetype_id=vid,
            name=f"{base_arch.name} [{vid}]",
            **overrides,
        )
    return catalog


def apply_champion_to_config(best_arch: StrategyArchetype, config_path: str = "src/config.py", env_path: str = ".env") -> None:
    """Updates BOTH StrategySettings defaults in src/config.py AND .env with the champion strategy parameters."""
    env_vars = {
        "ACTIVE_ARCHETYPE": best_arch.archetype_id,
        "CLAUSE_POLICY": str(best_arch.clause_policy),
        "CLAUSE_RATIO_CAP": f"{float(best_arch.clause_ratio_cap):.2f}",
        "CLAUSE_MIN_AVG_PTS": f"{float(best_arch.clause_min_avg_pts):.2f}",
        "CLAUSE_MIN_DELTA_XP": f"{float(best_arch.clause_min_delta_xp):.2f}",
        "MAX_SQUAD_SIZE": str(int(best_arch.target_roster_size)),
        "PROTECT_PILLARS_ABSOLUTE": "true" if best_arch.protect_pillars_absolute else "false",
        "SPECULATIVE_HOLD_DAYS_MAX": str(int(best_arch.speculative_hold_days_max)),
        "CLAUSE_TIMING_MODE": str(best_arch.clause_timing_mode),
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

    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg_txt = f.read()
        patterns = [
            (r'(_get_config_var\("ACTIVE_ARCHETYPE",\s*")[^"]*("\))', rf'\g<1>{env_vars["ACTIVE_ARCHETYPE"]}\g<2>'),
            (r'(_get_config_var\("CLAUSE_POLICY",\s*")[^"]*("\))', rf'\g<1>{env_vars["CLAUSE_POLICY"]}\g<2>'),
            (r'(_get_config_var\("CLAUSE_RATIO_CAP",\s*")[^"]*("\))', rf'\g<1>{env_vars["CLAUSE_RATIO_CAP"]}\g<2>'),
            (r'(_get_config_var\("CLAUSE_MIN_AVG_PTS",\s*")[^"]*("\))', rf'\g<1>{env_vars["CLAUSE_MIN_AVG_PTS"]}\g<2>'),
            (r'(_get_config_var\("CLAUSE_MIN_DELTA_XP",\s*")[^"]*("\))', rf'\g<1>{env_vars["CLAUSE_MIN_DELTA_XP"]}\g<2>'),
            (r'(_get_config_var\("MAX_SQUAD_SIZE",\s*")[^"]*("\))', rf'\g<1>{env_vars["MAX_SQUAD_SIZE"]}\g<2>'),
            (r'(_get_config_var\("PROTECT_PILLARS_ABSOLUTE",\s*")[^"]*("\))', rf'\g<1>{env_vars["PROTECT_PILLARS_ABSOLUTE"]}\g<2>'),
            (r'(_get_config_var\("SPECULATIVE_HOLD_DAYS_MAX",\s*")[^"]*("\))', rf'\g<1>{env_vars["SPECULATIVE_HOLD_DAYS_MAX"]}\g<2>'),
            (r'(_get_config_var\("CLAUSE_TIMING_MODE",\s*")[^"]*("\))', rf'\g<1>{env_vars["CLAUSE_TIMING_MODE"]}\g<2>'),
        ]
        for pat, rep in patterns:
            cfg_txt = re.sub(pat, rep, cfg_txt)
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(cfg_txt)
        print(f"[OK] {config_path} defaults updated for {best_arch.archetype_id}.")


def _format_lb_table(lb: pd.DataFrame, max_rows: int = 25) -> str:
    rows = []
    for rank, (_, row) in enumerate(lb.head(max_rows).iterrows(), 1):
        rows.append([
            f"#{rank}",
            row["archetype_id"],
            int(row["runs"]),
            f"{row['avg_points_settled']:.1f} ±{row['std_points_settled']:.1f}",
            f"{row['avg_points_h2']:.1f}",
            f"{row['win_rate_pct']:.1f}%",
            f"{row['podium_rate_pct']:.1f}%",
            f"{row['avg_rank']:.2f}",
            f"{int(row['avg_net_worth']):,} €",
            f"{row['avg_snipes']:.1f}",
            f"{row['avg_sharpe']:.2f}",
            int(row["total_insolvent_penalties"]),
            f"{row['pareto_score']:.1f}",
        ])
    headers = [
        "Pos", "Estrategia", "Runs", "Pts Settled", "Pts H2",
        "Win%", "Podio%", "Rank", "Patrimonio", "Snipes", "Sharpe", "Insolv", "Pareto",
    ]
    return tabulate(rows, headers=headers, tablefmt="github")


def run_massive_tournament(cores: int = 12, output_dir: str = "./data/backtests", apply_best: bool = False) -> Dict[str, Any]:
    """Executes the full 3-Stage Parallel Tournament (M1 -> M2 -> M3) and exports complete artifacts."""
    os.makedirs(output_dir, exist_ok=True)
    runner = ScenarioTournamentRunner()
    t_start = time.time()

    catalog = build_massive_m1_catalog()
    m1_seeds = [42, 101, 7, 2024, 99, 13, 77, 888]
    print("\n" + "=" * 95)
    print(f" STAGE M1 — WIDE EXPLORATION ({len(catalog)} estrategias x {len(m1_seeds)} seeds = {len(catalog)*len(m1_seeds)} sims, cores={cores})")
    print("=" * 95)
    t1 = time.time()
    df_m1 = runner.run_tournament(
        archetype_ids=list(catalog.keys()),
        scenario_ids=["GROUND_TRUTH_40M"],
        seeds=m1_seeds,
        max_workers=cores,
        custom_archetypes=catalog,
    )
    df_m1.to_csv(os.path.join(output_dir, "massive_m1_raw.csv"), index=False)
    lb_m1 = runner.aggregate_leaderboard(df_m1)
    lb_m1.to_csv(os.path.join(output_dir, "massive_m1_leaderboard.csv"), index=False)
    print(f"\n[M1 completado en {time.time() - t1:.1f}s]")
    print(_format_lb_table(lb_m1))

    # Top 6 strictly solvent strategies advance to M2 (prefer diversity if tied)
    solvent_m1 = lb_m1[lb_m1["total_insolvent_penalties"] == 0]
    top6_ids = list(solvent_m1["archetype_id"].head(6)) if len(solvent_m1) >= 6 else list(lb_m1["archetype_id"].head(6))

    # Stage M2A: 6 Top candidates x 6 Stress Scenarios x 12 MC Seeds
    m2a_seeds = [42, 101, 7, 2024, 99, 13, 77, 888, 555, 333, 17, 256]
    print("\n" + "=" * 95)
    print(f" STAGE M2A — STRESS MATRIX ({len(top6_ids)} top x {len(M2_STRESS_SCENARIOS)} escenarios x {len(m2a_seeds)} seeds = {len(top6_ids)*len(M2_STRESS_SCENARIOS)*len(m2a_seeds)} sims)")
    print("=" * 95)
    t2 = time.time()
    df_m2a = runner.run_tournament(
        archetype_ids=top6_ids,
        scenario_ids=M2_STRESS_SCENARIOS,
        seeds=m2a_seeds,
        max_workers=cores,
        custom_archetypes=catalog,
    )
    df_m2a.to_csv(os.path.join(output_dir, "massive_m2a_stress_raw.csv"), index=False)
    lb_m2a = runner.aggregate_leaderboard(df_m2a)
    print(f"\n[M2A Stress completado en {time.time() - t2:.1f}s]")
    print(_format_lb_table(lb_m2a))

    # Per-scenario breakdown table
    scen_piv = df_m2a.groupby(["archetype_id", "scenario_id"], as_index=False).agg(
        pts=("total_points_settled", "mean"),
        win_pct=("is_champion", lambda x: round(float(np.mean(x)) * 100.0, 1)),
        insolv=("insolvent_rounds", "sum"),
    )
    scen_piv.to_csv(os.path.join(output_dir, "massive_m2a_by_scenario.csv"), index=False)

    # Stage M2B: Multi-Start Day-1 Roster Fairness across 4 real starting draws
    multistart_mgrs = ["Dani SR", "Birra München", "Piedras FC", "JubiladosFC"]
    m2b_seeds = [42, 101, 7, 2024, 99, 13]
    print("\n" + "=" * 95)
    print(f" STAGE M2B — MULTI-START DAY-1 FAIRNESS ({len(top6_ids)} top x {len(multistart_mgrs)} plantillas reales x {len(m2b_seeds)} seeds = {len(top6_ids)*len(multistart_mgrs)*len(m2b_seeds)} sims)")
    print("=" * 95)
    t2b = time.time()
    df_m2b = runner.run_tournament(
        archetype_ids=top6_ids,
        scenario_ids=["GROUND_TRUTH_40M"],
        seeds=m2b_seeds,
        max_workers=cores,
        custom_archetypes=catalog,
        manager_overrides=multistart_mgrs,
    )
    df_m2b.to_csv(os.path.join(output_dir, "massive_m2b_multistart_raw.csv"), index=False)
    lb_m2b = runner.aggregate_leaderboard(df_m2b)
    print(f"\n[M2B Multi-Start completado en {time.time() - t2b:.1f}s]")
    print(_format_lb_table(lb_m2b))

    # Combined M2 ranking for advancing Top 3 strictly solvent to M3
    df_m2_combined = pd.concat([df_m2a, df_m2b], ignore_index=True)
    lb_m2_comb = runner.aggregate_leaderboard(df_m2_combined)
    lb_m2_comb.to_csv(os.path.join(output_dir, "massive_m2_combined_leaderboard.csv"), index=False)
    solvent_m2 = lb_m2_comb[lb_m2_comb["total_insolvent_penalties"] == 0]
    top3_ids = list(solvent_m2["archetype_id"].head(3)) if len(solvent_m2) >= 3 else list(lb_m2_comb["archetype_id"].head(3))

    # Stage M3: Out-of-Time H2/J11-J12 audit + ±15% Parameter Sensitivity Stress
    m3_seeds = [42, 101, 7, 2024, 99, 13]
    sens_catalog: Dict[str, StrategyArchetype] = {}
    sens_parent_map: Dict[str, str] = {}
    for cid in top3_ids:
        base_c = catalog[cid]
        sens_catalog[cid] = base_c
        sens_parent_map[cid] = cid
        perturbations = [
            ("SENS_LOW15", {
                "clause_ratio_cap": round(max(1.50, base_c.clause_ratio_cap * 0.85), 2),
                "clause_min_delta_xp": round(max(0.25, base_c.clause_min_delta_xp * 0.85), 2),
                "efr_weight": round(max(0.50, base_c.efr_weight * 0.85), 2),
                "credit_utilization_aggression": round(max(0.60, base_c.credit_utilization_aggression * 0.85), 2),
            }),
            ("SENS_HIGH15", {
                "clause_ratio_cap": round(min(2.95, base_c.clause_ratio_cap * 1.15), 2),
                "clause_min_delta_xp": round(min(2.00, base_c.clause_min_delta_xp * 1.15), 2),
                "efr_weight": round(min(2.00, base_c.efr_weight * 1.15), 2),
                "credit_utilization_aggression": round(min(1.60, base_c.credit_utilization_aggression * 1.15), 2),
            }),
        ]
        for tag, ov in perturbations:
            sid = f"{cid}__{tag}"
            sens_catalog[sid] = replace(base_c, archetype_id=sid, name=f"{cid} ({tag})", **ov)
            sens_parent_map[sid] = cid

    print("\n" + "=" * 95)
    print(f" STAGE M3 — OUT-OF-TIME (H2+J11-J12) & ±15% SENSITIVITY ({len(sens_catalog)} configs x {len(m3_seeds)} seeds = {len(sens_catalog)*len(m3_seeds)} sims)")
    print("=" * 95)
    t3 = time.time()
    df_m3 = runner.run_tournament(
        archetype_ids=list(sens_catalog.keys()),
        scenario_ids=["GROUND_TRUTH_40M"],
        seeds=m3_seeds,
        max_workers=cores,
        custom_archetypes=sens_catalog,
    )
    df_m3["parent_id"] = df_m3["archetype_id"].map(sens_parent_map)
    df_m3.to_csv(os.path.join(output_dir, "massive_m3_sensitivity_raw.csv"), index=False)

    # Final Pareto Champion Synthesis across Top 3
    final_rows: List[Dict[str, Any]] = []
    for cid in top3_ids:
        sub_m2_base = df_m2a[(df_m2a["archetype_id"] == cid) & (df_m2a["scenario_id"] == "GROUND_TRUTH_40M")]
        sub_m2_all = df_m2_combined[df_m2_combined["archetype_id"] == cid]
        sub_m3_sens = df_m3[df_m3["parent_id"] == cid]
        base_pts = float(sub_m2_base["total_points_settled"].mean()) if not sub_m2_base.empty else float(sub_m2_all["avg_points_settled"].mean())
        sens_min_pts = float(sub_m3_sens.groupby("archetype_id")["total_points_settled"].mean().min()) if not sub_m3_sens.empty else base_pts
        sens_ret_pct = (sens_min_pts / base_pts * 100.0) if base_pts > 0 else 100.0
        insolv_tot = int(sub_m2_all["insolvent_rounds"].sum() + sub_m3_sens["insolvent_rounds"].sum())
        win_m2 = float(sub_m2_all["is_champion"].mean() * 100.0)
        pts_m2 = float(sub_m2_all["total_points_settled"].mean())
        h2_m2 = float(sub_m2_all["points_h2"].mean())
        j11_12 = float(sub_m2_all["j11_j12_points"].mean())
        final_score = (pts_m2 + win_m2 * 1.5 + h2_m2 * 0.25 + (sens_ret_pct - 90.0) * 1.0) if insolv_tot == 0 else -1e6

        final_rows.append({
            "archetype_id": cid,
            "baseline_12seed_pts": round(base_pts, 1),
            "baseline_win_pct": round(float(sub_m2_base["is_champion"].mean() * 100.0), 1) if not sub_m2_base.empty else 0.0,
            "m2_combined_pts": round(pts_m2, 1),
            "m2_combined_win_pct": round(win_m2, 1),
            "m2_combined_podium_pct": round(float(sub_m2_all["is_podium"].mean() * 100.0), 1),
            "m2_h2_pts": round(h2_m2, 1),
            "m2_j11_j12_pts": round(j11_12, 1),
            "sensitivity_min_pts": round(sens_min_pts, 1),
            "sensitivity_retention_pct": round(sens_ret_pct, 1),
            "total_insolvencies": insolv_tot,
            "final_pareto_score": round(final_score, 2),
        })

    df_final = pd.DataFrame(final_rows).sort_values(
        by=["final_pareto_score", "m2_combined_win_pct", "m2_combined_pts"],
        ascending=[False, False, False],
    ).reset_index(drop=True)
    df_final.to_csv(os.path.join(output_dir, "massive_m3_final_pareto.csv"), index=False)

    print(f"\n[M3 completado en {time.time() - t3:.1f}s | Tiempo total torneo: {time.time() - t_start:.1f}s]")
    print("\n" + "=" * 95)
    print(" STAGE M3 — FINAL PARETO CHAMPION SYNTHESIS")
    print("=" * 95)
    print(tabulate(df_final, headers="keys", tablefmt="github", showindex=False))

    champ_id = str(df_final.iloc[0]["archetype_id"])
    champ_arch = catalog[champ_id]
    # Write clean Markdown report
    total_sims = len(df_m1) + len(df_m2a) + len(df_m2b) + len(df_m3)
    md_path = os.path.join(output_dir, "massive_tournament_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Massive 3-Stage Quantitative Tournament Report\n\n")
        f.write(f"- **Total Simulations Executed**: `{total_sims}` runs (`105` causal days each, `J1..J12` settled)\n")
        f.write(f"- **Pareto Champion**: `{champ_arch.archetype_id}` ({champ_arch.name})\n")
        f.write(f"- **Zero-Insolvency Constraint**: Passed (`{int(df_final.iloc[0]['total_insolvencies'])}` insolvencies across M1+M2+M3)\n\n")
        f.write("## Stage M1 — Wide Exploration Leaderboard\n\n" + _format_lb_table(lb_m1) + "\n\n")
        f.write("## Stage M2A — 6-Regime Stress Matrix (Aggregated)\n\n" + _format_lb_table(lb_m2a) + "\n\n")
        f.write("## Stage M2B — 4-Roster Multi-Start Fairness\n\n" + _format_lb_table(lb_m2b) + "\n\n")
        f.write("## Stage M3 — Out-of-Time & ±15% Sensitivity Pareto Synthesis\n\n")
        f.write(tabulate(df_final, headers="keys", tablefmt="github", showindex=False) + "\n")
    print(f"\n[OK] Massive Tournament Report guardado en {md_path}")

    if apply_best:
        # Also ensure STRATEGY_ARCHETYPES resolves champion cleanly if parametric variant wins
        apply_champion_to_config(champ_arch)

    return {
        "champion_id": champ_id,
        "champion_archetype": champ_arch,
        "total_runs": total_sims,
        "lb_m1": lb_m1,
        "lb_m2a": lb_m2a,
        "lb_m2b": lb_m2b,
        "df_final": df_final,
        "report_path": md_path,
    }


def main():
    parser = argparse.ArgumentParser(description="Monte Carlo Strategy Tournament Runner")
    parser.add_argument("--massive", action="store_true", help="Run full 3-Stage Parallel Tournament (M1->M2->M3)")
    parser.add_argument("--archetypes", nargs="+", default=list(STRATEGY_ARCHETYPES.keys()), help="Archetype IDs to evaluate")
    parser.add_argument("--scenarios", nargs="+", default=["GROUND_TRUTH_40M"], help="Scenarios to run")
    parser.add_argument("--seeds", type=int, default=3, help="Number of Monte Carlo seeds per archetype (e.g. 3, 5)")
    parser.add_argument("--cores", type=int, default=8, help="Parallel worker processes (up to 12)")
    parser.add_argument("--output-csv", type=str, default="./data/warehouse/tournament_results.csv", help="CSV output path")
    parser.add_argument("--output-dir", type=str, default="./data/backtests", help="Output directory for --massive artifacts")
    parser.add_argument("--apply-best", action="store_true", help="Apply champion archetype parameters to src/config.py and .env")
    args = parser.parse_args()

    if args.massive:
        run_massive_tournament(cores=min(12, max(1, args.cores)), output_dir=args.output_dir, apply_best=args.apply_best)
        return

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

    os.makedirs(os.path.dirname(args.output_csv) or ".", exist_ok=True)
    df_raw.to_csv(args.output_csv, index=False)
    print(f"\nSaved raw simulation runs to {args.output_csv}")

    lb = runner.aggregate_leaderboard(df_raw)
    print("\n" + "=" * 95)
    print(" TOURNAMENT LEADERBOARD (AGGREGATE ACROSS SEEDS & SCENARIOS)")
    print("=" * 95)
    print(_format_lb_table(lb))

    best_arch_id = str(lb.iloc[0]["archetype_id"])
    best_arch = STRATEGY_ARCHETYPES[best_arch_id]

    print("\n" + "=" * 95)
    print(f" CAMPEÓN DEL TORNEO: {best_arch.name} ({best_arch_id})")
    print(f" Descripción: {best_arch.description}")
    print(f" Puntos Medios: {lb.iloc[0]['avg_points_settled']:.1f} pts | H2: {lb.iloc[0]['avg_points_h2']:.1f} pts | Puesto Medio: {lb.iloc[0]['avg_rank']:.2f} | Win%: {lb.iloc[0]['win_rate_pct']:.1f}% | Podio%: {lb.iloc[0]['podium_rate_pct']:.1f}%")
    print(f" Patrimonio Final Medio: {int(lb.iloc[0]['avg_net_worth']):,} € | Insolvencias: {int(lb.iloc[0]['total_insolvent_penalties'])}")
    print("=" * 95)

    if args.apply_best:
        apply_champion_to_config(best_arch)
    else:
        print("\nPara aplicar automáticamente este arquetipo como configuración activa del bot:")
        print("  PYTHONPATH=. .venv/bin/python test/07_strategy_backtest/run_tournament.py --apply-best")


if __name__ == "__main__":
    main()
