# Guion de demostración: 30 segundos, pantalla dividida

Mitad izquierda: terminal. Mitad derecha: navegador con las pestañas de Make (historial del escenario), HubSpot (Tareas) y Slack. Resolución 1920×1080, dos mitades de 960 px.

## Antes de grabar

1. Datos nuevos con tus IDs de HubSpot, para que las fechas queden relativas a hoy:

```bash
python scripts/generate_sample_data.py
```

2. Terminal con fuente grande (18 a 20 pt), fondo oscuro y colores activos. Ejecuta `clear`.
3. HubSpot: lista de Tareas ordenada por fecha de vencimiento, con el filtro del responsable aplicado. Debe verse que aún no existe la tarea de Logística Andina.
4. Make: el escenario activo, abierto en el historial de ejecuciones.
5. No muestres `.env` ni la URL del webhook en pantalla. La ruta de esa URL funciona como contraseña.
6. Haz una toma de prueba con el comando de abajo. Como incluye `--reset-state`, puedes repetirla las veces que quieras sin que el cooldown suprima la alerta. Después de cada prueba, borra en HubSpot la tarea creada.

Comando de la toma:

```bash
churn-engine --reset-state --dispatch --max-alerts 1 --log-level ERROR
```

`--max-alerts 1` envía solo la cuenta de mayor MRR en riesgo (C-1001), y `--log-level ERROR` deja en pantalla únicamente la tabla.

## Guion por tiempos

| Tiempo | Izquierda (terminal) | Derecha (navegador) | Voz | Texto en pantalla |
|---|---|---|---|---|
| 0 a 5 s | Prompt vacío | Lista de Tareas de HubSpot, sin la tarea de churn | "Esta cuenta paga ocho mil cuatrocientos dólares al mes y lleva cuarenta y un días sin entrar." | Logística Andina SAC, USD 8,400 al mes, 41 días sin login |
| 5 a 13 s | Escribes y ejecutas el comando. Aparece la tabla con C-1001, 90.8, Alto, enviada | Sin cambios | "Ejecuto el script: cuarenta cuentas puntuadas, cinco en riesgo alto. Esta saca noventa coma ocho sobre cien." | Score 90.8 / 100: Alto |
| 13 a 18 s | La tabla queda a la vista | Historial de Make: ejecución nueva con las burbujas en webhook, router y HubSpot | "El payload sale por webhook y Make lo enruta." | Webhook, Make |
| 18 a 27 s | La tabla queda a la vista | Tareas de HubSpot: aparece "[Churn Alto] Logística Andina SAC: contactar en 24 h", prioridad alta. Abre el detalle un instante para mostrar las señales en las notas | "En HubSpot aparece una tarea de prioridad alta para su responsable de cuenta, con las señales y un plazo de veinticuatro horas." | Tarea HIGH, vence en 24 h |
| 27 a 30 s | Sin cambios | Mensaje de Slack en el canal de alertas, o la tarea abierta | "Nadie abrió una hoja de cálculo." | Del dato a la tarea, sin pasos manuales |

La voz suma unas 70 palabras, lo que cabe en 30 segundos a ritmo normal. Si en tu toma pasa de 30 s, quita la frase del paso de Make (13 a 18 s) y deja solo el efecto visual.

## Detalles que ayudan

- Cronometra el tiempo real entre pulsar Enter y ver la tarea en HubSpot. Si es de pocos segundos, ponlo como texto en pantalla en lugar de una frase genérica. Usa el número que midas.
- Graba las dos mitades en una sola captura, o graba por separado y sincroniza por el momento del Enter. Evita acelerar el video: se nota.
- Deja la tarea abierta en los últimos segundos para que se lea el cuerpo (score, MRR en riesgo, señales, renovación y acción sugerida).
- Antes de repetir una toma, borra en HubSpot la tarea de la toma anterior para que la lista vuelva a verse sin ella. El estado local ya se reinicia con `--reset-state`.
