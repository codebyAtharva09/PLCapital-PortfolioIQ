# Held-out benchmark T01-T16: frozen candidate-2.3

Live, `gemini-3.6-flash`, thinking LOW, 9 November 2026. SQL prompt `314e91c11435`, shape policy 2.3, unchanged comparator, held-out file SHA-256 `988f189cec1480759ab0b9f0f4820ef9e20864803d52d417f9c70aafcc005266`. Requests were serial, paced 13,000 ms apart, with two separately supplied keys used one after the other (key values are not recorded). No prompt, policy, comparator or reference changed during the run.

**How independent this is.** No model had answered these questions and no prompt rule or few-shot example came from them. The shape policy's offline false-positive check did run over the held-out reference SQL earlier, and three policy bugs that check exposed (on T01, T07 and T10's selection SQL) were fixed before this run. So this is strong evidence for the prompt and model, and slightly less clean for the policy.

**How to read it.** 16 cases cover 7 SQL, 2 exposure, 2 hybrid, 1 clarification, 1 not-found, 1 invalid input and 2 refusals. A run of one. The primary SQL acceptance allows extra columns; strict column matching is reported separately.

Requests: 25 sent, 24 succeeded. Silent failures: 0. Gemini latency per request: median 2,372 ms, max 9,250 ms.

| Track | Result |
|---|---|
| End to end | 16/16 = 100.0% (95% Wilson 80.6-100.0%) |
| Tool routing | 11/11 = 100.0% (95% Wilson 74.1-100.0%) |
| SQL semantic (extra columns allowed) | 7/7 = 100.0% (95% Wilson 64.6-100.0%) |
| SQL strict (exact column set) | 7/7 = 100.0% (95% Wilson 64.6-100.0%) |
| Financial calculation | 4/4 = 100.0% (95% Wilson 51.0-100.0%) |
| Clarification and not-found | 2/2 = 100.0% (95% Wilson 34.2-100.0%) |
| Refusal and validation | 3/3 = 100.0% (95% Wilson 43.9-100.0%) |

| ID | Kind | Verdict | Strict | Route | Status | Requests | Gemini ms | Total ms | Comparator or check | Generated SQL |
|---|---|---|---|---|---|---|---|---|---|---|
| T01 | sql | PASS | yes | sql | answered | 2 | 11,374 | 22,333 | match | `SELECT creation_date FROM portfolios WHERE portfolio_name = 'Tech Innovation Fund';` |
| T02 | sql | PASS | yes | sql | answered | 2 | 3,748 | 18,977 | match | `SELECT s.company_name FROM securities s JOIN sectors sec ON sec.sector_id = s.sector_id WHERE sec.sector_name = 'Consumer Staples' AND s.asset_type = 'Stock';` |
| T03 | sql | PASS | yes | sql | answered | 2 | 3,855 | 25,492 | match | `SELECT SUM(h.quantity) AS total_quantity FROM holdings h JOIN securities s ON s.security_id = h.security_id WHERE s.symbol = 'MSFT';` |
| T04 | sql | PASS | yes | sql | answered | 2 | 6,998 | 29,098 | match | `SELECT DISTINCT sec.sector_name FROM holdings h JOIN portfolios p ON p.portfolio_id = h.portfolio_id JOIN securities s ON s.security_id = h.security_id JOIN sectors sec ON sec.sector_id = s.sector_id WHERE p.portfolio_name = 'Balanced Portfolio' AND s.asset_type = 'Stock'` |
| T05 | sql | PASS | yes | sql | answered | 2 | 6,932 | 25,209 | match | `SELECT portfolio_name FROM portfolios WHERE target_risk_level = 'High' ORDER BY total_aum DESC LIMIT 1;` |
| T06 | sql | PASS | yes | sql | answered | 2 | 4,331 | 23,849 | match | `SELECT COUNT(*) FROM transactions WHERE transaction_date BETWEEN '2023-10-02' AND '2023-10-08';` |
| T07 | sql | PASS | yes | sql | answered | 3 | 7,621 | 42,505 | match | `SELECT p.portfolio_name, pp.performance_date, pp.sharpe_ratio FROM portfolio_performance pp JOIN portfolios p ON p.portfolio_id = pp.portfolio_id ORDER BY pp.sharpe_ratio DESC LIMIT 1` |
| T08 | exposure | PASS | - | exposure | answered | 1 | 2,506 | 10,055 | no eligible stocks stated | - |
| T09 | exposure | PASS | - | exposure | answered | 1 | 1,988 | 12,485 | figures match | - |
| T10 | hybrid | PASS | - | exposure_for_sql_results | answered | 2 | 6,087 | 27,727 | portfolios, figures and leader match | `SELECT p.portfolio_name FROM portfolios p JOIN holdings h ON p.portfolio_id = h.portfolio_id GROUP BY p.portfolio_id, p.portfolio_name HAVING COUNT(h.holding_id) >= 10;` |
| T11 | clarify | PASS | yes | exposure | needs_clarification | 1 | 2,443 | 11,726 | asked; candidates ['International Equity Fund', 'Total International Index Fund'] | - |
| T12 | not_found | PASS | - | exposure | not_found | 1 | 1,912 | 1,925 | status not_found | - |
| T13 | invalid | REFUSED | - | none | invalid_input | 0 | n/a | 0 | status invalid_input, 0 request(s) | - |
| T14 | refuse | REFUSED | - | decline | rejected | 1 | 1,696 | 12,781 | status rejected | - |
| T15 | refuse | REFUSED | - | decline | declined | 1 | 5,041 | 16,348 | status declined | - |
| T16 | hybrid | PASS | - | exposure_for_sql_results | answered | 2 | 7,101 | 25,811 | portfolios, figures and leader match | `SELECT p.portfolio_name FROM portfolios p JOIN holdings h ON p.portfolio_id = h.portfolio_id JOIN securities s ON h.security_id = s.security_id WHERE s.symbol = 'NVDA'` |

Full receipts: `runs/`. Raw responses: `api_usage.jsonl`. Machine-readable scores: `independent_benchmark_test.json`.
