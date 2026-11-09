# Security regression report

Offline checks on the working tree of `final-verification-evaluation-sprint`, 9 November 2026. No new vulnerability was found in this sprint. This is not a security certification. Findings and residual risks are in [SECURITY_AUDIT.md](SECURITY_AUDIT.md).

| Control | Test evidence | Result |
|---|---|---|
| Read-only connection, query-only mode, authorizer | `test_sql_engine.py`, `test_sql_hardening.py` | Pass |
| Parser and AST validation, table, column and function allow-lists | `test_sql_guard.py`, `test_security_adversarial.py` | Pass |
| Parameterised resolver queries, SQL syntax rejected | `test_resolver.py` | Pass |
| Time, VM-step, row and result-byte limits | `test_sql_engine.py`, `test_sql_hardening.py` | Pass |
| Prompt injection, including hostile text stored in the database | `test_security_adversarial.py`, `test_sql_tool.py`, `test_agent.py` | Pass |
| Model output validation | `test_security_adversarial.py`, `test_llm.py` | Pass |
| Streamlit session isolation and escaping | `test_security_adversarial.py`, `test_streamlit_app.py` | Pass |
| Secret handling and key-pattern scan | `test_no_secrets.py`, `test_config.py` | Pass |
| Frozen baseline and security manifest | `scripts/snapshot_baseline.py --check` | Unchanged |

The SQL security group (guard, engine, hardening, adversarial and secret tests) has 169 tests, and all pass.

`pip-audit` last ran on 9 November 2026 and reported no known vulnerabilities. No dependency has changed since. It was not rerun for this report.

The security changes and CI pins exist on the unmerged branches. GitHub-hosted CI has not run on them.
