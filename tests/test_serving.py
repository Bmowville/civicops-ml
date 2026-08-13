from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from civicops_ml.serving import ModelIntegrityError, PredictionService


class PredictionServiceTests(unittest.TestCase):
    def test_model_digest_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_path = root / "candidate.joblib"
            diagnostics_path = root / "diagnostics.json"
            model_path.write_bytes(b"not the verified model")
            diagnostics_path.write_text(
                json.dumps(
                    {
                        "artifacts": {"model": {"sha256": "0" * 64}},
                        "calibration": {"selection": {"selected": "identity"}},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ModelIntegrityError):
                PredictionService(model_path, diagnostics_path)


if __name__ == "__main__":
    unittest.main()
