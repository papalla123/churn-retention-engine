# churn-retention-engine

Pipeline en Python que puntúa el riesgo de abandono de cada cliente B2B a partir de su comportamiento de uso y avisa a Customer Success por webhook. Un escenario de Make recibe la alerta, crea una tarea de prioridad alta en HubSpot y publica un mensaje en Slack.

El score es una suma ponderada y explicable de tres señales: inactividad de login, tickets de soporte abiertos y caída de uso. No es un modelo entrenado. Sin un histórico de cancelaciones no hay con qué entrenarlo, y una regla visible le sirve más a un equipo de Customer Success que una probabilidad que nadie puede justificar. La interfaz `Scorer` deja el lugar listo para reemplazarla por un clasificador cuando ese histórico exista.

## Inicio rápido

Requiere Python 3.11 o superior.

```bash
python -m venv .venv
source .venv/bin/activate        # PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# Simulación: calcula y exporta, no envía nada.
# El CSV de ejemplo se generó con esa fecha de referencia, por eso --as-of.
churn-engine --as-of 2026-10-16T12:00:00Z --no-state
```

Salida:

```text
Corrida 20261016T120000Z | modo: simulación (sin --dispatch, no se envía nada)
Filas: 43 leídas, 40 válidas, 3 en cuarentena
Riesgo: Alto 5 | Medio 9 | Bajo 26

CLIENTE  EMPRESA                     PLAN        MRR USD  SCORE  NIVEL  ESTADO
-------  --------------------------  ----------  -------  -----  -----  --------
C-1001   Logística Andina SAC        Enterprise    8,400   90.8  Alto   simulada
C-1029   Constructora Altiplano SAC  Growth        1,410   89.0  Alto   simulada
C-1013   Educación Costa Verde SAC   Growth        1,170   97.5  Alto   simulada
C-1028   Agroexport Cordillera SAC   Starter         600   93.7  Alto   simulada
C-1037   Retail Andina SA            Starter         170  100.0  Alto   simulada
```

Las alertas salen ordenadas por MRR en riesgo (MRR × score / 100), no por score, para que la cuenta que más dinero pone en juego llegue primero.

### Envío real

1. Configura el escenario de Make y el webhook siguiendo [docs/MAKE_HUBSPOT_SETUP.md](docs/MAKE_HUBSPOT_SETUP.md).
2. Copia `.env.example` como `.env` y completa `MAKE_WEBHOOK_URL` y `MAKE_WEBHOOK_TOKEN`.
3. Regenera los datos de ejemplo para que las fechas sean de hoy. El CSV incluido está fijado al 16 de octubre de 2026 y, con el paso de los días, todas las cuentas parecerían inactivas y el guardrail bloquearía el envío:

```bash
python scripts/generate_sample_data.py
```

4. Ejecuta con `--dispatch`:

```bash
churn-engine --dispatch --max-alerts 1
```

Para repetir una prueba con la misma cuenta, agrega `--reset-state`. Sin eso, el cooldown (72 h por defecto) suprime la segunda alerta del mismo cliente.

## Cómo funciona

```text
CSV -> ingesta y validación -> features -> score 0-100 -> nivel (Alto/Medio/Bajo)
    -> guardrail -> cooldown (SQLite) -> payload JSON -> POST al webhook de Make
    -> HubSpot (tarea HIGH) + Slack
```

El detalle de cada etapa, los modos de falla y las decisiones de diseño están en [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

Cada corrida escribe en `output/<run_id>/`:

| Archivo | Contenido |
|---|---|
| `scored_customers.csv` | Todos los clientes válidos con sus componentes, score y nivel |
| `alerts.json` | Payloads de las alertas seleccionadas en la corrida |
| `rejected_rows.csv` | Filas en cuarentena con el valor original y el motivo |
| `run_summary.json` | Conteos, estado de cada alerta y bandera del guardrail |

## Contrato de datos

| Columna | Obligatoria | Regla |
|---|---|---|
| `customer_id` | sí | Texto único y no vacío |
| `company_name` | sí | No vacío |
| `plan` | sí | Texto libre |
| `mrr_usd` | sí | Número mayor o igual a 0 |
| `last_login_at` | sí | ISO 8601. Sin zona horaria se asume UTC. No puede estar más de un día en el futuro |
| `open_support_tickets` | sí | Entero mayor o igual a 0 |
| `usage_last_30d` | sí | Número mayor o igual a 0 |
| `usage_prev_30d` | sí | Número mayor o igual a 0. Con 0 no hay línea base y la caída de uso puntúa 0 |
| `contract_end_date` | no | ISO 8601. Solo da contexto de renovación, no entra al score |
| `hubspot_company_id` | no | Necesario para asociar la tarea a la empresa en HubSpot |
| `hubspot_owner_id` | no | Responsable de la tarea. Sin él, la tarea queda sin asignar |
| `owner_email` | no | Se muestra en el mensaje de Slack |

Una fila que incumple una regla no detiene la corrida: va a `rejected_rows.csv` con su motivo y el resto continúa.

## Modelo de scoring

Cada señal se lleva a una escala de 0 a 100 con una rampa lineal y se combina con pesos que suman 1. Todo vive en `config/scoring.toml`.

| Señal | Peso | 0 puntos | 100 puntos |
|---|---|---|---|
| Inactividad de login | 0.40 | 7 días o menos | 45 días o más |
| Tickets de soporte abiertos | 0.25 | 0 | 5 o más |
| Caída de uso frente a los 30 días previos | 0.35 | 10% o menos | 70% o más |

Alto desde 70 puntos, Medio desde 40, Bajo por debajo. El nivel se calcula sobre el score ya redondeado a un decimal, así coincide siempre con el número que ve el equipo.

Ejemplo, C-1001: 41 días sin login (89.5 puntos), 4 tickets (80) y caída de uso del 85% (100). Score: 0.40 × 89.5 + 0.25 × 80 + 0.35 × 100 = 90.8.

Una sola señal mala no dispara alerta. Cinco tickets abiertos con el resto en orden dan 25 puntos, nivel Bajo.

## Payload de alerta

Un POST por cliente, con cabeceras `Content-Type: application/json`, `Idempotency-Key` (igual a `event_id`) y `X-Webhook-Token`. El payload ya trae el título y el cuerpo de la tarea y el texto de Slack, así el escenario de Make solo enruta y mapea campos.

<details>
<summary>Ejemplo completo (schema 1.0)</summary>

```json
{
  "schema_version": "1.0",
  "event_type": "churn_risk_alert",
  "event_id": "2b64889246b671fb6351",
  "run_id": "20261016T120000Z",
  "generated_at": "2026-10-16T12:00:00Z",
  "customer": {
    "customer_id": "C-1001",
    "company_name": "Logística Andina SAC",
    "plan": "Enterprise",
    "mrr_usd": 8400.0,
    "contract_end_date": "2026-11-12",
    "hubspot_company_id": "900000001",
    "hubspot_owner_id": "800000001",
    "owner_email": "ana.cs@example.com"
  },
  "risk": {
    "churn_score": 90.8,
    "risk_level": "Alto",
    "mrr_at_risk_usd": 7627.2,
    "days_to_renewal": 27,
    "drivers": [
      "Sin iniciar sesión hace 41 días",
      "Uso -85% frente a los 30 días previos",
      "4 tickets de soporte abiertos"
    ],
    "components": {
      "login_inactivity": 89.5,
      "support_pressure": 80.0,
      "usage_drop": 100.0
    }
  },
  "metrics": {
    "last_login_at": "2026-09-05T12:00:00Z",
    "days_since_last_login": 41,
    "open_support_tickets": 4,
    "usage_last_30d": 780.0,
    "usage_prev_30d": 5200.0,
    "usage_change_pct": -85.0
  },
  "action": {
    "recommended": "create_hubspot_task",
    "priority": "HIGH",
    "due_at": "2026-10-17T12:00:00.000Z",
    "task_title": "[Churn Alto] Logística Andina SAC: contactar en 24 h",
    "task_body": "Score de churn: 90.8/100 (Alto).\nMRR en riesgo: USD 7,627 de USD 8,400 mensuales.\nSeñales detectadas:\n- Sin iniciar sesión hace 41 días\n- Uso -85% frente a los 30 días previos\n- 4 tickets de soporte abiertos\nRenovación: 2026-11-12 (en 27 días).\nAcción sugerida: Llamada de salud de cuenta y revisión de los tickets abiertos con Soporte.",
    "slack_text": "*Churn Alto* | Logística Andina SAC (Enterprise) | score 90.8/100 | MRR en riesgo USD 7,627\nSin iniciar sesión hace 41 días; Uso -85% frente a los 30 días previos; 4 tickets de soporte abiertos\nResponsable: ana.cs@example.com"
  }
}
```

</details>

`event_id` es un hash determinista de cliente, fecha y nivel: un reintento del mismo envío reutiliza la clave y el receptor puede descartar el duplicado.

## Opciones de la CLI

| Opción | Por defecto | Efecto |
|---|---|---|
| `--input` | `data/sample_customers.csv` | CSV de clientes |
| `--config` | `config/scoring.toml` | Pesos, rampas, umbrales y reglas de alerta |
| `--output-dir` | `output` | Carpeta base de resultados |
| `--as-of` | ahora (UTC) | Fecha de referencia ISO 8601, para resultados reproducibles |
| `--min-level` | `Alto` | Nivel mínimo que genera alerta (`Alto`, `Medio` o `Bajo`) |
| `--max-alerts` | `50` | Tope por corrida, ordenado por MRR en riesgo |
| `--dispatch` | apagado | Envía al webhook. Sin esta opción es una simulación |
| `--webhook-url` | `MAKE_WEBHOOK_URL` | URL del webhook (debe ser https, salvo localhost) |
| `--force` | apagado | Envía aunque el guardrail esté activo |
| `--state-db` | `state/alerts.sqlite3` | Historial de alertas para el cooldown |
| `--no-state` | apagado | No usa cooldown ni guarda historial |
| `--reset-state` | apagado | Borra el historial antes de correr |
| `--log-level` | `INFO` | `DEBUG`, `INFO`, `WARNING` o `ERROR` |
| `--no-color` | apagado | Desactiva los colores ANSI |

Códigos de salida: `0` correcto, `1` error de configuración o de datos, `2` alguna alerta no se pudo enviar, `3` guardrail activo y nada enviado. Un scheduler puede usarlos para avisar de fallos.

## Datos de ejemplo

```bash
python scripts/generate_sample_data.py                 # 40 clientes + 3 filas sucias, fechas relativas a hoy
python scripts/generate_sample_data.py --rows 60 --seed 3
```

La cuenta C-1001 (Logística Andina SAC) está pensada para la demo: es la de mayor MRR en riesgo. Para que su tarea quede asociada a una empresa real de HubSpot, define `DEMO_HUBSPOT_COMPANY_ID` y `DEMO_HUBSPOT_OWNER_ID` en `.env` antes de generar los datos, o pásalos con `--hubspot-company-id` y `--hubspot-owner-id`. Todas las empresas y correos del dataset son ficticios.

## Pruebas

```bash
python -m unittest discover -s tests -t .     # o: pytest
```

La suite tiene 66 pruebas. Cubren la validación y la cuarentena, el cálculo del score y sus bordes, el payload (JSON estricto, sin NaN), el cooldown, el pipeline completo con un despachador simulado, el corte de circuito tras fallos consecutivos y el cliente HTTP contra un servidor local (reintentos, `Retry-After`, errores de conexión).

## Programar la ejecución

El proceso es de lote corto: cada corrida lee los datos vigentes, puntúa y envía lo nuevo. La frecuencia define la latencia de la alerta. Con cron, cada 15 minutos:

```cron
*/15 * * * * cd /opt/churn-retention-engine && .venv/bin/churn-engine --input /data/customers.csv --dispatch --log-level WARNING >> logs/churn.log 2>&1
```

En Windows se puede usar el Programador de tareas, y en un repositorio de GitHub un workflow con `schedule`. En ambos casos el historial de `state/` tiene que persistir entre corridas o el cooldown no funcionará.

## Límites conocidos

- Procesa lotes, no eventos sueltos. Para reaccionar a un evento individual hay que reemplazar `ingest.py` por un consumidor (cola, CDC o API de producto).
- Los umbrales y pesos son punto de partida. Conviene calibrarlos contra cancelaciones reales antes de confiar en el nivel Alto.
- El estado vive en un SQLite local. Con varios procesos en paralelo se necesita una base compartida.
- La autenticación hacia Make es un token en cabecera más la URL secreta del webhook. Si el receptor pasa a un servicio propio, conviene firmar el cuerpo con HMAC.
