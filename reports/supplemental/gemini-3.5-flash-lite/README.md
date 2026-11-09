# Supplemental evaluation: gemini-3.5-flash-lite

**Not the official model.** These results come from `gemini-3.5-flash-lite`, run on 2026-11-07 with the
owner's approval to cover cases the official `gemini-3.6-flash` evaluation could not reach on its
free-tier quota. They are never merged with, or substituted for, the official results in `reports/`.

- Same code and prompts as the official runs: router `676786d09950`, SQL prompt `d5a914eb71d5`, context
  `4b74dcd0419a`, thinking level LOW, 13-second pacing, at most one retry, stop after two consecutive API
  failures, every attempt counted.
- Separate folders: `runs/` (one directory per run, receipts recorded as they arrived), the reports
  below, and `api_usage.jsonl` (every request with its raw response). The response cache was separate too
  (`.cache/supplemental-gemini-3.5-flash-lite/`, not committed).
- Reproduce or rescore in PowerShell from the project root:
  `. .\scripts\supplemental_env.ps1`, then for example
  `python -m portfolioiq_eval official --rescore @SUPPLEMENTAL_DIRS` (no API calls).

| Report | Contents |
|---|---|
| `official_evaluation.md` | Q1-Q10 through the agent |
| `sql_evaluation.md` | U9, U10 (SQL tool) |
| `agent_integration.md` | A2, A3 |
| `stability.md` | R18: Q4, Q7, Q10, R01, R09, three fresh rounds each |
| `api_usage.jsonl` | Every request, failed ones included, with raw responses |
