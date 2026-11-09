# Official evaluation: ground_truth_dataset.json (Q1-Q10)

Agent runs: model gemini-3.5-flash-lite (served gemini-3.5-flash-lite); router 676786d09950; SQL prompt d5a914eb71d5; context 4b74dcd0419a.

Three evidence layers, reported separately and never blended. A question with no recorded live result is NOT EVALUATED and is never counted as wrong.

| Layer | What it measures | Result |
|---|---|---|
| Agent, end to end (live Gemini) | Route chosen and the tool result against the reference | PASS 7, FAIL 3; routing 10/10 evaluated (of 10) |
| SQL tool, direct (Q1-Q8, live, recorded) | Execution accuracy, strict comparison (B13) | NOT EVALUATED 8 |
| Deterministic calculation (Q9-Q10, no Gemini) | Exposure engine against hand-derived fixtures | PASS 2 |

## Results by question

| ID | Question | Expected route | Agent (end to end) | SQL tool (direct) | Calculation (deterministic) |
|---|---|---|---|---|---|
| Q1 | How many portfolios do we have in total? | sql | PASS | NOT EVALUATED | - |
| Q2 | What are the names of all active portfolios? | sql | PASS | NOT EVALUATED | - |
| Q3 | Which securities are in the Technology sector? | sql | PASS | NOT EVALUATED | - |
| Q4 | What is the total Assets Under Management (AUM) for portfolios with high target risk level? | sql | FAIL | NOT EVALUATED | - |
| Q5 | Show me the top 5 holdings by cost basis in the Growth Equity Fund | sql | FAIL | NOT EVALUATED | - |
| Q6 | What is the average current price of securities in each sector? | sql | PASS | NOT EVALUATED | - |
| Q7 | For each portfolio, show the total value of Technology sector holdings and what percentage it represents of the total portfolio value | sql | FAIL | NOT EVALUATED | - |
| Q8 | Find portfolios that have holdings in more than 5 different sectors and show their diversification metrics | sql | PASS | NOT EVALUATED | - |
| Q9 | What are the sector exposures for the Tech Innovation Fund? | exposure | PASS | - | PASS |
| Q10 | Calculate the sector exposure breakdown for international equity | exposure | PASS | - | PASS |

## Explanations

- **Q1** agent: Routed to SQL; the result matches the reference exactly.
  SQL tool (no run): Not evaluated: no live run has asked this question yet.
- **Q2** agent: Routed to SQL; the result matches the reference exactly.
  SQL tool (no run): Not evaluated: no live run has asked this question yet.
- **Q3** agent: Routed to SQL; the result matches the reference exactly.
  SQL tool (no run): Not evaluated: no live run has asked this question yet.
- **Q4** agent: The result differs from the reference: 4 row(s) instead of 1.
  SQL tool (no run): Not evaluated: no live run has asked this question yet.
- **Q5** agent: The result differs from the reference: no column matches the expected values of 'holding_id'.
  SQL tool (no run): Not evaluated: no live run has asked this question yet.
- **Q6** agent: Routed to SQL; the result matches the reference exactly.
  SQL tool (no run): Not evaluated: no live run has asked this question yet.
- **Q7** agent: The result differs from the reference: 13 row(s) instead of 8.
  SQL tool (no run): Not evaluated: no live run has asked this question yet.
- **Q8** agent: Routed to SQL; the result matches the reference exactly.
  SQL tool (no run): Not evaluated: no live run has asked this question yet.
- **Q9** agent: Routed to the exposure calculator for Tech Innovation Fund; both methods match the fixture.
  Calculation: Method A and Method B match the hand-derived fixture (Technology 100.00% / 100.00%); engine verification INDEPENDENTLY_RECONCILED.
- **Q10** agent: Routed to the exposure calculator for International Equity Fund; both methods match the fixture.
  Calculation: Method A and Method B match the hand-derived fixture (Energy 38.00% / 38.00%, Technology 36.00% / 36.00%, Automotive 15.00% / 15.00%, Consumer Staples 11.00% / 11.00%); engine verification INDEPENDENTLY_RECONCILED; phrase 'international equity' resolves to International Equity Fund by rule B5.1.

Agent receipts: `reports/runs/official-*/official_outputs.jsonl` (none recorded yet means the live agent run is still pending). SQL tool records: `reports/runs/*/sql_outputs.jsonl`. Fixtures: `evaluation/fixtures/`. Scoring makes no API calls: `python -m portfolioiq_eval official --rescore`.
