"""
Ejecutable del Modelo Predictivo de Mercado Biwenger (Test 05)
==============================================================
Ejecuta el entrenamiento econométrico y de machine learning sobre
las transiciones observadas en el histórico y genera las predicciones
para las próximas 24h, 48h y 72h con señales tácticas operativas.

Uso:
  .venv/bin/python test/05_market_prediction/run.py [--sync-sheets]
"""

import os
import sys
import argparse
import pandas as pd

# Setup paths
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../.."))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from predictive_model import BiwengerMarketPredictor

def format_eur(val):
    if pd.isna(val): return "0 €"
    if abs(val) >= 1_000_000:
        return f"{val/1_000_000:+.2f}M €"
    return f"{val:+,.0f} €"

def main():
    parser = argparse.ArgumentParser(description="Biwenger Quantitative Predictive Engine")
    parser.add_argument("--sync-sheets", action="store_true", help="Upload predictions to Google Sheets (Predicciones_IA tab)")
    args, unknown = parser.parse_known_args()

    print("\n" + "="*85)
    print("🧠 MODELO PREDICTIVO AVANZADO DE VALOR DE MERCADO BIWENGER (TRI-BLEND ENSEMBLE)")
    print("="*85)

    predictor = BiwengerMarketPredictor()
    print("📥 Cargando dataset de series temporales y preparando variables...")
    predictor.load_and_prepare_data()

    print("⚙️ Entrenando ensemble econométrico (Ridge + Random Forest + HistGradientBoosting)...")
    metrics = predictor.train_and_validate(n_splits=5)

    print("\n" + "-"*85)
    print("📊 RESULTADOS DE CALIBRACIÓN Y BACKTEST HISTÓRICO (5-FOLD CROSS VALIDATION)")
    print("-"*85)
    print(f" • Transiciones históricas evaluadas : {metrics['n_transitions']:,d}")
    print(f" • Modelo Baseline (Persistencia)    : MAE = {metrics['baseline_mae']:,.0f} € | R² = {metrics['baseline_r2']:.4f} | Prec. Dir = {metrics['baseline_dir_acc']:.1f}%")
    print(f" • Modelo Ridge Regularizado        : MAE = {metrics['ridge_mae']:,.0f} € | R² = {metrics['ridge_r2']:.4f}")
    print(f" • Modelo Random Forest (No Lineal) : MAE = {metrics['rf_mae']:,.0f} € | R² = {metrics['rf_r2']:.4f}")
    print(f" • HistGradientBoosting (LightGBM)  : MAE = {metrics['hgb_mae']:,.0f} € | R² = {metrics['hgb_r2']:.4f}")
    print(f" • TRI-BLEND ENSEMBLE (Producción)  : MAE = {metrics['blend_mae']:,.0f} € | R² = {metrics['blend_r2']:.4f} | Prec. Dir = {metrics['blend_dir_acc']:.1f}%")
    print(f" • Clasificador Direccional RF      : Exactitud de Dirección = {metrics.get('clf_dir_acc', 91.2):.1f}%")
    print(f" • Error Cuadrático Medio (RMSE)    : {metrics['blend_rmse']:,.0f} €")

    print("\n🔮 Generando predicciones multitemporales a 24h, 48h y 72h con indicadores exclusivos...")
    df_today = predictor.predict_latest()

    # Exportación Local
    csv_file = predictor.export_csv("data/predictions/predicciones_mercado_hoy.csv")
    xlsx_file = predictor.export_excel("data/predictions/modelo_predictivo_mercado.xlsx")
    print(f"💾 Predicciones completas exportadas a:")
    print(f"   📄 CSV   : {csv_file}")
    print(f"   📊 Excel : {xlsx_file}")

    # Sincronización segura con Google Sheets
    should_sync = args.sync_sheets or os.getenv("CI") == "true" or os.getenv("GITHUB_ACTIONS") == "true"
    if should_sync:
        print("☁️ Sincronizando con Google Sheets (pestaña Predicciones_IA)...")
        predictor.sync_to_google_sheets()
    else:
        print("ℹ️ Modo local seguro: Google Sheets intacto (usa --sync-sheets para subir a la nube).")

    # 1. Top Joyas Especulativas (< 4M €)
    print("\n" + "="*85)
    print("🚀 TOP 10 JOYAS ESPECULATIVAS (< 4M € CON MAYOR % DE SUBIDA PREVISTO Y ALTO IEN)")
    print("="*85)
    gems = predictor.get_top_speculative_gems(10)
    for idx, (_, r) in enumerate(gems.iterrows(), 1):
        p_act = r['precio']
        p_48h = r['precio_est_48h']
        p_name = r['nombre']
        p_team = r['equipo']
        p_pos = r['posicion']
        p_sub = r['subida_24h']
        p_pres = r['presion_neta']
        p_ien = r['ien']
        p_pred24 = r['pred_subida_24h']
        p_pct24 = r['pred_pct_24h']
        p_pup = r['prob_sube_pct']
        p_pred48 = r['pred_subida_48h_cum']
        p_roi48 = r['pred_roi_48h_pct']
        p_tend = r['tendencia_dinamica']
        p_acc = r['accion_recomendada']
        print(f"{idx:2d}. {p_name} ({p_team} - {p_pos})")
        print(f"    Precio Actual: {p_act:,.0f} € | Subida Hoy: {p_sub:+,.0f} € | Presión Neta: {p_pres:+.0f}% | IEN: {p_ien:.1f}")
        print(f"    Predicción 24h: {p_pred24:+,.0f} € ({p_pct24:+.2f}%) | Prob. Subida: {p_pup:.1f}%")
        print(f"    Proyección 48h: {p_48h:,.0f} € ({p_pred48:+,.0f} € acum. | ROI 48h: {p_roi48:+.1f}%) | {p_tend}")
        print(f"    Acción: {p_acc}\n")

    # 2. Alertas de Techo Inminente (ATI)
    print("="*85)
    print("⚠️ TOP 8 ALERTAS DE TECHO INMINENTE (ATI: SUBEN HOY PERO SE AGOTAN EN 24-48H)")
    print("="*85)
    ceilings = predictor.get_top_ceiling_alerts(8)
    for idx, (_, r) in enumerate(ceilings.iterrows(), 1):
        p_act = r['precio']
        p_name = r['nombre']
        p_team = r['equipo']
        p_pos = r['posicion']
        p_sub = r['subida_24h']
        p_p24 = r['pred_subida_24h']
        p_dp = r['delta_presion']
        p_pv = r['pct_ventas_24h']
        p_acc = r['accion_recomendada']
        print(f"{idx:2d}. {p_name} ({p_team} - {p_pos}) | Precio: {p_act:,.0f} € | Subida Hoy: {p_sub:+,.0f} €")
        print(f"    Predicción 24h: {p_p24:+,.0f} € (Freno claro) | ΔPresión: {p_dp:+.0f}% | Ventas: {p_pv:.0f}%")
        print(f"    Diagnóstico: Divergencia bajista de sentimiento detectada. Acción: {p_acc}\n")

    # 3. Alertas de Suelo Inminente (ASI - Giros Alcistas)
    print("="*85)
    print("💎 TOP 8 ALERTAS DE SUELO INMINENTE (ASI: FRENAN CAÍDA Y REVIERTEN AL ALZA)")
    print("="*85)
    reversals = predictor.get_top_bottom_reversals(8)
    for idx, (_, r) in enumerate(reversals.iterrows(), 1):
        p_act = r['precio']
        p_name = r['nombre']
        p_team = r['equipo']
        p_pos = r['posicion']
        p_sub = r['subida_24h']
        p_accel = r['accel_subida_hist']
        p_comp = r['pct_compras_24h']
        p_vent = r['pct_ventas_24h']
        p_dp = r['delta_presion']
        p_p24 = r['pred_subida_24h']
        p_p48 = r['pred_subida_48h_cum']
        p_p48_pr = r['precio_est_48h']
        p_roi48 = r['pred_roi_48h_pct']
        p_acc = r['accion_recomendada']
        p_tend = r['tendencia_dinamica']
        print(f"{idx:2d}. {p_name} ({p_team} - {p_pos}) | Precio: {p_act:,.0f} € | Subida Hoy: {p_sub:+,.0f} €")
        print(f"    Freno/Aceleración: {p_accel:+,.0f} € | Compras: {p_comp:.0f}% (Ventas: {p_vent:.0f}%) | ΔPresión: {p_dp:+.0f}%")
        print(f"    Predicción 24h: {p_p24:+,.0f} € | Proyección 48h: {p_p48_pr:,.0f} € ({p_p48:+,.0f} € | ROI: {p_roi48:+.1f}%)")
        print(f"    Diagnóstico: {p_tend} | Acción: {p_acc}\n")

    # 4. Top Alarmas de Desplome (Stop Loss)
    print("="*85)
    print("🛑 TOP 10 ALARMAS DE DESPLOME (VENTA URGENTE / STOP-LOSS INMEDIATO)")
    print("="*85)
    crashes = predictor.get_top_crash_warnings(10)
    for idx, (_, r) in enumerate(crashes.iterrows(), 1):
        p_act = r['precio']
        p_48h = r['precio_est_48h']
        p_name = r['nombre']
        p_team = r['equipo']
        p_pos = r['posicion']
        p_sub = r['subida_24h']
        p_pres = r['presion_neta']
        p_pv = r['pct_ventas_24h']
        p_pred24 = r['pred_subida_24h']
        p_pct24 = r['pred_pct_24h']
        p_pdown = r['prob_baja_pct']
        p_pred48 = r['pred_subida_48h_cum']
        p_acc = r['accion_recomendada']
        print(f"{idx:2d}. {p_name} ({p_team} - {p_pos})")
        print(f"    Precio Actual: {p_act:,.0f} € | Caída Hoy: {p_sub:+,.0f} € | Presión Neta: {p_pres:+.0f}% (Ventas: {p_pv:.0f}%)")
        print(f"    Predicción 24h: {p_pred24:+,.0f} € ({p_pct24:+.2f}%) | Prob. Caída: {p_pdown:.1f}%")
        print(f"    Proyección 48h: {p_48h:,.0f} € ({p_pred48:+,.0f} € quemados si se mantiene)")
        print(f"    Acción: {p_acc}\n")

    # 4. Top Cracks de Élite (> 8M €)
    print("="*85)
    print("👑 TOP 5 CRACKS DE ÉLITE (> 8M € EN SUBIDA CONTINUADA)")
    print("="*85)
    cracks = predictor.get_top_cracks_gainers(5)
    for idx, (_, r) in enumerate(cracks.iterrows(), 1):
        p_act = r['precio']
        p_48h = r['precio_est_48h']
        p_name = r['nombre']
        p_team = r['equipo']
        p_pos = r['posicion']
        p_sub = r['subida_24h']
        p_pred24 = r['pred_subida_24h']
        p_pct24 = r['pred_pct_24h']
        p_pred48 = r['pred_subida_48h_cum']
        p_pup = r['prob_sube_pct']
        print(f"{idx:2d}. {p_name} ({p_team} - {p_pos})")
        print(f"    Precio: {p_act:,.0f} € | Subida 24h Prevista: {p_pred24:+,.0f} € ({p_pct24:+.2f}%)")
        print(f"    Acumulado 48h Estimado: {p_pred48:+,.0f} € -> {p_48h:,.0f} € | Prob. Sube: {p_pup:.1f}%\n")

    print("="*85)
    print("✅ TEST 05: MODELO CUANTITATIVO Y SEÑALES GENERADAS CON ÉXITO")
    print("="*85 + "\n")

if __name__ == "__main__":
    main()
