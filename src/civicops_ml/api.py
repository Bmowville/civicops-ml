"""FastAPI application for the CivicOps ML human-review workflow."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable
from urllib.parse import parse_qsl
from uuid import UUID

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)
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
from .rate_limit import SlidingWindowRateLimiter
from .monitoring import build_store_monitoring_report
from .store import Actor, AuditStoreProtocol, create_audit_store
from .telemetry import configure_telemetry

load_dotenv(".env.local", override=False)

AsgiMessage = dict[str, Any]
AsgiReceive = Callable[[], Awaitable[AsgiMessage]]
AsgiSend = Callable[[AsgiMessage], Awaitable[None]]
LOGGER = logging.getLogger("civicops_ml")


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


def _positive_limit(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be positive")
    return value


def _monitoring_window_days() -> int:
    return _positive_limit("CIVICOPS_MONITORING_WINDOW_DAYS", 30)


def _monitoring_interval_seconds() -> int:
    try:
        value = int(os.environ.get("CIVICOPS_MONITORING_INTERVAL_SECONDS", "21600"))
    except ValueError as exc:
        raise RuntimeError(
            "CIVICOPS_MONITORING_INTERVAL_SECONDS must be an integer"
        ) from exc
    if value < 300:
        raise RuntimeError(
            "CIVICOPS_MONITORING_INTERVAL_SECONDS must be at least 300"
        )
    return value


def _monitoring_baseline_path() -> Path:
    return _path_from_env(
        "CIVICOPS_MONITORING_BASELINE_PATH",
        "reports/monitoring_baseline.json",
    )


def _monitoring_dimensions(report: dict[str, Any]) -> dict[str, object]:
    feature_checks = report["checks"]["feature_drift"].values()
    divergence_values = [
        check["jensen_shannon"]
        for check in feature_checks
        if check["jensen_shannon"] is not None
    ]
    unseen_values = [
        check["unseen_category_rate"]
        for check in report["checks"]["feature_drift"].values()
        if check["unseen_category_rate"] is not None
    ]
    return {
        "component": "production_monitoring",
        "monitoring_status": report["overall_status"],
        "prediction_count": report["window"]["prediction_count"],
        "review_count": report["window"]["review_count"],
        "model_hash_mismatch_count": report["checks"]["model_integrity"][
            "mismatch_count"
        ],
        "review_completion_rate": report["checks"]["review_completion"]["rate"],
        "score_psi": report["checks"]["score_drift"]["psi"],
        "maximum_feature_jensen_shannon": (
            max(divergence_values) if divergence_values else None
        ),
        "maximum_unseen_category_rate": max(unseen_values) if unseen_values else None,
    }


def _build_application_monitoring_report(application: FastAPI) -> dict[str, Any]:
    return build_store_monitoring_report(
        application.state.store,
        _monitoring_baseline_path(),
        window_days=_monitoring_window_days(),
    )


async def _production_monitoring_loop(application: FastAPI) -> None:
    interval_seconds = _monitoring_interval_seconds()
    while True:
        try:
            report = await asyncio.to_thread(
                _build_application_monitoring_report,
                application,
            )
            application.state.latest_monitoring_report = report
            log = (
                LOGGER.error
                if report["overall_status"] == "critical"
                else LOGGER.warning
                if report["overall_status"] == "warning"
                else LOGGER.info
            )
            log(
                "CivicOps production monitoring evaluation completed",
                extra={"custom_dimensions": _monitoring_dimensions(report)},
            )
        except Exception:
            LOGGER.exception(
                "CivicOps production monitoring evaluation failed",
                extra={
                    "custom_dimensions": {
                        "component": "production_monitoring",
                        "monitoring_status": "evaluation_error",
                    }
                },
            )
        await asyncio.sleep(interval_seconds)


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


def _public_url(request: Request, route_name: str) -> str:
    route_path = str(request.app.url_path_for(route_name))
    public_base_url = os.environ.get("CIVICOPS_PUBLIC_BASE_URL")
    if public_base_url:
        if _environment() not in {"development", "test"} and not public_base_url.startswith(
            "https://"
        ):
            raise RuntimeError("CIVICOPS_PUBLIC_BASE_URL must use HTTPS")
        return f"{public_base_url.rstrip('/')}{route_path}"
    return str(request.url_for(route_name))


def _callback_url(request: Request) -> str:
    return _public_url(request, "auth_callback")


def _enforce_rate_limit(
    request: Request,
    key: str,
    *,
    limit: int,
    window_seconds: int,
) -> None:
    allowed, retry_after = request.app.state.rate_limiter.check(
        key,
        limit=limit,
        window_seconds=window_seconds,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="request rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )


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
        monitoring_task: asyncio.Task[None] | None = None
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
        if _environment() not in {"development", "test"}:
            monitoring_task = asyncio.create_task(
                _production_monitoring_loop(application)
            )
        try:
            yield
        finally:
            if monitoring_task is not None:
                monitoring_task.cancel()
                with suppress(asyncio.CancelledError):
                    await monitoring_task
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
    application.state.rate_limiter = SlidingWindowRateLimiter()
    application.state.latest_monitoring_report = None
    application.state.login_limit = _positive_limit(
        "CIVICOPS_LOGIN_LIMIT_PER_FIVE_MINUTES", 10
    )
    application.state.mutation_limit = _positive_limit(
        "CIVICOPS_MUTATION_LIMIT_PER_MINUTE", 30
    )
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
        if request.url.path == "/docs":
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "script-src 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data:; connect-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
            )
        else:
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
            return RedirectResponse(_public_url(request, "login"), status_code=303)
        return FileResponse(static_directory / "index.html")

    @application.get("/login", include_in_schema=False)
    async def login(request: Request) -> RedirectResponse:
        _enforce_rate_limit(
            request,
            "login:global",
            limit=request.app.state.login_limit,
            window_seconds=5 * 60,
        )
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
        return RedirectResponse(_public_url(request, "operator_console"), status_code=303)

    @application.post("/logout", include_in_schema=False)
    async def logout(request: Request) -> dict[str, str]:
        _current_user(request)
        _require_csrf(request)
        logout_url = request.app.state.authenticator.logout_url(
            _public_url(request, "operator_console")
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

    @application.get("/livez", include_in_schema=False)
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

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
        _enforce_rate_limit(
            request,
            f"mutation:{user.subject}",
            limit=request.app.state.mutation_limit,
            window_seconds=60,
        )
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
        _enforce_rate_limit(
            request,
            f"mutation:{user.subject}",
            limit=request.app.state.mutation_limit,
            window_seconds=60,
        )
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

    @application.get("/api/v1/admin/monitoring")
    async def production_monitoring(request: Request) -> dict[str, Any]:
        _require_roles(request, {ADMINISTRATOR_ROLE})
        report = await asyncio.to_thread(
            _build_application_monitoring_report,
            request.app,
        )
        request.app.state.latest_monitoring_report = report
        return report

    @application.get("/docs", include_in_schema=False)
    async def api_document(request: Request) -> HTMLResponse:
        _require_roles(request, APPLICATION_ROLES)
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title="CivicOps ML API documentation",
            swagger_js_url=(
                "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.32.13/"
                "swagger-ui-bundle.js"
            ),
            swagger_css_url=(
                "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.32.13/"
                "swagger-ui.css"
            ),
            swagger_favicon_url="/static/favicon.svg",
            swagger_ui_parameters={
                "deepLinking": True,
                "displayRequestDuration": True,
                "supportedSubmitMethods": ["get"],
            },
        )

    @application.get("/openapi.json", include_in_schema=False)
    async def openapi_document(request: Request) -> JSONResponse:
        _require_roles(request, APPLICATION_ROLES)
        return JSONResponse(application.openapi())

    return application


app = create_app()
configure_telemetry(app)


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
