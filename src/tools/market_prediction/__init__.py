"""
Biwenger Quantitative Market Prediction Module (v4)
===================================================
Production package for econometric and ML market forecasts:
- 24h, 48h, 72h price forecasting
- Early inflection warnings (Techo ATI & Suelo ASI)
- Expected Fantasy Return (EFR) & holding period calibration
- [v4] 7-day Weekly Seasonality Cycles, Kinematic Streaks, MACD & Shock Dynamics
"""

from .predictor import (
    BiwengerMarketPredictorV4,
    BiwengerMarketPredictorV3,
    BiwengerMarketPredictorV2,
    quantize_to_biwenger_grid
)
from .features import build_features, clean_and_sanitize_data
from .targets import build_targets, REGIME_NAMES, INFLECTION_NAMES

__all__ = [
    "BiwengerMarketPredictorV4",
    "BiwengerMarketPredictorV3",
    "BiwengerMarketPredictorV2",
    "quantize_to_biwenger_grid",
    "build_features",
    "clean_and_sanitize_data",
    "build_targets",
    "REGIME_NAMES",
    "INFLECTION_NAMES"
]
