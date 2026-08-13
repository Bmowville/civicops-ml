# Chronological baseline results

Evaluation date: August 13, 2026

## Result

Regularized logistic regression materially outperformed both comparison baselines on the held-out January–June 2026 extract. Removing ZIP code, borough, and community board did not reduce useful performance, so the geography-free model is the provisional candidate.

This is a retrospective modeling result, not a deployment claim. The evaluation also found temporal and agency-level calibration gaps that must be addressed before a live score is appropriate.

## Modeling extract

The extract includes every request in three seeded four-hour windows per week. The schedule covers all 42 weekday/daypart combinations in each 14-week cycle and uses seed `3112026`. This yielded 641,232 valid rows from 390 windows, representing 7.14% of elapsed time in the cohort. It included no duplicate service-request keys; 204 rows with invalid timestamps were rejected. Exact addresses and identifiers are not written to the model-ready file.

| Split | Period | Extract rows | Extract prevalence | Full-population prevalence |
| --- | --- | ---: | ---: | ---: |
| Train | Jan 2024–Jun 2025 | 376,309 | 18.98% | 17.81% |
| Validation | Jul–Dec 2025 | 128,840 | 17.47% | 16.23% |
| Test | Jan–Jun 2026 | 136,083 | 19.60% | 18.52% |

The sample is consistently 1.1–1.2 percentage points above server-side population aggregates. Ranking comparisons are useful, but the sample's calibration scores must not be presented as population-certified probabilities.

## Model comparison

All learned models fit only the training period. The validation period was used for candidate and threshold selection. No feature, regularization setting, or threshold was chosen from test results.

| Model | Test average precision | ROC AUC | Brier score | Top-10% precision | Top-10% recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| Constant training prevalence | 0.196 | 0.500 | 0.158 | 20.7% | 10.6% |
| Smoothed agency/problem history | 0.696 | 0.915 | 0.090 | 69.8% | 35.6% |
| Logistic, all planned features | 0.748 | 0.928 | 0.083 | 78.1% | 39.8% |
| **Logistic, no geography** | **0.749** | **0.928** | **0.083** | **77.9%** | **39.7%** |

The historical-rate model is already strong, confirming that agency and problem taxonomy explain much of the outcome. The logistic candidate improves test average precision by 0.053 and top-decile precision by 8.1 percentage points over that baseline.

## Candidate selection

The selection rule was applied to validation metrics only: prefer the geography-free model when its average precision is within 0.005 and its Brier score is within 0.001 of the full model.

| Validation result | Full logistic | No-geography logistic |
| --- | ---: | ---: |
| Average precision | 0.7846 | 0.7836 |
| Brier score | 0.0658 | 0.0658 |

The geography-free model met the equivalence rule. Excluding ZIP code, borough, and community board reduces unnecessary location-proxy risk without giving up material performance. Borough remains available only for evaluation, not prediction.

## Review-capacity threshold

The probability threshold `0.66999111` was fixed at the 90th percentile of candidate scores on validation.

| Period | Requests reviewed | Review rate | Precision | Recall |
| --- | ---: | ---: | ---: | ---: |
| Validation | 12,886 | 10.00% | 77.6% | 44.4% |
| Test, unchanged threshold | 10,273 | 7.55% | 80.4% | 30.9% |

The lower test review rate shows score-distribution drift. A production workflow would need capacity-aware threshold monitoring rather than assuming a fixed score always selects 10%.

## Calibration and subgroup findings

The candidate's test average predicted probability was 17.34%, compared with 19.60% observed in the extract and 18.52% in the full-period population aggregate. Test expected calibration error was 0.029 on the extract.

The largest high-volume agency calibration gaps were operationally meaningful:

- DOT: predicted 20.3%, observed 31.2%
- DOHMH: predicted 45.9%, observed 56.8%
- DOB: predicted 57.4%, observed 47.4%
- HPD: predicted 31.6%, observed 37.9%

Performance also varies by borough even though borough is not a model input. Test average precision ranged from 0.608 for Staten Island to 0.808 for Manhattan among named boroughs. These findings require within-agency diagnostics and recalibration work before deployment.

## Decision

The baseline demonstrates meaningful ranking skill beyond simple historical rates but does not justify operational deployment. Follow-up diagnostics confirmed strong dependence on agency and complaint taxonomy, measured modest overall score drift, and tested global and agency-aware calibration without finding sufficient validation improvement. The raw candidate score was retained.

The complete release assessment, intended-use boundary, limitations, and monitoring requirements are in the [model card](../MODEL_CARD.md). Machine-readable evidence is available in [`reports/baseline_metrics.json`](../reports/baseline_metrics.json) and [`reports/diagnostics.json`](../reports/diagnostics.json).
