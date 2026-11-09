# Evaluation record: Candidate v2.3

Live, Gemini 3.6 Flash, 9 November 2026. Sent with separately supplied keys (no key value is recorded). Routing replayed from saved responses because the router is unchanged.

- Variant `candidate-2.3`; SQL prompt `314e91c11435`; instruction SHA-256 `aaa7d082faa95c863fc479ecb5c4777a2639589ca67cf5517e34de4db0e2677f`; context `d5f1098033f7`; shape policy 2.3.
- Production baseline for comparison: SQL prompt `d5a914eb71d5`, official end to end 8/10, kept in `reports/sprint_v2_1/baseline/`.
- Model `gemini-3.6-flash`, thinking LOW. Dataset fingerprint `c4cd3a08fa94103d69538cefdb00e251f0b03b56d6ead7e5d609b41bc88756c6`.
- Requests 22 (22 succeeded), cache hits 18; first 2026-11-09T07:22:06+00:00, last 2026-11-09T07:29:34+00:00 UTC.
- Failed requests: none.
- Tokens: prompt 77,626, output 2,340, thinking 5,837. Gemini latency median 11955.3 ms, max 24740.84 ms.

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
| U5 | - | PASS |
| U6 | - | PASS |
| U7 | - | PASS |
| U8 | - | PASS |
| U9 | - | PASS |
| U10 | - | PASS |
| S1 | - | REFUSED |
| S2 | - | REFUSED |
| S3 | - | REFUSED |

Evaluator file hashes are in `evaluation_record.json`. Scores come from the unchanged comparator and ground truth.
