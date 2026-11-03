"""Envío de alertas a un webhook HTTP (Make u otro receptor) con reintentos e idempotencia."""
from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

import requests

from . import __version__

log = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class DispatchResult:
    ok: bool
    status_code: int | None
    attempts: int
    error: str | None = None


def redact_url(url: str) -> str:
    """Las URLs de webhook llevan un identificador secreto en la ruta: no se loguean completas."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/..."


def _retry_after_seconds(header: str | None) -> float | None:
    if not header:
        return None
    try:
        seconds = float(header)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


class WebhookDispatcher:
    """POST JSON con timeout, reintentos con backoff exponencial y cabecera Idempotency-Key.

    Reintenta ante errores de red, timeouts y estados 408/425/429/5xx (respetando Retry-After).
    Un 4xx distinto de 408/425/429 se considera definitivo: reintentar no lo arregla.
    """

    def __init__(
        self,
        url: str,
        *,
        token: str | None = None,
        timeout: tuple[float, float] = (3.05, 15.0),
        max_attempts: int = 4,
        backoff_base: float = 1.0,
        backoff_max: float = 30.0,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        parsed = urlparse(url)
        if not parsed.netloc:
            raise ValueError("URL de webhook inválida.")
        if parsed.scheme != "https" and parsed.hostname not in LOCAL_HOSTS:
            raise ValueError("El webhook debe usar https (http solo se admite en localhost).")
        if max_attempts < 1:
            raise ValueError("max_attempts debe ser al menos 1.")
        self.url = url
        self.token = token
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.session = session or requests.Session()
        self._sleep = sleep
        self._jitter = jitter

    def _delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(retry_after, self.backoff_max)
        base = min(self.backoff_max, self.backoff_base * 2 ** (attempt - 1))
        return base * (1 + 0.25 * self._jitter())

    def send(self, alert: Mapping[str, Any]) -> DispatchResult:
        body = json.dumps(alert, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Idempotency-Key": str(alert["event_id"]),
            "User-Agent": f"churn-engine/{__version__}",
        }
        if self.token:
            headers["X-Webhook-Token"] = self.token

        status: int | None = None
        error: str | None = None
        for attempt in range(1, self.max_attempts + 1):
            retry_after: float | None = None
            try:
                response = self.session.post(self.url, data=body, headers=headers, timeout=self.timeout)
                status = response.status_code
                if 200 <= status < 300:
                    return DispatchResult(True, status, attempt)
                error = f"HTTP {status}"
                if status not in RETRYABLE_STATUS:
                    return DispatchResult(False, status, attempt, error)
                retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
            except (requests.ConnectionError, requests.Timeout) as exc:
                error = type(exc).__name__
            except requests.RequestException as exc:  # URL mal formada, etc.: no tiene sentido reintentar
                return DispatchResult(False, status, attempt, type(exc).__name__)

            if attempt < self.max_attempts:
                delay = self._delay(attempt, retry_after)
                log.warning(
                    "Intento %d/%d fallido (%s) contra %s; reintento en %.1f s",
                    attempt, self.max_attempts, error, redact_url(self.url), delay,
                )
                self._sleep(delay)
        return DispatchResult(False, status, self.max_attempts, error)
