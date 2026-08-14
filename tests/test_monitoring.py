from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from civicops_ml.monitoring import (
    DEFAULT_THRESHOLDS,
    MONITORED_FEATURES,
    build_monitoring_baseline,
)


class StubModel:
    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        probabilities = np.linspace(0.05, 0.95, len(frame))
        return np.column_stack((1 - probabilities, probabilities))


class MonitoringBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        rows: list[dict[str, object]] = []
        for index in range(20):
            rows.append(
                {
                    "split": "train" if index < 15 else "test",
                    "created_date": f"2025-01-{index + 1:02d}T12:00:00+00:00",
                    "agency": "DOT" if index % 2 == 0 else "HPD",
                    "complaint_type": "Street Condition",
                    "descriptor": "Pothole",
                    "location_type": "Street",
                    "open_data_channel_type": "ONLINE",
                }
            )
        self.frame = pd.DataFrame(rows)

    def test_baseline_uses_only_training_rows_and_normalized_shares(self) -> None:
        baseline = build_monitoring_baseline(
            self.frame,
            StubModel(),
            model_sha256="a" * 64,
            extract_sha256="b" * 64,
        )

        self.assertEqual(baseline["reference"]["rows"], 15)
        self.assertEqual(baseline["model_sha256"], "a" * 64)
        self.assertEqual(set(baseline["features"]), set(MONITORED_FEATURES))
        self.assertAlmostEqual(
            sum(baseline["features"]["agency"]["shares"].values()),
            1.0,
        )
        self.assertAlmostEqual(sum(baseline["scores"]["bin_shares"]), 1.0)
        self.assertEqual(baseline["thresholds"], DEFAULT_THRESHOLDS)
        self.assertNotIn("target", baseline["privacy"]["included"])

    def test_missing_monitored_feature_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "location_type"):
            build_monitoring_baseline(
                self.frame.drop(columns=["location_type"]),
                StubModel(),
                model_sha256="a" * 64,
                extract_sha256="b" * 64,
            )

    def test_empty_training_split_is_rejected(self) -> None:
        frame = self.frame.assign(split="test")
        with self.assertRaisesRegex(ValueError, "non-empty training split"):
            build_monitoring_baseline(
                frame,
                StubModel(),
                model_sha256="a" * 64,
                extract_sha256="b" * 64,
            )


if __name__ == "__main__":
    unittest.main()
