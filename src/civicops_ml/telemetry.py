"""Privacy-conscious Azure Monitor OpenTelemetry setup."""

from __future__ import annotations

import logging
import os

LOGGER_NAME = "civicops_ml"


def configure_telemetry() -> bool:
    """Enable Azure Monitor only when the platform provides a connection string."""

    if not os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        return False

    traces_per_second = float(os.environ.get("CIVICOPS_TRACES_PER_SECOND", "0.5"))
    if not 0 < traces_per_second <= 100:
        raise RuntimeError("CIVICOPS_TRACES_PER_SECOND must be greater than 0 and at most 100")

    from azure.monitor.opentelemetry import configure_azure_monitor

    configure_azure_monitor(
        logger_name=LOGGER_NAME,
        enable_live_metrics=False,
        traces_per_second=traces_per_second,
        instrumentation_options={
            "django": {"enabled": False},
            "flask": {"enabled": False},
            "psycopg2": {"enabled": False},
        },
    )
    logging.getLogger(LOGGER_NAME).setLevel(logging.INFO)
    logging.getLogger(LOGGER_NAME).info(
        "CivicOps telemetry initialized",
        extra={"custom_dimensions": {"component": "api"}},
    )
    return True
