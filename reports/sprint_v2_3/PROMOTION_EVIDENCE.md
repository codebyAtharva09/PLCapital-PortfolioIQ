# Promotion evidence: candidate SQL configurations

All live results: `gemini-3.6-flash`, thinking LOW, unchanged official comparator and ground truth. Every experiment has its
own folder, usage ledger, `evaluation_record.md` and SHA-256 manifest. The baseline remains the default.

| Configuration | SQL prompt | Official end to end | SQL tool Q1-Q8 | Unseen U1-U10 (strict) | Safety S1-S3 | Requests | Record |
|---|---|---|---|---|---|---|---|
| Baseline | `d5a914eb71d5` | 8/10 (Q5, Q7 fail) | 6/8 | 7 of 8 run (U4 fails; U9, U10 never run) | 3/3 refused | see `reports/api_usage.jsonl` | `reports/sprint_v2_1/baseline/` |
| v2.1 | `5219dec6dd3e` | 10/10 | 8/8 | 8/10 (U5, U10 extra columns) | not run | 18 | `reports/sprint_v2_1/candidate/` |
| v2.2 | `2ef54d48d231` | 9/10 (Q8 regressed) | not run | stopped: U5 passed, U10 hit a 503 | not run | 10 | `reports/sprint_v2_2/candidate/` |
| v2.3 | `314e91c11435` | **10/10** | **8/8** | **10/10** | **3/3 refused** | 22, all succeeded | `reports/sprint_v2_3/candidate/` |

## What each version changed, and why

- **v2.1** replaced the baseline's column rule, corrected few-shot example VE3, added a share-of-total example and an attribute-list
  example, and added the AST and executed-row shape policy with at most one retry.
- **v2.2** added two rules after U5 and U10 returned extra columns: a named entity needs no identifying column, and "which X has the
  most Y" returns X. It overshot: Q8 lost the two values behind its ratio.
- **v2.3** added one rule: a calculated ratio is returned with the two values it divides.

U9 is the only retry in the v2.3 run: its first draft returned the join keys portfolio_id and security_id, the policy flagged them,
and the retry fixed it.

## Limits of this evidence

- Q5, Q7, Q8, U4, U5 and U10 each motivated a rule. The official and unseen sets are therefore development data for these rules, so
  20 of 20 is not an independent accuracy estimate. Even taken at face value, 20/20 has a 95% Wilson lower bound of about 84%.
- Each configuration was run once. Repeatability has not been measured for v2.3.
- The 51-case independent benchmark, including its held-out split, has not been run live.
- Routing and exposure used saved responses because the router is unchanged, so these runs did not re-test routing.
- "For each portfolio" lists only portfolios with qualifying holdings. That matches the reference but is one reading of the words. The
  answer carries a caveat stating it.
- Google's own quota counters could not be read during the sprint.

## Recommendation

**GO for promoting v2.3, and only v2.3.** It is the only configuration that keeps official 10/10 and clears the unseen failures, with
safety and offline regressions intact. The owner approved promotion on 9 November 2026, and v2.3 is now the default.

Promotion changed the default in `config.py`, pointed demo replay at the v2.3 recordings, and updated the tests and documents
that pin the default. `PORTFOLIOIQ_SQL_PROMPT=baseline` stays available.
