# Agent integration check

Verdicts: NOT EVALUATED 4. Behaved as expected: 0 of 0 evaluated (4 in the set). API errors (kept apart from correctness): 0. API requests: 0; cache hits: 0.

Expected behaviour is fixed in advance in `evaluation/agent_integration_questions.json`; receipts are recorded in `reports/runs/agent-*/agent_outputs.jsonl`. PASS: behaved as expected. FAIL: did not. REFUSED: declined, as expected. NOT EVALUATED: no valid receipt because of an API error or the request budget; never counted as wrong.

| Case | Question | Verdict | Route | Status | Explanation | Verification | API requests (cache hits) | Gemini ms | Tokens in / out / thinking | API error |
|---|---|---|---|---|---|---|---|---|---|---|
| A1 | How many holdings does the Dividend Aristocrats Fund have? | NOT EVALUATED | - | - | Not evaluated: not attempted yet because of the request budget. | - | - (-) | - | - / - / - | - |
| A2 | What are the sector exposures for the Tech Innovation Fund? | NOT EVALUATED | - | - | Not evaluated: not attempted yet because of the request budget. | - | - (-) | - | - / - / - | - |
| A3 | Sector exposure for the international portfolio | NOT EVALUATED | - | - | Not evaluated: not attempted yet because of the request budget. | - | - (-) | - | - / - / - | - |
| A4 | Which high-risk portfolio has the largest Technology exposure? | NOT EVALUATED | - | - | Not evaluated: not attempted yet because of the request budget. | - | - (-) | - | - / - / - | - |

## Provenance

Raw responses are saved the moment they arrive: in `reports/api_usage.jsonl` (the entry with the request hash shown) and in `.cache/llm_responses/<hash>.json` (not committed).

| Case | Run | Model (served version) | Router version | SQL prompt version | Receipt | Raw responses |
|---|---|---|---|---|---|---|
| A1 | - | - | - | - | - | - |
| A2 | - | - | - | - | - | - |
| A3 | - | - | - | - | - | - |
| A4 | - | - | - | - | - | - |
