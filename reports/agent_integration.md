# Agent integration check

Verdicts: PASS 4. Behaved as expected: 4 of 4 evaluated (4 in the set). API errors (kept apart from correctness): 0. API requests: 6; cache hits: 2.

Expected behaviour is fixed in advance in `evaluation/agent_integration_questions.json`; receipts are recorded in `reports/runs/agent-*/agent_outputs.jsonl`. PASS: behaved as expected. FAIL: did not. REFUSED: declined, as expected. NOT EVALUATED: no valid receipt (an API error, the request budget, or no live run has asked the question yet); never counted as wrong.

| Case | Question | Verdict | Route | Status | Explanation | Verification | API requests (cache hits) | Gemini ms | Tokens in / out / thinking | API error |
|---|---|---|---|---|---|---|---|---|---|---|
| A1 | How many holdings does the Dividend Aristocrats Fund have? | PASS | sql | answered | Behaved as expected (sql, answered). holding_count: 4. | RESULT_VALIDATED | 1 (1) | 3117.4 | 715 / 86 / 0 | - |
| A2 | What are the sector exposures for the Tech Innovation Fund? | PASS | exposure | answered | Behaved as expected (exposure, answered). Tech Innovation Fund: 1 sector(s) across an eligible stock weight of 100.00% of the portfolio. Largest sector: Technology, 100.00% of the portfolio (Method A) and 100.00% of the equity sleeve (Method B). | INDEPENDENTLY_RECONCILED | 0 (1) | 0.0 | 0 / 0 / 0 | - |
| A3 | Sector exposure for the international portfolio | PASS | exposure | needs_clarification | Behaved as expected (exposure, needs_clarification). 'international portfolio' is a generic description, so it cannot select a portfolio on its own. Which portfolio do you mean? | VERIFICATION_UNAVAILABLE | 2 (0) | 1560.3 | 710 / 80 / 0 | - |
| A4 | Which high-risk portfolio has the largest Technology exposure? | PASS | exposure_for_sql_results | answered | Behaved as expected (exposure_for_sql_results, answered). The SQL step returned 4 portfolio(s): Growth Equity Fund, Tech Innovation Fund, Small Cap Value Fund, Emerging Markets Fund. Highest Technology exposure: Tech Innovation Fund, 100.00% of the portfoli... | RESULT_VALIDATED | 3 (0) | 8014.6 | 3402 / 164 / 0 | - |

## Provenance

Raw responses are saved the moment they arrive: in `reports/api_usage.jsonl` (the entry with the request hash shown) and in `.cache/llm_responses/<hash>.json` (not committed).

| Case | Run | Model (served version) | Router version | SQL prompt version | Receipt | Raw responses |
|---|---|---|---|---|---|---|
| A1 | agent-20261107-181258 | gemini-3.6-flash (gemini-3.6-flash) | 676786d09950 | d5a914eb71d5 | cdf2f9152f2f40248ec2fb26e0f493a2 | reports/api_usage.jsonl: step 1 router 3539e88fcbf2e8fc (api), step 2 sql_query b1b494be5881cee1 (cache) |
| A2 | agent-20261108-135856 | gemini-3.6-flash (gemini-3.6-flash) | 676786d09950 | - | d9593852cf3246ee94f864ca0188f0bc | reports/api_usage.jsonl: step 1 router ff131f2ad49d4a6c (cache) |
| A3 | agent-20261108-135856 | gemini-3.6-flash (gemini-3.6-flash) | 676786d09950 | - | 176fee88d37b4d72844e29eeb633710f | reports/api_usage.jsonl: step 1 router 92bb9fe58d84dbca (api) |
| A4 | agent-20261107-181157 | gemini-3.6-flash (gemini-3.6-flash) | 676786d09950 | d5a914eb71d5 | 13919fc73df34f9d979dd2c225acad9e | reports/api_usage.jsonl: step 1 router f8039e753a1a5450 (api), step 2 sql_query d0ca487aef121c12 (api) |
