"""
Coach Analytic Tool (The Mister)
=================================
Analyzes squad status, optimal formations, player multipositions, goal bonus rules,
and provides structured tactical recommendations and lineup setups.
"""

import os
import sys
import json
import datetime
import pandas as pd
from typing import Dict, Any, Tuple, Optional
from dotenv import load_dotenv

from src.llm_endpoints.client import LLMClient
from src.tools.coach_analytic.prompts import get_coach_analysis_prompt, COACH_SYSTEM_ROLE
from src.tools.coach_analytic.guardrails import compute_squad_needs
from src.tools.coach_analytic.lineup import validate_lineup, order_lineup_for_api
from src.tools.coach_analytic.actions import LineupActions
from src.tools.data_extraction.auth import BiwengerAuth
from src.utils.json_helper import extract_json_from_llm
from src.utils.instructions import format_instructions_for_prompt, get_instructions_for_recipient

COACH_REQUIRED_COLUMNS = [
    'PLAYER_ID',
    'PLAYER_NAME',
    'PLAYER_POSITION',
    'PLAYER_ALT_POSITIONS',
    'PLAYER_PRICE',
    'PLAYER_PRICE_INCREMENT',
    'PLAYER_STATUS',
    'PLAYER_STATUS_INFO',
    'PLAYER_FITNESS',
    'PLAYER_POINTS',
    'AVG_POINTS',
    'AVG_POINTS_HOME',
    'AVG_POINTS_AWAY',
    'TEAM_NAME',
    'NEXT_GAME',
    'NEXT_RIVAL',
    'NEXT_GAME_WIN',
    'COMUNIATE_STARTER',
    'AVG_POINTS_MOMENTUM',
    'MOMENTUM_TREND',
    'EXPECTED_POINTS'
]

class CoachAnalytic:
    """
    Coach Tactical Analytic Tool.
    """
    def __init__(self):
        self.llm = LLMClient()

    @staticmethod
    def get_my_team_name() -> str:
        """Reads the user's team name from ./data/user_info.csv."""
        user_info_path = './data/user_info.csv'
        if os.path.exists(user_info_path):
            df_user = pd.read_csv(user_info_path)
            if not df_user.empty and 'team_name' in df_user.columns:
                return df_user['team_name'].iloc[0]
        raise RuntimeError(f"Could not read user team name from {user_info_path}")

    @staticmethod
    def prepare_prompt_data(df_master: pd.DataFrame, my_team_name: str) -> Tuple[pd.DataFrame, str, str, str]:
        """
        Filters df_master for the user's squad, formats it as Markdown, and builds the full prompt.
        """
        if 'BIWPLAYER_TEAM_NAME' in df_master.columns:
            my_squad = df_master[df_master['BIWPLAYER_TEAM_NAME'] == my_team_name].copy()
        else:
            my_squad = pd.DataFrame()

        if my_squad.empty:
            raise ValueError(f"No players found in master DataFrame for team '{my_team_name}'")

        existing_cols = [c for c in COACH_REQUIRED_COLUMNS if c in my_squad.columns]
        squad_view = my_squad[existing_cols].copy()

        # Consolidate positions
        if 'PLAYER_POSITION' in squad_view.columns and 'PLAYER_ALT_POSITIONS' in squad_view.columns:
            def _merge_pos(row):
                pos = str(row['PLAYER_POSITION']).strip() if pd.notna(row.get('PLAYER_POSITION')) else ""
                alt = str(row['PLAYER_ALT_POSITIONS']).strip() if pd.notna(row.get('PLAYER_ALT_POSITIONS')) else ""
                if alt and alt.lower() != 'nan':
                    parts = [pos] if pos and pos.lower() != 'nan' else []
                    for p in alt.replace('/', ',').split(','):
                        p_clean = p.strip()
                        if p_clean and p_clean not in parts and p_clean.lower() != 'nan':
                            parts.append(p_clean)
                    return ", ".join(parts)
                return pos

            squad_view['PLAYER_POSITION'] = squad_view.apply(_merge_pos, axis=1)
            squad_view.drop(columns=['PLAYER_ALT_POSITIONS'], inplace=True, errors='ignore')

        # Formats for token clarity
        if 'PLAYER_PRICE' in squad_view.columns and 'PLAYER_PRICE_INCREMENT' in squad_view.columns:
            def _format_inc(row):
                inc = row['PLAYER_PRICE_INCREMENT']
                price = row['PLAYER_PRICE']
                if pd.isna(inc) or pd.isna(price):
                    return "+0.00M (0.00%)"
                inc_val = float(inc)
                price_val = float(price)
                inc_m = inc_val / 1_000_000.0
                prev_price = price_val - inc_val
                pct = (inc_val / prev_price * 100.0) if prev_price > 0 else 0.0
                sign = "+" if inc_val >= 0 else ""
                return f"{sign}{inc_m:.2f}M ({sign}{pct:.2f}%)"
            
            squad_view['PLAYER_PRICE_INCREMENT'] = squad_view.apply(_format_inc, axis=1)

        if 'PLAYER_PRICE' in squad_view.columns:
            squad_view['PLAYER_PRICE'] = squad_view['PLAYER_PRICE'].apply(
                lambda v: f"{float(v)/1_000_000.0:.2f}M" if pd.notna(v) else "0.00M"
            )

        squad_summary_md = squad_view.to_markdown(index=False)

        # Context (Dates & Round)
        now_dt = datetime.datetime.now()
        current_time = now_dt.strftime("%Y-%m-%d %H:%M")
        jornada_name = "Jornada 1"
        jornada_start_time = "Unknown"
        time_remaining = "Unknown"

        next_path = './data/next_jornada.csv' if os.path.exists('./data/next_jornada.csv') else './data/raw/next_jornada.csv'
        if os.path.exists(next_path):
            try:
                df_next = pd.read_csv(next_path)
                if not df_next.empty:
                    first_match = df_next.iloc[0]
                    jornada_name = str(first_match.get('jornada') or first_match.get('NEXT_MATCH_JORNADA') or "Jornada 1")
                    if 'fecha' in df_next.columns and pd.notna(first_match.get('fecha')):
                        start_dt = pd.to_datetime(first_match['fecha']).tz_localize(None)
                        jornada_start_time = start_dt.strftime("%Y-%m-%d %H:%M")
                        delta = start_dt - now_dt
                        if delta.total_seconds() > 0:
                            days = delta.days
                            hours, remainder = divmod(delta.seconds, 3600)
                            minutes = remainder // 60
                            time_remaining = f"{days}d {hours}h {minutes}m"
                        else:
                            time_remaining = "Round in progress or passed"
            except Exception:
                pass

        needs = compute_squad_needs(my_squad)
        squad_needs_summary = (
            f"Squad size: {needs['squad_size']} players ({needs['fit_players']} fit). "
            f"By line: {needs['counts']}. {needs['summary']}"
        )

        user_instructions = format_instructions_for_prompt(target_role="coach")

        full_prompt = get_coach_analysis_prompt(
            current_time=current_time,
            jornada_name=jornada_name,
            jornada_start_time=jornada_start_time,
            time_remaining=time_remaining,
            my_team_name=my_team_name,
            squad_summary=squad_summary_md,
            squad_needs_summary=squad_needs_summary,
            user_instructions=user_instructions,
        )

        return my_squad, full_prompt, squad_summary_md, jornada_name

    def analyze(self, df_master: pd.DataFrame) -> Dict[str, Any]:
        """
        Executes tactical analysis on the squad.
        """
        team_name = self.get_my_team_name()
        my_squad, prompt, squad_md, jornada_name = self.prepare_prompt_data(df_master, team_name)

        # Call LLM
        raw_response = self.llm.call(
            prompt=prompt,
            system_role=COACH_SYSTEM_ROLE,
            temperature=0.1
        )

        parsed_json = extract_json_from_llm(raw_response)
        
        # Validation
        if parsed_json and "alineacion_propuesta" in parsed_json:
            alineacion = parsed_json["alineacion_propuesta"]
            valid = validate_lineup(alineacion, my_squad)
            parsed_json["_lineup_valid"] = valid

        return {
            "team_name": team_name,
            "jornada_name": jornada_name,
            "prompt": prompt,
            "raw_response": raw_response,
            "parsed_json": parsed_json,
            "squad_df": my_squad
        }

def format_coach_response_md(
    raw_response: str,
    json_data: dict,
    team_name: str,
    jornada_info: str,
    squad_df: Optional[pd.DataFrame] = None
) -> str:
    """Formats the LLM response into a clean, human-readable Markdown report with player names."""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Fast player lookup dictionary
    player_info_map = {}
    if squad_df is not None and not squad_df.empty and "PLAYER_ID" in squad_df.columns:
        for _, r in squad_df.iterrows():
            try:
                pid = int(r["PLAYER_ID"])
                player_info_map[pid] = r.to_dict()
            except (ValueError, TypeError):
                pass

    md_output = []
    md_output.append("# 📋 INFORME TÁCTICO DEL ENTRENADOR (THE MISTER)")
    md_output.append(f"**Fecha y Hora:** {now_str}  ")
    md_output.append(f"**Equipo:** `{team_name}`  ")
    md_output.append(f"**Jornada:** `{jornada_info}`  ")
    md_output.append("\n---\n")

    if json_data and isinstance(json_data, dict) and "error" not in json_data:
        # 1. Alineación Propuesta
        alineacion = json_data.get('alineacion_propuesta', {})
        formacion = alineacion.get('formacion', 'No especificada')
        titulares = alineacion.get('titulares', [])
        if not titulares:
            titulares = [{'player_id': pid, 'linea': 'LEGACY'} for pid in alineacion.get('id_jugadores_titulares', [])]
        
        md_output.append(f"## ⚽ 1. Alineación Recomendada ({formacion})")
        md_output.append(f"**Formación Táctica:** `{formacion}`  ")
        md_output.append(f"**Titulares Seleccionados ({len(titulares)} jugadores):**\n")

        # Build clean visual table with player names, team, and positions
        md_output.append("| Línea | Jugador | Equipo (TEAM_NAME) | Posición Ficha | ID | Prob. Titular | Estado |")
        md_output.append("| :---: | :--- | :--- | :---: | :---: | :---: | :---: |")

        for t in titulares:
            pid = t.get("player_id")
            linea = t.get("linea", "-")
            name = t.get("nombre")
            
            p_data = player_info_map.get(int(pid)) if pid is not None and int(pid) in player_info_map else {}
            if not name or name == "Nombre Real":
                name = p_data.get("PLAYER_NAME", f"Jugador {pid}")
            
            team = p_data.get("TEAM_NAME", "-")
            pos = p_data.get("PLAYER_POSITION", "-")
            starter = p_data.get("COMUNIATE_STARTER")
            starter_str = f"{int(float(starter)*100)}%" if pd.notna(starter) else "-"
            status = p_data.get("PLAYER_STATUS", "ok")
            status_badge = f"`{status}`" if status == "ok" else f"⚠️ `{status}`"

            # Enrich the titular object in json_data as well if missing name
            t["nombre"] = name

            md_output.append(f"| **{linea}** | **{name}** | {team} | `{pos}` | `{pid}` | {starter_str} | {status_badge} |")
        
        md_output.append("\n")

        # 2. Resumen Plantilla
        briefing = json_data.get('briefing_direccion_deportiva', {})
        resumen = briefing.get('resumen_plantilla', {})
        val_gen = resumen.get('valoracion_general', 'Sin detalles.')
        huecos = resumen.get('huecos_titulares_libres', 0)

        md_output.append(f"## 🛡️ 2. Diagnóstico de la Plantilla")
        md_output.append(f"* **Huecos Titulares Libres / Penalizaciones:** `{huecos}`")
        md_output.append(f"* **Valoración General:**\n  > {val_gen}\n")

        # 2.1 Directivas del Mánager Consideradas
        manager_inst = get_instructions_for_recipient("coach")
        if manager_inst:
            md_output.append("### 🗣️ Directivas del Mánager Integradas (Google Sheets)")
            for inst in manager_inst:
                md_output.append(f"* 📌 *\"{inst}\"*")
            md_output.append("\n")

        # 3. Lista de Ventas / Descartes
        ventas = briefing.get('lista_ventas', [])
        md_output.append(f"## 💸 3. Recomendaciones de Venta ({len(ventas)} jugadores)")
        if ventas:
            for v in ventas:
                v_pid = v.get("id_jugador")
                v_name = v.get("nombre")
                if not v_name and v_pid in player_info_map:
                    v_name = player_info_map[v_pid].get("PLAYER_NAME", f"Jugador {v_pid}")
                md_output.append(f"* **{v_name}** (ID: `{v_pid}`) — Prioridad: `{v.get('prioridad_venta')}`")
                md_output.append(f"  * *Motivo:* {v.get('motivo')}")
        else:
            md_output.append("✅ No se recomiendan ventas en este momento.\n")

        # 4. Necesidades de Fichaje
        necesidades = briefing.get('necesidades_fichaje', [])
        md_output.append(f"\n## 🎯 4. Necesidades de Fichaje ({len(necesidades)})")
        if necesidades:
            for n in necesidades:
                md_output.append(f"* **Posición:** `{n.get('posicion_requerida')}` — Prioridad: `{n.get('prioridad')}`")
        else:
            md_output.append("✅ No hay necesidades urgentes de fichaje.\n")

        # 5. Raw JSON
        md_output.append("\n---\n## 📄 JSON Crudo Generado por el LLM\n")
        md_output.append(f"```json\n{json.dumps(json_data, indent=2, ensure_ascii=False)}\n```\n")
    else:
        md_output.append(f"❌ Error procesando el veredicto del Entrenador:\n```\n{raw_response}\n```")

    return "\n".join(md_output)

def sync_lineup_to_biwenger(alineacion: dict, squad_df: pd.DataFrame) -> dict:
    """
    Synchronizes the proposed starting XI directly to the Biwenger app.
    """
    load_dotenv()
    username = os.getenv("BIWENGER_USERNAME")
    password = os.getenv("BIWENGER_PASSWORD")
    if not username or not password:
        return {"success": False, "message": "Credenciales BIWENGER_USERNAME o BIWENGER_PASSWORD no encontradas en .env"}

    formacion = alineacion.get("formacion")
    if not formacion or squad_df.empty or not validate_lineup(alineacion, squad_df):
        return {"success": False, "message": "La alineación no es válida o no supera la validación de plantilla."}

    player_ids = order_lineup_for_api(alineacion, squad_df)
    try:
        auth = BiwengerAuth(username, password)
        auth.login()
        user_info = auth.get_user_info()
        lineup_action = LineupActions(
            session=auth.get_session(),
            league_id=user_info.league_id,
            user_id=user_info.team_id
        )
        success = lineup_action.set_lineup(formation=formacion, player_ids=player_ids)
        return {
            "success": success,
            "formation": formacion,
            "player_ids": player_ids,
            "user_name": user_info.user_name,
            "team_name": user_info.team_name,
            "message": "Alineación actualizada con éxito en Biwenger." if success else "Error al enviar la alineación a Biwenger."
        }
    except Exception as e:
        return {"success": False, "message": f"Excepción al sincronizar alineación con Biwenger: {e}"}


def run_coach_analytic(
    df_master: Optional[pd.DataFrame] = None,
    output_dir: Optional[str] = None,
    sync_to_biwenger: bool = True
) -> Dict[str, Any]:
    """
    Executes the full Coach Analytic Tool pipeline, writes prompt & response markdown files,
    and automatically synchronizes the validated starting XI with Biwenger.
    """
    if df_master is None:
        master_path = './data/players_transformed.csv' if os.path.exists('./data/players_transformed.csv') else './data/_master.csv'
        if os.path.exists(master_path):
            df_master = pd.read_csv(master_path)
        else:
            from src.tools.data_extraction.runner import orchestrate_pipeline
            df_master = orchestrate_pipeline(extract=False)

    coach = CoachAnalytic()
    result = coach.analyze(df_master)

    # Save to output_dir if provided or default test/02_coach
    target_dir = output_dir or os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../test/02_coach'))
    os.makedirs(target_dir, exist_ok=True)

    prompt_path = os.path.join(target_dir, "01_coach_prompt.md")
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(result["prompt"])

    response_md = format_coach_response_md(
        raw_response=result["raw_response"],
        json_data=result["parsed_json"],
        team_name=result["team_name"],
        jornada_info=result["jornada_name"],
        squad_df=result["squad_df"]
    )

    response_path = os.path.join(target_dir, "02_coach_response.md")
    with open(response_path, "w", encoding="utf-8") as f:
        f.write(response_md)

    result["prompt_file"] = prompt_path
    result["response_file"] = response_path

    # Synchronize with Biwenger if valid and enabled
    sync_res = {"success": False, "message": "Sync skipped"}
    if sync_to_biwenger:
        json_data = result.get("parsed_json", {})
        if json_data.get("_lineup_valid") and "alineacion_propuesta" in json_data:
            alineacion = json_data["alineacion_propuesta"]
            sync_res = sync_lineup_to_biwenger(alineacion, result["squad_df"])
            if sync_res.get("success"):
                print(f"🎉 Alineación ({sync_res.get('formation')}) sincronizada en Biwenger ({sync_res.get('user_name')})")
            else:
                print(f"⚠️ Aviso sincronización Biwenger: {sync_res.get('message')}")
        else:
            sync_res = {"success": False, "message": "Alineación no válida; no se envía a Biwenger."}

    result["sync_result"] = sync_res
    return result
