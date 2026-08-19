"""
Test 03: Player Detail Extraction Test (Mariano Díaz - Alavés)
==============================================================
Tests anonymous extraction of full player profile, market value temporal trends,
and match performance via public Biwenger CDN.

Usage:
  .venv/bin/python test/03_player_detail/run.py [player_id]
  (Defaults to Mariano: 5697)
"""

import os
import sys
import json
import time

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.tools.player_detail import fetch_player_detail, format_player_detail_md


def main():
    player_id = int(sys.argv[1]) if len(sys.argv) > 1 else 5697  # Default: Mariano (Alavés)
    test_dir = os.path.dirname(os.path.abspath(__file__))

    print("=" * 70)
    print(f"🔍 TEST 03 — EXTRACCIÓN ANÓNIMA DE FICHA DE JUGADOR (ID: {player_id})")
    print("=" * 70)

    start_time = time.time()
    
    # 1. Fetch data anonymously from Biwenger CDN
    data = fetch_player_detail(player_id=player_id)
    elapsed = time.time() - start_time

    # 2. Format human-readable markdown
    md_content = format_player_detail_md(data)

    # 3. Save to markdown file in test directory
    safe_slug = data.get("perfil", {}).get("slug", f"player_{player_id}")
    report_file = os.path.join(test_dir, f"03_{safe_slug}_report.md")
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(md_content)

    perfil = data.get("perfil", {})
    vm = data.get("valor_mercado", {})
    rend = data.get("rendimiento", {})
    nxt = data.get("proximo_partido", {})
    liga = data.get("situacion_liga", {})

    p_curr = vm.get("precio_actual", 0)
    p_inc = vm.get("incremento_diario_24h", 0)
    inc_sign = "+" if p_inc >= 0 else ""

    print(f" ⏱️ Tiempo de Respuesta CDN : {elapsed:.2f} segundos")
    print(f" 👤 Jugador                : {perfil.get('nombre')} ({perfil.get('equipo')})")
    print(f" 🎯 Posición               : {perfil.get('posicion')}")
    print(f" 💰 Valor de Mercado       : {p_curr:,.0f} € ({inc_sign}{p_inc:,.0f} € / 24h)")
    print(f" 📊 Rendimiento            : {rend.get('puntos_totales')} pts ({rend.get('media_puntos')} pts/partido en {rend.get('partidos_jugados')} PJ)")
    print(f" ⚽ Goles / Asistencias    : {rend.get('goles')} Goles / {rend.get('asistencias')} Asistencias")
    print(f" 🔜 Próximo Partido        : {nxt.get('partido', 'N/A')} ({nxt.get('condicion', '-')})")
    if liga:
        print(f" 🏆 Propietario en Liga    : {liga.get('propietario')} (Cláusula: {liga.get('clausula_actual', 0):,.0f} €)")
    
    print("-" * 70)
    print("📈 VARIACIONES TEMPORALES DE COTIZACIÓN:")
    print("-" * 70)
    for k, v in vm.get("variaciones_temporales", {}).items():
        diff_sign = "+" if v.get("diff", 0) >= 0 else ""
        print(f"  • {k:10s}: Anterior: {v.get('past_price', 0):10,d} € | Dif: {diff_sign}{v.get('diff', 0):10,d} € ({diff_sign}{v.get('pct', 0.0):.2f}%)")
    
    print("=" * 70)
    print(f"📄 Informe completo generado en:\n   {report_file}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
