from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from civicops_ml.monitoring import (
    DEFAULT_THRESHOLDS,
    MONITORED_FEATURES,
    build_monitoring_baseline,
    build_monitoring_report,
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

    def _baseline(self) -> dict[str, object]:
        return build_monitoring_baseline(
            self.frame,
            StubModel(),
            model_sha256="a" * 64,
            extract_sha256="b" * 64,
        )

    @staticmethod
    def _record(
        *,
        probability: float = 0.5,
        reviewed: bool = True,
        model_sha256: str = "a" * 64,
        category: str = "DOT",
    ) -> dict[str, object]:
        return {
            "request": {
                "agency": category,
                "complaint_type": "Street Condition",
                "descriptor": "Pothole",
                "location_type": "Street",
                "open_data_channel_type": "ONLINE",
            },
            "probability": probability,
            "model_sha256": model_sha256,
            "reviewed": reviewed,
        }

    def test_small_production_sample_suppresses_drift_conclusions(self) -> None:
        report = build_monitoring_report(self._baseline(), [self._record()])

        self.assertEqual(report["overall_status"], "insufficient_data")
        self.assertEqual(report["checks"]["sample_size"]["observed_predictions"], 1)
        self.assertIsNone(report["checks"]["score_drift"]["psi"])
        self.assertEqual(report["privacy"]["output"], "aggregate metrics only")

    def test_model_hash_mismatch_is_critical_even_below_sample_minimum(self) -> None:
        report = build_monitoring_report(
            self._baseline(),
            [self._record(model_sha256="c" * 64)],
        )

        self.assertEqual(report["overall_status"], "critical")
        self.assertEqual(
            report["checks"]["model_integrity"]["mismatch_count"],
            1,
        )

    def test_large_shift_and_incomplete_reviews_are_reported(self) -> None:
        records = [
            self._record(probability=0.99, reviewed=False, category="NEW_AGENCY")
            for _ in range(DEFAULT_THRESHOLDS["minimum_predictions"])
        ]
        report = build_monitoring_report(self._baseline(), records)

        self.assertEqual(report["overall_status"], "critical")
        self.assertEqual(
            report["checks"]["review_completion"]["status"],
            "critical",
        )
        self.assertEqual(
            report["checks"]["feature_drift"]["agency"][
                "unseen_category_rate"
            ],
            1.0,
        )
        self.assertIsNotNone(report["checks"]["score_drift"]["psi"])


if __name__ == "__main__":
    unittest.main()
