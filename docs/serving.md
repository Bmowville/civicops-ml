# Serving architecture

## Runtime flow

```text
Operator input
    │
    ▼
Strict creation-time request contract
    │
    ▼
SHA-256 verified model + validation-selected threshold
    │
    ├── probability and review tier
    ├── largest conditional feature contributions
    └── unseen/infrequent category warnings
    │
    ▼
SQLite prediction audit record
    │
    ▼
Required human disposition and rationale
```

The API never accepts status, closure, resolution, due-date, address, coordinate, ZIP-code, borough, community-board, or service-request identifier fields. Request timestamps must include a UTC offset and are converted to New York local time before deriving hour, weekday, month, and weekend features.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/healthz` | Verify model and audit-store readiness |
| `GET` | `/api/v1/model` | Return the model digest, features, calibration method, threshold, and decision boundary |
| `POST` | `/api/v1/predictions` | Validate creation-time fields, create a score, and write its audit record |
| `POST` | `/api/v1/predictions/{id}/reviews` | Record exactly one human disposition and rationale |
| `GET` | `/docs` | OpenAPI interface generated from the request and response schemas |

Prediction responses explicitly set `human_review_required` to `true`. The operational threshold only assigns a review tier; it does not make or recommend a final service decision.

## Artifact integrity

At startup, the service computes the candidate model's SHA-256 digest and compares it with `reports/diagnostics.json`. A mismatch stops startup. The calibration method is also read from that report, while the threshold and selected model name are loaded from `reports/baseline_metrics.json`. This prevents a model, threshold, or report from being silently mixed with a different release.

## Audit storage

The local runtime uses SQLite with foreign keys, write-ahead logging, uniqueness protection for reviews, and explicit connection cleanup. It records:

- prediction and review UUIDs
- UTC record times
- the validated creation-time payload
- probability and review tier
- model digest
- reviewer action, role, and rationale

The accepted payload contains no resident identifier or exact location. The runtime database is ignored by Git.

## Current deployment boundary

The application binds to `127.0.0.1` by default. It includes no authentication or authorization and must not be exposed to a network in this state. A networked release requires identity, role-based permissions, durable managed storage, rate limiting, structured telemetry, retention rules, and an operational rollback procedure.

## Configuration

The local defaults can be replaced with environment variables:

| Variable | Default |
| --- | --- |
| `CIVICOPS_MODEL_PATH` | `models/candidate.joblib` |
| `CIVICOPS_CALIBRATOR_PATH` | `models/calibrator.joblib` |
| `CIVICOPS_DIAGNOSTICS_PATH` | `reports/diagnostics.json` |
| `CIVICOPS_BASELINE_PATH` | `reports/baseline_metrics.json` |
| `CIVICOPS_DB_PATH` | `var/civicops.sqlite3` |
