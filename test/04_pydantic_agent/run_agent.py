"""
Test 04: Ejecución del Agente Director Deportivo (Pydantic AI) con Trazabilidad Completa
========================================================================================
Ejecuta el Agente Autónomo Pydantic AI con una petición natural (sin pistas hardcodeadas),
registra el log paso a paso de herramientas invocadas y genera el informe de decisiones.

Uso:
  .venv/bin/python test/04_pydantic_agent/run_agent.py
"""

import sys
import os
import json
import asyncio
import datetime
import pandas as pd

# Añadir raíz del proyecto a sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.agent.pydantic_biwenger_agent import create_biwenger_agent


async def main():
    test_dir = os.path.dirname(os.path.abspath(__file__))
    log_file = os.path.join(test_dir, "04_agent_execution_log.md")
    report_file = os.path.join(test_dir, "04_sporting_director_response.md")

    print("=" * 70)
    print("👔 AGENTE DIRECTOR DEPORTIVO BIWENGER (PYDANTIC AI) — EJECUCIÓN CON LOGS")
    print("=" * 70)

    # Prompt neutral y sin pistas hardcodeadas: el agente debe descubrir todo con sus tools
    prompt_usuario = (
        "Hola, analiza el estado actual de mi plantilla, el informe del entrenador y el mercado. "
        "Tenemos que sanear la deuda antes del inicio de la próxima jornada de mañana sin dejar el equipo "
        "descompensado. Investiga las opciones de venta, explora el mercado en busca de las posiciones "
        "que necesitemos y presenta un plan de acción ejecutivo detallado y justificado."
    )

    print(f"\n👤 SOLICITUD AL AGENTE:\n{prompt_usuario}\n")
    print("-" * 70)
    print("⏳ El Agente está investigando los datos e invocando sus herramientas...\n")

    agent = create_biwenger_agent()
    result = await agent.run(prompt_usuario)

    # 1. Extraer el texto final
    output_text = getattr(result, 'output', getattr(result, 'data', str(result)))

    # 2. Extraer los mensajes / tool calls para generar el log detallado
    log_md = []
    log_md.append("# 📜 LOG DE EJECUCIÓN Y TRAZABILIDAD DEL AGENTE DIRECTOR DEPORTIVO\n")
    log_md.append(f"**Fecha y Hora:** `{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n")
    log_md.append(f"**Prompt del Usuario:**\n> {prompt_usuario}\n")
    log_md.append("---\n")
    log_md.append("## 🛠️ Herramientas Invocadas durante la Investigación\n")

    step_count = 0
    all_msgs = getattr(result, 'all_messages', lambda: [])()
    
    for msg in all_msgs:
        # Check if message has parts with tool calls or tool returns
        parts = getattr(msg, 'parts', [])
        for part in parts:
            part_kind = getattr(part, 'part_kind', str(type(part)))
            
            if 'tool_call' in str(part_kind).lower() or hasattr(part, 'tool_name'):
                step_count += 1
                t_name = getattr(part, 'tool_name', 'tool_call')
                t_args = getattr(part, 'args', {})
                print(f"  🔧 [Paso {step_count}] Invocando Tool: `{t_name}`")
                log_md.append(f"### Paso {step_count}: Tool `{t_name}`")
                log_md.append(f"- **Argumentos:**\n```json\n{json.dumps(t_args, ensure_ascii=False, indent=2) if isinstance(t_args, dict) else str(t_args)}\n```")

            elif 'tool_return' in str(part_kind).lower() or hasattr(part, 'content'):
                t_content = getattr(part, 'content', '')
                t_name = getattr(part, 'tool_name', 'resultado')
                content_str = json.dumps(t_content, ensure_ascii=False, indent=2) if isinstance(t_content, (dict, list)) else str(t_content)
                log_md.append(f"- **Respuesta de `{t_name}`:**\n```json\n{content_str[:1500]}\n```\n")

    log_md.append("---\n")
    log_md.append("## 📋 Veredicto Final Producido por el Agente\n")
    log_md.append(output_text)

    # Guardar archivos
    with open(log_file, "w", encoding="utf-8") as f:
        f.write("\n".join(log_md))

    with open(report_file, "w", encoding="utf-8") as f:
        f.write(f"# 👔 PLAN ESTRATÉGICO DEL DIRECTOR DEPORTIVO\n\n{output_text}")

    print("\n" + "=" * 70)
    print("📋 DICTAMEN FINAL DEL DIRECTOR DEPORTIVO (PYDANTIC AI):")
    print("=" * 70)
    print(output_text)
    print("=" * 70)
    print(f"\n📄 Informe de Decisiones : {report_file}")
    print(f"📜 Log de Ejecución Tool : {log_file}\n")


if __name__ == "__main__":
    asyncio.run(main())
