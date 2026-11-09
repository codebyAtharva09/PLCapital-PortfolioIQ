# Candidate v2.4: design and offline validation

**Status: offline only. Not evaluated live and not promoted.** Production stays on `candidate-2.3` (`314e91c11435`).
Select v2.4 with `PORTFOLIOIQ_SQL_PROMPT=candidate-2.4`. Its policy is a separate module, `src/portfolioiq/sql_policy_v24.py`, so
`sql_policy.py` stays byte-identical. Held-out T01-T16 was not used in its design or its tests.

## Root causes

**S10: policy false positive.** "at each target risk level" was missing from the grouping phrases, so the question read as one total.
A correct `GROUP BY` then raised `unrelated_record_columns` and `scalar_expected`, and spent one retry.

**U5 and S03: table-wide latest date.** Gemini wrote `calculation_date = (SELECT MAX(calculation_date) FROM risk_metrics)`. The prompt
taught this twice: the semantic context says "use the latest date in the relevant table", and few-shot VE4 uses a table-wide MAX. It
passes today only because portfolios 1 to 4 all end on 2023-10-09. U5's project-written reference has the same weakness and is left unchanged.

**S11 and S17: ratio projection.** Three separate causes:
- v2.3's rule says to return the part and the whole for every percentage. Q7 needs that: each portfolio has its own total. S11 asks for one
  figure, and in S17 the whole is the same 30 on every row.
- A tracing bug: `COUNT(*)` names no column, so its filters were dropped. S11's two different counts then looked identical, raising
  `duplicate_output_column` and `percentage_hardcoded` on correct arithmetic.
- The S17 reference form (`COUNT(*) / (SELECT COUNT(*) ...)`) would have been read as a value divided by itself.

## What v2.4 changes

| Area | Rule |
|---|---|
| Grouping | "at each" and similar phrases read as grouping. A `GROUP BY` on a column the question names is a grouped answer. A `GROUP BY` on an unnamed column is still a defect |
| Tracing | An aggregate keeps its scope's filters even with no named column. A measure records whether it is computed per group |
| Latest date | "latest" for a named entity or for each entity must use that entity's own date. Filtered or correlated subqueries, `ORDER BY ... LIMIT`, grouped CTEs and window functions are accepted. A table-wide `MAX` is flagged unless the question asks for it |
| Percentage, one figure | Only the percentage, unless a count or value is also asked for |
| Percentage, per entity | Identity, percentage and any value asked for. The part and whole are required when each entity has its own whole. A whole that is the same on every row is not returned |
| Calculated ratio | Returned alone, unless the question asks for metrics or the figures behind it (Q8) |
| Prompt and context | Baseline of v2.3 with those rules, the entity-level latest-date rule in the context, and a correlated VE4 |

Every rule here was motivated by S03, S10, S11, S17, U5 or Q8, so those cases are development data for v2.4. Reference answers were not changed.

## Offline evidence

- `tests/test_sql_policy_v24.py`: grouped aggregates, per-entity and explicit table-wide latest dates, ratios with and without requested operands,
  extra and missing columns, Q5, Q7, Q8, S10, S11 and S17, and proof that v2.2 and v2.3 keep their behaviour.
- Reference queries (official, unseen, shape): only U5 is flagged, for `global_latest_date`.
- Recorded real v2.3 answers: flagged only at U5, S03, S11 and S17, each for its actual defect.

These are checks of the policy against recorded output. **They do not show how Gemini will answer the v2.4 prompt.** v2.2 passed
offline and regressed Q8 live, and this design touches the same ratio rule.
