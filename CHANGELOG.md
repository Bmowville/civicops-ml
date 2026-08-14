# Changelog

## 0.3.4 — 2026-08-14

- Render authenticated interactive API documentation instead of returning raw schema JSON.
- Protect the OpenAPI schema separately and keep documentation write operations disabled.
- Open API documentation in a separate browser tab from the operations console.

## 0.3.3 — 2026-08-14

- Capture prediction and review form values before disabling controls during submission.
- Preserve timezone-aware creation timestamps from the browser console.

## 0.3.2 — 2026-08-14

- Bind OpenTelemetry request instrumentation explicitly to the FastAPI application.
- Exclude liveness and readiness probes from request traces.

## 0.3.1 — 2026-08-14

- Build every production authentication URL from the configured public HTTPS origin.
- Pin the production infrastructure to current Azure Verified Modules.

## 0.3.0 — 2026-08-14

- Add single-tenant Microsoft Entra sign-in with Operator and Administrator application roles.
- Require authenticated sessions and CSRF validation for prediction, review, and logout mutations.
- Redact authorization callback queries before access logging.
- Attribute every prediction and review to the authenticated Entra identity and application role.
- Add Neon PostgreSQL persistence, pooled TLS connections, and checksum-verified schema migrations.
- Keep SQLite as an isolated development and test fallback while requiring PostgreSQL in other environments.
- Protect model metadata, API documentation, and the administrative audit summary by role.
- Add live PostgreSQL integration coverage and identity-boundary tests.

## 0.2.0 — 2026-08-13

- Add the hash-verified FastAPI prediction service.
- Add strict creation-time request validation and reject unexpected fields.
- Add a local operator console with model contributions and category warnings.
- Require and persist a human disposition for every prediction.
- Add SQLite audit storage, health and model-metadata endpoints, security headers, and OpenAPI documentation.
- Expand the automated suite to cover API, audit, schema, and model-integrity behavior.

## 0.1.0 — 2026-08-13

- Define the leakage-aware target and feature contracts.
- Build the reproducible NYC 311 source audit and time-stratified modeling extract.
- Train and chronologically evaluate the no-geography logistic candidate.
- Add explainability, drift, subgroup, and validation-only calibration diagnostics.
- Publish the model card and machine-readable evaluation reports.
