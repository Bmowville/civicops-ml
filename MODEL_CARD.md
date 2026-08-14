# CivicOps ML model card

## Model summary

CivicOps ML estimates whether an NYC 311 service request will remain unresolved for more than seven calendar days. The current candidate is a regularized logistic-regression pipeline using only information available when a request is created.

**Status:** model candidate deployed with CivicOps ML 0.3.4 in a restricted, single-tenant human-review workflow. It is not approved for automated service decisions or public scoring.

**Intended use:** help a human operations reviewer identify requests that may warrant earlier attention. The score must not automatically deny, close, deprioritize, or reroute public services, and it is not a measure of resident importance, agency quality, or employee performance.

## Model details

| Item | Value |
| --- | --- |
| Candidate | Geography-free logistic regression |
| Library | scikit-learn 1.9.0 |
| Estimator | `LogisticRegression(C=1.0, solver="lbfgs", max_iter=500)` |
| Encoding | One-hot categorical encoding; categories with fewer than 25 training rows are pooled |
| Training period | January 2024–June 2025 |
| Validation period | July–December 2025 |
| Test period | January–June 2026 |
| Training rows | 376,309 |
| Validation rows | 128,840 |
| Test rows | 136,083 |

The model uses agency, complaint type, descriptor, location type, submission channel, creation hour, day of week, month, and weekend indicator. It excludes ZIP code, borough, community board, exact location, identifiers, post-creation status fields, resolution text, due dates, and closure information. Borough is retained only for evaluation.

## Data and evaluation design

The source is the public [NYC 311 Service Requests dataset](https://data.cityofnewyork.us/d/erm2-nwe9). The model-ready extract contains every request in three seeded four-hour windows per week. It represents 7.14% of elapsed time in the cohort and contains no addresses or service-request identifiers.

All splits are chronological. Model selection and calibration decisions use validation data only; the January–June 2026 period is held out for final evaluation. The sample's delayed-request prevalence is about 1.1 percentage points higher than the corresponding server-side population aggregate, so probability calibration is not population-certified.

## Held-out performance

| Metric | January–June 2026 test result |
| --- | ---: |
| Average precision | 0.749 |
| ROC AUC | 0.928 |
| Brier score | 0.083 |
| Log loss | 0.265 |
| Expected calibration error, 10 bins | 0.029 |
| Precision in highest-risk 10% | 77.9% |
| Recall in highest-risk 10% | 39.7% |

These are retrospective sample results, not claims about live performance. Monthly test average precision ranged from 0.723 to 0.781. The model underpredicted the observed delayed-request rate in every test month, although the gap narrowed from 3.0 percentage points in January to 0.2 points in June.

## Explainability findings

Validation permutation tests show that the model relies primarily on operational taxonomy:

| Permuted input | Average-precision decrease | Brier-score increase |
| --- | ---: | ---: |
| Agency | 0.360 | 0.075 |
| Complaint type | 0.149 | 0.029 |
| Descriptor | 0.093 | 0.020 |
| Location type | 0.008 | 0.002 |

Agency is therefore a major shortcut feature rather than a neutral identifier. Supported coefficient directions are consistent with large workflow differences—for example, NYPD requests receive a strongly lower score while TLC and food-establishment categories receive higher scores. These are conditional associations, not causal effects. Coefficients for rare categories are unstable and should not be interpreted individually.

## Calibration decision

The first half of validation was used to fit calibration candidates, and the second half was used to select among them. Neither candidate cleared the predeclared improvement rule:

| Validation selection result | Brier score | Log loss | Weighted agency calibration gap |
| --- | ---: | ---: | ---: |
| Raw score | **0.062450** | **0.190337** | **0.010404** |
| Global Platt scaling | 0.062445 | 0.190478 | 0.010475 |
| Agency-aware scaling | 0.062925 | 0.191732 | 0.014865 |

The raw score was retained. Global scaling improved Brier score by only 0.000005 while worsening the other two measures; agency-aware scaling worsened all three. This avoids adding complexity that the chronological validation evidence did not support.

## Drift and subgroup findings

Prediction-score PSI was 0.038 from training to validation and 0.037 from training to test, indicating limited movement in the overall score distribution. Test shifts were more visible in complaint taxonomy and submission channel: the online-channel share increased by 7.0 percentage points, and the share of the `Loud Music/Party` descriptor fell by 4.3 points.

Calendar-month divergence is inflated by design because the training period spans 18 months while each later split covers only six months. It should be interpreted as seasonal coverage, not by itself as a production incident.

High-volume test groups still show meaningful calibration differences. The model underpredicted delayed outcomes for DOT by 10.9 percentage points, DOHMH by 10.9 points, and HPD by 6.3 points; it overpredicted for DOB by 9.9 points. Borough average precision ranged from 0.608 for Staten Island to 0.808 for Manhattan even though borough is not an input.

## Limitations and failure modes

- Agency and complaint taxonomy encode operational processes that can change without warning.
- A single seven-day target does not represent each agency's service expectations.
- Public 311 data represents reported requests, not all community conditions.
- The time-window sample is reproducible but not a census, and its prevalence is somewhat elevated.
- The model is not reliable for causal conclusions, employee evaluation, or comparisons of resident need.
- New or renamed categories are pooled by the encoder and may behave differently from historical categories.
- Subgroup calibration gaps remain large enough to block public or automated scoring.

## Production controls and monitoring

The deployed workflow requires Microsoft Entra application roles, records the model hash and a human disposition for every completed review, stores the audit trail in Neon PostgreSQL, emits request telemetry to Application Insights, and runs immutable Container Apps revisions with a tested rollback path. The API accepts no service-request identifier, address, ZIP code, borough, community board, status, resolution text, or closure information.

The production-monitoring reference is generated from the training split and bound to the exact model and extract SHA-256 digests. It covers agency, complaint type, descriptor, location type, submission channel, and prediction-score distributions. Monitoring is suppressed below 100 predictions. Warning and critical boundaries are 0.10/0.20 for score PSI, 0.05/0.10 for feature Jensen-Shannon divergence, and 1%/5% for unseen categories. Human-review completion warns below 95% and is critical below 90%; any model-hash mismatch is critical.

Live outcome metrics are intentionally separate from the application audit trail because the service does not collect a request identifier or post-creation outcome. Average precision, Brier score, delayed-outcome prevalence, and subgroup calibration therefore require a new chronological evaluation cohort from the public source rather than joining operational users or rationales back to service requests. No monitoring result can authorize an automated service action.

## Reproducibility

The machine-readable [baseline metrics](reports/baseline_metrics.json) and [diagnostic report](reports/diagnostics.json) contain the full result. The diagnostic report records the SHA-256 digests of the ignored local model and modeling extract used to produce it. See the [model specification](docs/model-spec.md), [data audit](docs/data-audit.md), and [baseline results](docs/baseline-results.md) for the complete evidence trail.
