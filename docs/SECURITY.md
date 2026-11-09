# Security

PortfolioIQ treats everything that comes from Gemini or from a user as untrusted. Security never
depends on the model following its instructions. Every control below has automated tests.

## SQL: two independent layers

Generated SQL must pass both layers. Each would block a prohibited query on its own.

### Layer 1: static validation with SQLGlot (`src/portfolioiq/sql_guard.py`)

| Control | Rejects |
|---|---|
| Exactly one statement | `SELECT 1; DROP TABLE holdings` |
| Root must be SELECT or a set operation (UNION, INTERSECT, EXCEPT) | Anything else, including text SQLGlot parses leniently (`SELEC * FRM x` parses as an alias) |
| No forbidden node anywhere in the tree | INSERT, UPDATE, DELETE, REPLACE, DROP, CREATE, ALTER, PRAGMA, ATTACH, DETACH, transaction control, SELECT INTO, ANALYZE, VACUUM, including a DELETE hidden inside a WITH clause |
| Table allow-list (the nine supplied tables, plus CTEs the query defines) | Unknown tables, `sqlite_*` system tables, schema-qualified names (`main.portfolios`), table-valued functions (`pragma_table_info(...)`) |
| Column allow-list (schema columns, plus aliases the query defines) | Unknown columns, and double-quoted "strings", which SQLite reads as identifiers |
| Function allow-list for functions SQLGlot does not model | `load_extension`, `randomblob`, and any other unlisted function |

Security categories (multiple statements, forbidden operations, system tables, qualified names,
forbidden functions) are rejected outright. Only plain mistakes (unparseable SQL, an unknown column
or table, an execution error) get one repair round.

### Layer 2: SQLite's own controls (`src/portfolioiq/sql_engine.py`)

| Control | Effect |
|---|---|
| Read-only URI (`mode=ro`) | The database file cannot be written through this connection |
| `PRAGMA query_only = ON` | Any write is refused |
| Authorizer | Allows only SELECT, reads of the nine tables and allow-listed functions. Denies writes, schema changes, ATTACH, PRAGMA, recursive CTEs and everything else |
| One statement per call | Python's sqlite3 refuses multiple statements |
| Time limit | A progress handler stops any query after 2 seconds |
| Row cap | At most 500 rows are returned, and truncation is flagged |
| Immutable source | The database is rebuilt from the CSVs per data fingerprint and never edited; the supplied files are checksum-verified |

`tests/test_sql_engine.py` runs dangerous SQL straight against the executor, with the guard
bypassed, and checks that the database file's SHA-256 is identical afterwards.

## Untrusted model output

- Gemini may return only a JSON object matching `SqlDraft`: a query or a reasoned refusal. Anything
  else is a `malformed_output` error, never a guess.
- The SQL is validated and run read-only. No model output is ever executed as Python or any other code.
- The user's question is passed to Gemini as delimited data, with an instruction to ignore embedded
  instructions. A prompt injection that makes Gemini write `DROP TABLE` is still stopped by both layers.

## Agent routing

- The router may return only a `RouterDecision`: one of five actions, plus text arguments. It has no
  numeric field, so no figure can travel from the model into an answer. The answer text is composed
  by code from tool results.
- The router never runs anything itself. `sql` passes the user's question to the SQL tool, so both
  SQL layers still apply. A write request that the router wrongly sends to `sql` is still stopped by
  the guard; this is tested.
- A portfolio argument proposed by Gemini goes through the resolver. It is checked for SQL syntax
  (SR1), resolved only by documented rules, and used only when the question or an earlier tool result
  names it (grounding). Otherwise the user is asked.
- Hybrid questions follow one fixed pattern: one SQL query, then the exposure engine for each
  portfolio named in its result, at most one per portfolio in the database. There is no open-ended
  loop of model calls; a question costs at most three Gemini calls.
- The exposure engine runs under the same read-only connection and authorizer as generated SQL.

## Demo replay (DEMO / FIXTURE MODE)

- `src/portfolioiq/replay.py` has no network code. It answers a request only when the usage ledger
  holds a successful response to the byte-identical request (same request key as the live client);
  otherwise it fails with `not_recorded` and nothing is invented.
- Replayed responses are validated against the expected schema exactly like live ones, and every
  replayed call is marked `source="replay"` with its original timestamp in the receipt.
- Replay never writes to the ledger (tested by comparing the file's bytes before and after).

## Streamlit app

- Every value inserted into HTML (database values, model output, user input) passes through
  `html.escape`; `st.html` additionally sanitises with DOMPurify and runs no JavaScript (tested with a
  `<script>` cell).
- The app reads the database through the same read-only connection and authorizer as the CLI, and
  evaluation results from the report files; it never reads the ground truth.
- `.streamlit/config.toml` binds the server to `localhost`, turns off usage statistics, and serves the
  IBM Plex fonts from the app itself, so the browser makes no third-party requests.
- Live Gemini mode is disabled when no key is configured, and the app opens in demo mode.
- Errors are shown as short callouts; a broken data folder stops the app with a message, not a trace.

## Data checks

`src/portfolioiq/invariants.py` uses a read-only URI connection without the authorizer, because
`PRAGMA integrity_check` and `foreign_key_check` must run. Only these fixed, trusted queries use it;
user or model SQL never does.

## Portfolio-name input (rule SR1)

A portfolio reference containing SQL syntax is rejected before any matching; it is never sanitised
and then matched. Resolver queries are parameterised. See `docs/BUSINESS_RULES.md`.

## API usage records

- `reports/api_usage.jsonl` records every Gemini request: timestamp, purpose, model, status,
  latency, tokens and error kind, plus the raw text of every response received, including one
  that failed validation. It also records each request that was not sent because a run's request
  cap was reached. It never contains the API key (tested).
- Saved responses in `.cache/llm_responses/` are git-ignored. They hold model output only, which is
  validated again before reuse, exactly like a fresh response. A corrupt entry is ignored.

## Secrets

- The API key is read only from the `GEMINI_API_KEY` environment variable or a git-ignored `.env`.
  `.env.example` holds only a placeholder.
- The key is excluded from the settings' printed form, redacted from error messages, and never logged.
- `tests/test_no_secrets.py` scans every file git would commit for credential-shaped strings and for
  the configured key values. A planted fake key confirmed the scan fails when it should.
- The key once exposed in `.env.example` was moved, then revoked, and `.env` was deleted. Live runs use
  a separate key from the Windows environment.

## Errors

Users see short, safe messages: what failed and what still works. Raw stack traces and internal
paths are not shown, and SQLite authorization errors are reported as a fixed "Not allowed" message.

## Tests

| File | Covers |
|---|---|
| `tests/test_sql_guard.py` | Every rejection category; all eight reference queries accepted |
| `tests/test_sql_engine.py` | Database-level blocking without the guard, time limit, row cap, unchanged database |
| `tests/test_sql_tool.py` | Unsafe drafts rejected and never repaired, prompt injection, unchanged database |
| `tests/test_llm.py` | No key leaks in errors or the usage ledger, bounded retries, no model fallback |
| `tests/test_agent.py` | Misrouted write requests stopped by the guard, SR1 on the portfolio argument, ungrounded names never used, declines run nothing, unchanged database |
| `tests/test_router.py` | Question passed as delimited data; routing output carries no numbers; no evaluation material in the prompt |
| `tests/test_resolver.py` | SR1 rejects SQL syntax and allows legitimate punctuation |
| `tests/test_no_secrets.py`, `tests/test_config.py` | Secret hygiene |
| `tests/test_replay.py` | Demo replay never creates the live client, never writes the ledger, never invents an answer |
| `tests/test_streamlit_app.py` | Every workspace renders with no Gemini request; HTML cells are escaped |
| `tests/test_invariants.py` | The data-check connection cannot write; planted data defects are reported as FAIL |
| `tests/test_cli_smoke.py` | Real subprocesses with no key: clear message, exit code 4, no traceback; the app launches |
| `src/portfolioiq_eval/benchmark.py` (R14-R17, R19) | Input validation, SR1, guard and database layer with the guard bypassed, simulated API failures |

## Resource limits and scoped columns

Added after the frozen baseline and recorded in `evaluation/security_manifest.json`, which leaves the historical hashes in
`evaluation/baseline_configuration.json` unchanged.

- Every read-only connection sets SQLite limits on string length (1 MB), SQL length, column count, expression depth, compound selects, program size,
  function arguments, LIKE patterns and variables, and allows no attached databases. A generated string over the limit is never built.
- A statement stops at the 2,000 ms time limit or a budget of 50 million virtual-machine steps, whichever comes first.
- A result stops at 500 rows or 2 MB in total, and is marked as cut.
- The guard resolves every column to the tables in its own scope, through aliases, joins, CTEs and subqueries, so a column from the wrong table is
  rejected before execution. The earlier schema-wide check stays in place, so this can only add rejections.

Tests: `tests/test_sql_hardening.py`.

