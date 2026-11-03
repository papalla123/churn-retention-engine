import copy
import unittest

from churn_engine.config import ConfigError, Ramp, ScoringConfig

from .helpers import ROOT, default_config

BASE = {
    "weights": {"login_inactivity": 0.40, "support_pressure": 0.25, "usage_drop": 0.35},
    "ramps": {
        "login_inactivity": {"low": 7, "high": 45},
        "support_pressure": {"low": 0, "high": 5},
        "usage_drop": {"low": 10, "high": 70},
    },
    "risk_levels": {"alto_min": 70, "medio_min": 40},
}


def variant(mutate) -> dict:
    raw = copy.deepcopy(BASE)
    mutate(raw)
    return raw


class ConfigTests(unittest.TestCase):
    def test_default_file_loads(self):
        cfg = default_config()
        self.assertAlmostEqual(sum(cfg.weights.values()), 1.0)
        self.assertEqual((cfg.alto_min, cfg.medio_min), (70, 40))
        self.assertEqual(cfg.due_hours["Alto"], 24)

    def test_weights_must_sum_to_one(self):
        with self.assertRaisesRegex(ConfigError, "sumar 1.0"):
            ScoringConfig.from_dict(variant(lambda r: r["weights"].update(usage_drop=0.50)))

    def test_negative_weight_is_rejected(self):
        def mutate(r):
            r["weights"].update(login_inactivity=-0.10, usage_drop=0.85)
        with self.assertRaisesRegex(ConfigError, "negativos"):
            ScoringConfig.from_dict(variant(mutate))

    def test_ramp_requires_high_above_low(self):
        with self.assertRaises(ConfigError):
            Ramp(5, 5)
        with self.assertRaises(ConfigError):
            ScoringConfig.from_dict(variant(lambda r: r["ramps"]["usage_drop"].update(high=10)))

    def test_thresholds_must_be_ordered(self):
        with self.assertRaises(ConfigError):
            ScoringConfig.from_dict(variant(lambda r: r["risk_levels"].update(medio_min=80)))

    def test_unknown_signal_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "desconocidas"):
            ScoringConfig.from_dict(variant(lambda r: r["weights"].update(nps=0.1)))

    def test_missing_section_and_missing_file(self):
        with self.assertRaises(ConfigError):
            ScoringConfig.from_dict({"weights": BASE["weights"]})
        with self.assertRaises(ConfigError):
            ScoringConfig.from_toml(ROOT / "config" / "no_existe.toml")

    def test_non_numeric_value(self):
        with self.assertRaises(ConfigError):
            ScoringConfig.from_dict(variant(lambda r: r["risk_levels"].update(alto_min="mucho")))


if __name__ == "__main__":
    unittest.main()
