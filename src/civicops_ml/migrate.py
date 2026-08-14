"""Explicit production database migration and runtime-role provisioning."""

from __future__ import annotations

import os
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.errors import InsufficientPrivilege

from .store import PostgresAuditStore


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def provision_runtime_role(owner_url: str, role_name: str, password: str) -> None:
    """Create or rotate a non-owner API role and grant only audit access."""

    if len(password) < 32:
        raise RuntimeError("CIVICOPS_RUNTIME_PASSWORD must contain at least 32 characters")
    with psycopg.connect(owner_url, autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s",
            (role_name,),
        ).fetchone()
        if exists:
            connection.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {} NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE").format(
                    sql.Identifier(role_name),
                    sql.Literal(password),
                )
            )
        else:
            connection.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE").format(
                    sql.Identifier(role_name),
                    sql.Literal(password),
                )
            )
        database_name = connection.execute("SELECT current_database()").fetchone()[0]
        connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database_name),
                sql.Identifier(role_name),
            )
        )
        connection.execute("REVOKE ALL ON SCHEMA civicops FROM PUBLIC")
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA civicops TO {}").format(
                sql.Identifier(role_name)
            )
        )
        connection.execute(
            sql.SQL(
                "GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA civicops TO {}"
            ).format(sql.Identifier(role_name))
        )
        connection.execute(
            sql.SQL(
                "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA civicops TO {}"
            ).format(sql.Identifier(role_name))
        )
        connection.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA civicops "
                "GRANT SELECT, INSERT ON TABLES TO {}"
            ).format(sql.Identifier(role_name))
        )


def verify_runtime_role(database_url: str, expected_role: str) -> None:
    """Prove the runtime role can audit records but cannot modify the schema."""

    prediction_id = uuid4()
    with psycopg.connect(database_url) as connection:
        current_role, is_superuser, can_create_role, can_create_database = (
            connection.execute(
                """
                SELECT rolname, rolsuper, rolcreaterole, rolcreatedb
                FROM pg_roles
                WHERE rolname = current_user
                """
            ).fetchone()
        )
        if current_role != expected_role or any(
            (is_superuser, can_create_role, can_create_database)
        ):
            raise RuntimeError("runtime database role is not least privilege")

        with connection.transaction(force_rollback=True):
            connection.execute(
                """
                INSERT INTO civicops.predictions (
                    prediction_id, request_json, probability, review_tier,
                    model_sha256, actor_id, actor_name, actor_role
                ) VALUES (%s, '{}'::jsonb, 0.5, 'standard_review', %s, %s, %s, %s)
                """,
                (
                    prediction_id,
                    "0" * 64,
                    "release-verification",
                    "Release verification",
                    "CivicOps.Administrator",
                ),
            )

        try:
            with connection.transaction(force_rollback=True):
                connection.execute("CREATE TABLE civicops.permission_probe (id int)")
        except InsufficientPrivilege:
            pass
        else:
            raise RuntimeError("runtime database role can unexpectedly modify the schema")


def main() -> None:
    owner_url = _required("MIGRATION_DATABASE_URL")
    store = PostgresAuditStore(owner_url, max_connections=1, apply_migrations=True)
    store.close()

    role_name = os.environ.get("CIVICOPS_RUNTIME_ROLE")
    role_password = os.environ.get("CIVICOPS_RUNTIME_PASSWORD")
    if bool(role_name) != bool(role_password):
        raise RuntimeError(
            "CIVICOPS_RUNTIME_ROLE and CIVICOPS_RUNTIME_PASSWORD must be supplied together"
        )
    if role_name and role_password:
        provision_runtime_role(owner_url, role_name, role_password)
        runtime_url = os.environ.get("CIVICOPS_RUNTIME_DATABASE_URL")
        if runtime_url:
            verify_runtime_role(runtime_url, role_name)


if __name__ == "__main__":
    main()
