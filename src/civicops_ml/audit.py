"""Bounded source audit for the NYC 311 dataset."""

from __future__ import annotations

import argparse
import calendar
import json
import math
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from .contract import (
    API_URL,
    DATASET_ID,
    LEAKAGE_FIELDS,
    MODEL_SOURCE_FIELDS,
    ContractError,
    slow_resolution_label,
    source_fields,
)


@dataclass(frozen=True)
class AuditConfig:
    start: datetime
    end: datetime
    as_of: datetime
    rows_per_window: int = 400
    sample_day: int = 15
    hours: tuple[int, ...] = (0, 6, 12, 18)
    workers: int = 8


def _request_json(url: str, attempts: int = 3) -> Any:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "CivicOps-ML-audit/0.1"},
    )
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except Exception as exc:  # pragma: no cover - network recovery
            last_error = exc
            time.sleep(attempt + 1)
    assert last_error is not None
    raise last_error


def _month_starts(start: datetime, end: datetime) -> Iterable[tuple[int, int]]:
    year, month = start.year, start.month
    while datetime(year, month, 1) < end:
        yield year, month
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1


def sample_windows(config: AuditConfig) -> list[tuple[datetime, datetime]]:
    """Build deterministic monthly windows distributed across each day."""

    windows: list[tuple[datetime, datetime]] = []
    for year, month in _month_starts(config.start, config.end):
        last_day = calendar.monthrange(year, month)[1]
        day = min(config.sample_day, last_day)
        for hour in config.hours:
            window_start = datetime(year, month, day, hour)
            window_end = window_start + timedelta(hours=6)
            if config.start <= window_start < config.end:
                windows.append((window_start, min(window_end, config.end)))
    return windows


def _sample_url(start: datetime, end: datetime, limit: int) -> str:
    select = source_fields() + LEAKAGE_FIELDS
    params = {
        "$select": ",".join(select),
        "$where": (
            f"created_date >= '{start.isoformat(timespec='milliseconds')}' "
            f"AND created_date < '{end.isoformat(timespec='milliseconds')}'"
        ),
        "$order": "created_date,unique_key",
        "$limit": str(limit),
    }
    return API_URL + "?" + urllib.parse.urlencode(params)


def fetch_full_cohort_aggregate(config: AuditConfig) -> list[dict[str, Any]]:
    """Ask Socrata to aggregate the full cohort without downloading raw rows."""

    select = (
        "date_extract_y(created_date) as year,"
        "count(*) as requests,"
        "sum(case when closed_date is null "
        "or date_diff_d(closed_date,created_date) > 7 "
        "then 1 else 0 end) as over_7d,"
        "sum(case when closed_date is not null "
        "and date_diff_d(closed_date,created_date) < 0 "
        "then 1 else 0 end) as negative_duration"
    )
    params = {
        "$select": select,
        "$where": (
            f"created_date >= '{config.start.isoformat(timespec='milliseconds')}' "
            f"AND created_date < '{config.end.isoformat(timespec='milliseconds')}'"
        ),
        "$group": "year",
        "$order": "year",
    }
    rows = _request_json(API_URL + "?" + urllib.parse.urlencode(params))
    return [
        {
            "year": int(row["year"]),
            "requests": int(row["requests"]),
            "positive": int(row["over_7d"]),
            "positive_rate": round(int(row["over_7d"]) / int(row["requests"]), 6),
            "negative_duration": int(row["negative_duration"]),
        }
        for row in rows
    ]


def fetch_sample(config: AuditConfig) -> list[dict[str, Any]]:
    """Fetch a bounded sample; raw records remain in memory only."""

    windows = sample_windows(config)
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=config.workers) as pool:
        futures = [
            pool.submit(_request_json, _sample_url(start, end, config.rows_per_window))
            for start, end in windows
        ]
        for future in as_completed(futures):
            rows.extend(future.result())
    return rows


def _percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    lower, upper = math.floor(index), math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - index) + ordered[upper] * (index - lower)


def summarize(rows: list[dict[str, Any]], as_of: datetime) -> dict[str, Any]:
    """Return aggregate quality, target, and feature findings."""

    target_by_year: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    durations: list[float] = []
    invalid_rows = 0
    thresholds: Counter[int] = Counter()

    for row in rows:
        created = datetime.fromisoformat(row["created_date"])
        closed_raw = row.get("closed_date")
        closed = datetime.fromisoformat(closed_raw) if closed_raw else None
        try:
            label = slow_resolution_label(created, closed, as_of)
        except ContractError:
            invalid_rows += 1
            continue
        if closed is not None:
            durations.append((closed - created).total_seconds() / 86400)
        target_by_year[str(created.year)][0] += 1
        target_by_year[str(created.year)][1] += label
        for days in (1, 3, 7, 14, 30):
            if closed is None or (closed - created).total_seconds() > days * 86400:
                thresholds[days] += 1

    valid_count = len(rows) - invalid_rows
    keys = [row.get("unique_key") for row in rows]

    return {
        "dataset_id": DATASET_ID,
        "sample_rows": len(rows),
        "valid_rows": valid_count,
        "invalid_rows": invalid_rows,
        "duplicate_keys": len(keys) - len(set(keys)),
        "target_by_year": {
            year: {
                "rows": counts[0],
                "positive": counts[1],
                "rate": round(counts[1] / counts[0], 6),
            }
            for year, counts in sorted(target_by_year.items())
        },
        "threshold_rates": {
            str(days): round(thresholds[days] / valid_count, 6)
            for days in (1, 3, 7, 14, 30)
        },
        "closed_duration_days": {
            f"p{int(probability * 100)}": round(value, 4) if value is not None else None
            for probability in (0.5, 0.75, 0.9, 0.95, 0.99)
            for value in [_percentile(durations, probability)]
        },
        "feature_missing_rates": {
            field: round(sum(not row.get(field) for row in rows) / len(rows), 6)
            for field in MODEL_SOURCE_FIELDS
        },
        "feature_cardinality": {
            field: len({row.get(field) for row in rows if row.get(field)})
            for field in MODEL_SOURCE_FIELDS
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2026-07-01")
    parser.add_argument("--as-of", default=datetime.now().date().isoformat())
    parser.add_argument("--rows-per-window", type=int, default=400)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    config = AuditConfig(
        start=datetime.fromisoformat(args.start),
        end=datetime.fromisoformat(args.end),
        as_of=datetime.fromisoformat(args.as_of),
        rows_per_window=args.rows_per_window,
    )
    result = summarize(fetch_sample(config), config.as_of)
    result["full_cohort_by_year"] = fetch_full_cohort_aggregate(config)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
