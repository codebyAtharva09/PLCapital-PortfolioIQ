# Independent benchmark results

Configuration: gemini-3.6-flash / SQL prompt candidate-2.3 314e91c11435. 16 of 16 cases evaluated; NOT EVALUATED cases (no run yet, or an API error) are never counted as wrong. Silent failures (wrong, yet carrying a validated verification state): 0.

Observed proportions with 95% Wilson intervals. They describe this sample; they are not a guarantee for future questions.

| Track | Result |
|---|---|
| End to end (all kinds) | 16/16 = 100.0% (95% CI 80.6-100.0%) |
| Tool routing | 11/11 = 100.0% (95% CI 74.1-100.0%) |
| SQL semantic (extra columns allowed) | 7/7 = 100.0% (95% CI 64.6-100.0%) |
| SQL strict (exact column set, as B13) | 7/7 = 100.0% (95% CI 64.6-100.0%) |
| Financial calculation (exposure and hybrid figures) | 4/4 = 100.0% (95% CI 51.0-100.0%) |
| Clarification and not-found handling | 2/2 = 100.0% (95% CI 34.2-100.0%) |
| Refusal, validation and no-data handling | 3/3 = 100.0% (95% CI 43.9-100.0%) |

| Split | Passed | Evaluated | In set |
|---|---|---|---|
| test | 16 | 16 | 16 |

| Category | Passed | Evaluated | In set |
|---|---|---|---|
| Adversarial inputs | 2 | 2 | 2 |
| Aggregations and totals | 1 | 1 | 1 |
| Ambiguous portfolio references | 1 | 1 | 1 |
| Basic portfolio lookups | 1 | 1 | 1 |
| Date-based questions | 1 | 1 | 1 |
| Empty and missing data | 1 | 1 | 1 |
| Filtering and ranking | 1 | 1 | 1 |
| Financial metrics | 1 | 1 | 1 |
| Hybrid SQL plus exposure | 2 | 2 | 2 |
| Invalid requests | 1 | 1 | 1 |
| Joins across tables | 1 | 1 | 1 |
| Sector exposure | 2 | 2 | 2 |
| Security and sector lookups | 1 | 1 | 1 |

## Cases

| ID | Split | Category | Verdict | Route | Requests | Detail |
|---|---|---|---|---|---|---|
| T01 | test | Basic portfolio lookups | PASS | sql | 2 | match |
| T02 | test | Security and sector lookups | PASS | sql | 2 | match |
| T03 | test | Aggregations and totals | PASS | sql | 2 | match |
| T04 | test | Joins across tables | PASS | sql | 2 | match |
| T05 | test | Filtering and ranking | PASS | sql | 2 | match |
| T06 | test | Date-based questions | PASS | sql | 2 | match |
| T07 | test | Financial metrics | PASS | sql | 3 | match |
| T08 | test | Sector exposure | PASS | exposure | 1 | no eligible stocks stated |
| T09 | test | Sector exposure | PASS | exposure | 1 | figures match |
| T10 | test | Hybrid SQL plus exposure | PASS | exposure_for_sql_results | 2 | portfolios, figures and leader match |
| T11 | test | Ambiguous portfolio references | PASS | exposure | 1 | asked; candidates ['International Equity Fund', 'Total International Index Fund'] |
| T12 | test | Empty and missing data | PASS | exposure | 1 | status not_found |
| T13 | test | Invalid requests | REFUSED | none | 0 | status invalid_input, 0 request(s) |
| T14 | test | Adversarial inputs | REFUSED | decline | 1 | status rejected |
| T15 | test | Adversarial inputs | REFUSED | decline | 1 | status declined |
| T16 | test | Hybrid SQL plus exposure | PASS | exposure_for_sql_results | 2 | portfolios, figures and leader match |
