from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from civicops_ml.telemetry import configure_telemetry


class TelemetryTests(unittest.TestCase):
    def test_configuration_is_disabled_without_connection_string(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(configure_telemetry())

    def test_fastapi_application_is_instrumented_explicitly(self) -> None:
        application = object()
        with (
            patch("pathlib.Path.home", return_value=Path.cwd()),
            patch.dict(
                os.environ,
                {
                    "APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=test",
                    "CIVICOPS_TRACES_PER_SECOND": "0.5",
                },
                clear=True,
            ),
            patch(
                "azure.monitor.opentelemetry.configure_azure_monitor"
            ) as configure_monitor,
            patch(
                "opentelemetry.instrumentation.fastapi.FastAPIInstrumentor.instrument_app"
            ) as instrument_app,
        ):
            self.assertTrue(configure_telemetry(application))

        self.assertFalse(
            configure_monitor.call_args.kwargs["instrumentation_options"]["fastapi"][
                "enabled"
            ]
        )
        instrument_app.assert_called_once_with(
            application,
            excluded_urls=".*/livez,.*/healthz",
        )


if __name__ == "__main__":
    unittest.main()
