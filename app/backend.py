"""The Streamlit app's access to the shared backend (the same modules the CLI uses), with caching.

Nothing here calculates a figure of its own: portfolios, holdings and exposure come from the database
through `portfolioiq` (the exposure engine, the invariant engine, the agent), and evaluation results come
from the recorded report files. Every connection is read-only.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import streamlit as st

from portfolioiq.agent import PortfolioAgent
from portfolioiq.config import PROJECT_ROOT, Settings, load_settings
from portfolioiq.db import TABLE_LOAD_ORDER
from portfolioiq.exposure import ExposureResult, calculate_exposure
from portfolioiq.financial_verification import PortfolioReconciliation, SourceRecords, reconcile_portfolio
from portfolioiq.invariants import IntegrityReport, open_for_checks, run_invariants
from portfolioiq.replay import RecordedLLM
from portfolioiq.sql_engine import data_fingerprint, ensure_reference_db, open_read_only, read_schema
from portfolioiq.sql_generated_guard import validate_generated_sql
from portfolioiq.sql_guard import SqlValidation
from portfolioiq.sql_tool import SqlTool

REPORTS = PROJECT_ROOT / "reports"
PRODUCTION_RUNS = REPORTS / "sprint_v2_3" / "candidate" / "runs"
BASELINE_RUNS = REPORTS / "runs"
DEMO_EXTRA_QUESTIONS = (("BOND", "List each bond with its coupon rate and maturity date."),)  # recorded on the active configuration
SUPPLEMENTAL = "supplemental/gemini-3.5-flash-lite"
REPORT_FILES = {
    "official": "sprint_v2_3/candidate/official_evaluation.json",  # the active production configuration
    "sql": "sprint_v2_3/candidate/sql_evaluation.json",
    "agent": "production_v2_3/agent_integration.json",
    "production_record": "sprint_v2_3/candidate/evaluation_record.json",
    "baseline_sql": "sprint_v2_1/baseline/sql_evaluation.json",
    "benchmark": "reliability_benchmark.json",
    "baseline_official": "sprint_v2_1/baseline/official_evaluation.json",
    "flash_official": f"{SUPPLEMENTAL}/official_evaluation.json",
    "flash_sql": f"{SUPPLEMENTAL}/sql_evaluation.json",
    "flash_agent": f"{SUPPLEMENTAL}/agent_integration.json",
    "flash_stability": f"{SUPPLEMENTAL}/stability.json",
    "independent": "independent_benchmark_dev.json",
    "heldout": "heldout_v2_3/independent_benchmark_test.json",
    "tests": "test_results.json",
}
TABLE_NOTES = {
    "sectors": "Sector names and descriptions",
    "securities": "Stocks and bonds: type, sector, price, country",
    "benchmarks": "Benchmark indices referenced by portfolios",
    "portfolios": "Portfolio name, strategy, risk level, stated AUM, status",
    "holdings": "One position per portfolio and security: quantity, weight, cost basis",
    "transactions": "BUY and SELL records; the history does not fully reconcile with holdings",
    "historical_prices": "Daily price history per security",
    "portfolio_performance": "Daily performance values, portfolios 1 to 4 only",
    "risk_metrics": "Daily risk statistics, portfolios 1 to 4 only",
}
STACK_ROLES = {  # where each dependency is really used; nothing here runs in every workflow
    "google-genai": ("Google Gemini API", "Routing and SQL drafting only (llm.py)"),
    "sqlglot": ("SQLGlot", "Static validation of generated SQL (sql_guard.py)"),
    "pydantic": ("Pydantic", "Validated model output, receipts and tool results"),
    "python-dotenv": ("python-dotenv", "Optional local .env for the API key (config.py)"),
    "httpx": ("HTTPX", "Network-failure handling around Gemini calls (llm.py)"),
    "streamlit": ("Streamlit", "This workspace only; the CLI does not use it"),
    "altair": ("Altair", "Charts in this workspace"),
    "pandas": ("pandas", "Tables and chart data in this workspace"),
    "pytest": ("pytest", "Offline test suite"),
}


@st.cache_resource(show_spinner=False)
def settings() -> Settings:
    return load_settings()


@st.cache_resource(show_spinner=False)
def db_path() -> Path:
    s = settings()
    return ensure_reference_db(s.data_dir, s.schema_path, s.cache_dir)


@st.cache_resource(show_spinner=False)
def fingerprint() -> str:
    s = settings()
    return data_fingerprint(s.data_dir, s.schema_path)


@contextmanager
def connection():
    """A fresh read-only connection per use (SQLite connections are not shared across Streamlit threads)."""
    conn = open_read_only(db_path())
    try:
        yield conn
    finally:
        conn.close()


def active_configuration() -> dict:
    """The SQL configuration this process runs, from the same settings and tool the CLI uses."""
    tool = demo_sql_tool()
    return {"variant": tool.variant, "prompt_version": tool.prompt_version, "context_version": tool.context.version,
            "policy": tool.configuration.shape_policy}


def gemini_configured() -> bool:
    return bool(settings().gemini_api_key)


@st.cache_data(show_spinner=False)
def portfolio_register() -> list[dict]:
    """One row per portfolio: stated fields plus figures computed from holdings (never substituted for AUM)."""
    sql = """
        SELECT p.portfolio_id, p.portfolio_name, p.strategy_type, p.target_risk_level, p.status, p.total_aum,
               p.creation_date, p.benchmark_index, b.benchmark_name,
               COUNT(h.holding_id),
               TOTAL(h.current_weight),
               TOTAL(CASE WHEN s.asset_type = 'Stock' THEN h.current_weight END),
               TOTAL(CASE WHEN s.asset_type = 'Bond' THEN h.current_weight END),
               TOTAL(h.quantity * s.current_price),
               EXISTS (SELECT 1 FROM portfolio_performance pp WHERE pp.portfolio_id = p.portfolio_id),
               EXISTS (SELECT 1 FROM risk_metrics rm WHERE rm.portfolio_id = p.portfolio_id)
        FROM portfolios p
        LEFT JOIN benchmarks b ON b.benchmark_symbol = p.benchmark_index
        LEFT JOIN holdings h ON h.portfolio_id = p.portfolio_id
        LEFT JOIN securities s ON s.security_id = h.security_id
        GROUP BY p.portfolio_id
        ORDER BY p.portfolio_id
    """
    keys = ("portfolio_id", "name", "strategy", "risk", "status", "total_aum", "created", "benchmark_index",
            "benchmark_name", "holdings", "total_weight", "equity_weight", "bond_weight", "holdings_value",
            "has_performance", "has_risk")
    with connection() as conn:
        rows = [dict(zip(keys, r)) for r in conn.execute(sql)]
    for row in rows:
        row["residual"] = 1.0 - row["total_weight"]
        row["aum_coverage"] = row["holdings_value"] / row["total_aum"] if row["total_aum"] else None
        row["has_performance"], row["has_risk"] = bool(row["has_performance"]), bool(row["has_risk"])
    return rows


def portfolio_names() -> list[str]:
    return [r["name"] for r in portfolio_register()]


def portfolio_by_name(name: str) -> dict:
    return next(r for r in portfolio_register() if r["name"] == name)


@st.cache_data(show_spinner=False)
def universe() -> dict:
    with connection() as conn:
        counts = dict(conn.execute("SELECT asset_type, COUNT(*) FROM securities GROUP BY asset_type").fetchall())
        return {
            "portfolios": conn.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0],
            "securities": sum(counts.values()), "stocks": counts.get("Stock", 0), "bonds": counts.get("Bond", 0),
            "holdings": conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0],
            "sectors": conn.execute("SELECT COUNT(*) FROM sectors").fetchone()[0],
            "populated_sectors": conn.execute(
                "SELECT COUNT(DISTINCT sector_id) FROM securities WHERE sector_id IS NOT NULL").fetchone()[0],
            "transaction_portfolios": conn.execute(
                "SELECT COUNT(DISTINCT portfolio_id) FROM transactions").fetchone()[0],
        }


@st.cache_data(show_spinner=False)
def table_counts() -> list[dict]:
    """Row counts read from the database, one per supplied table (names come from TABLE_LOAD_ORDER, not from input)."""
    with connection() as conn:
        return [{"table": t, "rows": conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0],
                 "note": TABLE_NOTES.get(t, "")} for t in TABLE_LOAD_ORDER]


@st.cache_data(show_spinner=False)
def holdings(portfolio_id: int) -> list[dict]:
    sql = """
        SELECT s.symbol, s.company_name, s.asset_type, sec.sector_name, h.current_weight, h.quantity,
               s.current_price, h.quantity * s.current_price,
               h.quantity * s.current_price / SUM(h.quantity * s.current_price) OVER (),
               h.purchase_price, h.cost_basis, s.maturity_date, s.coupon_rate
        FROM holdings h
        JOIN securities s ON s.security_id = h.security_id
        LEFT JOIN sectors sec ON sec.sector_id = s.sector_id
        WHERE h.portfolio_id = ?
        ORDER BY h.current_weight DESC, s.symbol
    """
    keys = ("symbol", "name", "asset_type", "sector", "weight", "quantity", "current_price", "market_value",
            "market_value_weight", "purchase_price", "cost_basis", "maturity_date", "coupon_rate")
    with connection() as conn:
        return [dict(zip(keys, r)) for r in conn.execute(sql, (portfolio_id,))]


@st.cache_data(show_spinner=False)
def exposure(portfolio_id: int) -> ExposureResult:
    with connection() as conn:
        return calculate_exposure(conn, portfolio_id)


@st.cache_resource(show_spinner=False)
def _source_records() -> SourceRecords:
    with connection() as conn:
        return SourceRecords.load(conn)


def reconciliation(portfolio_id: int) -> PortfolioReconciliation:
    return reconcile_portfolio(_source_records(), portfolio_id)


@st.cache_data(show_spinner=False)
def integrity() -> IntegrityReport:
    s = settings()
    conn = open_for_checks(db_path())
    try:
        return run_invariants(conn, s.data_dir)
    finally:
        conn.close()


@st.cache_data(show_spinner=False, ttl=60)
def report(name: str) -> dict | None:
    """A recorded evaluation report, or None when it has not been generated yet."""
    path = REPORTS / REPORT_FILES[name]
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


@st.cache_data(show_spinner=False, ttl=60)
def api_usage() -> dict | None:
    from portfolioiq_eval import usage  # evaluation tooling, used only to read the ledger

    ledger = settings().api_usage_log
    return usage.summarise(ledger) if ledger and ledger.exists() else None


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


@st.cache_data(show_spinner=False, ttl=60)
def candidate_runs() -> list[dict]:
    """Every recorded candidate evaluation, newest sprint first. Each stays in its own folder, apart from the baseline."""
    found = []
    for folder in sorted(REPORTS.glob("sprint_*/candidate"), reverse=True):
        official, sql = _load(folder / "official_evaluation.json"), _load(folder / "sql_evaluation.json")
        if official is None and sql is None:
            continue
        metas = [m for m in (_load(p) for p in sorted((folder / "runs").glob("*/run_meta.json"))) if m]
        found.append({"sprint": folder.parent.name, "official": official, "sql": sql, "meta": metas[-1] if metas else {}, "path": folder.relative_to(PROJECT_ROOT).as_posix()})
    return found


@st.cache_data(show_spinner=False, ttl=60)
def diagnostic_comparisons() -> list[dict]:
    """Supplemental shape diagnostics (recorded, not official scores), newest sprint first."""
    rows = []
    for path in sorted(REPORTS.glob("sprint_*/supplemental/*/supplemental_comparison.json"), reverse=True):
        data = _load(path)
        if data:
            rows.append({"sprint": path.parents[2].name, "label": data.get("label") or path.parent.name, "exact_shape": data.get("exact_shape"),
                         "semantic": data.get("semantic"), "evaluated": data.get("evaluated"), "note": data.get("note")})
    return rows


@st.cache_data(show_spinner=False, ttl=60)
def run_history(root: Path = BASELINE_RUNS) -> list[dict]:
    """One row per recorded live run in one folder, oldest first. Runs are never overwritten."""
    rows = []
    for meta_path in sorted(root.glob("*/run_meta.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows.append({"run": meta_path.parent.name, "started": meta.get("started_utc") or meta.get("started"),
                     "model": meta.get("model"), "requests": meta.get("requests_sent"),
                     "prompt": meta.get("sql_prompt_version") or meta.get("prompt_version"),
                     "stopped": (meta.get("stopped") or {}).get("kind")})
    return rows


@st.cache_data(show_spinner=False, ttl=60)
def run_receipts(run: str) -> dict[str, dict]:
    """The receipts of one recorded official run, by case id."""
    path = next((root / run / "official_outputs.jsonl" for root in (PRODUCTION_RUNS, BASELINE_RUNS)
                 if (root / run / "official_outputs.jsonl").exists()), BASELINE_RUNS / run / "official_outputs.jsonl")
    found: dict[str, dict] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                found[record["case"]["id"]] = record["receipt"]
    except (OSError, json.JSONDecodeError, KeyError):
        return {}
    return found


@st.cache_data(show_spinner=False, ttl=60)
def frozen_baseline() -> dict | None:
    """Versions frozen for the production baseline, and whether the protected files still match them."""
    path = PROJECT_ROOT / "evaluation" / "baseline_configuration.json"
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    hashes = snapshot.get("file_sha256", {})
    security = (_load(PROJECT_ROOT / "evaluation" / "security_manifest.json") or {}).get("files", {})
    changed, hardened = [], []
    for name, expected in hashes.items():
        try:
            text = (PROJECT_ROOT / name).read_text(encoding="utf-8").replace("\r\n", "\n")
        except OSError:
            changed.append(name)
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != expected:
            (hardened if security.get(name, {}).get("sha256") == digest and security[name].get("baseline_sha256") == expected else changed).append(name)
    return {"model": snapshot.get("model"), "sdk": snapshot.get("sdk"), "versions": snapshot.get("versions", {}),
            "comparator": hashes.get("src/portfolioiq_eval/comparator.py", ""), "files": len(hashes), "changed": changed, "hardened": hardened}


def rescore_official() -> dict:
    """Score the recorded official run again, in memory: no API call and no report file is written."""
    from portfolioiq_eval import official, sql_eval

    runs = PRODUCTION_RUNS
    return official.score(official.recorded_outputs(runs), official.load_cases(), settings(),
                          sql_outputs=sql_eval.recorded_outputs(runs))["summary"]


@st.cache_resource(show_spinner=False)
def demo_agent() -> PortfolioAgent:
    """DEMO / FIXTURE MODE: Gemini output replayed from recorded responses; never sends a request."""
    s = settings()
    return PortfolioAgent.from_settings(s, llm=RecordedLLM(s))


@st.cache_resource(show_spinner=False)
def live_agent() -> PortfolioAgent:
    """The live agent: each question spends real Gemini requests under the client's retry and budget rules."""
    return PortfolioAgent.from_settings(settings())


@st.cache_resource(show_spinner=False)
def demo_sql_tool() -> SqlTool:
    """DEMO / FIXTURE MODE for the SQL tool alone: the production tool with recorded Gemini drafts."""
    s = settings()
    return SqlTool.from_settings(s, llm=RecordedLLM(s))


@st.cache_resource(show_spinner=False)
def live_sql_tool() -> SqlTool:
    return SqlTool.from_settings(settings())


def _ledger_stamp() -> tuple[int, int]:
    """Changes when a live run appends to the ledger, so replay coverage is recomputed only then."""
    ledger = settings().api_usage_log
    try:
        stat = ledger.stat()
        return stat.st_mtime_ns, stat.st_size
    except (OSError, AttributeError):
        return 0, 0


def recorded_questions() -> list[dict]:
    """Questions demo mode can really answer through the agent, proved by replaying each one (no request sent)."""
    try:
        return _recorded_questions(_ledger_stamp())
    except Exception:  # noqa: BLE001 - no examples is a usable page; a stack trace is not
        return []


@st.cache_data(show_spinner=False)
def _recorded_questions(_stamp: tuple[int, int]) -> list[dict]:
    candidates: list[tuple[str, str]] = []
    for name in ("official", "agent"):
        data = report(name)
        candidates += [(i["id"], i["question"]) for i in (data or {}).get("items", []) if "question" in i]
    candidates += list(DEMO_EXTRA_QUESTIONS)
    agent, seen, found = demo_agent(), set(), []
    for case_id, question in candidates:
        if question in seen:
            continue
        seen.add(question)
        receipt = agent.ask(question)
        if receipt.error_kind != "not_recorded" and receipt.model.replayed_responses:
            found.append({"id": case_id, "question": question, "route": receipt.route})
    return found


def recorded_sql_questions() -> list[dict]:
    """Questions the SQL tool can answer from recorded drafts, proved by replaying each one."""
    try:
        return _recorded_sql_questions(_ledger_stamp())
    except Exception:  # noqa: BLE001 - no examples is a usable page; a stack trace is not
        return []


@st.cache_data(show_spinner=False)
def _recorded_sql_questions(_stamp: tuple[int, int]) -> list[dict]:
    tool, seen, found = demo_sql_tool(), set(), []
    for name in ("official", "agent", "sql"):
        for item in (report(name) or {}).get("items", []):
            question = item.get("question")
            if not question or question in seen:
                continue
            seen.add(question)
            result = tool.answer(question)
            if result.replayed:
                found.append({"id": item["id"], "question": question, "status": result.status})
    return found


@st.cache_resource(show_spinner=False)
def _schema() -> dict[str, dict[str, str]]:
    return read_schema(db_path())


def validate_only(sql: str) -> SqlValidation:
    """The production SQL guard on one statement. Nothing is executed."""
    return validate_generated_sql(sql, _schema())


@st.cache_data(show_spinner=False)
def tech_stack() -> list[dict]:
    """The pinned dependencies in requirements.txt with what each is used for, plus the runtime."""
    pins = {}
    for line in (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        if "==" in line and not line.startswith("#"):
            name, version = line.split("==", 1)
            pins[name.strip().lower()] = version.strip()
    rows = [{"name": "Python", "version": platform.python_version(), "role": "Runtime"},
            {"name": "SQLite", "version": sqlite3.sqlite_version, "role": "Read-only portfolio database"}]
    rows += [{"name": label, "version": pins[key], "role": role} for key, (label, role) in STACK_ROLES.items() if key in pins]
    return rows


@st.cache_data(show_spinner=False)
def cli_transcript(arguments: tuple[str, ...]) -> dict:
    """Run the real CLI once, offline, and keep its output: no key is passed, so it cannot reach Gemini."""
    env = {**os.environ, "GEMINI_API_KEY": "", "PYTHONIOENCODING": "utf-8"}
    shown = "python -m portfolioiq " + " ".join(f'"{a}"' if " " in a else a for a in arguments)
    try:
        done = subprocess.run([sys.executable, "-m", "portfolioiq", *arguments], cwd=PROJECT_ROOT, env=env, text=True,
                              capture_output=True, timeout=90, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        return {"command": shown, "output": None, "exit_code": None, "error": type(exc).__name__}
    return {"command": shown, "output": done.stdout, "exit_code": done.returncode, "error": None}


def as_of() -> str | None:
    return integrity().as_of


def sqlite_version() -> str:
    return sqlite3.sqlite_version
