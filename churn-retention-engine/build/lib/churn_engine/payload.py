"""Payload JSON de alerta: contrato versionado entre el pipeline y Make / HubSpot.

La lógica de negocio vive aquí (Python, versionada y con tests). El escenario de Make solo
enruta y mapea campos, por eso el payload ya trae el título y el cuerpo de la tarea y el
texto del mensaje de Slack.
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta
from typing import Any, Mapping

import pandas as pd

from .config import ScoringConfig
from .scoring import COMPONENT_COLUMNS
from .timeutil import ensure_utc, iso_z

SCHEMA_VERSION = "1.0"
EVENT_TYPE = "churn_risk_alert"

HUBSPOT_PRIORITY = {"Alto": "HIGH", "Medio": "MEDIUM", "Bajo": "LOW"}
RECOMMENDED_ACTION = {
    "Alto": "Llamada de salud de cuenta y revisión de los tickets abiertos con Soporte",
    "Medio": "Correo de seguimiento y revisión de uso con el cliente",
    "Bajo": "Monitoreo sin acción inmediata",
}


def build_event_id(customer_id: str, as_of: datetime, risk_level: str) -> str:
    """Clave de idempotencia: mismo cliente, mismo día y mismo nivel producen el mismo ID.

    Un reintento del mismo envío reutiliza la clave, y Make puede descartar duplicados con ella.
    """
    key = f"{customer_id}|{ensure_utc(as_of).date().isoformat()}|{risk_level}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


# --- conversores seguros (NaN, NA y NaT se convierten en None; JSON no admite NaN) -------------

def _is_missing(value: Any) -> bool:
    return (
        value is None
        or value is pd.NA
        or value is pd.NaT
        or (isinstance(value, float) and math.isnan(value))
    )


def _text(value: Any) -> str | None:
    if _is_missing(value):
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any, digits: int | None = None) -> float | None:
    if _is_missing(value):
        return None
    number = float(value)
    return round(number, digits) if digits is not None else number


def _int(value: Any) -> int | None:
    return None if _is_missing(value) else int(value)


def _date(value: Any) -> str | None:
    return None if _is_missing(value) else value.date().isoformat()


def _plural(n: int, singular: str, plural: str) -> str:
    return singular if n == 1 else plural


# --- explicación del score ---------------------------------------------------------------------

def _driver_login(row: Mapping[str, Any]) -> str:
    days = int(row["days_since_last_login"])
    return f"Sin iniciar sesión hace {days} {_plural(days, 'día', 'días')}"


def _driver_support(row: Mapping[str, Any]) -> str:
    n = int(row["open_support_tickets"])
    return f"{n} {_plural(n, 'ticket de soporte abierto', 'tickets de soporte abiertos')}"


def _driver_usage(row: Mapping[str, Any]) -> str:
    change = row["usage_change_pct"]
    if _is_missing(change):
        return "Sin línea base de uso en los 30 días previos"
    return f"Uso {float(change):+.0f}% frente a los 30 días previos"


_DRIVER_TEXT = {
    "login_inactivity": _driver_login,
    "support_pressure": _driver_support,
    "usage_drop": _driver_usage,
}


def explain_drivers(row: Mapping[str, Any], cfg: ScoringConfig) -> list[str]:
    """Causas del score ordenadas por aporte ponderado (la más pesada primero).

    Solo entran las señales que llegan a `driver_min_component`; si ninguna llega,
    se informa la de mayor aporte para que el CSM nunca reciba una alerta sin contexto.
    """
    component = {s: float(row[COMPONENT_COLUMNS[s]]) for s in cfg.weights}
    ranked = sorted(component, key=lambda s: cfg.weights[s] * component[s], reverse=True)
    chosen = [s for s in ranked if component[s] >= cfg.driver_min_component] or ranked[:1]
    return [_DRIVER_TEXT[s](row) for s in chosen]


def _renewal_line(contract_end: str | None, days: int | None) -> str | None:
    if contract_end is None:
        return None
    if days is None:
        return f"Renovación: {contract_end}."
    if days >= 0:
        return f"Renovación: {contract_end} (en {days} {_plural(days, 'día', 'días')})."
    return f"Contrato vencido el {contract_end} (hace {-days} {_plural(-days, 'día', 'días')})."


# --- construcción del payload ------------------------------------------------------------------

def build_alert(row: Mapping[str, Any], cfg: ScoringConfig, as_of: datetime, run_id: str) -> dict[str, Any]:
    """Convierte una fila puntuada en el payload de alerta (solo tipos nativos de JSON)."""
    level = str(row["risk_level"])
    score = float(row["churn_score"])
    customer_id = str(row["customer_id"])
    company = str(row["company_name"])
    plan = str(row["plan"])
    mrr = float(row["mrr_usd"])
    mrr_at_risk = float(row["mrr_at_risk_usd"])
    owner_email = _text(row.get("owner_email"))
    contract_end = _date(row.get("contract_end_date"))
    days_to_renewal = _int(row.get("days_to_renewal"))

    drivers = explain_drivers(row, cfg)
    hours = cfg.due_hours[level]
    due_at = ensure_utc(as_of) + timedelta(hours=hours)

    body_lines = [
        f"Score de churn: {score:.1f}/100 ({level}).",
        f"MRR en riesgo: USD {mrr_at_risk:,.0f} de USD {mrr:,.0f} mensuales.",
        "Señales detectadas:",
        *[f"- {d}" for d in drivers],
        _renewal_line(contract_end, days_to_renewal),
        f"Acción sugerida: {RECOMMENDED_ACTION[level]}.",
    ]
    task_body = "\n".join(line for line in body_lines if line)

    slack_text = (
        f"*Churn {level}* | {company} ({plan}) | score {score:.1f}/100 | "
        f"MRR en riesgo USD {mrr_at_risk:,.0f}\n"
        f"{'; '.join(drivers)}\n"
        f"Responsable: {owner_email or 'sin asignar'}"
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "event_type": EVENT_TYPE,
        "event_id": build_event_id(customer_id, as_of, level),
        "run_id": run_id,
        "generated_at": iso_z(as_of),
        "customer": {
            "customer_id": customer_id,
            "company_name": company,
            "plan": plan,
            "mrr_usd": round(mrr, 2),
            "contract_end_date": contract_end,
            "hubspot_company_id": _text(row.get("hubspot_company_id")),
            "hubspot_owner_id": _text(row.get("hubspot_owner_id")),
            "owner_email": owner_email,
        },
        "risk": {
            "churn_score": score,
            "risk_level": level,
            "mrr_at_risk_usd": round(mrr_at_risk, 2),
            "days_to_renewal": days_to_renewal,
            "drivers": drivers,
            "components": {
                signal: round(float(row[column]), 1) for signal, column in COMPONENT_COLUMNS.items()
            },
        },
        "metrics": {
            "last_login_at": iso_z(row["last_login_at"].to_pydatetime()),
            "days_since_last_login": int(row["days_since_last_login"]),
            "open_support_tickets": int(row["open_support_tickets"]),
            "usage_last_30d": _number(row["usage_last_30d"]),
            "usage_prev_30d": _number(row["usage_prev_30d"]),
            "usage_change_pct": _number(row["usage_change_pct"], 1),
        },
        "action": {
            "recommended": "create_hubspot_task",
            "priority": HUBSPOT_PRIORITY[level],
            "due_at": iso_z(due_at, millis=True),
            "task_title": f"[Churn {level}] {company}: contactar en {hours} h",
            "task_body": task_body,
            "slack_text": slack_text,
        },
    }
