"""Score de churn (0-100) y clasificación de nivel de riesgo.

El modelo es un score heurístico ponderado y explicable, no un modelo entrenado.
La interfaz `Scorer` permite sustituirlo por un clasificador (por ejemplo, regresión
logística o gradient boosting con `predict_proba`) sin tocar el resto del pipeline.
"""
from __future__ import annotations

from typing import Protocol

import numpy as np
import pandas as pd

from .config import Ramp, ScoringConfig

LEVELS = ("Bajo", "Medio", "Alto")
LEVEL_RANK = {level: rank for rank, level in enumerate(LEVELS)}

COMPONENT_COLUMNS = {
    "login_inactivity": "c_login_inactivity",
    "support_pressure": "c_support_pressure",
    "usage_drop": "c_usage_drop",
}


def ramp(values: pd.Series, spec: Ramp) -> pd.Series:
    """Normaliza a 0-100: 0 en `low` o menos, 100 en `high` o más, lineal entre ambos."""
    scaled = (values.astype("float64") - spec.low) / (spec.high - spec.low)
    return scaled.clip(0.0, 1.0) * 100.0


def classify(scores: pd.Series, cfg: ScoringConfig) -> pd.Series:
    """Alto si score >= alto_min, Medio si score >= medio_min, Bajo en otro caso."""
    labels = np.select(
        [scores >= cfg.alto_min, scores >= cfg.medio_min],
        ["Alto", "Medio"],
        default="Bajo",
    )
    return pd.Series(labels, index=scores.index)


class Scorer(Protocol):
    def score(self, features: pd.DataFrame) -> pd.DataFrame:
        """Debe devolver las columnas churn_score, risk_level, mrr_at_risk_usd y c_*."""
        ...


class WeightedRuleScorer:
    """Suma ponderada de tres señales: inactividad de login, tickets abiertos y caída de uso."""

    def __init__(self, config: ScoringConfig) -> None:
        self.config = config

    def score(self, features: pd.DataFrame) -> pd.DataFrame:
        cfg = self.config
        out = features.copy()

        usage_drop_pct = (-out["usage_change_pct"]).fillna(0.0)  # sin línea base = sin señal
        out["c_login_inactivity"] = ramp(out["days_since_last_login"], cfg.ramps["login_inactivity"])
        out["c_support_pressure"] = ramp(out["open_support_tickets"], cfg.ramps["support_pressure"])
        out["c_usage_drop"] = ramp(usage_drop_pct, cfg.ramps["usage_drop"])

        total = sum(cfg.weights[signal] * out[column] for signal, column in COMPONENT_COLUMNS.items())
        # Se clasifica sobre el valor redondeado: el nivel siempre coincide con el score que ve el CSM.
        out["churn_score"] = total.round(1)
        out["risk_level"] = classify(out["churn_score"], cfg)
        out["mrr_at_risk_usd"] = (out["mrr_usd"] * out["churn_score"] / 100.0).round(2)
        return out
