# Serving architecture

## Runtime flow

```text
Microsoft Entra single-tenant sign-in
    │
    ├── CivicOps.Operator
    └── CivicOps.Administrator
             │
             ▼
Signed, secure session + CSRF validation
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
Neon PostgreSQL prediction audit record
             │
             ▼
Required human disposition and rationale
```

The API never accepts status, closure, resolution, due-date, address, coordinate, ZIP-code, borough, community-board, or service-request identifier fields. Request timestamps must include a UTC offset and are converted to New York local time before deriving hour, weekday, month, and weekend features.

## Identity and authorization

CivicOps is registered as a confidential, single-tenant Microsoft Entra web application. It uses the authorization-code flow and a certificate credential. Production callbacks use `form_post`, HTTPS-only cookies, and `SameSite=None`; localhost development uses the query response mode and `SameSite=Lax` because browsers do not permit a secure cross-site cookie over HTTP.

The application requests no Microsoft Graph permissions. Authorization comes from app-role claims in the validated ID token:

- `CivicOps.Operator` can inspect model metadata, create predictions, and record reviews.
- `CivicOps.Administrator` includes operator capabilities and can read aggregate audit counts.

The server validates token tenant, audience, issuer, expiration, and role claims. State and nonce validation are handled during the MSAL authorization-code exchange. Every authenticated session receives an independent CSRF token, required by all mutation endpoints.

Authorization callback query strings are removed from the ASGI scope before the HTTP server writes its access record, preventing one-time codes and state values from entering application logs.

## Endpoints

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| `GET` | `/healthz` | Public | Verify model and audit-store readiness |
| `GET` | `/api/v1/session` | Operator or Administrator | Return the active display identity, roles, and CSRF token |
| `GET` | `/api/v1/model` | Operator or Administrator | Return model digest, features, calibration, threshold, and decision boundary |
| `POST` | `/api/v1/predictions` | Operator or Administrator | Validate creation-time fields, create a score, and write its audit record |
| `POST` | `/api/v1/predictions/{id}/reviews` | Operator or Administrator | Record exactly one human disposition and rationale |
| `GET` | `/api/v1/admin/audit-summary` | Administrator | Return aggregate prediction and review counts |
| `GET` | `/docs` | Operator or Administrator | Return the generated OpenAPI document |

Prediction responses explicitly set `human_review_required` to `true`. The operational threshold only assigns a review tier; it does not make or recommend a final service decision.

## Artifact integrity

At startup, the service computes the candidate model's SHA-256 digest and compares it with `reports/diagnostics.json`. A mismatch stops startup. The calibration method is also read from that report, while the threshold and selected model name are loaded from `reports/baseline_metrics.json`. This prevents a model, threshold, or report from being silently mixed with a different release.

## Audit storage

Neon PostgreSQL is the durable runtime store. The application uses pooled TLS connections suitable for a serverless database and isolates its tables in the `civicops` schema. SQL migrations are packaged with the application, applied transactionally, and recorded with SHA-256 checksums. Startup fails if an already-applied migration has been modified.

Each prediction and review records:

- event and prediction UUIDs
- UTC record times
- the validated creation-time payload
- probability and review tier
- model digest
- authenticated Entra subject, display name, and application role
- reviewer action and rationale

The accepted payload contains no resident identifier or exact location. SQLite retains the same store contract for development and tests only; PostgreSQL is required in any other environment.

## Configuration

| Variable | Requirement |
| --- | --- |
| `CIVICOPS_ENVIRONMENT` | `development`, `test`, or a deployed environment name |
| `CIVICOPS_SESSION_SECRET` | At least 32 random characters; required outside development and test |
| `CIVICOPS_ENTRA_TENANT_ID` | Single Entra tenant identifier |
| `CIVICOPS_ENTRA_CLIENT_ID` | CivicOps application client identifier |
| `CIVICOPS_ENTRA_CERTIFICATE_PATH` | Private certificate key readable only by the application |
| `CIVICOPS_ENTRA_CERTIFICATE_THUMBPRINT` | Thumbprint of the public certificate registered in Entra |
| `DATABASE_URL` | PostgreSQL TLS connection string; required outside development and test |
| `CIVICOPS_PUBLIC_BASE_URL` | Public HTTPS origin for a deployed callback |
| `CIVICOPS_MODEL_PATH` | Candidate model artifact path |
| `CIVICOPS_CALIBRATOR_PATH` | Optional calibrator artifact path |
| `CIVICOPS_DIAGNOSTICS_PATH` | Diagnostic report path |
| `CIVICOPS_BASELINE_PATH` | Baseline metrics report path |
| `CIVICOPS_DB_PATH` | SQLite path used only when no `DATABASE_URL` exists in development or test |

The current cloud identity and Neon development database are configured and verified. Public Azure exposure remains gated on container deployment, the production HTTPS redirect URI, managed secret injection, and post-deployment sign-in verification.
