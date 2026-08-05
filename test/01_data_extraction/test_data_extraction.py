"""
Data Extraction Test Runner
============================
Convenience wrapper so running this file directly from VS Code prompts
an interactive menu to choose between Online and Offline modes.
"""

import sys
import os

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from test.01_data_extraction.test_extraction_suite import run_suite

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("⚙️ SELECCIONA EL MODO DE EJECUCIÓN DE LA PRUEBA DE EXTRACCIÓN")
    print("=" * 70)
    print(" [1] 🌐 Modo ONLINE  (Descarga en vivo desde Biwenger + Scraping)")
    print(" [2] 📁 Modo OFFLINE (Usar datos cacheados en ./data/*.csv — Ultrarrápido <1s)")
    print("=" * 70)
    
    extract_online = True
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
