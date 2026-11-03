"""Utilidades compartidas por los tests."""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from churn_engine.config import ScoringConfig
from churn_engine.features import build_features
from churn_engine.ingest import load_customers, validate_customers
from churn_engine.scoring import WeightedRuleScorer

AS_OF = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]
HEADER = (
    "customer_id,company_name,plan,mrr_usd,last_login_at,open_support_tickets,"
    "usage_last_30d,usage_prev_30d,contract_end_date,hubspot_company_id,hubspot_owner_id,owner_email"
)


def default_config() -> ScoringConfig:
    return ScoringConfig.from_toml(ROOT / "config" / "scoring.toml")


def csv_row(
    customer_id: str,
    *,
    company: str = "Empresa Demo SAC",
    plan: str = "Growth",
    mrr: float = 1000,
    days_ago: float = 2,
    tickets: float = 0,
    usage_last: float = 1000,
    usage_prev: float = 1000,
    contract_end: str = "",
    company_id: str = "",
    owner_id: str = "",
    owner_email: str = "",
) -> str:
    last_login = (AS_OF - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cells = [customer_id, company, plan, mrr, last_login, tickets, usage_last, usage_prev,
             contract_end, company_id, owner_id, owner_email]
    return ",".join(str(c) for c in cells)


def write_csv(path: Path, rows: list[str], header: str = HEADER) -> Path:
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


def score_rows(rows: list[str]) -> pd.DataFrame:
    """CSV -> validación -> features -> score, con la configuración por defecto."""
    with tempfile.TemporaryDirectory() as tmp:
        path = write_csv(Path(tmp) / "customers.csv", rows)
        valid, rejected = validate_customers(load_customers(path), AS_OF)
    assert rejected.empty, rejected
    return WeightedRuleScorer(default_config()).score(build_features(valid, AS_OF))


CRITICAL = dict(days_ago=41, tickets=4, usage_last=780, usage_prev=5200, mrr=8400)
MEDIUM = dict(days_ago=25, tickets=2, usage_last=600, usage_prev=1000, mrr=1000)


def standard_rows(n_healthy: int = 20, n_critical: int = 4) -> list[str]:
    """Clientes sanos más clientes críticos con MRR creciente (X-0 menor, X-n mayor)."""
    rows = [csv_row(f"H-{i}") for i in range(n_healthy)]
    rows += [
        csv_row(f"X-{i}", mrr=1000 + 500 * i, days_ago=50, tickets=5, usage_last=200, usage_prev=1000)
        for i in range(n_critical)
    ]
    return rows
