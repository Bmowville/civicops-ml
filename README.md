# CivicOps ML

CivicOps ML is a reproducible machine-learning case study that estimates whether an NYC 311 service request will remain unresolved beyond seven calendar days. Predictions are made from information available when the request is created, with explicit controls for target leakage, temporal drift, calibration, and subgroup performance.

## Project status

The data contract and source audit are complete. The initial audit covers 9,106,406 requests created from January 2024 through June 2026 and a bounded 48,000-row time-stratified sample. Model development will use chronological train, validation, and test periods.

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

The detailed [model specification](docs/model-spec.md) and [data audit](docs/data-audit.md) document the scope and known limitations.

## Validation

The data-contract tests use Python's standard library and do not require a data download:

```bash
python -m unittest discover -s tests -v
```

The audit utility queries bounded time windows and writes aggregate findings only; raw service-request records are not committed.
