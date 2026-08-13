from __future__ import annotations

import time
import unittest
from asyncio import run
from typing import Any

from civicops_ml.auth import (
    ADMINISTRATOR_ROLE,
    OPERATOR_ROLE,
    AuthenticationError,
    EntraAuthenticator,
    UserContext,
)
from civicops_ml.api import RedactAuthenticationQueryMiddleware

TENANT_ID = "11111111-1111-4111-8111-111111111111"
CLIENT_ID = "22222222-2222-4222-8222-222222222222"


class FakeMsalApplication:
    def __init__(self, claims: dict[str, Any]) -> None:
        self.claims = claims

    def acquire_token_by_auth_code_flow(self, *args: object, **kwargs: object):
        return {"id_token_claims": self.claims}


def authenticator_with_claims(claims: dict[str, Any]) -> EntraAuthenticator:
    authenticator = object.__new__(EntraAuthenticator)
    authenticator.tenant_id = TENANT_ID
    authenticator.client_id = CLIENT_ID
    authenticator.authority = f"https://login.microsoftonline.com/{TENANT_ID}"
    authenticator.response_mode = "query"
    authenticator.application = FakeMsalApplication(claims)
    return authenticator


def valid_claims() -> dict[str, Any]:
    return {
        "tid": TENANT_ID,
        "aud": CLIENT_ID,
        "iss": f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
        "exp": int(time.time()) + 300,
        "oid": "00000000-0000-0000-0000-000000000123",
        "name": "Test Operator",
        "preferred_username": "operator@example.test",
        "roles": [OPERATOR_ROLE],
    }


class EntraAuthenticatorTests(unittest.TestCase):
    def test_validated_claims_create_operator_context(self) -> None:
        authenticator = authenticator_with_claims(valid_claims())
        user = authenticator.finish_login(
            {"state": "expected"},
            {"state": "expected", "code": "test-code"},
        )
        self.assertEqual(user.primary_role, OPERATOR_ROLE)
        self.assertEqual(user.display_name, "Test Operator")

    def test_expired_identity_is_rejected(self) -> None:
        claims = valid_claims()
        claims["exp"] = int(time.time()) - 1
        authenticator = authenticator_with_claims(claims)
        with self.assertRaises(AuthenticationError):
            authenticator.finish_login(
                {"state": "expected"},
                {"state": "expected", "code": "test-code"},
            )

    def test_identity_without_application_role_is_rejected(self) -> None:
        claims = valid_claims()
        claims["roles"] = ["Unrelated.Role"]
        authenticator = authenticator_with_claims(claims)
        with self.assertRaises(AuthenticationError):
            authenticator.finish_login(
                {"state": "expected"},
                {"state": "expected", "code": "test-code"},
            )

    def test_administrator_role_is_primary(self) -> None:
        user = UserContext(
            subject="subject",
            display_name="Administrator",
            username="administrator@example.test",
            roles=(OPERATOR_ROLE, ADMINISTRATOR_ROLE),
        )
        self.assertEqual(user.primary_role, ADMINISTRATOR_ROLE)
        self.assertEqual(UserContext.from_session(user.to_session()), user)

    def test_callback_query_is_removed_before_access_logging(self) -> None:
        observed: dict[str, object] = {}
        scope = {
            "type": "http",
            "path": "/auth/callback",
            "raw_path": b"/auth/callback",
            "query_string": b"code=one-time-code&state=state-value",
        }

        async def inner(application_scope, receive, send):
            observed["application_query"] = application_scope["query_string"]
            await send({"type": "http.response.start", "status": 303, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                observed["logger_query"] = scope["query_string"]

        run(RedactAuthenticationQueryMiddleware(inner)(scope, receive, send))
        self.assertEqual(
            observed["application_query"],
            b"code=one-time-code&state=state-value",
        )
        self.assertEqual(observed["logger_query"], b"")


if __name__ == "__main__":
    unittest.main()
