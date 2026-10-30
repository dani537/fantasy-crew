"""
Ejecutable Principal del Motor Predictivo Cuantitativo Biwenger (v2)
=====================================================================
Pipeline completo de predicción y toma de decisiones tácticas:
- Validación Temporal Walk-Forward (Sin fuga de datos)
- Arquitectura en Dos Etapas (Zero-Hurdle + Residual Delta)
- Cuantización Discreta Biwenger 10.000 € (+/- 5k deadband)
- Rollout Multitemporal Encadenado (24h -> 48h -> 72h)
- Valor Esperado Fantasy (EFR) y Horizonte Óptimo de Retención
- Exportación local a CSV y Excel Multitab Estilizado (7 pestañas)

Uso:
  .venv/bin/python test/05_market_prediction/run.py
"""

import os
import sys
import pandas as pd

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../.."))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from predictive_model import BiwengerMarketPredictorV4

def main():
    print("\n" + "="*95)
    print("🧠 MOTOR CUANTITATIVO PREDICTIVO BIWENGER (v4 - ENSEMBLE OPTIMIZADO 14d + LAGS 7d + MACD + RACHAS)")
    print("="*95)

    predictor = BiwengerMarketPredictorV4()
    print("📥 Cargando histórico y ejecutando sanitización matemática de datos...")
    predictor.load_and_prepare_data()

    print("⚙️ Entrenando arquitectura dual-stage v4 (Zero-Hurdle Calibrado + Regresor Residual Multi-Modelo)...")
    metrics = predictor.train_and_validate()

    print("\n" + "-"*95)
    print("📊 RESULTADOS DE CALIBRACIÓN TEMPORAL (WALK-FORWARD FUERA DE MUESTRA v4)")
    print("-"*95)
    print(f" • Fechas de Validación Evaluadas        : {metrics.get('test_dates', [])}")
    print(f" • Muestras Evaluadas Fuera de Muestra    : {metrics['n_test_samples']:,d} transiciones")
    print(f" • Error Medio Absoluto en Euros (MAE)    : {metrics['v3_mae_eur']:,.0f} € (Baseline: {metrics['baseline_mae_eur']:,.0f} €)")
    print(f" • Coeficiente de Determinación (R²)      : {metrics['v3_r2_eur']:.4f} (Baseline: {metrics['baseline_r2_eur']:.4f})")
    print(f" • Exactitud Direccional Global (24h)     : {metrics['v3_dir_acc']:.1f}% (Baseline: {metrics['baseline_dir_acc']:.1f}%)")
    print(f" • Exactitud de Régimen de Mercado (72h)  : {metrics['regime_acc_72h']:.1f}%")

    print("\n🔮 Generando proyecciones multitemporales y señales tácticas para HOY...")
    df_today = predictor.predict_latest()

    import argparse
    parser = argparse.ArgumentParser(description="Biwenger Quantitative Predictive Engine v4")
    parser.add_argument("--sync-sheets", action="store_true", help="Upload predictions to Google Sheets (Predicciones_IA tab)")
    args, _ = parser.parse_known_args()

    # Exportación Local v4 + Multi-versión compatible
    csv_v4 = "data/predictions/predicciones_mercado_v4.csv"
    xlsx_v4 = "data/predictions/estudio_predictivo_mercado_v4.xlsx"
    csv_v3 = "data/predictions/predicciones_mercado_v3.csv"
    xlsx_v3 = "data/predictions/estudio_predictivo_mercado_v3.xlsx"
    csv_file = "data/predictions/predicciones_mercado_v2.csv"
    xlsx_file = "data/predictions/estudio_predictivo_mercado_v2.xlsx"
    csv_legacy = "data/predictions/predicciones_mercado_test.csv"
    xlsx_legacy = "data/predictions/estudio_predictivo_mercado.xlsx"

    os.makedirs("data/predictions", exist_ok=True)
    for p_csv in [csv_v4, csv_v3, csv_file, csv_legacy]:
        df_today.to_csv(p_csv, index=False, encoding="utf-8-sig")
    for p_xlsx in [xlsx_v4, xlsx_v3, xlsx_file, xlsx_legacy]:
        predictor.export_excel(df_today, p_xlsx)

    print(f"💾 Entregables exportados con éxito:")
    print(f"   📄 CSV   : {csv_v4} (y réplicas compatibles v3/v2/test)")
    print(f"   📊 Excel : {xlsx_v4} (7 pestañas con formato condicional y EFR)")

    if args.sync_sheets:
        print("☁️ Sincronizando con Google Sheets (pestaña Predicciones_IA)...")
        predictor.sync_to_google_sheets()
    else:
        print("ℹ️ Modo local seguro: Google Sheets intacto (usa --sync-sheets para sincronizar con la nube).")


    # 1. Micro-gangas (< 1.5M €)
    print("\n" + "="*95)
    print("🚀 TOP 8 MICRO-GANGAS ESPECULATIVAS (< 1.5M €: MÁXIMO ROI PORCENTUAL)")
    print("="*95)
    micro_gems = predictor.get_top_micro_gems(df_today, 8)
    for idx, (_, r) in enumerate(micro_gems.iterrows(), 1):
        print(f"{idx:2d}. {r['nombre']} ({r['equipo']} - {r['posicion']})")
        print(f"    Precio Actual: {r['precio']:,.0f} € | Subida Hoy: {r['subida_24h']:+,.0f} € | Presión Neta: {r['presion_neta']:+.0f}% (IEN: {r['ien']:.1f})")
        print(f"    Predicción 24h: {r['pred_subida_24h']:+,.0f} € ({r['pred_pct_24h']:+.2f}%) -> {r['precio_est_24h']:,.0f} €")
        print(f"    Proyección 72h: {r['precio_est_72h']:,.0f} € ({r['pred_subida_72h_cum']:+,.0f} € | ROI: {r['pred_roi_72h_pct']:+.1f}%) | EFR: {r['efr_72h_eur']:+,.0f} €")
        print(f"    Horizonte: {r['dias_optimos_retencion']} | Acción: {r['accion_recomendada']}\n")

    # 2. Gama Media (1.5M - 5M €)
    print("="*95)
    print("💎 TOP 8 JOYAS DE GAMA MEDIA (1.5M - 5M €: ALTO VALOR ABSOLUTO)")
    print("="*95)
    mid_gems = predictor.get_top_mid_gems(df_today, 8)
    for idx, (_, r) in enumerate(mid_gems.iterrows(), 1):
        print(f"{idx:2d}. {r['nombre']} ({r['equipo']} - {r['posicion']})")
        print(f"    Precio: {r['precio']:,.0f} € | Subida Hoy: {r['subida_24h']:+,.0f} € | Presión: {r['presion_neta']:+.0f}%")
        print(f"    Predicción 24h: {r['pred_subida_24h']:+,.0f} € ({r['pred_pct_24h']:+.2f}%)")
        print(f"    Proyección 72h: {r['precio_est_72h']:,.0f} € ({r['pred_subida_72h_cum']:+,.0f} € | ROI: {r['pred_roi_72h_pct']:+.1f}%)")
        print(f"    Horizonte: {r['dias_optimos_retencion']} | Acción: {r['accion_recomendada']}\n")

    # 3. Alertas de Suelo Inminente (ASI)
    print("="*95)
    print("🎯 ALERTAS DE SUELO INMINENTE (ASI: GIRO ALCISTA TEMPRANO - COMPRA SNIPER)")
    print("="*95)
    floors = predictor.get_top_floor_breakouts(df_today, 6)
    if len(floors) == 0:
        print("   ℹ️ No se detectan giros de suelo hoy con probabilidad > 50%.")
    for idx, (_, r) in enumerate(floors.iterrows(), 1):
        print(f"{idx:2d}. {r['nombre']} ({r['equipo']} - {r['posicion']}) | Precio: {r['precio']:,.0f} € | Subida Hoy: {r['subida_24h']:+,.0f} €")
        print(f"    Diagnóstico: P(Suelo) = {r['prob_suelo_pct']:.1f}% | Compras: {r['pct_compras_24h']:.0f}% (Ventas: {r['pct_ventas_24h']:.0f}%) | ΔPresión: {r.get('delta_presion_1d', 0):+.0f}%")
        print(f"    Predicción 24h: {r['pred_subida_24h']:+,.0f} € | Proyección 72h: {r['precio_est_72h']:,.0f} € ({r['pred_roi_72h_pct']:+.1f}%)")
        print(f"    Horizonte: {r['dias_optimos_retencion']} | Acción: {r['accion_recomendada']}\n")

    # 4. Alertas de Techo Inminente (ATI)
    print("="*95)
    print("⚠️ ALERTAS DE TECHO INMINENTE (ATI: SUBEN HOY PERO SE FRENAN MAÑANA - VENTA EN MÁXIMOS)")
    print("="*95)
    ceilings = predictor.get_top_ceiling_warnings(df_today, 6)
    if len(ceilings) == 0:
        print("   ℹ️ No se detectan giros de techo hoy con probabilidad > 50%.")
    for idx, (_, r) in enumerate(ceilings.iterrows(), 1):
        print(f"{idx:2d}. {r['nombre']} ({r['equipo']} - {r['posicion']}) | Precio: {r['precio']:,.0f} € | Subida Hoy: {r['subida_24h']:+,.0f} €")
        print(f"    Diagnóstico: P(Techo) = {r['prob_techo_pct']:.1f}% | Ventas: {r['pct_ventas_24h']:.0f}% | ΔPresión: {r.get('delta_presion_1d', 0):+.0f}% | Aceleración: {r.get('accel_subida_1d', 0):+,.0f} €")
        print(f"    Predicción 24h: {r['pred_subida_24h']:+,.0f} € (Freno proyectado)")
        print(f"    Horizonte: {r['dias_optimos_retencion']} | Acción: {r['accion_recomendada']}\n")

    # 5. Top Stop-Loss
    print("="*95)
    print("🛑 TOP 8 ALARMAS DE DESPLOME (VENTA URGENTE / STOP-LOSS INMEDIATO)")
    print("="*95)
    crashes = predictor.get_top_stop_loss(df_today, 8)
    for idx, (_, r) in enumerate(crashes.iterrows(), 1):
        print(f"{idx:2d}. {r['nombre']} ({r['equipo']} - {r['posicion']})")
        print(f"    Precio Actual: {r['precio']:,.0f} € | Caída Hoy: {r['subida_24h']:+,.0f} € | Ventas: {r['pct_ventas_24h']:.0f}%")
        print(f"    Predicción 24h: {r['pred_subida_24h']:+,.0f} € ({r['pred_pct_24h']:+.2f}%)")
        print(f"    Proyección 72h: {r['precio_est_72h']:,.0f} € ({r['pred_subida_72h_cum']:+,.0f} € si se mantiene | P(Desplome): {r['prob_desplome_pct']:.1f}%)")
        print(f"    Horizonte: {r['dias_optimos_retencion']} | Acción: {r['accion_recomendada']}\n")

    # 6. Cracks Élite
    print("="*95)
    print("👑 TOP 5 CRACKS DE ÉLITE (> 8M € EN SUBIDA CONTINUADA)")
    print("="*95)
    cracks = predictor.get_top_cracks(df_today, 5)
    for idx, (_, r) in enumerate(cracks.iterrows(), 1):
        print(f"{idx:2d}. {r['nombre']} ({r['equipo']} - {r['posicion']})")
        print(f"    Precio: {r['precio']:,.0f} € | Subida Hoy: {r['subida_24h']:+,.0f} € | Subida 24h Estimada: {r['pred_subida_24h']:+,.0f} €")
        print(f"    Proyección 72h: {r['precio_est_72h']:,.0f} € ({r['pred_subida_72h_cum']:+,.0f} € | ROI: {r['pred_roi_72h_pct']:+.1f}%)\n")

    print("="*95)
    print("✅ EJECUCIÓN v3 COMPLETADA CON ÉXITO")
    print("="*95 + "\n")

if __name__ == "__main__":
    main()
