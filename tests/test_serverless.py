from __future__ import annotations

import unittest

from civicops_ml.serverless import validate_serverless_environment


class ServerlessConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = {
            "CIVICOPS_ENVIRONMENT": "production",
            "CIVICOPS_AUTO_MIGRATE": "false",
            "CIVICOPS_RATE_LIMIT_BACKEND": "postgres",
            "CIVICOPS_MONITORING_MODE": "scheduled",
            "DATABASE_URL": "postgresql://user:password@db.example/civicops",
            "CIVICOPS_MONITORING_TOKEN": "test-monitoring-token-that-is-at-least-32-characters",
        }

    def test_valid_configuration_is_accepted(self) -> None:
        validate_serverless_environment(self.environment)

    def test_missing_or_unsafe_settings_are_rejected(self) -> None:
        for name in self.environment:
            with self.subTest(name=name):
                environment = self.environment.copy()
                environment.pop(name)
                with self.assertRaises(RuntimeError):
                    validate_serverless_environment(environment)
        for name, value in {
            "DATABASE_URL": "sqlite:///temporary.sqlite3",
            "CIVICOPS_ENVIRONMENT": "development",
            "CIVICOPS_AUTO_MIGRATE": "true",
            "CIVICOPS_RATE_LIMIT_BACKEND": "memory",
            "CIVICOPS_MONITORING_MODE": "background",
            "CIVICOPS_MONITORING_TOKEN": "short",
        }.items():
            with self.subTest(name=name):
                environment = self.environment | {name: value}
                with self.assertRaises(RuntimeError):
                    validate_serverless_environment(environment)
