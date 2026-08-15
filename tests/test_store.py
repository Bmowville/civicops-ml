from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from civicops_ml.schemas import PredictionRequest, ReviewRequest
from civicops_ml.store import Actor, AuditStore, create_audit_store


class AuditStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "audit.sqlite3"
        self.store = AuditStore(self.path)
        self.actor = Actor(
            subject="00000000-0000-0000-0000-000000000123",
            display_name="Test Operator",
            role="CivicOps.Operator",
        )
        self.request = PredictionRequest(
            created_at=datetime.fromisoformat("2026-04-10T14:30:00-04:00"),
            agency="DOT",
            complaint_type="Street Condition",
            descriptor="Pothole",
            location_type="Street",
            open_data_channel_type="ONLINE",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_prediction_and_review_are_persisted(self) -> None:
        prediction_id = self.store.record_prediction(
            self.request,
            0.72,
            "priority_review",
            "a" * 64,
            self.actor,
        )
        review = self.store.record_review(
            prediction_id,
            ReviewRequest(action="escalate", rationale="Requires earlier attention."),
            self.actor,
        )
        self.assertEqual(review.prediction_id, prediction_id)
        connection = sqlite3.connect(self.path)
        try:
            prediction_count = connection.execute(
                "SELECT count(*) FROM predictions"
            ).fetchone()[0]
            self.assertEqual(prediction_count, 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM reviews").fetchone()[0], 1)
            prediction_actor = connection.execute(
                "SELECT actor_id, actor_name, actor_role FROM predictions"
            ).fetchone()
            self.assertEqual(
                prediction_actor,
                (self.actor.subject, self.actor.display_name, self.actor.role),
            )
        finally:
            connection.close()

    def test_missing_prediction_is_rejected(self) -> None:
        with self.assertRaises(LookupError):
            self.store.record_review(
                "00000000-0000-0000-0000-000000000000",
                ReviewRequest(action="monitor", rationale="Monitor the request."),
                self.actor,
            )

    def test_only_one_review_is_allowed_per_prediction(self) -> None:
        prediction_id = self.store.record_prediction(
            self.request,
            0.2,
            "standard_review",
            "a" * 64,
            self.actor,
        )
        review = ReviewRequest(action="monitor", rationale="Monitor the request.")
        self.store.record_review(prediction_id, review, self.actor)
        with self.assertRaises(ValueError):
            self.store.record_review(prediction_id, review, self.actor)

    def test_audit_summary_reports_counts(self) -> None:
        prediction_id = self.store.record_prediction(
            self.request,
            0.2,
            "standard_review",
            "a" * 64,
            self.actor,
        )
        self.store.record_review(
            prediction_id,
            ReviewRequest(action="monitor", rationale="Monitor the request."),
            self.actor,
        )
        self.assertEqual(
            self.store.audit_summary(),
            {"prediction_count": 1, "review_count": 1},
        )

    def test_monitoring_snapshot_excludes_identity_and_rationale(self) -> None:
        prediction_id = self.store.record_prediction(
            self.request,
            0.72,
            "priority_review",
            "a" * 64,
            self.actor,
        )
        self.store.record_review(
            prediction_id,
            ReviewRequest(action="monitor", rationale="Monitor the request."),
            self.actor,
        )

        self.assertEqual(
            self.store.monitoring_snapshot(),
            [
                {
                    "request": {
                        "agency": "DOT",
                        "complaint_type": "Street Condition",
                        "descriptor": "Pothole",
                        "location_type": "Street",
                        "open_data_channel_type": "ONLINE",
                    },
                    "probability": 0.72,
                    "model_sha256": "a" * 64,
                    "reviewed": True,
                }
            ],
        )


class AuditStoreSelectionTests(unittest.TestCase):
    def test_production_rejects_automatic_migrations(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql://runtime:password@example.test/civicops",
                "CIVICOPS_ENVIRONMENT": "production",
                "CIVICOPS_AUTO_MIGRATE": "true",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "disabled in production"):
                create_audit_store()

    def test_production_opens_runtime_store_without_migrations(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql://runtime:password@example.test/civicops",
                "CIVICOPS_ENVIRONMENT": "production",
                "CIVICOPS_AUTO_MIGRATE": "false",
            },
            clear=True,
        ):
            with patch("civicops_ml.store.PostgresAuditStore") as postgres_store:
                selected = create_audit_store()
        self.assertIs(selected, postgres_store.return_value)
        postgres_store.assert_called_once_with(
            "postgresql://runtime:password@example.test/civicops",
            apply_migrations=False,
        )


if __name__ == "__main__":
    unittest.main()
