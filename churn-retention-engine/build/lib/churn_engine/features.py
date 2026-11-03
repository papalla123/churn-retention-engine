"""Ingeniería de variables: convierte los datos crudos en señales comparables."""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from .timeutil import ensure_utc


def build_features(clean: pd.DataFrame, as_of: datetime) -> pd.DataFrame:
    """Agrega las columnas derivadas que consume el scorer.

    - days_since_last_login: días enteros desde el último login (mínimo 0).
    - usage_change_pct: variación porcentual del uso frente a los 30 días previos.
      Queda en NaN cuando no hay línea base (usage_prev_30d <= 0), para no inventar una caída.
    - days_to_renewal: contexto para el CSM, no entra al score. Puede ser negativo o NaN.
    """
    as_of_ts = pd.Timestamp(ensure_utc(as_of))
    out = clean.copy()

    out["days_since_last_login"] = (as_of_ts - out["last_login_at"]).dt.days.clip(lower=0).astype("int64")

    prev = out["usage_prev_30d"]
    has_baseline = prev > 0
    out["usage_baseline_missing"] = ~has_baseline
    out["usage_change_pct"] = (out["usage_last_30d"] - prev) / prev.where(has_baseline) * 100.0

    out["days_to_renewal"] = (out["contract_end_date"].dt.normalize() - as_of_ts.normalize()).dt.days
    return out
