"""
NaN-safe numeric coercion helpers.

`float(pd.to_numeric("", errors="coerce") or default)` silently returns NaN because
NaN is truthy in Python. `safe_num` always returns a finite float.
"""

import math
from typing import Any

import pandas as pd


def safe_num(value: Any, default: float = 0.0) -> float:
    """Coerces `value` (str with ',' decimals, '%', None, NaN, numbers) to a finite float."""
    if value is None:
        return float(default)
    if isinstance(value, str):
        txt = value.strip().replace("%", "").replace("€", "").strip()
        if not txt or txt.lower() in ("nan", "none", "null"):
            return float(default)
        # Spanish decimal comma ("5,3") when no dot thousands separator ambiguity
        if "," in txt and "." not in txt:
            txt = txt.replace(",", ".")
        value = txt
    try:
        out = float(pd.to_numeric(value, errors="coerce"))
    except (TypeError, ValueError):
        return float(default)
    if math.isnan(out) or math.isinf(out):
        return float(default)
    return out
