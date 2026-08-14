from __future__ import annotations

import unittest
from pathlib import Path


APP_JS = (
    Path(__file__).resolve().parents[1] / "src" / "civicops_ml" / "static" / "app.js"
).read_text(encoding="utf-8")
INDEX_HTML = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "civicops_ml"
    / "static"
    / "index.html"
).read_text(encoding="utf-8")


class StaticUiTests(unittest.TestCase):
    def _handler(self, start: str, end: str) -> str:
        start_index = APP_JS.index(start)
        end_index = APP_JS.index(end, start_index)
        return APP_JS[start_index:end_index]

    def test_prediction_values_are_captured_before_controls_are_disabled(self) -> None:
        handler = self._handler(
            'predictionForm.addEventListener("submit"',
            'reviewForm.addEventListener("submit"',
        )
        self.assertLess(
            handler.index("new FormData(predictionForm)"),
            handler.index("setBusy(predictionForm, true)"),
        )

    def test_review_values_are_captured_before_controls_are_disabled(self) -> None:
        handler = self._handler(
            'reviewForm.addEventListener("submit"',
            "async function initializeSession",
        )
        self.assertLess(
            handler.index("new FormData(reviewForm)"),
            handler.index("setBusy(reviewForm, true)"),
        )

    def test_api_documentation_opens_in_a_separate_tab(self) -> None:
        self.assertIn(
            '<a href="/docs" target="_blank" rel="noopener noreferrer">',
            INDEX_HTML,
        )


if __name__ == "__main__":
    unittest.main()
