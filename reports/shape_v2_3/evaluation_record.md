# Evaluation record: Candidate v2.3 on the frozen result-shape set

Live, Gemini 3.6 Flash, 9 November 2026, 17 questions from evaluation/sql_shape_set.json, SQL tool only (no router). Sent with a separately supplied key that belongs to a different free-tier Google project from the main PortfolioIQ project; the key value is not recorded. The set was written after the v2.1 failure analysis and S07 was corrected once before any answer existed (see the file's revisions field), so it is a targeted generalization set, not a blind test. Strict score 15/17; relaxed 17/17. S11 and S17 fail strictly because the answer adds component columns (the part and the whole of a share) that the reference omits; every value in them is correct.

- Variant `candidate-2.3`; SQL prompt `314e91c11435`; instruction SHA-256 `aaa7d082faa95c863fc479ecb5c4777a2639589ca67cf5517e34de4db0e2677f`; context `d5f1098033f7`; shape policy 2.3.
- Production baseline for comparison: SQL prompt `d5a914eb71d5`, official end to end 8/10, kept in `reports/sprint_v2_1/baseline/`.
- Model `gemini-3.6-flash`, thinking LOW. Dataset fingerprint `c4cd3a08fa94103d69538cefdb00e251f0b03b56d6ead7e5d609b41bc88756c6`.
- Requests 19 (19 succeeded), cache hits 0; first 2026-11-09T07:45:19+00:00, last 2026-11-09T07:49:11+00:00 UTC.
- Failed requests: none.
- Tokens: prompt 67,083, output 2,038, thinking 5,221. Gemini latency median 3365.3 ms, max 7072.865 ms.

| Case | Official agent (end to end) | SQL tool (strict) |
|---|---|---|
| S01 | - | PASS |
| S02 | - | PASS |
| S03 | - | PASS |
| S04 | - | PASS |
| S05 | - | PASS |
| S06 | - | PASS |
| S07 | - | PASS |
| S08 | - | PASS |
| S09 | - | PASS |
| S10 | - | PASS |
| S11 | - | FAIL |
| S12 | - | PASS |
| S13 | - | PASS |
| S14 | - | PASS |
| S15 | - | PASS |
| S16 | - | PASS |
| S17 | - | FAIL |

Evaluator file hashes are in `evaluation_record.json`. Scores come from the unchanged comparator and ground truth.
