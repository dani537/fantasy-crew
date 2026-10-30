"""
Test 02: Coach Analytic Tool Test
==================================
Executes the Coach Analytic Tool, validates tactical squad analysis,
and saves the review documents (01_coach_prompt.md and 02_coach_response.md).

Usage:
  .venv/bin/python test/02_coach/run.py
"""

import os
import sys
import time

# Add project root to sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.tools.coach_analytic import run_coach_analytic


def main():
    test_dir = os.path.dirname(os.path.abspath(__file__))

    print("=" * 70)
    print("🧪 COACH AGENT TEST — (The Mister)")
    print("=" * 70)

    start_time = time.time()
    
    # Run the Coach Analytic Tool
    result = run_coach_analytic(output_dir=test_dir)
    
    elapsed = time.time() - start_time
    json_data = result.get("parsed_json", {})
    is_valid = json_data.get("_lineup_valid", False)
    formation = json_data.get("alineacion_propuesta", {}).get("formacion", "N/A")

    sync_result = result.get("sync_result", {})
    sync_status = "SUCCESS ✅" if sync_result.get("success") else f"NOT SYNCED ({sync_result.get('message', 'N/A')})"

    print("\n" + "=" * 70)
    print("📊 RESUMEN DEL TEST DEL ENTRENADOR (THE MISTER)")
    print("=" * 70)
    print(f" ⚽ Equipo Analizado     : {result.get('team_name')}")
    print(f" ⏱️ Tiempo Total         : {elapsed:.2f} segundos")
    print(f" 🛡️ Jugadores Plantilla  : {len(result.get('squad_df', []))} jugadores")
    print(f" 📋 Formación Propuesta  : {formation}")
    print(f" 🟢 Validación Táctica   : {'SUCCESS ✅' if is_valid else 'FAILED ❌'}")
    print(f" 🚀 Sincro en Biwenger   : {sync_status}")
    print("-" * 70)
    print("📄 ARCHIVOS GENERADOS")
    print("-" * 70)
    print(f" 📄 Prompt Guardado (MD) : {result.get('prompt_file')}")
    print(f" 📋 Veredicto Coach (MD) : {result.get('response_file')}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
