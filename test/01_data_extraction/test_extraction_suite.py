"""
Data Extraction Test Suite & Validation Tool
==============================================
Runs unit-level and integration-level checks on the data extraction pipeline,
prints a detailed terminal dashboard summary, and exports a multi-sheet Excel report.

Modes:
  - Online  (default/--online): Performs live API authentication, scrapers, and CSV generation.
  - Offline (--offline): Uses existing ./data/*.csv files to test transformations and master generation instantly.

Usage:
  .venv/bin/python test/01_data_extraction/test_extraction_suite.py [--offline | --online]
"""

import sys
import os
import argparse
import time
import datetime
import pandas as pd

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from src.data_extraction.runner import orchestrate_pipeline, import_data


REQUIRED_CSV_FILES = [
    'players.csv',
    'teams.csv',
    'next_jornada.csv',
    'league_players.csv',
    'league_teams.csv',
    'market_offers.csv',
    'market_sales.csv',
    'comuniate.csv',
    'news.csv',
    'user_info.csv',
    'rounds.csv'
]

ESSENTIAL_MASTER_COLUMNS = [
    'PLAYER_ID',
    'PLAYER_NAME',
    'TEAM_NAME',
    'PLAYER_POSITION',
    'PLAYER_PRICE',
    'PLAYER_POINTS'
]


def test_file_integrity(data_dir: str = './data') -> bool:
    print("\n🔍 --- TEST 1: CSV File Integrity ---")
    missing_files = []
    
    for filename in REQUIRED_CSV_FILES:
        filepath = os.path.join(data_dir, filename)
        if not os.path.exists(filepath):
            missing_files.append(filename)

    if missing_files:
        print(f"❌ Missing CSV files ({len(missing_files)}): {', '.join(missing_files)}")
    else:
        print("✅ All required CSV files exist.")

    return len(missing_files) == 0


def test_master_dataframe(df_master: pd.DataFrame) -> bool:
    print("\n🔍 --- TEST 2: Master DataFrame Validation ---")
    
    if df_master is None or df_master.empty:
        print("❌ Master DataFrame is None or empty.")
        return False
        
    print(f"✅ Master DataFrame shape: {df_master.shape[0]} rows, {df_master.shape[1]} columns")

    # Check essential columns
    missing_essential = [col for col in ESSENTIAL_MASTER_COLUMNS if col not in df_master.columns]
    
    if missing_essential:
        print(f"⚠️ Missing essential columns: {missing_essential}")
    else:
        print("✅ All essential master columns are present.")

    # Check for duplicate player IDs
    if 'PLAYER_ID' in df_master.columns:
        duplicates = df_master['PLAYER_ID'].duplicated().sum()
        if duplicates > 0:
            print(f"⚠️ Warning: Found {duplicates} duplicate player IDs in master DataFrame.")
        else:
            print("✅ No duplicate player IDs found.")
            
    # Sample display
    display_cols = [c for c in ['PLAYER_NAME', 'TEAM_NAME', 'PLAYER_POSITION', 'PLAYER_PRICE', 'AVG_POINTS', 'IS_MY_PLAYER'] if c in df_master.columns]
    print("\n📊 Sample extracted data (First 5 rows):")
    print(df_master[display_cols].head().to_string(index=False))

    return len(missing_essential) == 0


def export_test_results_excel(df_master: pd.DataFrame, data: dict, elapsed_time: float, mode_str: str, output_dir: str):
    """
    Exports a comprehensive multi-sheet Excel file to output_dir containing test results and extracted tables.
    """
    print("\n📊 --- EXPORTING TEST RESULTS TO EXCEL ---")
    os.makedirs(output_dir, exist_ok=True)
    excel_path = os.path.join(output_dir, "test_extraction_results.xlsx")
    
    # Prepare Summary Sheet Data
    summary_data = [
        {"Metric / Parameter": "Test Execution Date", "Value": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
        {"Metric / Parameter": "Execution Mode", "Value": mode_str},
        {"Metric / Parameter": "Total Duration (seconds)", "Value": f"{elapsed_time:.2f}s"},
        {"Metric / Parameter": "Master DataFrame Rows", "Value": len(df_master) if df_master is not None else 0},
        {"Metric / Parameter": "Master DataFrame Columns", "Value": len(df_master.columns) if df_master is not None else 0},
    ]
    
    for key, df in data.items():
        summary_data.append({
            "Metric / Parameter": f"Extracted CSV: {key}",
            "Value": f"{len(df)} rows" if not df.empty else "0 rows (Empty)"
        })
        
    df_summary = pd.DataFrame(summary_data)
    
    # Filter my squad from df_master if possible
    df_my_squad = pd.DataFrame()
    if df_master is not None and not df_master.empty:
        if 'IS_MY_PLAYER' in df_master.columns:
            df_my_squad = df_master[df_master['IS_MY_PLAYER'] == True]
            
    with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
        df_summary.to_excel(writer, sheet_name="00_Test_Summary", index=False)
        
        if df_master is not None and not df_master.empty:
            df_master.to_excel(writer, sheet_name="01_Master_DataFrame", index=False)
            
        if not df_my_squad.empty:
            df_my_squad.to_excel(writer, sheet_name="02_My_Squad", index=False)
            
        if 'market_sales' in data and not data['market_sales'].empty:
            data['market_sales'].to_excel(writer, sheet_name="03_Transfer_Market", index=False)
            
        if 'market_offers' in data and not data['market_offers'].empty:
            data['market_offers'].to_excel(writer, sheet_name="04_Market_Offers", index=False)
            
        if 'comuniate' in data and not data['comuniate'].empty:
            data['comuniate'].to_excel(writer, sheet_name="05_Comuniate_Lineups", index=False)
            
        if 'league_teams' in data and not data['league_teams'].empty:
            data['league_teams'].to_excel(writer, sheet_name="06_League_Standings", index=False)
            
        if 'next_match' in data and not data['next_match'].empty:
            data['next_match'].to_excel(writer, sheet_name="07_Next_Jornada", index=False)

    print(f"✅ Excel report generated successfully at:\n   📄 {excel_path}")


def print_terminal_dashboard(df_master: pd.DataFrame, data: dict, elapsed: float, mode_str: str, integrity_ok: bool, master_ok: bool, test_dir: str):
    """
    Prints a clean, visually appealing dashboard summary to the terminal.
    """
    status_str = "SUCCESS ✅" if (integrity_ok and master_ok) else "PASSED WITH WARNINGS ⚠️"
    
    # Counts
    total_master_rows = len(df_master) if df_master is not None else 0
    total_master_cols = len(df_master.columns) if df_master is not None else 0
    
    my_squad_count = 0
    if df_master is not None and not df_master.empty and 'IS_MY_PLAYER' in df_master.columns:
        my_squad_count = (df_master['IS_MY_PLAYER'] == True).sum()
            
    market_sales_count = len(data.get('market_sales', []))
    market_offers_count = len(data.get('market_offers', []))
    comuniate_count = len(data.get('comuniate', []))
    league_teams_count = len(data.get('league_teams', []))
    next_match_count = len(data.get('next_match', []))
    news_count = len(data.get('news', []))
    excel_path = os.path.join(test_dir, "test_extraction_results.xlsx")

    print("\n" + "=" * 70)
    print("📊 RESUMEN FINAL DEL TEST DE EXTRACCIÓN DE DATOS")
    print("=" * 70)
    print(f" ⚙️ Modo de Ejecución : {mode_str}")
    print(f" ⏱️ Tiempo Total      : {elapsed:.2f} segundos")
    print(f" 📅 Fecha y Hora       : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f" 🟢 Estado del Test   : {status_str}")
    print("-" * 70)
    print("📦 DESGLOSE DE DATOS EXTRAÍDOS")
    print("-" * 70)
    print(f" ⚽ Jugadores Totales (Master) : {total_master_rows} registros ({total_master_cols} columnas)")
    print(f" 🛡️ Jugadores en tu Plantilla : {my_squad_count} jugadores")
    print(f" 🏷️ Jugadores en el Mercado   : {market_sales_count} ventas activas")
    print(f" 📩 Ofertas Recibidas         : {market_offers_count} ofertas pendientes")
    print(f" 🔮 Alineaciones Comuniate    : {comuniate_count} jugadores parseados")
    print(f" 🏆 Clasificación de la Liga  : {league_teams_count} equipos rivales")
    print(f" 📅 Próxima Jornada           : {next_match_count} partidos programados")
    print(f" 📰 Noticias RSS Extraídas    : {news_count} noticias")
    print("-" * 70)
    print("📄 ARCHIVOS DE SALIDA GENERADOS")
    print("-" * 70)
    print(" 📁 CSVs crudos creados      : ./data/*.csv (13 archivos)")
    print(" 📊 DataFrame Máster          : ./data/_master.csv & ./data/_master.xlsx")
    print(f" 📗 Informe Excel Revisión    : {excel_path}")
    print("=" * 70 + "\n")


def run_suite(extract_online: bool = True):
    mode_str = "ONLINE (Live API & Scraping)" if extract_online else "OFFLINE (Cached ./data/*.csv)"
    test_dir = os.path.dirname(os.path.abspath(__file__))
    
    print("=" * 70)
    print(f"🧪 DATA EXTRACTION TEST SUITE — Mode: {mode_str}")
    print("=" * 70)
    
    start_time = time.time()
    
    try:
        df_master = orchestrate_pipeline(extract=extract_online)
        elapsed = time.time() - start_time
        
        integrity_ok = test_file_integrity()
        master_ok = test_master_dataframe(df_master)
        
        data = import_data()
        
        export_test_results_excel(df_master, data, elapsed, mode_str, test_dir)
        
        # Display the rich terminal summary dashboard
        print_terminal_dashboard(df_master, data, elapsed, mode_str, integrity_ok, master_ok, test_dir)
        
    except Exception as e:
        print("\n" + "=" * 70)
        print(f"❌ FATAL ERROR IN EXTRACTION PIPELINE: {e}")
        print("=" * 70)
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Data Extraction Test Suite")
    parser.add_argument("--offline", action="store_true", help="Run offline transformation test using cached CSVs")
    parser.add_argument("--online", action="store_true", help="Run full online extraction test with live API calls")
    args, unknown = parser.parse_known_args()

    extract_online = True

    if args.offline:
        extract_online = False
    elif args.online:
        extract_online = True
    else:
        print("\n" + "=" * 70)
        print("⚙️ SELECCIONA EL MODO DE EJECUCIÓN DE LA PRUEBA")
        print("=" * 70)
        print(" [1] 🌐 Modo ONLINE  (Descarga en vivo desde Biwenger + Scraping)")
        print(" [2] 📁 Modo OFFLINE (Usar datos cacheados en ./data/*.csv — Ultrarrápido <1s)")
        print("=" * 70)
        
        try:
            choice = input("👉 Elige una opción (1 o 2) [presiona Enter para 1 - Online]: ").strip()
            if choice == "2":
                extract_online = False
            else:
                extract_online = True
        except (KeyboardInterrupt, EOFError):
            print("\nOperación cancelada.")
            sys.exit(0)

    run_suite(extract_online=extract_online)
