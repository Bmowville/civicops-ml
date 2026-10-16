"""Fail-closed configuration for the horizontally scaled application."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from fastapi import FastAPI


def validate_serverless_environment(environment: Mapping[str, str]) -> None:
    required = {
        "CIVICOPS_ENVIRONMENT": "production",
        "CIVICOPS_AUTO_MIGRATE": "false",
        "CIVICOPS_RATE_LIMIT_BACKEND": "postgres",
        "CIVICOPS_MONITORING_MODE": "scheduled",
    }
    for name, expected in required.items():
        if environment.get(name) != expected:
            raise RuntimeError(f"serverless hosting requires {name}={expected}")
    if not environment.get("DATABASE_URL", "").startswith(
        ("postgres://", "postgresql://")
    ):
        raise RuntimeError("serverless hosting requires a PostgreSQL DATABASE_URL")
    if len(environment.get("CIVICOPS_MONITORING_TOKEN", "")) < 32:
        raise RuntimeError(
            "CIVICOPS_MONITORING_TOKEN must contain at least 32 characters"
        )


def create_serverless_app() -> FastAPI:
    validate_serverless_environment(os.environ)
    from .api import app

    return app
