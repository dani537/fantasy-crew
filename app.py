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
from typing import Optional, Dict, Any, List, Union

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
        def _parse_curr(val):
            s = str(val).replace("€", "").replace(".", "").replace(",", ".").replace("+", "").strip()
            try:
                return float(s)
            except Exception:
                return 0.0
        bal_num = _parse_curr(r.get("Saldo_Real_Disponible", 0))
        sq_num = _parse_curr(r.get("Valor_Plantilla", 0))
        patr_num = _parse_curr(r.get("Patrimonio_Total", 0))
        mb_num = _parse_curr(r.get("Max_Puja_Permitida", 0))
        num_jug = int(r.get("Num_Jugadores", 0) or 0)
        return {
            "team_name": str(r.get("Manager", "Dani SR")),
            "league_name": "Liga Biwenger",
            "balance": bal_num,
            "squad_value": sq_num,
            "total_equity": patr_num if patr_num > 0 else (bal_num + sq_num),
            "max_bid_available": mb_num if mb_num > 0 else (bal_num + sq_num * 0.25),
            "squad_count": num_jug,
            "last_updated": str(r.get("Ultima_Actualizacion", ""))
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
    # Fallback to Google Sheets (Mi_Plantilla + Mercado_Fichajes)
    sheets = load_all_sheets_db()
    df_sq = sheets.get("Mi_Plantilla", pd.DataFrame())
    df_mkt = sheets.get("Mercado_Fichajes", pd.DataFrame())
    
    rows = []
    def _parse_c(v):
        s = str(v).replace("€", "").replace(".", "").replace("+", "").replace(",", ".").strip()
        try:
            return float(s)
        except Exception:
            return 0.0

    if not df_sq.empty and "Jugador" in df_sq.columns:
        for idx, r in df_sq.iterrows():
            rows.append({
                "PLAYER_ID": idx + 10000,
                "PLAYER_NAME": r.get("Jugador", ""),
                "PLAYER_POSITION": r.get("Posicion", "MF"),
                "TEAM_NAME": r.get("Equipo", ""),
                "PLAYER_PRICE": _parse_c(r.get("Precio", 0)),
                "PLAYER_PRICE_INCREMENT": _parse_c(r.get("Subida_24h", 0)),
                "PLAYER_STATUS": r.get("Estado", "Apto"),
                "COMUNIATE_STATUS": r.get("Titularidad", "Titular"),
                "COMUNIATE_STARTER": 1.0 if "titular" in str(r.get("Titularidad", "")).lower() else 0.5,
                "PLAYER_POINTS": int(pd.to_numeric(r.get("Puntos", 0), errors="coerce") or 0),
                "MARKET_OFFER_AMOUNT": _parse_c(r.get("Oferta_Mercado", 0)) if str(r.get("Oferta_Mercado", "—")) not in ("—", "-", "") else None,
                "BIWPLAYER_CLAUSE": _parse_c(r.get("Clausula", 0)),
                "BIWPLAYER_TEAM_NAME": "Dani SR",
                "ON_MARKET": False,
                "MARKET_SALE_PRICE": None
            })

    if not df_mkt.empty and "Jugador" in df_mkt.columns:
        for idx, r in df_mkt.iterrows():
            seller = str(r.get("Vendedor", "Mercado")).strip()
            is_cpu = seller.lower() in ("mercado", "biwenger", "")
            pr = _parse_c(r.get("Precio_Venta", 0))
            rows.append({
                "PLAYER_ID": idx + 20000,
                "PLAYER_NAME": r.get("Jugador", ""),
                "PLAYER_POSITION": r.get("Posicion", "MF"),
                "TEAM_NAME": r.get("Equipo", ""),
                "PLAYER_PRICE": pr,
                "PLAYER_PRICE_INCREMENT": _parse_c(r.get("Subida_24h", 0)),
                "PLAYER_STATUS": "Apto",
                "COMUNIATE_STATUS": "—",
                "COMUNIATE_STARTER": 0.5,
                "PLAYER_POINTS": 0,
                "MARKET_OFFER_AMOUNT": None,
                "BIWPLAYER_CLAUSE": pr,
                "BIWPLAYER_TEAM_NAME": "Mercado" if is_cpu else seller,
                "ON_MARKET": True,
                "MARKET_SALE_PRICE": pr,
                "MARKET_UNTIL": r.get("Fin_Puja", "")
            })

    if rows:
        return pd.DataFrame(rows)
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

@st.cache_data(ttl=60)
def load_active_events():
    path = "./data/active_events.csv"
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
def load_transfers_feed():
    path = "./data/raw/board_transfers.csv"
    if os.path.exists(path):
        try:
            return pd.read_csv(path)
        except Exception:
            pass
    # Fallback to Google Sheets Movimientos
    sheets = load_all_sheets_db()
    df_mov = sheets.get("Movimientos", pd.DataFrame())
    if not df_mov.empty:
        try:
            df_out = pd.DataFrame()
            df_out["date"] = df_mov.get("fecha", "")
            df_out["type"] = df_mov.get("tipo", "")
            df_out["player_id"] = pd.to_numeric(df_mov.get("player_id", 0), errors="coerce").fillna(0).astype(int)
            df_out["buyer_name"] = df_mov.get("comprador", "")
            df_out["seller_name"] = df_mov.get("vendedor", "")
            df_out["amount"] = pd.to_numeric(df_mov.get("precio", 0), errors="coerce").fillna(0)
            df_out["clause"] = df_mov.get("es_clausula", "NO").astype(str).str.upper().isin(["SÍ", "SI", "TRUE"])
            return df_out
        except Exception:
            pass
    return pd.DataFrame()

@st.cache_data(ttl=60)
def load_round_standings_cache():
    path = "./data/round_standings_cache.json"
    if os.path.exists(path):
        try:
            import json
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


# =============================================================================
# DATA INITIALIZATION & CALCULATIONS
# =============================================================================

user_ctx = load_user_info()
df_players = load_master_players()
df_rivals = load_rival_financials()
df_pred = load_predictions()
df_next_j = load_next_jornada()
df_active_ev = load_active_events()
df_offers_raw = load_market_offers()
df_transfers_raw = load_transfers_feed()
round_cache = load_round_standings_cache()

# User Context Defaults
if user_ctx:
    my_team_name = str(user_ctx.get("team_name", "Dani SR"))
    league_name = str(user_ctx.get("league_name", "Liga Biwenger"))
    balance = float(user_ctx.get("balance", 0.0))
else:
    my_team_name = "Dani SR"
    league_name = "Liga Biwenger"
    balance = 0.0

# Squad Slicing & Financial Aggregates
if not df_players.empty and "BIWPLAYER_TEAM_NAME" in df_players.columns:
    my_squad = df_players[df_players["BIWPLAYER_TEAM_NAME"] == my_team_name].copy()
else:
    my_squad = pd.DataFrame()

squad_value = float(my_squad["PLAYER_PRICE"].sum()) if not my_squad.empty else 0.0
squad_count = len(my_squad)
if squad_value == 0.0 and user_ctx and float(user_ctx.get("squad_value", 0.0)) > 0:
    squad_value = float(user_ctx.get("squad_value", 0.0))
    squad_count = int(user_ctx.get("squad_count", len(my_squad)))

total_equity = balance + squad_value
if user_ctx and float(user_ctx.get("total_equity", 0.0)) > 0 and total_equity == balance:
    total_equity = float(user_ctx.get("total_equity", balance + squad_value))

squad_day_gain = float(my_squad["PLAYER_PRICE_INCREMENT"].fillna(0).sum()) if not my_squad.empty else 0.0
squad_day_pct = (squad_day_gain / (squad_value - squad_day_gain) * 100.0) if (squad_value - squad_day_gain) > 0 else 0.0
active_offers_sum = float(my_squad["MARKET_OFFER_AMOUNT"].dropna().sum()) if (not my_squad.empty and "MARKET_OFFER_AMOUNT" in my_squad.columns) else 0.0
active_offers_count = int(my_squad["MARKET_OFFER_AMOUNT"].dropna().count()) if (not my_squad.empty and "MARKET_OFFER_AMOUNT" in my_squad.columns) else 0

# League Rules: Maximum Bid (quarterTeam: balance + 25% squad value)
debt_allowance = 0.25 * squad_value
max_bid_available = max(0.0, balance + debt_allowance)
if max_bid_available <= balance and user_ctx and float(user_ctx.get("max_bid_available", 0.0)) > 0:
    max_bid_available = float(user_ctx.get("max_bid_available", balance + debt_allowance))

# Historical Net Benefit (Base initial budget 40.0 M€)
base_budget_total = 40_000_000.0
net_profit = total_equity - base_budget_total
net_profit_pct = (net_profit / base_budget_total) * 100.0

# Dynamic Matchday Kickoff & Positive Balance Countdown
next_kickoff_str = "Fecha por confirmar"
countdown_str = "Próximamente"
hours_left_num = 999.0
next_round_name = "Próx. Jornada"
active_round_name = "Jornada en juego"
active_round_status = "En liquidación"

now_dt = datetime.datetime.now()

# 1. Identify active and pending rounds from active_events.csv
if not df_active_ev.empty:
    # Pending / upcoming round where positive balance is required
    df_pending = df_active_ev[df_active_ev["ESTADO"].astype(str).str.lower().str.contains("pendiente|pending")].copy()
    if not df_pending.empty:
        p_row = df_pending.iloc[0]
        next_round_name = str(p_row.get("JORNADA", "Próx. Jornada"))
        f_start = p_row.get("FECHA_INICIO")
        if pd.notna(f_start):
            try:
                dt_p = pd.to_datetime(f_start)
                next_kickoff_str = dt_p.strftime("%d/%m %H:%M")
                diff_p = dt_p - now_dt
                hours_left_num = diff_p.total_seconds() / 3600.0
                if diff_p.total_seconds() > 0:
                    d = diff_p.days
                    h, rem = divmod(diff_p.seconds, 3600)
                    m = rem // 60
                    countdown_str = f"En {d}d {h}h {m}m" if d > 0 else f"En {h}h {m}m"
                else:
                    countdown_str = "Iniciada hoy"
            except Exception:
                pass
    
    # In-progress / finishing round
    df_in_play = df_active_ev[df_active_ev["ESTADO"].astype(str).str.lower().str.contains("en juego|active")].copy()
    if not df_in_play.empty:
        a_row = df_in_play.iloc[0]
        active_round_name = str(a_row.get("JORNADA", "Jornada 4"))
        active_round_status = str(a_row.get("ESTADO", "En juego"))

# Fallback to next_jornada.csv if active_events had no pending
if countdown_str in ("Próximamente", "Fecha por confirmar") and not df_next_j.empty:
    f_date = df_next_j.iloc[0].get("fecha")
    if pd.notna(f_date):
        try:
            dt = pd.to_datetime(f_date).tz_localize(None)
            next_kickoff_str = dt.strftime("%d/%m %H:%M")
            diff = dt - now_dt
            if diff.total_seconds() > 0:
                d = diff.days
                h, rem = divmod(diff.seconds, 3600)
                m = rem // 60
                countdown_str = f"En {d}d {h}h {m}m" if d > 0 else f"En {h}h {m}m"
            else:
                countdown_str = "En juego"
        except Exception:
            pass

# Projected Matchday Prizes (from round_standings_cache)
my_round_points = 0
my_round_pos = 0
projected_bonuses_sum = 0.0
bonuses_breakdown = {}

if round_cache and "standings" in round_cache:
    for st_item in round_cache["standings"]:
        if str(st_item.get("name", "")).strip() == my_team_name or str(st_item.get("id")) == str(user_ctx.get("team_id") if user_ctx else ""):
            lineup_dict = st_item.get("lineup") or {}
            my_round_points = int(lineup_dict.get("points", 0) or 0)
            my_round_pos = int(lineup_dict.get("position", 0) or 0)
            bonuses_breakdown = lineup_dict.get("bonuses") or {}
            projected_bonuses_sum = float(sum(bonuses_breakdown.values()))
            break

# If cache was empty or 0, compute standard league bonus estimate (50k/pt + pos bonus)
if projected_bonuses_sum == 0.0 and my_round_points > 0:
    projected_bonuses_sum = float(my_round_points * 50_000)
    bonuses_breakdown = {"bonusPoint": projected_bonuses_sum}

projected_post_round_balance = balance + projected_bonuses_sum


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


def get_data_snapshot_info(user_ctx_dict: Optional[dict] = None) -> dict:
    """Computes exact timestamp, relative age, and freshness indicator for current data."""
    now_dt = datetime.datetime.now()
    dt_result = None
    source_label = "Desconocido"

    # 1. Session state check (if synced in current session)
    if "last_sync_timestamp" in st.session_state:
        dt_result = st.session_state["last_sync_timestamp"]
        source_label = "Sincronizado ahora"

    # 2. Local files modification time
    if dt_result is None:
        candidates = [
            "./data/players_transformed.csv",
            "./data/rival_financials.csv",
            "./data/user_info.csv",
            "./data/active_events.csv",
            "./data/round_standings_cache.json"
        ]
        mtimes = [os.path.getmtime(p) for p in candidates if os.path.exists(p)]
        if mtimes:
            dt_result = datetime.datetime.fromtimestamp(max(mtimes))
            source_label = "Archivos locales"

    # 3. Google Sheets 'Ultima_Actualizacion' fallback
    if dt_result is None and user_ctx_dict and user_ctx_dict.get("last_updated"):
        raw_upd = str(user_ctx_dict.get("last_updated", "")).strip()
        try:
            dt_result = pd.to_datetime(raw_upd).to_pydatetime()
            source_label = "Google Sheets"
        except Exception:
            pass

    # Fallback default if nothing found
    if dt_result is None:
        return {
            "dt": None,
            "formatted": "Sin sincronizar aún",
            "relative": "Sin datos",
            "status_color": "#94a3b8",
            "status_badge": "⚪ Sin sincronizar",
            "source": "N/D"
        }

    # Compute relative time
    diff_sec = max(0, (now_dt - dt_result).total_seconds())
    mins = int(diff_sec // 60)
    hours = int(diff_sec // 3600)
    days = int(diff_sec // 86400)

    if diff_sec < 90:
        rel_str = "hace unos segundos"
        status_color = "#10b981"
        status_badge = "🟢 En tiempo real"
    elif mins < 60:
        rel_str = f"hace {mins} min"
        status_color = "#10b981"
        status_badge = "🟢 Datos al día"
    elif hours < 6:
        rel_str = f"hace {hours}h {mins % 60}m"
        status_color = "#10b981"
        status_badge = "🟢 Datos de hoy"
    elif hours < 24:
        rel_str = f"hace {hours}h"
        status_color = "#f59e0b"
        status_badge = "🟡 Datos de hoy"
    elif days == 1:
        rel_str = "ayer"
        status_color = "#f59e0b"
        status_badge = "🟡 Datos de ayer"
    else:
        rel_str = f"hace {days} días"
        status_color = "#ef4444"
        status_badge = "🔴 Desactualizado (>24h)"

    # Formatted date string
    if dt_result.date() == now_dt.date():
        fmt_date = f"Hoy a las {dt_result.strftime('%H:%M')}"
    elif dt_result.date() == (now_dt - datetime.timedelta(days=1)).date():
        fmt_date = f"Ayer a las {dt_result.strftime('%H:%M')}"
    else:
        fmt_date = dt_result.strftime("%d/%m/%Y %H:%M")

    return {
        "dt": dt_result,
        "formatted": fmt_date,
        "relative": rel_str,
        "status_color": status_color,
        "status_badge": status_badge,
        "source": source_label
    }

snap_info = get_data_snapshot_info(user_ctx)


# =============================================================================
# SIDEBAR (CLEAN, ELEGANT, UNBOXED)
# =============================================================================

with st.sidebar:
    st.markdown("## ⚽ Fantasy BI")
    st.caption("Executive Tactical & Financial Hub")
    st.markdown("")

    st.markdown(f"👤 **Mánager:** `{my_team_name}`")
    st.markdown(f"🏆 **Liga:** `{league_name}`")
    st.markdown(f"⏳ **Próx. Inicio:** `{next_round_name} ({next_kickoff_str})`")
    st.caption(f"Cuenta atrás al corte: **{countdown_str}**")

    st.markdown("---")

    if st.button("🔄 Sincronizar Datos (Biwenger)", type="primary", width="stretch"):
        with st.status("Conectando con Biwenger y actualizando datos...", expanded=True) as status:
            try:
                st.write("• Extrayendo información de la liga y mercado...")
                from src.tools.data_extraction.runner import orchestrate_pipeline
                orchestrate_pipeline(extract=True)
                st.session_state["last_sync_timestamp"] = datetime.datetime.now()
                st.cache_data.clear()
                status.update(label="¡Actualización completada!", state="complete", expanded=False)
                st.rerun()
            except Exception as e:
                status.update(label="Error en la sincronización", state="error", expanded=True)
                st.error(f"Detalle del error: {e}")

    # SNAPSHOT TIMESTAMP CARD IN SIDEBAR
    st.markdown(f"""
    <div style="background: rgba(30, 41, 59, 0.7); border: 1px solid #334155; border-radius: 8px; padding: 10px 12px; margin-top: 10px;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <span style="font-size: 0.72rem; font-weight: 700; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px;">🔄 Última Sincronización</span>
            <span style="font-size: 0.72rem; padding: 2px 6px; border-radius: 4px; background: rgba(16, 185, 129, 0.15); color: {snap_info['status_color']}; font-weight: 600;">{snap_info['status_badge']}</span>
        </div>
        <div style="font-size: 0.95rem; font-weight: 700; color: #f8fafc; margin-top: 4px;">
            🕒 {snap_info['formatted']}
        </div>
        <div style="font-size: 0.78rem; color: #cbd5e1; margin-top: 2px;">
            ⏱️ Antigüedad: <b>{snap_info['relative']}</b> • <i>{snap_info['source']}</i>
        </div>
    </div>
    """, unsafe_allow_html=True)

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
# HEADER: TOP BANNER & 5 EXECUTIVE PERSISTENT KPIS (ZERO CUTOFF)
# =============================================================================

top_c1, top_c2 = st.columns([3, 2])
with top_c1:
    st.markdown(f"### ⚽ Biwenger Fantasy BI &bull; <span style='font-size: 1.1rem; color: #94a3b8; font-weight: 400;'>{league_name}</span>", unsafe_allow_html=True)
with top_c2:
    st.markdown(f"""
    <div style="text-align: right; padding-top: 6px;">
        <span style="display: inline-block; background: #1e293b; border: 1px solid #334155; border-radius: 20px; padding: 5px 12px; font-size: 0.8rem; color: #e2e8f0;">
            🕒 <b>Corte de datos:</b> {snap_info['formatted']} ({snap_info['relative']}) &nbsp;|&nbsp; <span style="color: {snap_info['status_color']}; font-weight: 600;">{snap_info['status_badge']}</span>
        </span>
    </div>
    """, unsafe_allow_html=True)

st.markdown("<div style='height: 4px;'></div>", unsafe_allow_html=True)

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
    max_bid_fmt = f"{max_bid_available/1_000_000:.2f} M€"
    cred_fmt = f"+{debt_allowance/1_000_000:.2f} M€ límite"
    st.metric(
        label="⚔️ Puja Máxima Disp.",
        value=max_bid_fmt,
        delta=cred_fmt,
        delta_color="normal"
    )

with kpi3:
    st.metric(
        label="👥 Valor Plantilla",
        value=f"{squad_value/1_000_000:.2f} M€",
        delta=f"{squad_count} futbolistas",
        delta_color="off"
    )

with kpi4:
    eq_fmt = f"{total_equity/1_000_000:.2f} M€"
    profit_fmt = f"{net_profit/1_000_000:+.2f} M€ neto ({net_profit_pct:+.1f}%)"
    st.metric(
        label="🏦 Patrimonio Total",
        value=eq_fmt,
        delta=profit_fmt,
        delta_color="normal" if net_profit >= 0 else "inverse"
    )

with kpi5:
    gain_val_str = f"{squad_day_gain/1_000:+.0f} K€" if abs(squad_day_gain) < 1_000_000 else f"{squad_day_gain/1_000_000:+.2f} M€"
    st.metric(
        label="📈 Evolución Hoy",
        value=gain_val_str,
        delta=f"{squad_day_pct:+.2f}% en 24h",
        delta_color="normal" if squad_day_gain >= 0 else "inverse"
    )

st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)


# =============================================================================
# CINTA DE SELECTORES (NAVIGATION TABS)
# =============================================================================

tab_finances, tab_squad, tab_rivals, tab_players, tab_agent = st.tabs([
    "📊 1. Situación & Finanzas",
    "🎽 2. Mi Plantilla",
    "🏆 3. Rivales y Liga",
    "🛒 4. Mercado y Jugadores",
    "💬 5. Chat Agente IA"
])


# =============================================================================
# TAB 1: SITUACIÓN DEL EQUIPO Y RESUMEN FINANCIERO (NUEVA SECCIÓN DEDICADA)
# =============================================================================

with tab_finances:
    # -------------------------------------------------------------------------
    # 1. SEMÁFORO DE CUMPLIMIENTO DE JORNADA & DEADLINE
    # -------------------------------------------------------------------------
    st.markdown("### ⏱️ Semáforo de Jornada y Regla de Saldo Positivo")

    if balance >= 0:
        st.success(
            f"🟢 **ESTADO OK PARA PUNTUAR:** Tu saldo actual es de **{format_price(balance)}**. "
            f"Cuentas con un colchón de seguridad de **{format_price(balance)}** antes de caer en números rojos. "
            f"El corte oficial para la **{next_round_name}** es el **{next_kickoff_str}** (cuenta atrás: **{countdown_str}**)."
        )
    else:
        deuda_abs = abs(balance)
        st.error(
            f"🚨 **ALERTA CRÍTICA — NÚMEROS ROJOS:** Tu saldo está en **{format_price(balance)}**. "
            f"Debes ingresar al menos **{format_price(deuda_abs)}** antes del inicio de la **{next_round_name}** "
            f"(**{next_kickoff_str}**, en **{countdown_str}**) o **NO PUNTUARÁS NADA** durante el fin de semana."
        )

    f_col1, f_col2, f_col3, f_col4 = st.columns(4)
    with f_col1:
        st.metric("⏳ Próximo Inicio", f"{next_round_name}", delta=next_kickoff_str, delta_color="off")
    with f_col2:
        st.metric("⏱️ Cuenta Atrás al Corte", countdown_str, delta="Saldo debe ser ≥ 0 €", delta_color="normal" if balance >= 0 else "inverse")
    with f_col3:
        st.metric("🛡️ Margen de Saldo", format_price(balance), delta="Positivo" if balance >= 0 else "Déficit a cubrir", delta_color="normal" if balance >= 0 else "inverse")
    with f_col4:
        st.metric("⚔️ Puja Máx. Permitida", format_price(max_bid_available), delta=f"+{format_price(debt_allowance)} crédito", delta_color="normal")

    st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)

    # -------------------------------------------------------------------------
    # 2. SALDO A COBRAR PROYECTADO (JORNADA EN CURSO / LIQUIDACIÓN)
    # -------------------------------------------------------------------------
    st.markdown(f"### 🏆 Saldo a Cobrar Proyectado ({active_round_name} · {active_round_status})")
    st.caption("Cálculo basado en las puntuaciones de tu once alineado y las primas económicas oficiales configuradas en tu liga.")

    p_col1, p_col2, p_col3, p_col4 = st.columns(4)
    with p_col1:
        st.metric(
            f"⚽ Puntos {active_round_name}",
            f"{my_round_points} pts",
            delta=f"Puesto {my_round_pos}º provisional" if my_round_pos > 0 else "En juego",
            delta_color="off"
        )
    with p_col2:
        pts_money = bonuses_breakdown.get("bonusPoint", my_round_points * 50_000)
        st.metric("💵 Prima por Puntos", format_price(pts_money), delta="50.000 € por punto", delta_color="normal")
    with p_col3:
        pos_money = bonuses_breakdown.get("bonusRoundPointsPosition", 0.0)
        st.metric("🏅 Prima por Posición", format_price(pos_money), delta=f"Puesto {my_round_pos}º" if my_round_pos > 0 else "Sin bonus", delta_color="normal" if pos_money > 0 else "off")
    with p_col4:
        st.metric(
            "💰 Total Primas Proyectadas",
            format_price(projected_bonuses_sum),
            delta=f"Saldo futuro: {format_price(projected_post_round_balance)}",
            delta_color="normal"
        )

    st.info(
        f"💡 **Saldo Proyectado Post-Liquidación:** Con tus **{my_round_points} puntos**, percibirás un ingreso de **+{format_price(projected_bonuses_sum)}**. "
        f"Tu saldo pasará automáticamente de **{format_price(balance)}** a **{format_price(projected_post_round_balance)}** en cuanto la administración de Biwenger liquide la jornada."
    )

    st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)

    # -------------------------------------------------------------------------
    # 3. BUFFER DE LIQUIDEZ INMEDIATA (OFERTAS EN FIRME)
    # -------------------------------------------------------------------------
    st.markdown("### 📑 Buffer de Liquidez Inmediata (Ofertas Activas en Firme)")
    st.caption("Ofertas oficiales recibidas de la máquina o rivales listas para ser aceptadas en Biwenger para convertir jugadores en efectivo.")

    if not my_squad.empty and "MARKET_OFFER_AMOUNT" in my_squad.columns:
        offers_squad = my_squad[my_squad["MARKET_OFFER_AMOUNT"].notna() & (my_squad["MARKET_OFFER_AMOUNT"] > 0)].copy()
    else:
        offers_squad = pd.DataFrame()

    if offers_squad.empty:
        st.write("ℹ️ Actualmente no tienes ofertas activas recibidas en tu plantilla.")
    else:
        offers_squad["_raw_p"] = pd.to_numeric(offers_squad["PLAYER_PRICE"], errors="coerce").fillna(0)
        offers_squad["_raw_o"] = pd.to_numeric(offers_squad["MARKET_OFFER_AMOUNT"], errors="coerce").fillna(0)
        offers_squad["_diff"] = offers_squad["_raw_o"] - offers_squad["_raw_p"]
        offers_squad["_diff_pct"] = (offers_squad["_diff"] / offers_squad["_raw_p"] * 100.0).fillna(0)

        offers_squad["Jugador"] = offers_squad["PLAYER_NAME"]
        offers_squad["Club"] = offers_squad["TEAM_NAME"]
        offers_squad["Pos"] = offers_squad["PLAYER_POSITION"]
        offers_squad["Valor Mercado"] = (offers_squad["_raw_p"] / 1_000_000).round(2)
        offers_squad["Oferta Recibida"] = (offers_squad["_raw_o"] / 1_000_000).round(2)
        offers_squad["Diferencia"] = (offers_squad["_diff"] / 1_000).round().astype(int)
        
        def _fmt_offer_eval(r):
            d = r["_diff"]
            if d > 0:
                return f"🟢 Ganas {r['_diff_pct']:+.1f}% sobre valor"
            elif d < 0:
                return f"🔴 Pierdes {r['_diff_pct']:.1f}% sobre valor"
            return "🟡 Oferta a valor exacto"
            
        offers_squad["Evaluación"] = offers_squad.apply(_fmt_offer_eval, axis=1)
        offers_squad["Expira"] = offers_squad.get("MARKET_OFFER_UNTIL", "Próximamente").fillna("Próximamente")

        st.dataframe(
            offers_squad[["Jugador", "Club", "Pos", "Valor Mercado", "Oferta Recibida", "Diferencia", "Evaluación", "Expira"]],
            column_config={
                "Jugador": st.column_config.TextColumn("Futbolista", width="medium"),
                "Club": st.column_config.TextColumn("Club", width="small"),
                "Pos": st.column_config.TextColumn("Posición", width="small"),
                "Valor Mercado": st.column_config.NumberColumn("Valor Mercado", format="%.2f M€", width="small"),
                "Oferta Recibida": st.column_config.NumberColumn("Oferta Recibida", format="%.2f M€", width="small"),
                "Diferencia": st.column_config.NumberColumn("Diferencia (K€)", format="%+d K€", width="small"),
                "Evaluación": st.column_config.TextColumn("Evaluación Económica", width="medium"),
                "Expira": st.column_config.TextColumn("Expira", width="medium")
            },
            width="stretch",
            hide_index=True
        )

        st.success(
            f"💼 **Liquidez en Firme Disponible:** Tienes **{len(offers_squad)} ofertas activas** por un total de "
            f"**{format_price(active_offers_sum)}**. Puedes aceptar cualquiera de ellas para obtener liquidez inmediata."
        )

    st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)

    # -------------------------------------------------------------------------
    # 4. SALUD PATRIMONIAL & CAPACIDAD CREDITICIA
    # -------------------------------------------------------------------------
    st.markdown("### 🏦 Salud Patrimonial y Capacidad de Crédito")
    
    cap_c1, cap_c2, cap_c3 = st.columns(3)
    with cap_c1:
        st.markdown("##### 📊 Reparto de Activos")
        pct_squad = (squad_value / total_equity * 100.0) if total_equity > 0 else 0.0
        pct_cash = (balance / total_equity * 100.0) if total_equity > 0 else 0.0
        st.write(f"• **Invertido en Futbolistas:** `{format_price(squad_value)}` ({pct_squad:.1f}%)")
        st.write(f"• **Tesorería / Saldo Líquido:** `{format_price(balance)}` ({pct_cash:.1f}%)")
        st.write(f"• **Patrimonio Total Neto:** `{format_price(total_equity)}`")
        st.write(f"• **Beneficio Acumulado:** `{net_profit/1e6:+.2f} M€` ({net_profit_pct:+.1f}%)")

    with cap_c2:
        st.markdown("##### 💳 Reglas de Endeudamiento")
        st.write(f"• **Regla de Liga:** `quarterTeam` (Máx 25% deuda)")
        st.write(f"• **Límite de Crédito Permitido:** `{format_price(debt_allowance)}`")
        st.write(f"• **Puja Máxima Absoluta:** `{format_price(max_bid_available)}`")
        st.write(f"• **Capacidad de Apalancamiento:** Hasta `{debt_allowance/1e6:.2f} M€` en pujas")

    with cap_c3:
        st.markdown("##### 🛡️ Estrategia de Liquidez")
        if balance >= 0:
            st.write("• **Situación:** Saneada y solvente.")
            st.write("• **Recomendación:** Mantener posiciones alcistas y usar crédito solo en oportunidades de alta convicción.")
        else:
            st.write("• **Situación:** Rebalanceo necesario.")
            st.write("• **Acción urgente:** Aceptar ofertas activas o listar jugadores suplentes en el mercado.")

    st.markdown("<div style='height: 16px;'></div>", unsafe_allow_html=True)


# =============================================================================
# TAB 2: MI PLANTILLA (DESARROLLADA)
# =============================================================================

with tab_squad:
    if my_squad.empty:
        st.info("ℹ️ No se han detectado jugadores en tu plantilla. Pulsa **'🔄 Sincronizar Datos'** en la barra lateral para descargar los datos de Biwenger.")
    else:
        # Enrich squad DataFrame
        df_view = my_squad.copy()

        # Enrich with predictive indicators if available
        if "pred_subida_24h" not in df_view.columns and not df_pred.empty and "player_id" in df_pred.columns:
            pred_squad_cols = [
                "player_id", "pred_subida_24h", "pred_pct_24h", "precio_est_24h",
                "pred_subida_72h_cum", "pred_roi_72h_pct", "precio_est_72h",
                "efr_72h_eur", "regimen_72h_desc", "prob_techo_pct", "prob_desplome_pct",
                "prob_suelo_pct", "dias_optimos_retencion", "accion_recomendada"
            ]
            sub_p = df_pred[[c for c in pred_squad_cols if c in df_pred.columns]].drop_duplicates(subset=["player_id"]).copy()
            df_view["_pid"] = pd.to_numeric(df_view["PLAYER_ID"], errors="coerce")
            sub_p["_pid"] = pd.to_numeric(sub_p["player_id"], errors="coerce")
            df_view = pd.merge(df_view, sub_p.drop(columns=["player_id"]), on="_pid", how="left")
            df_view.drop(columns=["_pid"], inplace=True)

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

        # Predictive indicators (v3 Engine)
        df_view["_raw_pred_24h"] = pd.to_numeric(df_view.get("pred_subida_24h", 0.0), errors="coerce").fillna(0.0)
        df_view["_raw_pred_72h"] = pd.to_numeric(df_view.get("pred_subida_72h_cum", 0.0), errors="coerce").fillna(0.0)
        df_view["_raw_roi_72h"] = pd.to_numeric(df_view.get("pred_roi_72h_pct", 0.0), errors="coerce").fillna(0.0)
        df_view["_raw_prob_techo"] = pd.to_numeric(df_view.get("prob_techo_pct", 0.0), errors="coerce").fillna(0.0)
        df_view["_raw_prob_desplome"] = pd.to_numeric(df_view.get("prob_desplome_pct", 0.0), errors="coerce").fillna(0.0)
        df_view["_raw_prob_suelo"] = pd.to_numeric(df_view.get("prob_suelo_pct", 0.0), errors="coerce").fillna(0.0)

        df_view["Acción IA"] = df_view.get("accion_recomendada", pd.Series("—", index=df_view.index)).fillna("—")
        df_view["Pred. 24h (K€)"] = (df_view["_raw_pred_24h"] / 1_000).round().astype(int)
        df_view["Previsión 72h (K€)"] = (df_view["_raw_pred_72h"] / 1_000).round().astype(int)
        df_view["ROI 72h (%)"] = df_view["_raw_roi_72h"].round(1)
        df_view["Retención"] = df_view.get("dias_optimos_retencion", pd.Series("—", index=df_view.index)).fillna("—")

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
        # ALERTAS TÁCTICAS IA EN PLANTILLA (TECHO INMINENTE & STOP-LOSS)
        # ---------------------------------------------------------------------
        squad_ati = df_view[
            (df_view["Acción IA"].str.contains("TECHO", na=False)) |
            (df_view["_raw_prob_techo"] >= 55.0)
        ]
        squad_stoploss = df_view[
            (df_view["Acción IA"].str.contains("STOP-LOSS", na=False)) |
            (df_view["_raw_prob_desplome"] >= 65.0)
        ]
        squad_gainers = df_view[
            (df_view["_raw_pred_72h"] >= 60_000)
        ]

        if not squad_ati.empty:
            ati_items = [f"**{r['PLAYER_NAME']}** ({r['Valor (M€)']}M€ · Techo: {r['_raw_prob_techo']:.0f}% · {r['Retención']})" for _, r in squad_ati.iterrows()]
            st.warning(f"⚠️ **ALERTA DE TECHO INMINENTE (ATI) EN TU EQUIPO:** {len(squad_ati)} futbolista(s) en agotamiento de subida: {', '.join(ati_items)}. El modelo prevé freno o caída en 24-48h. ¡Considera vender hoy en el punto máximo de rentabilidad!")

        if not squad_stoploss.empty:
            loss_k = int(squad_stoploss["_raw_pred_72h"].sum() / 1_000)
            sl_items = [f"**{r['PLAYER_NAME']}** ({r['Previsión 72h (K€)']} K€ · Desplome: {r['_raw_prob_desplome']:.0f}%)" for _, r in squad_stoploss.iterrows()]
            st.error(f"🛑 **ALERTA STOP-LOSS EN TU PLANTILLA:** {len(squad_stoploss)} futbolista(s) en desvalorización acusada (Pérdida 72h prevista: **{loss_k:+d} K€**): {', '.join(sl_items)}. Considera darles salida urgente para blindar tu patrimonio.")

        if not squad_gainers.empty:
            gain_k = int(squad_gainers["_raw_pred_72h"].sum() / 1_000)
            g_items = [f"**{r['PLAYER_NAME']}** ({r['Previsión 72h (K€)']:+d} K€ · ROI: {r['ROI 72h (%)']:+.1f}%)" for _, r in squad_gainers.iterrows()]
            st.success(f"🚀 **MOTORES DE REVALORIZACIÓN (+{gain_k} K€ a 72h):** {', '.join(g_items)}. Mantener en plantilla para capturar plusvalías.")

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
                    "🚀 Motores de subida (Prev. 72h > 0)",
                    "⚠️ Alerta Techo / Venta en pico",
                    "🛑 Alerta Stop-Loss (Desplome)",
                    "💎 Compra Sniper / Suelo",
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
                    "🔮 Mayor Previsión 72h (IA)",
                    "🚀 Mayor ROI Previsto 72h (%)",
                    "⚠️ Mayor Probabilidad de Techo",
                    "🛑 Mayor Riesgo de Desplome",
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
        elif status_filter == "🚀 Motores de subida (Prev. 72h > 0)":
            filtered_df = filtered_df[filtered_df["_raw_pred_72h"] > 0]
        elif status_filter == "⚠️ Alerta Techo / Venta en pico":
            filtered_df = filtered_df[(filtered_df["Acción IA"].str.contains("TECHO", na=False)) | (filtered_df["_raw_prob_techo"] >= 50.0)]
        elif status_filter == "🛑 Alerta Stop-Loss (Desplome)":
            filtered_df = filtered_df[(filtered_df["Acción IA"].str.contains("STOP-LOSS", na=False)) | (filtered_df["_raw_prob_desplome"] >= 60.0)]
        elif status_filter == "💎 Compra Sniper / Suelo":
            filtered_df = filtered_df[(filtered_df["Acción IA"].str.contains("SUELO", na=False)) | (filtered_df["_raw_prob_suelo"] >= 50.0)]
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
        elif sort_choice == "🔮 Mayor Previsión 72h (IA)":
            filtered_df = filtered_df.sort_values(by="_raw_pred_72h", ascending=False)
        elif sort_choice == "🚀 Mayor ROI Previsto 72h (%)":
            filtered_df = filtered_df.sort_values(by="_raw_roi_72h", ascending=False)
        elif sort_choice == "⚠️ Mayor Probabilidad de Techo":
            filtered_df = filtered_df.sort_values(by="_raw_prob_techo", ascending=False)
        elif sort_choice == "🛑 Mayor Riesgo de Desplome":
            filtered_df = filtered_df.sort_values(by="_raw_prob_desplome", ascending=False)
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
            "Valor (M€)", "Subida 24h (K€)", "Tendencia",
            "Acción IA", "Pred. 24h (K€)", "Previsión 72h (K€)", "ROI 72h (%)", "Retención",
            "Oferta (M€)", "Cláusula (M€)", "Vendible"
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
                "Acción IA": st.column_config.TextColumn("Señal IA", help="Recomendación táctica del modelo cuantitativo v3", width="medium"),
                "Pred. 24h (K€)": st.column_config.NumberColumn("Pred. 24h", help="Variación prevista de valor en las próximas 24h", format="%+d K€", width="small"),
                "Previsión 72h (K€)": st.column_config.NumberColumn("Prev. 72h", help="Variación prevista acumulada en 3 días (72h)", format="%+d K€", width="small"),
                "ROI 72h (%)": st.column_config.NumberColumn("ROI 72h", help="Retorno porcentual previsto a 3 días", format="%+.1f%%", width="small"),
                "Retención": st.column_config.TextColumn("Retención", help="Horizonte recomendado de posesión", width="small"),
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

        # ---------------------------------------------------------------------
        # RADAR DE CLÁUSULAS ASUMIBLES EN RIVALES
        # ---------------------------------------------------------------------
        st.markdown("<div style='height: 16px;'></div>", unsafe_allow_html=True)
        st.markdown("#### ⚡ Radar de Cláusulas Asumibles en Rivales (Clausulazos)")
        st.caption("Futbolistas clave de tus rivales directos cuyas cláusulas de rescisión puedes pagar hoy mismo con tu saldo o mediante endeudamiento permitido.")

        if not df_players.empty and "BIWPLAYER_CLAUSE" in df_players.columns:
            df_clauses = df_players[
                (df_players["BIWPLAYER_TEAM_NAME"].notna()) &
                (df_players["BIWPLAYER_TEAM_NAME"] != my_team_name) &
                (df_players["BIWPLAYER_CLAUSE"].notna()) &
                (df_players["BIWPLAYER_CLAUSE"] > 0)
            ].copy()

            df_clauses["_raw_cl"] = pd.to_numeric(df_clauses["BIWPLAYER_CLAUSE"], errors="coerce").fillna(0)
            df_clauses["_raw_pr"] = pd.to_numeric(df_clauses["PLAYER_PRICE"], errors="coerce").fillna(0)
            df_clauses["_raw_pts"] = pd.to_numeric(df_clauses.get("PLAYER_POINTS", 0), errors="coerce").fillna(0)

            # Filter options for radar
            c_rad1, c_rad2 = st.columns([2.5, 2.5])
            with c_rad1:
                rad_filter = st.selectbox(
                    "Filtrar Radar de Cláusulas:",
                    options=[
                        f"💰 Cláusulas viables con mi Saldo actual (≤ {format_price(balance)})",
                        f"🚀 Cláusulas viables con mi Puja Máx. (≤ {format_price(max_bid_available)})",
                        "⭐ Todas las cláusulas de rivales ordenadas por puntos"
                    ],
                    index=0,
                    key="rad_filter_choice"
                )

            with c_rad2:
                rad_pos = st.selectbox(
                    "Filtrar Posición:",
                    options=["Todas las líneas", "🧤 Porteros (GK)", "🛡️ Defensas (DF)", "⚙️ Medios (MF)", "⚡ Delanteros (FW)"],
                    index=0,
                    key="rad_pos_choice"
                )

            filtered_rad = df_clauses.copy()

            if "Saldo actual" in rad_filter:
                filtered_rad = filtered_rad[filtered_rad["_raw_cl"] <= balance]
            elif "Puja Máx" in rad_filter:
                filtered_rad = filtered_rad[filtered_rad["_raw_cl"] <= max_bid_available]

            if rad_pos == "🧤 Porteros (GK)":
                filtered_rad = filtered_rad[filtered_rad["PLAYER_POSITION"] == "GK"]
            elif rad_pos == "🛡️ Defensas (DF)":
                filtered_rad = filtered_rad[filtered_rad["PLAYER_POSITION"] == "DF"]
            elif rad_pos == "⚙️ Medios (MF)":
                filtered_rad = filtered_rad[filtered_rad["PLAYER_POSITION"] == "MF"]
            elif rad_pos == "⚡ Delanteros (FW)":
                filtered_rad = filtered_rad[filtered_rad["PLAYER_POSITION"] == "FW"]

            if filtered_rad.empty:
                st.info("ℹ️ No hay jugadores que cumplan los criterios seleccionados en este filtro de cláusulas.")
            else:
                filtered_rad = filtered_rad.sort_values(by="_raw_pts", ascending=False)
                
                filtered_rad["Jugador"] = filtered_rad["PLAYER_NAME"]
                filtered_rad["Club"] = filtered_rad["TEAM_NAME"]
                filtered_rad["Rival Dueño"] = filtered_rad["BIWPLAYER_TEAM_NAME"]
                filtered_rad["Puntos"] = filtered_rad["_raw_pts"].astype(int)
                filtered_rad["Titularidad"] = (pd.to_numeric(filtered_rad.get("COMUNIATE_STARTER", 0.0), errors="coerce").fillna(0.0) * 100).astype(int)
                filtered_rad["Valor (M€)"] = (filtered_rad["_raw_pr"] / 1_000_000).round(2)
                filtered_rad["Cláusula (M€)"] = (filtered_rad["_raw_cl"] / 1_000_000).round(2)

                def _fmt_viability(row):
                    cl = row["_raw_cl"]
                    if cl <= balance:
                        return f"🟢 Saldo OK (sobran {format_price(balance - cl)})"
                    elif cl <= max_bid_available:
                        return f"🟡 Con Crédito (requiere {format_price(cl - balance)} de deuda)"
                    return f"🔴 Inasumible hoy (faltan {format_price(cl - max_bid_available)})"

                filtered_rad["Viabilidad"] = filtered_rad.apply(_fmt_viability, axis=1)

                st.dataframe(
                    filtered_rad[["Jugador", "Club", "Rival Dueño", "Titularidad", "Puntos", "Valor (M€)", "Cláusula (M€)", "Viabilidad"]],
                    column_config={
                        "Jugador": st.column_config.TextColumn("Futbolista", width="medium"),
                        "Club": st.column_config.TextColumn("Club", width="small"),
                        "Rival Dueño": st.column_config.TextColumn("Rival Propietario", width="medium"),
                        "Titularidad": st.column_config.ProgressColumn("Titularidad", format="%d%%", min_value=0, max_value=100, width="small"),
                        "Puntos": st.column_config.NumberColumn("Pts", format="%d", width="small"),
                        "Valor (M€)": st.column_config.NumberColumn("Valor Mercado", format="%.2f M€", width="small"),
                        "Cláusula (M€)": st.column_config.NumberColumn("Cláusula", format="%.2f M€", width="small"),
                        "Viabilidad": st.column_config.TextColumn("Diagnóstico Clausulazo", width="medium")
                    },
                    width="stretch",
                    height=360,
                    hide_index=True
                )
                st.caption(f"Mostrando **{len(filtered_rad)}** oportunidades de clausulazo identificadas.")

        # ---------------------------------------------------------------------
        # FEED DE ÚLTIMOS TRASPASOS Y MOVIMIENTOS DE LA LIGA
        # ---------------------------------------------------------------------
        st.markdown("<div style='height: 16px;'></div>", unsafe_allow_html=True)
        st.markdown("#### 📋 Feed de Últimos Traspasos y Fichajes de la Liga")
        st.caption("Registro cronológico de los fichajes, ventas al mercado y cláusulazos ejecutados recientemente en la liga.")

        if not df_transfers_raw.empty:
            feed_df = df_transfers_raw.head(25).copy()
            feed_df["_raw_amt"] = pd.to_numeric(feed_df["amount"], errors="coerce").fillna(0)

            # Map player details
            if not df_players.empty:
                pid_map = dict(zip(df_players["PLAYER_ID"].astype(str), df_players["PLAYER_NAME"]))
                club_map = dict(zip(df_players["PLAYER_ID"].astype(str), df_players["TEAM_NAME"]))
                val_map = dict(zip(df_players["PLAYER_ID"].astype(str), pd.to_numeric(df_players["PLAYER_PRICE"], errors="coerce").fillna(0)))
            else:
                pid_map, club_map, val_map = {}, {}, {}

            feed_df["_str_pid"] = feed_df["player_id"].astype(str)
            feed_df["Jugador"] = feed_df["_str_pid"].map(pid_map).fillna("Jugador desconocido")
            feed_df["Club"] = feed_df["_str_pid"].map(club_map).fillna("LaLiga")
            feed_df["_val_p"] = feed_df["_str_pid"].map(val_map).fillna(0)

            def _fmt_op_type(row):
                is_cl = bool(row.get("clause", False))
                t = str(row.get("type", "")).lower()
                b = str(row.get("buyer_name", "")).strip()
                s = str(row.get("seller_name", "")).strip()
                if is_cl:
                    return "⚡ Clausulazo"
                if t == "market_buy" or s == "Mercado":
                    return "🛒 Fichaje Mercado"
                if t == "user_sale" or b == "Mercado":
                    return "💵 Venta a Mercado"
                return "🔄 Traspaso entre Rivales"

            feed_df["Operación"] = feed_df.apply(_fmt_op_type, axis=1)
            feed_df["Comprador"] = feed_df["buyer_name"].fillna("Mercado")
            feed_df["Vendedor"] = feed_df["seller_name"].fillna("Mercado")
            feed_df["Precio Pagado (M€)"] = (feed_df["_raw_amt"] / 1_000_000).round(2)

            def _fmt_diff_transfer(row):
                amt = row["_raw_amt"]
                vp = row["_val_p"]
                if vp <= 0 or amt <= 0:
                    return "—"
                diff = amt - vp
                diff_pct = (diff / vp * 100.0)
                if abs(diff) < 5_000:
                    return "A valor exacto"
                elif diff > 0:
                    return f"+{diff/1e3:.0f} K€ (+{diff_pct:.1f}%)"
                else:
                    return f"{diff/1e3:.0f} K€ ({diff_pct:.1f}%)"

            feed_df["Sobreprecio vs Valor"] = feed_df.apply(_fmt_diff_transfer, axis=1)
            feed_df["Fecha"] = pd.to_datetime(feed_df["date"], errors="coerce").dt.strftime("%d/%m %H:%M").fillna(feed_df["date"].astype(str))

            st.dataframe(
                feed_df[["Fecha", "Jugador", "Club", "Operación", "Comprador", "Vendedor", "Precio Pagado (M€)", "Sobreprecio vs Valor"]],
                column_config={
                    "Fecha": st.column_config.TextColumn("Fecha", width="small"),
                    "Jugador": st.column_config.TextColumn("Futbolista", width="medium"),
                    "Club": st.column_config.TextColumn("Club", width="small"),
                    "Operación": st.column_config.TextColumn("Tipo Operación", width="medium"),
                    "Comprador": st.column_config.TextColumn("Comprador", width="medium"),
                    "Vendedor": st.column_config.TextColumn("Vendedor", width="medium"),
                    "Precio Pagado (M€)": st.column_config.NumberColumn("Precio Pagado", format="%.2f M€", width="small"),
                    "Sobreprecio vs Valor": st.column_config.TextColumn("Sobreprecio / Descuento", width="medium")
                },
                width="stretch",
                height=400,
                hide_index=True
            )
        else:
            st.write("ℹ️ No hay registros recientes de movimientos de mercado en la base de datos.")


# =============================================================================
# TAB 4: MERCADO Y JUGADORES (CENTRO DE SUBASTAS Y SCOUTING)
# =============================================================================

with tab_players:
    if df_players.empty:
        st.info("ℹ️ No hay datos del maestro de jugadores disponibles. Pulsa **'🔄 Sincronizar Datos'** en la barra lateral.")
    else:
        df_all = df_players.copy()

        # Merge predictions if available
        pred_cols_all = [
            "player_id", "pred_subida_24h", "pred_pct_24h", "precio_est_24h",
            "pred_subida_48h_cum", "pred_roi_48h_pct", "precio_est_48h",
            "pred_subida_72h_cum", "pred_roi_72h_pct", "precio_est_72h",
            "efr_72h_eur", "regimen_72h_desc", "prob_explosion_pct", "prob_desplome_pct",
            "prob_techo_pct", "prob_suelo_pct", "dias_optimos_retencion", "accion_recomendada"
        ]

        if not df_pred.empty and "player_id" in df_pred.columns:
            cols_to_pull = [c for c in pred_cols_all if c in df_pred.columns]
            pred_sub = df_pred[cols_to_pull].drop_duplicates(subset=["player_id"]).copy()
            overlap = [c for c in cols_to_pull if c in df_all.columns and c != "player_id"]
            if overlap:
                df_all.drop(columns=overlap, inplace=True)
            df_all["_pid"] = pd.to_numeric(df_all["PLAYER_ID"], errors="coerce")
            pred_sub["_pid"] = pd.to_numeric(pred_sub["player_id"], errors="coerce")
            df_all = pd.merge(df_all, pred_sub.drop(columns=["player_id"]), on="_pid", how="left")
            df_all.drop(columns=["_pid"], inplace=True)

        for c in ["pred_subida_24h", "precio_est_24h", "pred_subida_72h_cum", "precio_est_72h", "pred_roi_72h_pct", "efr_72h_eur", "prob_techo_pct", "prob_suelo_pct", "prob_desplome_pct", "prob_explosion_pct"]:
            if c not in df_all.columns:
                df_all[c] = 0.0
            else:
                df_all[c] = pd.to_numeric(df_all[c], errors="coerce").fillna(0.0)

        if "accion_recomendada" not in df_all.columns:
            df_all["accion_recomendada"] = "—"
        else:
            df_all["accion_recomendada"] = df_all["accion_recomendada"].fillna("—")

        if "dias_optimos_retencion" not in df_all.columns:
            df_all["dias_optimos_retencion"] = "—"
        else:
            df_all["dias_optimos_retencion"] = df_all["dias_optimos_retencion"].fillna("—")

        if "regimen_72h_desc" not in df_all.columns:
            df_all["regimen_72h_desc"] = "—"
        else:
            df_all["regimen_72h_desc"] = df_all["regimen_72h_desc"].fillna("—")

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
        df_all["_raw_pred_24h"] = _to_num(df_all["pred_subida_24h"])
        df_all["_raw_precio_est_24h"] = _to_num(df_all["precio_est_24h"])
        df_all["_raw_pred_72h"] = _to_num(df_all["pred_subida_72h_cum"])
        df_all["_raw_precio_est_72h"] = _to_num(df_all["precio_est_72h"])
        df_all["_raw_roi_72h"] = _to_num(df_all["pred_roi_72h_pct"])
        df_all["_raw_efr"] = _to_num(df_all["efr_72h_eur"])
        df_all["_raw_prob_techo"] = _to_num(df_all["prob_techo_pct"])
        df_all["_raw_prob_suelo"] = _to_num(df_all["prob_suelo_pct"])
        df_all["_raw_prob_desplome"] = _to_num(df_all["prob_desplome_pct"])

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
        df_all["Pred. 24h (K€)"] = (df_all["_raw_pred_24h"] / 1_000).round().astype(int)
        df_all["Previsión 72h (K€)"] = (df_all["_raw_pred_72h"] / 1_000).round().astype(int)
        df_all["Precio Est. 24h (M€)"] = (df_all["_raw_precio_est_24h"] / 1_000_000).round(2)
        df_all["Precio Est. 72h (M€)"] = (df_all["_raw_precio_est_72h"] / 1_000_000).round(2)
        df_all["ROI 72h (%)"] = df_all["_raw_roi_72h"].round(1)
        df_all["EFR (K€)"] = (df_all["_raw_efr"] / 1_000).round().astype(int)
        df_all["Retención"] = df_all["dias_optimos_retencion"]
        df_all["Régimen 72h"] = df_all["regimen_72h_desc"]
        df_all["Acción IA"] = df_all["accion_recomendada"]

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

        # ---------------------------------------------------------------------
        # CLASIFICACIÓN DE JUGADORES EN LOS 3 GRUPOS DE FICHAJE
        # ---------------------------------------------------------------------
        df_all["_is_mine"] = (df_all["BIWPLAYER_TEAM_NAME"].astype(str).str.strip() == my_team_name)

        # 1. Libres en el Mercado (Pujables contra la CPU / Mercado hoy)
        mkt_seller_str = df_all["MARKET_SALE_USER_NAME"].astype(str).str.lower().str.strip()
        team_str = df_all["BIWPLAYER_TEAM_NAME"].astype(str).str.lower().str.strip()
        df_all["_is_free_mkt"] = (
            (df_all["_raw_market_sale"] > 0) & 
            (mkt_seller_str.isin(["mercado", "none", "nan", ""]) | team_str.isin(["none", "nan", ""])) &
            (~df_all["_is_mine"])
        )

        # 2. Puestos en Venta por Rivales (Subasta de rivales)
        df_all["_is_rival_sale"] = (
            (df_all["_raw_market_sale"] > 0) & 
            (~mkt_seller_str.isin(["mercado", "none", "nan", ""])) &
            (~df_all["_is_mine"])
        )

        # 3. Clausulables a Rivales (Cláusula activa sin bloqueo temporal)
        has_lock = df_all["BIWPLAYER_CLAUSE_LOCKED_UNTIL"].notna() & (df_all["BIWPLAYER_CLAUSE_LOCKED_UNTIL"].astype(str).str.strip() != "") & (df_all["BIWPLAYER_CLAUSE_LOCKED_UNTIL"].astype(str).str.lower() != "nan")
        df_all["_is_clausulable"] = (
            (df_all["_raw_clause"] > 0) & 
            (df_all["BIWPLAYER_TEAM_NAME"].notna()) & 
            (~team_str.isin(["none", "nan", ""])) &
            (~df_all["_is_mine"]) &
            (~has_lock)
        )

        # 4. Todos los Fichables Hoy (Unión de los 3 grupos)
        df_all["_is_fichable_hoy"] = df_all["_is_free_mkt"] | df_all["_is_rival_sale"] | df_all["_is_clausulable"]

        # Vendedor display
        def _fmt_seller(row):
            if row["_is_mine"]:
                return f"⭐ {my_team_name} (Tú)"
            if row["_is_free_mkt"]:
                return "🤖 Mercado (Libre)"
            if row["_is_rival_sale"]:
                return f"👥 {row.get('MARKET_SALE_USER_NAME', 'Rival')}"
            if row["_is_clausulable"]:
                return f"👤 {row.get('BIWPLAYER_TEAM_NAME', 'Rival')}"
            owner = str(row.get("BIWPLAYER_TEAM_NAME", "")).strip()
            return f"👥 {owner}" if owner and owner.lower() != "nan" else "🆓 Sin Club"

        df_all["Vendedor"] = df_all.apply(_fmt_seller, axis=1)

        # Auction end time display
        def _fmt_until(row):
            u = row.get("MARKET_SALE_UNTIL")
            if pd.isna(u) or not str(u).strip() or str(u).lower() == "nan":
                return "—"
            try:
                dt_u = pd.to_datetime(u)
                return dt_u.strftime("%d/%m %H:%M")
            except Exception:
                return str(u)[:16]

        df_all["Fin Subasta"] = df_all.apply(_fmt_until, axis=1)

        # Numeric market price and clause in M€
        df_all["Precio Salida (M€)"] = df_all["_raw_market_sale"].apply(lambda s: round(s / 1_000_000, 2) if pd.notna(s) and s > 0 else np.nan)
        df_all["Cláusula (M€)"] = df_all["_raw_clause"].apply(lambda c: round(c / 1_000_000, 2) if pd.notna(c) and c > 0 else np.nan)

        # Viabilidad Fichaje Diagnóstico
        def _fmt_viability_mkt(row):
            if row["_is_mine"]:
                return "⭐ En Plantilla"
            
            sale_p = float(row.get("_raw_market_sale", 0.0))
            cl_p = float(row.get("_raw_clause", 0.0))

            # Cost to acquire
            primary_cost = sale_p if sale_p > 0 else cl_p
            if primary_cost <= 0:
                return "🔒 No en venta hoy"

            is_clause_only = (sale_p <= 0 and cl_p > 0)
            prefix = "⚡ Clausulazo" if is_clause_only else "🛒 Puja"

            if primary_cost <= balance:
                return f"🟢 {prefix} (Saldo OK)"
            elif primary_cost <= max_bid_available:
                return f"🟡 {prefix} (Con Crédito)"
            else:
                return f"🔴 {prefix} (Inasumible hoy)"

        df_all["Viabilidad"] = df_all.apply(_fmt_viability_mkt, axis=1)

        # Other presentation fields
        df_all["Titularidad"] = (df_all["_raw_starter"] * 100).astype(int)
        df_all["Puntos"] = df_all["_raw_points"]
        df_all["Percentil"] = (df_all["_raw_percentile"] * 100).round().astype(int)
        df_all["Media"] = df_all["_raw_avg"]
        df_all["xP"] = df_all["_raw_xp"]

        # Dynamic Counts for summary pills
        total_p = len(df_all)
        count_free = int(df_all["_is_free_mkt"].sum())
        count_rival_sale = int(df_all["_is_rival_sale"].sum())
        count_clausulable = int(df_all["_is_clausulable"].sum())
        count_fichables = int(df_all["_is_fichable_hoy"].sum())

        afford_balance_mask = (
            ((df_all["_raw_market_sale"] > 0) & (df_all["_raw_market_sale"] <= balance)) |
            ((df_all["_is_clausulable"]) & (df_all["_raw_clause"] <= balance))
        ) & (~df_all["_is_mine"])
        count_afford_bal = int(afford_balance_mask.sum())

        afford_maxbid_mask = (
            ((df_all["_raw_market_sale"] > 0) & (df_all["_raw_market_sale"] <= max_bid_available)) |
            ((df_all["_is_clausulable"]) & (df_all["_raw_clause"] <= max_bid_available))
        ) & (~df_all["_is_mine"])
        count_afford_mb = int(afford_maxbid_mask.sum())

        # ---------------------------------------------------------------------
        # SUMMARY PILLS DE MERCADO (ENFOCADO EN LOS 3 GRUPOS)
        # ---------------------------------------------------------------------
        st.markdown(f"""
        <div class="roster-summary-bar">
            <div class="roster-pill">
                <span>🔥 <b>1. Libres en Subasta Hoy:</b></span>
                <span style="color: #0284c7;"><b>{count_free}</b> jugadores (CPU)</span>
            </div>
            <div class="roster-pill">
                <span>👥 <b>2. A la Venta por Rivales:</b></span>
                <span style="color: #f59e0b;"><b>{count_rival_sale}</b> jugadores</span>
            </div>
            <div class="roster-pill">
                <span>⚡ <b>3. Clausulables a Rivales:</b></span>
                <span><b>{count_clausulable}</b> jugadores</span>
            </div>
            <div class="roster-pill">
                <span>🎯 <b>Total Fichables Hoy:</b></span>
                <span style="color: #10b981;"><b>{count_fichables}</b> opciones</span>
            </div>
            <div class="roster-pill">
                <span>💰 <b>Pujables con tu Saldo:</b></span>
                <span style="color: #10b981;"><b>{count_afford_bal}</b> (≤ {format_price(balance)})</span>
            </div>
            <div class="roster-pill">
                <span>🚀 <b>Pujables con Puja Máx:</b></span>
                <span style="color: #8b5cf6;"><b>{count_afford_mb}</b> (≤ {format_price(max_bid_available)})</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # ---------------------------------------------------------------------
        # GUÍA TÁCTICA DEL MOTOR PREDICTIVO IA (DESPLEGABLE EXPLICATIVO)
        # ---------------------------------------------------------------------
        with st.expander("🧠 **Guía Táctica del Motor Predictivo: ¿Cómo interpretar las Señales e Indicadores IA?**", expanded=False):
            g1, g2 = st.columns(2)
            with g1:
                st.markdown("""
                ##### 🎯 Señales Operativas de Mercado
                * **🚀 COMPRA FUERTE (CHOLLO MICRO / JOYA MEDIA):** Futbolistas económicos o de precio moderado en plena fase de aceleración con elevado **ROI esperado a 72h (>2.5% - 5%)**. Son las mejores oportunidades para especular y ganar dinero rápido.
                * **📈 MANTENER (SUBIDA ORGÁNICA):** Jugadores con inercia compradora continuada. Mantener en plantilla para seguir sumando valor diario.
                * **⚠️ VENTA EN PICO (TECHO INMINENTE - ATI):** Detecta desaceleración acusada de compras, aceleración negativa y saturación de liquidez. La subida se frenará en seco o caerá en 24-48h. **Vender en máximos de ciclo.**
                * **💎 COMPRA SNIPER (SUELO INMINENTE - ASI):** Futbolistas que venían en caída pero cuyo ritmo vendedor se neutraliza y la presión gira al alza. Punto de entrada perfecto antes del rebote.
                * **🛑 VENTA URGENTE (STOP-LOSS):** Desplome en curso o pérdida de titularidad/lesión. Salida inmediata para proteger tu presupuesto.
                * **👑 MANTENER (CRACK EN SUBIDA):** Activos de élite (>8M€) con tendencia económica positiva y peso clave en el once.
                * **⏸️ MANTENER / ESPERAR:** Jugadores con variación neutral o mercado lateral sin tendencia clara.
                """)
            with g2:
                st.markdown("""
                ##### 📊 Métricas Cuantitativas Explicadas
                * **Pred. 24h & Previsión 72h (K€):** Incremento estimado de precio en miles de euros para mañana y acumulado en 3 días por el ensamble de ML (HistGB + RF + ExtraTrees + Ridge).
                * **Precio Est. 24h / 72h (M€):** Valor proyectado absoluto del futbolista al término de cada ventana temporal.
                * **ROI 72h (%):** Retorno esperado de la inversión (*Return On Investment*) respecto a su precio actual en 3 días.
                * **EFR (€ - Expected Financial Return):** Beneficio neto esperado ponderado por probabilidades de subida vs riesgo de depreciación.
                * **Retención Óptima:** Horizonte sugerido de tenencia antes de agotar la inercia (*"1 día (Vender en 24h)"*, *"2 a 3 días"*, *"3+ días"* o *"0 días (Venta inmediata)"*).
                """)

        # ---------------------------------------------------------------------
        # FILTROS Y CONTROLES OPERATIVOS (2 FILAS LIMPIAS)
        # ---------------------------------------------------------------------
        row1_c1, row1_c2, row1_c3, row1_c4 = st.columns([1.8, 1.4, 1.4, 1.4])

        with row1_c1:
            f_group = st.selectbox(
                "🎯 Grupo de Fichaje:",
                options=[
                    f"🔥 1. Libres en el Mercado (Subasta CPU Hoy) [{count_free}]",
                    f"👥 2. En Venta por Rivales (Subasta de Rivales) [{count_rival_sale}]",
                    f"⚡ 3. Clausulables a Rivales [{count_clausulable}]",
                    f"🎯 4. Todos los Fichables Hoy (Unión de los 3 grupos) [{count_fichables}]",
                    f"🌐 5. Base de Datos Completa de LaLiga ({total_p} futbolistas)"
                ],
                index=0,
                key="mkt_f_group"
            )

        with row1_c2:
            f_afford = st.selectbox(
                "💰 Capacidad Financiera:",
                options=[
                    "Cualquier precio",
                    f"🟢 Asequibles con mi Saldo actual (≤ {format_price(balance)})",
                    f"🚀 Asequibles con mi Puja Máx. (≤ {format_price(max_bid_available)})",
                    "🏷️ Chollos (Salida < Valor Mercado)"
                ],
                index=0,
                key="mkt_f_afford"
            )

        with row1_c3:
            f_pos = st.selectbox(
                "Línea Táctica:",
                options=["Todas las líneas", "🧤 Porteros (GK)", "🛡️ Defensas (DF)", "⚙️ Medios (MF)", "⚡ Delanteros (FW)"],
                index=0,
                key="all_p_pos"
            )

        with row1_c4:
            all_teams = sorted([t for t in df_all["TEAM_NAME"].dropna().unique() if str(t).strip()])
            f_team = st.selectbox(
                "Club de LaLiga:",
                options=["Todos los clubes"] + all_teams,
                index=0,
                key="all_p_team"
            )

        row2_c1, row2_c2, row2_c3, row2_c4 = st.columns([1.5, 1.5, 1.6, 1.4])

        with row2_c1:
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

        with row2_c2:
            f_ai = st.selectbox(
                "Señal IA & Previsión:",
                options=[
                    "Todas las señales IA",
                    "🚀 Chollos Micro (< 1.5M con alto ROI)",
                    "💎 Joyas Gama Media (1.5M - 5M con alto ROI)",
                    "🎯 Alertas Suelo Inminente (ASI - Compra Sniper)",
                    "⚠️ Alertas Techo Inminente (ATI - Vender en Máximos)",
                    "🛑 Alarmas Desplome (Stop-Loss / Salida Urgente)",
                    "👑 Cracks de Élite (> 8M en subida)",
                    "📈 Subida Orgánica / Mantener",
                    "🔮 Previsión 72h Positiva (> 0 €)",
                    "⏸️ Mercado Lateral / Esperar"
                ],
                index=0,
                key="all_p_ai"
            )

        with row2_c3:
            f_sort = st.selectbox(
                "Ordenar Jugadores por:",
                options=[
                    "💰 Mayor Valor de Mercado",
                    "🏷️ Menor Precio de Salida (Económicos)",
                    "🔮 Mayor Subida Prevista a 72h (IA)",
                    "🚀 Mayor ROI Previsto 72h (%)",
                    "💎 Mayor Retorno Esperado EFR (€)",
                    "⚠️ Mayor Riesgo de Techo Inminente (%)",
                    "🎯 Mayor Probabilidad de Suelo (%)",
                    "📈 Mayor Subida 24h",
                    "📉 Mayor Caída 24h (Stop-Loss)",
                    "⭐ Más Puntos Fantasy",
                    "📊 Mayor Percentil Puntos",
                    "⚽ Mayor Titularidad (%)",
                    "🎯 Mayor xP (Próx. Partido)",
                    "⏱️ Fin de Subasta Próximo"
                ],
                index=0,
                key="all_p_sort"
            )

        with row2_c4:
            f_search = st.text_input("🔍 Buscar:", placeholder="Jugador o Club...", key="all_p_search")

        # ---------------------------------------------------------------------
        # APLICAR FILTROS MATEMÁTICOS
        # ---------------------------------------------------------------------
        filtered_all = df_all.copy()

        # 1. Grupo de Fichaje
        if "1. Libres en el Mercado" in f_group:
            filtered_all = filtered_all[filtered_all["_is_free_mkt"]]
        elif "2. En Venta por Rivales" in f_group:
            filtered_all = filtered_all[filtered_all["_is_rival_sale"]]
        elif "3. Clausulables a Rivales" in f_group:
            filtered_all = filtered_all[filtered_all["_is_clausulable"]]
        elif "4. Todos los Fichables Hoy" in f_group:
            filtered_all = filtered_all[filtered_all["_is_fichable_hoy"]]

        # 2. Capacidad Financiera
        if "Saldo actual" in f_afford:
            filtered_all = filtered_all[
                ((filtered_all["_raw_market_sale"] > 0) & (filtered_all["_raw_market_sale"] <= balance)) |
                ((filtered_all["_raw_market_sale"] <= 0) & (filtered_all["_raw_clause"] > 0) & (filtered_all["_raw_clause"] <= balance))
            ]
        elif "Puja Máx" in f_afford:
            filtered_all = filtered_all[
                ((filtered_all["_raw_market_sale"] > 0) & (filtered_all["_raw_market_sale"] <= max_bid_available)) |
                ((filtered_all["_raw_market_sale"] <= 0) & (filtered_all["_raw_clause"] > 0) & (filtered_all["_raw_clause"] <= max_bid_available))
            ]
        elif "Chollos" in f_afford:
            filtered_all = filtered_all[
                (filtered_all["_raw_market_sale"] > 0) & (filtered_all["_raw_market_sale"] < filtered_all["_raw_price"])
            ]

        # 3. Posición
        if f_pos == "🧤 Porteros (GK)":
            filtered_all = filtered_all[filtered_all["PLAYER_POSITION"] == "GK"]
        elif f_pos == "🛡️ Defensas (DF)":
            filtered_all = filtered_all[filtered_all["PLAYER_POSITION"] == "DF"]
        elif f_pos == "⚙️ Medios (MF)":
            filtered_all = filtered_all[filtered_all["PLAYER_POSITION"] == "MF"]
        elif f_pos == "⚡ Delanteros (FW)":
            filtered_all = filtered_all[filtered_all["PLAYER_POSITION"] == "FW"]

        # 4. Club
        if f_team != "Todos los clubes":
            filtered_all = filtered_all[filtered_all["TEAM_NAME"] == f_team]

        # 5. Condición & Titularidad
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

        # 6. Señal IA
        if "Chollos Micro" in f_ai:
            filtered_all = filtered_all[
                ((filtered_all["_raw_price"] < 1_500_000) & (filtered_all["_raw_roi_72h"] >= 3.0)) |
                (filtered_all["Acción IA"].str.contains("CHOLLO", na=False))
            ]
        elif "Joyas Gama Media" in f_ai:
            filtered_all = filtered_all[
                ((filtered_all["_raw_price"] >= 1_500_000) & (filtered_all["_raw_price"] <= 5_000_000) & (filtered_all["_raw_roi_72h"] >= 2.0)) |
                (filtered_all["Acción IA"].str.contains("JOYA", na=False))
            ]
        elif "Alertas Suelo Inminente" in f_ai:
            filtered_all = filtered_all[
                (filtered_all["_raw_prob_suelo"] >= 50.0) |
                (filtered_all["Acción IA"].str.contains("SUELO", na=False))
            ]
        elif "Alertas Techo Inminente" in f_ai:
            filtered_all = filtered_all[
                (filtered_all["_raw_prob_techo"] >= 50.0) |
                (filtered_all["Acción IA"].str.contains("TECHO", na=False))
            ]
        elif "Alarmas Desplome" in f_ai:
            filtered_all = filtered_all[
                (filtered_all["Acción IA"].str.contains("STOP-LOSS|DESPLOME", na=False)) |
                (filtered_all["_raw_prob_desplome"] >= 60.0)
            ]
        elif "Cracks de Élite" in f_ai:
            filtered_all = filtered_all[
                (filtered_all["_raw_price"] >= 8_000_000) &
                (filtered_all["_raw_pred_72h"] > 0)
            ]
        elif "Subida Orgánica" in f_ai:
            filtered_all = filtered_all[
                (filtered_all["Acción IA"].str.contains("ORGÁNICA|SUBIDA", na=False)) |
                (filtered_all["_raw_pred_24h"] > 0)
            ]
        elif "Previsión 72h Positiva" in f_ai:
            filtered_all = filtered_all[filtered_all["_raw_pred_72h"] > 0]
        elif "Mercado Lateral" in f_ai:
            filtered_all = filtered_all[filtered_all["Acción IA"].str.contains("LATERAL|ESPERAR", na=False)]

        # 7. Búsqueda
        if f_search.strip():
            q_p = f_search.strip().lower()
            filtered_all = filtered_all[
                filtered_all["PLAYER_NAME"].str.lower().str.contains(q_p, na=False) |
                filtered_all["TEAM_NAME"].str.lower().str.contains(q_p, na=False)
            ]

        # 8. Ordenación
        if f_sort == "💰 Mayor Valor de Mercado":
            filtered_all = filtered_all.sort_values(by="_raw_price", ascending=False)
        elif f_sort == "🏷️ Menor Precio de Salida (Económicos)":
            filtered_all["_sort_cost"] = filtered_all["_raw_market_sale"].replace(0, np.nan).fillna(filtered_all["_raw_price"])
            filtered_all = filtered_all.sort_values(by="_sort_cost", ascending=True)
        elif f_sort == "🔮 Mayor Subida Prevista a 72h (IA)":
            filtered_all = filtered_all.sort_values(by="_raw_pred_72h", ascending=False)
        elif f_sort == "🚀 Mayor ROI Previsto 72h (%)":
            filtered_all = filtered_all.sort_values(by="_raw_roi_72h", ascending=False)
        elif f_sort == "💎 Mayor Retorno Esperado EFR (€)":
            filtered_all = filtered_all.sort_values(by="_raw_efr", ascending=False)
        elif f_sort == "⚠️ Mayor Riesgo de Techo Inminente (%)":
            filtered_all = filtered_all.sort_values(by="_raw_prob_techo", ascending=False)
        elif f_sort == "🎯 Mayor Probabilidad de Suelo (%)":
            filtered_all = filtered_all.sort_values(by="_raw_prob_suelo", ascending=False)
        elif f_sort == "📈 Mayor Subida 24h":
            filtered_all = filtered_all.sort_values(by="_raw_inc", ascending=False)
        elif f_sort == "📉 Mayor Caída 24h (Stop-Loss)":
            filtered_all = filtered_all.sort_values(by="_raw_inc", ascending=True)
        elif f_sort == "⭐ Más Puntos Fantasy":
            filtered_all = filtered_all.sort_values(by="_raw_points", ascending=False)
        elif f_sort == "📊 Mayor Percentil Puntos":
            filtered_all = filtered_all.sort_values(by="_raw_percentile", ascending=False)
        elif f_sort == "⚽ Mayor Titularidad (%)":
            filtered_all = filtered_all.sort_values(by="_raw_starter", ascending=False)
        elif f_sort == "🎯 Mayor xP (Próx. Partido)":
            filtered_all = filtered_all.sort_values(by="_raw_xp", ascending=False)
        elif f_sort == "⏱️ Fin de Subasta Próximo":
            filtered_all = filtered_all.sort_values(by="MARKET_SALE_UNTIL", ascending=True, na_position="last")

        # ---------------------------------------------------------------------
        # TABLA PRINCIPAL DE MERCADO Y SUBASTAS
        # ---------------------------------------------------------------------
        all_display_cols = [
            "PLAYER_NAME", "TEAM_NAME", "Pos", "Estado", "Titularidad",
            "Puntos", "Percentil", "Valor (M€)", "Precio Salida (M€)", "Cláusula (M€)",
            "Vendedor", "Fin Subasta", "Viabilidad",
            "Acción IA", "Pred. 24h (K€)", "Previsión 72h (K€)", "ROI 72h (%)", "Retención"
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
                "Valor (M€)": st.column_config.NumberColumn("Valor Oficial", help="Precio oficial de mercado en Millones de Euros", format="%.2f M€", width="small"),
                "Precio Salida (M€)": st.column_config.NumberColumn("Precio Salida", help="Precio mínimo para pujar en la subasta de hoy en Millones de Euros", format="%.2f M€", width="small"),
                "Cláusula (M€)": st.column_config.NumberColumn("Cláusula", help="Cláusula de rescisión del jugador en Millones de Euros", format="%.2f M€", width="small"),
                "Vendedor": st.column_config.TextColumn("Vendedor / Dueño", help="Quién lo tiene en venta (Mercado CPU o Rival)", width="medium"),
                "Fin Subasta": st.column_config.TextColumn("Cierre Subasta", help="Fecha y hora límite para presentar tu puja", width="small"),
                "Viabilidad": st.column_config.TextColumn("Diagnóstico Fichaje", help="Indica si puedes ficharlo con tu saldo actual o usando crédito", width="medium"),
                "Acción IA": st.column_config.TextColumn("Señal IA", help="Recomendación econométrica táctica del modelo predictivo v3", width="medium"),
                "Pred. 24h (K€)": st.column_config.NumberColumn("Pred. 24h", help="Variación prevista de precio en las próximas 24h", format="%+d K€", width="small"),
                "Previsión 72h (K€)": st.column_config.NumberColumn("Prev. 72h", help="Variación acumulada prevista en 3 días (72h)", format="%+d K€", width="small"),
                "ROI 72h (%)": st.column_config.NumberColumn("ROI 72h", help="Rentabilidad porcentual prevista en 3 días", format="%+.1f%%", width="small"),
                "Retención": st.column_config.TextColumn("Retención", help="Horizonte temporal recomendado de tenencia", width="small")
            },
            width="stretch",
            height=540,
            hide_index=True
        )

        st.caption(f"Mostrando **{len(filtered_all)}** de **{total_p}** futbolistas según los filtros aplicados.")

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
                st.markdown(f"• **Vendedor / Propietario:** `{p_card['Vendedor']}`")
                st.markdown(f"• **Valor oficial de mercado:** `{format_price(p_card['_raw_price'])}`")
                sub_k = int(p_card['Subida 24h (K€)'])
                st.markdown(f"• **Subida 24h hoy:** `{p_card['Tendencia']} ({'+' if sub_k > 0 else ''}{sub_k} K€)`")
                
                # Financial Affordability callout
                p_sale = float(p_card["_raw_market_sale"])
                p_cl = float(p_card["_raw_clause"])
                p_is_mine = bool(p_card["_is_mine"])

                mkt_str = f"🛒 {format_price(p_sale)}" if p_sale > 0 else "—"
                cl_str = f"⚔️ {format_price(p_cl)}" if p_cl > 0 else "—"
                st.markdown(f"• **Puesto en Mercado:** `{mkt_str}`")
                st.markdown(f"• **Cláusula de rescisión:** `{cl_str}`")

                if p_is_mine:
                    st.success("⭐ **Ya está en tu equipo.** Forma parte de tu plantilla actual.")
                elif p_sale > 0 and p_sale <= balance:
                    diff_m = (balance - p_sale) / 1e6
                    st.success(f"🟢 **¡Pujable en Mercado con Saldo!** Tienes {format_price(balance)} y su precio de salida es {format_price(p_sale)}. Te sobrarían {diff_m:.2f} M€.")
                elif p_sale > 0 and p_sale <= max_bid_available:
                    need_m = (p_sale - balance) / 1e6
                    st.warning(f"🟡 **Pujable con Crédito.** Sale por {format_price(p_sale)}. Necesitarás usar {need_m:.2f} M€ de tu límite crediticio ({format_price(debt_allowance)} disponibles).")
                elif p_sale > 0 and p_sale > max_bid_available:
                    diff_mb = (p_sale - max_bid_available) / 1e6
                    st.error(f"🔴 **Inasumible hoy:** Sale por {format_price(p_sale)} y supera tu puja máxima permitida ({format_price(max_bid_available)}) por {diff_mb:.2f} M€.")
                elif p_cl > 0 and p_cl <= balance:
                    diff_cl = (balance - p_cl) / 1e6
                    st.success(f"⚡ **¡Clausulazo Viable con Saldo!** Su cláusula es de {format_price(p_cl)} y tu saldo es de {format_price(balance)}. Te sobrarían {diff_cl:.2f} M€.")
                elif p_cl > 0 and p_cl <= max_bid_available:
                    need_cl = (p_cl - balance) / 1e6
                    st.warning(f"🟡 **Clausulable con Crédito.** Cláusula de {format_price(p_cl)}. Requiere {need_cl:.2f} M€ de crédito.")
                elif p_cl > 0 and p_cl > max_bid_available:
                    st.info(f"⚔️ **Cláusula inasumible hoy.** Cláusula de {format_price(p_cl)} (supera tu puja máxima).")
                else:
                    st.caption("🔒 **No disponible hoy:** Ni está en venta en el mercado ni tiene cláusula activa.")

            with ic3:
                st.markdown(f"##### 🔮 Inteligencia & Econometría (IA)")
                st.markdown(f"• **Recomendación IA:** `{p_card['Acción IA']}`")
                p24_k = int(p_card.get('Pred. 24h (K€)', 0))
                p72_k = int(p_card.get('Previsión 72h (K€)', 0))
                p_roi = float(p_card.get('ROI 72h (%)', 0.0))
                p_efr = int(p_card.get('EFR (K€)', 0))
                st.markdown(f"• **Predicción 24h:** `{'+' if p24_k > 0 else ''}{p24_k} K€` (Precio est.: `{p_card.get('Precio Est. 24h (M€)', 0.0):.2f} M€`)")
                st.markdown(f"• **Previsión 72h:** `{'+' if p72_k > 0 else ''}{p72_k} K€` (Precio est.: `{p_card.get('Precio Est. 72h (M€)', 0.0):.2f} M€`)")
                st.markdown(f"• **ROI 72h:** `{'+' if p_roi > 0 else ''}{p_roi:.1f}%` · **EFR:** `{'+' if p_efr > 0 else ''}{p_efr} K€`")
                st.markdown(f"• **Régimen 72h:** `{p_card.get('Régimen 72h', '—')}`")
                st.markdown(f"• **Retención sugerida:** `{p_card.get('Retención', '—')}`")
                
                # Ceiling / Floor risk callouts if notable
                p_techo = float(p_card.get('_raw_prob_techo', 0.0))
                p_suelo = float(p_card.get('_raw_prob_suelo', 0.0))
                p_desplome = float(p_card.get('_raw_prob_desplome', 0.0))
                if p_techo >= 50.0:
                    st.caption(f"⚠️ **Riesgo de Techo Alto:** {p_techo:.0f}% de probabilidad de agotar subida.")
                elif p_suelo >= 50.0:
                    st.caption(f"🎯 **Suelo Cercano:** {p_suelo:.0f}% de probabilidad de frenar bajada y rebotar.")
                elif p_desplome >= 60.0:
                    st.caption(f"🛑 **Alerta de Desplome:** {p_desplome:.0f}% de probabilidad de pérdidas continuadas.")

                cpp = float(p_card.get("COST_PER_POINT", 0.0) or 0.0)
                if cpp > 0:
                    st.markdown(f"• **Coste por punto:** `{cpp:.2f} M€/pt`")
                # Recent fitness
                fit_str = str(p_card.get("PLAYER_FITNESS", "")).strip()
                if fit_str and fit_str.lower() != "nan" and fit_str != "[]":
                    st.markdown(f"• **Racha Fitness:** `{fit_str}`")


# =============================================================================
# TAB 5: CHAT CON EL AGENTE IA (PRÓXIMA FASE)
# =============================================================================

with tab_agent:
    st.markdown("### 💬 Copiloto Táctico & Financiero IA")
    st.caption("Asistente conversacional inteligente impulsado por LLM y razonamiento cuantitativo de Biwenger.")

    st.markdown("""
    <div style="background-color: var(--secondary-background-color); border: 1px solid rgba(128,128,128,0.2); border-radius: 10px; padding: 24px; margin-top: 10px;">
        <h4>🤖 Próximamente: Tu Director Deportivo IA en Tiempo Real</h4>
        <p style="font-size: 14px; line-height: 1.6; opacity: 0.9;">
            Una vez consolidado todo el sistema de datos, modelos cuantitativos y cuadro de mando financiero, 
            esta pestaña albergará el <b>Chat con el Agente Autónomo</b>.
        </p>
        <ul style="font-size: 13px; line-height: 1.8; opacity: 0.85;">
            <li>🧠 <b>Consultas de Once Ideal:</b> Pregúntale a quién alinear cada viernes según dificultad de rivales y titularidad.</li>
            <li>💰 <b>Estrategia de Pujas Óptima:</b> Consúltale cuánto sobrepujar por un jugador en subasta para ganarlo sin sobrepagar.</li>
            <li>⚔️ <b>Planes de Clausulazo & Stop-Loss:</b> Pídele simular qué ventas hacer para financiar una cláusula top sin romper tu balance.</li>
            <li>📊 <b>Auditoría Financiera de Rivales:</b> Pregúntale qué rivales están en números rojos o a punto de quebrar antes del fin de semana.</li>
        </ul>
        <div style="margin-top: 16px; padding: 10px 14px; background: rgba(59, 130, 246, 0.1); border-left: 4px solid #3b82f6; border-radius: 4px; font-size: 13px;">
            ℹ️ <b>Estado del Copiloto:</b> Los modelos econométricos, la base de datos de 580 jugadores y el motor de conciliación financiera ya están 100% operativos para alimentar el razonamiento del agente.
        </div>
    </div>
    """, unsafe_allow_html=True)


