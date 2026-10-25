"""
Early Turning Point & Inflexion Detection Engine (v2)
======================================================
Calibrates and evaluates ML models for market turning points 24h ahead:
1. Giro de Techo (Exhaustion / Take-Profit): Rising today -> Falls/stops tomorrow
2. Giro de Suelo (Breakout / Sniper Entry): Flat/dropping today -> Rises tomorrow
"""

import os
import sys
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import precision_score, recall_score, f1_score

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from features import build_features
from targets import build_targets

def run_inflection_calibration():
    print("\n" + "="*85)
    print("🔬 EXPERIMENTO DE INFLEXIÓN (v2): CALIBRACIÓN DE GIROS DE TECHO Y SUELO")
    print("="*85)

    raw_df = pd.read_csv("data/history/market_sentiment_timeseries.csv")
    df_feat, groups = build_features(raw_df, max_lags=4)
    df = build_targets(df_feat)

    valid = df.dropna(subset=["subida_lag2", "target_subida_1d"]).copy()
    dates = sorted(valid["fecha"].unique())

    # Walk-forward split
    train_dates = dates[:-2]
    test_dates = dates[-2:]

    feature_cols = groups["set6_master_v4"]

    train_df = valid[valid["fecha"].isin(train_dates)].copy()
    test_df = valid[valid["fecha"].isin(test_dates)].copy()

    print(f"Entrenamiento: Fechas {[str(d)[:10] for d in train_dates]} ({len(train_df)} muestras)")
    print(f"Evaluación Test: Fechas {[str(d)[:10] for d in test_dates]} ({len(test_df)} muestras)\n")

    # SUB-EXPERIMENTO A: DETECTOR DE GIRO DE TECHO
    print("-" * 85)
    print("⚠️ SUB-EXPERIMENTO A: DETECCIÓN DE GIRO DE TECHO (SUBE HOY -> SE AGOTA MAÑANA)")
    print("-" * 85)

    tr_rising = train_df[train_df["subida_24h"] > 0].copy()
    te_rising = test_df[test_df["subida_24h"] > 0].copy()

    tr_rising["target_techo"] = (tr_rising["target_subida_1d"] <= 0).astype(int)
    te_rising["target_techo"] = (te_rising["target_subida_1d"] <= 0).astype(int)

    base_techo_rate = te_rising['target_techo'].mean() * 100.0
    print(f"Jugadores que suben en Test: {len(te_rising)}")
    print(f"Casos reales de Techo (frenan o caen): {te_rising['target_techo'].sum()} (Tasa base: {base_techo_rate:.1f}%)\n")

    clf_techo = HistGradientBoostingClassifier(
        max_iter=100, max_depth=3, min_samples_leaf=12, class_weight="balanced", random_state=42
    )
    clf_techo.fit(tr_rising[feature_cols], tr_rising["target_techo"])
    te_rising["prob_techo"] = clf_techo.predict_proba(te_rising[feature_cols])[:, 1]

    print(f"{'Umbral P(Techo)':<18} | {'Alertas Generadas':<18} | {'Techos Reales':<15} | {'Precisión':<12} | {'Recall':<10} | {'Lift vs Azar':<12}")
    print("-" * 90)
    for th in [0.35, 0.45, 0.50, 0.55, 0.65]:
        pred_bin = (te_rising["prob_techo"] >= th).astype(int)
        tp = ((pred_bin == 1) & (te_rising["target_techo"] == 1)).sum()
        fp = ((pred_bin == 1) & (te_rising["target_techo"] == 0)).sum()
        fn = ((pred_bin == 0) & (te_rising["target_techo"] == 1)).sum()
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0
        lift = prec / (base_techo_rate / 100.0) if base_techo_rate > 0 else 0
        print(f"{th:>14.2f}    | {pred_bin.sum():>14}   | {tp:>11}   | {prec*100:>9.1f}% | {rec*100:>7.1f}% | {lift:>9.1f}x")

    top_techos = te_rising.sort_values(by="prob_techo", ascending=False).head(6)
    print("\n🔍 Top 6 Jugadores con Mayor Probabilidad de Techo Detectada:")
    for _, r in top_techos.iterrows():
        acierto = "✅ TECHO CONFIRMADO" if r['target_techo'] == 1 else "❌ SIGUIÓ SUBIENDO"
        print(f" • {r['nombre']:<20} ({r['equipo']:<14}) | Subida Hoy: {r['subida_24h']:>+8,.0f} € | P(Techo): {r['prob_techo']*100:5.1f}% | Mañana Real: {r['target_subida_1d']:>+8,.0f} € | {acierto}")

    # SUB-EXPERIMENTO B: DETECTOR DE GIRO DE SUELO
    print("\n" + "-" * 85)
    print("💎 SUB-EXPERIMENTO B: DETECCIÓN DE GIRO DE SUELO (CAE/PLANO HOY -> SUBE MAÑANA)")
    print("-" * 85)

    tr_falling = train_df[train_df["subida_24h"] <= 0].copy()
    te_falling = test_df[test_df["subida_24h"] <= 0].copy()

    tr_falling["target_suelo"] = (tr_falling["target_subida_1d"] > 0).astype(int)
    te_falling["target_suelo"] = (te_falling["target_subida_1d"] > 0).astype(int)

    base_suelo_rate = te_falling['target_suelo'].mean() * 100.0
    print(f"Jugadores planos o cayendo en Test: {len(te_falling)}")
    print(f"Casos reales de Suelo (giran al alza): {te_falling['target_suelo'].sum()} (Tasa base: {base_suelo_rate:.1f}%)\n")

    clf_suelo = HistGradientBoostingClassifier(
        max_iter=100, max_depth=3, min_samples_leaf=12, class_weight="balanced", random_state=42
    )
    clf_suelo.fit(tr_falling[feature_cols], tr_falling["target_suelo"])
    te_falling["prob_suelo"] = clf_suelo.predict_proba(te_falling[feature_cols])[:, 1]

    print(f"{'Umbral P(Suelo)':<18} | {'Alertas Generadas':<18} | {'Suelos Reales':<15} | {'Precisión':<12} | {'Recall':<10} | {'Lift vs Azar':<12}")
    print("-" * 90)
    for th in [0.35, 0.45, 0.50, 0.55, 0.65]:
        pred_bin = (te_falling["prob_suelo"] >= th).astype(int)
        tp = ((pred_bin == 1) & (te_falling["target_suelo"] == 1)).sum()
        fp = ((pred_bin == 1) & (te_falling["target_suelo"] == 0)).sum()
        fn = ((pred_bin == 0) & (te_falling["target_suelo"] == 1)).sum()
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0
        lift = prec / (base_suelo_rate / 100.0) if base_suelo_rate > 0 else 0
        print(f"{th:>14.2f}    | {pred_bin.sum():>14}   | {tp:>11}   | {prec*100:>9.1f}% | {rec*100:>7.1f}% | {lift:>9.1f}x")

    top_suelos = te_falling.sort_values(by="prob_suelo", ascending=False).head(6)
    print("\n🔍 Top 6 Jugadores con Mayor Probabilidad de Suelo Detectada:")
    for _, r in top_suelos.iterrows():
        acierto = "✅ SUELO CONFIRMADO" if r['target_suelo'] == 1 else "❌ SIGUIÓ CAYENDO/PLANO"
        print(f" • {r['nombre']:<20} ({r['equipo']:<14}) | Subida Hoy: {r['subida_24h']:>+8,.0f} € | P(Suelo): {r['prob_suelo']*100:5.1f}% | Mañana Real: {r['target_subida_1d']:>+8,.0f} € | {acierto}")

if __name__ == "__main__":
    run_inflection_calibration()
