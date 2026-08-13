from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from civicops_ml.api import create_app
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


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        store = AuditStore(Path(self.temporary_directory.name) / "api.sqlite3")
        self.client_context = TestClient(create_app(StubService(), store))
        self.client = self.client_context.__enter__()
        self.payload = {
            "created_at": "2026-04-10T14:30:00-04:00",
            "agency": "DOT",
            "complaint_type": "Street Condition",
            "descriptor": "Pothole",
            "location_type": "Street",
            "open_data_channel_type": "ONLINE",
        }

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temporary_directory.cleanup()

    def test_health_and_security_headers(self) -> None:
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.headers["x-frame-options"], "DENY")

    def test_prediction_requires_human_review(self) -> None:
        response = self.client.post("/api/v1/predictions", json=self.payload)
        self.assertEqual(response.status_code, 201)
        result = response.json()
        self.assertTrue(result["human_review_required"])
        self.assertEqual(result["review_tier"], "priority_review")
        self.assertIn("prediction_id", result)

    def test_timezone_and_unknown_fields_are_rejected(self) -> None:
        self.payload["created_at"] = "2026-04-10T14:30:00"
        self.payload["status"] = "Closed"
        response = self.client.post("/api/v1/predictions", json=self.payload)
        self.assertEqual(response.status_code, 422)

    def test_review_workflow_and_duplicate_protection(self) -> None:
        prediction = self.client.post("/api/v1/predictions", json=self.payload).json()
        path = f"/api/v1/predictions/{prediction['prediction_id']}/reviews"
        review = {"action": "monitor", "rationale": "Monitor the request outcome."}
        self.assertEqual(self.client.post(path, json=review).status_code, 201)
        self.assertEqual(self.client.post(path, json=review).status_code, 409)

    def test_review_for_unknown_prediction_returns_not_found(self) -> None:
        response = self.client.post(
            "/api/v1/predictions/00000000-0000-0000-0000-000000000000/reviews",
            json={"action": "monitor", "rationale": "Monitor the request outcome."},
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
