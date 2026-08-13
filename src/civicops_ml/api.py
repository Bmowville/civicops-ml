"""FastAPI application for the CivicOps ML human-review workflow."""

from __future__ import annotations

import argparse
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import UUID

import uvicorn
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .schemas import (
    HealthResponse,
    PredictionRequest,
    PredictionResponse,
    ReviewRequest,
    ReviewResponse,
)
from .serving import PredictionService
from .store import AuditStore


def _path_from_env(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default))


def create_app(
    service: PredictionService | Any | None = None,
    store: AuditStore | None = None,
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
            application.state.store = AuditStore(
                _path_from_env("CIVICOPS_DB_PATH", "var/civicops.sqlite3")
            )
        yield

    application = FastAPI(
        title="CivicOps ML",
        description=(
            "Human-review API for seven-day NYC 311 resolution-risk estimates. "
            "Scores cannot make service decisions."
        ),
        version=__version__,
        lifespan=lifespan,
    )
    application.state.service = service
    application.state.store = store

    static_directory = Path(__file__).parent / "static"
    application.mount("/static", StaticFiles(directory=static_directory), name="static")

    @application.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    @application.get("/", include_in_schema=False)
    async def operator_console() -> FileResponse:
        return FileResponse(static_directory / "index.html")

    @application.get("/healthz", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        return HealthResponse(
            status="ok",
            model_loaded=request.app.state.service is not None,
            storage_ready=request.app.state.store.ready(),
        )

    @application.get("/api/v1/model")
    async def model_metadata(request: Request) -> dict[str, Any]:
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
        result = request.app.state.service.predict(prediction_request)
        prediction_id = request.app.state.store.record_prediction(
            prediction_request,
            result["probability"],
            result["review_tier"],
            result["model_sha256"],
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
        try:
            return request.app.state.store.record_review(str(prediction_id), review)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

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
