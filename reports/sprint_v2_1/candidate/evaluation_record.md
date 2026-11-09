# Evaluation record: Candidate v2.1

Live, Gemini 3.6 Flash, 9 November 2026. Sent with the backup key. Routing replayed from saved responses because the router is unchanged. Safety cases were not run for this version.

- Variant `candidate-2.1`; SQL prompt `5219dec6dd3e`; instruction SHA-256 `c61f03e5fd3593f01a63442e79942c8c071296838d007330b4940134b2e303d4`; context `31fdf60fa2b5`; shape policy 2.1.
- Production baseline for comparison: SQL prompt `d5a914eb71d5`, official end to end 8/10, kept in `reports/sprint_v2_1/baseline/`.
- Model `gemini-3.6-flash`, thinking LOW. Dataset fingerprint `c4cd3a08fa94103d69538cefdb00e251f0b03b56d6ead7e5d609b41bc88756c6`.
- Requests 18 (18 succeeded), cache hits 18; first 2026-11-09T07:03:39+00:00, last 2026-11-09T07:08:02+00:00 UTC.
- Failed requests: none.
- Tokens: prompt 61,529, output 2,016, thinking 5,128. Gemini latency median 3333.5 ms, max 8063.1 ms.

| Case | Official agent (end to end) | SQL tool (strict) |
|---|---|---|
| Q1 | PASS | PASS |
| Q2 | PASS | PASS |
| Q3 | PASS | PASS |
| Q4 | PASS | PASS |
| Q5 | PASS | PASS |
| Q6 | PASS | PASS |
| Q7 | PASS | PASS |
| Q8 | PASS | PASS |
| Q9 | PASS | - |
| Q10 | PASS | - |
| U1 | - | PASS |
| U2 | - | PASS |
| U3 | - | PASS |
| U4 | - | PASS |
| U5 | - | FAIL |
| U6 | - | PASS |
| U7 | - | PASS |
| U8 | - | PASS |
| U9 | - | PASS |
| U10 | - | FAIL |
| S1 | - | NOT EVALUATED |
| S2 | - | NOT EVALUATED |
| S3 | - | NOT EVALUATED |

Evaluator file hashes are in `evaluation_record.json`. Scores come from the unchanged comparator and ground truth.
