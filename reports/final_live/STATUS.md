# Status: complete

Official Q1-Q10 through the agent, live, on the final code: **PASS 10**, routing 10 of 10.

- Code commit: `e52573335b0e92daef369d915f3983f5eaa4d653`. Files in `src/`, `evaluation/` and `data/` are unchanged since that commit.
- Configuration: `candidate-2.3`, SQL prompt `314e91c11435`, router `676786d09950`, model `gemini-3.6-flash`, thinking LOW, response cache off.
- Configuration manifest: `reports/final_verification/eval_config_20261109T200056Z.json`.
- Time: 2026-11-09, 20:00 to 20:25 UTC.

The evaluation took six sessions, because Gemini returned quota errors and 503 errors. A session stops by design at a quota error or after two consecutive outages, and the next one asks only the questions without an answer. Each question has one recorded answer, and no answered question was asked again.

API usage in `api_usage.jsonl`: 32 requests, of which 21 succeeded, 2 were 429 quota errors and 9 were 503 errors.

The SQL prompt's rules were written after studying failures in the official and unseen questions, so this is a development result, not a blind test. Each question was answered once, so repeatability is not measured.
