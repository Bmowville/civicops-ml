# CivicOps ML

CivicOps ML is a reproducible machine-learning system that estimates whether an NYC 311 service request will remain unresolved beyond seven calendar days. Predictions are made from information available when the request is created, with explicit controls for target leakage, temporal drift, calibration, and subgroup performance.

## Project status

The data contract, source audit, chronological evaluation, explainability analysis, drift checks, and calibration experiment are complete. The model now runs behind a versioned FastAPI service and a local operations console that requires a recorded human disposition for every score. The source audit covers 9,106,406 requests created from January 2024 through June 2026. Modeling uses a reproducible 641,232-row, time-stratified extract with separate training, validation, and 2026 test periods.

The provisional candidate is a regularized logistic model without ZIP code, borough, or community-board inputs. On the held-out sample it reached 0.749 average precision. Its highest-risk 10% contained 39.7% of delayed requests at 77.9% precision. Validation did not support adding global or agency-aware probability scaling, so the raw score was retained. These are retrospective sample results, not production-performance claims.

## Data source

- [NYC Open Data: 311 Service Requests from 2020 to Present](https://data.cityofnewyork.us/d/erm2-nwe9)
- Dataset identifier: `erm2-nwe9`
- Published update frequency: daily
- Customer-identifying information is not included in the public dataset

## Prediction contract

- **Prediction moment:** immediately after a service request is created
- **Target:** `1` when the request is still open after 168 hours; otherwise `0`
- **Eligible records:** requests old enough for the seven-day outcome to be observed
- **Evaluation:** chronological holdout, precision-recall, calibration, recall at a fixed review capacity, and performance by agency and borough
- **Intended use:** decision support for workload review, not automated denial, closure, or deprioritization of public services

The [model card](MODEL_CARD.md), [serving architecture](docs/serving.md), [model specification](docs/model-spec.md), [data audit](docs/data-audit.md), [baseline results](docs/baseline-results.md), and [changelog](CHANGELOG.md) document the system and its evidence.

## Local application

After building the ignored local model artifacts, start the API and operator console on localhost:

```bash
python -m civicops_ml.api
```

Open `http://127.0.0.1:8000` for the review console or `http://127.0.0.1:8000/docs` for the OpenAPI interface. Runtime audit records are written to the ignored `var/civicops.sqlite3` database.

The service fails closed when the model digest differs from the diagnostic report. It rejects post-outcome fields, fine-grained location inputs, timestamps without a UTC offset, timestamps outside the supported cohort, and unexpected request properties. This local build has no identity layer and must remain bound to localhost until authentication and authorization are implemented.

## Validation

Create an isolated environment and install the locked dependencies:

```bash
python -m venv .venv
python -m pip install -r requirements-lock.txt
python -m pip install -e . --no-deps
```

Run the validation suite without downloading data:

```bash
python -m unittest discover -s tests -v
```

The audit utility queries bounded time windows and writes aggregate findings only; raw service-request records are not committed.

Rebuild the ignored modeling extract and baseline report:

```bash
python -m civicops_ml.dataset
python -m civicops_ml.modeling
python -m civicops_ml.diagnostics
```

The extraction manifest records the sampling seed, source counts, and SHA-256 digest used for each report. The diagnostic report also records the exact candidate-model and modeling-extract digests.
