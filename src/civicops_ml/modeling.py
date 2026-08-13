"""Chronological baseline training and evaluation for CivicOps ML."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from .contract import DERIVED_CREATION_FIELDS, MODEL_SOURCE_FIELDS, assert_no_feature_leakage
from .metrics import (
    calibration_bins,
    classification_metrics,
    subgroup_metrics,
    threshold_metrics,
)

GEOGRAPHY_FIELDS = ("incident_zip", "borough", "community_board")
FULL_FEATURES = MODEL_SOURCE_FIELDS + DERIVED_CREATION_FIELDS
NO_GEOGRAPHY_FEATURES = tuple(field for field in FULL_FEATURES if field not in GEOGRAPHY_FIELDS)


@dataclass
class HistoricalRateBaseline:
    """Smoothed agency/problem history using training labels only."""

    pair_strength: float = 50.0
    agency_strength: float = 100.0

    def fit(self, frame: pd.DataFrame, target: np.ndarray) -> "HistoricalRateBaseline":
        working = frame[["agency", "complaint_type"]].copy()
        working["target"] = target
        self.global_rate_ = float(working["target"].mean())

        agency = working.groupby("agency", observed=True)["target"].agg(["sum", "count"])
        agency["rate"] = (
            agency["sum"] + self.agency_strength * self.global_rate_
        ) / (agency["count"] + self.agency_strength)
        self.agency_rates_ = agency["rate"].to_dict()

        pair = working.groupby(["agency", "complaint_type"], observed=True)["target"].agg(
            ["sum", "count"]
        )
        pair_prior = np.array(
            [self.agency_rates_.get(index[0], self.global_rate_) for index in pair.index]
        )
        pair["rate"] = (pair["sum"].to_numpy() + self.pair_strength * pair_prior) / (
            pair["count"].to_numpy() + self.pair_strength
        )
        self.pair_rates_ = pair["rate"].to_dict()
        return self

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        return np.array(
            [
                self.pair_rates_.get(
                    (agency, problem),
                    self.agency_rates_.get(agency, self.global_rate_),
                )
                for agency, problem in zip(
                    frame["agency"].astype(str),
                    frame["complaint_type"].astype(str),
                    strict=True,
                )
            ],
            dtype=float,
        )


def build_logistic_pipeline(features: tuple[str, ...]) -> Pipeline:
    encoder = OneHotEncoder(
        handle_unknown="infrequent_if_exist",
        min_frequency=25,
        sparse_output=True,
    )
    transform = ColumnTransformer(
        [("categorical", encoder, list(features))],
        remainder="drop",
    )
    model = LogisticRegression(
        C=1.0,
        solver="lbfgs",
        max_iter=500,
        tol=1e-5,
    )
    return Pipeline([("features", transform), ("model", model)])


def _load_extract(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str, compression="gzip")
    frame["target"] = frame["target"].astype(int)
    frame["created_date"] = pd.to_datetime(frame["created_date"])
    required = {"created_date", "split", "target"} | set(FULL_FEATURES)
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"modeling extract is missing columns: {sorted(missing)}")
    if frame.duplicated().all():
        raise ValueError("modeling extract contains only duplicate rows")
    return frame


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _model_predictions(
    models: dict[str, Any],
    frame: pd.DataFrame,
) -> dict[str, np.ndarray]:
    output: dict[str, np.ndarray] = {}
    for name, model in models.items():
        if name == "constant_prevalence":
            output[name] = np.full(len(frame), model, dtype=float)
        elif name == "historical_rate":
            output[name] = model.predict_proba(frame)
        else:
            output[name] = model.predict_proba(frame)[:, 1]
    return output


def select_candidate(
    y_validation: np.ndarray,
    predictions: dict[str, np.ndarray],
) -> dict[str, Any]:
    """Prefer the geography-free model when validation quality is equivalent.

    The tolerance is fixed before consulting test metrics: no-geography must be
    within 0.005 average precision and 0.001 Brier score of the full model.
    """

    full_ap = float(average_precision_score(y_validation, predictions["logistic_full"]))
    no_geo_ap = float(
        average_precision_score(y_validation, predictions["logistic_no_geography"])
    )
    full_brier = float(brier_score_loss(y_validation, predictions["logistic_full"]))
    no_geo_brier = float(
        brier_score_loss(y_validation, predictions["logistic_no_geography"])
    )
    choose_no_geo = no_geo_ap >= full_ap - 0.005 and no_geo_brier <= full_brier + 0.001
    return {
        "selected": "logistic_no_geography" if choose_no_geo else "logistic_full",
        "selected_on": "validation only",
        "rule": (
            "prefer no-geography when average precision is within 0.005 and "
            "Brier score is within 0.001 of the full model"
        ),
        "validation_comparison": {
            "logistic_full": {
                "average_precision": round(full_ap, 6),
                "brier": round(full_brier, 6),
            },
            "logistic_no_geography": {
                "average_precision": round(no_geo_ap, 6),
                "brier": round(no_geo_brier, 6),
            },
        },
    }


def train_and_evaluate(
    extract_path: Path,
    extract_manifest_path: Path,
    report_path: Path,
    model_directory: Path,
) -> dict[str, Any]:
    """Fit on 2024–June 2025, tune capacity on validation, test on 2026."""

    assert_no_feature_leakage()
    frame = _load_extract(extract_path)
    extract_manifest = json.loads(extract_manifest_path.read_text(encoding="utf-8"))
    if extract_manifest["sha256"] != _sha256(extract_path):
        raise ValueError("modeling extract does not match its manifest")
    train = frame[frame["split"] == "train"].copy()
    validation = frame[frame["split"] == "validation"].copy()
    test = frame[frame["split"] == "test"].copy()
    if min(len(train), len(validation), len(test)) == 0:
        raise ValueError("train, validation, and test splits must all be non-empty")
    if not (train["created_date"].max() < validation["created_date"].min()):
        raise ValueError("training and validation periods overlap")
    if not (validation["created_date"].max() < test["created_date"].min()):
        raise ValueError("validation and test periods overlap")

    y_train = train["target"].to_numpy(dtype=int)
    y_validation = validation["target"].to_numpy(dtype=int)
    y_test = test["target"].to_numpy(dtype=int)

    historical = HistoricalRateBaseline().fit(train, y_train)
    logistic_full = build_logistic_pipeline(FULL_FEATURES).fit(train, y_train)
    logistic_no_geo = build_logistic_pipeline(NO_GEOGRAPHY_FEATURES).fit(train, y_train)
    models: dict[str, Any] = {
        "constant_prevalence": float(y_train.mean()),
        "historical_rate": historical,
        "logistic_full": logistic_full,
        "logistic_no_geography": logistic_no_geo,
    }

    validation_predictions = _model_predictions(models, validation)
    test_predictions = _model_predictions(models, test)
    candidate_selection = select_candidate(y_validation, validation_predictions)
    candidate_name = candidate_selection["selected"]

    # Freeze the candidate model's validation-derived score threshold, then apply
    # that unchanged threshold to the untouched test period.
    operational_threshold = float(np.quantile(validation_predictions[candidate_name], 0.90))

    results: dict[str, Any] = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "extract": {
            "path": extract_path.as_posix(),
            "sha256": _sha256(extract_path),
            "rows": int(len(frame)),
            "sampling": extract_manifest["sampling"],
            "population_split_summary": extract_manifest["population_split_summary"],
        },
        "environment": {
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "scikit_learn": sklearn.__version__,
        },
        "features": {
            "full": list(FULL_FEATURES),
            "no_geography": list(NO_GEOGRAPHY_FEATURES),
        },
        "splits": {
            split_name: {
                "rows": int(len(split_frame)),
                "start": split_frame["created_date"].min().isoformat(),
                "end": split_frame["created_date"].max().isoformat(),
                "prevalence": round(float(split_frame["target"].mean()), 6),
            }
            for split_name, split_frame in (
                ("train", train),
                ("validation", validation),
                ("test", test),
            )
        },
        "models": {},
        "candidate_selection": candidate_selection,
        "operational_threshold": {
            "selected_on": "validation",
            "model": candidate_name,
            "selection_rule": "90th percentile of candidate validation scores",
            "validation": threshold_metrics(
                y_validation,
                validation_predictions[candidate_name],
                operational_threshold,
            ),
            "test": threshold_metrics(
                y_test,
                test_predictions[candidate_name],
                operational_threshold,
            ),
        },
        "test_subgroups": {
            "agency": subgroup_metrics(
                test,
                y_test,
                test_predictions[candidate_name],
                "agency",
            ),
            "borough": subgroup_metrics(
                test,
                y_test,
                test_predictions[candidate_name],
                "borough",
            ),
        },
        "test_calibration_bins": calibration_bins(
            y_test,
            test_predictions[candidate_name],
        ),
    }

    for name in models:
        results["models"][name] = {
            "validation": classification_metrics(y_validation, validation_predictions[name]),
            "test": classification_metrics(y_test, test_predictions[name]),
        }

    model_directory.mkdir(parents=True, exist_ok=True)
    joblib.dump(logistic_full, model_directory / "logistic_full.joblib")
    joblib.dump(logistic_no_geo, model_directory / "logistic_no_geography.joblib")
    joblib.dump(models[candidate_name], model_directory / "candidate.joblib")
    joblib.dump(historical, model_directory / "historical_rate.joblib")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract", type=Path, default=Path("data/modeling.csv.gz"))
    parser.add_argument(
        "--extract-manifest",
        type=Path,
        default=Path("data/extract_manifest.json"),
    )
    parser.add_argument("--report", type=Path, default=Path("reports/baseline_metrics.json"))
    parser.add_argument("--models", type=Path, default=Path("models"))
    args = parser.parse_args()
    print(
        json.dumps(
            train_and_evaluate(
                args.extract,
                args.extract_manifest,
                args.report,
                args.models,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
