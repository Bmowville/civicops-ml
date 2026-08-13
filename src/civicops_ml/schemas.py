"""Validated API contracts for CivicOps ML."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

NYC_TIMEZONE = ZoneInfo("America/New_York")
MODEL_COHORT_START = datetime(2024, 1, 1, tzinfo=NYC_TIMEZONE)


def _clean_category(value: object, *, allow_unknown: bool) -> str:
    if value is None:
        if allow_unknown:
            return "Unknown"
        raise ValueError("value is required")
    if not isinstance(value, str):
        raise ValueError("value must be a string")
    if any(ord(character) < 32 for character in value):
        raise ValueError("control characters are not allowed")
    cleaned = " ".join(value.strip().split())
    if not cleaned:
        if allow_unknown:
            return "Unknown"
        raise ValueError("value is required")
    return cleaned


class PredictionRequest(BaseModel):
    """Creation-time fields accepted by the prediction service."""

    model_config = ConfigDict(extra="forbid", str_max_length=200)

    created_at: datetime = Field(
        description="Service-request creation time with an explicit UTC offset"
    )
    agency: str = Field(min_length=1, max_length=80)
    complaint_type: str = Field(min_length=1, max_length=160)
    descriptor: str | None = Field(default=None, max_length=200)
    location_type: str | None = Field(default=None, max_length=160)
    open_data_channel_type: str = Field(min_length=1, max_length=80)

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a UTC offset")
        local_value = value.astimezone(NYC_TIMEZONE)
        if local_value < MODEL_COHORT_START:
            raise ValueError("created_at predates the model's supported cohort")
        utc_value = value.astimezone(timezone.utc)
        if utc_value > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("created_at cannot be in the future")
        return value

    @field_validator(
        "agency",
        "complaint_type",
        "open_data_channel_type",
        mode="before",
    )
    @classmethod
    def normalize_required_categories(cls, value: object) -> str:
        return _clean_category(value, allow_unknown=False)

    @field_validator("descriptor", "location_type", mode="before")
    @classmethod
    def normalize_optional_categories(cls, value: object) -> str:
        return _clean_category(value, allow_unknown=True)

    def model_features(self) -> dict[str, str]:
        local_time = self.created_at.astimezone(NYC_TIMEZONE)
        return {
            "agency": self.agency,
            "complaint_type": self.complaint_type,
            "descriptor": self.descriptor or "Unknown",
            "location_type": self.location_type or "Unknown",
            "open_data_channel_type": self.open_data_channel_type,
            "created_hour": f"{local_time.hour:02d}",
            "created_day_of_week": str(local_time.weekday()),
            "created_month": f"{local_time.month:02d}",
            "created_is_weekend": str(int(local_time.weekday() >= 5)),
        }


class FeatureContribution(BaseModel):
    feature: str
    value: str
    contribution: float
    direction: Literal["higher", "lower"]


class PredictionResponse(BaseModel):
    prediction_id: str
    probability: float
    review_tier: Literal["standard_review", "priority_review"]
    above_review_threshold: bool
    operational_threshold: float
    human_review_required: Literal[True] = True
    explanation: list[FeatureContribution]
    input_warnings: list[str]
    explanation_notice: str
    score_notice: str
    model_sha256: str


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["escalate", "monitor", "standard_process"]
    rationale: str = Field(min_length=5, max_length=500)

    @field_validator("rationale", mode="before")
    @classmethod
    def clean_text(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("value must be a string")
        if any(ord(character) < 32 for character in value):
            raise ValueError("control characters are not allowed")
        cleaned = " ".join(value.strip().split())
        return cleaned


class ReviewResponse(BaseModel):
    review_id: str
    prediction_id: str
    action: Literal["escalate", "monitor", "standard_process"]
    recorded_at: datetime


class HealthResponse(BaseModel):
    status: Literal["ok"]
    model_loaded: bool
    storage_ready: bool
