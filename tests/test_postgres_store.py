from __future__ import annotations

import os
import unittest
from datetime import datetime

from civicops_ml.schemas import PredictionRequest, ReviewRequest
from civicops_ml.store import Actor, PostgresAuditStore


@unittest.skipUnless(
    os.environ.get("CIVICOPS_TEST_DATABASE_URL"),
    "CIVICOPS_TEST_DATABASE_URL is required for the PostgreSQL integration test",
)
class PostgresAuditStoreIntegrationTests(unittest.TestCase):
    def test_migrations_and_audit_round_trip(self) -> None:
        store = PostgresAuditStore(os.environ["CIVICOPS_TEST_DATABASE_URL"])
        prediction_id: str | None = None
        actor = Actor(
            subject="integration-test-actor",
            display_name="Integration Test",
            role="CivicOps.Operator",
        )
        request = PredictionRequest(
            created_at=datetime.fromisoformat("2026-04-10T14:30:00-04:00"),
            agency="DOT",
            complaint_type="Street Condition",
            descriptor="Pothole",
            location_type="Street",
            open_data_channel_type="ONLINE",
        )
        try:
            self.assertTrue(store.ready())
            prediction_id = store.record_prediction(
                request,
                0.72,
                "priority_review",
                "a" * 64,
                actor,
            )
            response = store.record_review(
                prediction_id,
                ReviewRequest(
                    action="monitor",
                    rationale="Integration test audit round trip.",
                ),
                actor,
            )
            self.assertEqual(response.prediction_id, prediction_id)
            with store.pool.connection() as connection:
                stored_actor = connection.execute(
                    """
                    SELECT actor_id, actor_name, actor_role
                    FROM civicops.predictions
                    WHERE prediction_id = %s
                    """,
                    (prediction_id,),
                ).fetchone()
                migration_count = connection.execute(
                    "SELECT count(*) FROM civicops.schema_migrations"
                ).fetchone()[0]
            self.assertEqual(
                stored_actor,
                (actor.subject, actor.display_name, actor.role),
            )
            self.assertGreaterEqual(migration_count, 1)
        finally:
            if prediction_id is not None:
                with store.pool.connection() as connection:
                    connection.execute(
                        "DELETE FROM civicops.reviews WHERE prediction_id = %s",
                        (prediction_id,),
                    )
                    connection.execute(
                        "DELETE FROM civicops.predictions WHERE prediction_id = %s",
                        (prediction_id,),
                    )
            store.close()


if __name__ == "__main__":
    unittest.main()
