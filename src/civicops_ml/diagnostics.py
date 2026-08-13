"""Explainability, drift, and calibration diagnostics for the candidate model."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from scipy.spatial.distance import jensenshannon
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss

from .calibration import (
    FittedCalibrator,
    fit_agency_aware,
    fit_global_platt,
    select_calibrator,
    weighted_absolute_group_gap,
)
from .metrics import classification_metrics, subgroup_metrics
from .modeling import NO_GEOGRAPHY_FEATURES


def categorical_drift(reference: pd.Series, current: pd.Series) -> dict[str, Any]:
    """Return Jensen-Shannon divergence and category coverage diagnostics."""

    reference = reference.fillna("Unknown").astype("string").fillna("Unknown")
    current = current.fillna("Unknown").astype("string").fillna("Unknown")
    categories = sorted(set(reference.unique()) | set(current.unique()))
    reference_counts = reference.value_counts(normalize=True).reindex(categories, fill_value=0.0)
    current_counts = current.value_counts(normalize=True).reindex(categories, fill_value=0.0)
    divergence = float(jensenshannon(reference_counts, current_counts, base=2) ** 2)
    differences = (current_counts - reference_counts).abs()
    largest = str(differences.idxmax())
    unseen = ~current.isin(set(reference.unique()))
    return {
        "jensen_shannon_divergence": round(divergence, 6),
        "unseen_category_rate": round(float(unseen.mean()), 6),
        "largest_shift_category": largest,
        "largest_absolute_share_shift": round(float(differences.loc[largest]), 6),
        "reference_share": round(float(reference_counts.loc[largest]), 6),
        "current_share": round(float(current_counts.loc[largest]), 6),
    }


def population_stability_index(
    reference: np.ndarray,
    current: np.ndarray,
    bins: int = 10,
) -> float:
    """Return PSI using reference-score quantile bins."""

    boundaries = np.unique(np.quantile(reference, np.linspace(0, 1, bins + 1)))
    if len(boundaries) < 3:
        return 0.0
    boundaries[0], boundaries[-1] = -np.inf, np.inf
    reference_counts = np.histogram(reference, bins=boundaries)[0].astype(float)
    current_counts = np.histogram(current, bins=boundaries)[0].astype(float)
    epsilon = 1e-6
    reference_rates = np.clip(reference_counts / reference_counts.sum(), epsilon, None)
    current_rates = np.clip(current_counts / current_counts.sum(), epsilon, None)
    return float(np.sum((current_rates - reference_rates) * np.log(current_rates / reference_rates)))


def coefficient_explanations(
    model: Any,
    training_frame: pd.DataFrame,
    limit: int = 20,
    minimum_supported_rows: int = 1_000,
) -> dict[str, Any]:
    """Map one-hot logistic coefficients back to source fields and categories."""

    transformer = model.named_steps["features"]
    feature_names = transformer.get_feature_names_out()
    coefficients = model.named_steps["model"].coef_[0]
    rows: list[dict[str, Any]] = []
    fields = sorted(NO_GEOGRAPHY_FEATURES, key=len, reverse=True)
    for encoded_name, coefficient in zip(feature_names, coefficients, strict=True):
        plain = encoded_name.split("__", 1)[-1]
        source = next((field for field in fields if plain.startswith(field + "_")), "unknown")
        category = plain[len(source) + 1 :] if source != "unknown" else plain
        category_rows = None
        category_share = None
        if source != "unknown" and category != "infrequent_sklearn":
            category_rows = int((training_frame[source].astype(str) == category).sum())
            category_share = round(category_rows / len(training_frame), 6)
        rows.append(
            {
                "feature": source,
                "category": category,
                "training_rows": category_rows,
                "training_share": category_share,
                "coefficient": round(float(coefficient), 6),
                "odds_multiplier": round(float(np.exp(np.clip(coefficient, -10, 10))), 6),
            }
        )
    ordered = sorted(rows, key=lambda row: row["coefficient"])
    supported = [
        row
        for row in ordered
        if row["training_rows"] is not None
        and row["training_rows"] >= minimum_supported_rows
    ]
    return {
        "intercept": round(float(model.named_steps["model"].intercept_[0]), 6),
        "encoded_features": len(rows),
        "most_negative": ordered[:limit],
        "most_positive": list(reversed(ordered[-limit:])),
        "most_negative_supported": supported[:limit],
        "most_positive_supported": list(reversed(supported[-limit:])),
        "minimum_supported_rows": minimum_supported_rows,
        "warning": (
            "Coefficients are conditional associations, not causal effects. "
            "Use the supported lists for interpretation; extreme coefficients "
            "for rare categories may be unstable."
        ),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def permutation_importance(
    model: Any,
    frame: pd.DataFrame,
    target: np.ndarray,
    seed: int = 3112026,
) -> list[dict[str, Any]]:
    """Measure validation metric degradation from one-feature permutations."""

    baseline = model.predict_proba(frame)[:, 1]
    baseline_ap = float(average_precision_score(target, baseline))
    baseline_brier = float(brier_score_loss(target, baseline))
    rng = np.random.default_rng(seed)
    output: list[dict[str, Any]] = []
    for feature in NO_GEOGRAPHY_FEATURES:
        permuted = frame.copy()
        permuted[feature] = rng.permutation(permuted[feature].to_numpy())
        probabilities = model.predict_proba(permuted)[:, 1]
        output.append(
            {
                "feature": feature,
                "average_precision_drop": round(
                    baseline_ap - float(average_precision_score(target, probabilities)),
                    6,
                ),
                "brier_increase": round(
                    float(brier_score_loss(target, probabilities)) - baseline_brier,
                    6,
                ),
            }
        )
    return sorted(output, key=lambda row: row["average_precision_drop"], reverse=True)


def _calibration_evaluation(
    target: np.ndarray,
    probabilities: np.ndarray,
    agencies: pd.Series,
) -> dict[str, float]:
    return {
        "brier": round(float(brier_score_loss(target, probabilities)), 6),
        "log_loss": round(float(log_loss(target, probabilities, labels=[0, 1])), 6),
        "weighted_absolute_agency_gap": round(
            weighted_absolute_group_gap(target, probabilities, agencies),
            6,
        ),
    }


def _monthly_metrics(frame: pd.DataFrame, probabilities: np.ndarray) -> list[dict[str, Any]]:
    working = pd.DataFrame(
        {
            "month": frame["created_date"].dt.to_period("M").astype(str).to_numpy(),
            "target": frame["target"].to_numpy(dtype=int),
            "probability": probabilities,
        }
    )
    output: list[dict[str, Any]] = []
    for month, group in working.groupby("month", observed=True):
        target = group["target"].to_numpy(dtype=int)
        probability = group["probability"].to_numpy(dtype=float)
        output.append(
            {
                "month": str(month),
                "rows": int(len(group)),
                "prevalence": round(float(target.mean()), 6),
                "mean_probability": round(float(probability.mean()), 6),
                "calibration_gap": round(float(probability.mean() - target.mean()), 6),
                "average_precision": round(float(average_precision_score(target, probability)), 6),
            }
        )
    return output


def run_diagnostics(
    extract_path: Path,
    candidate_path: Path,
    report_path: Path,
    calibrator_path: Path,
) -> dict[str, Any]:
    frame = pd.read_csv(extract_path, dtype=str, compression="gzip")
    frame["target"] = frame["target"].astype(int)
    frame["created_date"] = pd.to_datetime(frame["created_date"])
    model = joblib.load(candidate_path)

    train = frame[frame["split"] == "train"].copy()
    validation = frame[frame["split"] == "validation"].copy()
    test = frame[frame["split"] == "test"].copy()

    raw_predictions = {
        "train": model.predict_proba(train)[:, 1],
        "validation": model.predict_proba(validation)[:, 1],
        "test": model.predict_proba(test)[:, 1],
    }

    drift = {
        split_name: {
            "features": {
                feature: categorical_drift(train[feature], split_frame[feature])
                for feature in NO_GEOGRAPHY_FEATURES
            },
            "score_psi": round(
                population_stability_index(raw_predictions["train"], raw_predictions[split_name]),
                6,
            ),
        }
        for split_name, split_frame in (("validation", validation), ("test", test))
    }

    calibration_fit = validation[validation["created_date"] < datetime(2025, 10, 1)].copy()
    calibration_selection = validation[validation["created_date"] >= datetime(2025, 10, 1)].copy()
    fit_probabilities = model.predict_proba(calibration_fit)[:, 1]
    selection_probabilities = model.predict_proba(calibration_selection)[:, 1]
    y_fit = calibration_fit["target"].to_numpy(dtype=int)
    y_selection = calibration_selection["target"].to_numpy(dtype=int)

    provisional = {
        "global_platt": fit_global_platt(
            fit_probabilities,
            calibration_fit["agency"],
            y_fit,
        ),
        "agency_aware": fit_agency_aware(
            fit_probabilities,
            calibration_fit["agency"],
            y_fit,
        ),
    }
    selection_metrics: dict[str, dict[str, float]] = {
        "identity": _calibration_evaluation(
            y_selection,
            selection_probabilities,
            calibration_selection["agency"],
        )
    }
    for name, calibrator in provisional.items():
        calibrated = calibrator.predict(selection_probabilities, calibration_selection["agency"])
        selection_metrics[name] = _calibration_evaluation(
            y_selection,
            calibrated,
            calibration_selection["agency"],
        )
    selection = select_calibrator(selection_metrics)

    y_validation = validation["target"].to_numpy(dtype=int)
    if selection["selected"] == "agency_aware":
        final_calibrator = fit_agency_aware(
            raw_predictions["validation"],
            validation["agency"],
            y_validation,
        )
    elif selection["selected"] == "global_platt":
        final_calibrator = fit_global_platt(
            raw_predictions["validation"],
            validation["agency"],
            y_validation,
        )
    else:
        final_calibrator = FittedCalibrator("identity", None)
    calibrator_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(final_calibrator, calibrator_path)

    y_test = test["target"].to_numpy(dtype=int)
    calibrated_test = final_calibrator.predict(raw_predictions["test"], test["agency"])
    calibration = {
        "protocol": {
            "fit": "2025-07-01 through 2025-09-30",
            "selection": "2025-10-01 through 2025-12-31",
            "final_refit": "full 2025-07-01 through 2025-12-31 validation period",
            "evaluation": "2026-01-01 through 2026-06-30 test period",
        },
        "selection_metrics": selection_metrics,
        "selection": selection,
        "test": {
            "raw": classification_metrics(y_test, raw_predictions["test"]),
            "calibrated": classification_metrics(y_test, calibrated_test),
        },
        "test_agency_raw": subgroup_metrics(
            test,
            y_test,
            raw_predictions["test"],
            "agency",
        ),
        "test_agency_calibrated": subgroup_metrics(
            test,
            y_test,
            calibrated_test,
            "agency",
        ),
        "test_borough_calibrated": subgroup_metrics(
            test,
            y_test,
            calibrated_test,
            "borough",
        ),
    }

    result = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "candidate": "logistic_no_geography",
        "artifacts": {
            "model": {
                "path": candidate_path.as_posix(),
                "sha256": _sha256(candidate_path),
            },
            "modeling_extract": {
                "path": extract_path.as_posix(),
                "sha256": _sha256(extract_path),
                "rows": int(len(frame)),
            },
        },
        "explainability": {
            "coefficients": coefficient_explanations(model, train),
            "validation_permutation_importance": permutation_importance(
                model,
                validation,
                y_validation,
            ),
        },
        "drift": drift,
        "monthly_test_metrics": _monthly_metrics(test, calibrated_test),
        "calibration": calibration,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract", type=Path, default=Path("data/modeling.csv.gz"))
    parser.add_argument("--candidate", type=Path, default=Path("models/candidate.joblib"))
    parser.add_argument("--report", type=Path, default=Path("reports/diagnostics.json"))
    parser.add_argument("--calibrator", type=Path, default=Path("models/calibrator.joblib"))
    args = parser.parse_args()
    result = run_diagnostics(args.extract, args.candidate, args.report, args.calibrator)
    summary = {
        "candidate": result["candidate"],
        "calibrator": result["calibration"]["selection"],
        "test_raw": result["calibration"]["test"]["raw"],
        "test_calibrated": result["calibration"]["test"]["calibrated"],
        "permutation_importance": result["explainability"]["validation_permutation_importance"],
        "drift": result["drift"],
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
