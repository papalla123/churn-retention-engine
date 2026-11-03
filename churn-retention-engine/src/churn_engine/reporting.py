"""Reporte de consola de una corrida, pensado para leerse bien en una demo en pantalla."""
from __future__ import annotations

from .pipeline import AlertOutcome, RunSummary

_COLORS = {"Alto": "\x1b[1;31m", "Medio": "\x1b[33m", "Bajo": "\x1b[32m"}
_RESET = "\x1b[0m"
_STATUS_LABEL = {"sent": "enviada", "failed": "fallida", "dry_run": "simulada", "blocked": "bloqueada"}


def _table(rows: list[AlertOutcome], color: bool) -> list[str]:
    headers = ("CLIENTE", "EMPRESA", "PLAN", "MRR USD", "SCORE", "NIVEL", "ESTADO")
    body = [
        (
            o.customer_id,
            o.company_name[:28],
            o.plan,
            f"{o.mrr_usd:,.0f}",
            f"{o.churn_score:.1f}",
            o.risk_level,
            _STATUS_LABEL[o.status],
        )
        for o in rows
    ]
    widths = [max(len(h), *(len(r[i]) for r in body)) for i, h in enumerate(headers)]
    right_aligned = {3, 4}

    def fmt(cells: tuple[str, ...], paint: bool) -> str:
        parts = []
        for i, cell in enumerate(cells):
            text = cell.rjust(widths[i]) if i in right_aligned else cell.ljust(widths[i])
            if paint and color and i == 5:
                text = _COLORS[cell] + text + _RESET
            parts.append(text)
        return "  ".join(parts).rstrip()

    rule = "  ".join("-" * w for w in widths)
    return [fmt(headers, False), rule, *(fmt(r, True) for r in body)]


def render_report(summary: RunSummary, *, color: bool = False) -> str:
    mode = "envío real" if summary.mode == "dispatch" else "simulación (sin --dispatch, no se envía nada)"
    lines = [
        f"Corrida {summary.run_id} | modo: {mode}",
        f"Filas: {summary.rows_in} leídas, {summary.rows_valid} válidas, "
        f"{summary.rows_rejected} en cuarentena",
        "Riesgo: " + " | ".join(f"{lvl} {summary.level_counts[lvl]}" for lvl in ("Alto", "Medio", "Bajo")),
        "",
    ]
    table_rows = [o for o in summary.outcomes if o.status in _STATUS_LABEL]
    lines += _table(table_rows, color) if table_rows else ["Sin alertas para enviar en esta corrida."]

    skipped = []
    if summary.alert_counts["suppressed"]:
        skipped.append(f"{summary.alert_counts['suppressed']} suprimidas por cooldown")
    if summary.alert_counts["truncated"]:
        skipped.append(f"{summary.alert_counts['truncated']} omitidas por --max-alerts")
    if skipped:
        lines += ["", "No enviadas: " + ", ".join(skipped)]
    if summary.guardrail_tripped:
        lines += ["", "AVISO: guardrail activo. Demasiadas cuentas en Alto, revisa los datos de origen "
                      "(usa --force solo si el resultado es correcto)."]
    lines += ["", f"Archivos: {summary.output_dir}"]
    return "\n".join(lines)
