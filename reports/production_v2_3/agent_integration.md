# Agent integration check

Verdicts: PASS 4. Behaved as expected: 4 of 4 evaluated (4 in the set). API errors (kept apart from correctness): 0. API requests: 1; cache hits: 5.

Expected behaviour is fixed in advance in `evaluation/agent_integration_questions.json`; receipts are recorded in `reports/runs/agent-*/agent_outputs.jsonl`. PASS: behaved as expected. FAIL: did not. REFUSED: declined, as expected. NOT EVALUATED: no valid receipt (an API error, the request budget, or no live run has asked the question yet); never counted as wrong.

| Case | Question | Verdict | Route | Status | Explanation | Verification | API requests (cache hits) | Gemini request latency | Total latency | Tokens in / out / thinking | API error |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A1 | How many holdings does the Dividend Aristocrats Fund have? | PASS | sql | answered | Behaved as expected (sql, answered). holding_count: 4. | RESULT_VALIDATED | 0 (2) | n/a | 66.18 ms | 0 / 0 / 0 | - |
| A2 | What are the sector exposures for the Tech Innovation Fund? | PASS | exposure | answered | Behaved as expected (exposure, answered). Tech Innovation Fund: 1 sector(s) across an eligible stock weight of 100.00% of the portfolio. Largest sector: Technology, 100.00% of the portfolio (Method A) and 100.00% of the equity sleeve (Method B). | INDEPENDENTLY_RECONCILED | 0 (1) | n/a | 3.17 ms | 0 / 0 / 0 | - |
| A3 | Sector exposure for the international portfolio | PASS | exposure | needs_clarification | Behaved as expected (exposure, needs_clarification). 'international portfolio' is a generic description, so it cannot select a portfolio on its own. Which portfolio do you mean? | VERIFICATION_UNAVAILABLE | 0 (1) | n/a | 9.64 ms | 0 / 0 / 0 | - |
| A4 | Which high-risk portfolio has the largest Technology exposure? | PASS | exposure_for_sql_results | answered | Behaved as expected (exposure_for_sql_results, answered). The SQL step returned 4 portfolio(s): Growth Equity Fund, Tech Innovation Fund, Small Cap Value Fund, Emerging Markets Fund. Highest Technology exposure: Tech Innovation Fund, 100.00% of the portfoli... | RESULT_VALIDATED | 1 (1) | 6,147 ms | 6,228 ms | 3515 / 65 / 131 | - |

## Provenance

Raw responses are saved the moment they arrive: in `reports/production_v2_3/api_usage.jsonl` (the entry with the request hash shown) and in `.cache/llm_responses/<hash>.json` (not committed).

| Case | Run | Model (served version) | Router version | SQL prompt version | Receipt | Raw responses |
|---|---|---|---|---|---|---|
| A1 | agent-20261109-131006 | gemini-3.6-flash (gemini-3.6-flash) | 676786d09950 | 314e91c11435 | 2db69ad921a64f1b8aeff4090f421bdb | reports/production_v2_3/api_usage.jsonl: step 1 router 3539e88fcbf2e8fc (cache), step 2 sql_query b78e3af9d72c530c (cache) |
| A2 | agent-20261109-131006 | gemini-3.6-flash (gemini-3.6-flash) | 676786d09950 | - | 7bcaa266a012417297d4defa3cfd4c64 | reports/production_v2_3/api_usage.jsonl: step 1 router ff131f2ad49d4a6c (cache) |
| A3 | agent-20261109-131006 | gemini-3.6-flash (gemini-3.6-flash) | 676786d09950 | - | fe2b6b96e46b400db0f55d138b7c04a0 | reports/production_v2_3/api_usage.jsonl: step 1 router 92bb9fe58d84dbca (cache) |
| A4 | agent-20261109-131006 | gemini-3.6-flash (gemini-3.6-flash) | 676786d09950 | 314e91c11435 | 1ce89d2569f54be3860a402b248c973b | reports/production_v2_3/api_usage.jsonl: step 1 router f8039e753a1a5450 (cache), step 2 sql_query 468dcebc8dc48dc4 (api) |
