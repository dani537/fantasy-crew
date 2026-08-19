"""
Biwenger Smart Dashboard & Tactical Hub
======================================
Interactive Streamlit application for squad management, market intelligence,
rival financial radar, Coach The Mister tactics, and Autonomous Pydantic AI Agent.

Run:
  .venv/bin/streamlit run app.py
"""

import os
import sys

# 1. Bridge Streamlit Cloud Secrets into os.environ before any imports
try:
    import streamlit as st
    if hasattr(st, "secrets"):
        for k, v in st.secrets.items():
            if isinstance(v, (str, int, float, bool)):
                os.environ[k] = str(v)
except Exception:
    pass

# Ensure directories exist
os.makedirs("./data/raw", exist_ok=True)
os.makedirs("./test/02_coach", exist_ok=True)
os.makedirs("./test/04_pydantic_agent", exist_ok=True)

import json
import datetime
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# Ensure root in sys.path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Page configuration
st.set_page_config(
    page_title="Biwenger Smart Hub",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .metric-card {
        background-color: #1e2530;
        border: 1px solid #2d3748;
        border-radius: 8px;
        padding: 16px;
        color: white;
    }
    .badge-positive {
        background-color: #065f46;
        color: #34d399;
        padding: 2px 8px;
        border-radius: 4px;
        font-weight: bold;
    }
    .badge-negative {
        background-color: #7f1d1d;
        color: #f87171;
        padding: 2px 8px;
        border-radius: 4px;
        font-weight: bold;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    .stTabs [data-baseweb="tab"] {
        padding: 8px 16px;
        border-radius: 6px 6px 0 0;
    }
</style>
""", unsafe_allow_html=True)


# =============================================================================
# DATA LOADERS (100% Dynamic, Zero Hardcoding)
# =============================================================================

@st.cache_data(ttl=60)
def load_user_info():
    path = "./data/user_info.csv"
    if os.path.exists(path):
        try:
            df = pd.read_csv(path)
            if not df.empty:
                return df.iloc[0].to_dict()
        except Exception:
            pass
    return None

@st.cache_data(ttl=60)
def load_master_players():
    path = "./data/players_transformed.csv"
    if os.path.exists(path):
        try:
            return pd.read_csv(path)
        except Exception:
            pass
    return pd.DataFrame()

@st.cache_data(ttl=60)
def load_rival_financials():
    path = "./data/rival_financials.csv"
    if os.path.exists(path):
        try:
            return pd.read_csv(path)
        except Exception:
            pass
    return pd.DataFrame()

@st.cache_data(ttl=60)
def load_market_offers():
    path = "./data/raw/market_offers.csv"
    if os.path.exists(path):
        try:
            return pd.read_csv(path)
        except Exception:
            pass
    return pd.DataFrame()

@st.cache_data(ttl=60)
def load_next_jornada():
    path = "./data/raw/next_jornada.csv"
    if os.path.exists(path):
        try:
            return pd.read_csv(path)
        except Exception:
            pass
    return pd.DataFrame()

@st.cache_data(ttl=60)
def load_transfers():
    path = "./data/raw/board_transfers.csv"
    if os.path.exists(path):
        try:
            return pd.read_csv(path)
        except Exception:
            pass
    return pd.DataFrame()


# =============================================================================
# SIDEBAR & EXTRACTION TRIGGER
# =============================================================================

st.sidebar.title("⚽ BIWENGER AI HUB")

user_ctx = load_user_info()

if user_ctx:
    my_team_name = str(user_ctx.get("team_name", "Mi Equipo"))
    league_name = str(user_ctx.get("league_name", "Liga Biwenger"))
    balance = float(user_ctx.get("balance", 0.0))
    st.sidebar.markdown(f"**Mánager:** `{my_team_name}`")
    st.sidebar.markdown(f"**Liga:** `{league_name}`")
else:
    my_team_name = "Sin datos"
    league_name = "Sin datos"
    balance = 0.0
    st.sidebar.warning("⚠️ No hay datos cargados todavía.")

# Next matchday info
df_next_j = load_next_jornada()
next_kickoff_str = "Próximamente"
if not df_next_j.empty:
    f_date = df_next_j.iloc[0].get("fecha")
    if pd.notna(f_date):
        try:
            dt = pd.to_datetime(f_date).tz_localize(None)
            next_kickoff_str = dt.strftime("%d/%m %H:%M")
        except Exception:
            pass
st.sidebar.markdown(f"**Próxima Jornada:** `{next_kickoff_str}`")
st.sidebar.markdown("---")

# Extraction Action Button
if st.sidebar.button("🔄 Actualizar Datos (Extracción)", type="primary", use_container_width=True):
    with st.sidebar.status("Conectando con Biwenger y extrayendo datos...", expanded=True) as status:
        try:
            st.write("1. Autenticando con Biwenger...")
            from src.tools.data_extraction.runner import orchestrate_pipeline
            orchestrate_pipeline(extract=True)
            st.cache_data.clear()
            status.update(label="¡Extracción completada con éxito!", state="complete", expanded=False)
            st.rerun()
        except Exception as e:
            status.update(label="Error en la extracción", state="error", expanded=True)
            st.error(f"❌ Error al extraer datos: {e}")

st.sidebar.markdown("---")


# =============================================================================
# TOP KPI BANNER (If Data Available)
# =============================================================================

df_players = load_master_players()
df_rivals = load_rival_financials()

if user_ctx and not df_players.empty:
    my_squad = df_players[df_players["BIWPLAYER_TEAM_NAME"] == my_team_name]
    squad_value = my_squad["PLAYER_PRICE"].sum() if not my_squad.empty else 0.0
    total_equity = balance + squad_value
    squad_count = len(my_squad)
    active_offers_sum = my_squad["MARKET_OFFER_AMOUNT"].dropna().sum() if "MARKET_OFFER_AMOUNT" in my_squad.columns else 0.0

    kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)

    with kpi1:
        balance_color = "normal" if balance >= 0 else "inverse"
        st.metric(
            label="💰 Saldo Bancario",
            value=f"{balance:,.0f} €",
            delta="SALDO POSITIVO ✅" if balance >= 0 else "NÚMEROS ROJOS ⚠️",
            delta_color=balance_color
        )

    with kpi2:
        st.metric(
            label="👥 Valor Plantilla",
            value=f"{squad_value/1_000_000:.2f} M€",
            delta=f"{squad_count} jugadores"
        )

    with kpi3:
        st.metric(
            label="🏦 Patrimonio Total",
            value=f"{total_equity/1_000_000:.2f} M€",
            delta="Capital Total"
        )

    with kpi4:
        st.metric(
            label="📑 Ofertas en Firme",
            value=f"{active_offers_sum/1_000_000:.2f} M€",
            delta="Liquidez Inmediata"
        )

    with kpi5:
        st.metric(
            label="⏳ Próximo Inicio",
            value=next_kickoff_str,
            delta="Deadline Saldo > 0€"
        )

    st.markdown("---")

else:
    st.info("👋 **¡Bienvenido a Biwenger AI Hub!** Pulsa el botón **'🔄 Actualizar Datos (Extracción)'** en la barra lateral izquierda para descargar tu liga y comenzar.")
    my_squad = pd.DataFrame()


# =============================================================================
# MAIN NAVIGATION TABS
# =============================================================================

tab_squad, tab_market, tab_rivals, tab_coach, tab_player_scan, tab_agent = st.tabs([
    "👔 Mi Plantilla & Finanzas",
    "🏪 Mercado de Fichajes",
    "🕵️ Rivales & Números Rojos",
    "⚽ Entrenador (The Mister)",
    "🔍 Ficha e Inteligencia CDN",
    "🤖 Agente Director Deportivo"
])


# -----------------------------------------------------------------------------
# TAB 1: MI PLANTILLA & FINANZAS
# -----------------------------------------------------------------------------
with tab_squad:
    st.subheader("📋 Estado de mi Plantilla y Ofertas Recibidas")

    if not my_squad.empty:
        col_sq1, col_sq2 = st.columns([3, 2])

        with col_sq1:
            display_cols = [
                "PLAYER_ID", "PLAYER_NAME", "PLAYER_POSITION", "PLAYER_PRICE",
                "PLAYER_PRICE_INCREMENT", "COMUNIATE_STARTER", "CAN_SELL_TODAY",
                "MARKET_OFFER_AMOUNT", "BIWPLAYER_CLAUSE"
            ]
            valid_cols = [c for c in display_cols if c in my_squad.columns]
            df_sq_display = my_squad[valid_cols].copy()
            df_sq_display.rename(columns={
                "PLAYER_ID": "ID",
                "PLAYER_NAME": "Jugador",
                "PLAYER_POSITION": "Pos",
                "PLAYER_PRICE": "Valor Mercado (€)",
                "PLAYER_PRICE_INCREMENT": "Subida 24h (€)",
                "COMUNIATE_STARTER": "Titularidad %",
                "CAN_SELL_TODAY": "Vendible Hoy",
                "MARKET_OFFER_AMOUNT": "Oferta Activa (€)",
                "BIWPLAYER_CLAUSE": "Cláusula (€)"
            }, inplace=True)

            if "Titularidad %" in df_sq_display.columns:
                df_sq_display["Titularidad %"] = (df_sq_display["Titularidad %"] * 100).astype(int).astype(str) + "%"

            st.dataframe(
                df_sq_display.style.format({
                    "Valor Mercado (€)": "{:,.0f} €",
                    "Subida 24h (€)": "{:+,.0f} €",
                    "Oferta Activa (€)": lambda x: f"{x:,.0f} €" if pd.notna(x) else "-",
                    "Cláusula (€)": lambda x: f"{x:,.0f} €" if pd.notna(x) else "-"
                }),
                use_container_width=True,
                height=450
            )

        with col_sq2:
            st.markdown("### 🧮 Simulador de Saneamiento en Vivo")
            st.write("Selecciona los jugadores que planeas vender para ver el saldo final:")

            options_map = {}
            for _, r in my_squad.iterrows():
                p_id = int(r["PLAYER_ID"])
                p_name = str(r["PLAYER_NAME"])
                p_price = float(r["PLAYER_PRICE"])
                p_offer = float(r["MARKET_OFFER_AMOUNT"]) if pd.notna(r.get("MARKET_OFFER_AMOUNT")) else p_price
                can_sell = bool(r.get("CAN_SELL_TODAY", True))
                lock_tag = "" if can_sell else " 🔒 (BLOQUEADO HOY)"
                options_map[f"{p_name} ({p_offer:,.0f} €){lock_tag}"] = (p_id, p_offer, can_sell, p_name)

            selected_labels = st.multiselect(
                "Jugadores a Vender:",
                options=list(options_map.keys()),
                default=[]
            )

            sim_income = 0.0
            blocked_selected = []
            for label in selected_labels:
                p_id, p_amt, can_sell, p_name = options_map[label]
                if not can_sell:
                    blocked_selected.append(p_name)
                sim_income += p_amt

            sim_final_balance = balance + sim_income

            st.markdown("---")
            st.metric("Ingresos Totales por Ventas", f"+{sim_income:,.0f} €")
            
            if sim_final_balance >= 0:
                st.success(f"✅ **Saldo Resultante: +{sim_final_balance:,.0f} €** (Cumple la regla > 0€)")
            else:
                st.error(f"⚠️ **Saldo Resultante: {sim_final_balance:,.0f} €** (Faltan {abs(sim_final_balance):,.0f} €)")

            if blocked_selected:
                st.warning(f"🚨 **Atención:** Has seleccionado jugadores con venta bloqueada hoy: **{', '.join(blocked_selected)}**")

    else:
        st.info("No hay datos de plantilla disponibles. Pulsa '🔄 Actualizar Datos (Extracción)'.")


# -----------------------------------------------------------------------------
# TAB 2: MERCADO DE FICHAJES
# -----------------------------------------------------------------------------
with tab_market:
    st.subheader("🏪 Mercado de Fichajes y Subastas Activas")

    if not df_players.empty and "MARKET_SALE_PRICE" in df_players.columns:
        mkt_players = df_players[df_players["MARKET_SALE_PRICE"] > 0].copy()

        f_col1, f_col2, f_col3 = st.columns(3)
        with f_col1:
            pos_filter = st.multiselect("Posición:", options=["GK", "DF", "MF", "FW"], default=["GK", "DF", "MF", "FW"])
        with f_col2:
            seller_filter = st.selectbox("Tipo de Vendedor:", options=["Todos", "Solo Mercado (Computer)", "Solo Rivales"])
        with f_col3:
            max_price_mkt = st.slider("Precio Máximo (€):", min_value=150_000, max_value=25_000_000, value=25_000_000, step=250_000)

        if pos_filter:
            mkt_players = mkt_players[mkt_players["PLAYER_POSITION"].isin(pos_filter)]
        if seller_filter == "Solo Mercado (Computer)":
            mkt_players = mkt_players[mkt_players["MARKET_SALE_USER_NAME"].isin(["Mercado", "None", None]) | mkt_players["MARKET_SALE_USER_NAME"].isna()]
        elif seller_filter == "Solo Rivales":
            mkt_players = mkt_players[~mkt_players["MARKET_SALE_USER_NAME"].isin(["Mercado", "None", None]) & mkt_players["MARKET_SALE_USER_NAME"].notna()]
        mkt_players = mkt_players[mkt_players["MARKET_SALE_PRICE"] <= max_price_mkt]

        mkt_cols = [
            "PLAYER_ID", "PLAYER_NAME", "TEAM_NAME", "PLAYER_POSITION",
            "MARKET_SALE_PRICE", "PLAYER_PRICE_INCREMENT", "COMUNIATE_STARTER",
            "MARKET_SALE_USER_NAME"
        ]
        valid_mkt_cols = [c for c in mkt_cols if c in mkt_players.columns]
        df_mkt_view = mkt_players[valid_mkt_cols].copy()
        df_mkt_view.rename(columns={
            "PLAYER_ID": "ID",
            "PLAYER_NAME": "Jugador",
            "TEAM_NAME": "Equipo",
            "PLAYER_POSITION": "Pos",
            "MARKET_SALE_PRICE": "Precio Salida (€)",
            "PLAYER_PRICE_INCREMENT": "Subida 24h (€)",
            "COMUNIATE_STARTER": "Titularidad %",
            "MARKET_SALE_USER_NAME": "Vendedor"
        }, inplace=True)

        if "Titularidad %" in df_mkt_view.columns:
            df_mkt_view["Titularidad %"] = (df_mkt_view["Titularidad %"] * 100).astype(int).astype(str) + "%"

        st.dataframe(
            df_mkt_view.sort_values(by="Subida 24h (€)", ascending=False).style.format({
                "Precio Salida (€)": "{:,.0f} €",
                "Subida 24h (€)": "{:+,.0f} €"
            }),
            use_container_width=True,
            height=400
        )

        df_transfers = load_transfers()
        if not df_transfers.empty:
            st.markdown("### 📜 Últimos Movimientos y Fichajes de la Liga")
            st.dataframe(df_transfers.head(10), use_container_width=True)

    else:
        st.info("No hay datos de mercado disponibles. Pulsa '🔄 Actualizar Datos (Extracción)'.")


# -----------------------------------------------------------------------------
# TAB 3: RIVALES & RADAR DE NÚMEROS ROJOS
# -----------------------------------------------------------------------------
with tab_rivals:
    st.subheader("🕵️ Radar Financiero y Plantillas de Rivales")

    if not df_rivals.empty:
        r_col1, r_col2 = st.columns([3, 2])

        with r_col1:
            st.markdown("#### 🏆 Clasificación Financiera de la Liga")
            df_rf_view = df_rivals[[
                "posicion_liga", "manager", "saldo_disponible", "patrimonio_total",
                "num_jugadores", "fichajes", "ventas"
            ]].copy()
            df_rf_view.rename(columns={
                "posicion_liga": "Pos",
                "manager": "Mánager",
                "saldo_disponible": "Saldo Estimado (€)",
                "patrimonio_total": "Patrimonio (€)",
                "num_jugadores": "Jugadores",
                "fichajes": "Compras",
                "ventas": "Ventas"
            }, inplace=True)

            st.dataframe(
                df_rf_view.style.format({
                    "Saldo Estimado (€)": "{:,.0f} €",
                    "Patrimonio (€)": "{:,.0f} €"
                }),
                use_container_width=True,
                height=420
            )

        with r_col2:
            st.markdown("#### 🚨 Rivales en Números Rojos (< 0€)")
            in_debt = df_rivals[df_rivals["saldo_disponible"] < 0].copy()

            if not in_debt.empty:
                for _, r_row in in_debt.iterrows():
                    m_name = r_row["manager"]
                    m_saldo = float(r_row["saldo_disponible"])
                    m_players = int(r_row["num_jugadores"])
                    st.error(f"🔴 **{m_name}** | Deuda: **{m_saldo:,.0f} €** ({m_players} jugadores)")
            else:
                st.success("No hay rivales en números rojos actualmente.")

            st.markdown("---")
            st.markdown("#### 🔍 Explorar Plantilla de un Rival")
            selected_manager = st.selectbox(
                "Selecciona un Mánager:",
                options=[m for m in df_rivals["manager"].unique() if m != my_team_name]
            )

            if selected_manager and not df_players.empty:
                rival_squad = df_players[df_players["BIWPLAYER_TEAM_NAME"] == selected_manager]
                st.dataframe(
                    rival_squad[["PLAYER_NAME", "PLAYER_POSITION", "PLAYER_PRICE", "COMUNIATE_STARTER", "BIWPLAYER_CLAUSE"]].style.format({
                        "PLAYER_PRICE": "{:,.0f} €",
                        "BIWPLAYER_CLAUSE": "{:,.0f} €"
                    }),
                    use_container_width=True
                )
    else:
        st.info("No hay datos de rivales disponibles. Pulsa '🔄 Actualizar Datos (Extracción)'.")


# -----------------------------------------------------------------------------
# TAB 4: ENTRENADOR (THE MISTER)
# -----------------------------------------------------------------------------
with tab_coach:
    st.subheader("⚽ Análisis Táctico del Entrenador (The Mister)")

    c_btn1, c_btn2 = st.columns([1, 4])
    with c_btn1:
        if st.button("🚀 Ejecutar Coach en Vivo", use_container_width=True):
            with st.spinner("El Coach está analizando el 11 y sincronizando con Biwenger..."):
                from src.tools.coach_analytic import run_coach_analytic
                run_coach_analytic()
                st.success("¡Análisis táctico completado y alineación sincronizada!")
                st.rerun()

    coach_response_path = "./test/02_coach/02_coach_response.md"
    if os.path.exists(coach_response_path):
        with open(coach_response_path, "r", encoding="utf-8") as f:
            coach_text = f.read()
        st.markdown(coach_text)
    else:
        st.info("No hay informe del entrenador disponible. Pulsa 'Ejecutar Coach en Vivo'.")


# -----------------------------------------------------------------------------
# TAB 5: FICHA E INTELIGENCIA CDN (DEEP SCAN)
# -----------------------------------------------------------------------------
with tab_player_scan:
    st.subheader("🔍 Ficha y Curva de Inteligencia Cuantitativa (Extracción Anónima)")

    scan_col1, scan_col2 = st.columns([2, 1])

    with scan_col1:
        if not df_players.empty:
            player_choices = {f"{r['PLAYER_NAME']} ({r.get('TEAM_NAME', 'Sin Equipo')}) - ID: {r['PLAYER_ID']}": int(r['PLAYER_ID']) for _, r in df_players.iterrows()}
            selected_choice = st.selectbox("Selecciona un jugador de LaLiga:", options=list(player_choices.keys()), index=0)
            target_player_id = player_choices[selected_choice]
        else:
            target_player_id = st.number_input("ID del Jugador en Biwenger:", value=5697, step=1)

    with scan_col2:
        st.write("")
        st.write("")
        run_scan = st.button("🔬 Analizar Ficha Vía CDN", use_container_width=True)

    if run_scan or st.session_state.get("last_scanned_id") == target_player_id:
        st.session_state["last_scanned_id"] = target_player_id
        with st.spinner(f"Consultando CDN anónimo de Biwenger para ID {target_player_id}..."):
            from src.tools.player_detail import fetch_player_detail, format_player_detail_md
            try:
                p_data = fetch_player_detail(target_player_id)
                md_report = format_player_detail_md(p_data)
                st.markdown(md_report)
            except Exception as e:
                st.error(f"Error al obtener los datos del jugador: {e}")


# -----------------------------------------------------------------------------
# TAB 6: AGENTE DIRECTOR DEPORTIVO (PYDANTIC AI)
# -----------------------------------------------------------------------------
with tab_agent:
    st.subheader("🤖 Agente Autónomo Director Deportivo (Pydantic AI)")
    st.write("El Agente investigará tus finanzas, informe del Coach, mercado y rivales para presentarte un Plan Ejecutivo de Saneamiento y Fichajes.")

    ag_btn1, ag_btn2 = st.columns([1, 4])
    with ag_btn1:
        run_agent_btn = st.button("🧠 Ejecutar Agente Director Deportivo", type="primary", use_container_width=True)

    if run_agent_btn:
        with st.spinner("El Agente Pydantic AI está consultando sus herramientas e investigando la estrategia óptima..."):
            import asyncio
            from src.agent.pydantic_biwenger_agent import create_biwenger_agent

            agent = create_biwenger_agent()
            prompt = (
                "Hola, analiza el estado actual de mi plantilla, el informe del entrenador y el mercado. "
                "Tenemos que sanear la deuda antes del inicio de la próxima jornada de mañana sin dejar el equipo "
                "descompensado. Investiga las opciones de venta, explora el mercado en busca de las posiciones "
                "que necesitemos y presenta un plan de acción ejecutivo detallado y justificado."
            )
            
            result = asyncio.run(agent.run(prompt))
            output_text = getattr(result, 'output', getattr(result, 'data', str(result)))

            report_path = "./test/04_pydantic_agent/04_sporting_director_response.md"
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(f"# 👔 PLAN ESTRATÉGICO DEL DIRECTOR DEPORTIVO\n\n{output_text}")

            st.success("¡Plan de Dirección Deportiva generado!")
            st.markdown(output_text)

    else:
        agent_report_path = "./test/04_pydantic_agent/04_sporting_director_response.md"
        if os.path.exists(agent_report_path):
            with open(agent_report_path, "r", encoding="utf-8") as f:
                saved_text = f.read()
            st.markdown(saved_text)
        else:
            st.info("Pulsa 'Ejecutar Agente Director Deportivo' para iniciar la simulación.")

    log_path = "./test/04_pydantic_agent/04_agent_execution_log.md"
    if os.path.exists(log_path):
        with st.expander("📜 Ver Log de Trazabilidad y Herramientas Invocadas por el Agente"):
            with open(log_path, "r", encoding="utf-8") as f:
                log_content = f.read()
            st.markdown(log_content)
