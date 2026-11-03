"""Ingesta del dataset de clientes: contrato de datos y cuarentena de filas inválidas.

Todo se lee como texto y se convierte de forma explícita. Así pandas no adivina tipos
(IDs con ceros a la izquierda, fechas ambiguas) y cada fila rechazada conserva el valor
original junto con el motivo, lista para revisarla o reprocesarla.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from .timeutil import ensure_utc

log = logging.getLogger(__name__)

REQUIRED_COLUMNS = (
    "customer_id",
    "company_name",
    "plan",
    "mrr_usd",
    "last_login_at",
    "open_support_tickets",
    "usage_last_30d",
    "usage_prev_30d",
)
OPTIONAL_COLUMNS = ("contract_end_date", "hubspot_company_id", "hubspot_owner_id", "owner_email")
NUMERIC_COLUMNS = ("mrr_usd", "open_support_tickets", "usage_last_30d", "usage_prev_30d")


class SchemaError(ValueError):
    """El archivo de entrada no cumple el contrato de datos."""


def load_customers(path: str | Path) -> pd.DataFrame:
    """Lee el CSV como texto, normaliza nombres de columna y verifica el esquema mínimo."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"No existe el archivo de entrada: {path}")
    if path.suffix.lower() != ".csv":
        raise SchemaError(f"Formato no soportado ({path.suffix or 'sin extensión'}). Usa un CSV.")
    try:
        df = pd.read_csv(path, dtype="string", keep_default_na=False, encoding="utf-8-sig")
    except pd.errors.EmptyDataError as exc:
        raise SchemaError("El archivo CSV está vacío.") from exc

    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise SchemaError("Faltan columnas obligatorias: " + ", ".join(missing))
    for col in OPTIONAL_COLUMNS:
        if col not in df.columns:
            df[col] = pd.Series("", index=df.index, dtype="string")
    for col in df.columns:
        df[col] = df[col].str.strip()
    return df


def validate_customers(raw: pd.DataFrame, as_of: datetime) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Separa filas válidas de filas en cuarentena.

    Devuelve (clean, rejected). `clean` trae los tipos ya convertidos (float, int, datetime UTC).
    `rejected` conserva los valores originales más la columna `reject_reason`.
    """
    as_of_ts = pd.Timestamp(ensure_utc(as_of))
    df = raw.copy()

    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col].astype(object), errors="coerce").astype("float64")
    contract_raw = df["contract_end_date"].astype(object)
    df["last_login_at"] = pd.to_datetime(
        df["last_login_at"].astype(object), utc=True, errors="coerce", format="ISO8601"
    )
    df["contract_end_date"] = pd.to_datetime(contract_raw, utc=True, errors="coerce", format="ISO8601")

    ids = df["customer_id"]
    checks: list[tuple[str, pd.Series]] = [
        ("missing_customer_id", ids == ""),
        ("duplicate_customer_id", (ids != "") & ids.duplicated(keep="first")),
        ("missing_company_name", df["company_name"] == ""),
        ("invalid_last_login_at", df["last_login_at"].isna()),
        ("last_login_in_future", df["last_login_at"] > as_of_ts + pd.Timedelta(days=1)),
        ("invalid_contract_end_date", (contract_raw != "") & df["contract_end_date"].isna()),
    ]
    for col in NUMERIC_COLUMNS:
        checks.append((f"invalid_{col}", df[col].isna()))
        checks.append((f"negative_{col}", df[col] < 0))
    tickets = df["open_support_tickets"]
    checks.append(("non_integer_open_support_tickets", tickets.notna() & (tickets % 1 != 0)))

    reasons = pd.Series([""] * len(df), index=df.index, dtype="object")
    for label, mask in checks:
        flagged = mask.fillna(False).astype(bool)
        reasons = reasons.mask(flagged, reasons + label + ";")

    bad = reasons != ""
    rejected = raw.loc[bad].copy()
    rejected["reject_reason"] = reasons[bad].str.rstrip(";")

    clean = df.loc[~bad].copy()
    clean["open_support_tickets"] = clean["open_support_tickets"].astype("int64")

    if bad.any():
        counts = rejected["reject_reason"].str.split(";").explode().value_counts()
        log.warning("%d filas en cuarentena: %s", int(bad.sum()), counts.to_dict())
    return clean.reset_index(drop=True), rejected.reset_index(drop=True)
