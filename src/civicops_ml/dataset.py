"""Reproducible modeling-extract builder for NYC 311 resolution risk."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from .contract import (
    API_URL,
    DATASET_ID,
    MODEL_SOURCE_FIELDS,
    ContractError,
    slow_resolution_label,
)

TRAIN_END = datetime(2025, 7, 1)
VALIDATION_END = datetime(2026, 1, 1)


@dataclass(frozen=True)
class ExtractConfig:
    start: datetime = datetime(2024, 1, 1)
    end: datetime = datetime(2026, 7, 1)
    as_of: datetime = datetime(2026, 8, 13)
    window_hours: int = 4
    windows_per_week: int = 3
    sampling_seed: int = 3112026
    row_limit: int = 10_000
    workers: int = 8


def rotating_weekly_windows(config: ExtractConfig) -> list[tuple[datetime, datetime]]:
    """Select three complete, distributed dayparts per week across the cohort.

    Over each 14-week cycle the schedule covers all seven weekdays and all six
    four-hour dayparts exactly once. A stable seeded shuffle assigns those
    strata to weeks, removing calendar-pattern correlation while keeping the
    extract reproducible. Every record within a selected window is requested,
    avoiding row-order or closed-only sampling bias.
    """

    windows: list[tuple[datetime, datetime]] = []
    week_start = config.start
    index = 0
    dayparts = 24 // config.window_hours
    strata = 7 * dayparts
    if config.windows_per_week != 3 or strata != 42:
        raise ValueError("the validated design requires three four-hour windows per week")

    while week_start < config.end:
        cycle = index // 14
        position = index % 14
        seed_bytes = hashlib.sha256(
            f"{config.sampling_seed}:{cycle}".encode("utf-8")
        ).digest()[:8]
        cycle_strata = list(range(strata))
        random.Random(int.from_bytes(seed_bytes, "big")).shuffle(cycle_strata)
        weekly_strata = cycle_strata[
            position * config.windows_per_week : (position + 1) * config.windows_per_week
        ]
        for stratum in weekly_strata:
            day_offset = stratum // dayparts
            hour = (stratum % dayparts) * config.window_hours
            start = week_start + timedelta(days=day_offset, hours=hour)
            if start < config.end:
                windows.append(
                    (start, min(start + timedelta(hours=config.window_hours), config.end))
                )
        week_start += timedelta(days=7)
        index += 1

    return windows


def _request_json(url: str, attempts: int = 4) -> Any:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "CivicOps-ML-extract/0.1"},
    )
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return json.load(response)
        except Exception as exc:  # pragma: no cover - network recovery
            last_error = exc
            time.sleep(2**attempt)
    assert last_error is not None
    raise last_error


def _window_url(start: datetime, end: datetime, limit: int) -> str:
    projection = ("unique_key", "created_date", "closed_date") + MODEL_SOURCE_FIELDS
    params = {
        "$select": ",".join(projection),
        "$where": (
            f"created_date >= '{start.isoformat(timespec='milliseconds')}' "
            f"AND created_date < '{end.isoformat(timespec='milliseconds')}'"
        ),
        "$order": "created_date,unique_key",
        "$limit": str(limit),
    }
    return API_URL + "?" + urllib.parse.urlencode(params)


def _population_aggregate_url(start: datetime, end: datetime) -> str:
    select = (
        "count(*) as requests,"
        "sum(case when closed_date is null "
        "or date_diff_d(closed_date,created_date) > 7 "
        "then 1 else 0 end) as positive,"
        "sum(case when closed_date is not null "
        "and date_diff_d(closed_date,created_date) < 0 "
        "then 1 else 0 end) as invalid"
    )
    params = {
        "$select": select,
        "$where": (
            f"created_date >= '{start.isoformat(timespec='milliseconds')}' "
            f"AND created_date < '{end.isoformat(timespec='milliseconds')}'"
        ),
    }
    return API_URL + "?" + urllib.parse.urlencode(params)


def fetch_population_split_summary(config: ExtractConfig) -> dict[str, dict[str, Any]]:
    """Return server-side population counts for the chronological splits."""

    intervals = {
        "train": (config.start, TRAIN_END),
        "validation": (TRAIN_END, VALIDATION_END),
        "test": (VALIDATION_END, config.end),
    }
    output: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(_request_json, _population_aggregate_url(start, end)): name
            for name, (start, end) in intervals.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            row = future.result()[0]
            requests = int(row["requests"])
            positive = int(row["positive"])
            invalid = int(row["invalid"])
            output[name] = {
                "rows": requests,
                "positive": positive,
                "prevalence": round(positive / requests, 6),
                "invalid_negative_duration": invalid,
            }
    return {name: output[name] for name in intervals}


def _fetch_window(
    window: tuple[datetime, datetime],
    row_limit: int,
) -> tuple[tuple[datetime, datetime], list[dict[str, Any]]]:
    start, end = window
    rows = _request_json(_window_url(start, end, row_limit))
    if len(rows) >= row_limit:
        raise RuntimeError(
            f"window {start.isoformat()} reached the {row_limit:,}-row safety limit"
        )
    return window, rows


def _split_name(created: datetime) -> str:
    if created < TRAIN_END:
        return "train"
    if created < VALIDATION_END:
        return "validation"
    return "test"


def _prepare_row(row: dict[str, Any], as_of: datetime) -> dict[str, Any] | None:
    created = datetime.fromisoformat(row["created_date"])
    closed_raw = row.get("closed_date")
    closed = datetime.fromisoformat(closed_raw) if closed_raw else None
    try:
        target = slow_resolution_label(created, closed, as_of)
    except ContractError:
        return None

    prepared: dict[str, Any] = {
        "created_date": created.isoformat(timespec="seconds"),
        "split": _split_name(created),
        "target": target,
        "created_hour": f"{created.hour:02d}",
        "created_day_of_week": str(created.weekday()),
        "created_month": f"{created.month:02d}",
        "created_is_weekend": str(int(created.weekday() >= 5)),
    }
    prepared.update({field: row.get(field) or "Unknown" for field in MODEL_SOURCE_FIELDS})
    return prepared


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_extract(
    config: ExtractConfig,
    output: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    """Download selected windows and write only model-ready, de-identified rows."""

    windows = rotating_weekly_windows(config)
    fetched: list[tuple[tuple[datetime, datetime], list[dict[str, Any]]]] = []
    with ThreadPoolExecutor(max_workers=config.workers) as pool:
        futures = [pool.submit(_fetch_window, window, config.row_limit) for window in windows]
        for future in as_completed(futures):
            fetched.append(future.result())

    fetched.sort(key=lambda item: item[0][0])
    source_rows = [row for _, rows in fetched for row in rows]
    keys = [row["unique_key"] for row in source_rows]
    duplicate_keys = len(keys) - len(set(keys))
    if duplicate_keys:
        raise RuntimeError(f"extract contains {duplicate_keys} duplicate service-request keys")

    prepared_rows: list[dict[str, Any]] = []
    invalid_rows = 0
    for row in source_rows:
        prepared = _prepare_row(row, config.as_of)
        if prepared is None:
            invalid_rows += 1
        else:
            prepared_rows.append(prepared)

    frame = pd.DataFrame(prepared_rows).sort_values(["created_date"], kind="stable")
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False, compression="gzip")

    split_summary = {
        split: {
            "rows": int(len(group)),
            "positive": int(group["target"].sum()),
            "prevalence": round(float(group["target"].mean()), 6),
            "start": str(group["created_date"].min()),
            "end": str(group["created_date"].max()),
        }
        for split, group in frame.groupby("split", observed=True)
    }
    manifest = {
        "dataset_id": DATASET_ID,
        "extracted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "as_of": config.as_of.isoformat(timespec="seconds"),
        "sampling": {
            "method": "seeded stratified selection of three complete four-hour windows per week",
            "seed": config.sampling_seed,
            "windows": len(windows),
            "window_hours": config.window_hours,
            "coverage_fraction": round(
                config.windows_per_week * config.window_hours / (7 * 24),
                6,
            ),
            "maximum_rows_per_window": max(len(rows) for _, rows in fetched),
        },
        "source_rows": len(source_rows),
        "invalid_rows_rejected": invalid_rows,
        "modeling_rows": len(frame),
        "duplicate_keys": duplicate_keys,
        "split_summary": split_summary,
        "population_split_summary": fetch_population_split_summary(config),
        "output": str(output.as_posix()),
        "sha256": _sha256(output),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/modeling.csv.gz"))
    parser.add_argument("--manifest", type=Path, default=Path("data/extract_manifest.json"))
    args = parser.parse_args()
    print(json.dumps(build_extract(ExtractConfig(), args.output, args.manifest), indent=2))


if __name__ == "__main__":
    main()
