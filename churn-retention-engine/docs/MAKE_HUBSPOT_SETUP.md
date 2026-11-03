# Configuración de Make y HubSpot

Resultado: cada alerta que envía `churn-engine --dispatch` crea una tarea de prioridad alta en HubSpot, asociada a la empresa y asignada a su responsable, y publica un mensaje en Slack.

```text
Webhooks > Custom webhook
        |
     filtro (token válido)
        |
      Router
        |-- ruta 1 (hay company ID y nivel Alto) --> HubSpot CRM > Create a Task
        |-- ruta 2 (todas las alertas) ------------> Slack > Create a Message
```

Si ya tienes conexiones de HubSpot y Slack en Make, reutilízalas.

## 1. Preparar HubSpot

1. Elige o crea una empresa de prueba. Abre su registro y copia el Company ID de la URL: `.../record/0-2/<COMPANY_ID>`.
2. Obtén el Owner ID del usuario que recibirá las tareas. En Make, agrega temporalmente un módulo HubSpot CRM > Make an API Call con método `GET` y URL `/crm/v3/owners`, ejecútalo una vez y copia el campo `id` del usuario cuyo `email` coincida. Si omites el dueño, la tarea queda sin asignar.
3. Escribe ambos valores en `.env` como `DEMO_HUBSPOT_COMPANY_ID` y `DEMO_HUBSPOT_OWNER_ID`, y regenera los datos:

```bash
python scripts/generate_sample_data.py
```

La cuenta C-1001 queda con esos IDs. En una integración real, `hubspot_company_id` y `hubspot_owner_id` llegan en el CSV desde tu fuente de datos.

## 2. Crear el webhook en Make

1. Crea un escenario nuevo y agrega el módulo Webhooks > Custom webhook.
2. Haz clic en Add, nombra el webhook `churn-alerts` y abre las opciones avanzadas. Activa Get request headers (en Yes). Deja JSON pass-through apagado para que Make descomponga el payload en campos mapeables. Guarda.
3. Copia la URL del webhook en `.env` como `MAKE_WEBHOOK_URL`.
4. Genera un secreto largo y guárdalo como `MAKE_WEBHOOK_TOKEN`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

5. Opcional: si envías siempre desde la misma IP, anótala en IP restrictions del webhook.

La zona de tu cuenta (`us1`, `us2`, `eu1`, `eu2`) cambia el dominio de la URL. Usa la que te muestre el módulo.

## 3. Enseñarle a Make la estructura del payload

1. En el escenario, haz clic en Run once. El webhook queda esperando una petición.
2. Desde el proyecto, envía una alerta real:

```bash
churn-engine --reset-state --dispatch --max-alerts 1
```

3. Make muestra que determinó la estructura. Si activaste Get request headers después de este paso, vuelve a determinarla (Re-determine data structure en el webhook) y reenvía, para que las cabeceras aparezcan en el panel de mapeo.

## 4. Filtro de autenticación

1. Haz clic en la línea entre el webhook y el siguiente módulo y crea un filtro llamado `Token válido`.
2. Condición: el campo `x-webhook-token` dentro de Headers, operador Equal to, valor igual a tu `MAKE_WEBHOOK_TOKEN`. Los nombres de cabecera suelen aparecer en minúsculas. Usa el nombre tal como lo muestre el panel.

Sin este filtro, cualquiera que conozca la URL puede ejecutar el escenario. Con él, las peticiones sin el token se detienen antes de tocar HubSpot.

## 5. Router con dos rutas

Agrega un Router después del webhook.

Ruta 1, tarea en HubSpot. Filtro con dos condiciones unidas por AND:
- `customer.hubspot_company_id` con el operador Exists.
- `risk.risk_level` Equal to `Alto`.

Ruta 2, aviso en Slack. Sin filtro. Recibe todas las alertas, también las de cuentas que aún no tienen empresa asociada en HubSpot.

## 6. Ruta 1: HubSpot CRM > Create a Task

Crea la conexión (HubSpot pide que un Super Admin apruebe la app de Make antes de conectar) y mapea los campos. Los nombres del módulo pueden variar un poco. La correspondencia es con estas propiedades de la API de tareas:

| Propiedad de HubSpot | Valor en Make |
|---|---|
| `hs_task_subject` (título) | `action.task_title` |
| `hs_task_body` (notas) | `action.task_body` |
| `hs_timestamp` (fecha de vencimiento) | `action.due_at` |
| `hs_task_priority` | `action.priority` |
| `hs_task_status` | `NOT_STARTED` |
| `hs_task_type` | `CALL` |
| `hubspot_owner_id` | `customer.hubspot_owner_id` |
| Asociación a empresa | `customer.hubspot_company_id` |

`hs_timestamp` es obligatorio y marca el vencimiento de la tarea. Para Alto el payload lo fija 24 horas después de la corrida (configurable en `[alerts.due_hours]`).

### Variante con Make an API Call

Si el módulo nativo no expone algún campo, por ejemplo la asociación, usa HubSpot CRM > Make an API Call:

- Método: `POST`
- URL: `/crm/v3/objects/tasks`
- Cuerpo (los valores entre `<>` se mapean desde el webhook):

```json
{
  "properties": {
    "hs_timestamp": "<action.due_at>",
    "hs_task_subject": "<action.task_title>",
    "hs_task_body": "<action.task_body>",
    "hs_task_priority": "<action.priority>",
    "hs_task_status": "NOT_STARTED",
    "hs_task_type": "CALL",
    "hubspot_owner_id": "<customer.hubspot_owner_id>"
  },
  "associations": [
    {
      "to": { "id": "<customer.hubspot_company_id>" },
      "types": [
        { "associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 192 }
      ]
    }
  ]
}
```

El ID 192 es la asociación tarea a empresa que HubSpot define por defecto (204 es tarea a contacto). Confírmalo en la tabla de IDs de la documentación de asociaciones. En ese módulo, los permisos se eligen en Connection > Custom Scopes. La documentación de la API de tareas pide `crm.objects.contacts.read` y `crm.objects.contacts.write`. Si HubSpot rechaza la asociación con la empresa, añade también los permisos de empresas.

Dos advertencias. La primera: HubSpot publica rutas con fecha (por ejemplo `/crm/objects/2026-03/tasks`) y marca `/crm/v3` como versión heredada. Sigue documentada, y migrar es cambiar la URL. La segunda: el cuerpo debe ser JSON válido, y `action.task_body` tiene saltos de línea que lo rompen si se pegan a mano dentro de un texto JSON. Construye el cuerpo con el módulo JSON > Create JSON, que escapa los valores.

## 7. Ruta 2: Slack > Create a Message

1. Conecta Slack y elige el canal de alertas (por ejemplo `#cs-alertas`).
2. En Text, mapea `action.slack_text`. El texto usa el formato de Slack: los asteriscos ponen la primera parte en negrita.

Para correo en lugar de Slack, usa Gmail o Outlook > Send an email: destinatario `customer.owner_email`, asunto `action.task_title` y contenido `action.task_body`.

## 8. Manejo de errores

1. En Scenario settings, activa Allow storing of incomplete executions.
2. Haz clic derecho sobre el módulo de HubSpot, Add error handler, Break. Si HubSpot falla (permisos, límite de tasa), Make guarda la ejecución incompleta y permite reintentarla en lugar de perder la alerta.

## 9. Activar y probar de punta a punta

1. Activa el escenario. Con un webhook instantáneo se ejecuta apenas llega cada petición.
2. Envía una alerta nueva:

```bash
churn-engine --reset-state --dispatch --max-alerts 1
```

3. Comprueba tres cosas: el historial de ejecuciones del escenario en Make, la tarea en HubSpot (Tareas, asignada al dueño que elegiste, asociada a la empresa) y el mensaje en el canal de Slack.


## Problemas frecuentes

| Síntoma | Causa probable | Qué revisar |
|---|---|---|
| La corrida termina con "Falta la URL del webhook" | `.env` sin `MAKE_WEBHOOK_URL` | Que el archivo esté en la carpeta desde donde ejecutas el comando |
| Código de salida 2 | Make respondió con error o no hubo conexión | `output/<run_id>/run_summary.json`, campo `outcomes[].detail` |
| El escenario se detiene en el filtro | Token distinto o cabeceras no capturadas | Get request headers en Yes, mismo secreto en `.env` y en el filtro, y estructura re-determinada |
| La tarea no tiene empresa asociada | `hubspot_company_id` vacío para ese cliente | La columna del CSV. La ruta 1 solo se ejecuta cuando el campo existe |
| La tarea queda sin asignar | `hubspot_owner_id` vacío | La columna del CSV o el Owner ID obtenido en el paso 1 |
| La segunda corrida no envía nada | Cooldown de 72 horas | Usa `--reset-state` solo para pruebas. En producción es el comportamiento buscado |
| HubSpot responde con error de permisos | La app de Make no está aprobada o faltan scopes | Que un Super Admin apruebe la app y los scopes de la conexión |
