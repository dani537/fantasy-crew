"""
Calculadora Interactiva de Pujas y Mercado (Test 99 - Pujas)
============================================================
Herramienta CLI interactiva para calcular la oferta óptima en Biwenger
basada en el modelo predictivo de regresión OLS y el histórico de la liga.

Usage:
  # Modo Interactivo:
  .venv/bin/python test/99_pujas/calculadora_pujas.py

  # Modo Directo con Parámetros:
  .venv/bin/python test/99_pujas/calculadora_pujas.py --precio 2.5M --posicion DF --pujadores 2
"""

import sys
import os
import argparse
import datetime
import pandas as pd
import numpy as np

# Add script directory and project root to sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, '../..'))

if script_dir not in sys.path:
    sys.path.insert(0, script_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from run import load_data, build_auction_study_data
from modelo_predictivo_pujas import BidPredictiveModel


def parse_price(price_input_str):
    """Parses flexible price input strings like '2.5M', '2,5m', '1500000', '0.8M'."""
    s = str(price_input_str).strip().replace(',', '.').upper()
    if s.endswith('M') or s.endswith('M€'):
        s_clean = s.replace('M€', '').replace('M', '')
        return float(s_clean) * 1_000_000.0
    elif s.endswith('K') or s.endswith('K€'):
        s_clean = s.replace('K€', '').replace('K', '')
        return float(s_clean) * 1_000.0
    else:
        val = float(s)
        if val < 1000.0:  # If user entered 2.5 assuming millions
            return val * 1_000_000.0
        return val


def format_eur(amount):
    """Formats float euro amounts into '2.45M € (2,450,000 €)'."""
    if amount >= 1_000_000:
        return f"{amount/1e6:.2f}M € ({amount:,.0f} €)"
    elif amount >= 1_000:
        return f"{amount/1e3:.0f}K € ({amount:,.0f} €)"
    else:
        return f"{amount:,.0f} €"


def run_bid_calculator(price_str=None, pos_str=None, bidders_int=None, manager_str=None, growth_str=None):
    # Load dataset & fit model
    df_bids, df_transfers, df_players = load_data()
    df_detail, df_managers, df_tiers = build_auction_study_data(df_bids, df_transfers, df_players)

    model = BidPredictiveModel()
    model.fit(df_detail)

    is_interactive = (price_str is None)

    # 1. Interactive Inputs if missing
    print("\n" + "=" * 70)
    print("🧮 CALCULADORA INTELIGENTE DE PUJAS BIWENGER")
    print("=" * 70)

    if not price_str:
        price_str = input("💵 Introduce el Precio de Mercado en día de puja D-1 (ej: 3.37M, 3370000): ").strip()
    price_eur = parse_price(price_str)

    if not pos_str:
        pos_str = input("⚽ Introduce la Posición (GK / DF / MF / FW): ").strip().upper()
    else:
        pos_str = pos_str.strip().upper()

    if pos_str not in ['GK', 'DF', 'MF', 'FW']:
        print(f"⚠️ Posición '{pos_str}' no válida. Usando 'MF' por defecto.")
        pos_str = 'MF'

    if bidders_int is None:
        bidders_in = input("⚔️ Pujadores esperados (1: Solo tú | 2: Moderada | 3: Guerra de pujas) [Defecto 2]: ").strip()
        bidders_int = int(bidders_in) if bidders_in.isdigit() else 2

    # Intraday growth calculation
    intraday_pct = 0.0
    intraday_eur = 0.0
    if growth_str is not None:
        g_clean = str(growth_str).strip().replace(',', '.').upper()
        if g_clean.endswith('%'):
            intraday_pct = float(g_clean.replace('%', ''))
            intraday_eur = price_eur * (intraday_pct / 100.0)
        else:
            intraday_eur = parse_price(g_clean)
            intraday_pct = (intraday_eur / price_eur) * 100.0 if price_eur > 0 else 0.0
    elif is_interactive and sys.stdin.isatty():
        try:
            g_in = input("📈 Subida Intradía estimada en ficha (€ o %, ej: +20k, +0.6% o Enter para 0): ").strip()
            if g_in:
                if g_in.endswith('%'):
                    intraday_pct = float(g_in.replace('%', ''))
                    intraday_eur = price_eur * (intraday_pct / 100.0)
                else:
                    intraday_eur = parse_price(g_in)
                    intraday_pct = (intraday_eur / price_eur) * 100.0 if price_eur > 0 else 0.0
        except EOFError:
            intraday_pct = 0.0

    # 2. Perform Calculation
    pred_res = model.predict_bid_amount(price_eur, pos_str, expected_bidders=bidders_int, intraday_growth_pct=intraday_pct)

    # 3. Calculate Scenarios
    scen_1 = model.predict_bid_amount(price_eur, pos_str, expected_bidders=1, intraday_growth_pct=intraday_pct)
    scen_2 = model.predict_bid_amount(price_eur, pos_str, expected_bidders=2, intraday_growth_pct=intraday_pct)
    scen_3 = model.predict_bid_amount(price_eur, pos_str, expected_bidders=3, intraday_growth_pct=intraday_pct)

    # 4. Display Dashboard
    print("\n" + "📊 RESULTADOS DE LA RECOMENDACIÓN DE PUJA".center(70, "="))
    print(f" ⚽ Jugador                 : Posición {pos_str}")
    print(f" 💰 Precio Salida (Día Puja): {format_eur(price_eur)}")
    print(f" 📈 Subida Diaria Ficha     : +{format_eur(intraday_eur)} (+{intraday_pct:.2f}%)")
    print(f" ⚔️ Nivel de Competencia    : {bidders_int} pujador{'es' if bidders_int > 1 else ''}")
    print("-" * 70)
    print(f" 📈 Sobrepuja Recomendada   : +{pred_res['predicted_overbid_pct']:.2f}% (+{format_eur(pred_res['predicted_overbid_eur'])})")
    print(f" 🎯 OFERTA TOTAL RECOMENDADA: 🔥 {format_eur(pred_res['recommended_bid_eur'])} 🔥")
    print("=" * 70)

    # 5. Show Multi-Scenario Table
    print("\n📋 MATRIZ DE ESCENARIOS SEGÚN COMPETENCIA:")
    print(f"  • Conservador (1 Puja)    : {format_eur(scen_1['recommended_bid_eur'])}  (+{scen_1['predicted_overbid_pct']:.1f}%)")
    print(f"  • Moderado (2 Pujas)      : {format_eur(scen_2['recommended_bid_eur'])}  (+{scen_2['predicted_overbid_pct']:.1f}%)")
    print(f"  • Agresivo (3+ Pujas)     : {format_eur(scen_3['recommended_bid_eur'])}  (+{scen_3['predicted_overbid_pct']:.1f}%)")

    # 6. Rival Manager Advice (if manager provided or in TTY)
    if manager_str is None and sys.stdin.isatty():
        try:
            manager_str = input("\n👥 ¿Prevés que puja algún rival en concreto? (escribe nombre o pulsa Enter para omitir): ").strip()
        except EOFError:
            manager_str = ""

    if manager_str:
        match_managers = df_managers[df_managers['Mánager'].str.lower().str.contains(manager_str.lower())]
        if not match_managers.empty:
            mgr_info = match_managers.iloc[0]
            print(f"\n🕵️ ANÁLISIS DE INTELIGENCIA RIVAL ({mgr_info['Mánager']}):")
            print(f"  • Subastas ganadas en liga : {mgr_info['Subastas Ganadas']}")
            print(f"  • Sobrepuja media habitual  : +{mgr_info['Sobrepuja Media s/Salida (%)']:.2f}%")
            print(f"  • Dinero excesivo regalado  : {format_eur(mgr_info['Exceso de Puja s/2ª Opción (€)'])}")

            rival_overbid_eur = price_eur * (mgr_info['Sobrepuja Media s/Salida (%)'] / 100.0)
            suggested_rival_bid = price_eur + rival_overbid_eur
            print(f"  👉 Puja estimada de {mgr_info['Mánager']} : {format_eur(suggested_rival_bid)}")
            if suggested_rival_bid > pred_res['recommended_bid_eur']:
                print(f"  ⚠️ ALERTA: {mgr_info['Mánager']} suele sobrepujar más que la media. Para asegurarlo deberás subir a ~{format_eur(suggested_rival_bid + 10000)}.")
        else:
            print(f"ℹ️ Mánager '{manager_str}' no encontrado en el histórico reciente.")

    print("\n" + "=" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Calculadora Inteligente de Pujas Biwenger.")
    parser.add_argument("--precio", "-p", type=str, help="Precio de mercado en día de puja D-1 (ej: 3.37M, 3370000)")
    parser.add_argument("--posicion", "-pos", type=str, help="Posición táctica: GK, DF, MF, FW")
    parser.add_argument("--pujadores", "-c", type=int, help="Pujadores esperados: 1, 2, 3")
    parser.add_argument("--subida", "-s", type=str, help="Subida intradía estimada (€ o %, ej: +20k, +0.6%)")
    parser.add_argument("--manager", "-m", type=str, help="Nombre del mánager rival esperado")

    args = parser.parse_args()

    # If args passed directly
    if args.precio or args.posicion:
        run_bid_calculator(price_str=args.precio, pos_str=args.posicion, bidders_int=args.pujadores, manager_str=args.manager, growth_str=args.subida)
    else:
        run_bid_calculator()


if __name__ == "__main__":
    main()
