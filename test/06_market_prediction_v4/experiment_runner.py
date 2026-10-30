"""
Comprehensive Econometric & Machine Learning Benchmark Suite (v3)
==================================================================
Runs rigorous empirical benchmarks on Biwenger historical data:
1. Architectural Comparison: Baseline vs Test 05 (Direct) vs Test 06 v2 (Hurdle + Residual + 10k Grid)
2. Feature Ablation: Price vs Sentiment vs Microstructure & Saturation vs Full Master
3. Quantized Discrete Grid Evaluation & Directional Accuracy Breakdown
4. 72h Regime Classification (Explosions & Crashes)
5. Fantasy Trading Portfolio Backtest (Alpha ROI & Capital Saved)
6. Qualitative Failure Diagnostics & Risk-Adjusted Holding Optimization
"""

import os
import sys
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Tuple
from sklearn.linear_model import Ridge
from sklearn.ensemble import (
    RandomForestRegressor, RandomForestClassifier, ExtraTreesRegressor,
    HistGradientBoostingRegressor, HistGradientBoostingClassifier
)
from sklearn.metrics import mean_absolute_error, r2_score, accuracy_score, classification_report

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from features import build_features
from targets import build_targets, REGIME_NAMES
from predictive_model import quantize_to_biwenger_grid

def load_dataset(csv_path: str = "data/history/market_sentiment_timeseries.csv") -> Tuple[pd.DataFrame, Dict[str, List[str]]]:
    """Loads and sanitizes all features and targets from historical timeseries."""
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Histórico local no encontrado en {csv_path}")
    raw_df = pd.read_csv(csv_path)
    df_feat, feature_groups = build_features(raw_df, max_lags=4)
    df_full = build_targets(df_feat)
    return df_full, feature_groups

def run_head_to_head_architecture_experiment(df: pd.DataFrame, feature_groups: Dict[str, List[str]], train_dates: List[str], test_dates: List[str]):
    """Experiment 1: Head-to-head comparison of Baseline vs Modelo v3 Actual vs NUEVO Modelo v4."""
    print("\n" + "="*95)
    print("🔬 EXPERIMENTO 1: COMPARATIVA DIRECTA DE ARQUITECTURAS (HEAD-TO-HEAD: BASELINE vs v3 vs v4)")
    print("="*95)

    valid = df.dropna(subset=["subida_lag2", "target_subida_1d", "target_delta_subida_1d"]).copy()
    train_mask = valid["fecha"].isin(train_dates)
    test_mask = valid["fecha"].isin(test_dates)

    cols_v3 = feature_groups["set5_master_v3"]
    cols_v4 = feature_groups["set6_master_v4"]

    X_tr_v3 = valid.loc[train_mask, cols_v3].fillna(0)
    X_te_v3 = valid.loc[test_mask, cols_v3].fillna(0)

    X_tr_v4 = valid.loc[train_mask, cols_v4].fillna(0)
    X_te_v4 = valid.loc[test_mask, cols_v4].fillna(0)

    y_tr_eur = valid.loc[train_mask, "target_subida_1d"]
    y_te_eur = valid.loc[test_mask, "target_subida_1d"]

    y_tr_zero = valid.loc[train_mask, "target_is_zero_1d"].astype(int)
    y_tr_delta = valid.loc[train_mask, "target_delta_subida_1d"]

    test_df = valid.loc[test_mask].copy()
    curr_sub = test_df["subida_24h"].values
    pres_te = test_df["presion_neta"].values
    ventas_te = test_df["pct_ventas_24h"].values
    compras_te = test_df["pct_compras_24h"].values
    streak_flat_te = test_df["streak_flat"].values if "streak_flat" in test_df.columns else np.zeros(len(curr_sub))

    # 1. Baseline: Pure Momentum Persistence
    pred_baseline = curr_sub

    # 2. Modelo Actual v3 (Producción: Ridge 10% + RF 30% + ET 20% + HGB 40% + 59 features v3)
    clf_z_v3 = HistGradientBoostingClassifier(max_iter=120, max_depth=4, min_samples_leaf=12, random_state=42)
    clf_z_v3.fit(X_tr_v3, y_tr_zero)
    prob_z_v3 = clf_z_v3.predict_proba(X_te_v3)[:, 1]

    reg_r_v3 = Ridge(alpha=100.0).fit(X_tr_v3, y_tr_delta)
    reg_rf_v3 = RandomForestRegressor(n_estimators=100, max_depth=6, min_samples_leaf=5, random_state=42).fit(X_tr_v3, y_tr_delta)
    reg_et_v3 = ExtraTreesRegressor(n_estimators=100, max_depth=6, min_samples_leaf=5, random_state=42).fit(X_tr_v3, y_tr_delta)
    reg_hgb_v3 = HistGradientBoostingRegressor(max_iter=120, max_depth=4, min_samples_leaf=12, l2_regularization=2.0, random_state=42).fit(X_tr_v3, y_tr_delta)

    p_del_v3 = 0.10 * reg_r_v3.predict(X_te_v3) + 0.30 * reg_rf_v3.predict(X_te_v3) + 0.20 * reg_et_v3.predict(X_te_v3) + 0.40 * reg_hgb_v3.predict(X_te_v3)
    raw_pred_v3 = curr_sub + p_del_v3
    quant_v3 = quantize_to_biwenger_grid(raw_pred_v3, deadband=5000.0)

    pred_v3 = np.where(
        (curr_sub == 0) & (prob_z_v3 >= 0.35) & (np.abs(raw_pred_v3) < 10000),
        0.0,
        quant_v3
    )
    pred_v3[(curr_sub == 10000) & (pres_te > 0) & (ventas_te <= 3.0) & (pred_v3 == 0)] = 10000.0
    pred_v3[(curr_sub == -10000) & (pres_te < 0) & (pred_v3 == 0)] = -10000.0

    # 3. NUEVO Modelo v4 (Ensemble HGB 50% + ET 25% + RF 15% + Ridge 10% + Curado v4)
    clf_z_v4 = HistGradientBoostingClassifier(max_iter=140, max_depth=5, min_samples_leaf=10, random_state=42)
    clf_z_v4.fit(X_tr_v4, y_tr_zero)
    prob_z_v4 = clf_z_v4.predict_proba(X_te_v4)[:, 1]

    reg_r_v4 = Ridge(alpha=150.0).fit(X_tr_v4, y_tr_delta)
    reg_rf_v4 = RandomForestRegressor(n_estimators=150, max_depth=7, min_samples_leaf=4, random_state=42).fit(X_tr_v4, y_tr_delta)
    reg_et_v4 = ExtraTreesRegressor(n_estimators=150, max_depth=7, min_samples_leaf=4, random_state=42).fit(X_tr_v4, y_tr_delta)
    reg_hgb_v4 = HistGradientBoostingRegressor(max_iter=140, max_depth=5, min_samples_leaf=10, l2_regularization=3.0, random_state=42).fit(X_tr_v4, y_tr_delta)

    p_del_v4 = 0.50 * reg_hgb_v4.predict(X_te_v4) + 0.25 * reg_et_v4.predict(X_te_v4) + 0.15 * reg_rf_v4.predict(X_te_v4) + 0.10 * reg_r_v4.predict(X_te_v4)
    raw_pred_v4 = curr_sub + p_del_v4
    quant_v4 = quantize_to_biwenger_grid(raw_pred_v4, deadband=5000.0)

    pred_v4 = np.where(
        (curr_sub == 0) & (prob_z_v4 >= 0.30) & (np.abs(raw_pred_v4) < 9500),
        0.0,
        quant_v4
    )
    pred_v4 = np.where(
        (curr_sub == 0) & (streak_flat_te >= 2) & (np.abs(pres_te) < 4.0) & (compras_te < 6.0),
        0.0,
        pred_v4
    )
    pred_v4[(curr_sub == 10000) & (pres_te > 0) & (ventas_te <= 3.0) & (pred_v4 == 0)] = 10000.0
    pred_v4[(curr_sub == -10000) & (pres_te < 0) & (pred_v4 == 0)] = -10000.0

    # Performance Table
    models = [
        ("1. Baseline (Inercia Pura)", pred_baseline),
        ("2. Modelo v3 Anterior (Producción)", pred_v3),
        ("3. NUEVO Modelo v4 (Optimizado 14d)", pred_v4)
    ]

    print(f"Evaluado sobre {len(test_df)} transiciones en fechas fuera de muestra ({[str(d)[:10] for d in test_dates]}):\n")
    print(f"{'Modelo':<42} | {'MAE (€)':<12} | {'R²':<10} | {'Exactitud Dir (%)':<18} | {'Exactitud Ceros (%)'}")
    print("-" * 105)
    for name, p in models:
        mae = mean_absolute_error(y_te_eur, p)
        r2 = r2_score(y_te_eur, p)
        dir_acc = (np.sign(p) == np.sign(y_te_eur)).mean() * 100.0
        actual_zeros = (y_te_eur == 0)
        zero_acc = (p[actual_zeros] == 0).mean() * 100.0 if actual_zeros.sum() > 0 else 0
        print(f"{name:<42} | {mae:>9,.0f} € | {r2:>8.4f} | {dir_acc:>16.1f}% | {zero_acc:>17.1f}%")

    # Segment Breakdown: v4 vs v3
    print("\n📊 DESGLOSE POR SEGMENTOS DE CAPITAL (MODELO v4 NUEVO vs MODELO v3 ANTERIOR):")
    print("-" * 115)
    tiers = [
        ("Micro-gangas (< 1.5M €)", test_df["precio"] < 1_500_000),
        ("Gama Media (1.5M - 5M €)", (test_df["precio"] >= 1_500_000) & (test_df["precio"] < 5_000_000)),
        ("Consolidados (5M - 10M €)", (test_df["precio"] >= 5_000_000) & (test_df["precio"] < 10_000_000)),
        ("Cracks Élite (> 10M €)", test_df["precio"] >= 10_000_000)
    ]
    print(f"{'Segmento':<26} | {'N Jug':<7} | {'MAE v3 Anterior':<17} | {'MAE v4 Nuevo':<15} | {'Mejora (€)':<14} | {'Dir Acc v4'}")
    print("-" * 115)
    for name, mask in tiers:
        sub = test_df[mask]
        if len(sub) == 0: continue
        sub_y = y_te_eur.loc[mask]
        sub_v3 = pred_v3[mask.values]
        sub_v4 = pred_v4[mask.values]
        mae_v3 = mean_absolute_error(sub_y, sub_v3)
        mae_v4 = mean_absolute_error(sub_y, sub_v4)
        diff = mae_v3 - mae_v4
        dir_v4 = (np.sign(sub_v4) == np.sign(sub_y)).mean() * 100.0
        print(f"{name:<26} | {len(sub):>5} | {mae_v3:>14,.0f} € | {mae_v4:>12,.0f} € | {diff:>+11,.0f} € | {dir_v4:>9.1f}%")

def run_feature_ablation_experiment(df: pd.DataFrame, feature_groups: Dict[str, List[str]], train_dates: List[str], test_dates: List[str]):
    """Experiment 2: Feature Ablation study measuring the impact of order-flow saturation and kinematics."""
    print("\n" + "="*95)
    print("🔬 EXPERIMENTO 2: ESTUDIO DE ABLACIÓN DE VARIABLES v2")
    print("="*95)

    valid = df.dropna(subset=["subida_lag2", "target_delta_subida_1d"]).copy()
    train_mask = valid["fecha"].isin(train_dates)
    test_mask = valid["fecha"].isin(test_dates)

    y_tr = valid.loc[train_mask, "target_delta_subida_1d"]
    y_te = valid.loc[test_mask, "target_delta_subida_1d"]

    results = []
    names = {
        "set1_price_only": "1. Solo Precios & Lags Cinemáticos",
        "set2_price_sentiment": "2. Precios + Sentimiento (% Compras/Ventas)",
        "set3_microstructure": "3. Precios + Sentimiento + Saturación & Microestructura",
        "set4_full_master": "4. Set Maestro v2 (Inflexiones + Deportivo)",
        "set5_master_v3": "5. Set Maestro v3 (+ Calendario + Breadth + Cliff)",
        "set6_master_v4": "6. Set Maestro v4 (+ Lags 7d + Rachas + MACD + Shock Semanal)"
    }

    for key, cols in feature_groups.items():
        X_tr = valid.loc[train_mask, cols].fillna(0)
        X_te = valid.loc[test_mask, cols].fillna(0)

        model = HistGradientBoostingRegressor(max_iter=100, max_depth=4, min_samples_leaf=12, l2_regularization=2.0, random_state=42)
        model.fit(X_tr, y_tr)
        preds = model.predict(X_te)

        mae = mean_absolute_error(y_te, preds)
        r2 = r2_score(y_te, preds)

        results.append({
            "Grupo": names.get(key, key),
            "N_Features": len(cols),
            "MAE Delta (€)": round(mae, 2),
            "R²": round(r2, 4)
        })

    res_df = pd.DataFrame(results)
    print(res_df.to_string(index=False))

    # Feature Importance on set6_master_v4
    cols_v4 = feature_groups["set6_master_v4"]
    X_tr_v4 = valid.loc[train_mask, cols_v4].fillna(0)
    rf = ExtraTreesRegressor(n_estimators=100, max_depth=8, random_state=42)
    rf.fit(X_tr_v4, y_tr)
    imp = pd.Series(rf.feature_importances_, index=cols_v4).sort_values(ascending=False)

    print("\n🏆 TOP 12 VARIABLES CON MAYOR PESO EN LA ACELERACIÓN RESIDUAL (MODELO v4):")
    for idx, (feat, val) in enumerate(imp.head(12).items(), 1):
        print(f" {idx:2d}. {feat:<28}: {val*100:5.2f}%")

def run_regime_experiment(df: pd.DataFrame, feature_cols: List[str]):
    """Experiment 4: 72h regime classification (Explosions, Crashes, Lateral)."""
    print("\n" + "="*95)
    print("🔬 EXPERIMENTO 4: DETECCIÓN DE REGÍMENES A 72H (DESPLOMES Y EXPLOSIONES)")
    print("="*95)

    valid_3d = df.dropna(subset=["subida_lag2", "target_regime_3d"]).copy()
    dates_3d = sorted(valid_3d["fecha"].unique())
    tr_dates = dates_3d[:-1]
    te_dates = dates_3d[-1:]

    X_tr = valid_3d[valid_3d["fecha"].isin(tr_dates)][feature_cols].fillna(0)
    y_tr = valid_3d[valid_3d["fecha"].isin(tr_dates)]["target_regime_3d"].astype(int)
    X_te = valid_3d[valid_3d["fecha"].isin(te_dates)][feature_cols].fillna(0)
    y_te = valid_3d[valid_3d["fecha"].isin(te_dates)]["target_regime_3d"].astype(int)

    classes = np.unique(y_tr)
    weights = len(y_tr) / (len(classes) * np.bincount(y_tr))
    sample_weights = np.array([weights[c] for c in y_tr])

    clf = HistGradientBoostingClassifier(max_iter=150, max_depth=5, min_samples_leaf=8, random_state=42)
    clf.fit(X_tr, y_tr, sample_weight=sample_weights)
    preds = clf.predict(X_te)

    labels = [0, 1, 2, 3]
    target_names = [REGIME_NAMES[i] for i in labels]

    print(f"Evaluación de Regímenes sobre {len(y_te)} jugadores en fecha test {str(te_dates[0])[:10]}:")
    print(f"Exactitud Global: {accuracy_score(y_te, preds)*100:.2f}%\n")
    print(classification_report(y_te, preds, labels=labels, target_names=target_names, zero_division=0))

def run_portfolio_trading_backtest(df: pd.DataFrame, feature_cols: List[str]):
    """Experiment 5: Financial Portfolio Backtest evaluating Alpha and ROI."""
    print("\n" + "="*95)
    print("🔬 EXPERIMENTO 5: BACKTEST DE CARTERA FANTASY Y RENTABILIDAD REAL (ROI)")
    print("="*95)

    valid_3d = df.dropna(subset=["subida_lag2", "target_pct_3d", "target_delta_3d"]).copy()
    dates_3d = sorted(valid_3d["fecha"].unique())
    tr_dates = dates_3d[:-1]
    te_dates = dates_3d[-1:]

    X_tr = valid_3d[valid_3d["fecha"].isin(tr_dates)][feature_cols].fillna(0)
    y_tr_pct = valid_3d[valid_3d["fecha"].isin(tr_dates)]["target_pct_3d"]
    X_te = valid_3d[valid_3d["fecha"].isin(te_dates)][feature_cols].fillna(0)

    reg = HistGradientBoostingRegressor(max_iter=120, max_depth=5, min_samples_leaf=10, random_state=42)
    reg.fit(X_tr, y_tr_pct)

    test_df = valid_3d[valid_3d["fecha"].isin(te_dates)].copy()
    test_df["pred_roi_3d"] = reg.predict(X_te)

    # Top 10 Chollos Especulativos (< 2.5M €)
    gems = test_df[test_df["precio"] < 2_500_000].sort_values(by="pred_roi_3d", ascending=False).head(10)
    market_avg_roi = test_df["target_pct_3d"].mean()
    gems_real_roi = gems["target_pct_3d"].mean()
    gems_real_gain = gems["target_delta_3d"].sum()

    print(f"1. ESTRATEGIA DE COMPRA: TOP 10 JOYAS ESPECULATIVAS (< 2.5M €)")
    print(f" • ROI Medio de Mercado (Buy & Hold aleatorio) : {market_avg_roi:+.2f}%")
    print(f" • ROI Real Obtenido por el Top 10 Modelo v2   : {gems_real_roi:+.2f}%  (Alpha: {gems_real_roi - market_avg_roi:+.2f}%)")
    print(f" • Beneficio Neto Generado en 72h (10 jugadores): {gems_real_gain:+,.0f} €\n")

    print(f"   {'Jugador':<20} | {'Equipo':<14} | {'Precio':<11} | {'Predicho 3d':<12} | {'Real 3d (%)':<12} | {'Beneficio (€)'}")
    print("   " + "-" * 85)
    for _, r in gems.iterrows():
        print(f"   {r['nombre']:<20} | {r['equipo']:<14} | {r['precio']:>9,.0f} € | {r['pred_roi_3d']:>+10.2f}% | {r['target_pct_3d']:>+10.2f}% | {r['target_delta_3d']:>+12,.0f} €")

    # Top 10 Stop-Loss
    crashes = test_df.sort_values(by="pred_roi_3d", ascending=True).head(10)
    crashes_real_loss = crashes["target_delta_3d"].sum()
    crashes_avg_drop = crashes["target_pct_3d"].mean()

    print(f"\n2. ESTRATEGIA DE DEFENSA: TOP 10 STOP-LOSS (VENTA URGENTE)")
    print(f" • Pérdida Media Real Evitada                  : {crashes_avg_drop:+.2f}%")
    print(f" • Capital Total Salvado al Vender a Tiempo    : {abs(crashes_real_loss):,.0f} €\n")

    print(f"   {'Jugador':<20} | {'Equipo':<14} | {'Precio':<11} | {'Predicho 3d':<12} | {'Caída Real (%)':<15} | {'Pérdida Evitada (€)'}")
    print("   " + "-" * 90)
    for _, r in crashes.iterrows():
        print(f"   {r['nombre']:<20} | {r['equipo']:<14} | {r['precio']:>9,.0f} € | {r['pred_roi_3d']:>+10.2f}% | {r['target_pct_3d']:>+13.2f}% | {r['target_delta_3d']:>+17,.0f} €")

def main():
    print("\n" + "="*95)
    print("🧠 SUITE DE EXPERIMENTACIÓN Y BENCHMARKING v4 - MOTOR CUANTITATIVO BIWENGER")
    print("="*95)

    df_full, feature_groups = load_dataset()
    dates = sorted(df_full["fecha"].unique())
    print(f"📥 Dataset sanitizado cargado: {len(df_full):,d} registros en {len(dates)} fechas continuas.")
    print(f"📅 Rango de Fechas: {str(dates[0])[:10]} -> {str(dates[-1])[:10]}")

    train_dates = dates[:-2]
    test_dates = dates[-2:]
    master_features = feature_groups["set6_master_v4"]

    # 1. Head to Head Architecture Benchmark (Baseline vs v3 vs v4)
    run_head_to_head_architecture_experiment(df_full, feature_groups, train_dates, test_dates)

    # 2. Feature Ablation
    run_feature_ablation_experiment(df_full, feature_groups, train_dates, test_dates)

    # 3. 72h Regime Classification
    run_regime_experiment(df_full, master_features)

    # 4. Trading Portfolio Backtest
    run_portfolio_trading_backtest(df_full, master_features)

    print("\n" + "="*95)
    print("✅ SUITE DE EXPERIMENTACIÓN v4 COMPLETADA CON ÉXITO")
    print("="*95 + "\n")

if __name__ == "__main__":
    main()
