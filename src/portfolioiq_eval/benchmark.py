"""Reliability benchmark (M5): the 19 scenarios in evaluation/reliability_scenarios.json.

Each scenario is reported on two separate columns, never blended into one score:

- **Deterministic component**: the code path the scenario depends on, checked with no Gemini at all
  (resolver, exposure engine, input validation, SR1, the SQL guard and the database authorizer, and
  simulated API failures through fake clients). These run offline, now.
- **Live end to end**: the recorded live result of the same question (M3 SQL tool or M4 agent runs),
  if one exists. Otherwise NOT EVALUATED, pending live API access. Nothing is estimated.

Gemini refusing an attack is never taken as proof of safety: R16 and R17 check the SQL guard and the
read-only database layer directly, with the guard bypassed for the second layer.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
from google.genai import errors

from portfolioiq.agent import PortfolioAgent
from portfolioiq.config import PROJECT_ROOT, Settings
from portfolioiq.exposure import calculate_exposure
from portfolioiq.llm import GeminiLLM
from portfolioiq.resolver import Catalog, resolve_portfolio
from portfolioiq.router import RouterDecision
from portfolioiq.semantic import SemanticContext
from portfolioiq.sql_engine import ensure_reference_db, execute_sql, open_read_only, read_schema
from portfolioiq.sql_guard import validate_sql
from portfolioiq_eval import agent_eval, sql_eval
from portfolioiq_eval.sql_eval import md_cell

SCENARIOS = PROJECT_ROOT / "evaluation" / "reliability_scenarios.json"
REPORTS = PROJECT_ROOT / "reports"
TOLERANCE = 1e-9
PENDING = "PENDING LIVE API ACCESS: no recorded live run of this scenario."
SIMULATED_KEY = "simulated-key-not-a-real-credential"


def load_scenarios(path: Path = SCENARIOS) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["scenarios"]


def run(settings: Settings, scenarios: list[dict] | None = None) -> dict:
    """Run every deterministic component and attach recorded live results. Makes no API calls."""
    scenarios = scenarios or load_scenarios()
    db_path = ensure_reference_db(settings.data_dir, settings.schema_path, settings.cache_dir)
    before = _sha256(db_path)
    live = _live_results(settings)
    conn = open_read_only(db_path)
    try:
        context = _Context(settings, db_path, conn, Catalog.from_db(conn))
        items = []
        for scenario in scenarios:
            component = _component(scenario.get("component"), context)
            items.append({"id": scenario["id"], "category": scenario["category"], "scenario": scenario["scenario"],
                          "expected": scenario["expected"], "component": component,
                          "live": _live(scenario.get("live") or {}, live)})
    finally:
        conn.close()
    after = _sha256(db_path)
    return {"summary": _summary(items, before == after), "items": items,
            "database": {"sha256_before": before, "sha256_after": after, "unchanged": before == after}}


@dataclasses.dataclass
class _Context:
    settings: Settings
    db_path: Path
    conn: object
    catalog: Catalog


# --- deterministic components --------------------------------------------------------------------

def _component(spec: dict | None, ctx: _Context) -> dict:
    if not spec:
        return {"verdict": "N/A", "explanation": "No deterministic component: this behaviour depends on live Gemini "
                                                 "output only."}
    handler = {"hybrid_leader": _hybrid_leader, "largest_aum_residual": _largest_aum, "exposure_figures": _figures,
               "no_equities": _no_equities, "resolver": _resolver, "reference_rows": _reference_rows,
               "no_performance_data": _no_performance, "input_validation": _input_validation, "sr1": _sr1,
               "sql_blocked": _sql_blocked, "simulated_api_failures": _simulated_failures}[spec["kind"]]
    problems, evidence = handler(spec, ctx)
    verdict = "FAIL" if problems else "PASS"
    return {"verdict": verdict, "kind": spec["kind"], "explanation": "; ".join(problems) if problems else evidence}


def _portfolio_id(ctx: _Context, name: str) -> int:
    return ctx.catalog.by_name(name).portfolio_id


def _hybrid_leader(spec: dict, ctx: _Context) -> tuple[list[str], str]:
    rows = execute_sql(ctx.db_path, spec["condition_sql"]).rows
    names = [r[0] for r in rows]
    results = [calculate_exposure(ctx.conn, _portfolio_id(ctx, n)) for n in names]
    share = {r.portfolio_name: next((s.method_a for s in r.sectors if s.sector == spec["sector"]), 0.0) for r in results}
    best = max(share.values()) if share else None
    leaders = [n for n, v in share.items() if best is not None and abs(v - best) <= TOLERANCE]
    problems = []
    if len(names) != spec["count"]:
        problems.append(f"{len(names)} portfolios match the condition, expected {spec['count']}")
    if leaders != [spec["leader"]]:
        problems.append(f"leader {leaders}, expected {spec['leader']}")
    return problems, (f"{len(names)} portfolios match; {spec['sector']} Method A: "
                      + ", ".join(f"{n} {v * 100:.2f}%" for n, v in sorted(share.items(), key=lambda x: -x[1]))
                      + f"; leader {spec['leader']}.")


def _largest_aum(spec: dict, ctx: _Context) -> tuple[list[str], str]:
    largest = execute_sql(ctx.db_path, "SELECT portfolio_name FROM portfolios ORDER BY total_aum DESC LIMIT 1").rows[0][0]
    result = calculate_exposure(ctx.conn, _portfolio_id(ctx, largest))
    problems = []
    if largest != spec["portfolio"]:
        problems.append(f"largest by AUM is {largest}, expected {spec['portfolio']}")
    if not (result.residual_reported and abs(result.residual - spec["residual"]) <= TOLERANCE):
        problems.append(f"residual {result.residual}, expected a reported line of {spec['residual']}")
    if abs(result.eligible_stock_weight - spec["equity"]) > TOLERANCE:
        problems.append(f"Method A sectors total {result.eligible_stock_weight}, expected {spec['equity']}")
    return problems, (f"{largest}: Method A sectors total {result.eligible_stock_weight * 100:.2f}% with an "
                      f"unattributed residual line of {result.residual * 100:.2f}%; {result.verification.value}.")


def _figures(spec: dict, ctx: _Context) -> tuple[list[str], str]:
    result = calculate_exposure(ctx.conn, _portfolio_id(ctx, spec["portfolio"]))
    got_a = {s.sector: s.method_a for s in result.sectors}
    got_b = {s.sector: s.method_b for s in result.sectors}
    bonds = next((e.weight for e in result.excluded if e.label.startswith("Bonds")), 0.0)
    problems = []
    for label, got, want in (("Method A", got_a, spec["method_a"]), ("Method B", got_b, spec["method_b"])):
        if set(got) != set(want) or any(abs(got[k] - v) > 1e-9 for k, v in want.items()):
            problems.append(f"{label} {got}, expected {want}")
    if abs(bonds - spec["bonds"]) > TOLERANCE:
        problems.append(f"excluded bonds {bonds}, expected {spec['bonds']}")
    return problems, (f"{spec['portfolio']}: Method A " + ", ".join(f"{k} {v * 100:.2f}%" for k, v in got_a.items())
                      + f"; bonds excluded {bonds * 100:.2f}%; Method B "
                      + ", ".join(f"{k} {v * 100:.2f}%" for k, v in got_b.items()) + f"; {result.verification.value}.")


def _no_equities(spec: dict, ctx: _Context) -> tuple[list[str], str]:
    result = calculate_exposure(ctx.conn, _portfolio_id(ctx, spec["portfolio"]))
    problems = [] if result.status == "no_eligible_stocks" and not result.sectors else [
        f"status {result.status} with {len(result.sectors)} sector(s)"]
    return problems, f"{result.message}"


def _resolver(spec: dict, ctx: _Context) -> tuple[list[str], str]:
    resolution = resolve_portfolio(ctx.catalog, spec["phrase"])
    names = [c.name for c in resolution.candidates]
    problems = []
    if resolution.status != spec["status"]:
        problems.append(f"status {resolution.status}, expected {spec['status']}")
    if "candidates" in spec and sorted(names) != sorted(spec["candidates"]):
        problems.append(f"candidates {names}, expected {spec['candidates']}")
    if "portfolio" in spec and resolution.portfolio_name != spec["portfolio"]:
        problems.append(f"resolved to {resolution.portfolio_name}, expected {spec['portfolio']}")
    if "suggestion" in spec and (spec["suggestion"] not in names or resolution.portfolio_id is not None):
        problems.append(f"suggestions {names}; expected {spec['suggestion']} offered, not applied")
    if spec["status"] == "not_found" and "suggestion" not in spec and resolution.portfolio_id is not None:
        problems.append("a portfolio was selected for an unknown name")
    shown = f"; candidates: {', '.join(names)}" if names else ""
    return problems, f"'{spec['phrase']}' -> {resolution.status} (rule {resolution.rule}){shown}."


def _reference_rows(spec: dict, ctx: _Context) -> tuple[list[str], str]:
    execution = execute_sql(ctx.db_path, spec["sql"])
    problems = []
    if "row_count" in spec and execution.row_count != spec["row_count"]:
        problems.append(f"{execution.row_count} rows, expected {spec['row_count']}")
    if "rows" in spec and execution.rows != spec["rows"]:
        problems.append(f"rows {execution.rows}, expected {spec['rows']}")
    return problems, f"The expected answer holds in the data: {execution.row_count} row(s) {execution.rows[:5]}."


def _no_performance(spec: dict, ctx: _Context) -> tuple[list[str], str]:
    pid = _portfolio_id(ctx, spec["portfolio"])
    rows = execute_sql(ctx.db_path, f"SELECT COUNT(*) FROM portfolio_performance WHERE portfolio_id = {int(pid)}").rows[0][0]
    covered = SemanticContext.from_db(ctx.db_path).covered_portfolios
    problems = []
    if rows:
        problems.append(f"{rows} performance rows exist for {spec['portfolio']}")
    if pid in covered:
        problems.append("the SQL context claims performance coverage for this portfolio")
    return problems, (f"0 performance rows for {spec['portfolio']} (portfolio_id {pid}); the SQL tool's context "
                      f"declares coverage for portfolio_id {', '.join(map(str, covered))} only, so the answer is "
                      "'no data', never an estimate.")


class _NoCallLLM:
    model = "no-call"

    def __init__(self):
        self.calls = 0

    def generate(self, **kwargs):
        self.calls += 1
        raise AssertionError("input validation must stop malformed input before any model call")


def _input_validation(spec: dict, ctx: _Context) -> tuple[list[str], str]:
    llm = _NoCallLLM()
    agent = PortfolioAgent(ctx.db_path, SemanticContext.from_db(ctx.db_path), llm)
    inputs = {"empty": "", "whitespace": "   \t ", "10,000 characters": "x" * 10_000,
              "control characters": "Sharpe ratio\x00\x07 of the Growth Equity Fund"}
    problems = []
    for label, text in inputs.items():
        receipt = agent.ask(text)
        if receipt.status != "invalid_input":
            problems.append(f"{label}: status {receipt.status}")
    if llm.calls:
        problems.append(f"the model was called {llm.calls} time(s)")
    return problems, (f"All {len(inputs)} malformed inputs ({', '.join(inputs)}) returned invalid_input with a "
                      "validation message; the model was never called.")


def _sr1(spec: dict, ctx: _Context) -> tuple[list[str], str]:
    before = _sha256(ctx.db_path)
    resolution = resolve_portfolio(ctx.catalog, spec["phrase"])
    problems = [] if resolution.status == "rejected" and resolution.portfolio_id is None else [
        f"status {resolution.status}"]
    if _sha256(ctx.db_path) != before:
        problems.append("the database changed")
    return problems, f"Rejected before any name matching (rule {resolution.rule}): {resolution.message} Database unchanged."


def _sql_blocked(spec: dict, ctx: _Context) -> tuple[list[str], str]:
    before = _sha256(ctx.db_path)
    validation = validate_sql(spec["sql"], read_schema(ctx.db_path))
    execution = execute_sql(ctx.db_path, spec["sql"])  # layer 2 alone: the guard is bypassed on purpose
    problems = []
    if validation.ok:
        problems.append("the SQL guard accepted it")
    if execution.status == "ok":
        problems.append("the read-only database layer executed it")
    if _sha256(ctx.db_path) != before:
        problems.append("the database changed")
    return problems, (f"Layer 1, SQL guard: rejected ({validation.category}: {validation.reason}). Layer 2, read-only "
                      f"database with the guard bypassed: refused ({execution.error}). Database SHA-256 unchanged. "
                      "Neither layer depends on Gemini refusing.")


def _simulated_failures(spec: dict, ctx: _Context) -> tuple[list[str], str]:
    decline = RouterDecision(action="decline", portfolio="", sql_question="", sector="", clarification_question="",
                             options=[], decline_kind="out_of_scope", reason="Simulated routing decision.")
    daily = ("You exceeded your current quota. Quota exceeded for metric: generate_content_free_tier_requests, "
             "description: Generate content requests per day limit exceeded on Free Tier. Please retry in 15h8m31s.")
    cases = {
        "invalid key": ([errors.ClientError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                                            "message": "API key not valid."}})], "error", "api_error", 1),
        "timeout": ([httpx.ReadTimeout("simulated"), httpx.ReadTimeout("simulated")], "error", "network", 2),
        "daily 429": ([errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                                          "message": daily}})], "error", "quota_exhausted", 1),
        "503 then success": ([errors.ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE",
                                                                 "message": "high demand"}}),
                              _response(decline)], "declined", None, 2),
        "malformed output": ([SimpleNamespace(parsed=None, text="not json", usage_metadata=None, model_version=None,
                                              prompt_feedback=None)], "error", "malformed_output", 1),
    }
    settings = dataclasses.replace(ctx.settings, gemini_api_key=SIMULATED_KEY, llm_cache_dir=None, api_usage_log=None)
    context = SemanticContext.from_db(ctx.db_path)
    problems, seen = [], []
    for label, (outcomes, status, kind, requests) in cases.items():
        llm = GeminiLLM(settings, client=_FakeClient(outcomes), sleep=lambda s: None)
        receipt = PortfolioAgent(ctx.db_path, context, llm).ask("Simulated question for the failure test")
        if (receipt.status, receipt.error_kind, receipt.model.api_requests) != (status, kind, requests):
            problems.append(f"{label}: {receipt.status}/{receipt.error_kind}/{receipt.model.api_requests} requests, "
                            f"expected {status}/{kind}/{requests}")
        if SIMULATED_KEY in receipt.model_dump_json():
            problems.append(f"{label}: the key appears in the receipt")
        seen.append(f"{label} -> {receipt.status}" + (f" ({receipt.error_kind})" if receipt.error_kind else "")
                    + f", {receipt.model.api_requests} request(s)")
    return problems, "SIMULATED with fake clients, no network: " + "; ".join(seen) + ". No crash; the key never appears."


class _FakeClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kwargs):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _response(decision: RouterDecision):
    return SimpleNamespace(parsed=decision, text=decision.model_dump_json(), model_version="simulated",
                           usage_metadata=SimpleNamespace(prompt_token_count=0, candidates_token_count=0,
                                                          thoughts_token_count=0, total_token_count=0),
                           prompt_feedback=None)


# --- recorded live results ------------------------------------------------------------------------

def _live_results(settings: Settings) -> dict:
    agent_items = {i["id"]: i for i in agent_eval.score(agent_eval.recorded_outputs(), agent_eval.load_cases())["items"]}
    sql_items = {i["id"]: i for i in sql_eval.score(sql_eval.recorded_outputs(), settings,
                                                    cases=sql_eval.load_cases())["items"]}
    return {"agent": agent_items, "sql": sql_items}


def _live(spec: dict, live: dict) -> dict:
    if spec.get("not_applicable"):
        return {"verdict": "N/A", "explanation": spec["not_applicable"], "sources": []}
    found = []
    if "agent_case" in spec:
        item = live["agent"].get(spec["agent_case"])
        if item:
            found.append(("agent", spec["agent_case"], item))
    if "sql_case" in spec:
        item = live["sql"].get(spec["sql_case"])
        if item:
            found.append(("SQL tool", spec["sql_case"], item))
    evaluated = [(layer, case, item) for layer, case, item in found if item["verdict"] != "NOT EVALUATED"]
    if not evaluated:
        attempted = [(case, item) for _, case, item in found if item.get("run")]
        pending = (f" The recorded attempt ({attempted[0][0]}, run {attempted[0][1]['run']}) hit an API error: "
                   f"{attempted[0][1].get('api_error') or 'see its report'}.") if attempted else ""
        return {"verdict": "NOT EVALUATED", "explanation": PENDING + pending, "sources": [c for _, c, _ in found]}
    verdicts = {item["verdict"] for _, _, item in evaluated}
    verdict = "FAIL" if "FAIL" in verdicts else ("REFUSED" if verdicts == {"REFUSED"} else "PASS")
    explanation = "; ".join(f"{layer} {case} ({item.get('run') or '-'}): {item['verdict']}" for layer, case, item in evaluated)
    return {"verdict": verdict, "explanation": explanation, "sources": [c for _, c, _ in evaluated]}


def _summary(items: list[dict], unchanged: bool) -> dict:
    def tally(key: str, values=("PASS", "FAIL", "REFUSED", "NOT EVALUATED", "N/A")) -> dict:
        return {v: sum(1 for i in items if i[key]["verdict"] == v) for v in values}

    return {"scenarios": len(items), "component": tally("component"), "live": tally("live"),
            "database_unchanged": unchanged}


def write_reports(report: dict, directory: Path = REPORTS, name: str = "reliability_benchmark") -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path, md_path = directory / f"{name}.json", directory / f"{name}.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    s = report["summary"]

    def counts(t: dict) -> str:
        return ", ".join(f"{k} {v}" for k, v in t.items() if v) or "none"

    lines = [
        "# Reliability benchmark (19 scenarios)", "",
        "Two columns, never blended. **Deterministic component**: the code path the scenario depends on, checked now "
        "with no Gemini. **Live end to end**: the recorded live result of the same question, or NOT EVALUATED while "
        "live API access is pending. Simulated API failures use fake clients and are labelled as simulated.", "",
        f"- Deterministic components: {counts(s['component'])} (N/A: the behaviour depends on live Gemini only)",
        f"- Live end to end: {counts(s['live'])} (N/A: a live run would add nothing, or failures are simulated "
        "by design)",
        f"- Database unchanged across the benchmark (SHA-256): {s['database_unchanged']}", "",
        "| ID | Category | Scenario | Expected | Deterministic component | Live end to end |",
        "|---|---|---|---|---|---|",
    ]
    for i in report["items"]:
        lines.append(f"| {i['id']} | {md_cell(i['category'])} | {md_cell(i['scenario'])} | {md_cell(i['expected'])} | "
                     f"{i['component']['verdict']} | {i['live']['verdict']} |")
    lines += ["", "## Evidence", ""]
    for i in report["items"]:
        lines.append(f"- **{i['id']}** component: {md_cell(i['component']['explanation'])}")
        lines.append(f"  live: {md_cell(i['live']['explanation'])}")
    lines += ["", "Regenerate with `python -m portfolioiq_eval benchmark` (no API calls)."]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
