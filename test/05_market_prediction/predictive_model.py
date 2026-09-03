"""
Biwenger Market Prediction Model (Test 05 - Market Prediction)
==============================================================
Statistical & Machine Learning model to forecast Biwenger player price variations,
momentum direction, and optimal trading decisions 24-48h ahead based on:
- Community Market Sentiment (% Compras, % Ventas, % Uso en Ligas, Presión Neta)
- Price Curves & Historical Momentum (24h, 7d, 14d, 30d, 1y Min/Max)
- Sporting Performance (Puntos, Medias, Sofascore, Picas, Goles, Asistencias)
- Tactical Context (Comuniate Titularidad, Estatus de Lesión)
"""

import os
import sys
import numpy as np
import pandas as pd
from typing import Dict, Tuple, List, Optional

class BiwengerMarketPredictor:
    """Predictive engine for player valuation and market movement forecasting."""

    def __init__(self, data_path: Optional[str] = None):
        self.data_path = data_path or "data/history/market_sentiment_timeseries.csv"
        self.df_raw: Optional[pd.DataFrame] = None
        self.df_features: Optional[pd.DataFrame] = None
        self.predictions: Optional[pd.DataFrame] = None

    def load_data(self) -> pd.DataFrame:
        """Loads and cleans latest available market dataset."""
        if not os.path.exists(self.data_path):
            # Fallback to daily snapshots
            snapshot_dir = "data/history/snapshots"
            if os.path.exists(snapshot_dir):
                snaps = sorted(os.listdir(snapshot_dir))
                if snaps:
                    self.data_path = os.path.join(snapshot_dir, snaps[-1])
        
        if not os.path.exists(self.data_path):
            raise FileNotFoundError(f"No se encontró dataset de mercado en {self.data_path}")

        df = pd.read_csv(self.data_path)
        # Ensure latest date only for today's forecast
        if "fecha" in df.columns:
            latest_date = df["fecha"].max()
            df = df[df["fecha"] == latest_date].copy()
            print(f"📅 Dataset cargado para la fecha: {latest_date} ({len(df)} jugadores)")
        
        self.df_raw = df
        return df

    def engineer_features(self) -> pd.DataFrame:
        """Computes advanced financial and sentiment predictive indicators."""
        if self.df_raw is None:
            self.load_data()

        df = self.df_raw.copy()

        # Numeric conversions
        num_cols = [
            "precio", "subida_24h", "pct_subida_24h", "diff_7d", "pct_7d", "diff_14d", "pct_14d",
            "pct_compras_24h", "pct_ventas_24h", "pct_uso_ligas", "presion_neta",
            "puntos_totales", "partidos_jugados", "media_puntos", "media_picas", "media_sofascore"
        ]
        for col in num_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

        # 1. Presión Neta (Balanza de Demanda vs Oferta)
        if "presion_neta" not in df.columns or df["presion_neta"].isna().all():
            df["presion_neta"] = df["pct_compras_24h"] - df["pct_ventas_24h"]

        # 2. Ratio de Liquidez y Demanda
        df["ratio_demanda"] = (df["pct_compras_24h"] + 0.01) / (df["pct_ventas_24h"] + 0.01)

        # 3. Factor de Inercia por Penetración (a mayor % uso, menor elasticidad porcentual)
        df["factor_inercia"] = np.clip(1.0 - (df["pct_uso_ligas"] / 100.0), 0.15, 1.0)

        # 4. Momentum Ponderado (24h + 7d)
        df["momentum_score"] = (df["pct_subida_24h"] * 0.6) + (df["pct_7d"] * 0.4)

        # 5. Rendimiento Deportivo Combinado
        df["deporte_score"] = (df["media_puntos"] * 0.5) + (df["media_sofascore"] * 0.5)

        self.df_features = df
        return df

    def predict(self) -> pd.DataFrame:
        """Executes the predictive algorithm and returns enriched forecasts."""
        if self.df_features is None:
            self.engineer_features()

        df = self.df_features.copy()

        # Modelo matemático calibrado empíricamente sobre el algoritmo de Biwenger:
        # Delta_Pct_Esperado = k * Presión_Neta * Factor_Inercia + Momentum_Decay
        k_elasticity = 0.14
        
        # Predicción de subida porcentual en 24h
        df["pred_pct_24h"] = np.round(
            (k_elasticity * df["presion_neta"] * df["factor_inercia"]) + (0.15 * df["pct_subida_24h"]),
            2
        )

        # Predicción en Euros absolutos
        df["pred_delta_eur_24h"] = np.round(df["precio"] * (df["pred_pct_24h"] / 100.0), -3)

        # Probabilidad de Subida (0% - 100%) basada en función logística sobre Presión Neta
        # Sigmoide centrada en 0 con escala de sensibilidad
        z = (df["presion_neta"] * 0.25) + (df["pct_subida_24h"] * 0.4)
        df["prob_subida_pct"] = np.round(100.0 / (1.0 + np.exp(-z)), 1)

        # Categorización de Fase de Mercado
        conditions_phase = [
            (df["presion_neta"] >= 15.0),
            (df["presion_neta"] >= 4.0) & (df["presion_neta"] < 15.0),
            (df["presion_neta"] >= -4.0) & (df["presion_neta"] < 4.0),
            (df["presion_neta"] < -4.0)
        ]
        choices_phase = [
            "🟢 Acumulación Explosiva",
            "🟡 Subida Estable",
            "⚪ Zona Neutra / Techo",
            "🔴 Liquidación / Desplome"
        ]
        df["fase_mercado"] = np.select(conditions_phase, choices_phase, default="⚪ Zona Neutra")

        # Recomendación Operativa
        conditions_rec = [
            (df["presion_neta"] >= 12.0) & (df["precio"] <= 5_000_000),
            (df["presion_neta"] >= 5.0),
            (df["presion_neta"] < -5.0),
            (df["subida_24h"] < 0) & (df["presion_neta"] < 0)
        ]
        choices_rec = [
            "🚀 COMPRA FUERTE (Especulación)",
            "📈 MANTENER (En Subida)",
            "⚠️ VENTA PREVENTIVA (Techo)",
            "🛑 STOP-LOSS INMEDIATO"
        ]
        df["accion_recomendada"] = np.select(conditions_rec, choices_rec, default="⏸️ ESPERAR / NEUTRAL")

        self.predictions = df
        return df

    def get_top_speculative_gems(self, n: int = 10) -> pd.DataFrame:
        """Identifies top undervalued players with highest buying momentum."""
        if self.predictions is None:
            self.predict()
        
        df = self.predictions[self.predictions["precio"] > 200_000].copy()
        # Filter for positive net pressure and price < 8M
        gems = df[(df["presion_neta"] > 0) & (df["precio"] <= 8_000_000)].copy()
        gems = gems.sort_values(by=["presion_neta", "pred_pct_24h"], ascending=[False, False])
        return gems.head(n)[[
            "nombre", "equipo", "posicion", "precio", "subida_24h", "presion_neta",
            "pct_compras_24h", "pct_ventas_24h", "pred_pct_24h", "pred_delta_eur_24h", "fase_mercado"
        ]]

    def get_top_crash_warnings(self, n: int = 10) -> pd.DataFrame:
        """Identifies players facing massive dumps with imminent price crashes."""
        if self.predictions is None:
            self.predict()
        
        df = self.predictions[self.predictions["precio"] > 500_000].copy()
        warnings = df.sort_values(by="presion_neta", ascending=True)
        return warnings.head(n)[[
            "nombre", "equipo", "posicion", "precio", "subida_24h", "presion_neta",
            "pct_compras_24h", "pct_ventas_24h", "pred_pct_24h", "pred_delta_eur_24h", "accion_recomendada"
        ]]
