from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping

from fastapi.testclient import TestClient

from civicops_ml.api import create_app
from civicops_ml.auth import ADMINISTRATOR_ROLE, OPERATOR_ROLE, UserContext
from civicops_ml.store import AuditStore


class StubService:
    model_sha256 = "a" * 64

    def metadata(self) -> dict[str, object]:
        return {
            "model": "test_model",
            "model_sha256": self.model_sha256,
            "calibration": "identity",
            "features": ["agency"],
            "operational_threshold": 0.67,
            "status": "human_review_only",
            "automated_decisions_allowed": False,
        }

    def predict(self, request: object) -> dict[str, object]:
        return {
            "probability": 0.72,
            "review_tier": "priority_review",
            "above_review_threshold": True,
            "operational_threshold": 0.66999111,
            "human_review_required": True,
            "explanation": [
                {
                    "feature": "agency",
                    "value": "DOT",
                    "contribution": 0.2,
                    "direction": "higher",
                }
            ],
            "input_warnings": [],
            "explanation_notice": "Associations, not causes.",
            "score_notice": "Human decision required.",
            "model_sha256": self.model_sha256,
        }


class StubAuthenticator:
    def __init__(self, roles: tuple[str, ...] = (OPERATOR_ROLE,)) -> None:
        self.user = UserContext(
            subject="00000000-0000-0000-0000-000000000123",
            display_name="Test Operator",
            username="operator@example.test",
            roles=roles,
        )

    def begin_login(self, redirect_uri: str) -> dict[str, Any]:
        return {
            "auth_uri": "https://login.example.test/authorize",
            "state": "test-state",
            "redirect_uri": redirect_uri,
        }

    def finish_login(
        self,
        flow: Mapping[str, Any],
        auth_response: Mapping[str, str],
    ) -> UserContext:
        if flow["state"] != auth_response.get("state"):
            raise ValueError("state mismatch")
        return self.user

    def logout_url(self, post_logout_redirect_uri: str) -> str:
        return f"https://login.example.test/logout?return={post_logout_redirect_uri}"


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        store = AuditStore(Path(self.temporary_directory.name) / "api.sqlite3")
        self.client_context = TestClient(
            create_app(
                StubService(),
                store,
                StubAuthenticator(),
                session_secret="test-session-secret-that-is-at-least-32-characters",
            )
        )
        self.client = self.client_context.__enter__()
        self.payload = {
            "created_at": "2026-04-10T14:30:00-04:00",
            "agency": "DOT",
            "complaint_type": "Street Condition",
            "descriptor": "Pothole",
            "location_type": "Street",
            "open_data_channel_type": "ONLINE",
        }
        self.csrf_token: str | None = None

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temporary_directory.cleanup()

    def sign_in(self) -> None:
        login = self.client.get("/login", follow_redirects=False)
        self.assertEqual(login.status_code, 303)
        self.assertEqual(
            login.headers["location"],
            "https://login.example.test/authorize",
        )
        callback = self.client.get(
            "/auth/callback?code=test-code&state=test-state",
            follow_redirects=False,
        )
        self.assertEqual(callback.status_code, 303)
        session = self.client.get("/api/v1/session")
        self.assertEqual(session.status_code, 200)
        self.csrf_token = session.json()["csrf_token"]

    def authenticated_post(self, path: str, payload: dict[str, object]):
        if self.csrf_token is None:
            self.sign_in()
        return self.client.post(
            path,
            json=payload,
            headers={"X-CSRF-Token": self.csrf_token},
        )

    def test_health_is_public_and_has_security_headers(self) -> None:
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.headers["x-frame-options"], "DENY")

    def test_console_redirects_to_login_and_api_rejects_anonymous_requests(self) -> None:
        root = self.client.get("/", follow_redirects=False)
        self.assertEqual(root.status_code, 303)
        self.assertEqual(root.headers["location"], "http://testserver/login")
        self.assertEqual(self.client.get("/api/v1/model").status_code, 401)

    def test_prediction_requires_human_review(self) -> None:
        response = self.authenticated_post("/api/v1/predictions", self.payload)
        self.assertEqual(response.status_code, 201)
        result = response.json()
        self.assertTrue(result["human_review_required"])
        self.assertEqual(result["review_tier"], "priority_review")
        self.assertIn("prediction_id", result)

    def test_mutations_require_csrf_token(self) -> None:
        self.sign_in()
        response = self.client.post("/api/v1/predictions", json=self.payload)
        self.assertEqual(response.status_code, 403)

    def test_timezone_and_unknown_fields_are_rejected(self) -> None:
        self.payload["created_at"] = "2026-04-10T14:30:00"
        self.payload["status"] = "Closed"
        response = self.authenticated_post("/api/v1/predictions", self.payload)
        self.assertEqual(response.status_code, 422)

    def test_review_workflow_and_duplicate_protection(self) -> None:
        prediction = self.authenticated_post(
            "/api/v1/predictions", self.payload
        ).json()
        path = f"/api/v1/predictions/{prediction['prediction_id']}/reviews"
        review = {"action": "monitor", "rationale": "Monitor the request outcome."}
        self.assertEqual(self.authenticated_post(path, review).status_code, 201)
        self.assertEqual(self.authenticated_post(path, review).status_code, 409)

    def test_review_for_unknown_prediction_returns_not_found(self) -> None:
        response = self.authenticated_post(
            "/api/v1/predictions/00000000-0000-0000-0000-000000000000/reviews",
            {"action": "monitor", "rationale": "Monitor the request outcome."},
        )
        self.assertEqual(response.status_code, 404)

    def test_operator_cannot_read_admin_audit_summary(self) -> None:
        self.sign_in()
        response = self.client.get("/api/v1/admin/audit-summary")
        self.assertEqual(response.status_code, 403)

    def test_administrator_can_read_audit_summary(self) -> None:
        self.client_context.__exit__(None, None, None)
        store = AuditStore(Path(self.temporary_directory.name) / "admin.sqlite3")
        self.client_context = TestClient(
            create_app(
                StubService(),
                store,
                StubAuthenticator((ADMINISTRATOR_ROLE,)),
                session_secret="test-session-secret-that-is-at-least-32-characters",
            )
        )
        self.client = self.client_context.__enter__()
        self.csrf_token = None
        self.sign_in()
        response = self.client.get("/api/v1/admin/audit-summary")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"prediction_count": 0, "review_count": 0},
        )


if __name__ == "__main__":
    unittest.main()
