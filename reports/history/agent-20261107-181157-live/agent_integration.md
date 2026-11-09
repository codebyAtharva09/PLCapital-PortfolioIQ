# Agent integration check

Verdicts: PASS 1, NOT EVALUATED 3. Behaved as expected: 1 of 1 evaluated (4 in the set). API errors (kept apart from correctness): 0. API requests: 3; cache hits: 0.

Expected behaviour is fixed in advance in `evaluation/agent_integration_questions.json`; receipts are recorded in `reports/runs/agent-*/agent_outputs.jsonl`. PASS: behaved as expected. FAIL: did not. REFUSED: declined, as expected. NOT EVALUATED: no valid receipt (an API error, the request budget, or no live run has asked the question yet); never counted as wrong.

| Case | Question | Verdict | Route | Status | Explanation | Verification | API requests (cache hits) | Gemini ms | Tokens in / out / thinking | API error |
|---|---|---|---|---|---|---|---|---|---|---|
| A1 | How many holdings does the Dividend Aristocrats Fund have? | NOT EVALUATED | - | - | Not evaluated: no live run has asked this question yet. | - | - (-) | - | - / - / - | - |
| A2 | What are the sector exposures for the Tech Innovation Fund? | NOT EVALUATED | - | - | Not evaluated: no live run has asked this question yet. | - | - (-) | - | - / - / - | - |
| A3 | Sector exposure for the international portfolio | NOT EVALUATED | - | - | Not evaluated: no live run has asked this question yet. | - | - (-) | - | - / - / - | - |
| A4 | Which high-risk portfolio has the largest Technology exposure? | PASS | exposure_for_sql_results | answered | Behaved as expected (exposure_for_sql_results, answered). The SQL step returned 4 portfolio(s): Growth Equity Fund, Tech Innovation Fund, Small Cap Value Fund, Emerging Markets Fund. Highest Technology exposure: Tech Innovation Fund, 100.00% of the portfoli... | RESULT_VALIDATED | 3 (0) | 8014.6 | 3402 / 164 / 0 | - |

## Provenance

Raw responses are saved the moment they arrive: in `reports/api_usage.jsonl` (the entry with the request hash shown) and in `.cache/llm_responses/<hash>.json` (not committed).

| Case | Run | Model (served version) | Router version | SQL prompt version | Receipt | Raw responses |
|---|---|---|---|---|---|---|
| A1 | - | - | - | - | - | - |
| A2 | - | - | - | - | - | - |
| A3 | - | - | - | - | - | - |
| A4 | agent-20261107-181157 | gemini-3.6-flash (gemini-3.6-flash) | 676786d09950 | d5a914eb71d5 | 13919fc73df34f9d979dd2c225acad9e | reports/api_usage.jsonl: step 1 router f8039e753a1a5450 (api), step 2 sql_query d0ca487aef121c12 (api) |
