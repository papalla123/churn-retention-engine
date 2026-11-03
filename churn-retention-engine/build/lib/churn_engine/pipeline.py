"""Orquestación: ingesta -> validación -> features -> score -> selección de alertas -> envío."""
from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol

from .config import ScoringConfig
from .dispatch import DispatchResult
from .features import build_features
from .ingest import SchemaError, load_customers, validate_customers
from .payload import SCHEMA_VERSION, build_alert
from .scoring import LEVEL_RANK, LEVELS, WeightedRuleScorer
from .state import AlertStateStore
from .timeutil import ensure_utc, iso_z

log = logging.getLogger(__name__)

STATUSES = ("sent", "failed", "dry_run", "blocked", "suppressed", "truncated")


class Dispatcher(Protocol):
    def send(self, alert: Mapping[str, Any]) -> DispatchResult: ...


@dataclass(frozen=True)
class AlertOutcome:
    customer_id: str
    company_name: str
    plan: str
    mrr_usd: float
    churn_score: float
    risk_level: str
    status: str  # sent | failed | dry_run | blocked | suppressed | truncated
    detail: str = ""


@dataclass
class RunSummary:
    run_id: str
    as_of: str
    input_file: str
    mode: str  # "dispatch" | "dry-run"
    rows_in: int
    rows_valid: int
    rows_rejected: int
    level_counts: dict[str, int]
    guardrail_tripped: bool
    candidates: int
    alert_counts: dict[str, int]
    output_dir: str
    outcomes: list[AlertOutcome] = field(default_factory=list)


def _outcome(row: Mapping[str, Any], status: str, detail: str = "") -> AlertOutcome:
    return AlertOutcome(
        customer_id=str(row["customer_id"]),
        company_name=str(row["company_name"]),
        plan=str(row["plan"]),
        mrr_usd=float(row["mrr_usd"]),
        churn_score=float(row["churn_score"]),
        risk_level=str(row["risk_level"]),
        status=status,
        detail=detail,
    )


def _write_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, allow_nan=False)
        fh.write("\n")


def run_pipeline(
    *,
    input_path: str | Path,
    config: ScoringConfig,
    output_root: str | Path,
    as_of: datetime,
    min_level: str = "Alto",
    max_alerts: int = 50,
    dispatcher: Dispatcher | None = None,
    store: AlertStateStore | None = None,
    force: bool = False,
    max_consecutive_failures: int = 3,
) -> RunSummary:
    """Ejecuta una corrida completa.

    Sin `dispatcher` es una simulación: calcula y exporta todo, pero no envía nada ni escribe estado.
    """
    if min_level not in LEVEL_RANK:
        raise ValueError(f"min_level inválido: {min_level!r}. Usa Alto, Medio o Bajo.")
    if max_alerts < 1:
        raise ValueError("max_alerts debe ser al menos 1.")
    if max_consecutive_failures < 1:
        raise ValueError("max_consecutive_failures debe ser al menos 1.")

    as_of = ensure_utc(as_of)
    run_id = as_of.strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(output_root) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) Ingesta y validación
    raw = load_customers(input_path)
    valid, rejected = validate_customers(raw, as_of)
    if not rejected.empty:
        rejected.to_csv(out_dir / "rejected_rows.csv", index=False, encoding="utf-8")
    if valid.empty:
        raise SchemaError(f"Ninguna fila superó la validación. Revisa {out_dir / 'rejected_rows.csv'}.")

    # 2) Features y score
    scored = WeightedRuleScorer(config).score(build_features(valid, as_of))
    scored = scored.sort_values(
        ["mrr_at_risk_usd", "churn_score", "customer_id"], ascending=[False, False, True], kind="stable"
    ).reset_index(drop=True)
    scored.to_csv(out_dir / "scored_customers.csv", index=False, encoding="utf-8")

    counts = scored["risk_level"].value_counts().reindex(list(LEVELS), fill_value=0)
    level_counts = {level: int(counts[level]) for level in LEVELS}

    # 3) Guardrail: un salto anómalo de cuentas en Alto suele ser un dato de origen roto
    alto_ratio = level_counts["Alto"] / len(scored)
    tripped = len(scored) >= config.guardrail_min_rows and alto_ratio > config.guardrail_max_alto_ratio
    if tripped:
        log.error(
            "Guardrail: %.0f%% de las cuentas salió en Alto (límite %.0f%%). Revisa la fuente de datos.",
            alto_ratio * 100, config.guardrail_max_alto_ratio * 100,
        )

    # 4) Selección: nivel mínimo, cooldown y tope por corrida (orden: mayor MRR en riesgo primero)
    min_rank = LEVEL_RANK[min_level]
    candidates = scored[scored["risk_level"].map(LEVEL_RANK) >= min_rank].to_dict("records")
    outcomes: list[AlertOutcome] = []
    selected: list[dict[str, Any]] = []
    for row in candidates:
        if store is not None:
            allowed, reason = store.decide(
                customer_id=str(row["customer_id"]),
                risk_level=str(row["risk_level"]),
                churn_score=float(row["churn_score"]),
                as_of=as_of,
                cooldown_hours=config.cooldown_hours,
                escalation_delta=config.escalation_delta,
            )
            if not allowed:
                outcomes.append(_outcome(row, "suppressed", reason))
                continue
        if len(selected) >= max_alerts:
            outcomes.append(_outcome(row, "truncated", f"max_alerts={max_alerts}"))
            continue
        selected.append(row)

    # 5) Payloads (siempre se exportan, aunque no se envíen)
    alerts = [build_alert(row, config, as_of, run_id) for row in selected]
    _write_json(
        out_dir / "alerts.json",
        {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "generated_at": iso_z(as_of),
            "min_level": min_level,
            "alerts": alerts,
        },
    )

    # 6) Envío
    live = dispatcher is not None and (not tripped or force)
    consecutive_failures = 0
    circuit_open = False
    for row, alert in zip(selected, alerts):
        if dispatcher is None:
            outcomes.append(_outcome(row, "dry_run"))
            continue
        if not live:
            outcomes.append(_outcome(row, "blocked", "guardrail"))
            continue
        if circuit_open:
            # Sin intento y sin registro de estado: se reintenta completa en la corrida siguiente.
            outcomes.append(_outcome(row, "failed", "no enviada: circuito abierto"))
            continue
        result = dispatcher.send(alert)
        status = "sent" if result.ok else "failed"
        detail = (
            f"HTTP {result.status_code}, {result.attempts} intento(s)"
            if result.ok
            else f"{result.error}, {result.attempts} intento(s)"
        )
        outcomes.append(_outcome(row, status, detail))
        if store is not None:
            store.record(
                event_id=alert["event_id"],
                customer_id=str(row["customer_id"]),
                risk_level=str(row["risk_level"]),
                churn_score=float(row["churn_score"]),
                status="SENT" if result.ok else "FAILED",
                attempts=result.attempts,
                error=result.error,
                at=as_of,
            )
        log.info(
            "envío %s customer=%s event_id=%s (%s)",
            status.upper(), row["customer_id"], alert["event_id"], detail,
        )
        consecutive_failures = 0 if result.ok else consecutive_failures + 1
        if consecutive_failures >= max_consecutive_failures:
            circuit_open = True
            log.error(
                "Se detiene el envío tras %d fallos consecutivos. El destino parece caído; "
                "las alertas restantes se reintentarán en la próxima corrida.",
                consecutive_failures,
            )

    tally = Counter(o.status for o in outcomes)
    summary = RunSummary(
        run_id=run_id,
        as_of=iso_z(as_of),
        input_file=str(input_path),
        mode="dispatch" if dispatcher is not None else "dry-run",
        rows_in=len(raw),
        rows_valid=len(valid),
        rows_rejected=len(rejected),
        level_counts=level_counts,
        guardrail_tripped=tripped,
        candidates=len(candidates),
        alert_counts={status: tally.get(status, 0) for status in STATUSES},
        output_dir=str(out_dir),
        outcomes=outcomes,
    )
    _write_json(out_dir / "run_summary.json", asdict(summary))
    return summary
