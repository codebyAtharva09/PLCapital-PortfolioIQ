"""Official evaluation of the Portfolio Agent on ground_truth_dataset.json (Q1 to Q10), M5.

Each question is reported on separate evidence layers, never blended into one score:

1. **Agent, end to end** (live Gemini): the route Gemini chose and the tool result, compared with the
   reference. Receipts are recorded in ``reports/runs/official-*/official_outputs.jsonl`` the moment
   they arrive, so scoring never needs an API call.
2. **SQL tool, direct** (Q1-Q8): the recorded live M3 results, scored by executing both queries
   (strict comparison, rule B13).
3. **Deterministic calculation** (Q9, Q10): the exposure engine run for the ground truth's portfolio
   and compared with the hand-derived fixtures; Q10's phrase is resolved by rule B5. No Gemini is
   involved, so this layer runs offline.

A question with no recorded live result is NOT EVALUATED (pending live API access). It is never
counted as wrong, and no layer's result is presented as another's.
"""

from __future__ import annotations

import dataclasses
import json
import math
import sqlite3
from pathlib import Path

from portfolioiq.agent import PortfolioAgent
from portfolioiq.config import PROJECT_ROOT, Settings
from portfolioiq.exposure import calculate_exposure
from portfolioiq.receipt import FinancialAnswerReceipt
from portfolioiq.resolver import Catalog, resolve_portfolio
from portfolioiq.sql_engine import ensure_reference_db, execute_sql, open_read_only
from portfolioiq_eval import agent_eval, sql_eval
from portfolioiq_eval.agent_eval import AgentCase, is_api_failure
from portfolioiq_eval.comparator import compare_results, requires_order
from portfolioiq_eval.sql_eval import md_cell

GROUND_TRUTH = PROJECT_ROOT / "evaluation" / "ground_truth_dataset.json"
FIXTURES = PROJECT_ROOT / "evaluation" / "fixtures"
REPORTS = PROJECT_ROOT / "reports"
OUTPUTS_NAME = "official_outputs.jsonl"
PENDING = "PENDING LIVE API ACCESS: no live agent run has asked this question yet."
FIGURE_TOLERANCE = 1e-9


@dataclasses.dataclass(frozen=True)
class OfficialCase:
    id: str
    kind: str  # text2sql | exposure_calculator
    question: str
    expected_route: str  # sql | exposure
    reference_sql: str | None = None
    portfolio: str | None = None
    fixture: Path | None = None


def load_cases() -> list[OfficialCase]:
    data = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    fixtures = {json.loads(p.read_text(encoding="utf-8"))["question_id"]: p for p in sorted(FIXTURES.glob("*.json"))}
    cases = []
    for q in data["questions"]:
        truth = q["ground_truth"]
        if q["type"] == "text2sql":
            cases.append(OfficialCase(f"Q{q['id']}", q["type"], q["question"], "sql", reference_sql=truth["sql_query"]))
        else:
            cases.append(OfficialCase(f"Q{q['id']}", q["type"], q["question"], "exposure",
                                      portfolio=truth["parameters"]["portfolio_name"], fixture=fixtures.get(q["id"])))
    return cases


def recorded_outputs(directory: Path = REPORTS / "runs") -> list[Path]:
    return sorted(directory.glob(f"*/{OUTPUTS_NAME}"))


def answered_case_ids(outputs: list[Path]) -> set[str]:
    return agent_eval.answered_case_ids(outputs)


def run(settings: Settings, cases: list[OfficialCase], min_interval_s: float, *, skip: set[str] = frozenset(),
        runs_dir: Path = REPORTS / "runs", agent: PortfolioAgent | None = None,
        max_requests: int | None = None) -> Path:
    """Live run through the agent, under the same request cap, pacing and stop rules as the M4 check."""
    return agent_eval.run(settings, [AgentCase(c.id, c.question, {}) for c in cases], min_interval_s, skip=skip,
                          runs_dir=runs_dir, agent=agent, max_requests=max_requests, prefix="official-",
                          outputs_name=OUTPUTS_NAME)


def score(outputs: list[Path], cases: list[OfficialCase], settings: Settings,
          sql_outputs: list[Path] | None = None) -> dict:
    """Score every layer from recorded results and deterministic checks. Makes no API calls."""
    db_path = ensure_reference_db(settings.data_dir, settings.schema_path, settings.cache_dir)
    receipts: dict[str, tuple[str, dict]] = {}
    for record in agent_eval._read(outputs):
        case_id, receipt = record["case"]["id"], record["receipt"]
        if case_id in receipts and is_api_failure(receipt) and not is_api_failure(receipts[case_id][1]):
            continue  # an API failure never replaces a valid answer
        receipts[case_id] = (record["run"], receipt)
    sql_report = sql_eval.score(sql_outputs if sql_outputs is not None else sql_eval.recorded_outputs(), settings,
                                cases=[c for c in sql_eval.load_cases() if c.group == "official"])
    sql_items = {i["id"]: i for i in sql_report["items"]}
    conn = open_read_only(db_path)
    try:
        catalog = Catalog.from_db(conn)
        items = []
        for case in cases:
            item = {"id": case.id, "kind": case.kind, "question": case.question, "expected_route": case.expected_route,
                    "agent": _agent_layer(case, receipts.get(case.id), db_path)}
            if case.kind == "text2sql":
                sql_item = sql_items.get(case.id)
                item["sql_tool"] = ({"verdict": sql_item["verdict"], "explanation": sql_item["explanation"],
                                     "run": sql_item.get("run"), "prompt_version": sql_item.get("prompt_version"),
                                     "model": sql_item.get("model")} if sql_item else None)
            else:
                item["calculation"] = _calculation_layer(case, conn, catalog)
            items.append(item)
    finally:
        conn.close()
    return {"summary": _summary(items), "items": items, "sources": {
        "agent_runs": sorted({i["agent"]["run"] for i in items if i["agent"].get("run")}),
        "sql_tool_runs": sorted({i["sql_tool"]["run"] for i in items if i.get("sql_tool") and i["sql_tool"]["run"]})}}


def replay_check(settings: Settings, outputs: list[Path], cases: list[OfficialCase]) -> dict:
    """Re-run each recorded official question OFFLINE from Gemini's saved responses and compare the outcome
    with the live receipt. Makes no API request and writes nothing to the usage ledger.

    What it shows: given the same model output, today's validation, SQL execution, exposure engine, answer
    composition and checks reproduce the recorded result. It is a regression net for code changes after a live
    run, not new evidence about the model.
    """
    from portfolioiq.replay import RecordedLLM

    agent = PortfolioAgent.from_settings(settings, llm=RecordedLLM(settings))
    live: dict[str, tuple[str, dict]] = {}
    for record in agent_eval._read(outputs):
        if not is_api_failure(record["receipt"]):
            live[record["case"]["id"]] = (record["run"], record["receipt"])
    items = []
    for case in cases:
        if case.id not in live:
            items.append({"id": case.id, "state": "no valid live receipt", "identical": None, "differences": []})
            continue
        run_name, recorded = live[case.id]
        replayed = agent.ask(case.question).model_dump(mode="json")
        left, right = _signature(recorded), _signature(replayed)
        differences = sorted(k for k in left if left[k] != right[k])
        items.append({"id": case.id, "run": run_name, "state": "replayed" if not replayed.get("error_kind") else
                      f"not replayable ({replayed['error_kind']})", "identical": not differences,
                      "differences": differences, "replayed_responses": replayed["model"]["replayed_responses"]})
    compared = [i for i in items if i["identical"] is not None]
    return {"summary": {"identical": sum(1 for i in compared if i["identical"]), "compared": len(compared),
                        "total": len(items)}, "items": items}


def _signature(receipt: dict) -> dict:
    """The parts of a receipt that must be reproducible: everything except ids, times, latencies and tokens."""
    sql = receipt.get("sql") or {}
    execution = sql.get("execution") or {}
    return {
        "route": receipt["route"], "status": receipt["status"], "verification": receipt["verification"],
        "answer": receipt["answer"], "caveats": receipt["caveats"],
        "checks": [[c["name"], c["status"]] for c in receipt["checks"]],
        "sql": sql.get("sql"), "rows": execution.get("rows"), "columns": execution.get("columns"),
        "exposures": [[e["portfolio_name"], [[s["sector"], s["method_a"], s["method_b"]] for s in e["sectors"]]]
                      for e in receipt["exposures"]],
        "clarification": (receipt.get("clarification") or {}).get("candidates"),
    }


def _agent_layer(case: OfficialCase, recorded: tuple[str, dict] | None, db_path: Path) -> dict:
    if recorded is None:
        return {"verdict": "NOT EVALUATED", "explanation": PENDING, "run": None}
    run_name, data = recorded
    receipt = FinancialAnswerReceipt.model_validate(data)
    base = {"run": run_name, "route": receipt.route, "status": receipt.status, "receipt_id": receipt.receipt_id,
            "api_requests": receipt.model.api_requests, "model": receipt.model.model,
            "model_version": receipt.model.model_version, "router_version": receipt.data.router_version,
            "sql_prompt_version": receipt.data.sql_prompt_version, "context_version": receipt.data.context_version,
            "route_correct": receipt.route == case.expected_route}
    if is_api_failure(data):
        return {**base, "verdict": "NOT EVALUATED", "route_correct": None,
                "explanation": f"Not evaluated because of a Gemini API error ({receipt.error_kind}), not a wrong answer."}
    if receipt.route != case.expected_route:
        return {**base, "verdict": "FAIL", "explanation": f"Routed to {receipt.route}; expected {case.expected_route}."}
    if case.kind == "text2sql":
        execution = receipt.sql.execution if receipt.sql else None
        if execution is None or execution.status != "ok":
            return {**base, "verdict": "FAIL", "explanation": f"No SQL result (status {receipt.status})."}
        reference = execute_sql(db_path, case.reference_sql, max_rows=10_000)
        comparison = compare_results(reference.columns, reference.rows, execution.columns, execution.rows,
                                     ordered=requires_order(case.reference_sql))
        if comparison.strict:
            return {**base, "verdict": "PASS", "explanation": "Routed to SQL; the result matches the reference exactly."}
        kind = "Relaxed match only" if comparison.relaxed else "The result differs from the reference"
        return {**base, "verdict": "FAIL", "explanation": f"{kind}: {comparison.reason}."}
    problems = _exposure_problems(case, receipt)
    if problems:
        return {**base, "verdict": "FAIL", "explanation": "; ".join(problems) + "."}
    return {**base, "verdict": "PASS",
            "explanation": f"Routed to the exposure calculator for {case.portfolio}; both methods match the fixture."}


def _exposure_problems(case: OfficialCase, receipt: FinancialAnswerReceipt) -> list[str]:
    names = [e.portfolio_name for e in receipt.exposures]
    if names != [case.portfolio]:
        return [f"exposure calculated for {names or 'no portfolio'}, expected {case.portfolio}"]
    return _fixture_differences(receipt.exposures[0], _fixture(case))


def _calculation_layer(case: OfficialCase, conn: sqlite3.Connection, catalog: Catalog) -> dict:
    fixture = _fixture(case)
    if fixture is None:
        return {"verdict": "NOT EVALUATED", "explanation": "No hand-derived fixture for this question."}
    row = conn.execute("SELECT portfolio_id FROM portfolios WHERE portfolio_name = ?", (case.portfolio,)).fetchone()
    result = calculate_exposure(conn, row[0])
    problems = _fixture_differences(result, fixture)
    notes = [f"engine verification {result.verification.value}"]
    phrase = fixture.get("portfolio_phrase")
    if phrase:
        resolution = resolve_portfolio(catalog, phrase, question=case.question)
        if resolution.status != "resolved" or resolution.portfolio_name != case.portfolio:
            problems.append(f"the phrase '{phrase}' did not resolve to {case.portfolio} ({resolution.status})")
        else:
            notes.append(f"phrase '{phrase}' resolves to {case.portfolio} by rule {resolution.rule}")
    sectors = ", ".join(f"{s.sector} {s.method_a * 100:.2f}% / {s.method_b * 100:.2f}%" for s in result.sectors)
    if problems:
        return {"verdict": "FAIL", "explanation": "; ".join(problems) + ".", "figures": sectors}
    return {"verdict": "PASS", "explanation": f"Method A and Method B match the hand-derived fixture ({sectors}); "
                                              + "; ".join(notes) + ".", "figures": sectors,
            "verification": result.verification.value}


def _fixture(case: OfficialCase) -> dict | None:
    return json.loads(case.fixture.read_text(encoding="utf-8")) if case.fixture else None


def _fixture_differences(result, fixture: dict | None) -> list[str]:
    if fixture is None:
        return ["no hand-derived fixture"]
    expected = fixture["expected"]
    got_a = {s.sector: s.method_a for s in result.sectors}
    got_b = {s.sector: s.method_b for s in result.sectors}
    problems = []
    for label, got, want in (("Method A", got_a, expected["method_a"]), ("Method B", got_b, expected["method_b"])):
        if set(got) != set(want):
            problems.append(f"{label} sectors {sorted(got)}, expected {sorted(want)}")
        elif any(not math.isclose(got[s], want[s], abs_tol=FIGURE_TOLERANCE) for s in want):
            problems.append(f"{label} figures differ from the fixture")
    for field, value in (("eligible_stock_weight", result.eligible_stock_weight), ("residual", result.residual),
                         ("total_weight", result.total_weight)):
        if not math.isclose(value, expected[field], abs_tol=FIGURE_TOLERANCE):
            problems.append(f"{field} {value}, expected {expected[field]}")
    return problems


def _summary(items: list[dict]) -> dict:
    def tally(values: list[str]) -> dict:
        return {v: sum(1 for x in values if x == v) for v in ("PASS", "FAIL", "REFUSED", "NOT EVALUATED")}

    agent = [i["agent"] for i in items]
    routed = [a for a in agent if a.get("route_correct") is not None]
    return {
        "agent": {**tally([a["verdict"] for a in agent]),
                  "routing_correct": sum(1 for a in routed if a["route_correct"]), "routing_evaluated": len(routed),
                  "total": len(items)},
        "sql_tool": tally([i["sql_tool"]["verdict"] if i.get("sql_tool") else "NOT EVALUATED"
                           for i in items if i["kind"] == "text2sql"]),
        "calculation": tally([i["calculation"]["verdict"] for i in items if i["kind"] != "text2sql"]),
    }


def write_reports(report: dict, directory: Path = REPORTS, name: str = "official_evaluation") -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path, md_path = directory / f"{name}.json", directory / f"{name}.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    s = report["summary"]

    def counts(t: dict) -> str:
        return ", ".join(f"{k} {v}" for k, v in t.items() if k in ("PASS", "FAIL", "REFUSED", "NOT EVALUATED") and v) or "none"

    agent = s["agent"]

    def seen(key: str) -> str:
        values = sorted({str(i["agent"][key]) for i in report["items"] if i["agent"].get(key)})
        return ", ".join(values) or "none recorded"

    lines = [
        "# Official evaluation: ground_truth_dataset.json (Q1-Q10)", "",
        f"Agent runs: model {seen('model')} (served {seen('model_version')}); router {seen('router_version')}; "
        f"SQL prompt {seen('sql_prompt_version')}; context {seen('context_version')}.", "",
        "Three evidence layers, reported separately and never blended. A question with no recorded live result is "
        "NOT EVALUATED and is never counted as wrong.", "",
        "| Layer | What it measures | Result |", "|---|---|---|",
        f"| Agent, end to end (live Gemini) | Route chosen and the tool result against the reference | {counts(agent)}; "
        f"routing {agent['routing_correct']}/{agent['routing_evaluated']} evaluated (of {agent['total']}) |",
        f"| SQL tool, direct (Q1-Q8, live, recorded) | Execution accuracy, strict comparison (B13) | {counts(s['sql_tool'])} |",
        f"| Deterministic calculation (Q9-Q10, no Gemini) | Exposure engine against hand-derived fixtures | "
        f"{counts(s['calculation'])} |", "",
        "## Results by question", "",
        "| ID | Question | Expected route | Agent (end to end) | SQL tool (direct) | Calculation (deterministic) |",
        "|---|---|---|---|---|---|",
    ]
    for i in report["items"]:
        sql = i.get("sql_tool")
        calc = i.get("calculation")
        lines.append(f"| {i['id']} | {md_cell(i['question'])} | {i['expected_route']} | {i['agent']['verdict']} | "
                     f"{sql['verdict'] if sql else '-'} | {calc['verdict'] if calc else '-'} |")
    lines += ["", "## Explanations", ""]
    for i in report["items"]:
        lines.append(f"- **{i['id']}** agent: {md_cell(i['agent']['explanation'])}")
        if i.get("sql_tool"):
            lines.append(f"  SQL tool ({i['sql_tool']['run'] or 'no run'}): {md_cell(i['sql_tool']['explanation'])}")
        if i.get("calculation"):
            lines.append(f"  Calculation: {md_cell(i['calculation']['explanation'])}")
    lines += ["", "Agent receipts: `reports/runs/official-*/official_outputs.jsonl` (none recorded yet means the live "
              "agent run is still pending). SQL tool records: `reports/runs/*/sql_outputs.jsonl`. Fixtures: "
              "`evaluation/fixtures/`. Scoring makes no API calls: `python -m portfolioiq_eval official --rescore`."]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
