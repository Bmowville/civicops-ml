"""Audited SQLite and PostgreSQL persistence for CivicOps ML."""

from __future__ import annotations

import json
import os
import sqlite3
from hashlib import sha256
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterator, Protocol
from uuid import uuid4

import psycopg
from psycopg.errors import ForeignKeyViolation, UniqueViolation
from psycopg_pool import NullConnectionPool, PoolTimeout

from .schemas import PredictionRequest, ReviewRequest, ReviewResponse


@dataclass(frozen=True)
class Actor:
    """Authenticated application actor written to every audit event."""

    subject: str
    display_name: str
    role: str


class AuditStoreProtocol(Protocol):
    def record_prediction(
        self,
        request: PredictionRequest,
        probability: float,
        review_tier: str,
        model_sha256: str,
        actor: Actor,
    ) -> str: ...

    def record_review(
        self,
        prediction_id: str,
        review: ReviewRequest,
        actor: Actor,
    ) -> ReviewResponse: ...

    def audit_summary(self) -> dict[str, int]: ...

    def monitoring_snapshot(
        self,
        *,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]: ...

    def ready(self) -> bool: ...

    def close(self) -> None: ...


SQLITE_SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS predictions (
    prediction_id TEXT PRIMARY KEY,
    recorded_at TEXT NOT NULL,
    request_json TEXT NOT NULL,
    probability REAL NOT NULL CHECK (probability >= 0 AND probability <= 1),
    review_tier TEXT NOT NULL,
    model_sha256 TEXT NOT NULL,
    actor_id TEXT NOT NULL DEFAULT 'legacy-local-user',
    actor_name TEXT NOT NULL DEFAULT 'Legacy local user',
    actor_role TEXT NOT NULL DEFAULT 'CivicOps.Operator'
);
CREATE TABLE IF NOT EXISTS reviews (
    review_id TEXT PRIMARY KEY,
    prediction_id TEXT NOT NULL UNIQUE,
    recorded_at TEXT NOT NULL,
    action TEXT NOT NULL,
    rationale TEXT NOT NULL,
    actor_id TEXT NOT NULL DEFAULT 'legacy-local-user',
    actor_name TEXT NOT NULL DEFAULT 'Legacy local user',
    actor_role TEXT NOT NULL DEFAULT 'CivicOps.Operator',
    FOREIGN KEY (prediction_id) REFERENCES predictions(prediction_id)
);
"""


class AuditStore:
    """SQLite development store with the same contract as PostgreSQL."""

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

    @staticmethod
    def _ensure_columns(
        connection: sqlite3.Connection,
        table: str,
        columns: dict[str, str],
    ) -> None:
        existing = {
            row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def _initialize(self) -> None:
        actor_columns = {
            "actor_id": "TEXT NOT NULL DEFAULT 'legacy-local-user'",
            "actor_name": "TEXT NOT NULL DEFAULT 'Legacy local user'",
            "actor_role": "TEXT NOT NULL DEFAULT 'CivicOps.Operator'",
        }
        with self._connection() as connection:
            connection.executescript(SQLITE_SCHEMA)
            self._ensure_columns(connection, "predictions", actor_columns)
            self._ensure_columns(connection, "reviews", actor_columns)

    def record_prediction(
        self,
        request: PredictionRequest,
        probability: float,
        review_tier: str,
        model_sha256: str,
        actor: Actor,
    ) -> str:
        prediction_id = str(uuid4())
        recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = request.model_dump(mode="json")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO predictions (
                    prediction_id, recorded_at, request_json, probability,
                    review_tier, model_sha256, actor_id, actor_name, actor_role
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prediction_id,
                    recorded_at,
                    json.dumps(payload, sort_keys=True),
                    probability,
                    review_tier,
                    model_sha256,
                    actor.subject,
                    actor.display_name,
                    actor.role,
                ),
            )
        return prediction_id

    def record_review(
        self,
        prediction_id: str,
        review: ReviewRequest,
        actor: Actor,
    ) -> ReviewResponse:
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
                        review_id, prediction_id, recorded_at, action, rationale,
                        actor_id, actor_name, actor_role
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review_id,
                        prediction_id,
                        recorded_at.isoformat(timespec="seconds"),
                        review.action,
                        review.rationale,
                        actor.subject,
                        actor.display_name,
                        actor.role,
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

    def audit_summary(self) -> dict[str, int]:
        with self._connection() as connection:
            prediction_count = connection.execute(
                "SELECT count(*) FROM predictions"
            ).fetchone()[0]
            review_count = connection.execute(
                "SELECT count(*) FROM reviews"
            ).fetchone()[0]
        return {
            "prediction_count": prediction_count,
            "review_count": review_count,
        }

    def monitoring_snapshot(
        self,
        *,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return only the non-identifying fields required for model monitoring."""

        query = """
            SELECT json_extract(p.request_json, '$.agency'),
                   json_extract(p.request_json, '$.complaint_type'),
                   json_extract(p.request_json, '$.descriptor'),
                   json_extract(p.request_json, '$.location_type'),
                   json_extract(p.request_json, '$.open_data_channel_type'),
                   p.probability, p.model_sha256,
                   CASE WHEN r.prediction_id IS NULL THEN 0 ELSE 1 END AS reviewed
            FROM predictions AS p
            LEFT JOIN reviews AS r ON r.prediction_id = p.prediction_id
        """
        parameters: tuple[str, ...] = ()
        if since is not None:
            if since.tzinfo is None or since.utcoffset() is None:
                raise ValueError("monitoring snapshot time must include a UTC offset")
            query += " WHERE p.recorded_at >= ?"
            parameters = (since.astimezone(timezone.utc).isoformat(timespec="seconds"),)
        query += " ORDER BY p.recorded_at"

        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            {
                "request": {
                    "agency": agency,
                    "complaint_type": complaint_type,
                    "descriptor": descriptor,
                    "location_type": location_type,
                    "open_data_channel_type": open_data_channel_type,
                },
                "probability": float(probability),
                "model_sha256": model_sha256,
                "reviewed": bool(reviewed),
            }
            for (
                agency,
                complaint_type,
                descriptor,
                location_type,
                open_data_channel_type,
                probability,
                model_sha256,
                reviewed,
            ) in rows
        ]

    def close(self) -> None:
        """SQLite connections are opened per operation and need no pool cleanup."""


class PostgresAuditStore:
    """Neon-compatible PostgreSQL store isolated in the civicops schema."""

    def __init__(
        self,
        database_url: str,
        *,
        max_connections: int = 5,
        apply_migrations: bool = True,
    ) -> None:
        if not database_url.startswith(("postgres://", "postgresql://")):
            raise ValueError("DATABASE_URL must use a PostgreSQL scheme")
        self.pool = NullConnectionPool(
            database_url,
            max_size=max_connections,
            open=False,
            timeout=10,
            check=NullConnectionPool.check_connection,
        )
        self.pool.open(wait=True, timeout=15)
        if apply_migrations:
            self.apply_migrations()

    def apply_migrations(self) -> None:
        """Apply checksum-tracked schema changes using an owner connection."""
        migration_root = files("civicops_ml").joinpath("migrations")
        migration_paths = sorted(
            entry for entry in migration_root.iterdir() if entry.name.endswith(".sql")
        )
        with self.pool.connection() as connection:
            connection.execute("CREATE SCHEMA IF NOT EXISTS civicops")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS civicops.schema_migrations (
                    version TEXT PRIMARY KEY,
                    sha256 CHAR(64) NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            applied = dict(
                connection.execute(
                    "SELECT version, sha256 FROM civicops.schema_migrations"
                ).fetchall()
            )
            for migration in migration_paths:
                version = migration.name
                migration_sql = migration.read_text(encoding="utf-8").replace(
                    "\r\n", "\n"
                )
                migration_sql = f"{migration_sql.rstrip()}\n"
                migration_sha256 = sha256(migration_sql.encode("utf-8")).hexdigest()
                if version in applied:
                    if applied[version] != migration_sha256:
                        raise RuntimeError(
                            f"applied migration {version} no longer matches its checksum"
                        )
                    continue
                with connection.transaction():
                    connection.execute(migration_sql)
                    connection.execute(
                        """
                        INSERT INTO civicops.schema_migrations (version, sha256)
                        VALUES (%s, %s)
                        """,
                        (version, migration_sha256),
                    )

    def record_prediction(
        self,
        request: PredictionRequest,
        probability: float,
        review_tier: str,
        model_sha256: str,
        actor: Actor,
    ) -> str:
        prediction_id = str(uuid4())
        with self.pool.connection() as connection:
            connection.execute(
                """
                INSERT INTO civicops.predictions (
                    prediction_id, request_json, probability, review_tier,
                    model_sha256, actor_id, actor_name, actor_role
                ) VALUES (%s, %s::jsonb, %s, %s, %s, %s, %s, %s)
                """,
                (
                    prediction_id,
                    json.dumps(request.model_dump(mode="json"), sort_keys=True),
                    probability,
                    review_tier,
                    model_sha256,
                    actor.subject,
                    actor.display_name,
                    actor.role,
                ),
            )
        return prediction_id

    def record_review(
        self,
        prediction_id: str,
        review: ReviewRequest,
        actor: Actor,
    ) -> ReviewResponse:
        review_id = str(uuid4())
        recorded_at = datetime.now(timezone.utc)
        try:
            with self.pool.connection() as connection:
                connection.execute(
                    """
                    INSERT INTO civicops.reviews (
                        review_id, prediction_id, action, rationale,
                        actor_id, actor_name, actor_role
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        review_id,
                        prediction_id,
                        review.action,
                        review.rationale,
                        actor.subject,
                        actor.display_name,
                        actor.role,
                    ),
                )
        except ForeignKeyViolation as exc:
            raise LookupError("prediction not found") from exc
        except UniqueViolation as exc:
            raise ValueError("a review is already recorded for this prediction") from exc
        return ReviewResponse(
            review_id=review_id,
            prediction_id=prediction_id,
            action=review.action,
            recorded_at=recorded_at,
        )

    def ready(self) -> bool:
        try:
            with self.pool.connection(timeout=3) as connection:
                connection.execute("SELECT 1").fetchone()
            return True
        except (psycopg.Error, PoolTimeout, TimeoutError):
            return False

    def audit_summary(self) -> dict[str, int]:
        with self.pool.connection() as connection:
            prediction_count = connection.execute(
                "SELECT count(*) FROM civicops.predictions"
            ).fetchone()[0]
            review_count = connection.execute(
                "SELECT count(*) FROM civicops.reviews"
            ).fetchone()[0]
        return {
            "prediction_count": prediction_count,
            "review_count": review_count,
        }

    def monitoring_snapshot(
        self,
        *,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return only the non-identifying fields required for model monitoring."""

        query = """
            SELECT p.request_json ->> 'agency',
                   p.request_json ->> 'complaint_type',
                   p.request_json ->> 'descriptor',
                   p.request_json ->> 'location_type',
                   p.request_json ->> 'open_data_channel_type',
                   p.probability, p.model_sha256,
                   (r.prediction_id IS NOT NULL) AS reviewed
            FROM civicops.predictions AS p
            LEFT JOIN civicops.reviews AS r ON r.prediction_id = p.prediction_id
        """
        parameters: tuple[datetime, ...] = ()
        if since is not None:
            if since.tzinfo is None or since.utcoffset() is None:
                raise ValueError("monitoring snapshot time must include a UTC offset")
            query += " WHERE p.recorded_at >= %s"
            parameters = (since.astimezone(timezone.utc),)
        query += " ORDER BY p.recorded_at"

        with self.pool.connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            {
                "request": {
                    "agency": agency,
                    "complaint_type": complaint_type,
                    "descriptor": descriptor,
                    "location_type": location_type,
                    "open_data_channel_type": open_data_channel_type,
                },
                "probability": float(probability),
                "model_sha256": model_sha256,
                "reviewed": bool(reviewed),
            }
            for (
                agency,
                complaint_type,
                descriptor,
                location_type,
                open_data_channel_type,
                probability,
                model_sha256,
                reviewed,
            ) in rows
        ]

    def close(self) -> None:
        self.pool.close()


def create_audit_store() -> AuditStoreProtocol:
    """Select durable PostgreSQL when configured, otherwise local SQLite."""

    database_url = os.environ.get("DATABASE_URL")
    environment = os.environ.get("CIVICOPS_ENVIRONMENT", "development").lower()
    if database_url:
        migrate_value = os.environ.get(
            "CIVICOPS_AUTO_MIGRATE",
            "true" if environment in {"development", "test"} else "false",
        ).lower()
        if migrate_value not in {"true", "false"}:
            raise RuntimeError("CIVICOPS_AUTO_MIGRATE must be true or false")
        if environment not in {"development", "test"} and migrate_value == "true":
            raise RuntimeError("automatic database migrations are disabled in production")
        return PostgresAuditStore(
            database_url,
            apply_migrations=migrate_value == "true",
        )
    if environment not in {"development", "test"}:
        raise RuntimeError("DATABASE_URL is required outside development")
    return AuditStore(Path(os.environ.get("CIVICOPS_DB_PATH", "var/civicops.sqlite3")))
