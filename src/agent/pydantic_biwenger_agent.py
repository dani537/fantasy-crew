"""
Agente Director Deportivo Biwenger (Construido con Pydantic AI)
==============================================================
Un agente de inteligencia y toma de decisiones deportivas/financieras 100% dinámico.
Lee todos los datos (usuario, liga, equipo, saldo, plantilla, rivales) desde los archivos
de datos extraídos en tiempo real, SIN NINGÚN VALOR HARDCODEADO.
"""

import os
import sys
import json
import datetime
import pandas as pd
from typing import List, Dict, Optional, Any
from dotenv import load_dotenv

load_dotenv()


def get_agent_model():
    """Configura el modelo LLM para Pydantic AI según las claves del .env."""
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    env_model = os.getenv("LLM_MODEL", "openai/gpt-5.6-luna")

    if openrouter_key:
        os.environ["OPENROUTER_API_KEY"] = openrouter_key
        model_name = env_model if env_model.startswith("openrouter:") else f"openrouter:{env_model}"
        return model_name
    elif os.getenv("DEEPSEEK_API_KEY"):
        return "deepseek:deepseek-chat"
    elif os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        return "google:gemini-2.5-flash"
    else:
        return "openai/gpt-5.6-luna"


def get_active_user_context() -> Dict[str, Any]:
    """
    Lee dinámicamente la información del usuario, equipo, liga y saldo real desde ./data/user_info.csv.
    """
    user_info_path = "./data/user_info.csv"
    if not os.path.exists(user_info_path):
        raise FileNotFoundError(
            f"No se encontró {user_info_path}. Ejecuta primero la extracción de datos (Test 01)."
        )

    df_u = pd.read_csv(user_info_path)
    if df_u.empty:
        raise ValueError(f"El archivo {user_info_path} está vacío.")

    row = df_u.iloc[0]
    return {
        "user_id": int(row.get("user_id", 0)),
        "user_name": str(row.get("user_name", "")),
        "league_id": int(row.get("league_id", 0)),
        "league_name": str(row.get("league_name", "Liga Biwenger")),
        "team_id": int(row.get("team_id", 0)),
        "team_name": str(row.get("team_name", "Mi Equipo")),
        "balance": float(row.get("balance", 0.0))
    }


def create_biwenger_agent():
    """
    Crea y configura el agente Director Deportivo con Pydantic AI de forma 100% dinámica.
    """
    from pydantic_ai import Agent

    user_ctx = get_active_user_context()
    team_name = user_ctx["team_name"]
    league_name = user_ctx["league_name"]
    model_name = get_agent_model()

    system_prompt = f"""
Eres el Director Deportivo y Gestor Estratégico del equipo '{team_name}' en la Liga Biwenger '{league_name}'.
Tu función es tomar decisiones ejecutivas de mercado, compras, ventas y finanzas coordinándote con el Entrenador.

PRINCIPIOS UNIVERSALES DE DECISIÓN ECONÓMICA Y DEPORTIVA:

1. PRINCIPIO DE STOP-LOSS Y ELIMINACIÓN DE ACTIVOS TÓXICOS (NO CAER EN COSTE HUNDIDO):
   - Lo que costó un jugador en el pasado es irrelevante (coste hundido).
   - Si un jugador tiene baja titularidad (0-30%), molestias o suplencia, y su valor está cayendo fuertemente (< -50.000 €/día), es un ACTIVO TÓXICO PRIORITARIO DE VENTA para cortar la sangría patrimonial y reinvertir en un titular fijo.

2. EFICIENCIA DE CAPITAL Y ROI POR POSICIÓN:
   - Inmovilizar grandes sumas de capital (>4.5M€) en posiciones de bajo techo fantasy (ej. pivotes defensivos destructores con bajo xG/xA) mientras otras líneas críticas (defensa o delantera) están en cuadro es una ineficiencia grave.
   - Es preferible liquidar ese activo caro prescindible para sanear la deuda de golpe y liberar presupuesto para reforzar las líneas deficientes.

3. CIRUGÍA QUIRÚRGICA vs DESMANTELAMIENTO DE PLANTILLA:
   - Prioriza siempre resolver la deuda con 1 o 2 ventas quirúrgicas de alto impacto antes que liquidar 3 o 4 jugadores que dejen líneas enteras sin efectivos.
   - El objetivo es maximizar los puntos esperados del Once Titular de la jornada que empieza.
   - Nunca cuentes con jugadores con 'puede_venderse_hoy': False (ej. comprados hoy, con 24h de bloqueo) para liquidar deuda antes del pitido inicial.

4. PUJAS Y NEGOCIACIÓN ASIMÉTRICA CON RIVALES ENDEUDADOS:
   - Al comprar a un rival con saldo negativo (<0€) y necesidad urgente de liquidez antes del deadline, la puja debe ser ajustada al Valor de Mercado (entre el 100% y máximo 105% de su VM). El rival no tiene poder de negociación y necesita vender.

METODOLOGÍA DE EJECUCIÓN:
1. Consulta informe del Entrenador (11 titular y necesidades).
2. Consulta Finanzas y Plantilla (deuda real, bloqueos y ofertas activas).
3. Si vendes activos tóxicos o caros y liberas presupuesto, rastrea el mercado y rivales endeudados para fichar titulares del puesto necesitado.
4. Simula matemáticamente la operación con la tool de simulación para validar viabilidad de venta, saldo >0€ y balance de efectivos por línea.
5. Presenta tu plan con ofertas máximas recomendadas.

SIEMPRE consulta tus herramientas antes de formular tu plan de acción.
"""

    agent = Agent(
        model=model_name,
        system_prompt=system_prompt,
    )

    # =========================================================================
    # HERRAMIENTAS (TOOLS) DEL AGENTE — 100% DINÁMICAS
    # =========================================================================

    @agent.tool_plain
    def consultar_informe_entrenador() -> Dict[str, Any]:
        """
        Consulta el informe táctico más reciente generado por el Entrenador (The Mister):
        alineación recomendada, diagnóstico de líneas, descartes para venta y necesidades de fichaje.
        """
        response_path = "./test/02_coach/02_coach_response.md"
        if not os.path.exists(response_path):
            from src.tools.coach_analytic import run_coach_analytic
            run_coach_analytic()

        if os.path.exists(response_path):
            with open(response_path, "r", encoding="utf-8") as f:
                content = f.read()
            return {
                "estado": "Informe disponible",
                "resumen_informe": content[:2500]
            }
        return {"error": "No se pudo obtener el informe del entrenador."}

    @agent.tool_plain
    def consultar_finanzas_y_plantilla() -> Dict[str, Any]:
        """
        Devuelve el estado económico actual del equipo propio: saldo bancario, deuda a sanear,
        patrimonio, lista de jugadores en plantilla, estado de bloqueo de venta y OFERTAS ACTIVAS en firme.
        """
        ctx = get_active_user_context()
        my_team = ctx["team_name"]
        saldo = ctx["balance"]

        master_path = "./data/players_transformed.csv"
        if not os.path.exists(master_path):
            master_path = "./data/_master.csv"

        jugadores = []
        liquidez_ofertas_activas = 0.0
        valor_plantilla = 0.0

        if os.path.exists(master_path):
            df_m = pd.read_csv(master_path)
            mis_jugadores = df_m[df_m.get("IS_MY_PLAYER", False) == True] if "IS_MY_PLAYER" in df_m.columns else pd.DataFrame()
            if mis_jugadores.empty and "BIWPLAYER_TEAM_NAME" in df_m.columns:
                mis_jugadores = df_m[df_m["BIWPLAYER_TEAM_NAME"] == my_team]

            for _, r in mis_jugadores.iterrows():
                offer_amt = r.get("MARKET_OFFER_AMOUNT")
                has_offer = pd.notna(offer_amt) and float(offer_amt) > 0
                offer_dict = None
                if has_offer:
                    amt = float(offer_amt)
                    liquidez_ofertas_activas += amt
                    offer_dict = {
                        "importe": amt,
                        "de": str(r.get("MARKET_OFFER_FROM_NAME", "Mercado")),
                        "valida_hasta": str(r.get("MARKET_OFFER_UNTIL", "Fin de ciclo"))
                    }

                can_sell = bool(r.get("CAN_SELL_TODAY", True))
                block_reason = str(r.get("SALE_BLOCKED_REASON", "")) if not can_sell else None
                p_price = float(r.get("PLAYER_PRICE", 0))
                valor_plantilla += p_price

                jugadores.append({
                    "id": int(r.get("PLAYER_ID", 0)),
                    "nombre": r.get("PLAYER_NAME"),
                    "posicion": r.get("PLAYER_POSITION"),
                    "precio_mercado": p_price,
                    "subida_24h": float(r.get("PLAYER_PRICE_INCREMENT", 0)),
                    "titular_prob": float(r.get("COMUNIATE_STARTER", 0)),
                    "puede_venderse_hoy": can_sell,
                    "motivo_bloqueo": block_reason,
                    "tiene_oferta_activa": has_offer,
                    "oferta_activa": offer_dict
                })

        patrimonio = saldo + valor_plantilla

        return {
            "equipo": my_team,
            "saldo_bancario_actual": saldo,
            "deuda_a_sanear": abs(saldo) if saldo < 0 else 0.0,
            "valor_plantilla": valor_plantilla,
            "patrimonio_total": patrimonio,
            "total_jugadores": len(jugadores),
            "liquidez_disponible_en_ofertas_activas": liquidez_ofertas_activas,
            "plantilla": jugadores
        }

    @agent.tool_plain
    def buscar_jugadores_en_mercado(
        posicion: Optional[str] = None,
        max_precio: Optional[float] = None,
        min_subida: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        Busca y filtra jugadores disponibles en el mercado de fichajes hoy.
        :param posicion: Filtrar por posición ('GK', 'DF', 'MF', 'FW').
        :param max_precio: Precio máximo de salida en €.
        :param min_subida: Subida diaria mínima en € (+VM).
        """
        master_path = "./data/players_transformed.csv"
        if not os.path.exists(master_path):
            return []

        df_m = pd.read_csv(master_path)
        if "MARKET_SALE_PRICE" not in df_m.columns:
            return []

        en_mercado = df_m[df_m["MARKET_SALE_PRICE"] > 0].copy()

        if posicion:
            en_mercado = en_mercado[en_mercado["PLAYER_POSITION"] == posicion]
        if max_precio:
            en_mercado = en_mercado[en_mercado["MARKET_SALE_PRICE"] <= max_precio]
        if min_subida:
            en_mercado = en_mercado[en_mercado["PLAYER_PRICE_INCREMENT"] >= min_subida]

        oportunidades = []
        for _, r in en_mercado.iterrows():
            vendedor = str(r.get("MARKET_SALE_USER_NAME", "Mercado"))
            oportunidades.append({
                "id": int(r.get("PLAYER_ID", 0)),
                "nombre": r.get("PLAYER_NAME"),
                "equipo": r.get("TEAM_NAME"),
                "posicion": r.get("PLAYER_POSITION"),
                "precio_salida": float(r.get("MARKET_SALE_PRICE", 0)),
                "subida_24h": float(r.get("PLAYER_PRICE_INCREMENT", 0)),
                "titular_prob": float(r.get("COMUNIATE_STARTER", 0)),
                "puntos": float(r.get("PLAYER_POINTS", 0)),
                "vendedor": vendedor,
                "es_de_rival": vendedor not in ("Mercado", "None", "nan")
            })

        oportunidades.sort(key=lambda x: (x["subida_24h"], x["titular_prob"]), reverse=True)
        return oportunidades[:20]

    @agent.tool_plain
    def buscar_jugadores_en_rivales(
        posicion: Optional[str] = None,
        solo_rivales_endeudados: bool = False,
        max_precio: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        Rastrea plantillas de otros mánagers de la liga para encontrar jugadores en venta,
        excedentes de plantilla o activos de rivales con problemas económicos (<0€).
        :param posicion: Filtrar por posición ('GK', 'DF', 'MF', 'FW').
        :param solo_rivales_endeudados: Si es True, solo busca en mánagers con saldo negativo.
        :param max_precio: Precio máximo de mercado del jugador.
        """
        ctx = get_active_user_context()
        my_team = ctx["team_name"]

        master_path = "./data/players_transformed.csv"
        rf_path = "./data/rival_financials.csv"
        if not os.path.exists(master_path):
            return []

        df_m = pd.read_csv(master_path)
        df_rf = pd.read_csv(rf_path) if os.path.exists(rf_path) else pd.DataFrame()

        endeudados_set = set()
        if not df_rf.empty:
            endeudados_df = df_rf[df_rf["saldo_disponible"] < 0]
            endeudados_set = set(endeudados_df["manager"].astype(str).tolist())

        df_rivales = df_m[
            (df_m["BIWPLAYER_TEAM_NAME"].notna()) & 
            (df_m["BIWPLAYER_TEAM_NAME"] != my_team) & 
            (df_m["BIWPLAYER_TEAM_NAME"] != "nan")
        ].copy()

        if posicion:
            df_rivales = df_rivales[df_rivales["PLAYER_POSITION"] == posicion]
        if max_precio:
            df_rivales = df_rivales[df_rivales["PLAYER_PRICE"] <= max_precio]
        if solo_rivales_endeudados:
            df_rivales = df_rivales[df_rivales["BIWPLAYER_TEAM_NAME"].isin(endeudados_set)]

        candidatos = []
        for _, r in df_rivales.iterrows():
            owner = str(r.get("BIWPLAYER_TEAM_NAME"))
            candidatos.append({
                "id": int(r.get("PLAYER_ID", 0)),
                "nombre": r.get("PLAYER_NAME"),
                "equipo_real": r.get("TEAM_NAME"),
                "propietario": owner,
                "propietario_en_deuda": owner in endeudados_set,
                "posicion": r.get("PLAYER_POSITION"),
                "precio": float(r.get("PLAYER_PRICE", 0)),
                "subida_24h": float(r.get("PLAYER_PRICE_INCREMENT", 0)),
                "titular_prob": float(r.get("COMUNIATE_STARTER", 0)),
                "clausula": float(r.get("BIWPLAYER_CLAUSE", 0)) if pd.notna(r.get("BIWPLAYER_CLAUSE")) else 0,
                "en_venta_mercado": pd.notna(r.get("MARKET_SALE_PRICE")) and float(r.get("MARKET_SALE_PRICE")) > 0
            })

        candidatos.sort(key=lambda x: (x["propietario_en_deuda"], x["titular_prob"], x["subida_24h"]), reverse=True)
        return candidatos[:25]

    @agent.tool_plain
    def analizar_jugador_en_detalle(player_id: int) -> Dict[str, Any]:
        """
        Descarga de forma anónima vía API CDN de Biwenger la ficha completa de un jugador:
        curva de valor de temporada (+VM), partidos, minutos jugados, picas, Sofascore,
        previsión Comuniate y viabilidad de cláusula.
        """
        from src.tools.player_detail import fetch_player_detail
        try:
            return fetch_player_detail(player_id=player_id)
        except Exception as e:
            return {"error": f"No se pudo obtener el detalle del jugador {player_id}: {e}"}

    @agent.tool_plain
    def simular_saneamiento_y_once(
        jugadores_a_vender_ids: List[int],
        jugadores_a_fichar_ids: Optional[List[int]] = None
    ) -> Dict[str, Any]:
        """
        Simula matemáticamente el resultado de las ventas y compras propuestas:
        comprueba si los jugadores se pueden vender legalmente hoy, calcula el saldo
        resultante final y evalúa el balance de jugadores disponibles por línea (GK, DF, MF, FW).
        """
        ctx = get_active_user_context()
        my_team = ctx["team_name"]
        saldo_inicial = ctx["balance"]

        master_path = "./data/players_transformed.csv"
        if not os.path.exists(master_path):
            return {"error": "Datos no disponibles."}

        df_m = pd.read_csv(master_path)
        mis_p = df_m[df_m["IS_MY_PLAYER"] == True].copy() if "IS_MY_PLAYER" in df_m.columns else pd.DataFrame()
        if mis_p.empty and "BIWPLAYER_TEAM_NAME" in df_m.columns:
            mis_p = df_m[df_m["BIWPLAYER_TEAM_NAME"] == my_team].copy()

        total_ingresos = 0.0
        ventas_detalle = []
        errores_bloqueo = []

        for p_id in jugadores_a_vender_ids:
            match_row = mis_p[mis_p["PLAYER_ID"] == p_id]
            if match_row.empty:
                errores_bloqueo.append(f"Jugador ID {p_id} no pertenece a tu plantilla.")
                continue

            r = match_row.iloc[0]
            can_sell = bool(r.get("CAN_SELL_TODAY", True))
            p_name = r.get("PLAYER_NAME", f"ID {p_id}")

            if not can_sell:
                errores_bloqueo.append(f"{p_name} (ID {p_id}) TIENE LA VENTA BLOQUEADA HOY: {r.get('SALE_BLOCKED_REASON')}")

            # Si hay oferta activa en firme, usamos el importe exacto; si no, precio de mercado
            offer_amt = r.get("MARKET_OFFER_AMOUNT")
            if pd.notna(offer_amt) and float(offer_amt) > 0:
                income = float(offer_amt)
                tipo_ingreso = "Oferta activa en firme"
            else:
                income = float(r.get("PLAYER_PRICE", 0))
                tipo_ingreso = "Precio mercado (subasta noche)"

            total_ingresos += income
            ventas_detalle.append({
                "id": p_id,
                "nombre": p_name,
                "posicion": r.get("PLAYER_POSITION"),
                "ingreso": income,
                "tipo_ingreso": tipo_ingreso,
                "puede_venderse": can_sell
            })

        total_gastos = 0.0
        compras_detalle = []
        if jugadores_a_fichar_ids:
            for f_id in jugadores_a_fichar_ids:
                f_row = df_m[df_m["PLAYER_ID"] == f_id]
                if not f_row.empty:
                    fr = f_row.iloc[0]
                    cost = float(fr.get("MARKET_SALE_PRICE", fr.get("PLAYER_PRICE", 0)))
                    total_gastos += cost
                    compras_detalle.append({
                        "id": f_id,
                        "nombre": fr.get("PLAYER_NAME"),
                        "posicion": fr.get("PLAYER_POSITION"),
                        "coste": cost
                    })

        saldo_final = saldo_inicial + total_ingresos - total_gastos
        es_saldo_positivo = (saldo_final > 0)
        es_operacion_valida = (len(errores_bloqueo) == 0) and es_saldo_positivo

        # Calcular composición de plantilla restante
        vendidos_set = set(jugadores_a_vender_ids)
        restantes = mis_p[~mis_p["PLAYER_ID"].isin(vendidos_set)]
        conteo_lineas = restantes["PLAYER_POSITION"].value_counts().to_dict()

        return {
            "es_operacion_valida": es_operacion_valida,
            "errores_bloqueo": errores_bloqueo,
            "saldo_inicial": saldo_inicial,
            "total_ingresos_ventas": total_ingresos,
            "total_gastos_compras": total_gastos,
            "saldo_final_resultante": saldo_final,
            "queda_en_positivo": es_saldo_positivo,
            "margen_positivo": saldo_final if es_saldo_positivo else 0.0,
            "ventas_simuladas": ventas_detalle,
            "compras_simuladas": compras_detalle,
            "efectivos_restantes_por_linea": conteo_lineas
        }

    @agent.tool_plain
    def consultar_directivas_manager() -> List[str]:
        """
        Consulta las sugerencias y directivas estratégicas que el Mánager Humano ha introducido en Google Sheets.
        """
        from src.utils.instructions import get_instructions_for_recipient
        return get_instructions_for_recipient(target_role="agent")

    return agent
