#!/usr/bin/env python3
"""Genera un dataset sintético de clientes B2B para probar y demostrar el pipeline.

Incluye cuentas sanas, en observación (riesgo Medio) y críticas (riesgo Alto), una cuenta
protagonista (C-1001) pensada para la demo y tres filas sucias que muestran la cuarentena.
Todas las empresas y correos son ficticios (los correos usan el dominio reservado example.com).
"""
from __future__ import annotations

import argparse
import csv
import itertools
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

COLUMNS = [
    "customer_id", "company_name", "plan", "mrr_usd", "last_login_at", "open_support_tickets",
    "usage_last_30d", "usage_prev_30d", "contract_end_date", "hubspot_company_id",
    "hubspot_owner_id", "owner_email",
]
SECTORS = ["Logística", "Agroexport", "Minera", "Clínica", "Retail", "Fintech", "Textiles",
           "Constructora", "Educación", "Pesquera"]
BRANDS = ["Andina", "Pacífico", "Cordillera", "Inti", "Quilla", "Costa Verde", "Altiplano",
          "Amazonas", "Titicaca", "Sol Norte"]
SUFFIXES = ["SAC", "SA", "EIRL"]
PLANS = {"Starter": (150, 600), "Growth": (800, 2500), "Enterprise": (3000, 7000)}
USAGE_BASELINE = {"Starter": (200, 900), "Growth": (1000, 4000), "Enterprise": (3000, 9000)}
CSM_EMAILS = ["ana.cs@example.com", "luis.cs@example.com", "marta.cs@example.com"]
FEATURED_ID = "C-1001"


def load_dotenv(path: Path) -> None:
    """Carga KEY=VALUE de un .env sin sobrescribir variables ya definidas en el entorno."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def iso_z(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def profile_params(profile: str, rng: random.Random) -> tuple[int, int, float]:
    """Devuelve (días sin login, tickets abiertos, ratio de uso actual / previo)."""
    if profile == "healthy":
        return rng.randint(0, 6), rng.randint(0, 1), rng.uniform(0.92, 1.30)
    if profile == "mixed":  # una sola señal débil: no debe disparar alerta
        weak = rng.choice(["tickets", "login", "usage"])
        login, tickets, ratio = rng.randint(0, 5), 0, rng.uniform(0.95, 1.20)
        if weak == "tickets":
            tickets = rng.randint(4, 5)
        elif weak == "login":
            login = rng.randint(14, 20)
        else:
            ratio = rng.uniform(0.60, 0.75)
        return login, tickets, ratio
    if profile == "watch":
        return rng.randint(22, 30), rng.randint(2, 3), rng.uniform(0.55, 0.70)
    if profile == "critical":
        return rng.randint(35, 60), rng.randint(3, 7), rng.uniform(0.10, 0.40)
    raise ValueError(profile)


def make_row(number: int, profile: str, name: str, rng: random.Random, as_of: datetime) -> dict[str, str]:
    plan = rng.choices(list(PLANS), weights=[50, 35, 15])[0]
    login_days, tickets, ratio = profile_params(profile, rng)
    prev = rng.randint(*USAGE_BASELINE[plan])
    renewal_days = rng.randint(10, 60) if profile == "critical" else rng.randint(15, 330)
    last_login = as_of - timedelta(days=login_days, hours=rng.randint(0, 20))
    return {
        "customer_id": f"C-{number}",
        "company_name": f"{name} {rng.choice(SUFFIXES)}",
        "plan": plan,
        "mrr_usd": str(int(round(rng.uniform(*PLANS[plan]), -1))),
        "last_login_at": iso_z(last_login),
        "open_support_tickets": str(tickets),
        "usage_last_30d": str(int(prev * ratio)),
        "usage_prev_30d": str(prev),
        "contract_end_date": (as_of + timedelta(days=renewal_days)).date().isoformat(),
        "hubspot_company_id": "",
        "hubspot_owner_id": "",
        "owner_email": CSM_EMAILS[number % len(CSM_EMAILS)],
    }


def featured_row(as_of: datetime, company_id: str, owner_id: str) -> dict[str, str]:
    """Cuenta protagonista de la demo: la de mayor MRR en riesgo del dataset."""
    return {
        "customer_id": FEATURED_ID,
        "company_name": "Logística Andina SAC",
        "plan": "Enterprise",
        "mrr_usd": "8400",
        "last_login_at": iso_z(as_of - timedelta(days=41)),
        "open_support_tickets": "4",
        "usage_last_30d": "780",
        "usage_prev_30d": "5200",
        "contract_end_date": (as_of + timedelta(days=27)).date().isoformat(),
        "hubspot_company_id": company_id,
        "hubspot_owner_id": owner_id,
        "owner_email": CSM_EMAILS[0],
    }


def build_dataset(total: int, seed: int, as_of: datetime, company_id: str, owner_id: str,
                  dirty: bool) -> list[dict[str, str]]:
    rng = random.Random(seed)
    n_critical = max(1, round(total * 0.12))   # incluye la cuenta protagonista
    n_watch = round(total * 0.22)
    n_mixed = max(1, round(total * 0.08))
    n_healthy = total - n_critical - n_watch - n_mixed
    profiles = (["critical"] * (n_critical - 1) + ["watch"] * n_watch
                + ["mixed"] * n_mixed + ["healthy"] * n_healthy)
    rng.shuffle(profiles)

    names = [f"{s} {b}" for s, b in itertools.product(SECTORS, BRANDS) if (s, b) != ("Logística", "Andina")]
    rng.shuffle(names)

    rows = [make_row(1002 + i, profile, names[i], rng, as_of) for i, profile in enumerate(profiles)]
    rows.append(featured_row(as_of, company_id, owner_id))
    rng.shuffle(rows)

    if dirty:  # filas rotas a propósito para mostrar la cuarentena
        healthy_id = next(r["customer_id"] for r in rows if r["customer_id"] != FEATURED_ID)
        rows.append({**rows[0], "customer_id": healthy_id})                    # ID duplicado
        rows.append({**rows[1], "customer_id": "C-9001", "last_login_at": "ayer por la tarde"})
        rows.append({**rows[2], "customer_id": "C-9002", "open_support_tickets": "-2"})
    return rows


def _rows_arg(value: str) -> int:
    number = int(value)
    if not 8 <= number <= 99:
        raise argparse.ArgumentTypeError("--rows debe estar entre 8 y 99")
    return number


def main() -> None:
    load_dotenv(Path(".env"))
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="data/sample_customers.csv")
    p.add_argument("--rows", type=_rows_arg, default=40, help="Clientes válidos a generar (8 a 99)")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--as-of", help="Fecha de referencia ISO 8601 (por defecto, ahora en UTC)")
    p.add_argument("--hubspot-company-id", default=os.environ.get("DEMO_HUBSPOT_COMPANY_ID", ""),
                   help="Company ID real de HubSpot para la cuenta C-1001 (o variable DEMO_HUBSPOT_COMPANY_ID)")
    p.add_argument("--hubspot-owner-id", default=os.environ.get("DEMO_HUBSPOT_OWNER_ID", ""),
                   help="Owner ID real de HubSpot para la cuenta C-1001 (o variable DEMO_HUBSPOT_OWNER_ID)")
    p.add_argument("--clean", action="store_true", help="No agrega filas sucias")
    args = p.parse_args()

    as_of = datetime.fromisoformat(args.as_of) if args.as_of else datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    rows = build_dataset(args.rows, args.seed, as_of, args.hubspot_company_id, args.hubspot_owner_id,
                         dirty=not args.clean)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} filas escritas en {out}")


if __name__ == "__main__":
    main()
