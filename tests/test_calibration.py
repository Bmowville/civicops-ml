from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from civicops_ml.calibration import (
    calibration_frame,
    probability_logit,
    select_calibrator,
    weighted_absolute_group_gap,
)


class CalibrationTests(unittest.TestCase):
    def test_probability_logit_is_finite_at_boundaries(self) -> None:
        logits = probability_logit(np.array([0.0, 0.5, 1.0]))
        self.assertTrue(np.isfinite(logits).all())
        self.assertEqual(logits[1], 0.0)

    def test_calibration_frame_contains_no_target(self) -> None:
        frame = calibration_frame(
            np.array([0.1, 0.9]),
            pd.Series(["DOT", "HPD"]),
        )
        self.assertEqual(list(frame.columns), ["score_logit", "agency"])

    def test_weighted_group_gap(self) -> None:
        gap = weighted_absolute_group_gap(
            np.array([0, 0, 1, 1]),
            np.array([0.1, 0.1, 0.8, 0.8]),
            pd.Series(["A", "A", "B", "B"]),
            minimum_rows=1,
        )
        self.assertAlmostEqual(gap, 0.15)

    def test_agency_calibrator_requires_both_improvements(self) -> None:
        metrics = {
            "identity": {"brier": 0.101, "weighted_absolute_agency_gap": 0.051},
            "global_platt": {"brier": 0.10, "weighted_absolute_agency_gap": 0.05},
            "agency_aware": {"brier": 0.099, "weighted_absolute_agency_gap": 0.045},
        }
        self.assertEqual(select_calibrator(metrics)["selected"], "agency_aware")
        metrics["agency_aware"]["weighted_absolute_agency_gap"] = 0.050
        self.assertEqual(select_calibrator(metrics)["selected"], "global_platt")

    def test_identity_is_retained_without_material_improvement(self) -> None:
        metrics = {
            "identity": {"brier": 0.10, "weighted_absolute_agency_gap": 0.05},
            "global_platt": {"brier": 0.0998, "weighted_absolute_agency_gap": 0.05},
            "agency_aware": {"brier": 0.0998, "weighted_absolute_agency_gap": 0.04},
        }
        self.assertEqual(select_calibrator(metrics)["selected"], "identity")


if __name__ == "__main__":
    unittest.main()
