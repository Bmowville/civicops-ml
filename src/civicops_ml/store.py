"""SQLite audit store for predictions and required human reviews."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from .schemas import PredictionRequest, ReviewRequest, ReviewResponse


class AuditStore:
    """Persist model calls and reviewer dispositions without resident identifiers."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS predictions (
                    prediction_id TEXT PRIMARY KEY,
                    recorded_at TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    probability REAL NOT NULL CHECK (probability >= 0 AND probability <= 1),
                    review_tier TEXT NOT NULL,
                    model_sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    review_id TEXT PRIMARY KEY,
                    prediction_id TEXT NOT NULL UNIQUE,
                    recorded_at TEXT NOT NULL,
                    action TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    reviewer_role TEXT NOT NULL,
                    FOREIGN KEY (prediction_id) REFERENCES predictions(prediction_id)
                );
                """
            )

    def record_prediction(
        self,
        request: PredictionRequest,
        probability: float,
        review_tier: str,
        model_sha256: str,
    ) -> str:
        prediction_id = str(uuid4())
        recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = request.model_dump(mode="json")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO predictions (
                    prediction_id, recorded_at, request_json,
                    probability, review_tier, model_sha256
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    prediction_id,
                    recorded_at,
                    json.dumps(payload, sort_keys=True),
                    probability,
                    review_tier,
                    model_sha256,
                ),
            )
        return prediction_id

    def record_review(self, prediction_id: str, review: ReviewRequest) -> ReviewResponse:
        review_id = str(uuid4())
        recorded_at = datetime.now(timezone.utc)
        try:
            with self._connection() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM predictions WHERE prediction_id = ?",
                    (prediction_id,),
                ).fetchone()
                if exists is None:
                    raise LookupError("prediction not found")
                connection.execute(
                    """
                    INSERT INTO reviews (
                        review_id, prediction_id, recorded_at,
                        action, rationale, reviewer_role
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review_id,
                        prediction_id,
                        recorded_at.isoformat(timespec="seconds"),
                        review.action,
                        review.rationale,
                        review.reviewer_role,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("a review is already recorded for this prediction") from exc
        return ReviewResponse(
            review_id=review_id,
            prediction_id=prediction_id,
            action=review.action,
            recorded_at=recorded_at,
        )

    def ready(self) -> bool:
        try:
            with self._connection() as connection:
                connection.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False
