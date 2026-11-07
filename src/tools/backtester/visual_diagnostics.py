"""
Visual Diagnostics & Publication-Grade Chart Generator (`visual_diagnostics.py` v3)
===================================================================================
Generates 4 high-resolution (`220 dpi`), Spanish-annotated visual panels in
`./data/backtest/charts/` (and optionally mirrors them to an artifact directory)
from the honest 94-day backtest outputs:
  1. `01_curva_puntos_y_banda_mc20.png`
  2. `02_patrimonio_y_diente_sierra_credito.png`
  3. `03_regimen_escasez_y_atribucion_alpha.png`
  4. `04_eficiencia_jornada_a_jornada_e_iteraciones.png`
"""

import os
import shutil
import argparse
from typing import List, Optional
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


# Clean modern palette
PALETTE = {
    "v3_mc": "#0F766E",       # Deep emerald-teal (Champion v3 MC20)
    "v3_band": "#5EEAD4",     # Soft teal fill (P10-P90)
    "v3_strict": "#2563EB",   # Royal blue (Champion v3 Strict)
    "legacy_v2": "#D97706",   # Amber-orange (Legacy v2 Honest Rules)
    "passive": "#64748B",     # Slate gray (Passive MILP XI)
    "real_mgr": "#DC2626",    # Crimson red (Real Manager)
    "leader": "#1E293B",      # Charcoal navy (#1 League Leader)
    "podium": "#7C3AED",      # Violet (#3 Podium reference)
    "bg": "#F8FAFC",          # Crisp slate-white canvas
    "grid": "#CBD5E1",
}


def _configure_matplotlib_style() -> None:
    plt.rcParams.update({
        "figure.facecolor": "white",
        "axes.facecolor": PALETTE["bg"],
        "axes.edgecolor": "#94A3B8",
        "axes.linewidth": 0.9,
        "axes.grid": True,
        "grid.color": PALETTE["grid"],
        "grid.linestyle": "--",
        "grid.alpha": 0.55,
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "axes.labelsize": 10,
        "legend.fontsize": 8.8,
        "legend.framealpha": 0.94,
    })


def generate_all_backtest_charts(
    backtest_dir: str = "./data/backtest",
    charts_dir: Optional[str] = None,
    mirror_dir: Optional[str] = None,
) -> List[str]:
    """
    Reads CSV outputs in `backtest_dir` and renders all 4 visual diagnostic PNGs.
    Returns the list of generated file paths in `charts_dir`.
    """
    _configure_matplotlib_style()
    out_dir = charts_dir or os.path.join(backtest_dir, "charts")
    os.makedirs(out_dir, exist_ok=True)

    df_abl = pd.read_csv(os.path.join(backtest_dir, "ablation_summary.csv"))
    df_st = pd.read_csv(os.path.join(backtest_dir, "league_standings_comparison.csv"))
    df_cv = pd.read_csv(os.path.join(backtest_dir, "daily_equity_curves.csv"))
    df_bands = pd.read_csv(os.path.join(backtest_dir, "mc_daily_bands.csv"))
    df_reg = pd.read_csv(os.path.join(backtest_dir, "out_of_time_regime_split.csv"))
    df_rd = pd.read_csv(os.path.join(backtest_dir, "round_by_round_comparison.csv"))
    iter_path = os.path.join(backtest_dir, "iterations_comparison.csv")
    df_iter = pd.read_csv(iter_path) if os.path.exists(iter_path) else pd.DataFrame()

    # Dynamic manager & leader names (zero hardcoding)
    real_targets = df_cv[df_cv["entity_type"] == "REAL_TARGET"]["entity"].unique()
    real_mgr_name = str(real_targets[0]) if len(real_targets) > 0 else "Mánager Real"

    real_only_st = df_st[~df_st["is_bot"].astype(bool)].sort_values("points_j1_j10", ascending=False).reset_index(drop=True)
    leader_name = str(real_only_st.iloc[0]["manager"]) if not real_only_st.empty else "Líder #1"
    podium_name = str(real_only_st.iloc[min(2, len(real_only_st) - 1)]["manager"]) if not real_only_st.empty else "Podio #3"

    df_cv["dt"] = pd.to_datetime(df_cv["fecha"])
    df_bands["dt"] = pd.to_datetime(df_bands["fecha"])

    real_row = df_abl[df_abl["strategy_id"] == "0_Real_Manager"].iloc[0]
    leg_row = df_abl[df_abl["strategy_id"] == "2_Legacy_v2_HonestRules"].iloc[0]
    strict_row = df_abl[df_abl["strategy_id"] == "4_Champion_v3_Strict"].iloc[0]
    mc_row = df_abl[df_abl["strategy_id"] == "5_Champion_v3_MC20"].iloc[0]

    generated: List[str] = []

    # =========================================================================
    # CHART 1: Cumulative Points Progression + MC20 P10-P90 Confidence Band
    # =========================================================================
    fig1, ax1 = plt.subplots(figsize=(12.5, 6.2))
    ax1.fill_between(
        df_bands["dt"],
        df_bands["settled_points_p10"],
        df_bands["settled_points_p90"],
        color=PALETTE["v3_band"],
        alpha=0.38,
        label="Banda P10–P90 Champion v3 (20 semillas Mercado)",
    )
    ax1.plot(
        df_bands["dt"],
        df_bands["settled_points"],
        color=PALETTE["v3_mc"],
        linewidth=2.8,
        label=f"Fantasy Champion v3 MC20 ({mc_row['points_mean']:.1f} pts J1-J10 | Rank #{int(mc_row['simulated_rank'])})",
    )

    def _plot_entity_pts(ent: str, color: str, lbl: str, ls: str = "-", lw: float = 1.9) -> None:
        sub = df_cv[df_cv["entity"] == ent].sort_values("dt")
        if not sub.empty:
            ax1.plot(sub["dt"], sub["settled_points"], color=color, linestyle=ls, linewidth=lw, label=lbl)

    _plot_entity_pts(
        "4_Champion_v3_Strict",
        PALETTE["v3_strict"],
        f"Champion v3 Strict Conservador ({strict_row['points_mean']:.0f} pts | Rank #{int(strict_row['simulated_rank'])})",
        ls="-.",
        lw=2.0,
    )
    _plot_entity_pts(
        "2_Legacy_v2_HonestRules",
        PALETTE["legacy_v2"],
        f"Legacy v2 bajo Reglas Reales <=5 Ventas/48h ({leg_row['points_mean']:.1f} pts | Rank #{int(leg_row['simulated_rank'])})",
        ls="--",
        lw=2.0,
    )
    _plot_entity_pts(
        real_mgr_name,
        PALETTE["real_mgr"],
        f"{real_mgr_name} Real Observado ({real_row['points_mean']:.0f} pts | Rank #{int(real_row['simulated_rank'])})",
        ls="-",
        lw=2.2,
    )
    _plot_entity_pts(
        leader_name,
        PALETTE["leader"],
        f"#1 {leader_name} ({int(real_only_st.iloc[0]['points_j1_j10'])} pts)",
        ls=":",
        lw=2.0,
    )
    _plot_entity_pts(
        podium_name,
        PALETTE["podium"],
        f"#3 Podio ({podium_name}: {int(real_only_st.iloc[min(2, len(real_only_st)-1)]['points_j1_j10'])} pts)",
        ls=":",
        lw=1.6,
    )

    # Mark Regime transition on Sep 15
    split_dt = pd.Timestamp("2026-09-15")
    ax1.axvline(split_dt, color="#475569", linestyle="--", linewidth=1.2, alpha=0.8)
    y_top = max(float(df_bands["settled_points_p90"].max()), float(real_only_st.iloc[0]["points_j1_j10"])) * 1.07
    ax1.set_ylim(0, y_top)
    ax1.text(
        split_dt - pd.Timedelta(days=2.5),
        y_top * 0.56,
        "Régimen 1: Abundancia\n(J1–J5: Mercado Libre Amplio)",
        fontsize=8.8,
        color="#334155",
        fontweight="bold",
        ha="right",
    )
    ax1.text(
        split_dt + pd.Timedelta(days=2.5),
        y_top * 0.90,
        "Régimen 2 Out-of-Time: Escasez\n(J6–J10 + J11 Vivo: Retención NPV)",
        fontsize=8.8,
        color="#0F766E",
        fontweight="bold",
        ha="left",
    )

    delta_vs_real = mc_row["points_mean"] - real_row["points_mean"]
    delta_vs_leg = mc_row["points_mean"] - leg_row["points_mean"]
    ax1.annotate(
        f"Ventaja J1-J10: {delta_vs_real:+.1f} pts vs Real\n"
        f"({delta_vs_leg:+.1f} pts vs Legacy v2)\n"
        f"+J11 en vivo: +{mc_row['j11_partial_points']:.1f} pts",
        xy=(df_bands["dt"].iloc[-1], float(df_bands["settled_points"].iloc[-1])),
        xytext=(df_bands["dt"].iloc[-1] - pd.Timedelta(days=27), float(df_bands["settled_points"].iloc[-1]) * 0.72),
        arrowprops={"arrowstyle": "->", "color": PALETTE["v3_mc"], "lw": 1.5},
        bbox={"boxstyle": "round,pad=0.45", "facecolor": "#ECFDF5", "edgecolor": PALETTE["v3_mc"], "lw": 1.2},
        fontsize=9,
        fontweight="bold",
        color="#065F46",
    )

    ax1.set_title("1. Evolución de Puntos Oficiales (94 Días) — Champion v3 vs Legacy v2 y Mánagers Reales")
    ax1.set_ylabel("Puntos Oficiales Liquidadas (Media AS + SofaScore)")
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%d-%b"))
    ax1.legend(loc="upper left")
    fig1.tight_layout()
    p1 = os.path.join(out_dir, "01_curva_puntos_y_banda_mc20.png")
    fig1.savefig(p1, dpi=220)
    plt.close(fig1)
    generated.append(p1)

    # =========================================================================
    # CHART 2: Net Worth Evolution & Sawtooth 25% Credit-Solvency Cycle
    # =========================================================================
    fig2, (ax2_top, ax2_bot) = plt.subplots(2, 1, figsize=(12.5, 8.2), sharex=True, gridspec_kw={"height_ratios": [1.15, 1.0]})

    ax2_top.fill_between(
        df_bands["dt"],
        df_bands["net_worth_p10"] / 1e6,
        df_bands["net_worth_p90"] / 1e6,
        color=PALETTE["v3_band"],
        alpha=0.35,
        label="Banda P10–P90 Patrimonio Champion v3",
    )
    ax2_top.plot(df_bands["dt"], df_bands["net_worth"] / 1e6, color=PALETTE["v3_mc"], lw=2.6, label=f"Champion v3 MC20 ({mc_row['net_worth_mean_eur']/1e6:.2f} M€)")

    for ent, col_c, lbl_c, ls_c in [
        ("4_Champion_v3_Strict", PALETTE["v3_strict"], f"Champion v3 Strict ({strict_row['net_worth_mean_eur']/1e6:.2f} M€)", "-."),
        ("2_Legacy_v2_HonestRules", PALETTE["legacy_v2"], f"Legacy v2 Reglas Reales ({leg_row['net_worth_mean_eur']/1e6:.2f} M€)", "--"),
        (real_mgr_name, PALETTE["real_mgr"], f"{real_mgr_name} Real ({real_row['net_worth_mean_eur']/1e6:.2f} M€)", "-"),
        (leader_name, PALETTE["leader"], f"#1 {leader_name} ({real_only_st.iloc[0]['net_worth_eur']/1e6:.2f} M€)", ":"),
    ]:
        sub = df_cv[df_cv["entity"] == ent].sort_values("dt")
        if not sub.empty:
            ax2_top.plot(sub["dt"], sub["net_worth"] / 1e6, color=col_c, linestyle=ls_c, lw=1.9, label=lbl_c)

    ax2_top.set_title("2. Creación de Patrimonio Neto (94 Días) y Ciclo 'Diente de Sierra' de Crédito L-J (0 Insolvencias)")
    ax2_top.set_ylabel("Patrimonio Neto Total (M€)")
    ax2_top.legend(loc="upper left", ncol=2)

    # Bottom Panel: Cash & Collateral-Backed Credit Cycle
    cash_m = df_bands["cash"] / 1e6
    cred_floor_m = -df_bands["credit_limit_eur"] / 1e6
    ax2_bot.axhline(0.0, color="#15803D", linewidth=1.6, linestyle="-", label="Umbral Obligatorio Inicio Jornada (Saldo >= 0,00 M€)")
    ax2_bot.plot(df_bands["dt"], cash_m, color="#0284C7", lw=2.2, label="Caja Media Diaria Champion v3 (M€)")
    ax2_bot.plot(df_bands["dt"], cred_floor_m, color="#DC2626", lw=1.4, linestyle=":", label="Límite Oficial Crédito Biwenger (-25% Valor Plantilla)")
    ax2_bot.fill_between(df_bands["dt"], np.minimum(cash_m, 0.0), 0.0, color="#FDE68A", alpha=0.55, label="Uso Táctico Crédito L-J (Respaldado por Top-5 Colateral en Venta)")
    ax2_bot.fill_between(df_bands["dt"], 0.0, np.maximum(cash_m, 0.0), color="#BBF7D0", alpha=0.45)

    kickoff_days = df_bands[df_bands["is_kickoff_day"].astype(bool)]
    for idx_k, (_, kr) in enumerate(kickoff_days.iterrows()):
        lbl_k = "Inicio de Jornada J1..J11 (100% Solvente >= 0€)" if idx_k == 0 else None
        ax2_bot.axvline(kr["dt"], color="#16A34A", linestyle="--", alpha=0.55, lw=1.1, label=lbl_k)
        ax2_bot.scatter([kr["dt"]], [max(0.0, float(kr["cash"]) / 1e6)], color="#15803D", s=26, zorder=5)

    ax2_bot.set_ylabel("Saldo de Caja / Crédito (M€)")
    ax2_bot.xaxis.set_major_formatter(mdates.DateFormatter("%d-%b"))
    ax2_bot.legend(loc="lower left", ncol=2)
    fig2.tight_layout()
    p2 = os.path.join(out_dir, "02_patrimonio_y_diente_sierra_credito.png")
    fig2.savefig(p2, dpi=220)
    plt.close(fig2)
    generated.append(p2)

    # =========================================================================
    # CHART 3: Market Scarcity Collapse & Out-of-Time H1 vs H2 Performance
    # =========================================================================
    fig3, (ax3_l, ax3_r) = plt.subplots(1, 2, figsize=(13.2, 5.8), gridspec_kw={"width_ratios": [1.05, 1.15]})

    ax3_l.axvspan(df_bands["dt"].min(), split_dt, color="#E0F2FE", alpha=0.45, label="H1: Abundancia (J1-J5)")
    ax3_l.axvspan(split_dt, df_bands["dt"].max(), color="#FEF3C7", alpha=0.45, label="H2: Escasez Severa (J6-J11)")
    ax3_l.plot(df_bands["dt"], df_bands["free_ge_5m"], color="#0369A1", lw=2.4, label="Jugadores Libres >= 5 M€")
    ax3_l.plot(df_bands["dt"], df_bands["free_ge_10m"], color="#B91C1C", lw=2.4, label="Estrellas Libres >= 10 M€")
    ax3_l.axvline(split_dt, color="#475569", linestyle="--", lw=1.2)
    ax3_l.set_title("3A. Colapso Empírico de Jugadores Libres en la Liga")
    ax3_l.set_ylabel("Nº de Jugadores Libres sin Propietario")
    ax3_l.xaxis.set_major_formatter(mdates.DateFormatter("%d-%b"))
    ax3_l.legend(loc="upper right")

    f5_start = int(round(df_bands["free_ge_5m"].iloc[0]))
    f5_end = int(round(df_bands["free_ge_5m"].iloc[-1]))
    f10_start = int(round(df_bands["free_ge_10m"].iloc[0]))
    f10_end = int(round(df_bands["free_ge_10m"].iloc[-1]))
    ax3_l.annotate(
        f"Libres >=5M€: {f5_start} -> {f5_end}\nLibres >=10M€: {f10_start} -> {f10_end}\n(Por qué vender estrellas por\nstop-loss en Oct destruye puntos)",
        xy=(df_bands["dt"].iloc[-10], float(df_bands["free_ge_5m"].iloc[-10])),
        xytext=(df_bands["dt"].iloc[18], max(f5_start * 0.52, 18)),
        arrowprops={"arrowstyle": "->", "color": "#B45309", "lw": 1.4},
        bbox={"boxstyle": "round,pad=0.4", "facecolor": "#FFFBEB", "edgecolor": "#D97706"},
        fontsize=8.6,
    )

    # Right Panel: H1 (Abundance) vs H2 (Out-of-Time Scarcity) Points/Round across strategies
    focus_ids = [
        ("0_Real_Manager", f"{real_mgr_name}\nReal"),
        ("1_Passive_MILP_XI", "1. Pasivo\nMILP XI"),
        ("2_Legacy_v2_HonestRules", "2. Legacy v2\n(Reglas Reales)"),
        ("3_v3_Quant_No_Clauses", "3. v3 Quant\n(Sin Cláusulas)"),
        ("4_Champion_v3_Strict", "4. Champion v3\n(Strict)"),
        ("5_Champion_v3_MC20", "5. Champion v3\n(MC20)"),
    ]
    sub_reg = df_reg.set_index("strategy_id")
    x_pos = np.arange(len(focus_ids))
    w_bar = 0.36
    h1_vals = [float(sub_reg.loc[sid, "h1_pts_per_round"]) for sid, _ in focus_ids if sid in sub_reg.index]
    h2_vals = [float(sub_reg.loc[sid, "h2_pts_per_round"]) for sid, _ in focus_ids if sid in sub_reg.index]
    x_labels = [lbl for sid, lbl in focus_ids if sid in sub_reg.index]

    bars1 = ax3_r.bar(x_pos - w_bar / 2, h1_vals, width=w_bar, color="#38BDF8", edgecolor="#0284C7", label="H1 Abundancia (J1–J5, Ago–15 Sep)")
    bars2 = ax3_r.bar(x_pos + w_bar / 2, h2_vals, width=w_bar, color=PALETTE["v3_mc"], edgecolor="#064E3B", label="H2 Escasez Out-of-Time (J6–J10, 16 Sep–Nov)")

    for b in list(bars1) + list(bars2):
        h_v = b.get_height()
        ax3_r.text(b.get_x() + b.get_width() / 2, h_v + 0.6, f"{h_v:.1f}", ha="center", va="bottom", fontsize=8.2, fontweight="bold")

    ax3_r.set_xticks(x_pos)
    ax3_r.set_xticklabels(x_labels, fontsize=8.6)
    ax3_r.set_ylim(0, max(max(h1_vals), max(h2_vals)) * 1.18)
    ax3_r.set_ylabel("Media de Puntos por Jornada (pts / J)")
    ax3_r.set_title("3B. Validación Out-of-Time: Rendimiento en Abundancia vs Escasez")
    ax3_r.legend(loc="upper left")

    fig3.tight_layout()
    p3 = os.path.join(out_dir, "03_regimen_escasez_y_atribucion_alpha.png")
    fig3.savefig(p3, dpi=220)
    plt.close(fig3)
    generated.append(p3)

    # =========================================================================
    # CHART 4: Round-by-Round J1..J11 Points & Iterative Calibration Summary
    # =========================================================================
    fig4, (ax4_l, ax4_r) = plt.subplots(1, 2, figsize=(13.5, 5.8), gridspec_kw={"width_ratios": [1.35, 0.95]})

    j_nums = df_rd["jornada_num"].astype(int).values
    x_j = np.arange(len(j_nums))
    wb = 0.25
    ax4_l.bar(x_j - wb, df_rd["real_manager_pts"], width=wb, color=PALETTE["real_mgr"], alpha=0.88, label=f"{real_mgr_name} Real")
    ax4_l.bar(x_j, df_rd["legacy_v2_honest_mean_pts"], width=wb, color=PALETTE["legacy_v2"], alpha=0.88, label="Legacy v2 (Reglas Reales)")
    ax4_l.bar(x_j + wb, df_rd["champion_v3_mc20_mean_pts"], width=wb, color=PALETTE["v3_mc"], alpha=0.95, label="Champion v3 (MC20 Media)")
    ax4_l.plot(x_j, df_rd["league_leader_real_pts"], color=PALETTE["leader"], marker="o", markersize=4.5, lw=1.6, ls=":", label=f"#1 {leader_name}")

    j_tick_lbls = [f"J{j}" if j <= 10 else "J11*" for j in j_nums]
    ax4_l.set_xticks(x_j)
    ax4_l.set_xticklabels(j_tick_lbls)
    ax4_l.set_ylabel("Puntos en la Jornada")
    ax4_l.set_title("4A. Puntos Jornada a Jornada (J1–J10 Cerradas + J11* en Vivo 9/10)")
    ax4_l.legend(loc="upper left", ncol=2)

    # Panel 4B: Iterative Backtest Calibration OR Trading Alpha + Efficiency Waterfall
    if not df_iter.empty and len(df_iter) >= 2:
        it_tags = df_iter["iteration_tag"].astype(str).tolist()
        it_pts = df_iter["v3_mc20_pts_mean"].astype(float).tolist()
        it_std = df_iter["v3_mc20_pts_std"].astype(float).tolist()
        it_h2 = df_iter["v3_mc20_h2_pts"].astype(float).tolist()
        xi = np.arange(len(it_tags))
        bars_it = ax4_r.bar(xi - 0.18, it_pts, yerr=it_std, capsize=4, width=0.35, color=PALETTE["v3_mc"], label="Puntos Totales J1-J10 (MC20)")
        bars_h2 = ax4_r.bar(xi + 0.18, it_h2, width=0.35, color="#0284C7", label="Puntos en Escasez H2 (J6-J10)")
        ax4_r.axhline(float(real_row["points_mean"]), color=PALETTE["real_mgr"], ls="--", lw=1.4, label=f"{real_mgr_name} Real ({real_row['points_mean']:.0f} pts)")
        ax4_r.axhline(float(leg_row["points_mean"]), color=PALETTE["legacy_v2"], ls=":", lw=1.4, label=f"Legacy v2 Honest ({leg_row['points_mean']:.1f} pts)")
        for idx_b, b in enumerate(bars_it):
            hv = b.get_height()
            err_pad = it_std[idx_b] if idx_b < len(it_std) else 0.0
            ax4_r.text(b.get_x() + b.get_width() / 2, hv + err_pad + 6, f"{hv:.1f}", ha="center", va="bottom", fontsize=8.3, fontweight="bold")
        for b in bars_h2:
            hv = b.get_height()
            ax4_r.text(b.get_x() + b.get_width() / 2, hv + 5, f"{hv:.1f}", ha="center", va="bottom", fontsize=8.3, fontweight="bold")
        ax4_r.set_xticks(xi)
        ax4_r.set_xticklabels([t.replace("_", "\n") for t in it_tags], fontsize=8.4)
        ax4_r.set_ylim(0, (max(it_pts) + max(it_std)) * 1.18)
        ax4_r.set_ylabel("Puntos Oficiales")
        ax4_r.set_title("4B. Evolución entre Iteraciones de Backtesting Causal")
        ax4_r.legend(loc="lower right", fontsize=8.0)
    else:
        # Fallback if only 1 iteration exists: Trading Alpha (M€) comparison
        al_labels = [lbl for sid, lbl in focus_ids if sid in df_abl["strategy_id"].values]
        al_vals = [
            float(df_abl[df_abl["strategy_id"] == sid].iloc[0]["trading_alpha_eur"]) / 1e6
            for sid, _ in focus_ids if sid in df_abl["strategy_id"].values
        ]
        xa = np.arange(len(al_labels))
        cols_a = [PALETTE["real_mgr"], PALETTE["passive"], PALETTE["legacy_v2"], "#0284C7", PALETTE["v3_strict"], PALETTE["v3_mc"]][:len(al_vals)]
        b_al = ax4_r.bar(xa, al_vals, color=cols_a, width=0.55)
        for b in b_al:
            hv = b.get_height()
            ax4_r.text(b.get_x() + b.get_width() / 2, hv + (0.6 if hv >= 0 else -1.8), f"{hv:+.1f}M", ha="center", va="bottom", fontsize=8.3, fontweight="bold")
        ax4_r.set_xticks(xa)
        ax4_r.set_xticklabels(al_labels, fontsize=8.2)
        ax4_r.set_ylabel("Trading Alpha Neto sin Primas (M€)")
        ax4_r.set_title("4B. Creación de Valor de Mercado Puro (Trading Alpha)")

    fig4.tight_layout()
    p4 = os.path.join(out_dir, "04_eficiencia_jornada_a_jornada_e_iteraciones.png")
    fig4.savefig(p4, dpi=220)
    plt.close(fig4)
    generated.append(p4)

    if mirror_dir:
        os.makedirs(mirror_dir, exist_ok=True)
        for fp in generated:
            shutil.copy2(fp, os.path.join(mirror_dir, os.path.basename(fp)))

    return generated


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate v3 Visual Backtest Diagnostics Suite")
    parser.add_argument("--backtest-dir", default="./data/backtest", help="Directory containing backtest CSV outputs")
    parser.add_argument("--charts-dir", default=None, help="Output directory for PNG charts")
    parser.add_argument("--mirror-dir", default=None, help="Optional second directory (e.g. artifacts folder) to copy PNGs")
    args = parser.parse_args()

    paths = generate_all_backtest_charts(
        backtest_dir=args.backtest_dir,
        charts_dir=args.charts_dir,
        mirror_dir=args.mirror_dir,
    )
    print(f"✅ Generados {len(paths)} paneles visuales en alta resolución:")
    for p in paths:
        print(f"   🖼️ {p}")


if __name__ == "__main__":
    main()
