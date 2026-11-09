# Final audit report

> **Update, 10 November 2026.** The live official evaluation on the final code has since completed: PASS 10. See `reports/final_live/STATUS.md`. The sections below describe the state on 9 November.

State on 9 November 2026. `main` holds the production release plus the README and logo updates. The security work is on the unmerged branch `security-financial-verification-sprint`. The verification work below is uncommitted on `final-verification-evaluation-sprint`.

## Priorities

| Priority | State | What exists | What is missing |
|---|---|---|---|
| 1. Independent financial verification | Mostly complete, offline | Pandas reference over the CSVs, 39-item inventory, none failed, wider runtime verifier | Stored performance and risk figures cannot be recomputed; a decision on Method B is open |
| 2. Live evaluation after hardening | Incomplete | Configuration manifest. One attempt stopped by a daily quota error after 2 requests | Official Q1 to Q10 and U1 to U10 on the hardened code |
| 3. Independent benchmark and repeatability | Incomplete | Provenance, coverage and hashes for the 35 cases | No live run, no live repeatability |
| 4. GitHub presentation | Complete on `main` | README with Mermaid architecture, assignment coverage and test notes; description and topics set | None for this sprint |
| 5. CI versions | Prepared, not run | Matrix for Ubuntu and Windows with Python 3.12 and 3.13, uncommitted | A GitHub run of the new matrix |

## Live evaluation plan

Each day's window allows at most 20 requests and resets at 08:00 UTC. Day 1: official Q1 to Q10, 18 requests. Day 2: U1 to U10 through the SQL tool. After that, the 35 independent cases take about three windows, and repeatability needs a further window or more.

## Test commands

```bash
pytest
python scripts/run_independent_verification.py
python scripts/snapshot_baseline.py --check
python -m portfolioiq_eval official --replay-check --runs-dir reports/sprint_v2_3/candidate/runs
```

## Changed files on the sprint branch

Modified: `.github/workflows/ci.yml`, `src/portfolioiq/financial_verification.py`, `tests/test_financial_verification.py`, `tests/test_q8_verification.py`.

Added: `src/portfolioiq_eval/independent_reference.py`, `scripts/run_independent_verification.py`, `scripts/freeze_eval_config.py`, `tests/test_independent_reference.py`, `docs/FINAL_AUDIT_REPORT.md`, `docs/INDEPENDENT_FINANCIAL_VERIFICATION.md`, `docs/INDEPENDENT_BENCHMARK_REPORT.md`, `docs/SECURITY_REGRESSION_REPORT.md`, `reports/final_verification/`.

`LIVE_EVALUATION_REPORT.md` was not written, because no live evaluation has completed.

## Known limitations

- The live 10 of 10 belongs to the production code. The hardened code has offline replay evidence only.
- Each configuration has been run once, so live repeatability is unmeasured.
- The independent benchmark is project-written and has no human review.
- The Method B rule in the brief differs from frozen rule B2. No figure differs on the supplied data.
- The recorded Q8 query does not filter distinct sectors to stocks. It is correct on this data because bonds have no sector.
