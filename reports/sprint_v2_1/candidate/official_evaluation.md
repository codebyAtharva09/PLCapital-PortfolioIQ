# Official evaluation: ground_truth_dataset.json (Q1-Q10)

Agent runs: model gemini-3.6-flash (served gemini-3.6-flash); router 676786d09950; SQL prompt 5219dec6dd3e; context 31fdf60fa2b5.

Three evidence layers, reported separately and never blended. A question with no recorded live result is NOT EVALUATED and is never counted as wrong.

| Layer | What it measures | Result |
|---|---|---|
| Agent, end to end (live Gemini) | Route chosen and the tool result against the reference | PASS 10; routing 10/10 evaluated (of 10) |
| SQL tool, direct (Q1-Q8, live, recorded) | Execution accuracy, strict comparison (B13) | PASS 8 |
| Deterministic calculation (Q9-Q10, no Gemini) | Exposure engine against hand-derived fixtures | PASS 2 |

## Results by question

| ID | Question | Expected route | Agent (end to end) | SQL tool (direct) | Calculation (deterministic) |
|---|---|---|---|---|---|
| Q1 | How many portfolios do we have in total? | sql | PASS | PASS | - |
| Q2 | What are the names of all active portfolios? | sql | PASS | PASS | - |
| Q3 | Which securities are in the Technology sector? | sql | PASS | PASS | - |
| Q4 | What is the total Assets Under Management (AUM) for portfolios with high target risk level? | sql | PASS | PASS | - |
| Q5 | Show me the top 5 holdings by cost basis in the Growth Equity Fund | sql | PASS | PASS | - |
| Q6 | What is the average current price of securities in each sector? | sql | PASS | PASS | - |
| Q7 | For each portfolio, show the total value of Technology sector holdings and what percentage it represents of the total portfolio value | sql | PASS | PASS | - |
| Q8 | Find portfolios that have holdings in more than 5 different sectors and show their diversification metrics | sql | PASS | PASS | - |
| Q9 | What are the sector exposures for the Tech Innovation Fund? | exposure | PASS | - | PASS |
| Q10 | Calculate the sector exposure breakdown for international equity | exposure | PASS | - | PASS |

## Explanations

- **Q1** agent: Routed to SQL; the result matches the reference exactly.
  SQL tool (20261109-123532): The executed result matches the reference result exactly.
- **Q2** agent: Routed to SQL; the result matches the reference exactly.
  SQL tool (20261109-123532): The executed result matches the reference result exactly.
- **Q3** agent: Routed to SQL; the result matches the reference exactly.
  SQL tool (20261109-123532): The executed result matches the reference result exactly.
- **Q4** agent: Routed to SQL; the result matches the reference exactly.
  SQL tool (20261109-123532): The executed result matches the reference result exactly.
- **Q5** agent: Routed to SQL; the result matches the reference exactly.
  SQL tool (20261109-123532): The executed result matches the reference result exactly.
- **Q6** agent: Routed to SQL; the result matches the reference exactly.
  SQL tool (20261109-123532): The executed result matches the reference result exactly.
- **Q7** agent: Routed to SQL; the result matches the reference exactly.
  SQL tool (20261109-123532): The executed result matches the reference result exactly.
- **Q8** agent: Routed to SQL; the result matches the reference exactly.
  SQL tool (20261109-123532): The executed result matches the reference result exactly.
- **Q9** agent: Routed to the exposure calculator for Tech Innovation Fund; both methods match the fixture.
  Calculation: Method A and Method B match the hand-derived fixture (Technology 100.00% / 100.00%); engine verification INDEPENDENTLY_RECONCILED.
- **Q10** agent: Routed to the exposure calculator for International Equity Fund; both methods match the fixture.
  Calculation: Method A and Method B match the hand-derived fixture (Energy 38.00% / 38.00%, Technology 36.00% / 36.00%, Automotive 15.00% / 15.00%, Consumer Staples 11.00% / 11.00%); engine verification INDEPENDENTLY_RECONCILED; phrase 'international equity' resolves to International Equity Fund by rule B5.1.

Agent receipts: `reports/runs/official-*/official_outputs.jsonl` (none recorded yet means the live agent run is still pending). SQL tool records: `reports/runs/*/sql_outputs.jsonl`. Fixtures: `evaluation/fixtures/`. Scoring makes no API calls: `python -m portfolioiq_eval official --rescore`.
