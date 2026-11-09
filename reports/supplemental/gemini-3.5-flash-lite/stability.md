# Stability benchmark (R18)

Model gemini-3.5-flash-lite; response cache off, so every round is a fresh Gemini response. A question is consistent when every evaluated round gives the same route and result; rounds with API errors are excluded and never counted as inconsistent.

Verdicts: PASS 5. Mean consistency 1.0. API requests 30.

| ID | Question | Verdict | Rounds (evaluated) | API failures | Consistency | Requests | Explanation |
|---|---|---|---|---|---|---|---|
| Q4 | What is the total Assets Under Management (AUM) for portfolios with high target risk level? | PASS | 3 (3) | 0 | 1.0 | 7 | All 3 rounds produced the same route and result. |
| Q7 | For each portfolio, show the total value of Technology sector holdings and what percentage it represents of the total portfolio value | PASS | 3 (3) | 0 | 1.0 | 6 | All 3 rounds produced the same route and result. |
| Q10 | Calculate the sector exposure breakdown for international equity | PASS | 3 (3) | 0 | 1.0 | 3 | All 3 rounds produced the same route and result. |
| R01 | Which high-risk portfolio has the largest Technology exposure? | PASS | 3 (3) | 0 | 1.0 | 7 | All 3 rounds produced the same route and result. |
| R09 | Which portfolios hold NVIDIA? | PASS | 3 (3) | 0 | 1.0 | 7 | All 3 rounds produced the same route and result. |

Receipts: `stability-*/stability_outputs.jsonl` in the runs directory. Rescore: `python -m portfolioiq_eval stability --rescore`.
