"""
Ejecutable del Modelo Predictivo de Mercado Biwenger (Test 05)
==============================================================
Ejecuta el entrenamiento econométrico y de machine learning sobre
las transiciones observadas en el histórico y genera las predicciones
para las próximas 24h, 48h y 72h con señales tácticas operativas.

Uso:
  .venv/bin/python test/05_market_prediction/run.py
"""

import os
import sys
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
    print("\n" + "="*85)
    print("🧠 MODELO PREDICTIVO AVANZADO DE VALOR DE MERCADO BIWENGER (ML & ECONOMETRÍA)")
    print("="*85)

    predictor = BiwengerMarketPredictor()
    print("📥 Cargando dataset de series temporales y preparando variables...")
    predictor.load_and_prepare_data()

    print("⚙️ Entrenando ensemble econométrico (Ridge + Random Forest) con validación cruzada...")
    metrics = predictor.train_and_validate(n_splits=5)

    print("\n" + "-"*85)
    print("📊 RESULTADOS DE CALIBRACIÓN Y BACKTEST HISTÓRICO (5-FOLD CROSS VALIDATION)")
    print("-"*85)
    print(f" • Transiciones históricas observadas : {metrics['n_transitions']:,d}")
    print(f" • Modelo Baseline (Persistencia)     : MAE = {metrics['baseline_mae']:,.0f} € | R² = {metrics['baseline_r2']:.4f} | Prec. Dir = {metrics['baseline_dir_acc']:.1f}%")
    print(f" • Modelo Ridge Regularizado         : MAE = {metrics['ridge_mae']:,.0f} € | R² = {metrics['ridge_r2']:.4f}")
    print(f" • Modelo Random Forest (No Lineal)  : MAE = {metrics['rf_mae']:,.0f} € | R² = {metrics['rf_r2']:.4f}")
    print(f" • ENSEMBLE BLEND (Producción)       : MAE = {metrics['blend_mae']:,.0f} € | R² = {metrics['blend_r2']:.4f} | Prec. Dir = {metrics['blend_dir_acc']:.1f}%")
    print(f" • Error Cuadrático Medio (RMSE)     : {metrics['blend_rmse']:,.0f} €")

    print("\n🔮 Generando predicciones multitemporales a 24h, 48h y 72h para hoy...")
    df_today = predictor.predict_latest()

    # Exportación
    csv_file = predictor.export_csv("data/predictions/predicciones_mercado_hoy.csv")
    xlsx_file = predictor.export_excel("data/predictions/modelo_predictivo_mercado.xlsx")
    print(f"💾 Predicciones completas exportadas a:")
    print(f"   📄 CSV   : {csv_file}")
    print(f"   📊 Excel : {xlsx_file}")
    predictor.sync_to_google_sheets()

    # 1. Top Joyas Especulativas (< 5M)
    print("\n" + "="*85)
    print("🚀 TOP 10 JOYAS ESPECULATIVAS (< 5M € CON MAYOR % DE SUBIDA PREVISTO)")
    print("="*85)
    gems = predictor.get_top_speculative_gems(10)
    for idx, (_, r) in enumerate(gems.iterrows(), 1):
        p_act = r['precio']
        p_48h = r['precio_est_48h']
        print(f"{idx:2d}. {r['nombre']} ({r['equipo']} - {r['posicion']})")
        print(f"    Precio Actual: {p_act:,.0f} € | Subida Hoy: {r['subida_24h']:+,.0f} € | Presión Neta: {r['presion_neta']:+.0f}% (Uso: {r['pct_uso_ligas']:.0f}%)")
        print(f"    Predicción 24h: {r['pred_subida_24h']:+,.0f} € ({r['pred_pct_24h']:+.2f}%) | Prob. Subida: {r['prob_sube_pct']:.1f}%")
        print(f"    Proyección 48h: {p_48h:,.0f} € ({r['pred_subida_48h_cum']:+,.0f} € acumulado) | Tendencia: {r['tendencia_dinamica']}")
        print(f"    Acción: {r['accion_recomendada']}\n")

    # 2. Top Alarmas de Desplome
    print("="*85)
    print("🛑 TOP 10 ALARMAS DE DESPLOME (VENTA URGENTE / STOP-LOSS)")
    print("="*85)
    crashes = predictor.get_top_crash_warnings(10)
    for idx, (_, r) in enumerate(crashes.iterrows(), 1):
        p_act = r['precio']
        p_48h = r['precio_est_48h']
        print(f"{idx:2d}. {r['nombre']} ({r['equipo']} - {r['posicion']})")
        print(f"    Precio Actual: {p_act:,.0f} € | Caída Hoy: {r['subida_24h']:+,.0f} € | Presión Neta: {r['presion_neta']:+.0f}% (Ventas: {r['pct_ventas_24h']:.0f}%)")
        print(f"    Predicción 24h: {r['pred_subida_24h']:+,.0f} € ({r['pred_pct_24h']:+.2f}%) | Prob. Caída: {r['prob_baja_pct']:.1f}%")
        print(f"    Proyección 48h: {p_48h:,.0f} € ({r['pred_subida_48h_cum']:+,.0f} € quemados si se mantiene)")
        print(f"    Acción: {r['accion_recomendada']}\n")

    # 3. Top Cracks de Élite (> 8M)
    print("="*85)
    print("👑 TOP 5 CRACKS DE ÉLITE (> 8M € EN SUBIDA CONTINUADA)")
    print("="*85)
    cracks = predictor.get_top_cracks_gainers(5)
    for idx, (_, r) in enumerate(cracks.iterrows(), 1):
        p_act = r['precio']
        p_48h = r['precio_est_48h']
        print(f"{idx:2d}. {r['nombre']} ({r['equipo']} - {r['posicion']})")
        print(f"    Precio: {p_act:,.0f} € | Subida 24h Prevista: {r['pred_subida_24h']:+,.0f} € ({r['pred_pct_24h']:+.2f}%)")
        print(f"    Acumulado 48h Estimado: {r['pred_subida_48h_cum']:+,.0f} € -> {p_48h:,.0f} € | Prob. Sube: {r['prob_sube_pct']:.1f}%\n")

    print("="*85)
    print("✅ TEST 05 Y PREDICCIÓN DIARIA COMPLETADOS CON ÉXITO")
    print("="*85 + "\n")

if __name__ == "__main__":
    main()
