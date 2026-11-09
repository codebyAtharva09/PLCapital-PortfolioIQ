"""CLI & Setup: how to install and run PortfolioIQ, with real CLI output and the shared architecture."""

from __future__ import annotations

import streamlit as st

import backend
import ui

INSTALL = """python -m venv .venv
.venv\\Scripts\\Activate.ps1                 # macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run app/streamlit_app.py          # opens http://localhost:8501"""
CLI = """python -m portfolioiq ask "Which high-risk portfolio has the largest Technology exposure?"   # live: needs a key
python -m portfolioiq ask --replay "How many holdings does the Dividend Aristocrats Fund have?"  # offline replay
python -m portfolioiq sql "Which portfolios hold NVIDIA?"                                       # live: SQL tool only
python -m portfolioiq exposure "Balanced Portfolio"                                             # deterministic, no Gemini
python -m portfolioiq integrity                                                                 # deterministic data checks"""
EVALUATOR = """python -m portfolioiq_eval official --rescore    # offline: score recorded runs, no API call
python -m portfolioiq_eval benchmark            # offline: 19 reliability scenarios
python -m portfolioiq_eval official --max-requests 18   # live: spends Gemini quota"""
TESTS = """python -m pytest                      # offline, no key needed
python scripts/record_test_results.py # also saves the summary shown on Evaluation & Evidence"""
ENV_VARS = [
    ["GEMINI_API_KEY", "Only for live questions", "Gemini API key, read from the environment or a git-ignored .env"],
    ["GEMINI_MODEL", "Optional", "Default gemini-3.6-flash. There is no silent fallback to another model"],
    ["GEMINI_THINKING_LEVEL", "Optional", "Default LOW"],
    ["PORTFOLIO_DATA_DIR", "Optional", "Default data/"],
    ["PORTFOLIOIQ_CACHE_DIR", "Optional", "Default .cache/ (built database and saved responses)"],
    ["PORTFOLIOIQ_LLM_CACHE", "Optional", "Set to off to disable saved responses"],
    ["PORTFOLIOIQ_USAGE_LOG", "Optional", "Default reports/api_usage.jsonl"],
    ["PORTFOLIOIQ_LLM_MIN_INTERVAL", "Optional", "Seconds between requests"],
    ["PORTFOLIOIQ_SQL_PROMPT", "Optional", "Default candidate-2.3 (active production). Set to baseline to roll back to the original configuration"],
]
EXAMPLES = (("Deterministic exposure", ("exposure", "Balanced Portfolio")),
            ("Agent, offline replay", ("ask", "--replay", "How many holdings does the Dividend Aristocrats Fund have?")))


def render() -> None:
    ui.page_header("CLI & Setup", "The CLI is a primary interface: it runs the same agent and tools as this workspace.")
    ui.head("Quick start", "Tested on Python 3.13, Windows 11. Requires Python 3.11 or later (pyproject.toml). Install time depends on your network: docs/REVIEWER_GUIDE.md records a measured run")
    st.code(INSTALL, language="powershell")
    ui.footnote("The database is built automatically from data/ on first use, read-only afterwards; no setup step is needed")

    ui.head("Launch the CLI")
    st.code(CLI, language="bash", wrap_lines=True)
    ui.footnote("Exit codes: 0 answer, 2 clarification or out of scope, 3 rejected, 4 Gemini unavailable, 5 no valid result")

    ui.head("Real output", "Produced by running the CLI now, with no key passed to it. Nothing below is typed in")
    for label, arguments in EXAMPLES:
        ui.section_label(label)
        with st.spinner("Running the CLI…"):
            run = backend.cli_transcript(arguments)
        st.code(f"> {run['command']}", language="bash")
        if run["error"]:
            ui.callout("error", "The CLI could not be run here", f"{run['error']}. Run the command in a terminal instead.")
        else:
            st.code(run["output"].rstrip() or "(no output)", language=None, wrap_lines=True)
            ui.footnote(f"Exit code {run['exit_code']}")

    ui.head("Evaluator")
    st.code(EVALUATOR, language="bash", wrap_lines=True)
    ui.head("Tests")
    st.code(TESTS, language="bash")

    ui.head("One backend, two interfaces", "The CLI and this workspace call the same modules; neither has its own business logic")
    ui.flow([("CLI or Streamlit", "src/portfolioiq/cli.py or app/"), ("PortfolioAgent", "agent.py"), ("SQL tool or exposure engine", "sql_tool.py, sql_guard.py, sql_engine.py, exposure.py"),
             ("Financial Answer Receipt", "receipt.py")])
    ui.table(["Capability", "CLI", "Streamlit page", "Shared module"], [
        ["Portfolio agent", "ask", "Ask PortfolioIQ", "portfolioiq.agent"],
        ["SQL Query Tool", "sql", "SQL Intelligence", "portfolioiq.sql_tool"],
        ["Exposure calculator", "exposure", "Exposure Analysis", "portfolioiq.exposure"],
        ["Data checks", "integrity", "Data & Methodology", "portfolioiq.invariants"],
        ["Evaluator", "python -m portfolioiq_eval", "Evaluation & Evidence (recorded results)", "portfolioiq_eval"],
    ])

    ui.head("Environment variables", "Names only. Real values are never shown or stored in the repository")
    ui.table(["Variable", "Needed", "Purpose"], ENV_VARS, widths=["32%", "16%", "52%"])
