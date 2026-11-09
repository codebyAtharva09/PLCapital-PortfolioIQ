# Q5 and Q7: root-cause diagnosis

Written from the recorded baseline run (`reports/runs/official-20261108-135509`, model `gemini-3.6-flash`, SQL prompt
`d5a914eb71d5`, context `4b74dcd0419a`, router `676786d09950`) and the unchanged official comparator. Baseline scores are
preserved in `reports/sprint_v2_1/baseline/`; the frozen configuration and hashes are in
`evaluation/baseline_configuration.json`.

## What actually runs

Verified in source (`agent.py`, `router.py`, `sql_tool.py`):

1. `input_problem` rejects empty, oversized or control-character input.
2. One Gemini call returns a validated `RouterDecision`. Intent classification, planning and tool selection are this one
   structured call.
3. `sql` route: `interpret_dates` resolves relative dates deterministically, then one Gemini call returns a `SqlDraft`.
4. `validate_sql` (SQLGlot allowlists) then `execute_sql` (read-only URI, `query_only`, authorizer, time limit, row cap).
   One repair round exists for parse, name or execution failures. Security rejections are never repaired.
5. `_result_checks`, then `_sql_answer` composes the text from rows. Gemini never writes a figure.
6. `FinancialAnswerReceipt` records SQL, checks, versions, tokens and latency.

The baseline has no check that a safe query returned what the question asked for. Both failures live in that gap.

## Q5: "Show me the top 5 holdings by cost basis in the Growth Equity Fund"

| | |
|---|---|
| Expected columns | holding_id, symbol, company_name, quantity, cost_basis |
| Generated columns | symbol, company_name, cost_basis |
| Rows | The same five, in the same order, with the same values |
| Comparator | "no column matches the expected values of 'holding_id'" |
| Origin | SQL generation: output-column selection. Routing, joins, filter, sort, limit and values are correct |

Root cause, two parts that reinforce each other:

- The instruction says "identifying names (such as portfolio_name, symbol or company_name) plus the values the question asks for".
- Few-shot example VE3 in the semantic context teaches exactly the failed shape for a holdings list:
  `SELECT s.symbol, s.company_name, h.quantity * s.current_price AS market_value`. A wording change alone would have
  contradicted the example the model is shown.

Identical SQL was produced on 6, 7 and 8 November, so this is systematic rather than random.

## Q7: "For each portfolio, show the total value of Technology sector holdings and what percentage it represents of the total portfolio value"

| | |
|---|---|
| Expected | 8 rows: portfolio_name, tech_value, total_value, tech_percentage |
| Generated | 13 rows: portfolio_name, tech_holding_value, tech_percentage |
| Comparator | "13 row(s) instead of 8" |
| Origin | SQL generation: population and output columns. Routing, joins and arithmetic are correct |

- Population: `SUM(CASE WHEN ... ELSE 0 END)` keeps five portfolios with no Technology holdings. This is a defensible
  reading of "for each portfolio". The reference lists only holders, so the wording and the reference do not state the
  same population. Candidate v2.1 adopts the reference's reading as one explicit rule, and the answer says so in a caveat.
- Missing denominator: `total_value` is not returned.
- For the 8 shared portfolios, the Technology values are identical and every percentage is within 0.0048 of the reference.

Correction to the brief: missing rounding and result order are real differences but they did not cause the failure. The
comparator allows 0.005 on numbers and scores order only for ORDER BY with LIMIT. Candidate v2.1 rounds and sorts anyway,
as the acceptance criteria ask.

Counterfactuals run on the comparator: for Q5, adding holding_id and quantity gives a strict match, and adding either alone
fails. For Q7, Technology-only rows plus total_value give a strict match, and all 13 rows plus total_value still fail.

## Candidate v2 (`candidates/sql_policy_v2.py`): weaknesses confirmed in the code

1. `_projection_names` adds every column found inside an output expression, so `h.quantity * 2` counts as projecting quantity.
2. Ranking only checks that some ORDER BY and LIMIT exist.
3. Percentage checks read alias text. A test even accepts `SUM(x) / SUM(x)` with aliases that look right.
4. No over-projection check.
5. `classify` treats any mention of "holding" as the entity, so "Find portfolios that have holdings ..." is misclassified.
6. `repairable` is reported, but nothing is repaired.
7. The prompt says to decide the population deliberately, while `reports/candidate_sql_prompt_v2.md` says to use matching
   entities. Two policies.

v2 is left byte-identical because its offline report records its hash. It is superseded by `src/portfolioiq/sql_policy.py`.

## Candidate v2.1

Selected only with `PORTFOLIOIQ_SQL_PROMPT=candidate-2.1`. The baseline text, examples and code path are unchanged.

- Instruction: baseline with the column rule and rounding rule replaced, plus one result-column policy and one population rule.
- Examples: VE3 corrected, VE6 (share of a total, on strategy AUM) and VE7 (attribute list, on Energy market cap) added.
- Shape policy: AST and executed-row checks, with at most one retry per question.

These rules were motivated by Q5, Q7, U4 and Flash-Lite's Q4. A pass on those questions is therefore not independent
evidence of general accuracy.
