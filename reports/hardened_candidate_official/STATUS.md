# Status: incomplete, not an evaluation result

A fresh official run on code commit `e818153b93e2eda7b334dc2268d85cbea2c07439` was started on 2026-11-09 at 14:09 UTC.

The routing request for Q1 succeeded. The next request was refused by Gemini with a daily quota error (429 RESOURCE_EXHAUSTED), and the run stopped by design. Two requests were sent. No question was scored, so `official_evaluation.md` shows NOT EVALUATED for all agent and SQL rows. The deterministic Q9 and Q10 calculations passed.

This folder is kept as the record of the attempt. The live 10/10 result in `reports/sprint_v2_3/` belongs to commit `ff5b216` and earlier, before this hardening. It does not apply to this candidate.

To complete the run after the daily reset (08:00 UTC, 13:30 IST), with the same settings:

```powershell
$env:PORTFOLIOIQ_LLM_CACHE='off'
$env:PORTFOLIOIQ_USAGE_LOG='reports/hardened_candidate_official/api_usage.jsonl'
python -m portfolioiq_eval official --max-requests 20 --rerun Q1 --runs-dir reports/hardened_candidate_official/runs --reports-dir reports/hardened_candidate_official
```
