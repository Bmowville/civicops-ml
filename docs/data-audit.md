# NYC 311 data audit

Audit date: August 13, 2026

## Source verification

The authoritative source is [NYC Open Data's 311 Service Requests from 2020 to Present](https://data.cityofnewyork.us/d/erm2-nwe9), dataset `erm2-nwe9`. Its metadata describes daily updates, 48 published columns, and no customer-identifying information. NYC [split the historical 2010–2019 records](https://opendata.cityofnewyork.us/311-service-requests-from-2010-to-present-updates/) into a separate dataset in December 2025 while retaining the current dataset's address and API field names.

The timestamps are Socrata floating timestamps. Duration calculations therefore preserve the publisher's timezone-free local representation rather than inventing a UTC offset.

## Full-cohort aggregates

The mature modeling window contains 9,106,406 requests created from January 1, 2024 through June 30, 2026.

| Creation period | Requests | Open or closed after 7 days | Rate | Invalid negative duration |
| --- | ---: | ---: | ---: | ---: |
| 2024 | 3,456,770 | 649,353 | 18.79% | 910 |
| 2025 | 3,655,044 | 587,303 | 16.07% | 839 |
| Jan–Jun 2026 | 1,994,592 | 369,325 | 18.52% | 255 |
| **Total** | **9,106,406** | **1,605,981** | **17.64%** | **2,004** |

The positive class is large enough for precision-recall evaluation without synthetic oversampling. The 0.022% of rows with closure before creation will be rejected rather than repaired.

## Bounded sample audit

The detailed audit used 48,000 records from 120 deterministic windows: four six-hour windows on the fifteenth day of each month from January 2024 through June 2026, capped at 400 rows per window. Raw records remained in memory and were not saved.

This sample is a data-quality probe, not the final modeling extract. Its seven-day positive rate was 16.62%, compared with 17.64% in the full-cohort aggregate, so later model reporting must use population checks or weights rather than treating the audit sample as prevalence-perfect.

### Resolution duration

Among valid closed requests in the sample:

| Percentile | Calendar days |
| --- | ---: |
| 50th | 0.24 |
| 75th | 2.47 |
| 90th | 15.50 |
| 95th | 43.68 |
| 99th | 194.36 |

The long right tail supports a risk-classification framing. Sample positive rates were 37.73% beyond one day, 23.64% beyond three days, 16.62% beyond seven days, 12.46% beyond fourteen days, and 8.51% beyond thirty days.

### Candidate feature quality

| Feature | Missing | Observed categories |
| --- | ---: | ---: |
| Agency | 0.00% | 16 |
| Problem | 0.00% | 162 |
| Problem detail | 0.62% | 637 |
| Location type | 11.09% | 89 |
| Incident ZIP | 0.83% | 205 |
| Borough | 0.00% | 6 |
| Submission channel | 0.00% | 5 |
| Community board | 0.00% | 77 |

Missing values are usable as explicit categories; location type requires particular monitoring. No duplicate service-request keys appeared in the 48,000-row sample.

## Leakage and shortcut findings

Status was present for every sampled row, while resolution description and resolution-update timestamp were present for more than 99%. Those fields are strong post-outcome signals and are excluded. Due date is also excluded even though sparsely populated because it encodes agency service expectations.

Agency and problem are legitimate creation-time features, but their seven-day rates vary sharply. In the sample, several high-volume NYPD problems closed within seven days almost universally, while other agencies exceeded seven days frequently. The model must therefore beat an agency-and-problem historical-rate baseline and report within-group behavior; a high overall score alone would not demonstrate useful learning.

## Decisions

- Use the transparent seven-day outcome rather than a closed-record-only duration regression.
- Preserve open mature requests as positive labels.
- Use chronological evaluation with an untouched 2026 test period.
- Exclude exact locations and all post-creation fields.
- Compare geographic and non-geographic feature sets.
- Recheck population prevalence and subgroup coverage for the modeling extract.
