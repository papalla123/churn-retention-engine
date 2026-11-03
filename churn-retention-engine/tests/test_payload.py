import json
import unittest
from datetime import timedelta

from churn_engine.payload import build_alert, build_event_id, explain_drivers

from .helpers import AS_OF, CRITICAL, MEDIUM, csv_row, default_config, score_rows


def alert_for(**kwargs):
    row = score_rows([csv_row("C-1", **kwargs)]).to_dict("records")[0]
    return build_alert(row, default_config(), AS_OF, "RUN-1"), row


class PayloadTests(unittest.TestCase):
    def test_alert_is_strict_json(self):
        alert, _ = alert_for(**CRITICAL, company_id="900", owner_id="800", owner_email="cs@example.com",
                             contract_end="2026-11-12")
        text = json.dumps(alert, allow_nan=False, ensure_ascii=False)
        self.assertEqual(json.loads(text), alert)
        self.assertEqual(alert["customer"]["hubspot_company_id"], "900")
        self.assertEqual(alert["risk"]["risk_level"], "Alto")
        self.assertEqual(alert["risk"]["days_to_renewal"], 27)

    def test_missing_values_become_null(self):
        alert, _ = alert_for(usage_last=0, usage_prev=0)
        json.dumps(alert, allow_nan=False)
        self.assertIsNone(alert["metrics"]["usage_change_pct"])
        self.assertIsNone(alert["customer"]["hubspot_company_id"])
        self.assertIsNone(alert["customer"]["contract_end_date"])
        self.assertIsNone(alert["risk"]["days_to_renewal"])

    def test_event_id_is_deterministic_and_specific(self):
        base = build_event_id("C-1", AS_OF, "Alto")
        self.assertEqual(base, build_event_id("C-1", AS_OF + timedelta(hours=3), "Alto"))
        self.assertNotEqual(base, build_event_id("C-1", AS_OF, "Medio"))
        self.assertNotEqual(base, build_event_id("C-2", AS_OF, "Alto"))
        self.assertNotEqual(base, build_event_id("C-1", AS_OF + timedelta(days=1), "Alto"))

    def test_drivers_are_sorted_by_weighted_contribution(self):
        _, row = alert_for(**CRITICAL)
        self.assertEqual(
            explain_drivers(row, default_config()),
            [
                "Sin iniciar sesión hace 41 días",
                "Uso -85% frente a los 30 días previos",
                "4 tickets de soporte abiertos",
            ],
        )

    def test_drivers_fall_back_to_the_top_signal(self):
        # Ninguna señal llega a 50 puntos, pero el score es Medio: debe haber al menos una causa.
        alert, _ = alert_for(days_ago=25, tickets=2, usage_last=650, usage_prev=1000)
        self.assertEqual(alert["risk"]["risk_level"], "Medio")
        self.assertEqual(alert["risk"]["drivers"], ["Sin iniciar sesión hace 25 días"])

    def test_priority_due_date_and_title_follow_the_level(self):
        high, _ = alert_for(**CRITICAL)
        self.assertEqual(high["action"]["priority"], "HIGH")
        self.assertEqual(high["action"]["due_at"], "2026-10-17T12:00:00.000Z")
        self.assertEqual(high["action"]["task_title"], "[Churn Alto] Empresa Demo SAC: contactar en 24 h")
        medium, _ = alert_for(**MEDIUM)
        self.assertEqual(medium["action"]["priority"], "MEDIUM")
        self.assertEqual(medium["action"]["due_at"], "2026-10-19T12:00:00.000Z")

    def test_task_body_mentions_renewal(self):
        upcoming, _ = alert_for(**CRITICAL, contract_end="2026-11-12")
        self.assertIn("Renovación: 2026-11-12 (en 27 días).", upcoming["action"]["task_body"])
        overdue, _ = alert_for(**CRITICAL, contract_end="2026-10-10")
        self.assertIn("Contrato vencido el 2026-10-10 (hace 6 días).", overdue["action"]["task_body"])

    def test_slack_text_includes_owner_or_placeholder(self):
        with_owner, _ = alert_for(**CRITICAL, owner_email="ana.cs@example.com")
        self.assertIn("Responsable: ana.cs@example.com", with_owner["action"]["slack_text"])
        without_owner, _ = alert_for(**CRITICAL)
        self.assertIn("Responsable: sin asignar", without_owner["action"]["slack_text"])


if __name__ == "__main__":
    unittest.main()
