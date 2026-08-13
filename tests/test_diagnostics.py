from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from civicops_ml.diagnostics import categorical_drift, population_stability_index


class DriftTests(unittest.TestCase):
    def test_identical_category_distributions_have_zero_drift(self) -> None:
        values = pd.Series(["A", "A", "B", "C"])
        result = categorical_drift(values, values.copy())
        self.assertEqual(result["jensen_shannon_divergence"], 0.0)
        self.assertEqual(result["unseen_category_rate"], 0.0)

    def test_new_category_is_reported(self) -> None:
        result = categorical_drift(
            pd.Series(["A", "A", "B"]),
            pd.Series(["A", "C", "C"]),
        )
        self.assertAlmostEqual(result["unseen_category_rate"], 2 / 3, places=6)

    def test_missing_category_is_normalized(self) -> None:
        result = categorical_drift(
            pd.Series(["A", None]),
            pd.Series(["A", None]),
        )
        self.assertEqual(result["jensen_shannon_divergence"], 0.0)

    def test_identical_scores_have_zero_psi(self) -> None:
        scores = np.linspace(0.01, 0.99, 100)
        self.assertAlmostEqual(population_stability_index(scores, scores.copy()), 0.0)


if __name__ == "__main__":
    unittest.main()
