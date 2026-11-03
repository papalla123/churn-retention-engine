"""Utilidades de tiempo. El pipeline trabaja en UTC y recibe `as_of` por parámetro,
así el resultado es reproducible y testeable (nunca se llama a now() dentro de la lógica)."""
from __future__ import annotations

from datetime import datetime, timezone


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso_z(value: datetime, *, millis: bool = False) -> str:
    """ISO 8601 en UTC con sufijo Z (formato que acepta HubSpot para hs_timestamp)."""
    fmt = "%Y-%m-%dT%H:%M:%S.000Z" if millis else "%Y-%m-%dT%H:%M:%SZ"
    return ensure_utc(value).strftime(fmt)


def parse_iso(value: str) -> datetime:
    return ensure_utc(datetime.fromisoformat(value))
