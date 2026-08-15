"""Build the privacy-safe reference contract for production model monitoring."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

import joblib
import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from .store import AuditStoreProtocol


MONITORED_FEATURES = (
    "agency",
    "complaint_type",
    "descriptor",
    "location_type",
    "open_data_channel_type",
)

DEFAULT_THRESHOLDS = {
    "minimum_predictions": 100,
    "score_psi": {"warning": 0.1, "critical": 0.2},
    "feature_jensen_shannon": {"warning": 0.05, "critical": 0.1},
    "unseen_category_rate": {"warning": 0.01, "critical": 0.05},
    "review_completion_rate": {"warning_below": 0.95, "critical_below": 0.9},
    "model_hash_mismatch_count": {"critical_above": 0},
}

STATUS_RANK = {
    "ok": 0,
    "warning": 1,
    "insufficient_data": 2,
    "critical": 3,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _distribution(values: pd.Series) -> dict[str, float]:
    normalized = values.fillna("Unknown").astype("string").fillna("Unknown")
    shares = normalized.value_counts(normalize=True, sort=False)
    return {
        str(category): round(float(share), 10)
        for category, share in sorted(shares.items(), key=lambda item: str(item[0]))
    }


def _score_reference(probabilities: np.ndarray, bins: int = 10) -> dict[str, Any]:
    scores = np.asarray(probabilities, dtype=float)
    if scores.ndim != 1 or len(scores) == 0:
        raise ValueError("reference scores must be a non-empty one-dimensional array")
    if not np.isfinite(scores).all() or ((scores < 0) | (scores > 1)).any():
        raise ValueError("reference scores must be finite probabilities")

    quantiles = np.unique(np.quantile(scores, np.linspace(0, 1, bins + 1)))
    if len(quantiles) < 3:
        raise ValueError("reference scores need at least three distinct quantile boundaries")
    internal_edges = quantiles[1:-1]
    histogram_edges = np.concatenate(([-np.inf], internal_edges, [np.inf]))
    counts = np.histogram(scores, bins=histogram_edges)[0]
    shares = counts / counts.sum()
    return {
        "mean": round(float(scores.mean()), 10),
        "internal_bin_edges": [round(float(value), 10) for value in internal_edges],
        "bin_shares": [round(float(value), 10) for value in shares],
    }


def _normalized(values: np.ndarray, *, epsilon: float = 1e-12) -> np.ndarray:
    clipped = np.clip(np.asarray(values, dtype=float), epsilon, None)
    return clipped / clipped.sum()


def _jensen_shannon(
    reference: Mapping[str, float],
    observed_values: Sequence[str],
) -> float:
    observed_counts_by_category = Counter(observed_values)
    categories = sorted({*reference, *observed_counts_by_category})
    reference_shares = np.asarray(
        [float(reference.get(category, 0.0)) for category in categories]
    )
    observed_counts = np.asarray(
        [observed_counts_by_category[category] for category in categories],
        dtype=float,
    )
    reference_distribution = _normalized(reference_shares)
    observed_distribution = _normalized(observed_counts)
    midpoint = (reference_distribution + observed_distribution) / 2
    divergence = 0.5 * np.sum(
        reference_distribution * np.log(reference_distribution / midpoint)
    ) + 0.5 * np.sum(
        observed_distribution * np.log(observed_distribution / midpoint)
    )
    return float(divergence)


def _population_stability_index(
    reference_shares: Sequence[float],
    internal_edges: Sequence[float],
    observed_scores: Sequence[float],
) -> float:
    scores = np.asarray(observed_scores, dtype=float)
    if not np.isfinite(scores).all() or ((scores < 0) | (scores > 1)).any():
        raise ValueError("production scores must be finite probabilities")
    edges = np.asarray([-np.inf, *internal_edges, np.inf], dtype=float)
    observed_counts = np.histogram(scores, bins=edges)[0]
    reference_distribution = _normalized(np.asarray(reference_shares, dtype=float))
    observed_distribution = _normalized(observed_counts.astype(float))
    if len(reference_distribution) != len(observed_distribution):
        raise ValueError("monitoring score bins do not match the reference contract")
    return float(
        np.sum(
            (observed_distribution - reference_distribution)
            * np.log(observed_distribution / reference_distribution)
        )
    )


def _status_above(value: float, thresholds: Mapping[str, float]) -> str:
    if value >= float(thresholds["critical"]):
        return "critical"
    if value >= float(thresholds["warning"]):
        return "warning"
    return "ok"


def _status_below(value: float, thresholds: Mapping[str, float]) -> str:
    if value < float(thresholds["critical_below"]):
        return "critical"
    if value < float(thresholds["warning_below"]):
        return "warning"
    return "ok"


def _worst_status(statuses: Sequence[str]) -> str:
    return max(statuses, key=lambda status: STATUS_RANK[status])


def build_monitoring_report(
    baseline: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    *,
    generated_at: datetime | None = None,
    window_start: datetime | None = None,
) -> dict[str, Any]:
    """Compare aggregate production activity with the committed reference."""

    if baseline.get("schema_version") != 1:
        raise ValueError("unsupported monitoring baseline schema")
    expected_model_sha256 = str(baseline.get("model_sha256", ""))
    if len(expected_model_sha256) != 64:
        raise ValueError("monitoring baseline has an invalid model digest")

    features = baseline.get("features")
    scores = baseline.get("scores")
    thresholds = baseline.get("thresholds")
    if not isinstance(features, Mapping) or not isinstance(scores, Mapping):
        raise ValueError("monitoring baseline is missing reference distributions")
    if not isinstance(thresholds, Mapping):
        raise ValueError("monitoring baseline is missing thresholds")

    requests: list[Mapping[str, Any]] = []
    probabilities: list[float] = []
    model_hashes: list[str] = []
    reviewed: list[bool] = []
    for record in records:
        request = record.get("request")
        if not isinstance(request, Mapping):
            raise ValueError("monitoring record request must be an object")
        requests.append(request)
        probabilities.append(float(record["probability"]))
        model_hashes.append(str(record["model_sha256"]))
        reviewed.append(bool(record["reviewed"]))

    prediction_count = len(records)
    review_count = sum(reviewed)
    review_completion_rate = (
        review_count / prediction_count if prediction_count else None
    )
    mismatch_count = sum(
        digest != expected_model_sha256 for digest in model_hashes
    )
    minimum_predictions = int(thresholds["minimum_predictions"])
    enough_data = prediction_count >= minimum_predictions
    integrity_status = "critical" if mismatch_count else "ok"

    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": (generated_at or datetime.now(timezone.utc)).isoformat(
            timespec="seconds"
        ),
        "window": {
            "start": (
                window_start.astimezone(timezone.utc).isoformat(timespec="seconds")
                if window_start is not None
                else None
            ),
            "prediction_count": prediction_count,
            "review_count": review_count,
        },
        "model_sha256": expected_model_sha256,
        "overall_status": "ok",
        "checks": {
            "sample_size": {
                "minimum_predictions": minimum_predictions,
                "observed_predictions": prediction_count,
                "status": "ok" if enough_data else "insufficient_data",
            },
            "model_integrity": {
                "mismatch_count": mismatch_count,
                "status": integrity_status,
            },
            "review_completion": {
                "rate": (
                    round(float(review_completion_rate), 10)
                    if review_completion_rate is not None
                    else None
                ),
                "status": "insufficient_data",
            },
            "score_drift": {
                "psi": None,
                "status": "insufficient_data",
            },
            "feature_drift": {
                feature: {
                    "jensen_shannon": None,
                    "unseen_category_rate": None,
                    "status": "insufficient_data",
                }
                for feature in MONITORED_FEATURES
            },
        },
        "privacy": {
            "output": "aggregate metrics only",
            "excluded": [
                "prediction_identifier",
                "actor_identity",
                "review_rationale",
                "service_request_identifier",
                "address",
            ],
        },
    }

    if not enough_data:
        report["overall_status"] = (
            "critical" if integrity_status == "critical" else "insufficient_data"
        )
        return report

    review_status = _status_below(
        float(review_completion_rate),
        thresholds["review_completion_rate"],
    )
    score_psi = _population_stability_index(
        scores["bin_shares"],
        scores["internal_bin_edges"],
        probabilities,
    )
    score_status = _status_above(score_psi, thresholds["score_psi"])
    report["checks"]["review_completion"]["status"] = review_status
    report["checks"]["score_drift"] = {
        "psi": round(score_psi, 10),
        "status": score_status,
    }

    feature_statuses: list[str] = []
    for feature in MONITORED_FEATURES:
        feature_reference = features.get(feature)
        if not isinstance(feature_reference, Mapping) or not isinstance(
            feature_reference.get("shares"), Mapping
        ):
            raise ValueError(f"monitoring baseline is missing feature {feature}")
        values = [str(request.get(feature) or "Unknown") for request in requests]
        reference_shares = feature_reference["shares"]
        divergence = _jensen_shannon(reference_shares, values)
        unseen_count = sum(value not in reference_shares for value in values)
        unseen_rate = unseen_count / prediction_count
        divergence_status = _status_above(
            divergence,
            thresholds["feature_jensen_shannon"],
        )
        unseen_status = _status_above(
            unseen_rate,
            thresholds["unseen_category_rate"],
        )
        feature_status = _worst_status([divergence_status, unseen_status])
        feature_statuses.append(feature_status)
        report["checks"]["feature_drift"][feature] = {
            "jensen_shannon": round(divergence, 10),
            "unseen_category_rate": round(unseen_rate, 10),
            "status": feature_status,
        }

    report["overall_status"] = _worst_status(
        [integrity_status, review_status, score_status, *feature_statuses]
    )
    return report


def build_store_monitoring_report(
    store: AuditStoreProtocol,
    baseline_path: Path,
    *,
    window_days: int = 30,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a production report without selecting identities or rationales."""

    if window_days < 1:
        raise ValueError("monitoring window must be at least one day")
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("monitoring time must include a UTC offset")
    current_time = current_time.astimezone(timezone.utc)
    window_start = current_time - timedelta(days=window_days)
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    records = store.monitoring_snapshot(since=window_start)
    return build_monitoring_report(
        baseline,
        records,
        generated_at=current_time,
        window_start=window_start,
    )


def build_monitoring_baseline(
    frame: pd.DataFrame,
    model: Any,
    *,
    model_sha256: str,
    extract_sha256: str,
) -> dict[str, Any]:
    """Return a model-bound reference contract using only the training split."""

    required = {"split", "created_date", *MONITORED_FEATURES}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"monitoring baseline is missing columns: {sorted(missing)}")

    training = frame.loc[frame["split"] == "train"].copy()
    if training.empty:
        raise ValueError("monitoring baseline requires a non-empty training split")
    probabilities = model.predict_proba(training)[:, 1]
    created_dates = pd.to_datetime(training["created_date"], utc=True)

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_sha256": model_sha256,
        "extract_sha256": extract_sha256,
        "reference": {
            "split": "train",
            "rows": int(len(training)),
            "start": created_dates.min().isoformat(),
            "end": created_dates.max().isoformat(),
        },
        "features": {
            feature: {"shares": _distribution(training[feature])}
            for feature in MONITORED_FEATURES
        },
        "scores": _score_reference(probabilities),
        "thresholds": DEFAULT_THRESHOLDS,
        "privacy": {
            "included": [*MONITORED_FEATURES, "prediction_probability"],
            "excluded": [
                "service_request_identifier",
                "address",
                "actor_identity",
                "review_rationale",
            ],
        },
    }


def generate_monitoring_baseline(
    extract_path: Path,
    model_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    frame = pd.read_csv(extract_path, dtype=str, compression="gzip")
    model = joblib.load(model_path)
    baseline = build_monitoring_baseline(
        frame,
        model,
        model_sha256=_sha256(model_path),
        extract_sha256=_sha256(extract_path),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(baseline, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return baseline


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract", type=Path, default=Path("data/modeling.csv.gz"))
    parser.add_argument("--model", type=Path, default=Path("models/candidate.joblib"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/monitoring_baseline.json"),
    )
    args = parser.parse_args()
    baseline = generate_monitoring_baseline(args.extract, args.model, args.output)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "reference_rows": baseline["reference"]["rows"],
                "model_sha256": baseline["model_sha256"],
            },
            indent=2,
        )
    )


def production_main() -> None:
    """Generate a privacy-safe aggregate report from the configured audit store."""

    from dotenv import load_dotenv

    from .store import create_audit_store

    load_dotenv(".env.local", override=False)
    parser = argparse.ArgumentParser(description=production_main.__doc__)
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path(
            os.environ.get(
                "CIVICOPS_MONITORING_BASELINE_PATH",
                "reports/monitoring_baseline.json",
            )
        ),
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=int(os.environ.get("CIVICOPS_MONITORING_WINDOW_DAYS", "30")),
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fail-on-critical", action="store_true")
    args = parser.parse_args()

    store = create_audit_store()
    try:
        report = build_store_monitoring_report(
            store,
            args.baseline,
            window_days=args.window_days,
        )
    finally:
        store.close()
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    if args.fail_on_critical and report["overall_status"] == "critical":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
