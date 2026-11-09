# Evaluation record: Candidate v2.2 (failed the official gate)

Live, Gemini 3.6 Flash, 9 November 2026. Official 9/10: Q8 regressed. Stopped there; only U5 was evaluated on the unseen set (U10 hit a 503). Kept as a failed experiment.

- Variant `candidate-2.2`; SQL prompt `2ef54d48d231`; instruction SHA-256 `74bcc4da4acc884a3e3d1d9fae8aa8e8c4977d9e33acea605f9221688fbef3f6`; context `d5f1098033f7`; shape policy 2.2.
- Production baseline for comparison: SQL prompt `d5a914eb71d5`, official end to end 8/10, kept in `reports/sprint_v2_1/baseline/`.
- Model `gemini-3.6-flash`, thinking LOW. Dataset fingerprint `c4cd3a08fa94103d69538cefdb00e251f0b03b56d6ead7e5d609b41bc88756c6`.
- Requests 10 (9 succeeded), cache hits 10; first 2026-11-09T07:15:31+00:00, last 2026-11-09T07:20:25+00:00 UTC.
- Failed requests: U10 sql_tool draft 1: 503 UNAVAILABLE.
- Tokens: prompt 31,395, output 1,115, thinking 2,285. Gemini latency median 3326.7 ms, max 5436.999 ms.

| Case | Official agent (end to end) | SQL tool (strict) |
|---|---|---|
| Q1 | PASS | NOT EVALUATED |
| Q2 | PASS | NOT EVALUATED |
| Q3 | PASS | NOT EVALUATED |
| Q4 | PASS | NOT EVALUATED |
| Q5 | PASS | NOT EVALUATED |
| Q6 | PASS | NOT EVALUATED |
| Q7 | PASS | NOT EVALUATED |
| Q8 | FAIL | NOT EVALUATED |
| Q9 | PASS | - |
| Q10 | PASS | - |
| U1 | - | NOT EVALUATED |
| U2 | - | NOT EVALUATED |
| U3 | - | NOT EVALUATED |
| U4 | - | NOT EVALUATED |
| U5 | - | PASS |
| U6 | - | NOT EVALUATED |
| U7 | - | NOT EVALUATED |
| U8 | - | NOT EVALUATED |
| U9 | - | NOT EVALUATED |
| U10 | - | NOT EVALUATED |
| S1 | - | NOT EVALUATED |
| S2 | - | NOT EVALUATED |
| S3 | - | NOT EVALUATED |

Evaluator file hashes are in `evaluation_record.json`. Scores come from the unchanged comparator and ground truth.
