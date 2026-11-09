"""Evaluation of the SQL Query Tool: Q1-Q8, unseen and safety questions, compared by executed results.

Built for a strict request budget (the free tier allows 20 requests per day):

- a live run asks Gemini only for cases with no valid recorded answer, unless a re-run is
  requested explicitly. It stops as soon as the daily quota is exhausted, when its own request cap
  is reached (retries and failed requests count), or after two consecutive outage responses;
- every answer is recorded to JSONL before scoring, and scoring makes no API calls;
- several runs are scored together. A later valid answer replaces an earlier one, but an API
  error never replaces a valid answer;
- cases that were never answered are reported as NOT EVALUATED, never as incorrect.

Outcomes are kept separate: correct, incorrect (including relaxed-only matches), failed (no valid
SQL), refused, not evaluated. Each case is also given a verdict in the reporting vocabulary: PASS,
FAIL, REFUSED (a safety prompt refused, the expected outcome) or NOT EVALUATED. API errors are
reported apart from model correctness, and latency and API usage separately too.
"""

from __future__ import annotations

import csv
import dataclasses
import datetime as dt
import hashlib
import json
import math
import os
import re
import statistics
from pathlib import Path

from portfolioiq.config import PROJECT_ROOT, Settings
from portfolioiq.sql_engine import ensure_reference_db, execute_sql
from portfolioiq.sql_tool import SqlTool, SqlToolResult
from portfolioiq.timing import format_ms, summarise_ms
from portfolioiq_eval.comparator import compare_results, requires_order

GROUND_TRUTH = PROJECT_ROOT / "evaluation" / "ground_truth_dataset.json"
UNSEEN = PROJECT_ROOT / "evaluation" / "unseen_sql_questions.json"
SHAPE_SET = PROJECT_ROOT / "evaluation" / "sql_shape_set.json"
SHAPE_CHECKSUM = PROJECT_ROOT / "evaluation" / "sql_shape_set.sha256"
REPORTS = PROJECT_ROOT / "reports"
FAILURE_STATUSES = {"invalid_sql", "execution_error", "timeout"}
REFUSAL_STATUSES = {"rejected", "unanswerable", "needs_clarification"}
STOP_KINDS = {"quota_exhausted", "not_configured", "api_error", "budget_exhausted"}  # later cases would fail too
OUTAGE_KINDS = {"unavailable", "network", "rate_limited"}  # transient, but never retried case after case
MAX_CONSECUTIVE_OUTAGES = 2  # failed requests in a row, counted per request, not per case
LEDGER = "reports/api_usage.jsonl"


def ledger_label() -> str:
    """The usage ledger named in reports: the configured one (a supplemental run has its own)."""
    return Path(os.getenv("PORTFOLIOIQ_USAGE_LOG") or LEDGER).as_posix()
_HTTP_STATUS = re.compile(r"Gemini API error (\d{3} [A-Z_]+)")


@dataclasses.dataclass(frozen=True)
class Case:
    id: str
    group: str  # official | unseen | safety | shape
    question: str
    reference_sql: str | None


def load_cases() -> list[Case]:
    gold = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    cases = [Case(f"Q{q['id']}", "official", q["question"], q["ground_truth"]["sql_query"])
             for q in gold["questions"] if q["type"] == "text2sql"]
    unseen = json.loads(UNSEEN.read_text(encoding="utf-8"))
    cases += [Case(q["id"], "unseen", q["question"], q["reference_sql"]) for q in unseen["questions"]]
    cases += [Case(q["id"], "safety", q["question"], None) for q in unseen["safety"]]
    return cases


def load_shape_cases() -> list[Case]:
    """The frozen result-shape set (group "shape"), scored strictly like every other SQL case.

    The file is frozen: a change after its checksum was written is an error, never a silent edit.
    """
    frozen = SHAPE_CHECKSUM.read_text(encoding="utf-8").split()[0]
    text = SHAPE_SET.read_text(encoding="utf-8")
    if hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest() != frozen:
        raise ValueError(f"{SHAPE_SET.name} no longer matches {SHAPE_CHECKSUM.name}: the frozen set was modified")
    return [Case(q["id"], "shape", q["question"], q["reference_sql"]) for q in json.loads(text)["cases"]]


def recorded_outputs(directory: Path = REPORTS / "runs") -> list[Path]:
    """Every recorded run, oldest first."""
    return sorted(directory.glob("*/sql_outputs.jsonl"))


def answered_case_ids(outputs: list[Path]) -> set[str]:
    """Cases that already have a valid recorded answer (anything except a Gemini error)."""
    return {r["case"]["id"] for r in _read_records(outputs) if r["result"]["status"] != "llm_error"}


def run(settings: Settings, cases: list[Case], min_interval_s: float, *, skip: set[str] = frozenset(),
        runs_dir: Path = REPORTS / "runs", max_requests: int | None = None) -> Path:
    """Live run: ask Gemini for each case not in ``skip`` and record the answers immediately.

    ``max_requests`` caps the API requests this run may send, retries and failed requests included.
    """
    run_dir = new_run_dir(runs_dir)
    tool = SqlTool.from_settings(dataclasses.replace(settings, llm_min_interval_s=min_interval_s,
                                                     llm_request_cap=max_requests))
    meta = {"started_utc": _utc_now(), "model": settings.gemini_model, "thinking_level": settings.gemini_thinking_level,
            "min_interval_s": min_interval_s, "request_cap": max_requests, "context_version": tool.context.version,
            "prompt_version": tool.prompt_version, "prompt_variant": tool.variant,
            "db_sha256_before": _hash(tool.db_path),
            "skipped": sorted(c.id for c in cases if c.id in skip), "attempted": [], "stopped": None}
    outputs = run_dir / "sql_outputs.jsonl"
    outages = 0
    with outputs.open("w", encoding="utf-8") as handle:
        for case in cases:
            if case.id in skip:
                continue
            result = tool.answer(case.question, label=case.id)
            handle.write(json.dumps({"case": dataclasses.asdict(case), "result": result.model_dump(mode="json")}) + "\n")
            handle.flush()
            meta["attempted"].append(case.id)
            print(f"{case.id:4} {result.status:20} requests {result.llm_calls}  cache {result.cache_hits}  "
                  f"LLM {format_ms(result.llm_latency_ms):>10}  {case.question[:56]}", flush=True)
            failure = result.error_kind if result.status == "llm_error" else None
            answered = [a.llm for a in result.attempts]
            outages = outage_streak(outages, failure, result.llm_calls - sum(c.attempts for c in answered),
                                    len(answered))
            stop = stop_reason(failure, outages)
            if stop:
                meta["stopped"] = {"case": case.id, "kind": stop, "message": result.message[:400]}
                print(f"Stopping: {stop}. Remaining cases were not attempted.", flush=True)
                break
    meta.update(finished_utc=_utc_now(), requests_sent=getattr(tool.llm, "requests_sent", None),
                db_sha256_after=_hash(tool.db_path))
    (run_dir / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return outputs


def new_run_dir(runs_dir: Path, prefix: str = "") -> Path:
    """A new, empty directory named by local time; never reuses an earlier run's directory."""
    stamp = f"{prefix}{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    run_dir, number = runs_dir / stamp, 1
    while run_dir.exists():
        number += 1
        run_dir = runs_dir / f"{stamp}-{number}"
    run_dir.mkdir(parents=True)
    return run_dir


def outage_streak(previous: int, error_kind: str | None, failed_requests: int, answered_calls: int) -> int:
    """Failed outage requests (503, network, rate limit) in a row, counted per request across cases.

    A case that ends in an outage error adds its failed requests to the streak; if any of its calls
    was answered first, the streak restarts from that case. Any other ending resets it.
    """
    if error_kind not in OUTAGE_KINDS:
        return 0
    return (0 if answered_calls else previous) + max(1, failed_requests)


def stop_reason(error_kind: str | None, consecutive_outages: int) -> str | None:
    """Why a live run must stop now: an error every later case would hit, or a run of outages."""
    if error_kind in STOP_KINDS:
        return error_kind
    if consecutive_outages >= MAX_CONSECUTIVE_OUTAGES:
        return f"{consecutive_outages} consecutive {error_kind} errors"
    return None


def score(outputs: Path | list[Path], settings: Settings, cases: list[Case] | None = None) -> dict:
    """Score recorded outputs against reference results. Makes no API calls.

    With ``cases``, every listed case appears in the report, unanswered ones as NOT EVALUATED.
    """
    paths = [outputs] if isinstance(outputs, Path) else list(outputs)
    db_path = ensure_reference_db(settings.data_dir, settings.schema_path, settings.cache_dir)
    chosen: dict[str, dict] = {}
    recorded: dict[str, list[dict]] = {}
    for record in _read_records(paths):
        case_id = record["case"]["id"]
        recorded.setdefault(case_id, []).append(record)
        current = chosen.get(case_id)
        if current is None or current["result"]["status"] == "llm_error" or record["result"]["status"] != "llm_error":
            chosen[case_id] = record

    order = [c.id for c in cases] if cases else list(chosen)
    known = {c.id: c for c in cases} if cases else {}
    items = []
    for case_id in order + [i for i in chosen if i not in order]:
        record = chosen.get(case_id)
        if record is None:
            case = known[case_id]
            items.append(_not_run(case))
            continue
        item = {**_score_case(Case(**record["case"]), SqlToolResult.model_validate(record["result"]), db_path),
                "run": record["run"], "history": [_history_entry(r, db_path) for r in recorded[case_id]]}
        item["raw_response"] = raw_response_location(item)
        items.append(item)
    metas = [_read_meta(p) for p in paths]
    meta = _merge_meta(metas)
    usage = _api_usage(settings.api_usage_log, metas)
    return {"meta": meta, "summary": _summarise(items, meta, usage), "items": items}


def write_reports(report: dict, directory: Path = REPORTS) -> tuple[Path, Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path, md_path, csv_path = (directory / f"sql_evaluation.{ext}" for ext in ("json", "md", "csv"))
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")
    columns = ["id", "group", "question", "verdict", "explanation", "verification", "api_error", "status", "outcome",
               "strict", "relaxed", "reason", "attempts", "llm_calls", "cache_hits", "llm_latency_ms", "sql_latency_ms",
               "prompt_tokens", "output_tokens", "thinking_tokens", "model", "model_version", "prompt_version",
               "context_version", "raw_response", "run", "sql"]
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["items"])
    return json_path, md_path, csv_path


def _score_case(case: Case, result: SqlToolResult, db_path: Path) -> dict:
    item = {
        "id": case.id, "group": case.group, "question": case.question, "status": result.status,
        "verification": result.verification.value, "sql": result.sql, "attempts": len(result.attempts),
        "llm_calls": result.llm_calls, "cache_hits": result.cache_hits, "llm_latency_ms": result.llm_latency_ms,
        "sql_latency_ms": result.sql_latency_ms, "prompt_tokens": result.prompt_tokens,
        "output_tokens": result.output_tokens, "thinking_tokens": result.thinking_tokens,
        "model": result.model, "model_version": result.model_version, "prompt_version": result.prompt_version,
        "context_version": result.context_version, "message": result.message,
        "api_error": _api_error(result),
        "raw_responses": [{"request_hash": a.llm.request_hash[:16], "source": a.llm.source}
                          for a in result.attempts if a.llm.request_hash],
    }
    item.update(_judge(case, result, db_path))
    item["verdict"] = verdict(case.group, item["outcome"], item["passed"])
    return item


def _history_entry(record: dict, db_path: Path) -> dict:
    """One recorded answer to a case: its run, status, verdict and SQL. Only the chosen answer is scored."""
    result = SqlToolResult.model_validate(record["result"])
    judged = _score_case(Case(**record["case"]), result, db_path)
    return {"run": record["run"], "status": result.status, "verdict": judged["verdict"],
            "api_error": judged["api_error"], "sql": result.sql}


def _judge(case: Case, result: SqlToolResult, db_path: Path) -> dict:
    """Outcome, pass flag and explanation for one recorded answer."""
    if result.status == "llm_error":
        return dict(outcome="not_evaluated", passed=False, strict=False, relaxed=False,
                    reason=f"not evaluated: {result.message}",
                    explanation=f"Not evaluated because of a Gemini API error, not a wrong answer. {result.message}")
    if case.group == "safety":
        refused = result.status in REFUSAL_STATUSES
        return dict(outcome="refused" if refused else result.status, passed=refused, strict=False, relaxed=False,
                    reason="refused and nothing was executed" if refused else "the request was not refused",
                    explanation=_refusal(result) if refused else
                    f"Not refused: the SQL tool returned status {result.status}. {result.message}")
    if result.status in ("ok", "no_rows"):
        reference = execute_sql(db_path, case.reference_sql, max_rows=10_000)
        comparison = compare_results(reference.columns, reference.rows, result.execution.columns,
                                     result.execution.rows, ordered=requires_order(case.reference_sql))
        outcome = "correct" if comparison.strict else ("relaxed_only" if comparison.relaxed else "incorrect")
        explanation = {
            "correct": "The executed result matches the reference result exactly." if comparison.reason == "match"
            else f"The executed result matches the reference result ({comparison.reason}).",
            "relaxed_only": f"Relaxed match only ({comparison.reason}); the strict comparison is the score.",
            "incorrect": f"The executed result differs from the reference result: {comparison.reason}.",
        }[outcome]
        return dict(outcome=outcome, passed=comparison.strict, strict=comparison.strict, relaxed=comparison.relaxed,
                    reason=comparison.reason, explanation=explanation)
    if result.status in FAILURE_STATUSES:
        return dict(outcome="failed", passed=False, strict=False, relaxed=False, reason=result.message,
                    explanation=f"No valid SQL ({result.status}). {result.message}")
    return dict(outcome="refused", passed=False, strict=False, relaxed=False, reason=result.message,
                explanation=f"An answerable question was declined ({result.status}). {result.message}")


def _api_error(result: SqlToolResult) -> str | None:
    """The Gemini failure kind, kept apart from correctness. Older records hold only the error message."""
    if result.status != "llm_error":
        return None
    if result.error_kind:
        return result.error_kind
    found = _HTTP_STATUS.search(result.message)
    return f"{found.group(1)} (kind not recorded)" if found else "kind not recorded"


def _refusal(result: SqlToolResult) -> str:
    """Who refused a safety prompt: Gemini itself, the SQL guard, or the database authorizer."""
    last = result.attempts[-1] if result.attempts else None
    if last is not None and last.sql and last.execution is not None:
        return f"Refused by the database authorizer; nothing was changed. {result.message}"
    if last is not None and last.sql:
        return f"Gemini drafted SQL; the SQL guard rejected it and nothing was executed. {result.message}"
    return f"Refused by Gemini ({result.status}); no SQL was written or executed. {result.message}"


def verdict(group: str, outcome: str, passed: bool) -> str:
    """The reporting vocabulary: PASS, FAIL, REFUSED (a safety prompt refused, as expected), NOT EVALUATED."""
    if outcome == "not_evaluated":
        return "NOT EVALUATED"
    if group == "safety":
        return "REFUSED" if passed else "FAIL"
    return "PASS" if outcome == "correct" else "FAIL"


def raw_response_location(item: dict) -> str:
    """Where the raw model responses behind a scored answer are kept."""
    if item["raw_responses"]:
        return f"{ledger_label()}: " + ", ".join(f"{r['request_hash']} ({r['source']})" for r in item["raw_responses"])
    if item.get("attempts"):
        return (f"not kept (the run predates the usage ledger); parsed output in "
                f"reports/runs/{item['run']}/sql_outputs.jsonl")
    return "none (no response was received)"


def _not_run(case: Case) -> dict:
    return {"id": case.id, "group": case.group, "question": case.question, "status": "not_run",
            "outcome": "not_evaluated", "verdict": "NOT EVALUATED", "passed": False, "strict": False, "relaxed": False,
            "reason": "no recorded answer yet: no live run has asked this question",
            "explanation": "Not evaluated: no live run has asked this question yet.", "verification": None,
            "api_error": None, "sql": None, "attempts": 0, "llm_calls": 0, "cache_hits": 0, "llm_latency_ms": None,
            "sql_latency_ms": None, "prompt_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "model": None,
            "model_version": None, "prompt_version": None, "context_version": None, "message": "",
            "raw_responses": [], "raw_response": "none (not attempted)", "run": None, "history": []}


def _summarise(items: list[dict], meta: dict, usage: dict) -> dict:
    def group(name: str) -> list[dict]:
        return [i for i in items if i["group"] == name]

    def outcomes(rows: list[dict]) -> dict:
        counts = {key: sum(i["outcome"] == key for i in rows)
                  for key in ("correct", "relaxed_only", "incorrect", "failed", "refused", "not_evaluated")}
        return {"strict": sum(i["strict"] for i in rows), "relaxed": sum(i["relaxed"] for i in rows),
                "evaluated": len(rows) - counts["not_evaluated"], "total": len(rows), **counts}

    def verdicts(rows: list[dict]) -> dict:
        return {v: sum(i["verdict"] == v for i in rows) for v in ("PASS", "FAIL", "REFUSED", "NOT EVALUATED")}

    # Rows recorded before 2026-11-09 hold 0.0 where nothing ran; they are read as "not measured", not as 0 ms.
    llm = sorted(i["llm_latency_ms"] for i in items if i["llm_calls"] and i["llm_latency_ms"])
    sql = sorted(i["sql_latency_ms"] for i in items if i["sql_latency_ms"])
    # Cases with no usable answer (a Gemini error, the budget, never asked) are counted, not dropped.
    unavailable = sum(i["outcome"] == "not_evaluated" or i.get("api_error") is not None for i in items)
    safety = group("safety")
    summary = {
        "official_execution_accuracy": outcomes(group("official")),
        "unseen_execution_accuracy": outcomes(group("unseen")),
        "safety_refusals": {"refused": sum(i.get("passed", False) for i in safety),
                            "evaluated": sum(i["outcome"] != "not_evaluated" for i in safety), "total": len(safety)},
        "verdicts": {name: verdicts(group(name)) for name in ("official", "unseen", "safety")},
        "api_errors": sum(i.get("api_error") is not None for i in items),
        "database_unchanged": meta.get("database_unchanged"),
        "repairs_used": sum(i["attempts"] > 1 for i in items),
        "llm_calls": sum(i["llm_calls"] for i in items),
        "cache_hits": sum(i.get("cache_hits", 0) for i in items),
        "latency_ms": {
            "llm_median": round(statistics.median(llm), 1) if llm else None,
            "llm_p95": _nearest_rank(llm, 0.95), "llm_max": llm[-1] if llm else None,
            "sql_median": round(statistics.median(sql), 3) if sql else None,
            # Complete statistics in milliseconds. "llm": successful Gemini requests (client-observed time);
            # "sql_execution": query execution. p95 is null below 20 observations.
            "llm": summarise_ms(llm, unavailable=unavailable),
            "sql_execution": summarise_ms(sql),
        },
        "tokens": {k: sum(i[f"{k}_tokens"] for i in items) for k in ("prompt", "output", "thinking")},
        "api_usage": usage,
    }
    if group("shape"):  # present only when the frozen result-shape set was run, so standard reports keep their shape
        summary["shape_execution_accuracy"] = outcomes(group("shape"))
        summary["verdicts"]["shape"] = verdicts(group("shape"))
    return summary


def _api_usage(ledger: Path | None, metas: list[dict]) -> dict:
    """Requests recorded in the usage ledger during the scored runs' time windows."""
    windows = [(m["started_utc"], m["finished_utc"]) for m in metas if m.get("started_utc") and m.get("finished_utc")]
    if ledger is None or not ledger.exists() or not windows:
        return {"source": "ledger unavailable for these runs", "requests": None}
    entries = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            if any(start <= entry["timestamp_utc"] <= end for start, end in windows):
                entries.append(entry)
    requests = [e for e in entries if e.get("api_request")]
    statuses: dict[str, int] = {}
    for entry in requests:
        statuses[entry["status"]] = statuses.get(entry["status"], 0) + 1
    return {"source": "reports/api_usage.jsonl", "requests": len(requests), "by_status": statuses,
            "cache_hits": sum(1 for e in entries if e.get("event") == "cache_hit"),
            "prompt_tokens": sum(e.get("prompt_tokens") or 0 for e in requests),
            "output_tokens": sum(e.get("output_tokens") or 0 for e in requests),
            "thinking_tokens": sum(e.get("thinking_tokens") or 0 for e in requests)}


def _read_records(paths: list[Path]) -> list[dict]:
    records = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                record["run"] = path.parent.name
                records.append(record)
    return records


def _read_meta(path: Path) -> dict:
    meta_path = path.parent / "run_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return {**meta, "run": path.parent.name}


def _merge_meta(metas: list[dict]) -> dict:
    checks = [m["db_sha256_before"] == m["db_sha256_after"] for m in metas
              if m.get("db_sha256_before") and m.get("db_sha256_after")]
    return {
        "runs": [{k: m.get(k) for k in ("run", "started_utc", "started", "finished_utc", "finished", "model",
                                         "thinking_level", "min_interval_s", "request_cap", "requests_sent",
                                         "context_version", "prompt_version", "skipped", "attempted", "stopped")}
                 for m in metas],
        "model": ", ".join(sorted({str(m.get("model", "n/a")) for m in metas})),
        "context_version": ", ".join(sorted({str(m.get("context_version", "n/a")) for m in metas})),
        "database_unchanged": all(checks) if checks and len(checks) == len(metas) else None,
    }


def _nearest_rank(values: list[float], quantile: float) -> float | None:
    return values[max(0, math.ceil(quantile * len(values)) - 1)] if values else None


def _latency_cells(stats: dict) -> str:
    """min / median / mean / p95 / max in ms, with n/a where a value does not exist (p95 needs 20+ observations)."""
    cells = " / ".join(format_ms(stats[k]) for k in ("min_ms", "median_ms", "mean_ms", "p95_ms", "max_ms"))
    return f"{cells} (n = {stats['count']})"


def _markdown(report: dict) -> str:
    s, meta = report["summary"], report["meta"]
    official, unseen, safety = s["official_execution_accuracy"], s["unseen_execution_accuracy"], s["safety_refusals"]

    def share(part: int, group: dict) -> str:
        missing = group["total"] - group["evaluated"]
        note = f"; {missing} not evaluated" if missing else ""
        return f"{part}/{group['evaluated']} evaluated ({group['total']} in set{note})"

    def tally(name: str) -> str:
        counts = s["verdicts"][name]
        return ", ".join(f"{verdict_name} {n}" for verdict_name, n in counts.items() if n) or "none"

    usage = s["api_usage"]
    lines = [
        "# SQL Query Tool evaluation", "",
        f"Model `{meta['model']}`; context version `{meta['context_version']}`; runs scored together: "
        + ", ".join(r["run"] for r in meta["runs"]) + ".", "",
        "| Verdicts | Result |", "|---|---|",
        f"| Official Q1-Q8 | {tally('official')} |",
        f"| Unseen U1-U10 | {tally('unseen')} |",
        f"| Safety S1-S3 | {tally('safety')} |",
        *([f"| Result-shape set S01-S17 | {tally('shape')} |"] if "shape" in s["verdicts"] else []),
        f"| API errors (kept apart from model correctness) | {s['api_errors']} |", "",
        "| Metric | Result |", "|---|---|",
        f"| Official Q1-Q8, strict | {share(official['strict'], official)} |",
        f"| Official Q1-Q8, relaxed | {share(official['relaxed'], official)} |",
        f"| Unseen questions, strict | {share(unseen['strict'], unseen)} |",
        f"| Unseen questions, relaxed | {share(unseen['relaxed'], unseen)} |",
        *([f"| Result-shape set, strict | {share(s['shape_execution_accuracy']['strict'], s['shape_execution_accuracy'])} |",
           f"| Result-shape set, relaxed | {share(s['shape_execution_accuracy']['relaxed'], s['shape_execution_accuracy'])} |"]
          if "shape_execution_accuracy" in s else []),
        f"| Outcomes, official + unseen: correct / relaxed only / incorrect / failed / refused / not evaluated | "
        + " / ".join(str(official[k] + unseen[k]) for k in
                     ("correct", "relaxed_only", "incorrect", "failed", "refused", "not_evaluated")) + " |",
        f"| Safety prompts refused, nothing executed | {share(safety['refused'], safety)} |",
        f"| Database unchanged (SHA-256 before and after every run) | {s['database_unchanged']} |",
        f"| Repairs used | {s['repairs_used']} |",
        f"| Gemini requests behind the scored answers / cache hits | {s['llm_calls']} / {s['cache_hits']} |",
        f"| Gemini request latency, successful requests: min / median / mean / p95 / max (client-observed) | "
        f"{_latency_cells(s['latency_ms']['llm'])} |",
        f"| Cases with no latency (Gemini error, budget stop or not run), counted apart | "
        f"{s['latency_ms']['llm']['unavailable']} |",
        f"| SQL execution latency: min / median / mean / p95 / max | {_latency_cells(s['latency_ms']['sql_execution'])} |",
        f"| Tokens prompt / output / thinking | {s['tokens']['prompt']} / {s['tokens']['output']} / {s['tokens']['thinking']} |",
        f"| API requests in the ledger during these runs | {usage.get('requests')} {usage.get('by_status', '')} |",
        "", *_runs_table(meta["runs"]),
        "", "## Results by question", "",
        "PASS: the executed result matches the reference result exactly. FAIL: a different result, no valid SQL, a "
        "declined answerable question, or a safety prompt that was not refused. REFUSED: a safety prompt was refused "
        "and nothing was executed, which is the expected outcome. NOT EVALUATED: no valid answer is recorded (an API "
        "error, the request budget, or a run that stopped before reaching the case); it is never counted as wrong.", "",
        "| ID | Question | Verdict | Explanation | Verification | API requests (cache hits) | Gemini latency | "
        "Tokens in / out / thinking | API error |", "|---|---|---|---|---|---|---|---|---|",
    ]
    for i in report["items"]:
        lines.append(f"| {i['id']} | {md_cell(i['question'])} | {i['verdict']} | {md_cell(i['explanation'], 220)} | "
                     f"{i['verification'] or '-'} | {i['llm_calls']} ({i['cache_hits']}) | "
                     f"{format_ms(i['llm_latency_ms'] if i['llm_calls'] else None)} | "
                     f"{i['prompt_tokens']} / {i['output_tokens']} / {i['thinking_tokens']} | {i['api_error'] or '-'} |")
    lines += _history_table(report["items"])
    lines += ["", "## Provenance", "",
              f"Every successful raw response is saved the moment it arrives: in `{ledger_label()}` (the entry with the request "
              "hash shown below) and in `.cache/llm_responses/<hash>.json` (not committed). `cache` means the "
              "response was reused from an identical earlier request, at no quota cost.", "",
              "| ID | Run | Model (served version) | SQL prompt version | Context version | Raw response |",
              "|---|---|---|---|---|---|"]
    for i in report["items"]:
        served = f"{i['model'] or '-'} ({i['model_version'] or '-'})"
        lines.append(f"| {i['id']} | {i.get('run') or '-'} | {served} | {i['prompt_version'] or 'not recorded'} | "
                     f"{i['context_version'] or '-'} | {md_cell(i['raw_response'])} |")
    lines += ["", "## Generated SQL", ""]
    for i in report["items"]:
        lines += [f"**{i['id']}** ({i['outcome']})", "", "```sql", i["sql"] or "-- no SQL", "```", ""]
    return "\n".join(lines)


def _runs_table(runs: list[dict]) -> list[str]:
    """Each scored run: its settings, request count and why it stopped. Older runs recorded less."""
    lines = ["## Runs", "",
             "Each live run has its own directory under `reports/runs/`, and earlier runs are never modified.", "",
             "| Run | Started | Model, thinking | Pacing (s) | Request cap | Requests sent | SQL prompt version | "
             "Cases asked | Stopped |", "|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        started = r.get("started_utc") or (f"{r['started']} (local time)" if r.get("started") else "-")
        asked = r.get("attempted")
        stopped = r.get("stopped")
        if stopped:
            stop = f"at {stopped['case']}: {stopped['kind']}; later cases were not asked"
        else:
            stop = "no" if asked is not None else "not recorded"
        lines.append(
            f"| {r['run']} | {started} | {r.get('model') or '-'}, {r.get('thinking_level') or '-'} | "
            f"{r.get('min_interval_s') if r.get('min_interval_s') is not None else '-'} | "
            f"{r.get('request_cap') if r.get('request_cap') is not None else 'none'} | "
            f"{r.get('requests_sent') if r.get('requests_sent') is not None else 'not recorded'} | "
            f"{r.get('prompt_version') or 'not recorded'} | "
            f"{(', '.join(asked) or 'none') if asked is not None else 'not recorded'} | {md_cell(stop)} |")
    return lines


def _history_table(items: list[dict]) -> list[str]:
    """Cases with answers in more than one run. The latest valid answer is scored; the others are kept."""
    repeated = [i for i in items if len(i.get("history", [])) > 1]
    if not repeated:
        return []
    lines = ["", "## Cases recorded in more than one run", "",
             "The latest valid answer is scored, and an API error never replaces a valid answer. Earlier answers "
             "stay in their own run directories.", "",
             "| ID | Run | Verdict of that answer | API error | SQL compared with the scored answer |",
             "|---|---|---|---|---|"]
    for i in repeated:
        for h in i["history"]:
            if h["run"] == i["run"]:
                run, same = f"{h['run']} (scored)", "-"
            else:
                run = h["run"]
                same = "-" if not (h.get("sql") and i.get("sql")) else ("identical" if h["sql"] == i["sql"] else "different")
            lines.append(f"| {i['id']} | {run} | {h.get('verdict', '-')} | {h.get('api_error') or '-'} | {same} |")
    return lines


def md_cell(text: object, limit: int | None = None) -> str:
    """Text made safe for one Markdown table cell."""
    value = " ".join(str(text).split()).replace("|", "\\|")
    return value if limit is None or len(value) <= limit else value[: limit - 3] + "..."


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
