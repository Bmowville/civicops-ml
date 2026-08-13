# CivicOps ML

CivicOps ML is a reproducible machine-learning case study that estimates whether an NYC 311 service request will remain unresolved beyond seven calendar days. Predictions are made from information available when the request is created, with explicit controls for target leakage, temporal drift, calibration, and subgroup performance.

## Project status

The data contract, source audit, and chronological baseline evaluation are complete. The source audit covers 9,106,406 requests created from January 2024 through June 2026. Baseline modeling uses a reproducible 641,232-row, time-stratified extract with separate training, validation, and 2026 test periods.

The provisional candidate is a regularized logistic model without ZIP code, borough, or community-board inputs. On the held-out sample it reached 0.749 average precision. Its highest-risk 10% contained 39.7% of delayed requests at 77.9% precision. These are retrospective sample results, not production-performance claims.

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

The detailed [model specification](docs/model-spec.md), [data audit](docs/data-audit.md), and [baseline results](docs/baseline-results.md) document the scope, evidence, and known limitations.

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
```

The extraction manifest records the sampling seed, source counts, and SHA-256 digest used for each report.
