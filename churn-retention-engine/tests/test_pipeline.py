import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from churn_engine.dispatch import DispatchResult
from churn_engine.ingest import SchemaError
from churn_engine.pipeline import run_pipeline
from churn_engine.state import AlertStateStore

from .helpers import AS_OF, MEDIUM, csv_row, default_config, standard_rows, write_csv


class FakeDispatcher:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []

    def send(self, alert):
        self.sent.append(alert)
        return DispatchResult(True, 200, 1) if self.ok else DispatchResult(False, 503, 4, "HTTP 503")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def run_with(self, rows, **kwargs):
        input_path = write_csv(self.tmp / "customers.csv", rows)
        params = dict(input_path=input_path, config=default_config(), output_root=self.tmp / "out", as_of=AS_OF)
        params.update(kwargs)
        return run_pipeline(**params)

    def test_dry_run_exports_files_without_sending(self):
        summary = self.run_with(standard_rows())
        out = Path(summary.output_dir)
        self.assertEqual(summary.mode, "dry-run")
        self.assertEqual((summary.rows_in, summary.rows_valid, summary.rows_rejected), (24, 24, 0))
        self.assertEqual(summary.level_counts, {"Bajo": 20, "Medio": 0, "Alto": 4})
        self.assertEqual(summary.alert_counts["dry_run"], 4)
        for name in ("scored_customers.csv", "alerts.json", "run_summary.json"):
            self.assertTrue((out / name).is_file(), name)
        self.assertFalse((out / "rejected_rows.csv").exists())
        alerts = json.loads((out / "alerts.json").read_text(encoding="utf-8"))["alerts"]
        self.assertEqual([a["customer"]["customer_id"] for a in alerts], ["X-3", "X-2", "X-1", "X-0"])

    def test_dispatch_then_cooldown_suppresses_the_next_run(self):
        fake = FakeDispatcher()
        with AlertStateStore(self.tmp / "state.sqlite3") as store:
            first = self.run_with(standard_rows(), dispatcher=fake, store=store)
            self.assertEqual(first.alert_counts["sent"], 4)
            second = self.run_with(standard_rows(), dispatcher=fake, store=store, as_of=AS_OF + timedelta(hours=1))
        self.assertEqual(second.alert_counts["suppressed"], 4)
        self.assertEqual(second.alert_counts["sent"], 0)
        self.assertEqual(len(fake.sent), 4)

    def test_max_alerts_keeps_the_highest_mrr_at_risk(self):
        fake = FakeDispatcher()
        summary = self.run_with(standard_rows(), dispatcher=fake, max_alerts=2)
        self.assertEqual([a["customer"]["customer_id"] for a in fake.sent], ["X-3", "X-2"])
        self.assertEqual(summary.alert_counts["truncated"], 2)

    def test_min_level_medio_includes_medium_accounts(self):
        rows = standard_rows() + [csv_row("M-0", **MEDIUM)]
        self.assertEqual(self.run_with(rows).candidates, 4)
        self.assertEqual(self.run_with(rows, min_level="Medio").candidates, 5)

    def test_guardrail_blocks_dispatch_and_force_overrides(self):
        rows = standard_rows(n_healthy=10, n_critical=10)
        fake = FakeDispatcher()
        blocked = self.run_with(rows, dispatcher=fake)
        self.assertTrue(blocked.guardrail_tripped)
        self.assertEqual((blocked.alert_counts["blocked"], blocked.alert_counts["sent"]), (10, 0))
        self.assertEqual(fake.sent, [])
        forced = self.run_with(rows, dispatcher=fake, force=True)
        self.assertEqual(forced.alert_counts["sent"], 10)

    def test_failed_dispatch_is_retried_on_the_next_run(self):
        with AlertStateStore(self.tmp / "state.sqlite3") as store:
            failed = self.run_with(standard_rows(), dispatcher=FakeDispatcher(ok=False), store=store)
            self.assertEqual(failed.alert_counts["failed"], 4)
            self.assertIsNone(store.last_sent("X-3"))
            retry = self.run_with(standard_rows(), dispatcher=FakeDispatcher(), store=store,
                                  as_of=AS_OF + timedelta(minutes=5))
        self.assertEqual(retry.alert_counts["sent"], 4)

    def test_circuit_opens_after_consecutive_failures(self):
        fake = FakeDispatcher(ok=False)
        with AlertStateStore(self.tmp / "state.sqlite3") as store:
            summary = self.run_with(standard_rows(n_critical=6), dispatcher=fake, store=store)
            self.assertIsNone(store.last_sent("X-5"))
        self.assertEqual(len(fake.sent), 3)                 # solo 3 intentos reales
        self.assertEqual(summary.alert_counts["failed"], 6)  # las 3 restantes quedan pendientes
        details = [o.detail for o in summary.outcomes if o.status == "failed"]
        self.assertEqual(sum("circuito abierto" in d for d in details), 3)

    def test_success_resets_the_failure_counter(self):
        class Flaky(FakeDispatcher):
            def send(self, alert):
                self.sent.append(alert)
                ok = len(self.sent) % 3 != 0                 # falla cada tercer envío, nunca 3 seguidos
                return DispatchResult(True, 200, 1) if ok else DispatchResult(False, 503, 4, "HTTP 503")
        fake = Flaky()
        summary = self.run_with(standard_rows(n_critical=6), dispatcher=fake)
        self.assertEqual(len(fake.sent), 6)
        self.assertEqual(summary.alert_counts["sent"], 4)
        self.assertEqual(summary.alert_counts["failed"], 2)

    def test_rejected_rows_are_exported(self):
        rows = standard_rows() + [csv_row("H-0"), csv_row("Z-1", tickets=-1)]
        summary = self.run_with(rows)
        self.assertEqual(summary.rows_rejected, 2)
        text = (Path(summary.output_dir) / "rejected_rows.csv").read_text(encoding="utf-8")
        self.assertIn("duplicate_customer_id", text)
        self.assertIn("negative_open_support_tickets", text)

    def test_all_rows_invalid_raises(self):
        with self.assertRaises(SchemaError):
            self.run_with([csv_row("Z-1", tickets=-1)])

    def test_invalid_arguments(self):
        with self.assertRaises(ValueError):
            self.run_with(standard_rows(), min_level="Critico")
        with self.assertRaises(ValueError):
            self.run_with(standard_rows(), max_alerts=0)
        with self.assertRaises(ValueError):
            self.run_with(standard_rows(), max_consecutive_failures=0)

    def test_same_as_of_gives_identical_payloads(self):
        a = self.run_with(standard_rows(), output_root=self.tmp / "a")
        b = self.run_with(standard_rows(), output_root=self.tmp / "b")
        read = lambda s: (Path(s.output_dir) / "alerts.json").read_text(encoding="utf-8")
        self.assertEqual(read(a), read(b))


if __name__ == "__main__":
    unittest.main()
