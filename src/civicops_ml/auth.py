"""Microsoft Entra authentication and application-role authorization."""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import urlencode
from uuid import UUID

import msal

OPERATOR_ROLE = "CivicOps.Operator"
ADMINISTRATOR_ROLE = "CivicOps.Administrator"
APPLICATION_ROLES = frozenset({OPERATOR_ROLE, ADMINISTRATOR_ROLE})


class AuthenticationError(RuntimeError):
    """Raised when an identity response cannot establish an application user."""


@dataclass(frozen=True)
class UserContext:
    """Minimum authenticated identity retained in the signed application session."""

    subject: str
    display_name: str
    username: str
    roles: tuple[str, ...]

    @property
    def primary_role(self) -> str:
        if ADMINISTRATOR_ROLE in self.roles:
            return ADMINISTRATOR_ROLE
        return OPERATOR_ROLE

    def has_any_role(self, allowed_roles: set[str] | frozenset[str]) -> bool:
        return bool(set(self.roles) & set(allowed_roles))

    def to_session(self) -> dict[str, Any]:
        value = asdict(self)
        value["roles"] = list(self.roles)
        return value

    @classmethod
    def from_session(cls, value: object) -> UserContext:
        if not isinstance(value, dict):
            raise AuthenticationError("authentication session is missing")
        try:
            roles = tuple(
                role for role in value["roles"] if isinstance(role, str)
            )
            user = cls(
                subject=str(value["subject"]),
                display_name=str(value["display_name"]),
                username=str(value["username"]),
                roles=roles,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthenticationError("authentication session is invalid") from exc
        if not user.subject or not user.has_any_role(APPLICATION_ROLES):
            raise AuthenticationError("authentication session is unauthorized")
        return user


class AuthenticatorProtocol(Protocol):
    def begin_login(self, redirect_uri: str) -> dict[str, Any]: ...

    def finish_login(
        self,
        flow: Mapping[str, Any],
        auth_response: Mapping[str, str],
    ) -> UserContext: ...

    def logout_url(self, post_logout_redirect_uri: str) -> str: ...


class EntraAuthenticator:
    """Confidential-client OIDC flow backed by Microsoft Entra ID."""

    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        client_credential: str | dict[str, str],
        response_mode: str = "query",
    ) -> None:
        self.tenant_id = str(UUID(tenant_id))
        self.client_id = str(UUID(client_id))
        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        if response_mode not in {"query", "form_post"}:
            raise ValueError("response_mode must be query or form_post")
        self.response_mode = response_mode
        self.application = msal.ConfidentialClientApplication(
            self.client_id,
            authority=self.authority,
            client_credential=client_credential,
        )

    @classmethod
    def from_environment(cls) -> EntraAuthenticator:
        tenant_id = _required_environment("CIVICOPS_ENTRA_TENANT_ID")
        client_id = _required_environment("CIVICOPS_ENTRA_CLIENT_ID")
        client_secret = os.environ.get("CIVICOPS_ENTRA_CLIENT_SECRET")
        certificate_path = os.environ.get("CIVICOPS_ENTRA_CERTIFICATE_PATH")
        certificate_private_key = os.environ.get(
            "CIVICOPS_ENTRA_CERTIFICATE_PRIVATE_KEY"
        )
        certificate_thumbprint = os.environ.get(
            "CIVICOPS_ENTRA_CERTIFICATE_THUMBPRINT"
        )

        certificate_sources = sum(
            source is not None
            for source in (certificate_path, certificate_private_key)
        )
        if certificate_sources > 1:
            raise RuntimeError(
                "configure an Entra certificate path or private key value, not both"
            )
        if client_secret and (
            certificate_path or certificate_private_key or certificate_thumbprint
        ):
            raise RuntimeError(
                "configure either an Entra client secret or certificate, not both"
            )
        if client_secret:
            credential: str | dict[str, str] = client_secret
        elif (certificate_path or certificate_private_key) and certificate_thumbprint:
            private_key = (
                Path(certificate_path).read_text(encoding="utf-8")
                if certificate_path
                else certificate_private_key
            )
            credential = {
                "private_key": private_key,
                "thumbprint": certificate_thumbprint,
            }
        else:
            raise RuntimeError(
                "an Entra client secret or certificate credential is required"
            )
        return cls(
            tenant_id=tenant_id,
            client_id=client_id,
            client_credential=credential,
            response_mode=(
                "query"
                if os.environ.get("CIVICOPS_ENVIRONMENT", "development").lower()
                in {"development", "test"}
                else "form_post"
            ),
        )

    def begin_login(self, redirect_uri: str) -> dict[str, Any]:
        flow = self.application.initiate_auth_code_flow(
            scopes=[],
            redirect_uri=redirect_uri,
            prompt="select_account",
            response_mode=self.response_mode,
        )
        if "auth_uri" not in flow:
            raise AuthenticationError("Microsoft Entra login could not be started")
        return flow

    def finish_login(
        self,
        flow: Mapping[str, Any],
        auth_response: Mapping[str, str],
    ) -> UserContext:
        try:
            result = self.application.acquire_token_by_auth_code_flow(
                dict(flow),
                dict(auth_response),
                scopes=[],
            )
        except ValueError as exc:
            raise AuthenticationError("Microsoft Entra login response is invalid") from exc
        claims = result.get("id_token_claims")
        if not isinstance(claims, dict):
            raise AuthenticationError("Microsoft Entra did not return a valid identity")
        self._validate_claims(claims)

        roles = tuple(
            sorted(
                role
                for role in claims.get("roles", [])
                if isinstance(role, str) and role in APPLICATION_ROLES
            )
        )
        if not roles:
            raise AuthenticationError("this account has no CivicOps application role")

        subject = str(claims.get("oid") or claims.get("sub") or "")
        username = str(
            claims.get("preferred_username") or claims.get("email") or subject
        )
        display_name = str(claims.get("name") or username)
        if not subject:
            raise AuthenticationError("Microsoft Entra identity has no subject")
        return UserContext(
            subject=subject,
            display_name=display_name,
            username=username,
            roles=roles,
        )

    def _validate_claims(self, claims: Mapping[str, Any]) -> None:
        if claims.get("tid") != self.tenant_id:
            raise AuthenticationError("Microsoft Entra tenant does not match")
        audience = claims.get("aud")
        audiences = {audience} if isinstance(audience, str) else set(audience or [])
        if self.client_id not in audiences:
            raise AuthenticationError("Microsoft Entra audience does not match")
        if int(claims.get("exp", 0)) <= int(time.time()):
            raise AuthenticationError("Microsoft Entra identity token has expired")
        accepted_issuers = {
            f"https://login.microsoftonline.com/{self.tenant_id}/v2.0",
            f"https://sts.windows.net/{self.tenant_id}/",
        }
        if claims.get("iss") not in accepted_issuers:
            raise AuthenticationError("Microsoft Entra issuer does not match")

    def logout_url(self, post_logout_redirect_uri: str) -> str:
        query = urlencode({"post_logout_redirect_uri": post_logout_redirect_uri})
        return f"{self.authority}/oauth2/v2.0/logout?{query}"


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value
