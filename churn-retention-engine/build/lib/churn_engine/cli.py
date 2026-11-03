"""Interfaz de línea de comandos.

Códigos de salida: 0 correcto, 1 error de configuración o de datos,
2 alguna alerta no se pudo enviar, 3 guardrail activo (no se envió nada).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .config import ConfigError, ScoringConfig
from .dispatch import WebhookDispatcher
from .ingest import SchemaError
from .pipeline import run_pipeline
from .reporting import render_report
from .state import AlertStateStore
from .timeutil import parse_iso

EXIT_OK, EXIT_FATAL, EXIT_PARTIAL, EXIT_GUARDRAIL = 0, 1, 2, 3


def _iso_datetime(value: str) -> datetime:
    try:
        return parse_iso(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"fecha ISO 8601 inválida: {value!r}") from exc


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"se esperaba un entero: {value!r}") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("debe ser al menos 1")
    return number


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="churn-engine",
        description="Calcula el score de churn por cliente y dispara alertas de retención por webhook.",
    )
    p.add_argument("--input", default="data/sample_customers.csv", help="CSV de clientes")
    p.add_argument("--config", default="config/scoring.toml", help="Configuración del modelo (TOML)")
    p.add_argument("--output-dir", default="output", help="Carpeta base de resultados")
    p.add_argument("--as-of", type=_iso_datetime, help="Fecha de referencia ISO 8601 (por defecto, ahora en UTC)")
    p.add_argument("--min-level", choices=["Alto", "Medio", "Bajo"], default="Alto",
                   help="Nivel mínimo que genera alerta (por defecto Alto)")
    p.add_argument("--max-alerts", type=_positive_int, default=50,
                   help="Tope de alertas por corrida, ordenadas por MRR en riesgo (por defecto 50)")
    p.add_argument("--dispatch", action="store_true",
                   help="Envía las alertas al webhook. Sin esta opción la corrida es una simulación")
    p.add_argument("--webhook-url", help="URL del webhook (por defecto, variable MAKE_WEBHOOK_URL)")
    p.add_argument("--force", action="store_true", help="Envía aunque el guardrail de datos esté activo")
    p.add_argument("--state-db", default="state/alerts.sqlite3", help="Base SQLite con el historial de alertas")
    p.add_argument("--no-state", action="store_true", help="No usa cooldown ni guarda historial")
    p.add_argument("--reset-state", action="store_true", help="Borra el historial de alertas antes de correr")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    p.add_argument("--no-color", action="store_true", help="Desactiva los colores ANSI")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _load_dotenv(path: Path) -> None:
    """Carga KEY=VALUE de un .env sin sobrescribir variables ya definidas en el entorno."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _build_dispatcher(args: argparse.Namespace) -> WebhookDispatcher | None:
    if not args.dispatch:
        return None
    url = args.webhook_url or os.environ.get("MAKE_WEBHOOK_URL")
    if not url:
        raise ConfigError("Falta la URL del webhook: define MAKE_WEBHOOK_URL en .env o usa --webhook-url.")
    return WebhookDispatcher(url, token=os.environ.get("MAKE_WEBHOOK_TOKEN"))


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):  # evita errores de codificación en consolas de Windows
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)
    _load_dotenv(Path(".env"))
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    as_of = args.as_of or datetime.now(timezone.utc)

    store: AlertStateStore | None = None
    try:
        config = ScoringConfig.from_toml(args.config)
        dispatcher = _build_dispatcher(args)
        if not args.no_state:
            state_path = Path(args.state_db)
            if args.reset_state:
                state_path.unlink(missing_ok=True)
            store = AlertStateStore(state_path)
        summary = run_pipeline(
            input_path=args.input,
            config=config,
            output_root=args.output_dir,
            as_of=as_of,
            min_level=args.min_level,
            max_alerts=args.max_alerts,
            dispatcher=dispatcher,
            store=store,
            force=args.force,
        )
    except (ConfigError, SchemaError, FileNotFoundError, ValueError) as exc:
        logging.getLogger("churn_engine").error("%s", exc)
        return EXIT_FATAL
    finally:
        if store is not None:
            store.close()

    use_color = sys.stdout.isatty() and not args.no_color and "NO_COLOR" not in os.environ
    print(render_report(summary, color=use_color))

    if args.dispatch and summary.guardrail_tripped and not args.force:
        return EXIT_GUARDRAIL
    if summary.alert_counts["failed"]:
        return EXIT_PARTIAL
    return EXIT_OK
