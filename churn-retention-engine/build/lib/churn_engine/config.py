"""Carga y validación de la configuración del modelo de scoring."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

SIGNALS = ("login_inactivity", "support_pressure", "usage_drop")
LEVEL_NAMES = ("Bajo", "Medio", "Alto")


class ConfigError(ValueError):
    """Configuración inválida o incompleta."""


@dataclass(frozen=True)
class Ramp:
    """Rampa lineal: 0 puntos en `low` o menos, 100 puntos en `high` o más."""

    low: float
    high: float

    def __post_init__(self) -> None:
        if not self.high > self.low:
            raise ConfigError(f"Rampa inválida: high ({self.high}) debe ser mayor que low ({self.low}).")


@dataclass(frozen=True)
class ScoringConfig:
    weights: dict[str, float]
    ramps: dict[str, Ramp]
    alto_min: float
    medio_min: float
    driver_min_component: float
    due_hours: dict[str, int]
    cooldown_hours: int
    escalation_delta: float
    guardrail_max_alto_ratio: float
    guardrail_min_rows: int

    @classmethod
    def from_toml(cls, path: str | Path) -> "ScoringConfig":
        path = Path(path)
        try:
            with path.open("rb") as fh:
                raw = tomllib.load(fh)
        except FileNotFoundError as exc:
            raise ConfigError(f"No existe el archivo de configuración: {path}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"TOML inválido en {path}: {exc}") from exc
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ScoringConfig":
        try:
            raw_weights, raw_ramps, levels = raw["weights"], raw["ramps"], raw["risk_levels"]
        except KeyError as exc:
            raise ConfigError(f"Falta la sección {exc} en la configuración.") from exc

        unknown = (set(raw_weights) | set(raw_ramps)) - set(SIGNALS)
        if unknown:
            raise ConfigError(f"Señales desconocidas: {', '.join(sorted(unknown))}.")

        try:
            weights = {s: float(raw_weights[s]) for s in SIGNALS}
            ramps = {s: Ramp(float(raw_ramps[s]["low"]), float(raw_ramps[s]["high"])) for s in SIGNALS}
            alto_min, medio_min = float(levels["alto_min"]), float(levels["medio_min"])
        except KeyError as exc:
            raise ConfigError(f"Falta la clave {exc} en la configuración.") from exc
        except ConfigError:
            raise
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"Valor no numérico en la configuración: {exc}") from exc

        if any(w < 0 for w in weights.values()):
            raise ConfigError("Los pesos no pueden ser negativos.")
        if abs(sum(weights.values()) - 1.0) > 1e-6:
            raise ConfigError(f"Los pesos deben sumar 1.0 (suman {sum(weights.values()):.4f}).")
        if not 0 < medio_min < alto_min <= 100:
            raise ConfigError("Se exige 0 < medio_min < alto_min <= 100.")

        alerts = raw.get("alerts", {})
        guardrail = raw.get("guardrail", {})
        try:
            due_hours = {"Alto": 24, "Medio": 72, "Bajo": 168}
            due_hours.update({k: int(v) for k, v in alerts.get("due_hours", {}).items()})
            cooldown_hours = int(alerts.get("cooldown_hours", 72))
            escalation_delta = float(alerts.get("escalation_delta", 10))
            driver_min = float(alerts.get("driver_min_component", 50))
            max_ratio = float(guardrail.get("max_alto_ratio", 0.40))
            min_rows = int(guardrail.get("min_rows", 20))
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"Valor inválido en [alerts] o [guardrail]: {exc}") from exc

        if set(due_hours) - set(LEVEL_NAMES):
            raise ConfigError("[alerts.due_hours] solo admite Alto, Medio y Bajo.")
        if cooldown_hours < 0 or escalation_delta < 0:
            raise ConfigError("cooldown_hours y escalation_delta no pueden ser negativos.")
        if not 0 < max_ratio <= 1:
            raise ConfigError("guardrail.max_alto_ratio debe estar en (0, 1].")

        return cls(
            weights=weights,
            ramps=ramps,
            alto_min=alto_min,
            medio_min=medio_min,
            driver_min_component=driver_min,
            due_hours=due_hours,
            cooldown_hours=cooldown_hours,
            escalation_delta=escalation_delta,
            guardrail_max_alto_ratio=max_ratio,
            guardrail_min_rows=min_rows,
        )
