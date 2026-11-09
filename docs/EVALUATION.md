# Evaluation: methodology and results

Every result on this page comes from a recorded run or a deterministic check. Reports are regenerated
from those records with no API call; nothing is estimated. Cases without a live result are **NOT
EVALUATED** and are never counted as wrong.

**Active production configuration (9 November 2026): `candidate-2.3`, SQL prompt `314e91c11435`.** Live on
`gemini-3.6-flash` with the unchanged comparator and ground truth: official end to end PASS 10, SQL Q1-Q8 PASS 8, unseen U1-U10
PASS 10 (strict), safety REFUSED 3, agent integration PASS 4. These rules were written from failures in these sets, so the
result is not an independent estimate. On the frozen held-out split T01-T16 it scored 16 of 16 (`reports/heldout_v2_3/HELDOUT_RESULT.md`); the other 35 independent cases are NOT YET EVALUATED. Evidence:
`reports/sprint_v2_3/PROMOTION_EVIDENCE.md`. The sections below record the original baseline and stay as they were.

Original baseline results as of 7 November 2026, model `gemini-3.6-flash` (served as `gemini-3.6-flash`), SDK
`google-genai` 2.34.0, thinking level LOW, SQL prompt version `d5a914eb71d5`, router version
`676786d09950`.

## Vocabulary

| Verdict | Meaning |
|---|---|
| PASS | The behaviour matches the expectation fixed in advance (for SQL: the executed result equals the reference result) |
| FAIL | It does not (a different result, a relaxed-only match, no valid SQL, a declined answerable question, a wrong route) |
| REFUSED | A safety prompt was refused and nothing was executed, which is the expected outcome |
| NOT EVALUATED | No valid live result exists: an API error, the request budget, or no run has asked the question yet |
| N/A | The check does not apply (for example, a behaviour that depends only on live model output) |

Verification states (`INDEPENDENTLY_RECONCILED`, `RESULT_VALIDATED`, `VERIFICATION_FAILED`,
`VERIFICATION_UNAVAILABLE`) describe what the system itself checked; correctness against a reference is
judged separately by the evaluators, and the two are never merged.

## 1. SQL Query Tool (M3): 21 questions, live

Command: `python -m portfolioiq_eval sql --rescore`. Report: `reports/sql_evaluation.{md,json,csv}`.

Both the generated SQL and the reference SQL run on the same read-only database; results are compared,
not SQL text. Columns are matched by values (aliases ignored); strict accuracy requires exactly the
reference columns (B13); extra columns are reported separately as relaxed matches; row order is scored
only for ORDER BY ... LIMIT questions (B7); numbers match within 0.005 or 1e-6 relative, with no unit
conversion (B12).

| Set | Result |
|---|---|
| Official Q1-Q8 | PASS 6 (Q1, Q2, Q3, Q4, Q6, Q8), FAIL 2 (Q5, Q7) |
| Unseen U1-U10 (project-written) | PASS 7 (U1, U2, U3, U5, U6, U7, U8), FAIL 1 (U4), NOT EVALUATED 2 (U9, U10) |
| Safety S1-S3 | REFUSED 3 (Gemini declined; nothing was executed; database SHA-256 unchanged) |

Failures, kept exactly as recorded:

- **Q5** "Top 5 holdings by cost basis in the Growth Equity Fund": the right five holdings in the right
  order, but without `holding_id` and `quantity`. Two runs (2026-11-06 and 2026-11-07) produced
  byte-identical SQL. The cause is the SQL tool's column guidance ("identifying names plus the values
  the question asks for").
- **Q7** "For each portfolio, the Technology value and its share": Gemini listed all 13 portfolios,
  giving the five without Technology holdings a value of 0, and omitted the total-value column. The
  reference lists the 8 portfolios that hold Technology. For those 8, the values are identical and the
  shares agree to two decimals.
- **U4** "Each bond with its coupon rate and maturity date": an extra `company_name` column, so a
  relaxed match only. U4 and Q5 show the same instruction affecting strict matching in opposite
  directions.

The comparator, references, prompt and ground truth were not changed. A general column rule is an
option for a separately versioned prompt, reported before and after.

## 2. Agent integration (M4): one question per route, live

Window 1 on 8 November 2026 completed all four integration cases: A1, A2, A3 and A4 PASS. A2
was answered using the cached Q9 exposure result at no quota cost. A3 correctly returned the
expected clarification for the generic phrase “international portfolio”. This replaces the older
pending wording in historical notes; the historical run records remain preserved.

Command: `python -m portfolioiq_eval agent --rescore`. Report: `reports/agent_integration.{md,json}`.

| Case | Route | Result | Requests | Notes |
|---|---|---|---|---|
| A1 "How many holdings does the Dividend Aristocrats Fund have?" | sql | PASS | 1 + 1 cache hit | Holding count 4; the SQL draft was U2's saved response |
| A2 "What are the sector exposures for the Tech Innovation Fund?" | exposure | PASS | 0 (1 cache) | Answered using the cached Q9 exposure result |
| A3 "Sector exposure for the international portfolio" | exposure (clarification) | PASS | 2 | Correct clarification with the expected candidates |
| A4 "Which high-risk portfolio has the largest Technology exposure?" | exposure_for_sql_results | PASS | 3 | One 503, then a successful retry; Tech Innovation Fund leads with 100% Technology |

## 3. Official questions (M5): Q1-Q10 on three layers

Command: `python -m portfolioiq_eval official --rescore`. Report: `reports/official_evaluation.{md,json}`.

| Layer | What it measures | Result |
|---|---|---|
| Agent, end to end (live) | Route chosen and tool result against the reference | NOT EVALUATED 10: no live official agent run yet |
| SQL tool, direct (live, recorded) | Execution accuracy for Q1-Q8 | PASS 6, FAIL 2 (layer 1 above) |
| Deterministic calculation | Exposure engine for Q9 (Tech Innovation Fund) and Q10 (International Equity Fund) against hand-derived fixtures; Q10's phrase "international equity" resolved by rule B5.1 | PASS 2, both `INDEPENDENTLY_RECONCILED` |

The fixtures (`evaluation/fixtures/`) were derived by hand from the CSVs, independently of the code.

## 4. Reliability benchmark (M5): 19 scenarios

Command: `python -m portfolioiq_eval benchmark`. Report: `reports/reliability_benchmark.{md,json}`.
Scenario definitions: `evaluation/reliability_scenarios.json`.

Each scenario has two separate columns. **Deterministic component**: the code path the scenario
depends on, checked now with no Gemini. **Live end to end**: the recorded live result of the same
question, if any.

| Result | Deterministic component | Live end to end |
|---|---|---|
| PASS | 17 | 3 (R01 hybrid = A4; R09 = U1; R10 = A1 and U2) |
| REFUSED | | 2 (R16 = S1, R17 = S2) |
| N/A | 2 (R13 out of scope, R18 stability: live output only) | 2 (R14 input validation runs before any model call; R19 failures are simulated by design) |
| NOT EVALUATED | | 12 |

Highlights of the deterministic components:

- **R16, R17**: the SQL guard rejects `DELETE ...` and `SELECT 1; UPDATE ...`; with the guard bypassed,
  the read-only database layer refuses them too; the database SHA-256 is unchanged. Gemini refusing is
  never taken as proof of safety.
- **R14**: empty, whitespace-only, 10,000-character and control-character inputs return
  `invalid_input`, and the model is never called.
- **R15**: `Growth Equity Fund'; DROP TABLE holdings;--` is rejected by SR1 before any name matching.
- **R19 (simulated)**: invalid key (api_error, 1 request), timeout (network, 2 requests), daily 429
  (quota_exhausted, 1 request, no retry), 503 then success (2 requests), malformed output
  (malformed_output): every case gives an error receipt, no crash, and the key never appears.
- **R05, R06, R08, R12**: the resolver asks for clarification for "international portfolio" and
  "equity fund", suggests "Tech Innovation Fund" for "Tech Inovation Fund" without applying it, and
  finds nothing for "Bitcoin Fund".

## 5. Financial Invariant Engine (M5)

Command: `python -m portfolioiq integrity`. Shown on the Data Integrity workspace.

22 checks on the supplied data: **14 PASS, 8 WARNING, 0 FAIL**. The warnings are documented anomalies:
weights total 98% for Total Stock Market Index Fund (I06); `current_price` differs from the 2023-10-09
close for all 30 securities (I11, rule B11); holdings cover 1.68%-14.72% of stated AUM (I12); 22 of 23
traded positions do not reconcile (I13); performance and risk data cover 4 of 13 portfolios (I15); Home
Depot's classification (I18); every security is US and USD (I19); stated weights differ from
market-value weights (I20).

## 6. API usage

Command: `python -m portfolioiq_eval usage`. Ledger: `reports/api_usage.jsonl`.

On 2026-11-07: 19 requests (15 answered, four 503 "high demand"), one cache hit, one budget stop;
tokens 36,452 prompt, 1,399 output, 450 thinking. Google AI Studio's own counter showed the same 19 of 20.
Failed requests count against the daily quota. The 2026-11-06 run predates the ledger.

## 7. Supplemental evaluation: gemini-3.5-flash-lite (separately labelled)

**These are not Gemini 3.6 Flash results.** Approved by the owner on 7 November 2026 to cover the cases the
official model could not reach on its free-tier quota. Everything lives in its own folder,
`reports/supplemental/gemini-3.5-flash-lite/` (runs, reports, usage ledger), with its own response cache;
the official reports and ledger were byte-identical before and after. The evaluator refuses to score runs
from different models together. Same code, router version `676786d09950`, SQL prompt `d5a914eb71d5`,
context `4b74dcd0419a`, thinking level LOW, 13-second pacing. Environment: `scripts/supplemental_env.ps1`
(changes nothing on disk; the default model stays `gemini-3.6-flash`).

Pre-flight checks before the first request: AI Studio showed the "Gemini API" project's Gemini 3.5 Flash
Lite free-tier limits at 0/15 RPM, 0/250K TPM, 0/500 RPD; the dataset is the supplied synthetic
assessment data with no personal or confidential information, which Google's Unpaid Services terms
permit (submitted content may be used by Google and read by human reviewers); a captured payload holds only
the instruction, the schema description, portfolio and sector names, project-written examples and the
question (about 3 KB for routing, 10 KB for a SQL draft), with no key, path or personal data; the model is
selected per process through `GEMINI_MODEL`.

| Evaluation | Flash-Lite result |
|---|---|
| Official Q1-Q10 through the agent | **PASS 7, FAIL 3** (Q4, Q5, Q7); routing correct 10 of 10 |
| Q9-Q10 deterministic calculation | PASS 2 |
| Unseen U9, U10 (SQL tool) | PASS 1 (U10), FAIL 1 (U9) |
| Agent integration A2, A3 | PASS 2 (A2 exposure, A3 clarification with both candidates) |
| Stability R18 (Q4, Q7, Q10, R01, R09; three fresh rounds, cache off) | PASS 5 of 5, consistency 1.0 |

Stability measures repeatability, not correctness: Q4 and Q7 gave the same wrong result in every round.
Usage: 52 requests (48 answered, four 503 responses, each retried once successfully), one cache hit (A2
reused Q9's identical routing request), 77,871 prompt and 4,674 output tokens, no thinking tokens; median
latency 1.7 s, maximum 9.7 s. Raw responses: `reports/supplemental/gemini-3.5-flash-lite/api_usage.jsonl`.

Failures: **Q4** listed each high-risk portfolio's AUM (4 rows) instead of the total; **Q5** the same
missing `holding_id`/`quantity` columns as Gemini 3.6 Flash; **Q7** the same 13-row reading as Gemini
3.6 Flash; **U9** "transactions from last year" returned no rows (the reference uses the 2022 calendar
year relative to the data anchor; see the supplemental SQL report).

## Running a further live evaluation

A live run needs a Gemini key and quota. Runs from different models or SQL prompt versions are never scored together, so when the default
folders hold another configuration's runs, as they do on a fresh clone, the run is kept in `reports/live/<model>-<prompt version>/`.
Pass `--runs-dir` and `--reports-dir` to choose other folders.

```bash
python -m portfolioiq_eval official --max-requests 18
```

Each run records its answers as they arrive, so an interrupted run loses nothing, and no case is asked twice unless `--rerun` names it.
Runs are paced 13,000 ms apart and stop after two consecutive API failures.
