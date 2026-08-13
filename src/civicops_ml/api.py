"""FastAPI application for the CivicOps ML human-review workflow."""

from __future__ import annotations

import argparse
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable
from urllib.parse import parse_qsl
from uuid import UUID

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .auth import (
    ADMINISTRATOR_ROLE,
    APPLICATION_ROLES,
    AuthenticationError,
    AuthenticatorProtocol,
    EntraAuthenticator,
    UserContext,
)
from .schemas import (
    HealthResponse,
    PredictionRequest,
    PredictionResponse,
    ReviewRequest,
    ReviewResponse,
)
from .serving import PredictionService
from .store import Actor, AuditStoreProtocol, create_audit_store

load_dotenv(".env.local", override=False)

AsgiMessage = dict[str, Any]
AsgiReceive = Callable[[], Awaitable[AsgiMessage]]
AsgiSend = Callable[[AsgiMessage], Awaitable[None]]


class RedactAuthenticationQueryMiddleware:
    """Remove authorization responses before the HTTP server writes access logs."""

    def __init__(self, application: Any) -> None:
        self.application = application

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: AsgiReceive,
        send: AsgiSend,
    ) -> None:
        if scope.get("type") != "http" or scope.get("path") != "/auth/callback":
            await self.application(scope, receive, send)
            return

        async def send_redacted(message: AsgiMessage) -> None:
            if message.get("type") == "http.response.start":
                scope["query_string"] = b""
                scope["raw_path"] = b"/auth/callback"
            await send(message)

        await self.application(scope, receive, send_redacted)


def _path_from_env(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default))


def _environment() -> str:
    return os.environ.get("CIVICOPS_ENVIRONMENT", "development").lower()


def _session_secret(configured_secret: str | None) -> str:
    value = configured_secret or os.environ.get("CIVICOPS_SESSION_SECRET")
    if value and len(value) >= 32:
        return value
    if _environment() in {"development", "test"}:
        return secrets.token_urlsafe(48)
    raise RuntimeError("CIVICOPS_SESSION_SECRET must contain at least 32 characters")


def _current_user(request: Request) -> UserContext:
    try:
        return UserContext.from_session(request.session.get("user"))
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        ) from exc


def _require_roles(
    request: Request,
    allowed_roles: set[str] | frozenset[str],
) -> UserContext:
    user = _current_user(request)
    if not user.has_any_role(allowed_roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="the assigned application role does not allow this operation",
        )
    return user


def _require_csrf(request: Request) -> None:
    expected = request.session.get("csrf_token", "")
    supplied = request.headers.get("X-CSRF-Token", "")
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed",
        )


def _callback_url(request: Request) -> str:
    public_base_url = os.environ.get("CIVICOPS_PUBLIC_BASE_URL")
    if public_base_url:
        if _environment() not in {"development", "test"} and not public_base_url.startswith(
            "https://"
        ):
            raise RuntimeError("CIVICOPS_PUBLIC_BASE_URL must use HTTPS")
        return f"{public_base_url.rstrip('/')}/auth/callback"
    return str(request.url_for("auth_callback"))


def create_app(
    service: PredictionService | Any | None = None,
    store: AuditStoreProtocol | None = None,
    authenticator: AuthenticatorProtocol | None = None,
    *,
    session_secret: str | None = None,
) -> FastAPI:
    """Create an application, allowing dependency injection for isolated tests."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if application.state.service is None:
            application.state.service = PredictionService(
                model_path=_path_from_env(
                    "CIVICOPS_MODEL_PATH",
                    "models/candidate.joblib",
                ),
                diagnostics_path=_path_from_env(
                    "CIVICOPS_DIAGNOSTICS_PATH",
                    "reports/diagnostics.json",
                ),
                calibrator_path=_path_from_env(
                    "CIVICOPS_CALIBRATOR_PATH",
                    "models/calibrator.joblib",
                ),
                baseline_metrics_path=_path_from_env(
                    "CIVICOPS_BASELINE_PATH",
                    "reports/baseline_metrics.json",
                ),
            )
        if application.state.store is None:
            application.state.store = create_audit_store()
        if application.state.authenticator is None:
            application.state.authenticator = EntraAuthenticator.from_environment()
        try:
            yield
        finally:
            application.state.store.close()

    application = FastAPI(
        title="CivicOps ML",
        description=(
            "Human-review API for seven-day NYC 311 resolution-risk estimates. "
            "Scores cannot make service decisions."
        ),
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.state.service = service
    application.state.store = store
    application.state.authenticator = authenticator
    application.add_middleware(
        SessionMiddleware,
        secret_key=_session_secret(session_secret),
        session_cookie="civicops_session",
        max_age=8 * 60 * 60,
        same_site=(
            "lax" if _environment() in {"development", "test"} else "none"
        ),
        https_only=_environment() not in {"development", "test"},
    )
    application.add_middleware(RedactAuthenticationQueryMiddleware)

    static_directory = Path(__file__).parent / "static"
    application.mount("/static", StaticFiles(directory=static_directory), name="static")

    @application.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), geolocation=(), microphone=(), payment=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self' https://login.microsoftonline.com"
        )
        if _environment() not in {"development", "test"}:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response

    @application.get("/", include_in_schema=False, response_model=None)
    async def operator_console(request: Request) -> FileResponse | RedirectResponse:
        try:
            _require_roles(request, APPLICATION_ROLES)
        except HTTPException:
            return RedirectResponse(request.url_for("login"), status_code=303)
        return FileResponse(static_directory / "index.html")

    @application.get("/login", include_in_schema=False)
    async def login(request: Request) -> RedirectResponse:
        request.session.clear()
        flow = request.app.state.authenticator.begin_login(_callback_url(request))
        request.session["auth_flow"] = flow
        return RedirectResponse(str(flow["auth_uri"]), status_code=303)

    @application.api_route(
        "/auth/callback",
        methods=["GET", "POST"],
        include_in_schema=False,
    )
    async def auth_callback(request: Request) -> RedirectResponse:
        flow = request.session.pop("auth_flow", None)
        if not isinstance(flow, dict):
            raise HTTPException(status_code=400, detail="login session has expired")
        auth_response = dict(request.query_params)
        if request.method == "POST":
            content_type = request.headers.get("content-type", "").split(";", 1)[0]
            if content_type != "application/x-www-form-urlencoded":
                raise HTTPException(status_code=400, detail="invalid login response format")
            body = (await request.body()).decode("utf-8", errors="strict")
            auth_response = dict(parse_qsl(body, keep_blank_values=True))
        try:
            user = request.app.state.authenticator.finish_login(
                flow,
                auth_response,
            )
        except AuthenticationError as exc:
            request.session.clear()
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        request.session.clear()
        request.session["user"] = user.to_session()
        request.session["csrf_token"] = secrets.token_urlsafe(32)
        return RedirectResponse(request.url_for("operator_console"), status_code=303)

    @application.post("/logout", include_in_schema=False)
    async def logout(request: Request) -> dict[str, str]:
        _current_user(request)
        _require_csrf(request)
        logout_url = request.app.state.authenticator.logout_url(
            str(request.url_for("operator_console"))
        )
        request.session.clear()
        return {"logout_url": logout_url}

    @application.get("/healthz", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        return HealthResponse(
            status="ok",
            model_loaded=request.app.state.service is not None,
            storage_ready=request.app.state.store.ready(),
        )

    @application.get("/api/v1/session")
    async def session(request: Request) -> dict[str, Any]:
        user = _require_roles(request, APPLICATION_ROLES)
        return {
            "user": {
                "display_name": user.display_name,
                "username": user.username,
                "roles": list(user.roles),
            },
            "csrf_token": request.session["csrf_token"],
        }

    @application.get("/api/v1/model")
    async def model_metadata(request: Request) -> dict[str, Any]:
        _require_roles(request, APPLICATION_ROLES)
        return request.app.state.service.metadata()

    @application.post(
        "/api/v1/predictions",
        response_model=PredictionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_prediction(
        prediction_request: PredictionRequest,
        request: Request,
    ) -> PredictionResponse:
        user = _require_roles(request, APPLICATION_ROLES)
        _require_csrf(request)
        result = request.app.state.service.predict(prediction_request)
        prediction_id = request.app.state.store.record_prediction(
            prediction_request,
            result["probability"],
            result["review_tier"],
            result["model_sha256"],
            Actor(user.subject, user.display_name, user.primary_role),
        )
        return PredictionResponse(prediction_id=prediction_id, **result)

    @application.post(
        "/api/v1/predictions/{prediction_id}/reviews",
        response_model=ReviewResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_review(
        prediction_id: UUID,
        review: ReviewRequest,
        request: Request,
    ) -> ReviewResponse:
        user = _require_roles(request, APPLICATION_ROLES)
        _require_csrf(request)
        try:
            return request.app.state.store.record_review(
                str(prediction_id),
                review,
                Actor(user.subject, user.display_name, user.primary_role),
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.get("/api/v1/admin/audit-summary")
    async def audit_summary(request: Request) -> dict[str, int]:
        _require_roles(request, {ADMINISTRATOR_ROLE})
        return request.app.state.store.audit_summary()

    @application.get("/docs", include_in_schema=False)
    async def api_document(request: Request) -> JSONResponse:
        _require_roles(request, APPLICATION_ROLES)
        return JSONResponse(application.openapi())

    return application


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run(
        "civicops_ml.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
