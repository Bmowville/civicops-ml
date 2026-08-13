from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from civicops_ml.audit import AuditConfig, sample_windows, summarize  # noqa: E402


class AuditTests(unittest.TestCase):
    def test_monthly_windows_cover_four_day_parts(self) -> None:
        config = AuditConfig(
            start=datetime(2025, 1, 1),
            end=datetime(2025, 3, 1),
            as_of=datetime(2025, 4, 1),
        )
        windows = sample_windows(config)
        self.assertEqual(len(windows), 8)
        self.assertEqual({start.hour for start, _ in windows}, {0, 6, 12, 18})

    def test_summary_counts_labels_and_missing_values(self) -> None:
        rows = [
            {
                "unique_key": "1",
                "created_date": "2025-01-01T00:00:00.000",
                "closed_date": "2025-01-02T00:00:00.000",
                "agency": "DOT",
                "complaint_type": "Street Condition",
                "borough": "MANHATTAN",
                "open_data_channel_type": "ONLINE",
                "community_board": "01 MANHATTAN",
            },
            {
                "unique_key": "2",
                "created_date": "2025-01-01T00:00:00.000",
                "agency": "DOT",
                "complaint_type": "Street Condition",
                "borough": "MANHATTAN",
                "open_data_channel_type": "PHONE",
                "community_board": "01 MANHATTAN",
            },
        ]
        result = summarize(rows, datetime(2025, 2, 1))
        self.assertEqual(result["sample_rows"], 2)
        self.assertEqual(result["target_by_year"]["2025"]["positive"], 1)
        self.assertEqual(result["feature_missing_rates"]["descriptor"], 1.0)
        self.assertEqual(result["duplicate_keys"], 0)


if __name__ == "__main__":
    unittest.main()
