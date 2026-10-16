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

The service applies a bounded global login rate and a per-user mutation rate. The horizontally scaled host shares sliding-window state through PostgreSQL row locks so concurrent instances cannot each consume a separate allowance. Only SHA-256 hashes of throttle keys and bounded event timestamps are retained; expired inactive keys are removed by scheduled monitoring. The in-memory backend remains available for local or single-replica operation. Request bodies, tokens, rationales, and exact user identifiers are not emitted as application telemetry.

## Endpoints

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| `GET` | `/livez` | Public | Verify that the application process is responsive |
| `GET` | `/healthz` | Public | Verify model and audit-store readiness |
| `GET` | `/api/v1/session` | Operator or Administrator | Return the active display identity, roles, and CSRF token |
| `GET` | `/api/v1/model` | Operator or Administrator | Return model digest, features, calibration, threshold, and decision boundary |
| `POST` | `/api/v1/predictions` | Operator or Administrator | Validate creation-time fields, create a score, and write its audit record |
| `POST` | `/api/v1/predictions/{id}/reviews` | Operator or Administrator | Record exactly one human disposition and rationale |
| `GET` | `/api/v1/admin/audit-summary` | Administrator | Return aggregate prediction and review counts |
| `GET` | `/api/v1/admin/monitoring` | Administrator | Return aggregate drift, integrity, and review-completion status |
| `GET` | `/docs` | Operator or Administrator | Render interactive, read-only Swagger documentation in a separate tab |
| `GET` | `/openapi.json` | Operator or Administrator | Return the protected OpenAPI schema |
| `POST` | `/api/v1/internal/monitoring` | Monitoring service credential | Evaluate aggregate monitoring and expire inactive throttle keys |

Prediction responses explicitly set `human_review_required` to `true`. The operational threshold only assigns a review tier; it does not make or recommend a final service decision.

## Artifact integrity

At startup, the service computes the candidate model's SHA-256 digest and compares it with `reports/diagnostics.json`. A mismatch stops startup. The calibration method is also read from that report, while the threshold and selected model name are loaded from `reports/baseline_metrics.json`. This prevents a model, threshold, or report from being silently mixed with a different release.

## Audit storage

Neon PostgreSQL is the durable runtime store. The application uses pooled TLS connections suitable for a serverless database and isolates its tables in the `civicops` schema. SQL migrations are packaged with the application, applied transactionally, and recorded with SHA-256 checksums. Development can migrate on startup. Production rejects automatic migration and uses the `civicops-migrate` release command with an owner connection before the API starts. The API role has schema usage plus read/insert access to audit tables and cannot alter the schema. Migration fails if an already-applied file has been modified.

Each prediction and review records:

- event and prediction UUIDs
- UTC record times
- the validated creation-time payload
- probability and review tier
- model digest
- authenticated Entra subject, display name, and application role
- reviewer action and rationale

The accepted payload contains no resident identifier or exact location. SQLite retains the same store contract for development and tests only; PostgreSQL is required in any other environment.

## Production monitoring

The hosted release evaluates the most recent 30-day audit window through an authenticated external job scheduled every six hours. GitHub Actions scheduling is best-effort and can be delayed; it is not an exact-time service guarantee. The web process does not need to stay alive between requests. A continuously running deployment can instead use background mode, which evaluates at startup and at the configured interval.

Monitoring compares aggregate creation-time feature and score distributions with the model-bound reference in `reports/monitoring_baseline.json`. Drift and review-completion conclusions are suppressed below 100 predictions, but any model-digest mismatch is critical immediately.

The monitoring query selects no prediction identifier, actor identity, actor role, review action, or rationale. The external workflow logs the overall status only and fails when warning or critical findings require review. Azure Monitor is optional and is enabled only when its connection string is configured; its telemetry contains aggregate metrics only. Administrators can request a fresh aggregate report from `/api/v1/admin/monitoring`; the same calculation is available through `civicops-monitor` for controlled operations.

## Serverless deployment

The current deployment uses Vercel's Python 3.12 FastAPI runtime and large-functions beta to accommodate the pinned scientific Python dependencies. `vercel.json` installs `requirements-lock.txt` and the application package, excludes development and secret-bearing local files, and bounds the function duration. Only the two serving artifacts and their diagnostic references are included.

The `app.py` adapter refuses to start unless the production environment requires PostgreSQL, disables automatic migrations, enables the PostgreSQL rate-limit backend, and selects scheduled monitoring with a service credential. Apply checksum-verified migrations with an owner connection before deploying a validated commit using `vercel deploy --prod`. Provision a dedicated non-owner API role with `civicops-migrate`; the web application must never use the owner connection.

Register the deployed HTTPS `/auth/callback` address in the existing Entra web application and store its certificate private key, session key, database URL, and monitoring credential in encrypted hosting settings. Use a separate certificate per deployment target and preserve existing registrations during a rollout.

Configure the repository variable `CIVICOPS_MONITORING_URL` with the deployed monitoring endpoint and the repository secret `CIVICOPS_MONITORING_TOKEN` with the same service credential used by the host. The `Production monitoring` workflow supports scheduled runs and manual dispatch. This credential authorizes aggregate monitoring only; it cannot sign in, create predictions, or record reviews.

## Configuration

| Variable | Requirement |
| --- | --- |
| `CIVICOPS_ENVIRONMENT` | `development`, `test`, or a deployed environment name |
| `CIVICOPS_SESSION_SECRET` | At least 32 random characters; required outside development and test |
| `CIVICOPS_ENTRA_TENANT_ID` | Single Entra tenant identifier |
| `CIVICOPS_ENTRA_CLIENT_ID` | CivicOps application client identifier |
| `CIVICOPS_ENTRA_CERTIFICATE_PATH` | Private certificate key readable only by the application |
| `CIVICOPS_ENTRA_CERTIFICATE_PRIVATE_KEY` | Encrypted hosting secret or Key Vault-injected PEM value used instead of a certificate path |
| `CIVICOPS_ENTRA_CERTIFICATE_THUMBPRINT` | Thumbprint of the public certificate registered in Entra |
| `DATABASE_URL` | PostgreSQL TLS connection string; required outside development and test |
| `CIVICOPS_AUTO_MIGRATE` | `false` in production; production rejects `true` |
| `CIVICOPS_PUBLIC_BASE_URL` | Public HTTPS origin for a deployed callback |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Enables Azure Monitor OpenTelemetry when present |
| `CIVICOPS_TRACES_PER_SECOND` | Bounded trace sampling rate; defaults to 0.5 |
| `CIVICOPS_LOGIN_LIMIT_PER_FIVE_MINUTES` | Global login-attempt limit; defaults to 10 |
| `CIVICOPS_MUTATION_LIMIT_PER_MINUTE` | Per-user mutation limit; defaults to 30 |
| `CIVICOPS_RATE_LIMIT_BACKEND` | `postgres` for shared serverless throttling; `memory` for local or single-replica operation |
| `CIVICOPS_MONITORING_BASELINE_PATH` | Model-bound production monitoring reference |
| `CIVICOPS_MONITORING_WINDOW_DAYS` | Bounded audit window; defaults to 30 days |
| `CIVICOPS_MONITORING_INTERVAL_SECONDS` | Runtime evaluation interval; minimum 300 seconds and defaults to six hours |
| `CIVICOPS_MONITORING_MODE` | `scheduled` for serverless hosting; `background` for a continuously running process |
| `CIVICOPS_MONITORING_TOKEN` | At least 32 random characters; service credential for the scheduled monitoring endpoint |
| `CIVICOPS_MODEL_PATH` | Candidate model artifact path |
| `CIVICOPS_CALIBRATOR_PATH` | Optional calibrator artifact path |
| `CIVICOPS_DIAGNOSTICS_PATH` | Diagnostic report path |
| `CIVICOPS_BASELINE_PATH` | Baseline metrics report path |
| `CIVICOPS_DB_PATH` | SQLite path used only when no `DATABASE_URL` exists in development or test |

The alternative Azure infrastructure uses managed-identity Key Vault references, an Entra certificate, Application Insights sampling, health probes, immutable image digests, and multiple Container App revisions for rollback. Account-specific identifiers and secret deployment values are intentionally not part of the repository.
