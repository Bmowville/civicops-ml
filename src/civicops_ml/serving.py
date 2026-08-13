"""Integrity-checked inference for the CivicOps ML candidate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from .calibration import FittedCalibrator
from .modeling import NO_GEOGRAPHY_FEATURES
from .schemas import FeatureContribution, PredictionRequest

EXPLANATION_NOTICE = (
    "Contributions are model associations, not causes or service recommendations."
)
SCORE_NOTICE = (
    "This retrospective score is not population-calibrated and cannot make a service decision."
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ModelIntegrityError(RuntimeError):
    """Raised when a serving artifact differs from its diagnostic record."""


class PredictionService:
    """Load a verified model artifact and produce review-support scores."""

    def __init__(
        self,
        model_path: Path,
        diagnostics_path: Path,
        calibrator_path: Path | None = None,
        baseline_metrics_path: Path = Path("reports/baseline_metrics.json"),
    ) -> None:
        report = json.loads(diagnostics_path.read_text(encoding="utf-8"))
        expected_hash = report["artifacts"]["model"]["sha256"]
        actual_hash = sha256_file(model_path)
        if actual_hash != expected_hash:
            raise ModelIntegrityError(
                "candidate model digest does not match reports/diagnostics.json"
            )

        baseline = json.loads(baseline_metrics_path.read_text(encoding="utf-8"))
        self.model: Any = joblib.load(model_path)
        self.model_sha256 = actual_hash
        if baseline["operational_threshold"]["model"] != report["candidate"]:
            raise ModelIntegrityError("operational threshold targets a different model")
        self.operational_threshold = float(
            baseline["operational_threshold"]["validation"]["threshold"]
        )
        self.calibration_method = report["calibration"]["selection"]["selected"]
        if self.calibration_method == "identity":
            self.calibrator = FittedCalibrator("identity", None)
        elif calibrator_path is None or not calibrator_path.exists():
            raise ModelIntegrityError("the selected calibrator artifact is missing")
        else:
            self.calibrator = joblib.load(calibrator_path)

    def metadata(self) -> dict[str, Any]:
        return {
            "model": "logistic_no_geography",
            "model_sha256": self.model_sha256,
            "calibration": self.calibration_method,
            "features": list(NO_GEOGRAPHY_FEATURES),
            "operational_threshold": self.operational_threshold,
            "status": "human_review_only",
            "automated_decisions_allowed": False,
        }

    def predict(self, request: PredictionRequest) -> dict[str, Any]:
        features = request.model_features()
        frame = pd.DataFrame([features], columns=NO_GEOGRAPHY_FEATURES)
        raw_probability = self.model.predict_proba(frame)[:, 1]
        calibrated = self.calibrator.predict(raw_probability, pd.Series([request.agency]))
        probability = float(calibrated[0])
        if not np.isfinite(probability) or not 0 <= probability <= 1:
            raise RuntimeError("model returned an invalid probability")
        above_threshold = probability >= self.operational_threshold
        return {
            "probability": probability,
            "review_tier": "priority_review" if above_threshold else "standard_review",
            "above_review_threshold": above_threshold,
            "operational_threshold": self.operational_threshold,
            "human_review_required": True,
            "explanation": self._explain(frame),
            "input_warnings": self._input_warnings(frame),
            "explanation_notice": EXPLANATION_NOTICE,
            "score_notice": SCORE_NOTICE,
            "model_sha256": self.model_sha256,
        }

    def _explain(self, frame: pd.DataFrame, limit: int = 5) -> list[FeatureContribution]:
        transformer = self.model.named_steps["features"]
        encoded = transformer.transform(frame)
        names = transformer.get_feature_names_out()
        coefficients = self.model.named_steps["model"].coef_[0]
        row = encoded.getrow(0) if hasattr(encoded, "getrow") else encoded[0]
        indices = row.indices if hasattr(row, "indices") else np.flatnonzero(row)
        values = row.data if hasattr(row, "data") else np.asarray(row)[indices]
        fields = sorted(NO_GEOGRAPHY_FEATURES, key=len, reverse=True)
        contributions: list[FeatureContribution] = []
        for index, encoded_value in zip(indices, values, strict=True):
            name = names[index].split("__", 1)[-1]
            source = next(
                (field for field in fields if name.startswith(field + "_")),
                "unknown",
            )
            category = name[len(source) + 1 :] if source != "unknown" else name
            contribution = float(coefficients[index] * encoded_value)
            contributions.append(
                FeatureContribution(
                    feature=source,
                    value=category,
                    contribution=round(contribution, 6),
                    direction="higher" if contribution >= 0 else "lower",
                )
            )
        contributions.sort(key=lambda item: abs(item.contribution), reverse=True)
        return contributions[:limit]

    def _input_warnings(self, frame: pd.DataFrame) -> list[str]:
        transformer = self.model.named_steps["features"]
        encoder = transformer.named_transformers_["categorical"]
        warnings: list[str] = []
        for feature, categories, infrequent in zip(
            NO_GEOGRAPHY_FEATURES,
            encoder.categories_,
            encoder.infrequent_categories_,
            strict=True,
        ):
            value = frame.iloc[0][feature]
            known_values = {str(category) for category in categories}
            infrequent_values = (
                {str(category) for category in infrequent}
                if infrequent is not None
                else set()
            )
            if value not in known_values:
                warnings.append(f"{feature} was not present in training data")
            elif value in infrequent_values:
                warnings.append(f"{feature} was pooled as an infrequent training value")
        return warnings
