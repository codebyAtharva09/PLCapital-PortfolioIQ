"""Agent runs: questions through the full Portfolio Agent, live, with every receipt recorded.

M4 uses this for its live integration check: one question per route (SQL, exposure, an ambiguous
portfolio reference, and a hybrid question), each with its expected behaviour stated in advance in
`evaluation/agent_integration_questions.json`. M5 extends the same runner to the ground-truth set
and the reliability benchmark.

The free-tier budget rules apply:

- requests are paced, and a run may be given a hard request cap (retries and failures count);
- each receipt is written the moment it exists;
- a case with a recorded answer is not asked again unless named in ``rerun``;
- the run stops at the first exhausted-quota or exhausted-budget error, or after two consecutive
  outage responses;
- scoring reads only recorded receipts and makes no API calls.

Each case is reported as PASS, FAIL, REFUSED or NOT EVALUATED, with its model and prompt versions,
API requests, latency, tokens, verification state and the location of its raw model responses.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
from pathlib import Path

from portfolioiq.agent import PortfolioAgent
from portfolioiq.config import PROJECT_ROOT, Settings
from portfolioiq.receipt import FinancialAnswerReceipt
from portfolioiq.timing import format_ms, summarise_ms
from portfolioiq_eval.sql_eval import ledger_label, md_cell, new_run_dir, outage_streak, stop_reason

INTEGRATION_QUESTIONS = PROJECT_ROOT / "evaluation" / "agent_integration_questions.json"
REPORTS = PROJECT_ROOT / "reports"
LLM_ERROR_KINDS = {"not_configured", "quota_exhausted", "rate_limited", "unavailable", "api_error", "network",
                   "malformed_output", "blocked", "budget_exhausted", "llm_error"}


@dataclasses.dataclass(frozen=True)
class AgentCase:
    id: str
    question: str
    expect: dict


def load_cases(path: Path = INTEGRATION_QUESTIONS) -> list[AgentCase]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [AgentCase(q["id"], q["question"], q["expect"]) for q in data["questions"]]


def recorded_outputs(directory: Path = REPORTS / "runs") -> list[Path]:
    """Every recorded agent run, oldest first."""
    return sorted(directory.glob("*/agent_outputs.jsonl"))


def is_api_failure(receipt: dict) -> bool:
    return receipt["status"] == "error" and receipt.get("error_kind") in LLM_ERROR_KINDS


def answered_case_ids(outputs: list[Path]) -> set[str]:
    """Cases with a recorded receipt that is not a Gemini failure."""
    return {r["case"]["id"] for r in _read(outputs) if not is_api_failure(r["receipt"])}


def run(settings: Settings, cases: list[AgentCase], min_interval_s: float, *, skip: set[str] = frozenset(),
        runs_dir: Path = REPORTS / "runs", agent: PortfolioAgent | None = None,
        max_requests: int | None = None, prefix: str = "agent-", outputs_name: str = "agent_outputs.jsonl") -> Path:
    """Ask the agent each case not in ``skip``, recording each receipt immediately.

    ``max_requests`` caps the API requests this run may send, retries and failed requests included.
    ``prefix`` and ``outputs_name`` keep other question sets (the official Q1-Q10 run) in their own records.
    """
    run_dir = new_run_dir(runs_dir, prefix=prefix)
    agent = agent or PortfolioAgent.from_settings(dataclasses.replace(settings, llm_min_interval_s=min_interval_s,
                                                                      llm_request_cap=max_requests))
    meta = {"started_utc": _utc_now(), "model": settings.gemini_model, "thinking_level": settings.gemini_thinking_level,
            "min_interval_s": min_interval_s, "request_cap": max_requests, "context_version": agent.context.version,
            "sql_prompt_version": agent.sql_tool.prompt_version, "sql_prompt_variant": agent.sql_tool.variant,
            "router_version": agent.router_version,
            "db_sha256_before": _hash(agent.db_path), "skipped": sorted(c.id for c in cases if c.id in skip),
            "attempted": [], "stopped": None}
    outputs = run_dir / outputs_name
    outages = 0
    with outputs.open("w", encoding="utf-8") as handle:
        for case in cases:
            if case.id in skip:
                continue
            receipt = agent.ask(case.question, label=case.id)
            handle.write(json.dumps({"case": dataclasses.asdict(case), "receipt": receipt.model_dump(mode="json")}) + "\n")
            handle.flush()
            meta["attempted"].append(case.id)
            print(f"{case.id:4} {receipt.route:26} {receipt.status:20} requests {receipt.model.api_requests}  "
                  f"cache {receipt.model.cache_hits}  {case.question[:60]}", flush=True)
            failure = receipt.error_kind if is_api_failure(receipt.model_dump(mode="json")) else None
            answered = llm_calls(receipt)
            outages = outage_streak(outages, failure, receipt.model.api_requests - sum(c.attempts for c in answered),
                                    len(answered))
            stop = stop_reason(failure, outages)
            if stop:
                meta["stopped"] = {"case": case.id, "kind": stop, "message": receipt.answer[:400]}
                print(f"Stopping: {stop}. Remaining cases were not attempted.", flush=True)
                break
    meta.update(finished_utc=_utc_now(), requests_sent=getattr(agent.llm, "requests_sent", None),
                db_sha256_after=_hash(agent.db_path))
    (run_dir / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return outputs


def llm_calls(receipt: FinancialAnswerReceipt) -> list:
    """The answered Gemini calls behind a receipt, in order: the routing call, then any SQL drafts."""
    return [call for step in receipt.tool_calls for call in step.llm_calls]


def check(case: AgentCase, receipt: FinancialAnswerReceipt) -> list[str]:
    """Differences between a receipt and the behaviour expected in advance; empty means it behaved as expected."""
    expect, problems = case.expect, []
    if "route" in expect and receipt.route != expect["route"]:
        problems.append(f"route {receipt.route}, expected {expect['route']}")
    if "status" in expect and receipt.status != expect["status"]:
        problems.append(f"status {receipt.status}, expected {expect['status']}")
    if "portfolio" in expect:
        names = [e.portfolio_name for e in receipt.exposures]
        if names != [expect["portfolio"]]:
            problems.append(f"exposure for {names or 'no portfolio'}, expected {expect['portfolio']}")
    if "candidates" in expect:
        offered = [c.name for c in receipt.clarification.candidates] if receipt.clarification else []
        if offered != expect["candidates"]:
            problems.append(f"candidates {offered}, expected {expect['candidates']}")
    if "leader" in expect:
        leaders = receipt.comparison.leaders_method_a if receipt.comparison else []
        if leaders != [expect["leader"]]:
            problems.append(f"leader {leaders or 'none'}, expected {expect['leader']}")
    if "rows" in expect:
        rows = receipt.sql.execution.rows if receipt.sql and receipt.sql.execution else None
        if rows != expect["rows"]:
            problems.append(f"rows {rows}, expected {expect['rows']}")
    return problems


def score(outputs: list[Path], cases: list[AgentCase]) -> dict:
    """Compare the latest valid receipt per case with its expected behaviour. Makes no API calls."""
    chosen: dict[str, tuple[str, dict]] = {}
    for record in _read(outputs):
        case_id, receipt = record["case"]["id"], record["receipt"]
        if case_id in chosen and is_api_failure(receipt) and not is_api_failure(chosen[case_id][1]):
            continue  # an API failure never replaces a valid answer
        chosen[case_id] = (record["run"], receipt)
    items = []
    for case in cases:
        if case.id not in chosen:
            items.append({"id": case.id, "question": case.question, "outcome": "not_evaluated",
                          "verdict": "NOT EVALUATED", "problems": [],
                          "explanation": "Not evaluated: no live run has asked this question yet."})
            continue
        run_name, data = chosen[case.id]
        items.append(_score_receipt(case, run_name, data))
    evaluated = [i for i in items if i["outcome"] != "not_evaluated"]
    recorded = [i for i in items if "api_requests" in i]
    return {"summary": {"as_expected": sum(i["outcome"] == "as_expected" for i in items),
                        "unexpected": sum(i["outcome"] == "unexpected" for i in items),
                        "evaluated": len(evaluated), "total": len(items),
                        "verdicts": {v: sum(i["verdict"] == v for i in items)
                                     for v in ("PASS", "FAIL", "REFUSED", "NOT EVALUATED")},
                        "api_errors": sum(i.get("api_error") is not None for i in items),
                        "api_requests": sum(i["api_requests"] for i in recorded),
                        "cache_hits": sum(i["cache_hits"] for i in recorded),
                        # Milliseconds, over answered cases; cases with no valid receipt are counted apart.
                        "latency_ms": {
                            "total": summarise_ms((i.get("total_latency_ms") for i in evaluated),
                                                  unavailable=len(items) - len(evaluated)),
                            "gemini_requests": summarise_ms(i.get("llm_latency_ms") for i in evaluated)}},
            "items": items}


def _score_receipt(case: AgentCase, run_name: str, data: dict) -> dict:
    receipt = FinancialAnswerReceipt.model_validate(data)
    failed = is_api_failure(data)
    problems = [] if failed else check(case, receipt)
    outcome = "not_evaluated" if failed else ("as_expected" if not problems else "unexpected")
    refused = receipt.status in ("declined", "rejected")
    verdict = {"not_evaluated": "NOT EVALUATED", "unexpected": "FAIL"}.get(outcome, "REFUSED" if refused else "PASS")
    if failed:
        explanation = f"Not evaluated because of a Gemini API error, not a wrong answer. {receipt.answer}"
    elif problems:
        explanation = "Did not behave as expected: " + "; ".join(problems) + "."
    else:
        explanation = f"Behaved as expected ({receipt.route}, {receipt.status}). {receipt.answer}"
    raw = [{"step": step.step, "tool": step.tool, "request_hash": call.request_hash[:16], "source": call.source}
           for step in receipt.tool_calls for call in step.llm_calls if call.request_hash]
    usage = receipt.model
    return {"id": case.id, "question": case.question, "outcome": outcome, "verdict": verdict, "problems": problems,
            "explanation": explanation, "run": run_name, "route": receipt.route, "status": receipt.status,
            "answer": receipt.answer, "verification": receipt.verification.value,
            "api_error": receipt.error_kind if failed else None, "model": usage.model,
            "model_version": usage.model_version, "router_version": receipt.data.router_version,
            "sql_prompt_version": receipt.data.sql_prompt_version if receipt.sql else None,
            "api_requests": usage.api_requests, "cache_hits": usage.cache_hits,
            "latency_schema": receipt.timings.latency_schema, "total_latency_ms": receipt.timings.total_latency_ms,
            "llm_latency_ms": receipt.timings.llm_latency_ms,
            "planner_latency_ms": receipt.timings.planner_latency_ms,
            "sql_generation_latency_ms": receipt.timings.sql_generation_latency_ms,
            "sql_execution_latency_ms": receipt.timings.sql_execution_latency_ms,
            "exposure_calculation_latency_ms": receipt.timings.exposure_calculation_latency_ms,
            "orchestration_latency_ms": receipt.timings.orchestration_latency_ms,
            "prompt_tokens": usage.prompt_tokens,
            "output_tokens": usage.output_tokens, "thinking_tokens": usage.thinking_tokens,
            "total_tokens": usage.prompt_tokens + usage.output_tokens + usage.thinking_tokens,
            "raw_responses": raw,
            "raw_response": (f"{ledger_label()}: " + ", ".join(f"step {r['step']} {r['tool']} {r['request_hash']} ({r['source']})"
                                                         for r in raw)) if raw else "none (no response was received)",
            "receipt_id": receipt.receipt_id}


def write_reports(report: dict, directory: Path = REPORTS, name: str = "agent_integration") -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path, md_path = directory / f"{name}.json", directory / f"{name}.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    s = report["summary"]
    tally = ", ".join(f"{name} {n}" for name, n in s["verdicts"].items() if n) or "none"
    lines = ["# Agent integration check", "",
             f"Verdicts: {tally}. Behaved as expected: {s['as_expected']} of {s['evaluated']} evaluated "
             f"({s['total']} in the set). API errors (kept apart from correctness): {s['api_errors']}. "
             f"API requests: {s['api_requests']}; cache hits: {s['cache_hits']}.", "",
             "Expected behaviour is fixed in advance in `evaluation/agent_integration_questions.json`; receipts are "
             "recorded in `reports/runs/agent-*/agent_outputs.jsonl`. PASS: behaved as expected. FAIL: did not. "
             "REFUSED: declined, as expected. NOT EVALUATED: no valid receipt (an API error, the request budget, or "
             "no live run has asked the question yet); never counted as wrong.", "",
             "| Case | Question | Verdict | Route | Status | Explanation | Verification | API requests (cache hits) "
             "| Gemini request latency | Total latency | Tokens in / out / thinking | API error |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i in report["items"]:
        lines.append(f"| {i['id']} | {md_cell(i['question'])} | {i['verdict']} | {i.get('route', '-')} | "
                     f"{i.get('status', '-')} | {md_cell(i['explanation'], 260)} | {i.get('verification', '-')} | "
                     f"{i.get('api_requests', '-')} ({i.get('cache_hits', '-')}) | "
                     f"{format_ms(i.get('llm_latency_ms'))} | {format_ms(i.get('total_latency_ms'))} | "
                     f"{i.get('prompt_tokens', '-')} / {i.get('output_tokens', '-')} / {i.get('thinking_tokens', '-')} | "
                     f"{i.get('api_error') or '-'} |")
    lines += ["", "## Provenance", "",
              f"Raw responses are saved the moment they arrive: in `{ledger_label()}` (the entry with the request hash shown) "
              "and in `.cache/llm_responses/<hash>.json` (not committed).", "",
              "| Case | Run | Model (served version) | Router version | SQL prompt version | Receipt | Raw responses |",
              "|---|---|---|---|---|---|---|"]
    for i in report["items"]:
        served = f"{i.get('model') or '-'} ({i.get('model_version') or '-'})" if "model" in i else "-"
        lines.append(f"| {i['id']} | {i.get('run') or '-'} | {served} | {i.get('router_version') or '-'} | "
                     f"{i.get('sql_prompt_version') or '-'} | {i.get('receipt_id') or '-'} | "
                     f"{md_cell(i.get('raw_response') or '-')} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def _read(paths: list[Path]) -> list[dict]:
    records = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                record["run"] = path.parent.name
                records.append(record)
    return records


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
