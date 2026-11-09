"""Stability benchmark (reliability scenario R18): the same questions asked several times, fresh each time.

Questions Q4, Q7, Q10 (ground truth) and scenarios R01, R09 go through the agent ``rounds`` times with the
response cache turned off, so every round gets a new Gemini response. Each round is recorded as its own run
(``stability-*/stability_outputs.jsonl``). Scoring compares what each round actually produced (route,
status and the result itself), never the wording, and makes no API calls.

A question is consistent when every evaluated round produced the same signature. Rounds that hit an API
error are NOT EVALUATED and excluded, never counted as inconsistent.
"""

from __future__ import annotations

import dataclasses
import json
from collections import Counter
from pathlib import Path

from portfolioiq.config import PROJECT_ROOT, Settings
from portfolioiq.receipt import FinancialAnswerReceipt
from portfolioiq_eval import agent_eval
from portfolioiq_eval.agent_eval import AgentCase, is_api_failure
from portfolioiq_eval.sql_eval import md_cell

GROUND_TRUTH = PROJECT_ROOT / "evaluation" / "ground_truth_dataset.json"
SCENARIOS = PROJECT_ROOT / "evaluation" / "reliability_scenarios.json"
REPORTS = PROJECT_ROOT / "reports"
OUTPUTS_NAME = "stability_outputs.jsonl"
QUESTION_IDS = ("Q4", "Q7", "Q10", "R01", "R09")


def load_cases() -> list[AgentCase]:
    truth = {f"Q{q['id']}": q["question"] for q in json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))["questions"]}
    scenarios = {s["id"]: s["scenario"] for s in json.loads(SCENARIOS.read_text(encoding="utf-8"))["scenarios"]}
    questions = {**truth, **scenarios}
    return [AgentCase(i, questions[i], {}) for i in QUESTION_IDS]


def recorded_outputs(directory: Path = REPORTS / "runs") -> list[Path]:
    return sorted(directory.glob(f"*/{OUTPUTS_NAME}"))


def run_round(settings: Settings, cases: list[AgentCase], min_interval_s: float, *, runs_dir: Path,
              max_requests: int | None) -> Path:
    """One round, with the response cache off so Gemini answers afresh."""
    fresh = dataclasses.replace(settings, llm_cache_dir=None)
    return agent_eval.run(fresh, cases, min_interval_s, runs_dir=runs_dir, max_requests=max_requests,
                          prefix="stability-", outputs_name=OUTPUTS_NAME)


def signature(receipt: FinancialAnswerReceipt) -> str:
    """What the round produced, independent of wording: route, status and the result values."""
    parts: dict = {"route": receipt.route, "status": receipt.status}
    if receipt.sql is not None and receipt.sql.execution is not None and receipt.sql.execution.status == "ok":
        rows = [[round(v, 6) if isinstance(v, float) else v for v in row] for row in receipt.sql.execution.rows]
        parts["rows"] = sorted(json.dumps(r, default=str) for r in rows)
    if receipt.exposures:
        parts["exposure"] = {e.portfolio_name: {s.sector: round(s.method_a, 9) for s in e.sectors}
                             for e in receipt.exposures}
    if receipt.comparison is not None:
        parts["leaders"] = receipt.comparison.leaders_method_a
    return json.dumps(parts, sort_keys=True, default=str)


def score(outputs: list[Path], cases: list[AgentCase]) -> dict:
    by_case: dict[str, list[dict]] = {c.id: [] for c in cases}
    for record in agent_eval._read(outputs):
        case_id = record["case"]["id"]
        if case_id not in by_case:
            continue
        receipt = FinancialAnswerReceipt.model_validate(record["receipt"])
        failed = is_api_failure(record["receipt"])
        by_case[case_id].append({"run": record["run"], "failed": failed, "route": receipt.route,
                                 "status": receipt.status, "signature": None if failed else signature(receipt),
                                 "api_requests": receipt.model.api_requests, "llm_latency_ms": receipt.timings.llm_latency_ms,
                                 "tokens": receipt.model.prompt_tokens + receipt.model.output_tokens
                                 + receipt.model.thinking_tokens, "model": receipt.model.model,
                                 "error_kind": receipt.error_kind})
    items = []
    for case in cases:
        rounds = by_case[case.id]
        evaluated = [r for r in rounds if not r["failed"]]
        counts = Counter(r["signature"] for r in evaluated)
        modal = counts.most_common(1)[0][1] if counts else 0
        if len(evaluated) < 2:
            verdict, explanation = "NOT EVALUATED", f"{len(evaluated)} evaluated round(s); at least 2 are needed."
        elif len(counts) == 1:
            verdict, explanation = "PASS", f"All {len(evaluated)} rounds produced the same route and result."
        else:
            verdict = "FAIL"
            explanation = (f"{len(counts)} different outcomes in {len(evaluated)} rounds; the most common occurred "
                           f"{modal} time(s). Routes: {', '.join(r['route'] for r in evaluated)}.")
        items.append({"id": case.id, "question": case.question, "verdict": verdict, "explanation": explanation,
                      "rounds": len(rounds), "evaluated": len(evaluated),
                      "api_failures": sum(r["failed"] for r in rounds),
                      "consistency": round(modal / len(evaluated), 3) if evaluated else None,
                      "api_requests": sum(r["api_requests"] for r in rounds),
                      "tokens": sum(r["tokens"] for r in rounds), "runs": sorted({r["run"] for r in rounds}),
                      "models": sorted({r["model"] for r in rounds if r["model"]})})
    evaluated_items = [i for i in items if i["verdict"] != "NOT EVALUATED"]
    return {"summary": {"questions": len(items),
                        "verdicts": {v: sum(i["verdict"] == v for i in items) for v in ("PASS", "FAIL", "NOT EVALUATED")},
                        "consistency_rate": (round(sum(i["consistency"] for i in evaluated_items) / len(evaluated_items), 3)
                                             if evaluated_items else None),
                        "api_requests": sum(i["api_requests"] for i in items)},
            "items": items}


def write_reports(report: dict, directory: Path = REPORTS, name: str = "stability") -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path, md_path = directory / f"{name}.json", directory / f"{name}.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    s = report["summary"]
    models = sorted({m for i in report["items"] for m in i["models"]})
    lines = ["# Stability benchmark (R18)", "",
             f"Model {', '.join(models) or 'none recorded'}; response cache off, so every round is a fresh Gemini "
             "response. A question is consistent when every evaluated round gives the same route and result; "
             "rounds with API errors are excluded and never counted as inconsistent.", "",
             f"Verdicts: {', '.join(f'{k} {v}' for k, v in s['verdicts'].items() if v) or 'none'}. Mean consistency "
             f"{s['consistency_rate']}. API requests {s['api_requests']}.", "",
             "| ID | Question | Verdict | Rounds (evaluated) | API failures | Consistency | Requests | Explanation |",
             "|---|---|---|---|---|---|---|---|"]
    for i in report["items"]:
        lines.append(f"| {i['id']} | {md_cell(i['question'])} | {i['verdict']} | {i['rounds']} ({i['evaluated']}) | "
                     f"{i['api_failures']} | {i['consistency']} | {i['api_requests']} | {md_cell(i['explanation'])} |")
    lines += ["", "Receipts: `stability-*/stability_outputs.jsonl` in the runs directory. Rescore: "
              "`python -m portfolioiq_eval stability --rescore`."]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
