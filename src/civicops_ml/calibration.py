"""Validation-only probability calibration for CivicOps ML."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder


def probability_logit(probabilities: np.ndarray) -> np.ndarray:
    """Convert probabilities to finite logits."""

    clipped = np.clip(np.asarray(probabilities, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(clipped / (1 - clipped))


def calibration_frame(probabilities: np.ndarray, agencies: pd.Series) -> pd.DataFrame:
    """Create the only two inputs allowed in post-model calibration."""

    return pd.DataFrame(
        {
            "score_logit": probability_logit(probabilities),
            "agency": agencies.fillna("Unknown").astype("string").fillna("Unknown").to_numpy(),
        }
    )


@dataclass
class FittedCalibrator:
    """Serializable wrapper for global or agency-aware calibration."""

    name: str
    estimator: Any

    def predict(self, probabilities: np.ndarray, agencies: pd.Series) -> np.ndarray:
        if self.name == "identity":
            return np.asarray(probabilities, dtype=float)
        frame = calibration_frame(probabilities, agencies)
        if self.name == "global_platt":
            return self.estimator.predict_proba(frame[["score_logit"]])[:, 1]
        return self.estimator.predict_proba(frame)[:, 1]


def fit_global_platt(
    probabilities: np.ndarray,
    agencies: pd.Series,
    target: np.ndarray,
) -> FittedCalibrator:
    """Fit a global sigmoid adjustment to validation predictions."""

    frame = calibration_frame(probabilities, agencies)
    estimator = LogisticRegression(C=1_000_000, solver="lbfgs", max_iter=500)
    estimator.fit(frame[["score_logit"]], target)
    return FittedCalibrator("global_platt", estimator)


def fit_agency_aware(
    probabilities: np.ndarray,
    agencies: pd.Series,
    target: np.ndarray,
) -> FittedCalibrator:
    """Fit a regularized global slope with shrunken agency intercept shifts."""

    frame = calibration_frame(probabilities, agencies)
    transformer = ColumnTransformer(
        [
            ("score", "passthrough", ["score_logit"]),
            (
                "agency",
                OneHotEncoder(
                    handle_unknown="infrequent_if_exist",
                    min_frequency=250,
                    sparse_output=True,
                ),
                ["agency"],
            ),
        ],
        remainder="drop",
    )
    estimator = Pipeline(
        [
            ("features", transformer),
            ("model", LogisticRegression(C=1.0, solver="lbfgs", max_iter=500)),
        ]
    )
    estimator.fit(frame, target)
    return FittedCalibrator("agency_aware", estimator)


def weighted_absolute_group_gap(
    target: np.ndarray,
    probabilities: np.ndarray,
    agencies: pd.Series,
    minimum_rows: int = 100,
) -> float:
    """Return row-weighted absolute agency calibration gap."""

    frame = pd.DataFrame(
        {
            "target": target,
            "probability": probabilities,
            "agency": agencies.fillna("Unknown").astype("string").fillna("Unknown").to_numpy(),
        }
    )
    numerator = 0.0
    denominator = 0
    for _, group in frame.groupby("agency", observed=True):
        if len(group) < minimum_rows:
            continue
        gap = abs(float(group["probability"].mean()) - float(group["target"].mean()))
        numerator += len(group) * gap
        denominator += len(group)
    return numerator / denominator if denominator else 0.0


def select_calibrator(selection_metrics: dict[str, dict[str, float]]) -> dict[str, Any]:
    """Apply calibration only for meaningful chronological improvements."""

    raw_metrics = selection_metrics["identity"]
    global_metrics = selection_metrics["global_platt"]
    agency_metrics = selection_metrics["agency_aware"]
    candidates: list[tuple[str, float]] = []
    comparisons: dict[str, dict[str, float | bool]] = {}
    for name, metrics in (
        ("global_platt", global_metrics),
        ("agency_aware", agency_metrics),
    ):
        brier_improvement = raw_metrics["brier"] - metrics["brier"]
        group_gap_improvement = (
            raw_metrics["weighted_absolute_agency_gap"]
            - metrics["weighted_absolute_agency_gap"]
        )
        relative_group_improvement = (
            group_gap_improvement / raw_metrics["weighted_absolute_agency_gap"]
            if raw_metrics["weighted_absolute_agency_gap"]
            else 0.0
        )
        if name == "agency_aware":
            qualifies = brier_improvement >= 0.0005 and relative_group_improvement >= 0.05
        else:
            group_gap_change = (
                -relative_group_improvement if relative_group_improvement < 0 else 0.0
            )
            qualifies = brier_improvement >= 0.0005 and group_gap_change <= 0.05
        comparisons[name] = {
            "brier_improvement_vs_identity": round(brier_improvement, 6),
            "absolute_group_gap_improvement_vs_identity": round(group_gap_improvement, 6),
            "relative_group_gap_improvement_vs_identity": round(
                relative_group_improvement,
                6,
            ),
            "qualifies": qualifies,
        }
        if qualifies:
            candidates.append((name, metrics["brier"]))

    selected = min(candidates, key=lambda item: item[1])[0] if candidates else "identity"
    return {
        "selected": selected,
        "rule": (
            "retain identity unless a calibrator improves chronological selection "
            "Brier by at least 0.0005; agency-aware must also improve weighted "
            "absolute agency gap by at least 5%, while global calibration may not "
            "worsen that gap by more than 5%"
        ),
        "comparisons": comparisons,
    }
