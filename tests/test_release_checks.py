from __future__ import annotations

import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "release_checks", Path(__file__).resolve().parents[1] / ".github" / "scripts" / "release.py"
)
assert SPEC is not None and SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
SHA = "a" * 40
ORIGIN = "https://example.vercel.app"


class ReleaseChecksTests(unittest.TestCase):
    def civicops_response(self, url, **kwargs):
        path = url.removeprefix(ORIGIN)
        if path == "/healthz":
            return (
                200,
                json.dumps(
                    {"status": "ok", "model_loaded": True, "storage_ready": True, "revision": SHA}
                ).encode(),
                {},
            )
        if path == "/":
            return 303, b"", {"Location": "/login"}
        return 401, b"", {}

    def dataops_response(self, url, **kwargs):
        path = url.removeprefix(ORIGIN)
        if path == "/health":
            return (
                200,
                json.dumps(
                    {"status": "ok", "environment": "production", "database": "ok", "revision": SHA}
                ).encode(),
                {},
            )
        if kwargs.get("method") == "POST":
            self.assertEqual(kwargs["data"], {})
            return 401, b"", {}
        if path == "/dashboard":
            return 200, b"DataOps dashboard", {}
        return 200, b"swagger-ui", {}

    def test_civicops_checks_revision_and_anonymous_access(self):
        with patch.object(release, "request", side_effect=self.civicops_response):
            release.check_live(ORIGIN, "civicops", SHA)

    def test_dataops_checks_pages_and_anonymous_write_rejection(self):
        with patch.object(release, "request", side_effect=self.dataops_response):
            release.check_live(ORIGIN, "dataops", SHA)

    def test_absolute_same_origin_sign_in_redirect_is_valid(self):
        def response(url, **kwargs):
            if url == ORIGIN + "/":
                return 303, b"", {"Location": ORIGIN + "/login"}
            return self.civicops_response(url, **kwargs)

        with patch.object(release, "request", side_effect=response):
            release.check_live(ORIGIN, "civicops", SHA)

    def test_external_sign_in_redirect_is_rejected(self):
        def response(url, **kwargs):
            if url == ORIGIN + "/":
                return 303, b"", {"Location": "https://other.test/login"}
            return self.civicops_response(url, **kwargs)

        with patch.object(release, "request", side_effect=response):
            with self.assertRaisesRegex(RuntimeError, "sign-in"):
                release.check_live(ORIGIN, "civicops")

    def test_wrong_revision_fails(self):
        with patch.object(release, "request", side_effect=self.civicops_response):
            with self.assertRaisesRegex(RuntimeError, "tested commit"):
                release.check_live(ORIGIN, "civicops", "b" * 40)

    def test_unsafe_origin_is_rejected(self):
        for url in ("http://example.vercel.app", "https://evil.test", ORIGIN + "/path"):
            with self.assertRaises(RuntimeError):
                release.check_live(url, "civicops")

    def test_failed_storage_is_rejected(self):
        payload = {"status": "ok", "model_loaded": True, "storage_ready": False}
        with patch.object(release, "request", return_value=(200, json.dumps(payload).encode(), {})):
            with self.assertRaisesRegex(RuntimeError, "storage"):
                release.check_live(ORIGIN, "civicops")

    def test_open_civicops_api_is_rejected(self):
        def response(url, **kwargs):
            if url.endswith("/api/v1/session"):
                return 200, b"{}", {}
            return self.civicops_response(url, **kwargs)

        with patch.object(release, "request", side_effect=response):
            with self.assertRaisesRegex(RuntimeError, "session"):
                release.check_live(ORIGIN, "civicops")

    def test_open_dataops_write_api_is_rejected(self):
        def response(url, **kwargs):
            if kwargs.get("method") == "POST":
                return 422, b"{}", {}
            return self.dataops_response(url, **kwargs)

        with patch.object(release, "request", side_effect=response):
            with self.assertRaisesRegex(RuntimeError, "ingestion"):
                release.check_live(ORIGIN, "dataops")

    def test_non_main_event_cannot_promote(self):
        with patch.dict(
            os.environ, {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_REF": "refs/heads/main"}
        ):
            with patch.object(release, "github") as api:
                with self.assertRaises(RuntimeError):
                    release.publish(ORIGIN, "civicops")
                api.assert_not_called()

    def test_superseded_commit_does_not_move_release_branch(self):
        env = {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": SHA}
        with patch.dict(os.environ, env):
            with patch.object(release, "github", return_value={"object": {"sha": "b" * 40}}) as api:
                release.publish(ORIGIN, "civicops")
                self.assertEqual(api.call_count, 1)

    def test_failed_vercel_status_cannot_pass_release(self):
        env = {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": SHA}
        replies = [
            {"object": {"sha": SHA}},
            {"object": {"sha": SHA}},
            {"statuses": [{"context": "Vercel", "state": "failure"}]},
        ]
        with patch.dict(os.environ, env):
            with patch.object(release, "github", side_effect=replies):
                with patch.object(release, "check_live") as check:
                    with self.assertRaisesRegex(RuntimeError, "deployment failed"):
                        release.publish(ORIGIN, "civicops")
                    check.assert_not_called()

    def test_unrelated_success_does_not_confirm_deployment(self):
        env = {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": SHA}
        replies = [
            {"object": {"sha": SHA}},
            {"object": {"sha": SHA}},
            {"statuses": [{"context": "other", "state": "success"}]},
            {"statuses": [{"context": "Vercel", "state": "success"}]},
        ]
        with patch.dict(os.environ, env):
            with patch.object(release, "github", side_effect=replies):
                with patch.object(release.time, "sleep") as sleep:
                    with patch.object(release, "check_live") as check:
                        release.publish(ORIGIN, "civicops")
                        sleep.assert_called_once_with(15)
                        check.assert_called_once_with(ORIGIN, "civicops", SHA)

    def test_promotion_is_fast_forward_and_checks_live_revision(self):
        env = {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": SHA}
        replies = [
            {"object": {"sha": SHA}},
            {"object": {"sha": "b" * 40}},
            {},
            {"statuses": [{"context": "Vercel", "state": "success"}]},
        ]
        with patch.dict(os.environ, env):
            with patch.object(release, "github", side_effect=replies) as api:
                with patch.object(release, "check_live") as check:
                    release.publish(ORIGIN, "civicops")
                    self.assertEqual(
                        api.call_args_list[2].kwargs["data"], {"sha": SHA, "force": False}
                    )
                    check.assert_called_once_with(ORIGIN, "civicops", SHA)


if __name__ == "__main__":
    unittest.main()
