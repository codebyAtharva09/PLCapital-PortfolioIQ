# PortfolioIQ

**An AI agent that answers plain-English questions about a portfolio database and shows how each answer was produced.**

Gemini chooses the tool and drafts the SQL. Python runs the SQL read-only, calculates every figure and writes every answer.
A command-line agent, an evaluator and an optional Streamlit app share the same backend.

> **Status.** On the final code, the active configuration (`candidate-2.3` on `gemini-3.6-flash`) scored **10 of 10** live on the official
> questions through the agent ([report](reports/final_live/official_evaluation.md), [how it was run](reports/final_live/STATUS.md)).

![PortfolioIQ Overview](docs/assets/screenshots/01_overview_first_screen.png)

## Assignment coverage

| Requirement | What it does | Start with |
|---|---|---|
| SQL Query Tool | Gemini turns a question into one `SELECT`, which is validated and run read-only | `src/portfolioiq/sql_tool.py`, `sql_guard.py` |
| Exposure Calculator | Sector exposure from `holdings.current_weight`, with bonds excluded | `src/portfolioiq/exposure.py` |
| Portfolio Agent (CLI) | Gemini chooses a tool, asks for clarification, or declines | `src/portfolioiq/agent.py`, `router.py`, `cli.py` |
| Agent Evaluator | Runs the ground truth through the agent and compares executed results | `src/portfolioiq_eval/official.py`, `comparator.py` |

**These eight files, about 2,500 lines, are the assignment.** The rest of the repository is independent verification, the optional
Streamlit app, tests and recorded evidence.

## Quick start

Requires Python 3.12 or later.

```bash
git clone https://github.com/codebyAtharva09/PLCapital-PortfolioIQ.git
cd PLCapital-PortfolioIQ
python -m venv .venv
# Windows (PowerShell): .venv\Scripts\Activate.ps1      macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
```

The database builds itself from `data/` on first use.

```bash
# The agent
python -m portfolioiq ask "How many portfolios do we have in total?"                              # live, needs a key
python -m portfolioiq ask --replay "How many holdings does the Dividend Aristocrats Fund have?"   # recorded responses, no key
python -m portfolioiq exposure "international equity"                                             # no Gemini, no key

# The evaluator: the ten ground-truth questions through the agent
python -m portfolioiq_eval official --max-requests 18                                             # live, about 18 Gemini requests
python -m portfolioiq_eval official --rescore                                                     # recorded live run, no key

# Tests and the optional app
pytest                                                                                            # offline, no key
streamlit run app/streamlit_app.py                                                                # opens http://localhost:8501
```

Set the key with `$env:GEMINI_API_KEY = "<your key>"` (PowerShell) or `export GEMINI_API_KEY="<your key>"`, or put it in a git-ignored
`.env` file. Google's free tier allows 20 requests a day for `gemini-3.6-flash`.

## How it works

```mermaid
%%{init: {"flowchart": {"wrappingWidth": 460}}}%%
flowchart TD
    USER["<b>User / Reviewer</b><br/>CLI, Streamlit app or evaluators"]
    IFACE{"Interface<br/>Layer"}
    AGENT["<b>PortfolioAgent (agent.py)</b><br/>Validates input; one route;<br/>at most 3 Gemini calls a question"]
    ROUTER["<b>Gemini Router (router.py, llm.py)</b><br/>gemini-3.6-flash, thinking LOW<br/>RouterDecision: validated, no numeric field"]
    DEMO["<b>Demo mode (replay.py)</b><br/>Exact recorded responses; sends nothing"]
    ERROR["<b>Typed error</b><br/>Quota or API failure;<br/>no invented answer"]
    SQL["<b>SQL Query Tool (sql_tool.py)</b><br/>Semantic context from the schema;<br/>Gemini drafts one SELECT; one repair"]
    GUARD["<b>SQL Guard (sql_guard.py)</b><br/>SQLGlot AST: one statement; allow-listed<br/>tables, columns and functions"]
    RESOLVER["<b>Portfolio Resolver (resolver.py)</b><br/>Fixed rules; parameterised queries;<br/>SQL syntax is rejected"]
    EXPOSURE["<b>Exposure Engine (exposure.py)</b><br/>Methods A and B on two paths,<br/>reconciled within 1e-9; bonds excluded"]
    CLARIFY["<b>Clarify or decline</b><br/>Reply with no tool run"]
    DB[("<b>SQLite database (rebuilt from the supplied CSVs)</b><br/>URI mode=ro + PRAGMA query_only + authorizer<br/>2,000 ms, 50M VM steps, 500 rows, 2 MB, SQLite limits<br/>One execution path for generated SQL")]
    CHECKS["<b>Checks and independent verification</b><br/>Result checks and result-shape policy (sql_policy.py)<br/>Independent verification: figures rebuilt in Python from source records<br/>States: validated, reconciled, not verifiable, or verification failed"]
    COMPOSER["<b>Answer Composer (agent.py)</b><br/>Code writes every figure;<br/>Gemini never sees query results"]
    RECEIPT["<b>Financial Answer Receipt (receipt.py)</b><br/>Route, SQL, figures, checks,<br/>verification state, versions, fingerprint"]
    FINAL["<b>Final Response + Receipt</b><br/>CLI, Streamlit app, JSON download, evaluators"]

    USER --> IFACE
    IFACE -->|"CLI (cli.py)"| AGENT
    IFACE -->|"Web UI (streamlit_app.py)"| AGENT
    IFACE -->|"Evaluators (portfolioiq_eval)"| AGENT
    IFACE -->|"No model needed"| DB
    AGENT --> ROUTER
    DEMO -.-> ROUTER
    ROUTER -->|"429 / 503 / 504"| ERROR
    ROUTER -->|"sql"| SQL
    ROUTER -->|"exposure_for_sql_results (hybrid)"| SQL
    ROUTER -->|"exposure"| RESOLVER
    ROUTER -->|"clarify / decline"| CLARIFY
    SQL --> GUARD
    SQL -->|"portfolio names"| RESOLVER
    RESOLVER --> EXPOSURE
    GUARD -->|"read-only"| DB
    EXPOSURE -->|"read-only"| DB
    DB --> CHECKS
    CHECKS --> COMPOSER
    CLARIFY --> COMPOSER
    ERROR --> COMPOSER
    COMPOSER --> RECEIPT
    RECEIPT --> FINAL

    classDef app fill:#ECECFF,stroke:#7C5CD6,color:#2B2B3A
    classDef control fill:#E6F5EC,stroke:#2E9E5B,color:#2B2B3A
    classDef alt fill:#FFF4DF,stroke:#D9962B,color:#2B2B3A
    class USER,IFACE,AGENT,ROUTER,SQL,GUARD,RESOLVER,EXPOSURE,CLARIFY,COMPOSER,RECEIPT,FINAL app
    class DB,CHECKS control
    class DEMO,ERROR alt
```

Only the router and the SQL draft call Gemini, and Gemini never receives query results. Exposure uses `holdings.current_weight`, excludes
bonds, and reports each sector as a share of the whole portfolio and of the equity holdings.

## Results

| Evaluation (live, `gemini-3.6-flash`) | Result |
|---|---|
| **Official Q1-Q10 through the agent, final code** | **PASS 10** (the original configuration scored PASS 8, FAIL 2) |
| SQL tool, 10 unseen questions, strict, before the final hardening | PASS 10 |
| SQL tool, 3 unsafe prompts, before the final hardening | Refused 3 of 3 |
| Frozen held-out benchmark, 16 cases, before the final hardening | PASS 13, correctly refused 3 |

The offline suite passes 1,057 tests, using fakes in place of Gemini. Reports are in [reports/](reports/), and each live run keeps its raw
receipts.

## Known limitations

- The rules in the active SQL prompt were written after studying failures in the official and unseen sets, so those scores are development results.
- Each configuration was run once, so repeatability is unmeasured.
- It is a local, single-user, read-only tool with no authentication.

## More detail

[docs/DETAILS.md](docs/DETAILS.md) has the configuration, methodology, security design, full results, command reference and repository
layout. [docs/REVIEWER_GUIDE.md](docs/REVIEWER_GUIDE.md) is a five-minute walkthrough, and [docs/EVALUATION.md](docs/EVALUATION.md) has the
per-case evaluation.
