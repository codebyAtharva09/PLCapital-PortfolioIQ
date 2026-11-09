# SQL Query Tool evaluation

Model `gemini-3.6-flash`; context version `d5f1098033f7`; runs scored together: 20261109-131514.

| Verdicts | Result |
|---|---|
| Official Q1-Q8 | none |
| Unseen U1-U10 | none |
| Safety S1-S3 | none |
| Result-shape set S01-S17 | PASS 15, FAIL 2 |
| API errors (kept apart from model correctness) | 0 |

| Metric | Result |
|---|---|
| Official Q1-Q8, strict | 0/0 evaluated (0 in set) |
| Official Q1-Q8, relaxed | 0/0 evaluated (0 in set) |
| Unseen questions, strict | 0/0 evaluated (0 in set) |
| Unseen questions, relaxed | 0/0 evaluated (0 in set) |
| Result-shape set, strict | 15/17 evaluated (17 in set) |
| Result-shape set, relaxed | 17/17 evaluated (17 in set) |
| Outcomes, official + unseen: correct / relaxed only / incorrect / failed / refused / not evaluated | 0 / 0 / 0 / 0 / 0 / 0 |
| Safety prompts refused, nothing executed | 0/0 evaluated (0 in set) |
| Database unchanged (SHA-256 before and after every run) | True |
| Repairs used | 2 |
| Gemini requests behind the scored answers / cache hits | 19 / 0 |
| Gemini request latency, successful requests: min / median / mean / p95 / max (client-observed) | 1,982 ms / 3,544 ms / 4,058 ms / n/a / 10,438 ms (n = 17) |
| Cases with no latency (Gemini error, budget stop or not run), counted apart | 0 |
| SQL execution latency: min / median / mean / p95 / max | 0.227 ms / 0.374 ms / 0.628 ms / n/a / 2.99 ms (n = 17) |
| Tokens prompt / output / thinking | 67083 / 2038 / 5221 |
| API requests in the ledger during these runs | 19 {'ok': 19} |

## Runs

Each live run has its own directory under `reports/runs/`, and earlier runs are never modified.

| Run | Started | Model, thinking | Pacing (s) | Request cap | Requests sent | SQL prompt version | Cases asked | Stopped |
|---|---|---|---|---|---|---|---|---|
| 20261109-131514 | 2026-11-09T07:45:14+00:00 | gemini-3.6-flash, LOW | 13.0 | 20 | 19 | 314e91c11435 | S01, S02, S03, S04, S05, S06, S07, S08, S09, S10, S11, S12, S13, S14, S15, S16, S17 | no |

## Results by question

PASS: the executed result matches the reference result exactly. FAIL: a different result, no valid SQL, a declined answerable question, or a safety prompt that was not refused. REFUSED: a safety prompt was refused and nothing was executed, which is the expected outcome. NOT EVALUATED: no valid answer is recorded (an API error, the request budget, or a run that stopped before reaching the case); it is never counted as wrong.

| ID | Question | Verdict | Explanation | Verification | API requests (cache hits) | Gemini latency | Tokens in / out / thinking | API error |
|---|---|---|---|---|---|---|---|---|
| S01 | What is the highest beta recorded for the Growth Equity Fund? | PASS | The executed result matches the reference result exactly. | RESULT_VALIDATED | 1 (0) | 4,783 ms | 3517 / 92 / 404 | - |
| S02 | What is the date of the most recent transaction in the Growth Equity Fund? | PASS | The executed result matches the reference result exactly. | RESULT_VALIDATED | 1 (0) | 4,107 ms | 3520 / 91 / 413 | - |
| S03 | What is the most recent tracking error for the Balanced Portfolio? | PASS | The executed result matches the reference result exactly. | RESULT_VALIDATED | 1 (0) | 2,181 ms | 3517 / 109 / 0 | - |
| S04 | What is the total cost basis of the Energy holdings across all portfolios? | PASS | The executed result matches the reference result exactly. | RESULT_VALIDATED | 1 (0) | 3,275 ms | 3519 / 108 / 292 | - |
| S05 | Give the symbol and current price of the most expensive bond. | PASS | The executed result matches the reference result exactly. | RESULT_VALIDATED | 1 (0) | 2,797 ms | 3517 / 75 / 195 | - |
| S06 | List the name and creation date of every active portfolio. | PASS | The executed result matches the reference result exactly. | RESULT_VALIDATED | 1 (0) | 2,186 ms | 3516 / 65 / 0 | - |
| S07 | Show me the top 3 holdings by quantity in the Small Cap Value Fund. | PASS | The executed result matches the reference result exactly. | RESULT_VALIDATED | 1 (0) | 4,629 ms | 3521 / 139 / 477 | - |
| S08 | List each Financials stock with its listing date. | PASS | The executed result matches the reference result exactly. | RESULT_VALIDATED | 1 (0) | 4,576 ms | 3515 / 98 / 442 | - |
| S09 | How many portfolios are there for each status? | PASS | The executed result matches the reference result exactly. | RESULT_VALIDATED | 1 (0) | 1,982 ms | 3514 / 64 / 0 | - |
| S10 | What is the total stated AUM of the portfolios at each target risk level? | PASS | The executed result matches the reference result exactly. | RESULT_VALIDATED | 2 (0) | 10,438 ms | 7125 / 176 / 1291 | - |
| S11 | What percentage of all securities are bonds? | FAIL | Relaxed match only (match with 2 extra column(s)); the strict comparison is the score. | RESULT_VALIDATED | 2 (0) | 4,080 ms | 7183 / 291 / 0 | - |
| S12 | Which sector has the highest total market cap among its stocks? Give the sector name. | PASS | The executed result matches the reference result exactly. | RESULT_VALIDATED | 1 (0) | 3,544 ms | 3522 / 112 / 377 | - |
| S13 | List the names of the portfolios that hold at least one bond. | PASS | The executed result matches the reference result exactly. | RESULT_VALIDATED | 1 (0) | 3,245 ms | 3518 / 103 / 161 | - |
| S14 | Which securities are held in the Emerging Markets Fund? Give their symbols. | PASS | The executed result matches the reference result exactly. | RESULT_VALIDATED | 1 (0) | 4,674 ms | 3519 / 97 / 293 | - |
| S15 | How many transactions did the Tech Innovation Fund make in 2023? | PASS | The executed result matches the reference result exactly. | RESULT_VALIDATED | 1 (0) | 5,992 ms | 3521 / 130 / 552 | - |
| S16 | What is the average current price of the Healthcare stocks? | PASS | The executed result matches the reference result exactly. | RESULT_VALIDATED | 1 (0) | 3,500 ms | 3516 / 97 / 324 | - |
| S17 | For each sector, show the number of securities and what percentage of all securities it represents. | FAIL | Relaxed match only (match with 1 extra column(s)); the strict comparison is the score. | RESULT_VALIDATED | 1 (0) | 2,996 ms | 3523 / 191 / 0 | - |

## Provenance

Every successful raw response is saved the moment it arrives: in `reports/shape_v2_3/api_usage.jsonl` (the entry with the request hash shown below) and in `.cache/llm_responses/<hash>.json` (not committed). `cache` means the response was reused from an identical earlier request, at no quota cost.

| ID | Run | Model (served version) | SQL prompt version | Context version | Raw response |
|---|---|---|---|---|---|
| S01 | 20261109-131514 | gemini-3.6-flash (gemini-3.6-flash) | 314e91c11435 | d5f1098033f7 | reports/shape_v2_3/api_usage.jsonl: 3a6cdafaf38c926a (api) |
| S02 | 20261109-131514 | gemini-3.6-flash (gemini-3.6-flash) | 314e91c11435 | d5f1098033f7 | reports/shape_v2_3/api_usage.jsonl: 6b9b8283c02428f8 (api) |
| S03 | 20261109-131514 | gemini-3.6-flash (gemini-3.6-flash) | 314e91c11435 | d5f1098033f7 | reports/shape_v2_3/api_usage.jsonl: 18adcb6eebdf89f4 (api) |
| S04 | 20261109-131514 | gemini-3.6-flash (gemini-3.6-flash) | 314e91c11435 | d5f1098033f7 | reports/shape_v2_3/api_usage.jsonl: fa00eeda27944be6 (api) |
| S05 | 20261109-131514 | gemini-3.6-flash (gemini-3.6-flash) | 314e91c11435 | d5f1098033f7 | reports/shape_v2_3/api_usage.jsonl: c20ed43baf0a44fe (api) |
| S06 | 20261109-131514 | gemini-3.6-flash (gemini-3.6-flash) | 314e91c11435 | d5f1098033f7 | reports/shape_v2_3/api_usage.jsonl: c14592fb4c525662 (api) |
| S07 | 20261109-131514 | gemini-3.6-flash (gemini-3.6-flash) | 314e91c11435 | d5f1098033f7 | reports/shape_v2_3/api_usage.jsonl: 5063512b9bcdbf4c (api) |
| S08 | 20261109-131514 | gemini-3.6-flash (gemini-3.6-flash) | 314e91c11435 | d5f1098033f7 | reports/shape_v2_3/api_usage.jsonl: 4a4f5c2e5a0a0cc2 (api) |
| S09 | 20261109-131514 | gemini-3.6-flash (gemini-3.6-flash) | 314e91c11435 | d5f1098033f7 | reports/shape_v2_3/api_usage.jsonl: 68da195580a90fd5 (api) |
| S10 | 20261109-131514 | gemini-3.6-flash (gemini-3.6-flash) | 314e91c11435 | d5f1098033f7 | reports/shape_v2_3/api_usage.jsonl: 024e07fb6ef979e0 (api), 59bc692c52eb8035 (api) |
| S11 | 20261109-131514 | gemini-3.6-flash (gemini-3.6-flash) | 314e91c11435 | d5f1098033f7 | reports/shape_v2_3/api_usage.jsonl: 64ddf928104df3e1 (api), a8c1adc689c8d3ab (api) |
| S12 | 20261109-131514 | gemini-3.6-flash (gemini-3.6-flash) | 314e91c11435 | d5f1098033f7 | reports/shape_v2_3/api_usage.jsonl: 8e6669deb022f1cf (api) |
| S13 | 20261109-131514 | gemini-3.6-flash (gemini-3.6-flash) | 314e91c11435 | d5f1098033f7 | reports/shape_v2_3/api_usage.jsonl: 38890f045358627d (api) |
| S14 | 20261109-131514 | gemini-3.6-flash (gemini-3.6-flash) | 314e91c11435 | d5f1098033f7 | reports/shape_v2_3/api_usage.jsonl: 43de7232fd563d9c (api) |
| S15 | 20261109-131514 | gemini-3.6-flash (gemini-3.6-flash) | 314e91c11435 | d5f1098033f7 | reports/shape_v2_3/api_usage.jsonl: 9675433ee7d3d87f (api) |
| S16 | 20261109-131514 | gemini-3.6-flash (gemini-3.6-flash) | 314e91c11435 | d5f1098033f7 | reports/shape_v2_3/api_usage.jsonl: c41709963e965cdd (api) |
| S17 | 20261109-131514 | gemini-3.6-flash (gemini-3.6-flash) | 314e91c11435 | d5f1098033f7 | reports/shape_v2_3/api_usage.jsonl: ee52bd256cb31aac (api) |

## Generated SQL

**S01** (correct)

```sql
SELECT MAX(rm.beta) AS max_beta FROM risk_metrics rm JOIN portfolios p ON p.portfolio_id = rm.portfolio_id WHERE p.portfolio_name = 'Growth Equity Fund';
```

**S02** (correct)

```sql
SELECT MAX(t.transaction_date) AS max_transaction_date FROM transactions t JOIN portfolios p ON p.portfolio_id = t.portfolio_id WHERE p.portfolio_name = 'Growth Equity Fund'
```

**S03** (correct)

```sql
SELECT rm.tracking_error FROM risk_metrics rm JOIN portfolios p ON p.portfolio_id = rm.portfolio_id WHERE p.portfolio_name = 'Balanced Portfolio' AND rm.calculation_date = (SELECT MAX(calculation_date) FROM risk_metrics);
```

**S04** (correct)

```sql
SELECT SUM(h.cost_basis) AS total_cost_basis FROM holdings h JOIN securities s ON h.security_id = s.security_id JOIN sectors sec ON sec.sector_id = s.sector_id WHERE sec.sector_name = 'Energy';
```

**S05** (correct)

```sql
SELECT symbol, current_price FROM securities WHERE asset_type = 'Bond' ORDER BY current_price DESC LIMIT 1;
```

**S06** (correct)

```sql
SELECT portfolio_name, creation_date FROM portfolios WHERE status = 'Active'
```

**S07** (correct)

```sql
SELECT h.holding_id, s.symbol, s.company_name, h.quantity FROM holdings h JOIN securities s ON s.security_id = h.security_id JOIN portfolios p ON p.portfolio_id = h.portfolio_id WHERE p.portfolio_name = 'Small Cap Value Fund' ORDER BY h.quantity DESC LIMIT 3
```

**S08** (correct)

```sql
SELECT s.symbol, s.listing_date FROM securities s JOIN sectors sec ON sec.sector_id = s.sector_id WHERE sec.sector_name = 'Financials' AND s.asset_type = 'Stock'
```

**S09** (correct)

```sql
SELECT status, COUNT(*) AS portfolio_count FROM portfolios GROUP BY status ORDER BY portfolio_count DESC;
```

**S10** (correct)

```sql
SELECT target_risk_level, SUM(total_aum) AS total_aum FROM portfolios GROUP BY target_risk_level ORDER BY total_aum DESC;
```

**S11** (relaxed_only)

```sql
WITH bond_cnt AS (SELECT COUNT(*) AS bond_count FROM securities WHERE asset_type = 'Bond'), total_cnt AS (SELECT COUNT(*) AS total_count FROM securities) SELECT bond_cnt.bond_count, total_cnt.total_count, ROUND(bond_cnt.bond_count * 100.0 / total_cnt.total_count, 2) AS bond_percentage FROM bond_cnt, total_cnt;
```

**S12** (correct)

```sql
SELECT sec.sector_name FROM sectors sec JOIN securities s ON sec.sector_id = s.sector_id WHERE s.asset_type = 'Stock' GROUP BY sec.sector_id, sec.sector_name ORDER BY SUM(s.market_cap) DESC LIMIT 1
```

**S13** (correct)

```sql
SELECT DISTINCT p.portfolio_name FROM portfolios p JOIN holdings h ON p.portfolio_id = h.portfolio_id JOIN securities s ON s.security_id = h.security_id WHERE s.asset_type = 'Bond';
```

**S14** (correct)

```sql
SELECT s.symbol FROM holdings h JOIN securities s ON s.security_id = h.security_id JOIN portfolios p ON p.portfolio_id = h.portfolio_id WHERE p.portfolio_name = 'Emerging Markets Fund';
```

**S15** (correct)

```sql
SELECT COUNT(*) AS transaction_count FROM transactions t JOIN portfolios p ON p.portfolio_id = t.portfolio_id WHERE p.portfolio_name = 'Tech Innovation Fund' AND t.transaction_date >= '2023-01-01' AND t.transaction_date <= '2023-12-31';
```

**S16** (correct)

```sql
SELECT AVG(s.current_price) AS average_current_price FROM securities s JOIN sectors sec ON sec.sector_id = s.sector_id WHERE sec.sector_name = 'Healthcare' AND s.asset_type = 'Stock';
```

**S17** (relaxed_only)

```sql
WITH sector_counts AS (SELECT sec.sector_name, COUNT(s.security_id) AS security_count FROM sectors sec JOIN securities s ON s.sector_id = sec.sector_id GROUP BY sec.sector_id, sec.sector_name), total AS (SELECT COUNT(*) AS total_count FROM securities) SELECT sc.sector_name, sc.security_count, t.total_count, ROUND(sc.security_count * 100.0 / t.total_count, 2) AS percentage FROM sector_counts sc CROSS JOIN total t ORDER BY percentage DESC;
```
