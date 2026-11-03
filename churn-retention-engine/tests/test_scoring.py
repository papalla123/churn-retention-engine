import copy
import unittest

import pandas as pd

from churn_engine.config import Ramp, ScoringConfig
from churn_engine.scoring import classify, ramp, WeightedRuleScorer

from .config_data import RAW
from .helpers import CRITICAL, MEDIUM, csv_row, default_config, score_rows


class RampTests(unittest.TestCase):
    def test_boundaries_and_midpoint(self):
        result = ramp(pd.Series([0, 7, 26, 45, 90]), Ramp(7, 45))
        self.assertEqual([round(v, 6) for v in result], [0.0, 0.0, 50.0, 100.0, 100.0])


class ClassifyTests(unittest.TestCase):
    def test_thresholds_are_inclusive(self):
        levels = classify(pd.Series([69.9, 70.0, 40.0, 39.9, 100.0]), default_config())
        self.assertEqual(list(levels), ["Medio", "Alto", "Medio", "Bajo", "Alto"])


class ScorerTests(unittest.TestCase):
    def test_critical_customer(self):
        row = score_rows([csv_row("C-1", **CRITICAL)]).iloc[0]
        self.assertEqual(row["churn_score"], 90.8)
        self.assertEqual(row["risk_level"], "Alto")
        self.assertAlmostEqual(row["mrr_at_risk_usd"], 7627.2)

    def test_healthy_customer(self):
        row = score_rows([csv_row("C-1")]).iloc[0]
        self.assertEqual((row["churn_score"], row["risk_level"]), (0.0, "Bajo"))

    def test_medium_customer(self):
        row = score_rows([csv_row("C-1", **MEDIUM)]).iloc[0]
        self.assertEqual(row["churn_score"], 46.4)
        self.assertEqual(row["risk_level"], "Medio")

    def test_one_weak_signal_does_not_raise_an_alert(self):
        row = score_rows([csv_row("C-1", tickets=5)]).iloc[0]
        self.assertEqual(row["churn_score"], 25.0)
        self.assertEqual(row["risk_level"], "Bajo")

    def test_missing_usage_baseline_is_neutral(self):
        row = score_rows([csv_row("C-1", usage_last=0, usage_prev=0)]).iloc[0]
        self.assertEqual(row["c_usage_drop"], 0.0)
        self.assertFalse(pd.isna(row["churn_score"]))

    def test_usage_growth_is_not_penalized(self):
        row = score_rows([csv_row("C-1", usage_last=3000, usage_prev=1000)]).iloc[0]
        self.assertEqual(row["c_usage_drop"], 0.0)

    def test_level_follows_the_rounded_score(self):
        # El score sin redondear es 90.789; con alto_min = 90.8 el nivel debe coincidir con lo que se muestra.
        raw = copy.deepcopy(RAW)
        raw["risk_levels"]["alto_min"] = 90.8
        features_row = score_rows([csv_row("C-1", **CRITICAL)])
        rescored = WeightedRuleScorer(ScoringConfig.from_dict(raw)).score(features_row)
        self.assertEqual(rescored.iloc[0]["churn_score"], 90.8)
        self.assertEqual(rescored.iloc[0]["risk_level"], "Alto")


if __name__ == "__main__":
    unittest.main()
