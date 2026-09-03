"""
Ejecutable de Predicción de Mercado (Test 05)
==============================================
Ejecuta el modelo predictivo de mercado sobre el dataset diario más reciente
y muestra las mejores oportunidades de especulación y alertas de venta.

Uso:
  python test/05_market_prediction/run.py
"""

import sys
import os
import pandas as pd

# Path setup
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../.."))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from predictive_model import BiwengerMarketPredictor

def main():
    print("\n" + "="*80)
    print("🧠 TEST 05: MODELO PREDICTIVO DE MERCADO Y SENTIMIENTO BIWENGER")
    print("="*80)

    predictor = BiwengerMarketPredictor()
    try:
        predictor.load_data()
    except FileNotFoundError as e:
        print(f"❌ Error: {e}")
        return

    print("⚙️ Generando indicadores financieros y de sentimiento...")
    predictor.engineer_features()

    print("🔮 Ejecutando modelo predictivo de variación de precio a 24-48h...")
    df_pred = predictor.predict()

    # Guardar predicciones completas en CSV
    out_dir = "data/predictions"
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, "predicciones_mercado_hoy.csv")
    df_pred.to_csv(out_file, index=False, encoding="utf-8-sig")
    print(f"💾 Predicciones completas exportadas a: {out_file}\n")

    # 1. Top 10 Joyas Especulativas
    print("="*80)
    print("🚀 TOP 10 JOYAS ESPECULATIVAS (MÁXIMA PRESIÓN COMPRADORA / PUJAR HOY)")
    print("="*80)
    top_gems = predictor.get_top_speculative_gems(10)
    for idx, (_, r) in enumerate(top_gems.iterrows(), 1):
        print(f"{idx:2d}. {r['nombre']} ({r['equipo']} - {r['posicion']})")
        print(f"    Precio: {r['precio']:,.0f} € | Subida 24h: {r['subida_24h']:+,.0f} €")
        print(f"    Presión Neta: {r['presion_neta']:+.1f}% (Compras: {r['pct_compras_24h']:.1f}%, Ventas: {r['pct_ventas_24h']:.1f}%)")
        print(f"    Predicción 24h: {r['pred_pct_24h']:+.2f}% (~{r['pred_delta_eur_24h']:+,.0f} €) | Fase: {r['fase_mercado']}\n")

    # 2. Top 10 Alarmas de Desplome
    print("="*80)
    print("🛑 TOP 10 ALARMAS DE DESPLOME (PRESIÓN VENDEDORA / VENTA URGENTE)")
    print("="*80)
    top_crashes = predictor.get_top_crash_warnings(10)
    for idx, (_, r) in enumerate(top_crashes.iterrows(), 1):
        print(f"{idx:2d}. {r['nombre']} ({r['equipo']} - {r['posicion']})")
        print(f"    Precio: {r['precio']:,.0f} € | Subida 24h: {r['subida_24h']:+,.0f} €")
        print(f"    Presión Neta: {r['presion_neta']:+.1f}% (Compras: {r['pct_compras_24h']:.1f}%, Ventas: {r['pct_ventas_24h']:.1f}%)")
        print(f"    Predicción 24h: {r['pred_pct_24h']:+.2f}% (~{r['pred_delta_eur_24h']:+,.0f} €) | Acción: {r['accion_recomendada']}\n")

    print("="*80)
    print("✅ Test 05 completado con éxito.")
    print("="*80 + "\n")

if __name__ == "__main__":
    main()
