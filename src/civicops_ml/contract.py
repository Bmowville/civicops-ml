"""Data contract for creation-time NYC 311 resolution-risk predictions."""

from __future__ import annotations

from datetime import datetime, timedelta

DATASET_ID = "erm2-nwe9"
DATASET_URL = f"https://data.cityofnewyork.us/d/{DATASET_ID}"
API_URL = f"https://data.cityofnewyork.us/resource/{DATASET_ID}.json"

TARGET_HORIZON = timedelta(days=7)

# Every model feature must be knowable when the service request is created.
MODEL_SOURCE_FIELDS = (
    "agency",
    "complaint_type",
    "descriptor",
    "location_type",
    "incident_zip",
    "borough",
    "open_data_channel_type",
    "community_board",
)

DERIVED_CREATION_FIELDS = (
    "created_hour",
    "created_day_of_week",
    "created_month",
    "created_is_weekend",
)

LABEL_ONLY_FIELDS = ("created_date", "closed_date")
AUDIT_ONLY_FIELDS = ("unique_key",)

# These values either occur after creation or encode an agency outcome/SLA.
LEAKAGE_FIELDS = (
    "status",
    "due_date",
    "resolution_description",
    "resolution_action_updated_date",
)

# Fine-grained locations are unnecessary for the initial model and create
# avoidable privacy, memorization, and proxy-discrimination risks.
EXCLUDED_LOCATION_FIELDS = (
    "incident_address",
    "street_name",
    "cross_street_1",
    "cross_street_2",
    "intersection_street_1",
    "intersection_street_2",
    "bbl",
    "latitude",
    "longitude",
    "location",
)


class ContractError(ValueError):
    """Raised when a source row cannot receive a reliable label."""


def parse_timestamp(value: str | datetime) -> datetime:
    """Parse a Socrata floating timestamp without inventing a timezone."""

    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def is_label_mature(
    created_at: str | datetime,
    as_of: str | datetime,
    *,
    horizon: timedelta = TARGET_HORIZON,
) -> bool:
    """Return whether the outcome horizon has elapsed at extraction time."""

    created = parse_timestamp(created_at)
    observed_at = parse_timestamp(as_of)
    return observed_at - created >= horizon


def slow_resolution_label(
    created_at: str | datetime,
    closed_at: str | datetime | None,
    as_of: str | datetime,
    *,
    horizon: timedelta = TARGET_HORIZON,
) -> int:
    """Return 1 when a mature request was not closed within the horizon."""

    created = parse_timestamp(created_at)
    observed_at = parse_timestamp(as_of)

    if created > observed_at:
        raise ContractError("created_at occurs after the extraction timestamp")
    if observed_at - created < horizon:
        raise ContractError("request has not reached the target horizon")

    if closed_at is None:
        return 1

    closed = parse_timestamp(closed_at)
    if closed < created:
        raise ContractError("closed_at occurs before created_at")
    if closed > observed_at:
        raise ContractError("closed_at occurs after the extraction timestamp")

    return int(closed - created > horizon)


def source_fields() -> tuple[str, ...]:
    """Return the minimal source projection used for extraction and labeling."""

    return AUDIT_ONLY_FIELDS + LABEL_ONLY_FIELDS + MODEL_SOURCE_FIELDS


def assert_no_feature_leakage() -> None:
    """Fail fast if a future-derived field is added to the feature contract."""

    features = set(MODEL_SOURCE_FIELDS) | set(DERIVED_CREATION_FIELDS)
    overlap = features & (set(LEAKAGE_FIELDS) | set(LABEL_ONLY_FIELDS))
    if overlap:
        raise ContractError(f"leakage fields present in model features: {sorted(overlap)}")
