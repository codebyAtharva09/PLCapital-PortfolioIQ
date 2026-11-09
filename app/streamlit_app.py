"""PortfolioIQ Streamlit application: a reviewer workspace on the same backend as the CLI.

Run from the project root:   streamlit run app/streamlit_app.py

Overview, Exposure Analysis, Evaluation & Evidence, Data & Methodology and CLI & Setup are deterministic and work
without Gemini. Ask PortfolioIQ and SQL Intelligence run live when a Gemini key is configured and the reviewer
chooses Live mode; by default they use a clearly labelled DEMO / FIXTURE MODE that replays recorded Gemini
responses and never sends a request.
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import streamlit as st  # noqa: E402

import backend  # noqa: E402
import ui  # noqa: E402
from views import ask, evaluation, exposure, methodology, overview, setup, sql_intelligence  # noqa: E402

st.set_page_config(page_title="PortfolioIQ", layout="wide", initial_sidebar_state="expanded")
st.logo(str(APP_DIR / "static" / "wordmark.svg"), size="large")
st.sidebar.image(str(APP_DIR / "static" / "pl_capital_logo.png"), width=200)
st.sidebar.caption("Built for the PL Capital AI Engineer assessment")
ui.inject_css()

PAGES = {
    "overview": st.Page(overview.render, title="Overview", url_path="overview", default=True),
    "ask": st.Page(ask.render, title="Ask PortfolioIQ", url_path="ask"),
    "sql": st.Page(sql_intelligence.render, title="SQL Intelligence", url_path="sql"),
    "exposure": st.Page(exposure.render, title="Exposure Analysis", url_path="exposure"),
    "evaluation": st.Page(evaluation.render, title="Evaluation & Evidence", url_path="evaluation"),
    "methodology": st.Page(methodology.render, title="Data & Methodology", url_path="methodology"),
    "setup": st.Page(setup.render, title="CLI & Setup", url_path="setup"),
}
ui.PAGES.update(PAGES)
navigation = st.navigation({
    "Start": [PAGES["overview"]],
    "Ask": [PAGES["ask"], PAGES["sql"]],
    "Explore": [PAGES["exposure"]],
    "Verify": [PAGES["evaluation"], PAGES["methodology"], PAGES["setup"]],
}, position="sidebar")

try:
    settings = backend.settings()
    gemini = "Gemini key configured" if backend.gemini_configured() else "no Gemini key: demo replay only"
    active = backend.active_configuration()
    ui.context_strip(backend.as_of(), backend.fingerprint(), settings.gemini_model, f"SQL configuration {active['variant']} ({active['prompt_version']}) · {gemini}")
except Exception:  # noqa: BLE001 - a broken data folder must not show a stack trace
    ui.callout("error", "Data unavailable", "The supplied data could not be loaded. Check that the data folder is "
               "present and unmodified (python -m portfolioiq integrity).")
    st.stop()

navigation.run()
