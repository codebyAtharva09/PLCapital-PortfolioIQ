# Independent benchmark report

**This benchmark has not been run live.** No model result exists for these 35 cases. The cost is about 60 requests, or three daily free-tier windows, and quota for the current window is exhausted.

## Provenance

| Question | Finding |
|---|---|
| Does it exist? | Yes: `evaluation/independent_benchmark_dev_validation.json`, 22 development and 13 validation cases, frozen 2026-11-08. SHA-256 `6ccf357f…e563e504`, checked against `evaluation/independent_benchmark.sha256` |
| Where did it originate? | Written within this project, by the same development effort as the system |
| Used in development? | No run has been recorded. The cases are visible in the repository, so they cannot be called blind |
| Were expected outputs available? | Yes, stored in the file |
| Were they independently calculated? | Yes: `benchmark_oracles.py` computes them in plain Python from the CSV files without SQLite or production code, and a test checks them against the reference SQL |
| Overlap with official, unseen, shape, agent or held-out sets | None by exact normalised question text. Near-paraphrases were not assessed |
| Human review | None recorded. Please treat them as project-written development questions |

Held-out cases T01 to T16 were not opened, changed or used.

## Coverage

| Kind | Cases |
|---|---:|
| SQL (lookups, joins, aggregates, ranking, dates, financial metrics) | 22 |
| Sector exposure | 3 |
| Hybrid SQL plus exposure | 3 |
| Clarification | 2 |
| Missing data | 1 |
| Out of scope | 1 |
| Adversarial refusal | 2 |
| Invalid input | 1 |

## Results

| Metric | Result |
|---|---:|
| Total questions | 35 |
| Evaluated live | 0 |
| Correct answers | Not run |
| Correct refusals | Not run |
| Incorrect answers | Not run |
| Calculation failures | Not run |
| Routing failures | Not run |
| Infrastructure failures | Not run |
| Independent verification passes | Not run |
| Live repeatability rate | Not measured |

Offline, replaying the recorded responses of Q1 to Q10 reproduced 10 of 10 receipts identically. This shows the code after the model is deterministic. It does not establish that the model repeats itself.

Historical results remain in their own folders and are unchanged.

## Per-question status

| ID | Split | Category | Kind | Outcome |
|---|---|---|---|---|
| D01 | dev | Basic portfolio lookups | `sql` | Not run |
| D02 | dev | Basic portfolio lookups | `sql` | Not run |
| D03 | dev | Security and sector lookups | `sql` | Not run |
| D04 | dev | Security and sector lookups | `sql` | Not run |
| D05 | dev | Aggregations and totals | `sql` | Not run |
| D06 | dev | Aggregations and totals | `sql` | Not run |
| D07 | dev | Joins across tables | `sql` | Not run |
| D08 | dev | Joins across tables | `sql` | Not run |
| D09 | dev | Filtering and ranking | `sql` | Not run |
| D10 | dev | Filtering and ranking | `sql` | Not run |
| D11 | dev | Date-based questions | `sql` | Not run |
| D12 | dev | Date-based questions | `sql` | Not run |
| D13 | dev | Financial metrics | `sql` | Not run |
| D14 | dev | Financial metrics | `sql` | Not run |
| D15 | dev | Sector exposure | `exposure` | Not run |
| D16 | dev | Sector exposure | `exposure` | Not run |
| D17 | dev | Hybrid SQL plus exposure | `hybrid` | Not run |
| D18 | dev | Hybrid SQL plus exposure | `hybrid` | Not run |
| D19 | dev | Ambiguous portfolio references | `clarify` | Not run |
| D20 | dev | Empty and missing data | `no_data` | Not run |
| D21 | dev | Invalid requests | `decline` | Not run |
| D22 | dev | Adversarial inputs | `refuse` | Not run |
| V01 | validation | Basic portfolio lookups | `sql` | Not run |
| V02 | validation | Security and sector lookups | `sql` | Not run |
| V03 | validation | Aggregations and totals | `sql` | Not run |
| V04 | validation | Joins across tables | `sql` | Not run |
| V05 | validation | Filtering and ranking | `sql` | Not run |
| V06 | validation | Date-based questions | `sql` | Not run |
| V07 | validation | Financial metrics | `sql` | Not run |
| V08 | validation | Sector exposure | `exposure` | Not run |
| V09 | validation | Hybrid SQL plus exposure | `hybrid` | Not run |
| V10 | validation | Ambiguous portfolio references | `clarify` | Not run |
| V11 | validation | Empty and missing data | `sql` | Not run |
| V12 | validation | Invalid requests | `invalid` | Not run |
| V13 | validation | Adversarial inputs | `refuse` | Not run |
