from __future__ import annotations

import unittest
from datetime import datetime

from civicops_ml.dataset import ExtractConfig, _prepare_row, rotating_weekly_windows


class WindowSamplingTests(unittest.TestCase):
    def test_first_cycle_covers_every_weekday_daypart_pair(self) -> None:
        config = ExtractConfig(
            start=datetime(2024, 1, 1),
            end=datetime(2024, 4, 8),
        )
        windows = rotating_weekly_windows(config)[:42]
        strata = {(start.weekday(), start.hour // 4) for start, _ in windows}
        self.assertEqual(len(windows), 42)
        self.assertEqual(len(strata), 42)

    def test_schedule_is_reproducible(self) -> None:
        config = ExtractConfig(
            start=datetime(2024, 1, 1),
            end=datetime(2024, 6, 1),
        )
        self.assertEqual(rotating_weekly_windows(config), rotating_weekly_windows(config))

    def test_seed_changes_the_schedule(self) -> None:
        common = {
            "start": datetime(2024, 1, 1),
            "end": datetime(2024, 6, 1),
        }
        first = rotating_weekly_windows(ExtractConfig(**common, sampling_seed=1))
        second = rotating_weekly_windows(ExtractConfig(**common, sampling_seed=2))
        self.assertNotEqual(first, second)

    def test_windows_do_not_cross_cohort_end(self) -> None:
        config = ExtractConfig(
            start=datetime(2026, 6, 20),
            end=datetime(2026, 7, 1),
        )
        self.assertTrue(all(end <= config.end for _, end in rotating_weekly_windows(config)))


class RowPreparationTests(unittest.TestCase):
    def test_prepared_row_contains_safe_features_and_target(self) -> None:
        row = {
            "unique_key": "123",
            "created_date": "2025-08-02T14:30:00.000",
            "closed_date": "2025-08-12T14:30:00.000",
            "agency": "DOT",
            "complaint_type": "Street Condition",
            "borough": "BROOKLYN",
        }
        prepared = _prepare_row(row, datetime(2026, 8, 13))
        self.assertIsNotNone(prepared)
        assert prepared is not None
        self.assertEqual(prepared["target"], 1)
        self.assertEqual(prepared["split"], "validation")
        self.assertEqual(prepared["created_hour"], "14")
        self.assertEqual(prepared["created_is_weekend"], "1")
        self.assertEqual(prepared["descriptor"], "Unknown")
        self.assertNotIn("unique_key", prepared)
        self.assertNotIn("closed_date", prepared)

    def test_invalid_negative_duration_is_rejected(self) -> None:
        row = {
            "unique_key": "123",
            "created_date": "2025-08-02T14:30:00.000",
            "closed_date": "2025-08-01T14:30:00.000",
        }
        self.assertIsNone(_prepare_row(row, datetime(2026, 8, 13)))


if __name__ == "__main__":
    unittest.main()
