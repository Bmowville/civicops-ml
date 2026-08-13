from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from civicops_ml.modeling import HistoricalRateBaseline, select_candidate


class HistoricalRateBaselineTests(unittest.TestCase):
    def test_unseen_pair_falls_back_to_agency_then_global(self) -> None:
        train = pd.DataFrame(
            {
                "agency": ["DOT", "DOT", "HPD", "HPD"],
                "complaint_type": ["Street", "Street", "Heat", "Heat"],
            }
        )
        target = np.array([0, 0, 1, 1])
        model = HistoricalRateBaseline(pair_strength=1, agency_strength=1).fit(train, target)
        score = model.predict_proba(
            pd.DataFrame(
                {
                    "agency": ["DOT", "UNKNOWN"],
                    "complaint_type": ["New", "New"],
                }
            )
        )
        self.assertLess(score[0], model.global_rate_)
        self.assertEqual(score[1], model.global_rate_)


class CandidateSelectionTests(unittest.TestCase):
    def test_equivalent_no_geography_model_is_preferred(self) -> None:
        target = np.array([0, 0, 1, 1])
        predictions = {
            "logistic_full": np.array([0.1, 0.2, 0.8, 0.9]),
            "logistic_no_geography": np.array([0.1, 0.2, 0.8, 0.9]),
        }
        self.assertEqual(
            select_candidate(target, predictions)["selected"],
            "logistic_no_geography",
        )

    def test_materially_weaker_no_geography_model_is_not_preferred(self) -> None:
        target = np.array([0, 0, 1, 1, 0, 1])
        predictions = {
            "logistic_full": np.array([0.05, 0.1, 0.8, 0.9, 0.2, 0.7]),
            "logistic_no_geography": np.full(6, 0.5),
        }
        self.assertEqual(select_candidate(target, predictions)["selected"], "logistic_full")


if __name__ == "__main__":
    unittest.main()
