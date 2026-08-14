"""Build the privacy-safe reference contract for production model monitoring."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd


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


if __name__ == "__main__":
    main()
