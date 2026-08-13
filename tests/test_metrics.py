from __future__ import annotations

import unittest

import numpy as np

from civicops_ml.metrics import (
    expected_calibration_error,
    threshold_metrics,
    top_fraction_metrics,
)


class MetricTests(unittest.TestCase):
    def test_perfect_calibration_has_zero_error(self) -> None:
        target = np.array([0, 0, 1, 1])
        probabilities = np.array([0.0, 0.0, 1.0, 1.0])
        self.assertEqual(expected_calibration_error(target, probabilities), 0.0)

    def test_top_fraction_uses_exact_capacity_with_ties(self) -> None:
        target = np.array([1, 0, 1, 0, 0, 0, 0, 0, 0, 0])
        probabilities = np.full(10, 0.2)
        metrics = top_fraction_metrics(target, probabilities, fraction=0.2)
        self.assertEqual(metrics["reviewed"], 2)

    def test_threshold_metrics_reports_actual_review_rate(self) -> None:
        target = np.array([1, 0, 1, 0])
        probabilities = np.array([0.9, 0.8, 0.3, 0.1])
        metrics = threshold_metrics(target, probabilities, threshold=0.5)
        self.assertEqual(metrics["reviewed"], 2)
        self.assertEqual(metrics["precision"], 0.5)
        self.assertEqual(metrics["recall"], 0.5)


if __name__ == "__main__":
    unittest.main()
