"""Evaluation metrics for imbalanced, probability-producing classifiers."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    log_loss,
    roc_auc_score,
)


def expected_calibration_error(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    bins: int = 10,
) -> float:
    """Return fixed-width expected calibration error."""

    edges = np.linspace(0.0, 1.0, bins + 1)
    assignments = np.minimum(np.digitize(probabilities, edges[1:-1]), bins - 1)
    error = 0.0
    for index in range(bins):
        mask = assignments == index
        if mask.any():
            error += float(mask.mean()) * abs(
                float(y_true[mask].mean()) - float(probabilities[mask].mean())
            )
    return error


def calibration_bins(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    bins: int = 10,
) -> list[dict[str, Any]]:
    """Return fixed-width calibration-bin data suitable for reports."""

    edges = np.linspace(0.0, 1.0, bins + 1)
    assignments = np.minimum(np.digitize(probabilities, edges[1:-1]), bins - 1)
    output: list[dict[str, Any]] = []
    for index in range(bins):
        mask = assignments == index
        if mask.any():
            output.append(
                {
                    "lower": round(float(edges[index]), 4),
                    "upper": round(float(edges[index + 1]), 4),
                    "rows": int(mask.sum()),
                    "mean_probability": round(float(probabilities[mask].mean()), 6),
                    "observed_rate": round(float(y_true[mask].mean()), 6),
                }
            )
    return output


def top_fraction_metrics(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    fraction: float = 0.10,
) -> dict[str, float | int]:
    """Measure an exact review capacity, even when prediction scores tie."""

    count = max(1, int(np.ceil(len(y_true) * fraction)))
    order = np.argsort(-probabilities, kind="stable")[:count]
    true_positives = int(y_true[order].sum())
    total_positives = int(y_true.sum())
    return {
        "reviewed": count,
        "review_rate": round(count / len(y_true), 6),
        "precision": round(true_positives / count, 6),
        "recall": round(true_positives / total_positives, 6) if total_positives else 0.0,
    }


def classification_metrics(
    y_true: np.ndarray,
    probabilities: np.ndarray,
) -> dict[str, Any]:
    """Return ranking, probability, and capacity metrics."""

    return {
        "rows": int(len(y_true)),
        "positives": int(y_true.sum()),
        "prevalence": round(float(y_true.mean()), 6),
        "mean_probability": round(float(probabilities.mean()), 6),
        "average_precision": round(float(average_precision_score(y_true, probabilities)), 6),
        "roc_auc": round(float(roc_auc_score(y_true, probabilities)), 6),
        "brier": round(float(brier_score_loss(y_true, probabilities)), 6),
        "log_loss": round(float(log_loss(y_true, probabilities, labels=[0, 1])), 6),
        "ece_10": round(float(expected_calibration_error(y_true, probabilities)), 6),
        "top_10_percent": top_fraction_metrics(y_true, probabilities),
    }


def threshold_metrics(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    threshold: float,
) -> dict[str, float | int]:
    selected = probabilities >= threshold
    selected_count = int(selected.sum())
    true_positives = int(y_true[selected].sum()) if selected_count else 0
    positives = int(y_true.sum())
    return {
        "threshold": round(float(threshold), 8),
        "reviewed": selected_count,
        "review_rate": round(selected_count / len(y_true), 6),
        "precision": round(true_positives / selected_count, 6) if selected_count else 0.0,
        "recall": round(true_positives / positives, 6) if positives else 0.0,
    }


def subgroup_metrics(
    frame: pd.DataFrame,
    y_true: np.ndarray,
    probabilities: np.ndarray,
    group_column: str,
    minimum_rows: int = 100,
) -> list[dict[str, Any]]:
    """Return calibration and ranking metrics for sufficiently large groups."""

    working = pd.DataFrame(
        {
            "group": frame[group_column].astype(str).to_numpy(),
            "target": y_true,
            "probability": probabilities,
        }
    )
    output: list[dict[str, Any]] = []
    for group, values in working.groupby("group", observed=True):
        if len(values) < minimum_rows:
            continue
        observed = values["target"].to_numpy(dtype=int)
        predicted = values["probability"].to_numpy(dtype=float)
        average_precision = (
            float(average_precision_score(observed, predicted))
            if len(np.unique(observed)) == 2
            else None
        )
        observed_rate = float(observed.mean())
        mean_probability = float(predicted.mean())
        output.append(
            {
                "group": str(group),
                "rows": int(len(values)),
                "prevalence": round(observed_rate, 6),
                "mean_probability": round(mean_probability, 6),
                "calibration_gap": round(mean_probability - observed_rate, 6),
                "brier": round(float(brier_score_loss(observed, predicted)), 6),
                "average_precision": round(average_precision, 6) if average_precision is not None else None,
            }
        )
    return sorted(output, key=lambda row: row["rows"], reverse=True)
