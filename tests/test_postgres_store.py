from __future__ import annotations

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from hashlib import sha256
from uuid import uuid4

from civicops_ml.rate_limit import PostgresSlidingWindowRateLimiter
from civicops_ml.schemas import PredictionRequest, ReviewRequest
from civicops_ml.store import Actor, PostgresAuditStore


@unittest.skipUnless(
    os.environ.get("CIVICOPS_TEST_DATABASE_URL"),
    "CIVICOPS_TEST_DATABASE_URL is required for the PostgreSQL integration test",
)
class PostgresAuditStoreIntegrationTests(unittest.TestCase):
    def test_shared_limits_are_atomic_across_instances_and_expire(self) -> None:
        store = PostgresAuditStore(os.environ["CIVICOPS_TEST_DATABASE_URL"])
        other_store = PostgresAuditStore(
            os.environ["CIVICOPS_TEST_DATABASE_URL"], apply_migrations=False
        )
        key = "integration-limit-" + uuid4().hex
        key_hash = sha256(key.encode()).hexdigest()
        limiters = [
            PostgresSlidingWindowRateLimiter(store.pool),
            PostgresSlidingWindowRateLimiter(other_store.pool),
        ]
        try:
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(
                    executor.map(
                        lambda index: limiters[index % 2].check(
                            key, limit=3, window_seconds=60
                        ),
                        range(12),
                    )
                )
            self.assertEqual(sum(allowed for allowed, _ in results), 3)
            self.assertTrue(
                all(retry >= 1 for allowed, retry in results if not allowed)
            )
            with store.pool.connection() as connection:
                count = connection.execute(
                    "SELECT cardinality(event_times) FROM civicops.rate_limits WHERE key_sha256=%s",
                    (key_hash,),
                ).fetchone()[0]
                self.assertEqual(count, 3)
                connection.execute(
                    "UPDATE civicops.rate_limits SET event_times=ARRAY[EXTRACT(EPOCH FROM clock_timestamp())::float8 - 61] WHERE key_sha256=%s",
                    (key_hash,),
                )
            self.assertTrue(limiters[1].check(key, limit=3, window_seconds=60)[0])
            with store.pool.connection() as connection:
                connection.execute(
                    "UPDATE civicops.rate_limits SET expires_at=CURRENT_TIMESTAMP - INTERVAL '2 days' WHERE key_sha256=%s",
                    (key_hash,),
                )
            limiters[0].cleanup()
            with store.pool.connection() as connection:
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM civicops.rate_limits WHERE key_sha256=%s",
                        (key_hash,),
                    ).fetchone()
                )
        finally:
            with store.pool.connection() as connection:
                connection.execute(
                    "DELETE FROM civicops.rate_limits WHERE key_sha256=%s", (key_hash,)
                )
            other_store.close()
            store.close()

    def test_migrations_and_audit_round_trip(self) -> None:
        store = PostgresAuditStore(
            os.environ["CIVICOPS_TEST_DATABASE_URL"],
            apply_migrations=True,
        )
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
            snapshot = store.monitoring_snapshot()
            matching = [
                record
                for record in snapshot
                if record["request"]["agency"] == "DOT"
                and record["model_sha256"] == "a" * 64
            ]
            self.assertTrue(matching)
            self.assertTrue(matching[-1]["reviewed"])
            self.assertNotIn("actor_id", matching[-1])
            self.assertNotIn("rationale", matching[-1])
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
