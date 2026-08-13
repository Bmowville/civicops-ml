from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from civicops_ml.contract import (  # noqa: E402
    DERIVED_CREATION_FIELDS,
    LABEL_ONLY_FIELDS,
    LEAKAGE_FIELDS,
    MODEL_SOURCE_FIELDS,
    ContractError,
    assert_no_feature_leakage,
    is_label_mature,
    slow_resolution_label,
)


class TargetContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.created = datetime(2025, 1, 1, 9, 30)
        self.as_of = datetime(2025, 2, 1)

    def test_closure_within_seven_days_is_negative(self) -> None:
        closed = self.created + timedelta(days=6, hours=23)
        self.assertEqual(slow_resolution_label(self.created, closed, self.as_of), 0)

    def test_closure_at_seven_days_is_negative(self) -> None:
        closed = self.created + timedelta(days=7)
        self.assertEqual(slow_resolution_label(self.created, closed, self.as_of), 0)

    def test_closure_after_seven_days_is_positive(self) -> None:
        closed = self.created + timedelta(days=7, seconds=1)
        self.assertEqual(slow_resolution_label(self.created, closed, self.as_of), 1)

    def test_open_mature_request_is_positive(self) -> None:
        self.assertEqual(slow_resolution_label(self.created, None, self.as_of), 1)

    def test_immature_request_is_rejected(self) -> None:
        with self.assertRaisesRegex(ContractError, "target horizon"):
            slow_resolution_label(self.created, None, self.created + timedelta(days=6))

    def test_negative_duration_is_rejected(self) -> None:
        with self.assertRaisesRegex(ContractError, "before created_at"):
            slow_resolution_label(
                self.created,
                self.created - timedelta(seconds=1),
                self.as_of,
            )

    def test_future_closure_is_rejected(self) -> None:
        with self.assertRaisesRegex(ContractError, "after the extraction"):
            slow_resolution_label(
                self.created,
                self.as_of + timedelta(seconds=1),
                self.as_of,
            )

    def test_maturity_boundary(self) -> None:
        self.assertFalse(is_label_mature(self.created, self.created + timedelta(days=6)))
        self.assertTrue(is_label_mature(self.created, self.created + timedelta(days=7)))


class FeatureContractTests(unittest.TestCase):
    def test_model_features_exclude_future_fields(self) -> None:
        features = set(MODEL_SOURCE_FIELDS) | set(DERIVED_CREATION_FIELDS)
        prohibited = set(LEAKAGE_FIELDS) | set(LABEL_ONLY_FIELDS)
        self.assertFalse(features & prohibited)
        assert_no_feature_leakage()

    def test_model_features_exclude_exact_address(self) -> None:
        self.assertNotIn("incident_address", MODEL_SOURCE_FIELDS)
        self.assertNotIn("latitude", MODEL_SOURCE_FIELDS)
        self.assertNotIn("longitude", MODEL_SOURCE_FIELDS)


if __name__ == "__main__":
    unittest.main()
