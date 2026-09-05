"""
Fantasy BI — Executive Tactical & Financial Hub
================================================
Modern, clean dashboard for Biwenger squad analytics, financial tracking,
market intelligence, and tactical decision support.
"""

import os
import sys
import datetime
import numpy as np
import pandas as pd
import streamlit as st

# Ensure root in sys.path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Bridge Streamlit Cloud Secrets into os.environ
try:
    if hasattr(st, "secrets"):
        for k, v in st.secrets.items():
            if isinstance(v, (str, int, float, bool)):
                os.environ[k] = str(v)
except Exception:
    pass

# Ensure directories exist
os.makedirs("./data/raw", exist_ok=True)

# -----------------------------------------------------------------------------
# PAGE CONFIGURATION & THEME-ADAPTIVE CLEAN STYLING
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Fantasy BI · Hub",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    /* Prevent Streamlit top header toolbar from cutting off top cards */
    .block-container {
        padding-top: 4.2rem !important;
        padding-bottom: 2rem !important;
        max-width: 98% !important;
    }

    /* Clean, spacious metric cards that never cut off text */
    [data-testid="stMetric"] {
        background-color: var(--secondary-background-color);
        border: 1px solid rgba(128, 128, 128, 0.18);
        border-radius: 10px;
        padding: 12px 16px !important;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
        overflow: visible !important;
    }
    [data-testid="stMetricLabel"] {
        font-size: 13px !important;
        font-weight: 600 !important;
        letter-spacing: 0.3px;
        color: var(--text-color);
        opacity: 0.88;
        overflow: visible !important;
        line-height: 1.5 !important;
    }
    [data-testid="stMetricLabel"] > div {
        overflow: visible !important;
        line-height: 1.5 !important;
    }
    [data-testid="stMetricValue"] {
        font-size: 24px !important;
        font-weight: 700 !important;
        line-height: 1.3 !important;
        overflow: visible !important;
        padding: 2px 0 !important;
    }
    [data-testid="stMetricDelta"] {
        font-size: 12px !important;
        font-weight: 600 !important;
        overflow: visible !important;
    }

    /* Roster & league status pill strip */
    .roster-summary-bar {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        align-items: center;
        margin-top: 4px;
        margin-bottom: 12px;
    }
    .roster-pill {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 12px;
        font-weight: 500;
        background: var(--secondary-background-color);
        border: 1px solid rgba(128, 128, 128, 0.2);
        color: var(--text-color);
    }
</style>
""", unsafe_allow_html=True)


# =============================================================================
# =============================================================================
# DATA LOADERS (LOCAL CSV WITH GOOGLE SHEETS CLOUD FALLBACK)
# =============================================================================

def _get_sheets_client():
    """Resolves gspread client from st.secrets or local credentials."""
    try:
        import gspread
        if hasattr(st, "secrets") and "GOOGLE_SERVICE_ACCOUNT_JSON" in st.secrets:
            import json
            creds_dict = json.loads(st.secrets["GOOGLE_SERVICE_ACCOUNT_JSON"])
            return gspread.service_account_from_dict(creds_dict)
        creds_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or "./credentials_google.json"
        if os.path.exists(creds_file):
            return gspread.service_account(filename=creds_file)
    except Exception:
        pass
    return None

@st.cache_data(ttl=1800)
def load_all_sheets_db():
    """Reads all operational sheets from fantasy_tracker in one cached pass."""
    gc = _get_sheets_client()
    if not gc:
        return {}
    sheet_id = os.getenv("GOOGLE_SHEET_ID") or "1V3lDapPrpGgLGVl-rvNi3Ishy70dAo22UEn24toa4kk"
    try:
        sh = gc.open_by_key(sheet_id)
        data = {}
        target_worksheets = [
            "Saldos_Estimados", "Mi_Plantilla", "Estado_Usuario",
            "Mercado_Fichajes", "Predicciones_IA", "Primas_Jornadas", "Movimientos"
        ]
        for ws in sh.worksheets():
            if ws.title in target_worksheets:
                records = ws.get_all_records()
                data[ws.title] = pd.DataFrame(records)
        return data
    except Exception:
        return {}

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
    # Fallback to Google Sheets
    sheets = load_all_sheets_db()
    df_u = sheets.get("Estado_Usuario", pd.DataFrame())
    if not df_u.empty:
        r = df_u.iloc[0].to_dict()
        saldo_val = str(r.get("Saldo_Real_Disponible", 0)).replace("€", "").replace(".", "").replace(",", ".").strip()
        try:
            bal_num = float(saldo_val)
        except Exception:
            bal_num = 0.0
        return {
            "team_name": r.get("Manager", "Dani SR"),
            "league_name": "Liga Biwenger",
            "balance": bal_num
        }
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
    # Fallback to Google Sheets
    sheets = load_all_sheets_db()
    return sheets.get("Saldos_Estimados", pd.DataFrame())

@st.cache_data(ttl=60)
def load_predictions():
    path = "./data/predictions/predicciones_mercado_hoy.csv"
    if os.path.exists(path):
        try:
            return pd.read_csv(path)
        except Exception:
            pass
    # Fallback to Google Sheets
    sheets = load_all_sheets_db()
    return sheets.get("Predicciones_IA", pd.DataFrame())

@st.cache_data(ttl=60)
def load_next_jornada():
    path = "./data/raw/next_jornada.csv"
    if os.path.exists(path):
        try:
            return pd.read_csv(path)
        except Exception:
            pass
    return pd.DataFrame()


# =============================================================================
# DATA INITIALIZATION & CALCULATIONS
# =============================================================================

user_ctx = load_user_info()
df_players = load_master_players()
df_rivals = load_rival_financials()
df_pred = load_predictions()
df_next_j = load_next_jornada()

# User Context Defaults
if user_ctx:
    my_team_name = str(user_ctx.get("team_name", "Mi Equipo"))
    league_name = str(user_ctx.get("league_name", "Liga Biwenger"))
    balance = float(user_ctx.get("balance", 0.0))
else:
    my_team_name = "Sin Datos"
    league_name = "Liga Biwenger"
    balance = 0.0

# Next Matchday Info
next_kickoff_str = "Próximamente"
countdown_str = "Fecha por confirmar"
if not df_next_j.empty:
    f_date = df_next_j.iloc[0].get("fecha")
    if pd.notna(f_date):
        try:
            dt = pd.to_datetime(f_date).tz_localize(None)
            next_kickoff_str = dt.strftime("%d/%m %H:%M")
            now_dt = datetime.datetime.now()
            diff = dt - now_dt
            if diff.total_seconds() > 0:
                d = diff.days
                h, rem = divmod(diff.seconds, 3600)
                m = rem // 60
                countdown_str = f"En {d}d {h}h" if d > 0 else f"En {h}h {m}m"
            else:
                countdown_str = "En juego"
        except Exception:
            pass

# Squad Slicing & Financial Aggregates
if not df_players.empty and "BIWPLAYER_TEAM_NAME" in df_players.columns:
    my_squad = df_players[df_players["BIWPLAYER_TEAM_NAME"] == my_team_name].copy()
else:
    my_squad = pd.DataFrame()

squad_value = float(my_squad["PLAYER_PRICE"].sum()) if not my_squad.empty else 0.0
squad_count = len(my_squad)
total_equity = balance + squad_value
squad_day_gain = float(my_squad["PLAYER_PRICE_INCREMENT"].fillna(0).sum()) if not my_squad.empty else 0.0
squad_day_pct = (squad_day_gain / (squad_value - squad_day_gain) * 100.0) if (squad_value - squad_day_gain) > 0 else 0.0
active_offers_sum = float(my_squad["MARKET_OFFER_AMOUNT"].dropna().sum()) if (not my_squad.empty and "MARKET_OFFER_AMOUNT" in my_squad.columns) else 0.0
active_offers_count = int(my_squad["MARKET_OFFER_AMOUNT"].dropna().count()) if (not my_squad.empty and "MARKET_OFFER_AMOUNT" in my_squad.columns) else 0


# =============================================================================
# COMMON VALUE FORMATTERS
# =============================================================================

def format_price(val):
    """Formats numeric euro values into M€ (2 decimals) or K€."""
    if pd.isna(val) or val is None:
        return "—"
    try:
        v = float(val)
        if v == 0:
            return "0 €"
        sign = "-" if v < 0 else ""
        av = abs(v)
        if av >= 1_000_000:
            return f"{sign}{av / 1_000_000:.2f} M€"
        elif av >= 1_000:
            return f"{sign}{av / 1_000:.0f} K€"
        else:
            return f"{sign}{av:,.0f} €"
    except (ValueError, TypeError):
        return "—"


# =============================================================================
# SIDEBAR (CLEAN, ELEGANT, UNBOXED)
# =============================================================================

with st.sidebar:
    st.markdown("## ⚽ Fantasy BI")
    st.caption("Executive Tactical & Financial Hub")
    st.markdown("")

    st.markdown(f"👤 **Mánager:** `{my_team_name}`")
    st.markdown(f"🏆 **Liga:** `{league_name}`")
    st.markdown(f"⏳ **Próx. Jornada:** `{next_kickoff_str}`")
    st.caption(f"Cuenta atrás: **{countdown_str}**")

    st.markdown("---")

    if st.button("🔄 Sincronizar Datos (Biwenger)", type="primary", width="stretch"):
        with st.status("Conectando con Biwenger y actualizando datos...", expanded=True) as status:
            try:
                st.write("• Extrayendo información de la liga y mercado...")
                from src.tools.data_extraction.runner import orchestrate_pipeline
                orchestrate_pipeline(extract=True)
                st.cache_data.clear()
                status.update(label="¡Actualización completada!", state="complete", expanded=False)
                st.rerun()
            except Exception as e:
                status.update(label="Error en la sincronización", state="error", expanded=True)
                st.error(f"Detalle del error: {e}")

    st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)

    with st.expander("⚙️ Estado del Sistema", expanded=False):
        from src.config import _get_config_var
        tok = bool(_get_config_var("BIWENGER_TOKEN"))
        u = bool(_get_config_var("BIWENGER_USERNAME"))
        k = bool(_get_config_var("OPENROUTER_API_KEY") or _get_config_var("DEEPSEEK_API_KEY") or _get_config_var("LLM_API_KEY"))
        
        st.write(f"• Token Biwenger: {'✅ Activo' if tok else ('✅ Usuario/Pass' if u else '❌ Falta')}")
        st.write(f"• Conector IA: {'✅ Conectado' if k else '❌ Falta API Key'}")
        st.write(f"• Base de datos: {len(df_players)} jugadores")


# =============================================================================
# HEADER: 5 EXECUTIVE KPIS (FULL VISIBILITY, ZERO CUTOFF)
# =============================================================================

kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)

with kpi1:
    bal_fmt = f"{balance/1_000_000:.2f} M€" if abs(balance) >= 1_000_000 else f"{balance:,.0f} €"
    st.metric(
        label="💰 Saldo en Cuenta",
        value=bal_fmt,
        delta="SALDO POSITIVO" if balance >= 0 else "NÚMEROS ROJOS",
        delta_color="normal" if balance >= 0 else "inverse"
    )

with kpi2:
    st.metric(
        label="👥 Valor Plantilla",
        value=f"{squad_value/1_000_000:.2f} M€",
        delta=f"{squad_count} jugadores",
        delta_color="off"
    )

with kpi3:
    st.metric(
        label="🏦 Patrimonio Total",
        value=f"{total_equity/1_000_000:.2f} M€",
        delta="Capital Neto",
        delta_color="off"
    )

with kpi4:
    gain_val_str = f"{squad_day_gain/1_000:+.0f} K€" if abs(squad_day_gain) < 1_000_000 else f"{squad_day_gain/1_000_000:+.2f} M€"
    st.metric(
        label="📈 Evolución Hoy",
        value=gain_val_str,
        delta=f"{squad_day_pct:+.2f}% en 24h",
        delta_color="normal" if squad_day_gain >= 0 else "inverse"
    )

with kpi5:
    st.metric(
        label="📑 Ofertas en Firme",
        value=f"{active_offers_sum/1_000_000:.2f} M€",
        delta=f"{active_offers_count} ofertas activas",
        delta_color="normal" if active_offers_count > 0 else "off"
    )

st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)


# =============================================================================
# CINTA DE SELECTORES (NAVIGATION TABS)
# =============================================================================

tab_squad, tab_rivals, tab_players = st.tabs([
    "👔 Mi Plantilla",
    "🏆 Rivales y Liga",
    "🔍 Mercado y Jugadores"
])


# =============================================================================
# TAB 1: MI PLANTILLA (DESARROLLADA)
# =============================================================================

with tab_squad:
    if my_squad.empty:
        st.info("ℹ️ No se han detectado jugadores en tu plantilla. Pulsa **'🔄 Sincronizar Datos'** en la barra lateral para descargar los datos de Biwenger.")
    else:
        # Enrich squad DataFrame
        df_view = my_squad.copy()

        # Position Formatter with Alt-positions
        pos_icons = {"GK": "🧤 POR", "DF": "🛡️ DEF", "MF": "⚙️ MED", "FW": "⚡ DEL"}
        pos_rank = {"GK": 0, "DF": 1, "MF": 2, "FW": 3}

        def _format_pos(row):
            base = str(row.get("PLAYER_POSITION", ""))
            alt = str(row.get("PLAYER_ALT_POSITIONS", ""))
            label = pos_icons.get(base, base)
            if alt and alt != "nan" and alt.strip():
                label += f" ({alt.strip()})"
            return label

        df_view["Pos"] = df_view.apply(_format_pos, axis=1)
        df_view["_pos_order"] = df_view["PLAYER_POSITION"].map(pos_rank).fillna(9)

        # Status Formatter
        def _format_status(s):
            s_clean = str(s).lower()
            if s_clean == "ok": return "🟢 Apto"
            if s_clean in ("doubt", "duda"): return "🟡 Duda"
            if s_clean in ("injured", "lesionado"): return "🔴 Baja"
            if s_clean in ("suspended", "sanctioned", "sancionado"): return "🔴 Sancionado"
            return f"⚪ {s}"

        df_view["Estado"] = df_view["PLAYER_STATUS"].apply(_format_status)

        # Starter probability (0 to 100 integer for ProgressColumn)
        df_view["_raw_starter"] = pd.to_numeric(df_view["COMUNIATE_STARTER"], errors="coerce").fillna(0.0)
        df_view["Titularidad"] = (df_view["_raw_starter"] * 100).astype(int)

        # League Points Percentile Formatter (e.g., 98%, 94%, 89%)
        def _format_percentile(row):
            p = row.get("PERCENTILE")
            if pd.isna(p) or p is None:
                return "—"
            try:
                pval = int(round(float(p) * 100))
                return f"{pval}%"
            except (ValueError, TypeError):
                return "—"

        df_view["Percentil"] = df_view.apply(_format_percentile, axis=1)
        df_view["_raw_percentile"] = pd.to_numeric(df_view.get("PERCENTILE", 0.0), errors="coerce").fillna(0.0)

        # Recent Fitness Formatter (last 3 matches)
        def _format_fitness(fit_val):
            if pd.isna(fit_val) or not str(fit_val).strip():
                return "—"
            s = str(fit_val).replace("[", "").replace("]", "").replace("'", "").replace('"', "")
            parts = [p.strip() for p in s.split(",") if p.strip()]
            clean_parts = [p if p.lower() != "none" else "-" for p in parts[-3:]]
            return " · ".join(clean_parts) if clean_parts else "—"

        df_view["Racha Reciente"] = df_view["PLAYER_FITNESS"].apply(_format_fitness)

        # Raw numeric fields for precise sorting
        df_view["_raw_price"] = pd.to_numeric(df_view["PLAYER_PRICE"], errors="coerce").fillna(0).astype(float)
        df_view["_raw_inc"] = pd.to_numeric(df_view.get("PLAYER_PRICE_INCREMENT", 0), errors="coerce").fillna(0).astype(float)
        df_view["_raw_points"] = pd.to_numeric(df_view.get("PLAYER_POINTS", 0), errors="coerce").fillna(0).astype(int)
        df_view["_raw_offer"] = pd.to_numeric(df_view.get("MARKET_OFFER_AMOUNT"), errors="coerce")

        # Increment percentage & Trend formatter
        def _format_trend(row):
            inc = row.get("PLAYER_PRICE_INCREMENT")
            price = row.get("PLAYER_PRICE")
            if pd.isna(inc) or inc is None or inc == 0:
                return "🟡 0.0%"
            try:
                inc_val = float(inc)
                price_val = float(price) if pd.notna(price) else 0.0
                prev = price_val - inc_val
                pct = (inc_val / prev * 100.0) if prev > 0 else 0.0
                if inc_val > 0:
                    return f"🟢 +{pct:.1f}%"
                else:
                    return f"🔴 {pct:.1f}%"
            except (ValueError, TypeError):
                return "🟡 0.0%"

        df_view["Valor (M€)"] = (df_view["_raw_price"] / 1_000_000).round(2)
        df_view["Subida 24h (K€)"] = (df_view["_raw_inc"] / 1_000).round().astype(int)
        df_view["Tendencia"] = df_view.apply(_format_trend, axis=1)
        df_view["Oferta (M€)"] = df_view["_raw_offer"].apply(lambda o: round(o / 1_000_000, 2) if pd.notna(o) and o > 0 else np.nan)
        df_view["Cláusula (M€)"] = pd.to_numeric(df_view.get("BIWPLAYER_CLAUSE"), errors="coerce").apply(lambda c: round(c / 1_000_000, 2) if pd.notna(c) and c > 0 else np.nan)
        df_view["Percentil"] = (df_view["_raw_percentile"] * 100).round().astype(int)

        # Clear Green/Red Sellable Indicator (🟢 Sí / 🔴 Bloqueado)
        def _format_sellable(row):
            can_sell = bool(row.get("CAN_SELL_TODAY", True))
            return "🟢 Sí" if can_sell else "🔴 Bloqueado"

        df_view["Vendible"] = df_view.apply(_format_sellable, axis=1)
        df_view["Puntos"] = df_view["_raw_points"]
        df_view["Media"] = pd.to_numeric(df_view.get("AVG_POINTS", 0.0), errors="coerce").fillna(0.0)

        # ---------------------------------------------------------------------
        # ROSTER MINI-STATUS SUMMARY STRIP
        # ---------------------------------------------------------------------
        gk_c = int((df_view["PLAYER_POSITION"] == "GK").sum())
        df_c = int((df_view["PLAYER_POSITION"] == "DF").sum())
        mf_c = int((df_view["PLAYER_POSITION"] == "MF").sum())
        fw_c = int((df_view["PLAYER_POSITION"] == "FW").sum())
        fit_c = int((df_view["PLAYER_STATUS"] == "ok").sum())
        injured_c = squad_count - fit_c

        st.markdown(f"""
        <div class="roster-summary-bar">
            <div class="roster-pill">
                <span>📋 <b>{squad_count} Jugadores:</b></span>
                <span>🧤 {gk_c} POR · 🛡️ {df_c} DEF · ⚙️ {mf_c} MED · ⚡ {fw_c} DEL</span>
            </div>
            <div class="roster-pill">
                <span>🏥 <b>Disponibilidad:</b></span>
                <span style="color: #10b981;">🟢 {fit_c} Aptos</span>
                {"· <span style='color: #ef4444;'>🔴 " + str(injured_c) + " Baja</span>" if injured_c > 0 else ""}
            </div>
            <div class="roster-pill">
                <span>📑 <b>Ofertas en Firme:</b></span>
                <span style="color: #0284c7;">{active_offers_count} jugadores ({active_offers_sum/1_000_000:.2f} M€)</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # ---------------------------------------------------------------------
        # FILTERS & SORTING CONTROLS (4 COMPACT COLUMNS)
        # ---------------------------------------------------------------------
        f1, f2, f3, f4 = st.columns([1.3, 1.6, 1.8, 1.8])

        with f1:
            pos_filter = st.selectbox(
                "Línea Táctica:",
                options=["Todas las líneas", "🧤 Porteros (GK)", "🛡️ Defensas (DF)", "⚙️ Medios (MF)", "⚡ Delanteros (FW)"],
                index=0
            )

        with f2:
            status_filter = st.selectbox(
                "Filtro Rápido:",
                options=[
                    "Todos los jugadores",
                    "Titulares probables (≥60%)",
                    "Dudas o incidencias",
                    "Con oferta activa en firme",
                    "En subida hoy (+€)",
                    "En bajada hoy (-€)"
                ],
                index=0
            )

        with f3:
            sort_choice = st.selectbox(
                "Ordenar por:",
                options=[
                    "💰 Mayor Valor de Mercado",
                    "📈 Mayor Subida 24h",
                    "📉 Mayor Caída 24h (Stop-Loss)",
                    "⚽ Mayor Titularidad (%)",
                    "⭐ Más Puntos Fantasy",
                    "📊 Mayor Percentil Puntos",
                    "📋 Posición (POR → DEF → MED → DEL)"
                ],
                index=0
            )

        with f4:
            search_query = st.text_input("🔍 Buscar:", placeholder="Jugador o Club...")

        # Apply Filtering
        filtered_df = df_view.copy()

        # Position filter
        if pos_filter == "🧤 Porteros (GK)":
            filtered_df = filtered_df[filtered_df["PLAYER_POSITION"] == "GK"]
        elif pos_filter == "🛡️ Defensas (DF)":
            filtered_df = filtered_df[filtered_df["PLAYER_POSITION"] == "DF"]
        elif pos_filter == "⚙️ Medios (MF)":
            filtered_df = filtered_df[filtered_df["PLAYER_POSITION"] == "MF"]
        elif pos_filter == "⚡ Delanteros (FW)":
            filtered_df = filtered_df[filtered_df["PLAYER_POSITION"] == "FW"]

        # Situation filter
        if status_filter == "Titulares probables (≥60%)":
            filtered_df = filtered_df[filtered_df["_raw_starter"] >= 0.60]
        elif status_filter == "Dudas o incidencias":
            filtered_df = filtered_df[filtered_df["PLAYER_STATUS"] != "ok"]
        elif status_filter == "Con oferta activa en firme":
            filtered_df = filtered_df[filtered_df["_raw_offer"].notna() & (filtered_df["_raw_offer"] > 0)]
        elif status_filter == "En subida hoy (+€)":
            filtered_df = filtered_df[filtered_df["_raw_inc"] > 0]
        elif status_filter == "En bajada hoy (-€)":
            filtered_df = filtered_df[filtered_df["_raw_inc"] < 0]

        # Search query
        if search_query.strip():
            q = search_query.strip().lower()
            filtered_df = filtered_df[
                filtered_df["PLAYER_NAME"].str.lower().str.contains(q, na=False) |
                filtered_df["TEAM_NAME"].str.lower().str.contains(q, na=False)
            ]

        # Apply Sorting with Mathematical Accuracy
        if sort_choice == "💰 Mayor Valor de Mercado":
            filtered_df = filtered_df.sort_values(by="_raw_price", ascending=False)
        elif sort_choice == "📈 Mayor Subida 24h":
            filtered_df = filtered_df.sort_values(by="_raw_inc", ascending=False)
        elif sort_choice == "📉 Mayor Caída 24h (Stop-Loss)":
            filtered_df = filtered_df.sort_values(by="_raw_inc", ascending=True)
        elif sort_choice == "⚽ Mayor Titularidad (%)":
            filtered_df = filtered_df.sort_values(by="_raw_starter", ascending=False)
        elif sort_choice == "⭐ Más Puntos Fantasy":
            filtered_df = filtered_df.sort_values(by="_raw_points", ascending=False)
        elif sort_choice == "📊 Mayor Percentil Puntos":
            filtered_df = filtered_df.sort_values(by="_raw_percentile", ascending=False)
        elif sort_choice == "📋 Posición (POR → DEF → MED → DEL)":
            filtered_df = filtered_df.sort_values(by=["_pos_order", "_raw_price"], ascending=[True, False])

        # ---------------------------------------------------------------------
        # MAIN SQUAD TABLE (FULL WIDTH, RICH CONFIG)
        # ---------------------------------------------------------------------
        display_cols = [
            "PLAYER_NAME", "TEAM_NAME", "Pos", "Estado", "Titularidad",
            "Puntos", "Percentil", "Media", "Racha Reciente",
            "Valor (M€)", "Subida 24h (K€)", "Tendencia", "Oferta (M€)", "Cláusula (M€)",
            "Vendible"
        ]

        st.dataframe(
            filtered_df[display_cols],
            column_config={
                "PLAYER_NAME": st.column_config.TextColumn("Jugador", help="Nombre del futbolista", width="medium"),
                "TEAM_NAME": st.column_config.TextColumn("Club", help="Equipo de LaLiga", width="small"),
                "Pos": st.column_config.TextColumn("Posición", help="Posición táctica y alternativas", width="small"),
                "Estado": st.column_config.TextColumn("Estado", help="Disponibilidad física", width="small"),
                "Titularidad": st.column_config.ProgressColumn(
                    "Titularidad",
                    help="Probabilidad estimada de once inicial según Comuniate",
                    format="%d%%",
                    min_value=0,
                    max_value=100,
                    width="small"
                ),
                "Puntos": st.column_config.NumberColumn("Pts", help="Puntos totales acumulados", format="%d", width="small"),
                "Percentil": st.column_config.NumberColumn(
                    "Percentil Pts",
                    help="Percentil de puntos en LaLiga (0% a 100%, ej: 98% = Top 2% del campeonato)",
                    format="%d%%",
                    width="small"
                ),
                "Media": st.column_config.NumberColumn("Media", help="Media de puntos por partido", format="%.2f", width="small"),
                "Racha Reciente": st.column_config.TextColumn("Racha Reciente", help="Puntos de las últimas jornadas", width="small"),
                "Valor (M€)": st.column_config.NumberColumn("Valor (M€)", help="Precio oficial en Millones de Euros", format="%.2f M€", width="small"),
                "Subida 24h (K€)": st.column_config.NumberColumn("Subida 24h (K€)", help="Variación en 24h en Miles de Euros", format="%+d K€", width="small"),
                "Tendencia": st.column_config.TextColumn("Tendencia", help="Semáforo y porcentaje de variación 24h", width="small"),
                "Oferta (M€)": st.column_config.NumberColumn("Oferta (M€)", help="Oferta recibida en firme en Millones de Euros", format="%.2f M€", width="small"),
                "Cláusula (M€)": st.column_config.NumberColumn("Cláusula (M€)", help="Cláusula de rescisión en Millones de Euros", format="%.2f M€", width="small"),
                "Vendible": st.column_config.TextColumn(
                    "Vendible Hoy",
                    help="Indica si el jugador se puede vender hoy (🟢 Sí) o si la venta está bloqueada (🔴 Bloqueado)",
                    width="small"
                )
            },
            width="stretch",
            height=540,
            hide_index=True
        )

        st.caption(f"Mostrando **{len(filtered_df)}** de **{squad_count}** futbolistas.")


# =============================================================================
# TAB 2: RIVALES Y LIGA (DESARROLLADA)
# =============================================================================

with tab_rivals:
    if df_rivals.empty:
        st.info("ℹ️ No hay datos financieros de rivales disponibles. Pulsa **'🔄 Sincronizar Datos'** en la barra lateral.")
    else:
        rf_view = df_rivals.copy()

        # Numeric sanitization for sorting
        rf_view["_raw_pos"] = pd.to_numeric(rf_view["posicion_liga"], errors="coerce").fillna(99).astype(int)
        rf_view["_raw_points"] = pd.to_numeric(rf_view["puntos"], errors="coerce").fillna(0).astype(int)
        rf_view["_raw_saldo"] = pd.to_numeric(rf_view["saldo_disponible"], errors="coerce").fillna(0).astype(float)
        rf_view["_raw_squad"] = pd.to_numeric(rf_view["valor_plantilla"], errors="coerce").fillna(0).astype(float)
        rf_view["_raw_equity"] = pd.to_numeric(rf_view["patrimonio_total"], errors="coerce").fillna(0).astype(float)
        rf_view["_raw_maxbid"] = pd.to_numeric(rf_view.get("max_puja_posible", 0), errors="coerce").fillna(0).astype(float)
        rf_view["_raw_ops"] = pd.to_numeric(rf_view.get("fichajes", 0), errors="coerce").fillna(0) + pd.to_numeric(rf_view.get("ventas", 0), errors="coerce").fillna(0)

        # Highlight Manager
        rf_view["Mánager"] = rf_view["manager"].apply(lambda m: f"⭐ {m} (Tú)" if m == my_team_name else m)
        rf_view["Pos"] = rf_view["_raw_pos"].astype(int)
        rf_view["Puntos"] = rf_view["_raw_points"]
        rf_view["Plantilla"] = pd.to_numeric(rf_view["num_jugadores"], errors="coerce").fillna(0).astype(int)

        # Numeric currency fields (in M€) for mathematical sorting
        rf_view["Saldo (M€)"] = (rf_view["_raw_saldo"] / 1_000_000).round(2)
        rf_view["Plantilla (M€)"] = (rf_view["_raw_squad"] / 1_000_000).round(2)
        rf_view["Patrimonio (M€)"] = (rf_view["_raw_equity"] / 1_000_000).round(2)
        rf_view["Puja Máx. (M€)"] = (rf_view["_raw_maxbid"] / 1_000_000).round(2)

        # Threat Formatter
        def _fmt_threat(val):
            t = str(val).upper().strip()
            if "ALTA" in t: return "🚨 ALTA"
            if "MEDIA" in t: return "🟡 MEDIA"
            return "🟢 BAJA"

        rf_view["Amenaza Cláusula"] = rf_view.get("amenaza_clausulazo", "BAJA").apply(_fmt_threat)

        # Transactions
        fich = pd.to_numeric(rf_view.get("fichajes", 0), errors="coerce").fillna(0).astype(int)
        vent = pd.to_numeric(rf_view.get("ventas", 0), errors="coerce").fillna(0).astype(int)
        rf_view["Compras / Ventas"] = fich.astype(str) + " / " + vent.astype(str)

        # ---------------------------------------------------------------------
        # RIVAL SUMMARY PILLS
        # ---------------------------------------------------------------------
        leader = rf_view.sort_values(by="_raw_pos", ascending=True).iloc[0]["manager"]
        richest_mgr = rf_view.sort_values(by="_raw_saldo", ascending=False).iloc[0]
        max_eq_mgr = rf_view.sort_values(by="_raw_equity", ascending=False).iloc[0]

        st.markdown(f"""
        <div class="roster-summary-bar">
            <div class="roster-pill">
                <span>🏆 <b>Líder de la Liga:</b></span>
                <span>{leader} ({rf_view['_raw_points'].max()} pts)</span>
            </div>
            <div class="roster-pill">
                <span>💰 <b>Mayor Liquidez:</b></span>
                <span style="color: #10b981;">{richest_mgr['manager']} ({format_price(richest_mgr['_raw_saldo'])})</span>
            </div>
            <div class="roster-pill">
                <span>🏦 <b>Mayor Patrimonio:</b></span>
                <span>{max_eq_mgr['manager']} ({format_price(max_eq_mgr['_raw_equity'])})</span>
            </div>
            <div class="roster-pill">
                <span>👥 <b>Mánagers:</b></span>
                <span>{len(rf_view)} equipos</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # ---------------------------------------------------------------------
        # RIVAL FILTERS & SORTING
        # ---------------------------------------------------------------------
        rf_col1, rf_col2, rf_col3 = st.columns([1.8, 1.8, 2.0])

        with rf_col1:
            r_sort = st.selectbox(
                "Ordenar Clasificación por:",
                options=[
                    "🏆 Posición en Liga (Puntos)",
                    "💰 Mayor Saldo Disponible (Cash)",
                    "👥 Mayor Valor de Plantilla",
                    "🏦 Mayor Patrimonio Total",
                    "⚔️ Mayor Puja Máxima Posible",
                    "🚨 Mayor Amenaza de Cláusula",
                    "🔄 Más Movimientos de Mercado"
                ],
                index=0
            )

        with rf_col2:
            r_filter = st.selectbox(
                "Filtrar Rivales:",
                options=[
                    "Todos los mánagers",
                    "🚨 Amenaza de Cláusula ALTA",
                    "💰 Con más de 3 M€ en caja",
                    "⚠️ En números rojos (< 0€)"
                ],
                index=0
            )

        with rf_col3:
            r_search = st.text_input("🔍 Buscar Mánager:", placeholder="Ej: Joan GM, Ponceneta, Dani...")

        # Apply Filters
        filtered_rf = rf_view.copy()

        if r_filter == "🚨 Amenaza de Cláusula ALTA":
            filtered_rf = filtered_rf[filtered_rf["Amenaza Cláusula"].str.contains("ALTA")]
        elif r_filter == "💰 Con más de 3 M€ en caja":
            filtered_rf = filtered_rf[filtered_rf["_raw_saldo"] >= 3_000_000]
        elif r_filter == "⚠️ En números rojos (< 0€)":
            filtered_rf = filtered_rf[filtered_rf["_raw_saldo"] < 0]

        if r_search.strip():
            q_r = r_search.strip().lower()
            filtered_rf = filtered_rf[filtered_rf["manager"].str.lower().str.contains(q_r, na=False)]

        # Apply Sorting
        if r_sort == "🏆 Posición en Liga (Puntos)":
            filtered_rf = filtered_rf.sort_values(by="_raw_pos", ascending=True)
        elif r_sort == "💰 Mayor Saldo Disponible (Cash)":
            filtered_rf = filtered_rf.sort_values(by="_raw_saldo", ascending=False)
        elif r_sort == "👥 Mayor Valor de Plantilla":
            filtered_rf = filtered_rf.sort_values(by="_raw_squad", ascending=False)
        elif r_sort == "🏦 Mayor Patrimonio Total":
            filtered_rf = filtered_rf.sort_values(by="_raw_equity", ascending=False)
        elif r_sort == "⚔️ Mayor Puja Máxima Posible":
            filtered_rf = filtered_rf.sort_values(by="_raw_maxbid", ascending=False)
        elif r_sort == "🚨 Mayor Amenaza de Cláusula":
            threat_prio = {"🚨 ALTA": 0, "🟡 MEDIA": 1, "🟢 BAJA": 2}
            filtered_rf["_th_prio"] = filtered_rf["Amenaza Cláusula"].map(threat_prio).fillna(9)
            filtered_rf = filtered_rf.sort_values(by=["_th_prio", "_raw_saldo"], ascending=[True, False])
        elif r_sort == "🔄 Más Movimientos de Mercado":
            filtered_rf = filtered_rf.sort_values(by="_raw_ops", ascending=False)

        # ---------------------------------------------------------------------
        # MAIN RIVALS TABLE
        # ---------------------------------------------------------------------
        rf_display_cols = [
            "Pos", "Mánager", "Puntos", "Plantilla", "Saldo (M€)",
            "Plantilla (M€)", "Patrimonio (M€)", "Puja Máx. (M€)",
            "Amenaza Cláusula", "Compras / Ventas"
        ]

        st.dataframe(
            filtered_rf[rf_display_cols],
            column_config={
                "Pos": st.column_config.NumberColumn("Pos", help="Posición en la tabla clasificatoria", format="%dº", width="small"),
                "Mánager": st.column_config.TextColumn("Mánager", help="Nombre del equipo en la liga", width="medium"),
                "Puntos": st.column_config.NumberColumn("Puntos", help="Puntos oficiales acumulados", format="%d", width="small"),
                "Plantilla": st.column_config.NumberColumn("Jugadores", help="Futbolistas en plantilla", format="%d", width="small"),
                "Saldo (M€)": st.column_config.NumberColumn("Saldo Disp. (M€)", help="Liquidez estimada en cuenta corriente en M€", format="%.2f M€", width="small"),
                "Plantilla (M€)": st.column_config.NumberColumn("Valor Plantilla (M€)", help="Valor de mercado conjunto del equipo en M€", format="%.2f M€", width="small"),
                "Patrimonio (M€)": st.column_config.NumberColumn("Patrimonio Total (M€)", help="Capital neto (Saldo + Plantilla) en M€", format="%.2f M€", width="small"),
                "Puja Máx. (M€)": st.column_config.NumberColumn("Puja Máx. (M€)", help="Límite máximo que pueden pujar en una subasta hoy en M€", format="%.2f M€", width="small"),
                "Amenaza Cláusula": st.column_config.TextColumn("Amenaza Cláusula", help="Riesgo de que paguen cláusulas a rivales (basado en saldo y límite)", width="small"),
                "Compras / Ventas": st.column_config.TextColumn("Compras / Ventas", help="Transacciones realizadas en la liga", width="small")
            },
            width="stretch",
            height=460,
            hide_index=True
        )

        st.caption(f"Mostrando **{len(filtered_rf)}** de **{len(rf_view)}** mánagers de la liga.")

        # ---------------------------------------------------------------------
        # ESPIAR PLANTILLA Y CLÁUSULAS DE UN RIVAL
        # ---------------------------------------------------------------------
        st.markdown("<div style='height: 14px;'></div>", unsafe_allow_html=True)
        st.markdown("#### 🔍 Espiar Plantilla y Cláusulas de un Rival")

        rival_options = [m for m in df_rivals["manager"].unique() if m != my_team_name]
        if rival_options and not df_players.empty:
            selected_rival = st.selectbox(
                "Selecciona un mánager para analizar sus jugadores y cláusulas de rescisión:",
                options=rival_options,
                index=0
            )

            if selected_rival:
                r_squad = df_players[df_players["BIWPLAYER_TEAM_NAME"] == selected_rival].copy()
                if not r_squad.empty:
                    # Enrich rival squad
                    r_squad["_raw_price"] = pd.to_numeric(r_squad.get("PLAYER_PRICE", 0), errors="coerce").fillna(0)
                    r_squad["Pos"] = r_squad.apply(lambda r: pos_icons.get(str(r.get("PLAYER_POSITION")), str(r.get("PLAYER_POSITION"))), axis=1)
                    r_squad["Titularidad"] = (pd.to_numeric(r_squad.get("COMUNIATE_STARTER", 0.0), errors="coerce").fillna(0.0) * 100).astype(int)
                    r_squad["Valor (M€)"] = (r_squad["_raw_price"] / 1_000_000).round(2)
                    r_squad["Subida (K€)"] = (pd.to_numeric(r_squad.get("PLAYER_PRICE_INCREMENT", 0), errors="coerce").fillna(0) / 1_000).round().astype(int)

                    def _fmt_r_trend(r):
                        inc = float(r.get("PLAYER_PRICE_INCREMENT", 0) or 0)
                        pct = float(r.get("PLAYER_PRICE_INCREMENT_PCT", 0) or 0)
                        if inc > 0:
                            return f"🟢 +{pct:.1f}%"
                        elif inc < 0:
                            return f"🔴 {pct:.1f}%"
                        return "🟡 0.0%"

                    r_squad["Tendencia"] = r_squad.apply(_fmt_r_trend, axis=1)
                    r_squad["Cláusula (M€)"] = pd.to_numeric(r_squad.get("BIWPLAYER_CLAUSE"), errors="coerce").apply(lambda c: round(c / 1_000_000, 2) if pd.notna(c) and c > 0 else np.nan)
                    r_squad["Puntos"] = pd.to_numeric(r_squad.get("PLAYER_POINTS", 0), errors="coerce").fillna(0).astype(int)
                    r_squad["Percentil"] = (pd.to_numeric(r_squad.get("PERCENTILE", 0), errors="coerce").fillna(0.0) * 100).round().astype(int)

                    r_squad_sorted = r_squad.sort_values(by="_raw_price", ascending=False)
                    r_cols = ["PLAYER_NAME", "Pos", "Titularidad", "Puntos", "Percentil", "Valor (M€)", "Subida (K€)", "Tendencia", "Cláusula (M€)"]
                    st.dataframe(
                        r_squad_sorted[r_cols],
                        column_config={
                            "PLAYER_NAME": st.column_config.TextColumn("Futbolista", width="medium"),
                            "Pos": st.column_config.TextColumn("Posición", width="small"),
                            "Titularidad": st.column_config.ProgressColumn("Titularidad", format="%d%%", min_value=0, max_value=100, width="small"),
                            "Puntos": st.column_config.NumberColumn("Pts", format="%d", width="small"),
                            "Percentil": st.column_config.NumberColumn("Percentil Pts", format="%d%%", width="small"),
                            "Valor (M€)": st.column_config.NumberColumn("Valor (M€)", format="%.2f M€", width="small"),
                            "Subida (K€)": st.column_config.NumberColumn("Subida (K€)", format="%+d K€", width="small"),
                            "Tendencia": st.column_config.TextColumn("Tendencia", width="small"),
                            "Cláusula (M€)": st.column_config.NumberColumn("Cláusula (M€)", format="%.2f M€", width="small")
                        },
                        width="stretch",
                        hide_index=True
                    )
                    st.caption(f"Plantilla de **{selected_rival}** ({len(r_squad)} futbolistas).")
                else:
                    st.info(f"No se encontraron jugadores registrados para {selected_rival}.")


# =============================================================================
# TAB 3: MERCADO Y TODOS LOS JUGADORES (SCOUTING GENERAL)
# =============================================================================

with tab_players:
    if df_players.empty:
        st.info("ℹ️ No hay datos del maestro de jugadores disponibles. Pulsa **'🔄 Sincronizar Datos'** en la barra lateral.")
    else:
        df_all = df_players.copy()

        # Merge predictions if available
        if not df_pred.empty and "player_id" in df_pred.columns:
            pred_sub = df_pred[[
                "player_id", "pred_subida_24h", "pred_subida_72h_cum", "fase_mercado", "accion_recomendada"
            ]].drop_duplicates(subset=["player_id"]).copy()
            df_all["_pid"] = pd.to_numeric(df_all["PLAYER_ID"], errors="coerce")
            pred_sub["_pid"] = pd.to_numeric(pred_sub["player_id"], errors="coerce")
            df_all = pd.merge(df_all, pred_sub, on="_pid", how="left")
        else:
            df_all["pred_subida_24h"] = 0.0
            df_all["pred_subida_72h_cum"] = 0.0
            df_all["fase_mercado"] = "—"
            df_all["accion_recomendada"] = "—"

        # Sanitized raw numeric series for filtering and sorting
        def _to_num(series, default=0.0):
            return pd.to_numeric(series, errors="coerce").fillna(default)

        df_all["_raw_price"] = _to_num(df_all["PLAYER_PRICE"])
        df_all["_raw_inc"] = _to_num(df_all["PLAYER_PRICE_INCREMENT"] if "PLAYER_PRICE_INCREMENT" in df_all.columns else pd.Series(0.0, index=df_all.index))
        df_all["_raw_starter"] = _to_num(df_all["COMUNIATE_STARTER"] if "COMUNIATE_STARTER" in df_all.columns else pd.Series(0.0, index=df_all.index))
        df_all["_raw_points"] = _to_num(df_all["PLAYER_POINTS"] if "PLAYER_POINTS" in df_all.columns else pd.Series(0, index=df_all.index), 0).astype(int)
        df_all["_raw_avg"] = _to_num(df_all["AVG_POINTS"] if "AVG_POINTS" in df_all.columns else pd.Series(0.0, index=df_all.index))
        df_all["_raw_xp"] = _to_num(df_all["EXPECTED_POINTS"] if "EXPECTED_POINTS" in df_all.columns else pd.Series(0.0, index=df_all.index))
        df_all["_raw_percentile"] = _to_num(df_all["PERCENTILE"] if "PERCENTILE" in df_all.columns else pd.Series(0.0, index=df_all.index))
        df_all["_raw_clause"] = _to_num(df_all["BIWPLAYER_CLAUSE"] if "BIWPLAYER_CLAUSE" in df_all.columns else pd.Series(0.0, index=df_all.index))
        df_all["_raw_market_sale"] = _to_num(df_all["MARKET_SALE_PRICE"] if "MARKET_SALE_PRICE" in df_all.columns else pd.Series(0.0, index=df_all.index))
        df_all["_raw_pred_72h"] = _to_num(df_all["pred_subida_72h_cum"] if "pred_subida_72h_cum" in df_all.columns else pd.Series(0.0, index=df_all.index))

        # Tactical positions and order
        pos_icons = {"GK": "🧤 POR", "DF": "🛡️ DEF", "MF": "⚙️ MED", "FW": "⚡ DEL"}
        pos_rank = {"GK": 0, "DF": 1, "MF": 2, "FW": 3}

        def _fmt_pos_full(row):
            base = str(row.get("PLAYER_POSITION", "")).strip()
            alt = str(row.get("PLAYER_ALT_POSITIONS", "")).strip()
            label = pos_icons.get(base, base)
            if alt and alt.lower() != "nan":
                label += f" ({alt})"
            return label

        df_all["Pos"] = df_all.apply(_fmt_pos_full, axis=1)
        df_all["_pos_order"] = df_all["PLAYER_POSITION"].map(pos_rank).fillna(9)

        # Physical Status Formatter
        def _fmt_status_full(row):
            s = str(row.get("PLAYER_STATUS", "ok")).lower().strip()
            info = str(row.get("PLAYER_STATUS_INFO", "")).strip()
            if s == "ok":
                return "🟢 Apto"
            elif s == "injured":
                return f"🏥 Baja{f' ({info})' if info and info.lower() != 'nan' else ''}"
            elif s == "doubt":
                return f"🟡 Duda{f' ({info})' if info and info.lower() != 'nan' else ''}"
            elif s == "suspended":
                return "🟥 Sancionado"
            return s.capitalize()

        df_all["Estado"] = df_all.apply(_fmt_status_full, axis=1)

        # Trend percentage formatter
        def _fmt_inc_trend_pct(row):
            inc_val = float(row.get("_raw_inc", 0.0))
            price_val = float(row.get("_raw_price", 0.0))
            if inc_val == 0:
                return "🟡 0.0%"
            prev = price_val - inc_val
            pct = (inc_val / prev * 100.0) if prev > 0 else 0.0
            if inc_val > 0:
                return f"🟢 +{pct:.1f}%"
            return f"🔴 {pct:.1f}%"

        df_all["Tendencia"] = df_all.apply(_fmt_inc_trend_pct, axis=1)

        # Numeric currency and variation columns (in M€ or K€) for mathematical sorting
        df_all["Valor (M€)"] = (df_all["_raw_price"] / 1_000_000).round(2)
        df_all["Subida 24h (K€)"] = (df_all["_raw_inc"] / 1_000).round().astype(int)
        df_all["Previsión 72h (K€)"] = (df_all["_raw_pred_72h"] / 1_000).round().astype(int)

        # Ownership Formatter
        def _fmt_owner(row):
            t = row.get("BIWPLAYER_TEAM_NAME")
            if pd.isna(t) or not str(t).strip() or str(t).lower() == "nan":
                return "🆓 Libre"
            t_str = str(t).strip()
            if t_str == my_team_name:
                return f"⭐ {my_team_name} (Tú)"
            return f"👥 {t_str}"

        df_all["Propietario"] = df_all.apply(_fmt_owner, axis=1)

        # Market sale display (numeric float in M€ for exact sorting)
        df_all["En Mercado (M€)"] = df_all["_raw_market_sale"].apply(lambda s: round(s / 1_000_000, 2) if pd.notna(s) and s > 0 else np.nan)

        # Clause display (numeric float in M€ for exact sorting)
        df_all["Cláusula (M€)"] = df_all["_raw_clause"].apply(lambda c: round(c / 1_000_000, 2) if pd.notna(c) and c > 0 else np.nan)

        # Fichabilidad / Disponibilidad
        def _fmt_availability_badge(row):
            is_mine = (str(row.get("BIWPLAYER_TEAM_NAME", "")).strip() == my_team_name)
            if is_mine:
                return "⭐ En Plantilla"

            sale_price = float(row.get("_raw_market_sale", 0.0))
            clause = float(row.get("_raw_clause", 0.0))
            locked = pd.notna(row.get("BIWPLAYER_CLAUSE_LOCKED_UNTIL")) and str(row.get("BIWPLAYER_CLAUSE_LOCKED_UNTIL")).strip() != "" and str(row.get("BIWPLAYER_CLAUSE_LOCKED_UNTIL")).lower() != "nan"

            if sale_price > 0:
                if sale_price <= balance:
                    return "🟢 Compra Mercado (Saldo OK)"
                else:
                    return "🟡 En Mercado (Falta Saldo)"
            if clause > 0 and not locked:
                if clause <= balance:
                    return "⚡ Clausulable (Saldo OK)"
                else:
                    return "⚔️ Clausulable"
            return "🔒 No en venta hoy"

        df_all["Disponibilidad"] = df_all.apply(_fmt_availability_badge, axis=1)

        # Other presentation columns
        df_all["Titularidad"] = (df_all["_raw_starter"] * 100).astype(int)
        df_all["Puntos"] = df_all["_raw_points"]
        df_all["Percentil"] = (df_all["_raw_percentile"] * 100).round().astype(int)
        df_all["Media"] = df_all["_raw_avg"]
        df_all["xP"] = df_all["_raw_xp"]
        df_all["Acción IA"] = df_all.get("accion_recomendada", pd.Series("—", index=df_all.index)).fillna("—")

        # Counts for summary pills
        total_p = len(df_all)
        mkt_count = int((df_all["_raw_market_sale"] > 0).sum())
        free_count = int(df_all["BIWPLAYER_TEAM_NAME"].isna().sum())
        rival_count = int((df_all["BIWPLAYER_TEAM_NAME"].notna() & (df_all["BIWPLAYER_TEAM_NAME"] != my_team_name)).sum())
        afford_mask = ((df_all["_raw_market_sale"] > 0) & (df_all["_raw_market_sale"] <= balance)) | \
                      ((df_all["_raw_clause"] > 0) & (df_all["_raw_clause"] <= balance) & (df_all["BIWPLAYER_TEAM_NAME"] != my_team_name))
        afford_count = int(afford_mask.sum())
        clause_mask = (df_all["_raw_clause"] > 0) & (df_all["BIWPLAYER_TEAM_NAME"] != my_team_name)
        clause_count = int(clause_mask.sum())

        st.markdown(f"""
        <div class="roster-summary-bar">
            <div class="roster-pill">
                <span>📋 <b>Base de Datos:</b></span>
                <span>{total_p} futbolistas</span>
            </div>
            <div class="roster-pill">
                <span>🛒 <b>En Mercado Hoy:</b></span>
                <span style="color: #0284c7;"><b>{mkt_count}</b> en venta</span>
            </div>
            <div class="roster-pill">
                <span>💰 <b>Comprables con tu Saldo:</b></span>
                <span style="color: #10b981;"><b>{afford_count}</b> futbolistas (≤ {format_price(balance)})</span>
            </div>
            <div class="roster-pill">
                <span>⚔️ <b>Clausulables a Rivales:</b></span>
                <span>{clause_count} jugadores</span>
            </div>
            <div class="roster-pill">
                <span>🆓 <b>Sin Dueño (Libres):</b></span>
                <span>{free_count} jugadores</span>
            </div>
            <div class="roster-pill">
                <span>👥 <b>En Rivales:</b></span>
                <span>{rival_count} jugadores</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # ---------------------------------------------------------------------
        # CONTROLES DE FILTRO Y BÚSQUEDA (2 FILAS LIMPIAS)
        # ---------------------------------------------------------------------
        row1_c1, row1_c2, row1_c3, row1_c4 = st.columns([1.8, 1.4, 1.5, 1.5])

        with row1_c1:
            f_cat = st.selectbox(
                "Situación en Mercado & Liga:",
                options=[
                    f"🌐 Todos los futbolistas ({total_p})",
                    f"🛒 En venta en el Mercado hoy ({mkt_count})",
                    f"💰 Comprables con mi saldo ({afford_count})",
                    f"⚔️ Clausulables a rivales ({clause_count})",
                    f"👥 Pertenecen a Rivales ({rival_count})",
                    f"🆓 Libres / Sin propietario ({free_count})",
                    "🎯 Chollos en Mercado (Precio < Valor)",
                    f"⭐ De mi plantilla ({squad_count})"
                ],
                index=0,
                key="all_p_cat"
            )

        with row1_c2:
            f_pos = st.selectbox(
                "Línea Táctica:",
                options=["Todas las líneas", "🧤 Porteros (GK)", "🛡️ Defensas (DF)", "⚙️ Medios (MF)", "⚡ Delanteros (FW)"],
                index=0,
                key="all_p_pos"
            )

        with row1_c3:
            all_teams = sorted([t for t in df_all["TEAM_NAME"].dropna().unique() if str(t).strip()])
            f_team = st.selectbox(
                "Club de LaLiga:",
                options=["Todos los clubes"] + all_teams,
                index=0,
                key="all_p_team"
            )

        with row1_c4:
            f_fit = st.selectbox(
                "Condición & Titularidad:",
                options=[
                    "Todos los estados",
                    "🟢 Titulares clave (≥70%)",
                    "🟡 Titulares probables (≥50%)",
                    "🏥 Con incidencias (Duda/Baja)",
                    "📈 En subida de precio hoy (+€)",
                    "📉 En bajada de precio hoy (-€)"
                ],
                index=0,
                key="all_p_fit"
            )

        row2_c1, row2_c2, row2_c3, row2_c4 = st.columns([1.5, 1.5, 1.8, 1.6])

        with row2_c1:
            f_price = st.selectbox(
                "Rango de Precio:",
                options=[
                    "Cualquier precio",
                    "🟢 Low-Cost (< 1 M€)",
                    "🟡 Clase Media (1 - 5 M€)",
                    "💎 Top / Estrellas (> 5 M€)",
                    f"💰 Asequibles (≤ {format_price(balance)})"
                ],
                index=0,
                key="all_p_price"
            )

        with row2_c2:
            f_ai = st.selectbox(
                "Señal IA & Previsión:",
                options=[
                    "Todas las señales IA",
                    "🚀 Compra Fuerte (Especulación)",
                    "📈 Mantener en subida",
                    "🛑 Stop-Loss / Desplome",
                    "🔮 Previsión 72h positiva (+€)"
                ],
                index=0,
                key="all_p_ai"
            )

        with row2_c3:
            f_sort = st.selectbox(
                "Ordenar Jugadores por:",
                options=[
                    "💰 Mayor Valor de Mercado",
                    "📈 Mayor Subida 24h",
                    "📉 Mayor Caída 24h (Stop-Loss)",
                    "🔮 Mayor Subida Prevista a 72h (IA)",
                    "⭐ Más Puntos Fantasy",
                    "📊 Mayor Percentil Puntos",
                    "⚽ Mayor Titularidad (%)",
                    "🎯 Mayor xP (Próx. Partido)",
                    "🛒 En Mercado Primero",
                    "🏷️ Menor Precio (Económicos)"
                ],
                index=0,
                key="all_p_sort"
            )

        with row2_c4:
            f_search = st.text_input("🔍 Buscar:", placeholder="Jugador o Club...", key="all_p_search")

        # Apply Filters
        filtered_all = df_all.copy()

        # Category Filter
        if "En venta en el Mercado hoy" in f_cat:
            filtered_all = filtered_all[filtered_all["_raw_market_sale"] > 0]
        elif "Comprables con mi saldo" in f_cat:
            filtered_all = filtered_all[
                ((filtered_all["_raw_market_sale"] > 0) & (filtered_all["_raw_market_sale"] <= balance)) |
                ((filtered_all["_raw_clause"] > 0) & (filtered_all["_raw_clause"] <= balance) & (filtered_all["BIWPLAYER_TEAM_NAME"] != my_team_name))
            ]
        elif "Clausulables a rivales" in f_cat:
            filtered_all = filtered_all[
                (filtered_all["_raw_clause"] > 0) & 
                (filtered_all["BIWPLAYER_TEAM_NAME"] != my_team_name) &
                (filtered_all["BIWPLAYER_CLAUSE_LOCKED_UNTIL"].isna() | (filtered_all["BIWPLAYER_CLAUSE_LOCKED_UNTIL"] == ""))
            ]
        elif "Pertenecen a Rivales" in f_cat:
            filtered_all = filtered_all[filtered_all["BIWPLAYER_TEAM_NAME"].notna() & (filtered_all["BIWPLAYER_TEAM_NAME"] != my_team_name)]
        elif "Libres / Sin propietario" in f_cat:
            filtered_all = filtered_all[filtered_all["BIWPLAYER_TEAM_NAME"].isna()]
        elif "Chollos en Mercado" in f_cat:
            filtered_all = filtered_all[(filtered_all["_raw_market_sale"] > 0) & (filtered_all["_raw_market_sale"] < filtered_all["_raw_price"])]
        elif "De mi plantilla" in f_cat:
            filtered_all = filtered_all[filtered_all["BIWPLAYER_TEAM_NAME"] == my_team_name]

        # Position Filter
        if f_pos == "🧤 Porteros (GK)":
            filtered_all = filtered_all[filtered_all["PLAYER_POSITION"] == "GK"]
        elif f_pos == "🛡️ Defensas (DF)":
            filtered_all = filtered_all[filtered_all["PLAYER_POSITION"] == "DF"]
        elif f_pos == "⚙️ Medios (MF)":
            filtered_all = filtered_all[filtered_all["PLAYER_POSITION"] == "MF"]
        elif f_pos == "⚡ Delanteros (FW)":
            filtered_all = filtered_all[filtered_all["PLAYER_POSITION"] == "FW"]

        # Team Filter
        if f_team != "Todos los clubes":
            filtered_all = filtered_all[filtered_all["TEAM_NAME"] == f_team]

        # Condition Filter
        if f_fit == "🟢 Titulares clave (≥70%)":
            filtered_all = filtered_all[filtered_all["_raw_starter"] >= 0.70]
        elif f_fit == "🟡 Titulares probables (≥50%)":
            filtered_all = filtered_all[filtered_all["_raw_starter"] >= 0.50]
        elif f_fit == "🏥 Con incidencias (Duda/Baja)":
            filtered_all = filtered_all[filtered_all["PLAYER_STATUS"] != "ok"]
        elif f_fit == "📈 En subida de precio hoy (+€)":
            filtered_all = filtered_all[filtered_all["_raw_inc"] > 0]
        elif f_fit == "📉 En bajada de precio hoy (-€)":
            filtered_all = filtered_all[filtered_all["_raw_inc"] < 0]

        # Price Range Filter
        if "Low-Cost" in f_price:
            filtered_all = filtered_all[filtered_all["_raw_price"] < 1_000_000]
        elif "Clase Media" in f_price:
            filtered_all = filtered_all[(filtered_all["_raw_price"] >= 1_000_000) & (filtered_all["_raw_price"] <= 5_000_000)]
        elif "Top / Estrellas" in f_price:
            filtered_all = filtered_all[filtered_all["_raw_price"] > 5_000_000]
        elif "Asequibles" in f_price:
            filtered_all = filtered_all[filtered_all["_raw_price"] <= balance]

        # AI Signal Filter
        if "Compra Fuerte" in f_ai:
            filtered_all = filtered_all[filtered_all["Acción IA"].str.contains("COMPRA", na=False)]
        elif "Mantener" in f_ai:
            filtered_all = filtered_all[filtered_all["Acción IA"].str.contains("MANTENER", na=False)]
        elif "Stop-Loss" in f_ai:
            filtered_all = filtered_all[filtered_all["Acción IA"].str.contains("STOP-LOSS|VENDER", na=False)]
        elif "Previsión 72h positiva" in f_ai:
            filtered_all = filtered_all[filtered_all["_raw_pred_72h"] > 0]

        # Search Query
        if f_search.strip():
            q_p = f_search.strip().lower()
            filtered_all = filtered_all[
                filtered_all["PLAYER_NAME"].str.lower().str.contains(q_p, na=False) |
                filtered_all["TEAM_NAME"].str.lower().str.contains(q_p, na=False)
            ]

        # Sorting
        if f_sort == "💰 Mayor Valor de Mercado":
            filtered_all = filtered_all.sort_values(by="_raw_price", ascending=False)
        elif f_sort == "📈 Mayor Subida 24h":
            filtered_all = filtered_all.sort_values(by="_raw_inc", ascending=False)
        elif f_sort == "📉 Mayor Caída 24h (Stop-Loss)":
            filtered_all = filtered_all.sort_values(by="_raw_inc", ascending=True)
        elif f_sort == "🔮 Mayor Subida Prevista a 72h (IA)":
            filtered_all = filtered_all.sort_values(by="_raw_pred_72h", ascending=False)
        elif f_sort == "⭐ Más Puntos Fantasy":
            filtered_all = filtered_all.sort_values(by="_raw_points", ascending=False)
        elif f_sort == "📊 Mayor Percentil Puntos":
            filtered_all = filtered_all.sort_values(by="_raw_percentile", ascending=False)
        elif f_sort == "⚽ Mayor Titularidad (%)":
            filtered_all = filtered_all.sort_values(by="_raw_starter", ascending=False)
        elif f_sort == "🎯 Mayor xP (Próx. Partido)":
            filtered_all = filtered_all.sort_values(by="_raw_xp", ascending=False)
        elif f_sort == "🛒 En Mercado Primero":
            filtered_all["_is_mkt"] = (filtered_all["_raw_market_sale"] > 0).astype(int)
            filtered_all = filtered_all.sort_values(by=["_is_mkt", "_raw_price"], ascending=[False, False])
        elif f_sort == "🏷️ Menor Precio (Económicos)":
            filtered_all = filtered_all.sort_values(by="_raw_price", ascending=True)

        # Main Table
        all_display_cols = [
            "PLAYER_NAME", "TEAM_NAME", "Pos", "Estado", "Titularidad",
            "Puntos", "Percentil", "Media", "xP", "Valor (M€)",
            "Subida 24h (K€)", "Tendencia", "Previsión 72h (K€)", "Propietario", "En Mercado (M€)",
            "Cláusula (M€)", "Disponibilidad", "Acción IA"
        ]

        st.dataframe(
            filtered_all[all_display_cols],
            column_config={
                "PLAYER_NAME": st.column_config.TextColumn("Futbolista", help="Nombre del jugador", width="medium"),
                "TEAM_NAME": st.column_config.TextColumn("Club", help="Equipo de LaLiga", width="small"),
                "Pos": st.column_config.TextColumn("Posición", help="Posición táctica principal y alternativa", width="small"),
                "Estado": st.column_config.TextColumn("Estado", help="Disponibilidad médica", width="small"),
                "Titularidad": st.column_config.ProgressColumn(
                    "Titularidad",
                    help="Probabilidad estimada de once titular según Comuniate",
                    format="%d%%",
                    min_value=0,
                    max_value=100,
                    width="small"
                ),
                "Puntos": st.column_config.NumberColumn("Pts", help="Puntos acumulados oficiales", format="%d", width="small"),
                "Percentil": st.column_config.NumberColumn(
                    "Percentil Pts",
                    help="Percentil de puntos respecto a toda LaLiga (0% a 100%)",
                    format="%d%%",
                    width="small"
                ),
                "Media": st.column_config.NumberColumn("Media", help="Media de puntos por partido", format="%.2f", width="small"),
                "xP": st.column_config.NumberColumn("xP Próx. J.", help="Puntos esperados para la próxima jornada (titularidad x rival x forma)", format="%.1f", width="small"),
                "Valor (M€)": st.column_config.NumberColumn("Valor (M€)", help="Precio oficial en Millones de Euros", format="%.2f M€", width="small"),
                "Subida 24h (K€)": st.column_config.NumberColumn("Subida 24h (K€)", help="Variación en 24h en Miles de Euros", format="%+d K€", width="small"),
                "Tendencia": st.column_config.TextColumn("Tendencia", help="Semáforo y porcentaje de variación 24h", width="small"),
                "Previsión 72h (K€)": st.column_config.NumberColumn("Previsión 72h (IA)", help="Previsión acumulada de variación de precio a 3 días en Miles de Euros según el modelo ML", format="%+d K€", width="small"),
                "Propietario": st.column_config.TextColumn("Propietario Liga", help="Equipo de tu liga que tiene al futbolista", width="medium"),
                "En Mercado (M€)": st.column_config.NumberColumn("En Mercado (M€)", help="Precio de salida en el mercado en Millones de Euros", format="%.2f M€", width="small"),
                "Cláusula (M€)": st.column_config.NumberColumn("Cláusula (M€)", help="Cláusula de rescisión en Millones de Euros", format="%.2f M€", width="small"),
                "Disponibilidad": st.column_config.TextColumn("Oportunidad Fichaje", help="Diagnóstico de si puedes ficharlo hoy con tu saldo", width="medium"),
                "Acción IA": st.column_config.TextColumn("Señal IA", help="Recomendación del modelo econométrico", width="medium")
            },
            width="stretch",
            height=540,
            hide_index=True
        )

        st.caption(f"Mostrando **{len(filtered_all)}** de **{total_p}** futbolistas de LaLiga.")

        # ---------------------------------------------------------------------
        # FICHA TÉCNICA Y DIAGNÓSTICO DE FICHAJE
        # ---------------------------------------------------------------------
        st.markdown("<div style='height: 16px;'></div>", unsafe_allow_html=True)
        st.markdown("#### 🔍 Ficha Técnica y Diagnóstico de Fichaje")

        if not filtered_all.empty:
            inspect_options = filtered_all["PLAYER_NAME"].tolist()
            picked_name = st.selectbox(
                "Selecciona un futbolista para analizar al detalle sus métricas deportivas, contractuales y de IA:",
                options=inspect_options,
                index=0,
                key="all_p_inspect"
            )

            p_card = filtered_all[filtered_all["PLAYER_NAME"] == picked_name].iloc[0]

            ic1, ic2, ic3 = st.columns(3)

            with ic1:
                st.markdown(f"##### ⚽ Perfil Deportivo")
                st.markdown(f"• **Jugador:** `{p_card['PLAYER_NAME']}` ({p_card['Pos']})")
                st.markdown(f"• **Club:** `{p_card['TEAM_NAME']}`")
                st.markdown(f"• **Estado físico:** `{p_card['Estado']}`")
                st.markdown(f"• **Titularidad estimada:** `{p_card['Titularidad']}%`")
                st.markdown(f"• **Puntos acumulados:** `{p_card['Puntos']} pts` (Media: `{p_card['Media']:.2f}`)")
                st.markdown(f"• **Percentil Puntos:** `{p_card['Percentil']}` de LaLiga")
                
                # Matchday preview
                nxt_rival = p_card.get("NEXT_RIVAL")
                nxt_loc = p_card.get("NEXT_GAME")
                nxt_win = p_card.get("NEXT_GAME_WIN")
                nxt_win_str = f"({float(nxt_win)*100:.0f}% victoria)" if pd.notna(nxt_win) and float(nxt_win) > 0 else ""
                if pd.notna(nxt_rival) and str(nxt_rival).strip():
                    loc_str = "en casa" if str(nxt_loc).upper() == "LOCAL" else "fuera"
                    st.markdown(f"• **Próx. Partido:** vs `{nxt_rival}` ({loc_str}) {nxt_win_str}")
                st.markdown(f"• **xP Próx. Jornada:** `{p_card['xP']:.1f} puntos esperados`")

            with ic2:
                st.markdown(f"##### 💼 Situación & Mercado")
                st.markdown(f"• **Propietario:** `{p_card['Propietario']}`")
                st.markdown(f"• **Valor oficial de mercado:** `{format_price(p_card['_raw_price'])}`")
                sub_k = int(p_card['Subida 24h (K€)'])
                st.markdown(f"• **Subida 24h hoy:** `{p_card['Tendencia']} ({'+' if sub_k > 0 else ''}{sub_k} K€)`")
                
                # Financial Affordability callout
                p_sale = float(p_card["_raw_market_sale"])
                p_cl = float(p_card["_raw_clause"])
                p_is_mine = (str(p_card.get("BIWPLAYER_TEAM_NAME", "")).strip() == my_team_name)

                mkt_str = f"🛒 {format_price(p_sale)}" if p_sale > 0 else "—"
                cl_str = f"⚔️ {format_price(p_cl)}" if p_cl > 0 else "—"
                st.markdown(f"• **Puesto en Mercado:** `{mkt_str}`")
                st.markdown(f"• **Cláusula de rescisión:** `{cl_str}`")

                if p_is_mine:
                    st.success("⭐ **Ya está en tu equipo.** Forma parte de tu plantilla actual.")
                elif p_sale > 0 and p_sale <= balance:
                    diff_m = (balance - p_sale) / 1e6
                    st.success(f"🟢 **¡Fichable en Mercado!** Tienes {format_price(balance)} y cuesta {format_price(p_sale)}. Te sobrarían {diff_m:.2f} M€.")
                elif p_sale > 0 and p_sale > balance:
                    need_m = (p_sale - balance) / 1e6
                    st.warning(f"🟡 **En venta, pero falta liquidez.** Cuesta {format_price(p_sale)} y te faltan {need_m:.2f} M€ de saldo.")
                elif p_cl > 0 and p_cl <= balance:
                    diff_cl = (balance - p_cl) / 1e6
                    st.success(f"⚡ **¡Clausulazo Viable!** Su cláusula es de {format_price(p_cl)} y tu saldo es de {format_price(balance)}. Te sobrarían {diff_cl:.2f} M€.")
                elif p_cl > 0 and p_cl > balance:
                    need_cl = (p_cl - balance) / 1e6
                    st.info(f"⚔️ **Cláusula inasumible hoy.** Cláusula de {format_price(p_cl)} (te faltan {need_cl:.2f} M€ para clausularlo).")
                else:
                    st.caption("🔒 **No disponible hoy:** Ni está en venta en el mercado ni tiene cláusula activa.")

            with ic3:
                st.markdown(f"##### 🔮 Inteligencia & Econometría (IA)")
                st.markdown(f"• **Recomendación IA:** `{p_card['Acción IA']}`")
                prev_k = int(p_card['Previsión 72h (K€)'])
                st.markdown(f"• **Previsión acumulada 72h:** `{'+' if prev_k > 0 else ''}{prev_k} K€`")
                fase = str(p_card.get("fase_mercado", "—"))
                if fase and fase.lower() != "nan":
                    st.markdown(f"• **Fase de Mercado:** `{fase}`")
                cpp = float(p_card.get("COST_PER_POINT", 0.0) or 0.0)
                if cpp > 0:
                    st.markdown(f"• **Coste por punto:** `{cpp:.2f} M€/pt`")
                # Recent fitness
                fit_str = str(p_card.get("PLAYER_FITNESS", "")).strip()
                if fit_str and fit_str.lower() != "nan" and fit_str != "[]":
                    st.markdown(f"• **Racha Fitness:** `{fit_str}`")

