# Security audit, November 2026

Scope: branch `security-financial-verification-sprint`, code commit `e818153`. This is an internal engineering review backed by automated tests. It is not a certification, and it does not claim the system is free of defects. `docs/SECURITY.md` describes the controls in general; this report records what was examined, what was found and what remains.

## Threat model

**Assets.** The integrity of reported financial figures. The API key and its daily quota. The reference database and frozen evaluation evidence. The trust a reader can place in a saved receipt.

**Trust boundaries.** (1) Browser or CLI user to application. (2) Application to Gemini: everything returned is untrusted. (3) Generated SQL to SQLite. (4) Database text to prompts and pages. (5) Repository and CI to the outside world.

**Entry points.** Question text, the portfolio argument, Gemini's structured output, recorded responses used in replay, the SQL Intelligence view, downloads, and CI workflow inputs.

**Threat actors.** A user attempting prompt or SQL injection. A model that is manipulated or simply wrong. Hostile text in the data. A person altering a saved receipt. A compromised dependency or CI action.

| STRIDE | Scenario | Control | Tested in |
|---|---|---|---|
| Spoofing | A replayed answer passed off as live | `source="replay"` on every call, demo label in the UI | `test_replay.py` |
| Tampering | Data changed through generated SQL | Read-only URI, `query_only`, authorizer, static guard | `test_sql_engine.py`, `test_security_adversarial.py` |
| Tampering | A saved receipt edited | Fingerprint over all material fields | `test_security_adversarial.py` |
| Repudiation | No record of what the model returned | Usage ledger with raw responses | `test_llm.py` |
| Information disclosure | Key in logs, errors or pages | Environment only, redaction, secret scan | `test_no_secrets.py`, `test_llm.py` |
| Information disclosure | System tables read | Static guard, authorizer | `test_sql_guard.py`, `test_security_adversarial.py` |
| Denial of service | Runaway or oversized query | Time and step budget, row and byte caps, SQLite limits | `test_sql_hardening.py` |
| Denial of service | Quota drained | Bounded retries, no retry of daily quota, per-session cap | `test_llm.py`, `test_security_adversarial.py` |
| Elevation of privilege | Injected instruction runs a write or a tool | Code decides what runs; the model has no tool beyond structured fields | `test_security_adversarial.py`, `test_agent.py` |

## Findings

No Critical or High finding was identified. Every finding below is fixed on this branch.

### S-01 Medium: extreme nesting raised out of the validator

- **Description.** `validate_sql` caught parser errors but not `RecursionError`.
- **Impact.** A deeply nested draft ended the request with an internal error instead of a rejection. Nothing unsafe ran.
- **Reproduction.** `validate_sql("SELECT " + "(" * 4000 + "1" + ")" * 4000 + " FROM holdings", schema)` raises `RecursionError`.
- **Fix.** `validate_generated_sql` returns a `too_long` rejection.
- **Regression test.** `test_extreme_nesting_is_refused_rather_than_crashing_the_validator`.
- **Residual risk.** None known for this path.

### S-02 Medium: model output was parsed leniently

- **Description.** Unexpected keys were ignored, values were coerced between types, and no size limit applied.
- **Impact.** A malformed or manipulated response could be accepted in a shape the code did not intend.
- **Reproduction.** A `RouterDecision` payload with an extra key parsed successfully.
- **Fix.** `strict_value` applies to live, cached and replayed responses: no unexpected keys, strict types, 64,000 characters in total, 20,000 per string and 20 items per array. The JSON schema sent to Gemini is unchanged, so recorded requests still match.
- **Regression tests.** `test_invalid_router_output_is_rejected`, `test_malformed_truncated_or_incomplete_output_is_rejected`.
- **Residual risk.** A response that is well formed can still be wrong. Later layers handle that.

### S-03 Medium: the receipt fingerprint left out material fields

- **Description.** Checks, caveats, columns, financial verification and the model were not hashed, and only 16 hex characters were shown.
- **Impact.** Those fields could change without the fingerprint changing.
- **Reproduction.** Editing a caveat on a receipt left `fingerprint()` unchanged on the previous code.
- **Fix.** All material fields are hashed, the full digest is displayed, and `receipt_is_intact` compares a saved receipt with its recorded fingerprint.
- **Regression tests.** `test_changing_a_material_field_changes_the_fingerprint`, `test_changing_a_result_row_or_the_sql_changes_the_fingerprint`.
- **Residual risk.** A fingerprint detects a change only if the original value is kept elsewhere. It is not a signature and proves no author.

### S-04 Medium: a receipt could claim reconciliation without evidence

- **Description.** `FinancialVerification` accepted `INDEPENDENTLY_RECONCILED` with no figures.
- **Impact.** A code defect or edited payload could overstate assurance.
- **Reproduction.** `FinancialVerification(status=INDEPENDENTLY_RECONCILED, summary="x")` constructed successfully.
- **Fix.** A model validator requires checked rows and a reconciled figure for each calculated column.
- **Regression test.** `test_a_reconciled_claim_needs_evidence`.
- **Residual risk.** None known for this check. The states are shown separately in the receipt.

### S-05 Low: gaps in static SQL rejection

- **Description.** The frozen guard accepted `WITH RECURSIVE`, typed functions such as `random()`, and CTEs named after system tables.
- **Impact.** Defence in depth only. SQLite's authorizer refused the recursive query and `random()` when executed; the CTE case reads no system table.
- **Reproduction.** `validate_sql` returns `ok=True` for each.
- **Fix.** `sql_generated_guard.py` adds these rejections and an AST size limit on top of the guard. `sql_guard.py` stays exactly as recorded in the security manifest.
- **Regression tests.** `test_unsafe_statements_are_rejected_statically`, `test_every_query_the_assessment_or_the_recordings_contain_is_still_accepted`.
- **Residual risk.** SQLGlot and SQLite can disagree on parsing, which is why the database layer remains the final control.

### S-06 Low: no per-session limit on live questions

- **Description.** A browser session could keep asking live questions until the provider's quota ended it.
- **Impact.** One session could use the whole daily quota.
- **Reproduction.** Inspection of `ask.py`.
- **Fix.** 15 live questions per session. Demo mode is not limited.
- **Regression test.** `test_a_session_cannot_ask_unlimited_live_questions`.
- **Residual risk.** A new session starts a new count. The provider's project quota remains the overall ceiling.

### S-07 Low: CI actions referenced by mutable tags

- **Fix.** `actions/checkout` and `actions/setup-python` are pinned to commit SHAs. Permissions remain `contents: read`.
- **Residual risk.** Pins need occasional manual updates. The workflow change has not run on GitHub, because the branch has not been pushed.

### S-08 Informational: server-error retries had no jitter

- **Fix.** Up to one second of jitter on the single permitted retry.

## SQL threat coverage

| Threat | Static layer | Database layer |
|---|---|---|
| Writes, DDL, PRAGMA, ATTACH, DETACH | Rejected | Refused |
| Several statements | Rejected | Refused |
| System tables and table-valued pragmas, including through a CTE name | Rejected | Refused for real system tables |
| Unknown tables and columns, per scope, through aliases, joins, CTEs and subqueries | Rejected | SQLite error |
| Unapproved functions, `load_extension`, typed or not | Rejected | Refused |
| Recursive CTEs | Rejected | Refused |
| Very large or deep queries | Rejected | SQLite limits |

The string that is validated is the string that runs. Generated SQL has one execution path, and a test keeps it that way.

## SQLite limits

Every connection used for generated SQL is opened with `open_read_only`: read-only URI, `query_only`, authorizer, and limits on string length (1 MB), SQL length (100,000), columns (200), expression depth (100), compound selects (20), VM operations (200,000), function arguments (32), attached databases (0), LIKE patterns (1,000) and variables (32). Each statement stops at 2,000 ms or 50 million VM steps. Results stop at 500 rows or 2 MB and are marked as cut.

Other connections (`read_schema`, `invariants.py`, `dates.py`, `semantic.py`, `db.py`) run only fixed queries written in the code.

## Prompt injection

Question text is passed as delimited data. Tests use a scripted model that obeys the injection, so they exercise the code's response and not the model's discipline: injected writes, system-table reads, recursive queries and malicious portfolio arguments are rejected, hostile text stored in the database stays data, and the database file's hash is unchanged. The model never receives query results. Answer text is composed by code.

## Credentials

Keys come only from the environment or a git-ignored `.env`. The settings object hides the key from its printed form and errors are redacted. Across the complete Git history, every tracked file and the full branch diff, the key-pattern scan found 0 matches, and `.env` is not tracked.

## Session isolation

Streamlit session state is per session, and a two-session test confirms that history does not cross between them. Markup in a question is escaped. Cached functions hold only reference data.

## Receipt integrity

The receipt separates SQL execution, result checks, independent verification and answer text, through `assurance()`. A fingerprint is deterministic, ignores ids, times and latencies, and changes when any material field changes.

## Dependencies

`pip-audit` on 9 November 2026, against both the pinned requirements and the full installed set: no known vulnerabilities. This reflects the advisory database on that date. No dependency was added or changed.

## CI

Linux and Windows, Python 3.12, offline, `contents: read`, actions pinned to SHAs.

## Adversarial test results

The full offline suite has 1,030 tests, all passing on Python 3.13 and on a clean Python 3.12 install from the pins. `tests/test_security_adversarial.py` and `tests/test_q8_verification.py` were added in this work.

## Remaining risks

- **Temporary storage (Low).** `sql_engine.py` is frozen and does not set `temp_store`, so a large sort may use temporary files. The time and step budgets bound it.
- **No output-token cap (Low).** Oversized responses are rejected after they arrive, which still spends tokens.
- **Injection can still steer a valid read-only query (Medium).** It cannot write or reach other tables, but it can produce a wrong answer. Independent verification catches some of these, not all.
- **No authentication in the Streamlit app (Low in the intended setup).** It binds to localhost. Exposing it on a network would need access control.
- **Verifier scope.** Over 35 recorded production answers, 11 reconcile, 17 contain no calculation and 7 cannot be rebuilt. Those 7 are reported as not verifiable.
