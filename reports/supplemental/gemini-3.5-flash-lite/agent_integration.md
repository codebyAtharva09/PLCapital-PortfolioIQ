# Agent integration check

Verdicts: PASS 2, NOT EVALUATED 2. Behaved as expected: 2 of 2 evaluated (4 in the set). API errors (kept apart from correctness): 0. API requests: 2; cache hits: 1.

Expected behaviour is fixed in advance in `evaluation/agent_integration_questions.json`; receipts are recorded in `reports/runs/agent-*/agent_outputs.jsonl`. PASS: behaved as expected. FAIL: did not. REFUSED: declined, as expected. NOT EVALUATED: no valid receipt (an API error, the request budget, or no live run has asked the question yet); never counted as wrong.

| Case | Question | Verdict | Route | Status | Explanation | Verification | API requests (cache hits) | Gemini ms | Tokens in / out / thinking | API error |
|---|---|---|---|---|---|---|---|---|---|---|
| A1 | How many holdings does the Dividend Aristocrats Fund have? | NOT EVALUATED | - | - | Not evaluated: no live run has asked this question yet. | - | - (-) | - | - / - / - | - |
| A2 | What are the sector exposures for the Tech Innovation Fund? | PASS | exposure | answered | Behaved as expected (exposure, answered). Tech Innovation Fund: 1 sector(s) across an eligible stock weight of 100.00% of the portfolio. Largest sector: Technology, 100.00% of the portfolio (Method A) and 100.00% of the equity sleeve (Method B). | INDEPENDENTLY_RECONCILED | 0 (1) | 0.0 | 0 / 0 / 0 | - |
| A3 | Sector exposure for the international portfolio | PASS | exposure | needs_clarification | Behaved as expected (exposure, needs_clarification). 'international portfolio' is a generic description, so it cannot select a portfolio on its own. Which portfolio do you mean? | VERIFICATION_UNAVAILABLE | 2 (0) | 3306.5 | 710 / 79 / 0 | - |
| A4 | Which high-risk portfolio has the largest Technology exposure? | NOT EVALUATED | - | - | Not evaluated: no live run has asked this question yet. | - | - (-) | - | - / - / - | - |

## Provenance

Raw responses are saved the moment they arrive: in `reports/supplemental/gemini-3.5-flash-lite/api_usage.jsonl` (the entry with the request hash shown) and in `.cache/llm_responses/<hash>.json` (not committed).

| Case | Run | Model (served version) | Router version | SQL prompt version | Receipt | Raw responses |
|---|---|---|---|---|---|---|
| A1 | - | - | - | - | - | - |
| A2 | agent-20261107-204637 | gemini-3.5-flash-lite (gemini-3.5-flash-lite) | 676786d09950 | - | 57f56d5346644eebbcf56f2c24b3ca6d | reports/supplemental/gemini-3.5-flash-lite/api_usage.jsonl: step 1 router b33c939c88c784a4 (cache) |
| A3 | agent-20261107-204637 | gemini-3.5-flash-lite (gemini-3.5-flash-lite) | 676786d09950 | - | cb30cc9c995f479fbed81c8ebf78d685 | reports/supplemental/gemini-3.5-flash-lite/api_usage.jsonl: step 1 router 8fd45f8251653ffb (api) |
| A4 | - | - | - | - | - | - |
