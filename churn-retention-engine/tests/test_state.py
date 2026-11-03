import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from churn_engine.state import AlertStateStore

from .helpers import AS_OF

KW = dict(cooldown_hours=72, escalation_delta=10)


class StateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = AlertStateStore(Path(tmp.name) / "state" / "alerts.sqlite3")
        self.addCleanup(self.store.close)

    def sent(self, customer="C-1", level="Alto", score=80.0, status="SENT", at=AS_OF):
        self.store.record(event_id="e", customer_id=customer, risk_level=level, churn_score=score,
                          status=status, attempts=1, error=None, at=at)

    def decide(self, customer="C-1", level="Alto", score=80.0, hours=0):
        return self.store.decide(customer_id=customer, risk_level=level, churn_score=score,
                                 as_of=AS_OF + timedelta(hours=hours), **KW)

    def test_first_alert_is_allowed(self):
        self.assertEqual(self.decide(), (True, "first_alert"))

    def test_cooldown_suppresses_repeats(self):
        self.sent()
        self.assertEqual(self.decide(score=85, hours=10), (False, "cooldown_active"))

    def test_score_increase_bypasses_cooldown(self):
        self.sent()
        self.assertEqual(self.decide(score=90, hours=10), (True, "score_increased"))

    def test_level_escalation_bypasses_cooldown(self):
        self.sent(level="Medio", score=45.0)
        self.assertEqual(self.decide(level="Alto", score=72, hours=5), (True, "level_escalated"))

    def test_cooldown_expires(self):
        self.sent()
        self.assertEqual(self.decide(score=80, hours=71), (False, "cooldown_active"))
        self.assertEqual(self.decide(score=80, hours=72), (True, "cooldown_elapsed"))

    def test_failed_sends_do_not_count(self):
        self.sent(status="FAILED")
        self.assertEqual(self.decide(hours=1), (True, "first_alert"))

    def test_customers_are_independent(self):
        self.sent(customer="C-1")
        self.assertEqual(self.decide(customer="C-2", hours=1), (True, "first_alert"))


if __name__ == "__main__":
    unittest.main()
