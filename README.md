# PortfolioIQ

**An AI agent that answers plain-English questions about a portfolio database and shows how each answer was produced.**

Gemini chooses the tool and drafts the SQL. Python runs the SQL read-only, calculates every figure and writes every answer.
A command-line agent, an evaluator and an optional Streamlit app share the same backend.

## Assignment coverage

| Requirement | What it does | Start with |
|---|---|---|
| SQL Query Tool | Gemini turns a question into one `SELECT`, which is validated and run read-only | `src/portfolioiq/sql_tool.py`, `sql_guard.py` |
| Exposure Calculator | Sector exposure from `holdings.current_weight`, with bonds excluded | `src/portfolioiq/exposure.py` |
| Portfolio Agent (CLI) | Gemini chooses a tool, asks for clarification, or declines | `src/portfolioiq/agent.py`, `router.py`, `cli.py` |
| Agent Evaluator | Runs the ground truth through the agent and compares executed results | `src/portfolioiq_eval/official.py`, `comparator.py` |

**These eight files, about 2,500 lines, are the assignment.** The rest of the repository is independent verification, the optional
Streamlit app, tests and recorded evidence.

## Evaluator quick read

**Official Q1–Q10 Agent Evaluation: 10/10 PASS**, live on the final code ([report](reports/final_live/official_evaluation.md), [how it was run](reports/final_live/STATUS.md)).

| Evidence | Result | Type | Source |
|---|---|---|---|
| Official agent evaluation, Q1–Q10 | 10/10 PASS | Live, final code | [report](reports/final_live/official_evaluation.md) |
| SQL tool, unseen questions | 10/10 PASS | Live, pre-final-hardening | [report](reports/sprint_v2_3/candidate/sql_evaluation.md) |
| Unsafe prompts | 3/3 refused | Live, pre-final-hardening | [report](reports/sprint_v2_3/candidate/sql_evaluation.md) |
| Held-out benchmark, 16 cases | 13 passed, 3 correctly refused | Live, pre-final-hardening | [report](reports/heldout_v2_3/HELDOUT_RESULT.md) |
| Q9–Q10 deterministic calculations | 2/2 independently reconciled | No Gemini | [report](reports/final_live/official_evaluation.md), [fixtures](evaluation/fixtures/) |
| Offline test suite | 1,057 passed | Offline, fake Gemini clients | [results](reports/test_results.json) |
| Direct SQL-tool Q1–Q8 on the final code | `NOT EVALUATED` | Not run separately | [report](reports/final_live/official_evaluation.md) |
| Repeatability | `NOT MEASURED` | Each question answered once | [status](reports/final_live/STATUS.md) |

## Architecture

```mermaid
%%{init: {"flowchart": {"wrappingWidth": 460, "nodeSpacing": 34, "rankSpacing": 38}}}%%
flowchart TB
    RULE["BOUNDARY: Gemini chooses tools and drafts SQL.<br/>Python validates, executes, calculates, verifies and composes numeric answers.<br/>Gemini never receives SQL result rows and does not author financial figures."]:::rule

    subgraph L1["Layer 1: Input and agent"]
        Q["User question<br/>from the CLI or Streamlit"]:::py
        VAL["Input validation"]:::py
        ROUTER["GEMINI: router"]:::gem
        DECIDE["Validated routing decision<br/>has no numeric field"]:::py
        Q --> VAL --> ROUTER --> DECIDE
    end
    RULE ~~~ Q

    subgraph L2["Layer 2: Tool selection"]
        subgraph RSQL["SQL route"]
            S1["GEMINI: drafts one SELECT"]:::gem
            S2["SQLGlot validation"]:::py
            S3["SQLite authorizer and<br/>read-only execution"]:::py
            S4["Time, step, row and byte limits;<br/>result validation"]:::py
            S5["SQL result returned to<br/>application code only"]:::py
            S1 --> S2 --> S3 --> S4 --> S5
        end
        subgraph REXP["Exposure route"]
            E1["Deterministic portfolio resolver"]:::py
            E2["Exposure calculator<br/>Method A: sector stock weights / whole portfolio<br/>Method B: sector stock weights / eligible equity sleeve, rule B2"]:::py
            E5["Independent calculation and reconciliation"]:::py
            E1 --> E2 --> E5
        end
        subgraph RCLR["Clarify or decline route"]
            C1["Ambiguous portfolio reference:<br/>ask for clarification"]:::py
            C2["Unsupported or unsafe question:<br/>decline"]:::py
        end
    end

    DECIDE -->|"sql or exposure_for_sql_results"| S1
    DECIDE -->|"exposure"| E1
    DECIDE -->|"clarify"| C1
    DECIDE -->|"decline"| C2
    S5 -.->|"hybrid: portfolio names"| E1
    E1 -.->|"ambiguous name"| C1

    subgraph L3["Layer 3: Verify and answer"]
        V1["Independent financial verification<br/>figures rebuilt in Python where source data allows"]:::py
        V2["Verification status"]:::py
        A1["Python composes the final answer"]:::py
        A2["Financial Answer Receipt"]:::py
        INV["Financial invariants: 22 database-wide checks<br/>run on demand, not per answer"]:::py
        V1 --> V2 --> A1 --> A2
    end
    S5 --> V1
    E5 --> V2
    C1 --> A1
    C2 --> A1

    subgraph L4["Layer 4: Outside the agent"]
        CLI["CLI"]:::py
        ST["Streamlit"]:::py
        EV["Official evaluator<br/>calls the agent as a client"]:::ev
        GT["Ground truth<br/>read only by the evaluator"]:::ev
        IV["Independent verification<br/>pandas reference over the CSVs"]:::ev
        TEST["Offline test suite<br/>fake Gemini clients"]:::ev
        GT --> EV
    end
    A2 --> CLI
    A2 --> ST
    A2 -.->|"receipts scored outside the agent"| EV
    V1 ~~~ INV

    subgraph KEY["Legend"]
        K1["Calls Gemini"]:::gem
        K2["Deterministic Python"]:::py
        K3["Evaluation, not runtime"]:::ev
    end
    IV ~~~ K1

    style L1 fill:#F7F7FB,stroke:#BBBBC8,color:#111111
    style L2 fill:#F7F7FB,stroke:#BBBBC8,color:#111111
    style L3 fill:#F7F7FB,stroke:#BBBBC8,color:#111111
    style L4 fill:#F7F7FB,stroke:#BBBBC8,color:#111111
    style RSQL fill:#FFFFFF,stroke:#BBBBC8,color:#111111
    style REXP fill:#FFFFFF,stroke:#BBBBC8,color:#111111
    style RCLR fill:#FFFFFF,stroke:#BBBBC8,color:#111111
    style KEY fill:#FFFFFF,stroke:#BBBBC8,color:#111111
    classDef rule fill:#12263F,stroke:#12263F,color:#FFFFFF
    classDef gem fill:#DCEBFF,stroke:#1F6FEB,color:#111111
    classDef py fill:#ECECFF,stroke:#7C5CD6,color:#111111
    classDef ev fill:#EEEEEE,stroke:#777777,color:#111111
```

## Evaluation evidence map

```mermaid
%%{init: {"flowchart": {"wrappingWidth": 460, "nodeSpacing": 34, "rankSpacing": 38}}}%%
flowchart TB
    subgraph FINAL["Final code e525733"]
        A["LIVE FINAL CODE<br/>Official Q1-Q10 through the agent<br/>PASS 10 of 10, one run per question"]:::pass
        B["DETERMINISTIC, NO GEMINI<br/>Q9-Q10 against hand-derived fixtures<br/>PASS 2 of 2, INDEPENDENTLY_RECONCILED"]:::pass
        C["OFFLINE, FAKE GEMINI CLIENTS<br/>Automated test suite<br/>1,057 passed, no model accuracy measured"]:::pass
    end
    subgraph HIST["Pre-hardening, not rerun"]
        D["HISTORICAL PRE-HARDENING<br/>SQL tool, 10 unseen questions<br/>PASS 10 of 10, strict"]:::old
        E["HISTORICAL PRE-HARDENING<br/>3 unsafe prompts<br/>3 of 3 refused"]:::old
        F["HISTORICAL PRE-HARDENING<br/>Held-out benchmark, 16 cases<br/>13 passed, 3 correctly refused"]:::old
    end
    subgraph GAPS["No result exists"]
        G["NOT EVALUATED<br/>Standalone direct SQL-tool Q1-Q8<br/>live run on the final code"]:::none
        H["NOT MEASURED<br/>Repeatability<br/>each question answered once"]:::none
    end
    subgraph KEY["Legend"]
        K1["Green: completed and passed"]:::pass
        K2["Amber: historical, pre-hardening"]:::old
        K3["Grey: not evaluated or not measured"]:::none
    end
    A ~~~ D
    D ~~~ G
    G ~~~ K1

    style FINAL fill:#F7F7FB,stroke:#BBBBC8,color:#111111
    style HIST fill:#F7F7FB,stroke:#BBBBC8,color:#111111
    style GAPS fill:#F7F7FB,stroke:#BBBBC8,color:#111111
    style KEY fill:#FFFFFF,stroke:#BBBBC8,color:#111111
    classDef pass fill:#D7F0DF,stroke:#2E9E5B,color:#111111
    classDef old fill:#FFF0D1,stroke:#D9962B,color:#111111
    classDef none fill:#E4E4E4,stroke:#777777,color:#111111
```

## Evaluation results and how to interpret them

**Official Q1–Q10 Agent Evaluation: 10/10 PASS.** All ten supplied questions went through the complete agent workflow, and each result was compared
with the official reference. Q1–Q8 used the SQL route, and Q9–Q10 used the deterministic exposure calculator route. This is an end-to-end agent
evaluation, not a separate direct evaluation of the SQL tool.

Recorded configuration: `gemini-3.6-flash`, `candidate-2.3`, SQL prompt `314e91c11435`, router `676786d09950`, thinking LOW, response cache off. Code commit
`e52573335b0e92daef369d915f3983f5eaa4d653`, run on 9 November 2026 from 20:00 to 20:25 UTC. [Configuration manifest](reports/final_verification/eval_config_20261109T200056Z.json).

Each question was answered once, so repeatability was not measured. The prompt rules were written after studying earlier failures on the official and unseen
questions, so this is a successful development evaluation, not a blind benchmark.

| Evaluation | Configuration / timing | Execution mode | Scope | Result | What it proves |
|---|---|---|---|---|---|
| **A. Official agent evaluation** | `candidate-2.3`, final code `e525733` | Live Gemini 3.6 Flash, one run per question | Q1–Q10 end to end | **PASS 10/10**, ten recorded outcomes | The final agent routed and answered the ten supplied questions correctly in this run |
| **B. SQL tool, unseen** `HISTORICAL — PRE-FINAL-HARDENING` | `candidate-2.3`, before the final hardening | Live Gemini, strict comparison | 10 project-written questions | PASS 10/10 | The SQL tool answered them before hardening. Not rerun on the final code |
| **C. Unsafe prompts** `HISTORICAL — PRE-FINAL-HARDENING` | `candidate-2.3`, before the final hardening | Live Gemini | 3 unsafe prompts | 3/3 refused | The tool refused these prompts before hardening. Not rerun live on the final code |
| **D. Held-out benchmark** `HISTORICAL — PRE-FINAL-HARDENING` | `candidate-2.3`, before the final hardening, one run | Live Gemini | 16 frozen cases | 13 passed, 3 correctly refused | Behaviour on project-written cases that no prompt rule came from. The shape policy's false-positive check had seen their reference SQL, so it is not a blind benchmark |
| **E. Deterministic exposure** | Final code | No Gemini numeric generation | Q9–Q10 against hand-derived fixtures | PASS 2/2, `INDEPENDENTLY_RECONCILED` | The exposure calculation matches independently derived figures. Separate from the live routing result in A |
| **F. Offline tests** | Final code | Offline, fake Gemini clients | 1,057 tests | 1,057 passed | Deterministic code paths, security, financial calculations, routing and application behaviour. Not live Gemini accuracy |

**Offline test results and live Gemini evaluation results are different evidence types and must never be combined into one accuracy percentage.**

**The historical 8/10 result does not describe the final configuration. The final-code official agent evaluation passed all ten questions.** The original
configuration scored PASS 8, FAIL 2, and [that report](reports/sprint_v2_1/baseline/official_evaluation.md) is kept as historical evidence.

| Q1–Q8 scope | Final-code status |
|---|---|
| Through the complete agent | PASS 8 of 8 |
| Standalone direct SQL-tool live run | `NOT EVALUATED` |

That is a difference in scope, not an agent failure. A direct SQL-tool run scored 8 of 8 [before the final hardening](reports/sprint_v2_3/candidate/sql_evaluation.md), and it was not repeated.
For Q9–Q10, the live evaluation confirms routing and the fixture check confirms the calculation. They are two separate pieces of evidence.

### Provenance of the final live run

- **Requests.** The run took six sessions and [32 recorded requests](reports/final_live/api_usage.jsonl): 21 succeeded, 2 were 429 quota errors and 9 were 503 errors. Provider errors are infrastructure failures, not answers. Each question has one recorded answer.
- **API keys.** The ledger does not record which key served a request, so the number of keys is not established by the evidence. It has 21 successes in one UTC day, above the 20-request daily free-tier limit for one project, and successes continue after a quota error. The run should not be read as having fitted within one project's free-tier allowance.
- **Replay.** `official --replay-check` by default replays the responses recorded before the final hardening, and reproduces those receipts on the final code. It is not a check of the final live run, so use `--rescore` for that.
- **Model variation.** Compared with the recorded pre-hardening responses, the final run produced different SQL for Q3, Q6, Q7 and Q8, and different columns and answer text for Q6 and Q7. Both runs passed. This shows that output can vary; it is not a repeatability measurement.
- **Held-out file hash.** The held-out report cites the file's hash from before a later correction to its description field. No question or expected answer changed. See [the change record](evaluation/independent_benchmark_CHANGES.md).

## What the scores do not mean

1. The official agent's PASS 10/10 does not mean the standalone SQL tool was separately evaluated on the same final run.
2. The unseen, unsafe-prompt and held-out results were obtained before the final hardening.
3. Offline tests use fake Gemini responses and do not measure live model accuracy.
4. A correct refusal is a successful safety outcome, not an incorrect answer.
5. One successful run per question does not establish repeatability.
6. A successful SQL execution does not by itself establish financial correctness.
7. `INDEPENDENTLY_RECONCILED` applies only to calculations that were rebuilt and compared against an independent reference.

## Financial methodology

- **Exposure** uses `holdings.current_weight`, and bonds are excluded from named sector rows. Method A is each sector's stock weight over the whole portfolio. Method B is the same weight over the eligible equity sleeve.
- **Residual** is 100% minus all holding weights. It is shown as unattributed and is never treated as cash.
- **AUM** is the stated `total_aum`. Holdings cover only part of it, so market value is never called AUM.
- **Relative dates** are anchored to the latest date in the data, not today's date.
- **Q8's diversification ratio** is distinct stock sectors over all holdings, bonds included.

The frozen rules are in [docs/BUSINESS_RULES.md](docs/BUSINESS_RULES.md).

## Security architecture

Model output and user input are untrusted. Generated SQL passes two separate layers: a SQLGlot guard that allows one `SELECT` over approved tables, columns and functions, then SQLite itself
with a read-only connection, `query_only`, an authorizer and time, step, row and byte limits. This is not a certification of security. See [docs/SECURITY.md](docs/SECURITY.md) and [docs/SECURITY_AUDIT.md](docs/SECURITY_AUDIT.md).

## Quick start and reproducibility

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

# Score the recorded final-code live run again, with no key and no request
python -m portfolioiq_eval official --rescore --runs-dir reports/final_live/runs --reports-dir reports/rescore

# A new live evaluation, about 18 Gemini requests
python -m portfolioiq_eval official --max-requests 18

# Tests, the pandas reference and the optional app
pytest                                                                                            # offline, no key
python scripts/run_independent_verification.py                                                    # offline, no key
streamlit run app/streamlit_app.py                                                                # opens http://localhost:8501
```

Set the key with `$env:GEMINI_API_KEY = "<your key>"` (PowerShell) or `export GEMINI_API_KEY="<your key>"`, or put it in a git-ignored
`.env` file. Google's free tier allows 20 requests a day for `gemini-3.6-flash`.

![PortfolioIQ Overview](docs/assets/screenshots/01_overview_first_screen.png)

## Known limitations

- The final live evaluation is one run per question on ten known questions, so it is not evidence of generalisation or repeatability.
- The 35 development and validation cases of the independent benchmark have not been run live ([report](docs/INDEPENDENT_BENCHMARK_REPORT.md)).
- Stored performance and risk figures can be checked for retrieval but cannot be recomputed from the supplied data.
- Frozen rule B2 excludes unclassified stocks from the Method B denominator. The supplied data has none, so no figure depends on this.
- It is a local, single-user, read-only tool with no authentication.

## Detailed reports

| Document | Contents |
|---|---|
| [reports/final_live/](reports/final_live/STATUS.md) | Final-code live evaluation: status, report and raw receipts |
| [docs/EVALUATION.md](docs/EVALUATION.md) | Evaluation methodology and historical results |
| [docs/DETAILS.md](docs/DETAILS.md) | Configuration, methodology, security, commands and layout |
| [docs/REVIEWER_GUIDE.md](docs/REVIEWER_GUIDE.md) | Five-minute walkthrough |
| [docs/INDEPENDENT_FINANCIAL_VERIFICATION.md](docs/INDEPENDENT_FINANCIAL_VERIFICATION.md) | Independent reference, inventory and unverifiable figures |
| [docs/BUSINESS_RULES.md](docs/BUSINESS_RULES.md) | Frozen financial and resolution rules |
| [docs/SECURITY_REGRESSION_REPORT.md](docs/SECURITY_REGRESSION_REPORT.md) | Security checks |
