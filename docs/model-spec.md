# Model specification

## Objective

Estimate, at service-request creation time, the probability that an NYC 311 request will not close within 168 hours. Calibration is evaluated on a later validation period, and scaling is applied only when it meets a predeclared improvement rule. The resulting risk score is intended to help an operations reviewer find requests that may need earlier attention.

The score must not automatically deny, close, or deprioritize a request. It is not a measure of resident importance, agency quality, or employee performance.

## Observation and outcome

| Item | Definition |
| --- | --- |
| Prediction moment | Immediately after the service request is created |
| Unit | One unique 311 service request |
| Positive outcome | No valid closure timestamp within 168 hours of creation |
| Negative outcome | Valid closure timestamp at or before 168 hours |
| Maturity rule | At least 168 hours elapsed between creation and extraction |
| Invalid row | Closure precedes creation, timestamp is malformed, or key is duplicated |

Open requests older than seven days are valid positive examples. Training only on closed requests would remove some of the most important positive outcomes and introduce selection bias.

## Cohort and temporal split

- Cohort start: January 1, 2024
- Cohort end: June 30, 2026
- Training: January 2024 through June 2025
- Validation: July through December 2025
- Test: January through June 2026

No random split will be used for the reported result. The chronological holdout measures performance on newer requests and exposes taxonomy or operational drift.

## Creation-time features

Source fields:

- agency
- problem (`complaint_type` in the API)
- problem detail (`descriptor`)
- location type
- incident ZIP code
- borough
- submission channel
- community board

Derived from the creation timestamp:

- hour
- day of week
- month
- weekend indicator

Missing categorical values will receive an explicit `Unknown` category. Exact addresses, street names, coordinates, parcel identifiers, and the service-request identifier are excluded from model features.

## Leakage exclusions

The following fields are prohibited because they describe the outcome, later agency activity, or an agency SLA:

- closed date, except to construct the label
- status
- due date
- resolution description
- resolution action updated date

Council district, police precinct, and additional details are deferred because they were added to the published dataset in December 2025 and would create a schema-availability break across the training period.

## Modeling and evaluation plan

The first result will compare three levels:

1. Constant prevalence baseline.
2. Historical agency-and-problem risk baseline fitted on training data only.
3. Regularized logistic regression using one-hot categorical and creation-time features.

A nonlinear challenger will be considered only after the baseline pipeline is validated. Reported metrics will include:

- average precision and the precision-recall curve
- ROC AUC for reference, not as the primary metric
- Brier score and calibration error
- recall among the highest-risk 10% of requests
- precision at the chosen review capacity
- performance and calibration by agency and borough

The geography-free ablation becomes the provisional candidate when its validation average precision is within 0.005 and its Brier score is within 0.001 of the full model. This rule favors the simpler model when predictive quality is effectively equivalent and avoids unnecessary location proxies.

Threshold selection will use validation data only. The 2026 test period remains untouched until the pipeline and threshold are fixed.

## Known risks

- Agency and problem categories strongly influence duration and can create shortcut learning.
- Category definitions and operating procedures change over time.
- ZIP code and community board can act as socioeconomic proxies.
- A seven-day threshold is operationally clear but does not represent every agency's service expectation.
- The dataset records reported service requests, not all underlying community conditions.

The model report will therefore include a feature-ablation comparison without geography, subgroup calibration, and drift checks for categorical distributions and target prevalence.
