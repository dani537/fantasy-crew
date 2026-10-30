"""
Feature Engineering Store for Biwenger Market Predictive Engine (v3)
=====================================================================
Senior Quantitative Finance & Econometrics implementation:
- Full mathematical sanitation of European decimal formatting anomalies.
- Exact scale recalculation of relative percentage returns from raw euros.
- Microstructure order flow saturation and non-linear ownership headroom.
- Kinematic acceleration, jerk, and momentum divergence indicators.
- [NEW v3] Weekly Calendar Seasonality (DoW cyclics, Friday Debt effect, Monday Priming).
- [NEW v3] Market Breadth Dynamics (Mean market pressure, % gainers/losers, relative pressure).
- [NEW v3] Hypergainer Deceleration & Cliff Saturation Signals.
"""

import numpy as np
import pandas as pd
from typing import List, Tuple, Dict, Any

NUMERIC_BASE_COLS = [
    "precio", "subida_24h", "min_precio_1y", "max_precio_1y",
    "ganancia_temporada", "diff_7d", "diff_14d", "diff_30d",
    "pct_compras_24h", "pct_ventas_24h", "pct_uso_ligas", "presion_neta",
    "puntos_totales", "partidos_jugados", "media_puntos", "media_picas", "media_sofascore",
    "goles", "asistencias", "minutos"
]

def clean_and_sanitize_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Sanitizes raw dataframe:
    1. Replaces comma decimals with dots.
    2. Recomputes true relative percentage returns from nominal euros.
    3. Normalizes sporting metric scales (puntos, sofascore, picas).
    4. Imputes missing sentiment values forward per player.
    """
    df = df.copy()

    # Convert numeric columns
    for c in NUMERIC_BASE_COLS:
        if c in df.columns:
            if df[c].dtype == object:
                df[c] = df[c].astype(str).str.replace(",", ".").replace("", np.nan)
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    # Clean date and sort
    df["fecha"] = pd.to_datetime(df["fecha"])
    df = df.sort_values(by=["player_id", "fecha"]).reset_index(drop=True)

    # 1. True Mathematical Percentages (Eliminating decimal stripping artifact)
    prev_p = np.maximum(df["precio"] - df["subida_24h"], 100000.0)
    df["pct_subida_24h"] = (df["subida_24h"] / prev_p) * 100.0

    if "diff_7d" in df.columns:
        prev_7d = np.maximum(df["precio"] - df["diff_7d"], 100000.0)
        df["pct_7d"] = (df["diff_7d"] / prev_7d) * 100.0

    if "diff_14d" in df.columns:
        prev_14d = np.maximum(df["precio"] - df["diff_14d"], 100000.0)
        df["pct_14d"] = (df["diff_14d"] / prev_14d) * 100.0

    if "diff_30d" in df.columns:
        prev_30d = np.maximum(df["precio"] - df["diff_30d"], 100000.0)
        df["pct_30d"] = (df["diff_30d"] / prev_30d) * 100.0

    # 2. Sports Averages Normalization
    mask_pj = df["partidos_jugados"] > 0
    df["media_puntos"] = np.where(mask_pj, df["puntos_totales"] / df["partidos_jugados"], 0.0)

    # Normalize SofaScore (if scaled > 10, scale to 0-10)
    if "media_sofascore" in df.columns:
        df["media_sofascore"] = np.where(
            df["media_sofascore"] > 100, df["media_sofascore"] / 100.0,
            np.where(df["media_sofascore"] > 10, df["media_sofascore"] / 10.0, df["media_sofascore"])
        )

    # Normalize Picas (if scaled > 5, scale to 0-4)
    if "media_picas" in df.columns:
        df["media_picas"] = np.where(
            df["media_picas"] > 50, df["media_picas"] / 100.0,
            np.where(df["media_picas"] > 4, df["media_picas"] / 10.0, df["media_picas"])
        )

    # Impute forward per player for sentiment if missing
    for c in ["pct_compras_24h", "pct_ventas_24h", "pct_uso_ligas"]:
        if c in df.columns:
            df[c] = df.groupby("player_id")[c].ffill().fillna(0)

    df["presion_neta"] = df["pct_compras_24h"] - df["pct_ventas_24h"]
    return df

def build_features(df: pd.DataFrame, max_lags: int = 4) -> Tuple[pd.DataFrame, Dict[str, List[str]]]:
    """
    Constructs multi-tier feature sets for v3 engine:
    1. Base Price & Kinematics Lags
    2. Residual Acceleration & Momentum Delta
    3. Sentiment Order Flow & Saturation Lags
    4. Market Microstructure & Friction
    5. Divergence & Early Inflexion Signals
    6. [v3] Calendar Seasonality (Day of Week Cyclics & Friday Effect)
    7. [v3] Market Breadth Dynamics
    8. [v3] Hypergainer Deceleration & Cliff Saturation
    """
    df = clean_and_sanitize_data(df)

    # --- 1. Base Price & Kinematics Lags ---
    price_lags = []
    for lag in range(1, max_lags + 1):
        col_p = f"precio_lag{lag}"
        col_s = f"subida_lag{lag}"
        col_pct = f"pct_subida_lag{lag}"
        df[col_p] = df.groupby("player_id")["precio"].shift(lag)
        df[col_s] = df.groupby("player_id")["subida_24h"].shift(lag)
        df[col_pct] = df.groupby("player_id")["pct_subida_24h"].shift(lag)
        price_lags.extend([col_p, col_s, col_pct])

    # Derivatives (Velocity, Acceleration, Jerk)
    df["delta_subida_1d"] = df["subida_24h"] - df["subida_lag1"]
    df["accel_subida_1d"] = df["delta_subida_1d"]
    df["accel_subida_2d"] = df["subida_lag1"] - df["subida_lag2"]
    df["jerk_subida"] = df["accel_subida_1d"] - df["accel_subida_2d"]

    df["accel_pct_1d"] = df["pct_subida_24h"] - df["pct_subida_lag1"]
    df["accel_pct_2d"] = df["pct_subida_lag1"] - df["pct_subida_lag2"]

    # Moving averages
    df["sma3_subida"] = (df["subida_24h"] + df["subida_lag1"].fillna(df["subida_24h"]) + df["subida_lag2"].fillna(df["subida_24h"])) / 3.0
    df["sma3_pct"] = (df["pct_subida_24h"] + df["pct_subida_lag1"].fillna(df["pct_subida_24h"]) + df["pct_subida_lag2"].fillna(df["pct_subida_24h"])) / 3.0

    # Range metrics
    range_1y = np.maximum(df["max_precio_1y"] - df["min_precio_1y"], 100000.0)
    df["ratio_range_1y"] = np.clip((df["precio"] - df["min_precio_1y"]) / range_1y, 0.0, 1.0)
    df["dist_max_1y_pct"] = (df["precio"] - df["max_precio_1y"]) / np.maximum(df["max_precio_1y"], 100000.0) * 100.0

    # --- 2. Sentiment Order Flow Lags ---
    sentiment_lags = []
    for lag in range(1, max_lags + 1):
        col_c = f"compras_lag{lag}"
        col_v = f"ventas_lag{lag}"
        col_n = f"presion_lag{lag}"
        df[col_c] = df.groupby("player_id")["pct_compras_24h"].shift(lag)
        df[col_v] = df.groupby("player_id")["pct_ventas_24h"].shift(lag)
        df[col_n] = df.groupby("player_id")["presion_neta"].shift(lag)
        sentiment_lags.extend([col_c, col_v, col_n])

    # Sentiment velocity & acceleration
    df["delta_presion_1d"] = df["presion_neta"] - df["presion_lag1"]
    df["delta_presion_2d"] = df["presion_lag1"] - df["presion_lag2"]
    df["accel_presion"] = df["delta_presion_1d"] - df["delta_presion_2d"]
    df["delta_compras_1d"] = df["pct_compras_24h"] - df["compras_lag1"]
    df["delta_ventas_1d"] = df["pct_ventas_24h"] - df["ventas_lag1"]

    # Ratio of sales acceleration
    df["sales_acceleration_ratio"] = df["delta_ventas_1d"] / (df["ventas_lag1"].fillna(0) + 1.0)

    df["sma3_presion"] = (df["presion_neta"] + df["presion_lag1"].fillna(df["presion_neta"]) + df["presion_lag2"].fillna(df["presion_neta"])) / 3.0
    df["demand_ratio"] = (df["pct_compras_24h"] + 1.0) / (df["pct_ventas_24h"] + 1.0)

    # --- 3. Market Microstructure, Saturation & Friction ---
    df["log_precio"] = np.log1p(df["precio"])
    df["headroom_uso"] = np.clip(1.0 - (df["pct_uso_ligas"] / 100.0), 0.02, 1.0)

    # Effective order flow
    df["effective_order_flow"] = df["presion_neta"] * df["headroom_uso"]

    # Order flow saturation
    df["order_flow_saturation"] = df["presion_neta"] * (df["headroom_uso"] ** 2)

    # Capital friction by price tier
    df["flow_friction"] = df["effective_order_flow"] / np.maximum(df["log_precio"] - 10.0, 1.0)

    # Segment Tiers
    df["tier_micro"] = np.where(df["precio"] < 1_500_000, 1.0, 0.0)
    df["tier_mid"] = np.where((df["precio"] >= 1_500_000) & (df["precio"] < 5_000_000), 1.0, 0.0)
    df["tier_consolidated"] = np.where((df["precio"] >= 5_000_000) & (df["precio"] < 10_000_000), 1.0, 0.0)
    df["tier_crack"] = np.where(df["precio"] >= 10_000_000, 1.0, 0.0)

    # --- 4. Divergences & Early Inflexion Signals ---
    df["bullish_div"] = np.where(
        (df["subida_24h"] <= 0) & (
            ((df["delta_presion_1d"] >= 2.5) & (df["pct_compras_24h"] >= 6.0)) |
            ((df["accel_subida_1d"] >= 10000) & (df["presion_neta"] >= 3.0))
        ),
        1.0, 0.0
    )

    df["bearish_div"] = np.where(
        (df["subida_24h"] > 0) & (
            (df["delta_presion_1d"] <= -2.5) |
            (df["pct_ventas_24h"] >= 4.0) |
            ((df["accel_subida_1d"] < 0) & (df["pct_uso_ligas"] >= 50.0))
        ),
        1.0, 0.0
    )

    df["crash_signal"] = np.where(
        (df["subida_24h"] < 0) & (
            (df["presion_neta"] < -8.0) |
            ((df["pct_ventas_24h"] >= 10.0) & (df["pct_compras_24h"] <= 3.0))
        ),
        1.0, 0.0
    )

    # IEN: Índice de Especulación Neta
    df["ien"] = np.round(
        (df["effective_order_flow"] * df["demand_ratio"]) / np.maximum(df["log_precio"] - 11.0, 1.0),
        2
    )

    # --- 5. Sporting Performance & Availability Context ---
    df["titular_prob"] = np.where(df.get("comuniate_titular", "").astype(str).str.lower().isin(["si", "s", "1", "true"]), 1.0, 0.0)
    df["duda_prob"] = np.where(df.get("comuniate_duda", "").astype(str).str.lower().isin(["si", "s", "1", "true"]), 1.0, 0.0)
    df["estado_lesionado"] = np.where(df.get("estado", "").astype(str).str.lower().str.contains("lesion|baja|sancion"), 1.0, 0.0)

    # --- 6. [NEW v3 & v4] Calendar Seasonality & Interactions ---
    dow = df["fecha"].dt.dayofweek
    df["sin_dow"] = np.sin(2 * np.pi * dow / 7.0)
    df["cos_dow"] = np.cos(2 * np.pi * dow / 7.0)
    df["is_weekend"] = dow.isin([4, 5, 6]).astype(float)
    df["is_friday"] = (dow == 4).astype(float)
    df["is_monday"] = (dow == 0).astype(float)

    # --- 7. [NEW v3] Market Breadth Dynamics ---
    daily_stats = df.groupby("fecha").agg(
        mkt_mean_presion=("presion_neta", "mean"),
        mkt_pct_gainers=("subida_24h", lambda x: (x > 0).mean()),
        mkt_pct_losers=("subida_24h", lambda x: (x < 0).mean())
    ).reset_index()

    df = df.merge(daily_stats, on="fecha", how="left")
    df["rel_presion_to_market"] = df["presion_neta"] - df["mkt_mean_presion"]

    # --- 8. [NEW v3] Hypergainer Deceleration & Cliff Dynamics ---
    df["is_hypergainer"] = (df["subida_24h"] >= 150000).astype(float)
    df["hypergainer_drag"] = np.where(df["subida_24h"] >= 150000, np.minimum(0, df["delta_presion_1d"]), 0.0)
    df["hypergainer_accel_drop"] = np.where(df["subida_24h"] >= 150000, np.minimum(0, df["accel_subida_1d"]), 0.0)
    df["peak_exhaustion_ratio"] = (df["subida_24h"] / np.maximum(df["precio"], 100000.0)) * (df["pct_ventas_24h"] + 1.0)

    # --- 9. [NEW v4] Weekly 7-Day Cycle (Fase 1 del Roadmap) ---
    df["subida_lag7"] = df.groupby("player_id")["subida_24h"].shift(7).fillna(0)
    df["presion_lag7"] = df.groupby("player_id")["presion_neta"].shift(7).fillna(0)
    df["has_lag7"] = df.groupby("player_id")["subida_24h"].shift(7).notna().astype(float)
    df["weekly_subida_momentum"] = np.where(df["has_lag7"] == 1, df["subida_24h"] - df["subida_lag7"], 0.0)
    df["weekly_presion_momentum"] = np.where(df["has_lag7"] == 1, df["presion_neta"] - df["presion_lag7"], 0.0)

    # --- 10. [NEW v4] Kinematic Streak & Fatigue Dynamics ---
    is_up = (df["subida_24h"] > 0).astype(int)
    is_down = (df["subida_24h"] < 0).astype(int)
    is_flat = (df["subida_24h"] == 0).astype(int)

    df["streak_up"] = is_up.groupby([df["player_id"], (~is_up.astype(bool)).cumsum()]).cumsum().astype(float)
    df["streak_down"] = is_down.groupby([df["player_id"], (~is_down.astype(bool)).cumsum()]).cumsum().astype(float)
    df["streak_flat"] = is_flat.groupby([df["player_id"], (~is_flat.astype(bool)).cumsum()]).cumsum().astype(float)

    # --- 11. [NEW v4] MACD Fantasy & Multi-Speed Moving Averages ---
    ema_fast = df.groupby("player_id")["subida_24h"].transform(lambda s: s.ewm(span=2, adjust=False).mean())
    ema_slow = df.groupby("player_id")["subida_24h"].transform(lambda s: s.ewm(span=5, adjust=False).mean())
    df["macd_subida"] = ema_fast - ema_slow
    macd_signal = df.groupby("player_id")["macd_subida"].transform(lambda s: s.ewm(span=2, adjust=False).mean())
    df["macd_subida_hist"] = df["macd_subida"] - macd_signal

    # Rolling short-term volatility
    df["roll_std3_subida"] = df.groupby("player_id")["subida_24h"].transform(lambda s: s.rolling(3, min_periods=1).std()).fillna(0)
    df["roll_std3_presion"] = df.groupby("player_id")["presion_neta"].transform(lambda s: s.rolling(3, min_periods=1).std()).fillna(0)

    # --- 12. [NEW v4] Calendar Shock Interactions ---
    df["friday_debt_shock"] = df["is_friday"] * np.maximum(0, df["pct_ventas_24h"] - 3.0)
    df["monday_hype_factor"] = df["is_monday"] * np.maximum(0, df["delta_presion_1d"])
    df["weekend_fatigue"] = df["is_weekend"] * np.where(df["streak_up"] >= 4, 1.0, 0.0)
    df["flow_elasticity"] = df["subida_24h"] / (np.abs(df["presion_neta"]) + 1.0)

    # Feature sets for ablation and model pipelines
    feature_groups = {
        "set1_price_only": [
            "precio", "log_precio", "subida_24h", "pct_subida_24h",
            "subida_lag1", "subida_lag2", "pct_subida_lag1", "pct_subida_lag2",
            "delta_subida_1d", "accel_subida_1d", "accel_subida_2d",
            "sma3_subida", "sma3_pct", "ratio_range_1y", "dist_max_1y_pct"
        ],
        "set2_price_sentiment": [
            "precio", "log_precio", "subida_24h", "pct_subida_24h",
            "subida_lag1", "subida_lag2", "pct_subida_lag1", "pct_subida_lag2",
            "delta_subida_1d", "accel_subida_1d", "sma3_subida", "sma3_pct",
            "pct_compras_24h", "pct_ventas_24h", "presion_neta",
            "compras_lag1", "ventas_lag1", "presion_lag1",
            "delta_presion_1d", "delta_compras_1d", "delta_ventas_1d",
            "sma3_presion", "demand_ratio"
        ],
        "set3_microstructure": [
            "precio", "log_precio", "subida_24h", "pct_subida_24h",
            "subida_lag1", "subida_lag2", "pct_subida_lag1",
            "delta_subida_1d", "accel_subida_1d", "sma3_subida",
            "pct_compras_24h", "pct_ventas_24h", "pct_uso_ligas", "presion_neta",
            "presion_lag1", "delta_presion_1d", "delta_compras_1d", "delta_ventas_1d",
            "demand_ratio", "headroom_uso", "effective_order_flow", "order_flow_saturation",
            "flow_friction", "sales_acceleration_ratio",
            "tier_micro", "tier_mid", "tier_consolidated", "tier_crack", "ien"
        ],
        "set4_full_master": [
            "precio", "log_precio", "subida_24h", "pct_subida_24h",
            "subida_lag1", "subida_lag2", "pct_subida_lag1", "pct_subida_lag2",
            "delta_subida_1d", "accel_subida_1d", "accel_subida_2d", "jerk_subida",
            "sma3_subida", "sma3_pct",
            "pct_compras_24h", "pct_ventas_24h", "pct_uso_ligas", "presion_neta",
            "compras_lag1", "ventas_lag1", "presion_lag1",
            "delta_presion_1d", "delta_compras_1d", "delta_ventas_1d", "accel_presion",
            "sales_acceleration_ratio", "sma3_presion", "demand_ratio",
            "headroom_uso", "effective_order_flow", "order_flow_saturation", "flow_friction",
            "tier_micro", "tier_mid", "tier_consolidated", "tier_crack",
            "bullish_div", "bearish_div", "crash_signal", "ien",
            "ratio_range_1y", "media_puntos", "media_sofascore",
            "titular_prob", "duda_prob", "estado_lesionado"
        ],
        "set5_master_v3": [
            "precio", "log_precio", "subida_24h", "pct_subida_24h",
            "subida_lag1", "subida_lag2", "pct_subida_lag1", "pct_subida_lag2",
            "delta_subida_1d", "accel_subida_1d", "accel_subida_2d", "jerk_subida",
            "sma3_subida", "sma3_pct",
            "pct_compras_24h", "pct_ventas_24h", "pct_uso_ligas", "presion_neta",
            "compras_lag1", "ventas_lag1", "presion_lag1",
            "delta_presion_1d", "delta_compras_1d", "delta_ventas_1d", "accel_presion",
            "sales_acceleration_ratio", "sma3_presion", "demand_ratio",
            "headroom_uso", "effective_order_flow", "order_flow_saturation", "flow_friction",
            "tier_micro", "tier_mid", "tier_consolidated", "tier_crack",
            "bullish_div", "bearish_div", "crash_signal", "ien",
            "ratio_range_1y", "media_puntos", "media_sofascore",
            "titular_prob", "duda_prob", "estado_lesionado",
            "sin_dow", "cos_dow", "is_weekend", "is_friday", "is_monday",
            "mkt_mean_presion", "mkt_pct_gainers", "mkt_pct_losers", "rel_presion_to_market",
            "is_hypergainer", "hypergainer_drag", "hypergainer_accel_drop", "peak_exhaustion_ratio"
        ],
        "set6_master_v4": [
            # 1. Base Price & Kinematics Lags
            "precio", "log_precio", "subida_24h", "pct_subida_24h",
            "subida_lag1", "subida_lag2", "pct_subida_lag1", "pct_subida_lag2",
            "delta_subida_1d", "accel_subida_1d", "accel_subida_2d", "jerk_subida",
            "sma3_subida", "sma3_pct",
            # 2. MACD & Multi-Speed Momentum
            "macd_subida", "macd_subida_hist", "roll_std3_subida", "roll_std3_presion",
            # 3. Sentiment Order Flow & Kinematics
            "pct_compras_24h", "pct_ventas_24h", "pct_uso_ligas", "presion_neta",
            "presion_lag1", "delta_presion_1d", "accel_presion",
            "delta_compras_1d", "delta_ventas_1d", "sales_acceleration_ratio", "demand_ratio",
            # 4. Microstructure & Headroom
            "headroom_uso", "effective_order_flow", "order_flow_saturation", "flow_friction", "flow_elasticity",
            "tier_micro", "tier_mid", "tier_consolidated", "tier_crack",
            "bullish_div", "bearish_div", "crash_signal", "ien",
            # 5. Weekly 7-Day Cycle & Calendar Shock (Roadmap Fase 1)
            "subida_lag7", "presion_lag7", "weekly_subida_momentum", "weekly_presion_momentum",
            "sin_dow", "cos_dow", "is_weekend", "is_friday", "is_monday",
            "friday_debt_shock", "monday_hype_factor", "weekend_fatigue",
            # 6. Streaks & Inertia
            "streak_up", "streak_down", "streak_flat",
            # 7. Cliff Dynamics & Deceleration
            "is_hypergainer", "hypergainer_drag", "hypergainer_accel_drop", "peak_exhaustion_ratio"
        ]
    }

    return df, feature_groups
