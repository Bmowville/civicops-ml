from __future__ import annotations

import unittest
from datetime import datetime

from pydantic import ValidationError

from civicops_ml.schemas import PredictionRequest, ReviewRequest


class PredictionRequestTests(unittest.TestCase):
    def _request(self, created_at: datetime) -> PredictionRequest:
        return PredictionRequest(
            created_at=created_at,
            agency=" DOT ",
            complaint_type="Street   Condition",
            descriptor="Pothole",
            location_type="Street",
            open_data_channel_type="ONLINE",
        )

    def test_timestamp_requires_timezone(self) -> None:
        with self.assertRaises(ValidationError):
            self._request(datetime(2026, 4, 10, 14, 30))

    def test_model_features_use_new_york_local_time(self) -> None:
        request = self._request(datetime.fromisoformat("2026-04-11T02:30:00+00:00"))
        features = request.model_features()
        self.assertEqual(features["agency"], "DOT")
        self.assertEqual(features["complaint_type"], "Street Condition")
        self.assertEqual(features["created_hour"], "22")
        self.assertEqual(features["created_day_of_week"], "4")
        self.assertEqual(features["created_is_weekend"], "0")

    def test_extra_fields_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            PredictionRequest.model_validate(
                {
                    **self._request(
                        datetime.fromisoformat("2026-04-10T14:30:00-04:00")
                    ).model_dump(),
                    "status": "Closed",
                }
            )

    def test_null_or_non_string_required_category_is_rejected(self) -> None:
        payload = self._request(
            datetime.fromisoformat("2026-04-10T14:30:00-04:00")
        ).model_dump()
        for invalid in (None, 311):
            payload["agency"] = invalid
            with self.subTest(value=invalid), self.assertRaises(ValidationError):
                PredictionRequest.model_validate(payload)

    def test_timestamp_before_supported_cohort_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._request(datetime.fromisoformat("2023-12-31T12:00:00-05:00"))

    def test_whitespace_only_review_rationale_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ReviewRequest(action="monitor", rationale="     ")


if __name__ == "__main__":
    unittest.main()
