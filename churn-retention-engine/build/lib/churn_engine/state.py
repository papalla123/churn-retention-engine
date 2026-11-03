"""Estado de alertas en SQLite: evita duplicados y la fatiga de alertas en Customer Success.

Regla: un cliente no recibe otra alerta dentro de la ventana de cooldown, salvo que haya
subido de nivel o su score haya crecido al menos `escalation_delta` puntos desde la última
alerta enviada. Solo los envíos exitosos (SENT) cuentan; un envío fallido se reintenta en
la corrida siguiente.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .scoring import LEVEL_RANK
from .timeutil import ensure_utc, iso_z, parse_iso

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT    NOT NULL,
    customer_id TEXT    NOT NULL,
    risk_level  TEXT    NOT NULL,
    churn_score REAL    NOT NULL,
    status      TEXT    NOT NULL CHECK (status IN ('SENT', 'FAILED')),
    attempts    INTEGER NOT NULL,
    error       TEXT,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alert_customer ON alert_log (customer_id, status, created_at);
"""


class AlertStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "AlertStateStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def last_sent(self, customer_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT risk_level, churn_score, created_at FROM alert_log "
            "WHERE customer_id = ? AND status = 'SENT' "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (customer_id,),
        ).fetchone()

    def decide(
        self,
        *,
        customer_id: str,
        risk_level: str,
        churn_score: float,
        as_of: datetime,
        cooldown_hours: float,
        escalation_delta: float,
    ) -> tuple[bool, str]:
        """Devuelve (enviar?, motivo)."""
        last = self.last_sent(customer_id)
        if last is None:
            return True, "first_alert"
        elapsed_hours = (ensure_utc(as_of) - parse_iso(last["created_at"])).total_seconds() / 3600
        if elapsed_hours >= cooldown_hours:
            return True, "cooldown_elapsed"
        if LEVEL_RANK[risk_level] > LEVEL_RANK[last["risk_level"]]:
            return True, "level_escalated"
        if churn_score - last["churn_score"] >= escalation_delta:
            return True, "score_increased"
        return False, "cooldown_active"

    def record(
        self,
        *,
        event_id: str,
        customer_id: str,
        risk_level: str,
        churn_score: float,
        status: str,
        attempts: int,
        error: str | None,
        at: datetime,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO alert_log "
                "(event_id, customer_id, risk_level, churn_score, status, attempts, error, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, customer_id, risk_level, churn_score, status, attempts, error, iso_z(at)),
            )
