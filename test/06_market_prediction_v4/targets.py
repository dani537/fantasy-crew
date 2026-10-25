"""
Target Formulations for Biwenger Market Predictive Modeling (v2)
=================================================================
Computes multi-horizon continuous returns, hurdle binary targets,
residual acceleration deltas, regime classifications, and turning points.
"""

import numpy as np
import pandas as pd
from typing import Dict, Any

def build_targets(df: pd.DataFrame) -> pd.DataFrame:
    """Computes multi-horizon targets (24h, 48h, 72h), hurdle states, and regimes."""
    df = df.copy()
    df = df.sort_values(by=["player_id", "fecha"]).reset_index(drop=True)

    # 1. Continuous Horizons (Future Prices)
    df["target_price_1d"] = df.groupby("player_id")["precio"].shift(-1)
    df["target_price_2d"] = df.groupby("player_id")["precio"].shift(-2)
    df["target_price_3d"] = df.groupby("player_id")["precio"].shift(-3)

    # Future subida in nominal €
    df["target_subida_1d"] = df.groupby("player_id")["subida_24h"].shift(-1)
    df["target_delta_2d"] = df["target_price_2d"] - df["precio"]
    df["target_delta_3d"] = df["target_price_3d"] - df["precio"]

    # 2. Hurdle & Residual Targets (Stage 1 & Stage 2)
    # Stage 1: Zero-Hurdle Binary Target (1 if frozen at 0 €, 0 if moving)
    df["target_is_zero_1d"] = np.where(
        df["target_subida_1d"].isna(), np.nan,
        np.where(df["target_subida_1d"] == 0, 1.0, 0.0)
    )

    # Stage 2: Residual Acceleration Delta (Delta in Subida €)
    df["target_delta_subida_1d"] = df["target_subida_1d"] - df["subida_24h"]

    # Relative Percentage Returns (%)
    denom_price = np.maximum(df["precio"], 100000.0)
    df["target_pct_1d"] = (df["target_price_1d"] - df["precio"]) / denom_price * 100.0
    df["target_pct_2d"] = (df["target_price_2d"] - df["precio"]) / denom_price * 100.0
    df["target_pct_3d"] = (df["target_price_3d"] - df["precio"]) / denom_price * 100.0

    df["target_accel_pct_1d"] = df["target_pct_1d"] - df["pct_subida_24h"]

    # 3. Discrete Directional Targets (-1, 0, 1)
    df["target_dir_1d"] = np.where(
        df["target_subida_1d"].isna(), np.nan,
        np.where(
            df["target_subida_1d"] > 0, 1,
            np.where(df["target_subida_1d"] < 0, -1, 0)
        )
    )

    # 4. Categorical 3-Day Regime (Market Phase)
    # 0: Desplome (<-2.5%)
    # 1: Lateral / Débil (-2.5% a +0.5%)
    # 2: Alcista Moderado (+0.5% a +5.0%)
    # 3: Explosión Alcista (>+5.0%)
    def assign_regime(val):
        if pd.isna(val): return np.nan
        if val < -2.5: return 0
        elif val <= 0.5: return 1
        elif val <= 5.0: return 2
        else: return 3

    df["target_regime_3d"] = df["target_pct_3d"].apply(assign_regime)

    # 5. Turning Point / Inflexion Targets
    # Giro Techo: Rising today -> Falls or stops tomorrow
    df["target_techo_1d"] = np.where(
        (df["subida_24h"] > 0) & df["target_subida_1d"].notna(),
        (df["target_subida_1d"] <= 0).astype(float),
        np.nan
    )

    # Giro Suelo: Flat/dropping today -> Rises tomorrow
    df["target_suelo_1d"] = np.where(
        (df["subida_24h"] <= 0) & df["target_subida_1d"].notna(),
        (df["target_subida_1d"] > 0).astype(float),
        np.nan
    )

    return df

REGIME_NAMES = {
    0: "Desplome (<-2.5%)",
    1: "Lateral (-2.5..0.5%)",
    2: "Alcista (0.5..5%)",
    3: "Explosión (>5%)"
}

INFLECTION_NAMES = {
    0: "Cont. Bajista",
    1: "Cont. Alcista",
    2: "Giro Techo",
    3: "Giro Suelo"
}
