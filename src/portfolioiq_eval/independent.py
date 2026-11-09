"""Independent reliability benchmark: 51 hand-checked cases across 13 categories, split into development,
validation and held-out test sets. Design: reports/independent_benchmark_design.md.

How it differs from the official evaluation, and why:

- Expected answers come from `benchmark_oracles.py` (plain Python over the CSV files), not from reference SQL alone.
  The frozen dataset stores them, and tests prove oracle, reference SQL and stored value agree.
- Primary acceptance is *semantic*: every expected value must be present and correct, and extra columns are allowed
  (the question names what to return). Strict column-set matching, as the official comparator requires (B13), is
  reported separately for every case. The official comparator is not modified or reused for official scoring.
- Tracks are reported separately, each with a 95% Wilson interval: end to end, tool routing, SQL semantic (relaxed and
  strict), calculation, clarification, and refusal or validation. Silent failures (a wrong answer that carries a
  validated verification state) are counted on their own.
- An API error is NOT EVALUATED, never wrong. Held-out cases are loaded only when asked for, and a live run on them
  needs an explicit flag, so they are not used for tuning by accident.

Live runs go through the same agent, request cap, pacing and stop rules as every other evaluation.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

from portfolioiq.config import PROJECT_ROOT, Settings
from portfolioiq.receipt import Timings
from portfolioiq_eval import agent_eval
from portfolioiq_eval.agent_eval import AgentCase, is_api_failure
from portfolioiq_eval.comparator import compare_results
from portfolioiq_eval.sql_eval import md_cell

EVAL_DIR = PROJECT_ROOT / "evaluation"
DEV_VALIDATION = EVAL_DIR / "independent_benchmark_dev_validation.json"
HELD_OUT = EVAL_DIR / "independent_benchmark_heldout.json"
CHECKSUMS = EVAL_DIR / "independent_benchmark.sha256"
REPORTS = PROJECT_ROOT / "reports"
OUTPUTS_NAME = "independent_outputs.jsonl"
SPLITS = ("dev", "validation", "test")
TOLERANCE = 1e-9
VALIDATED = ("RESULT_VALIDATED", "INDEPENDENTLY_RECONCILED")
SECRET = re.compile(r"AIza[0-9A-Za-z_\-]{35}")
EXPECTED_ROUTE = {"sql": "sql", "no_data": "sql", "exposure": "exposure", "hybrid": "exposure_for_sql_results"}
REFUSAL_KINDS = ("refuse", "decline", "invalid")
# Requests one case needs: (minimum, maximum with one retry per call and one SQL repair). Invalid input needs none.
REQUESTS = {"sql": (2, 6), "no_data": (2, 6), "exposure": (1, 2), "hybrid": (2, 6), "clarify": (1, 2),
            "not_found": (1, 2), "decline": (1, 2), "refuse": (1, 4), "invalid": (0, 0)}


def _normalised_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")).hexdigest()


def verify_frozen() -> None:
    """The dataset is frozen: any edit after the checksums were written is an error, not a silent change."""
    frozen = {line.split(None, 1)[1].strip(): line.split()[0] for line in CHECKSUMS.read_text().splitlines() if line.strip()}
    for path in (DEV_VALIDATION, HELD_OUT):
        name = f"evaluation/{path.name}"
        if _normalised_sha256(path) != frozen.get(name):
            raise RuntimeError(f"{name} differs from its frozen checksum; the independent benchmark must not be edited.")


def load_cases(splits: tuple[str, ...] = ("dev", "validation")) -> list[dict]:
    """The cases of the requested splits. The held-out file is not even opened unless "test" is requested."""
    unknown = set(splits) - set(SPLITS)
    if unknown:
        raise ValueError(f"unknown split(s) {sorted(unknown)}; choose from {SPLITS}")
    verify_frozen()
    cases: list[dict] = []
    for path in (DEV_VALIDATION, HELD_OUT):
        if path == HELD_OUT and "test" not in splits:
            continue
        cases += [c for c in json.loads(path.read_text(encoding="utf-8"))["cases"] if c["split"] in splits]
    return cases


def request_budget(cases: list[dict]) -> dict:
    low = sum(REQUESTS[c["kind"]][0] for c in cases)
    high = sum(REQUESTS[c["kind"]][1] for c in cases)
    return {"cases": len(cases), "minimum": low, "maximum": high, "by_kind": dict(Counter(c["kind"] for c in cases))}


def recorded_outputs(directory: Path = REPORTS / "runs") -> list[Path]:
    return sorted(directory.glob(f"*/{OUTPUTS_NAME}"))


def run(settings: Settings, cases: list[dict], min_interval_s: float, *, runs_dir: Path = REPORTS / "runs",
        skip: set[str] = frozenset(), max_requests: int | None = None, tag: str = "dev", agent=None) -> Path:
    """Live run through the agent; each receipt is recorded the moment it arrives."""
    return agent_eval.run(settings, [AgentCase(c["id"], c["question"], {}) for c in cases], min_interval_s, skip=skip,
                          runs_dir=runs_dir, agent=agent, max_requests=max_requests, prefix=f"independent-{tag}-",
                          outputs_name=OUTPUTS_NAME)


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float] | None:
    """The 95% Wilson score interval for a proportion. An observed 38/40 gives about 83.5% to 98.6%."""
    if total == 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return centre - half, centre + half


# --- judging one receipt ---------------------------------------------------------------------------------------

def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, abs_tol=TOLERANCE)


def _same_figures(got: dict, want: dict) -> bool:
    return set(got) == set(want) and all(_close(got[k], want[k]) for k in want)


def _judge(case: dict, r: dict) -> dict:
    """The facts about one valid receipt: did it meet the case's acceptance rule, and on which track."""
    kind, want = case["kind"], case["expected"]
    status, route = r["status"], r["route"]
    exposures = r.get("exposures") or []
    execution = (r.get("sql") or {}).get("execution")
    expected_route = EXPECTED_ROUTE.get(kind)
    route_ok = None if expected_route is None else route == expected_route
    out = {"route_ok": route_ok, "strict": None, "calc_ok": None, "detail": ""}

    if kind in ("sql",):
        if status not in ("answered", "no_data") or execution is None or execution.get("status") != "ok":
            return {**out, "ok": False, "strict": False, "relaxed": False,
                    "detail": f"no valid SQL result (route {route}, status {status})"}
        cmp = compare_results(want["columns"], want["rows"], execution["columns"], execution["rows"], ordered=case["ordered"])
        accepted = cmp.strict if case["accept"] == "strict" else cmp.relaxed
        return {**out, "ok": bool(accepted and route_ok), "strict": bool(cmp.strict and route_ok), "detail": cmp.reason,
                "relaxed": bool(cmp.relaxed and route_ok)}
    if kind == "exposure":
        if len(exposures) != 1 or exposures[0]["portfolio_name"] != case["portfolio"]:
            return {**out, "ok": False, "calc_ok": False,
                    "detail": f"exposure for {[e['portfolio_name'] for e in exposures] or 'no portfolio'}, expected {case['portfolio']}"}
        e = exposures[0]
        if not want["method_a"]:
            ok = e["status"] == "no_eligible_stocks" and not e["sectors"]
            return {**out, "ok": bool(ok and route_ok), "calc_ok": ok, "detail": "no eligible stocks stated" if ok else f"status {e['status']}"}
        got_a = {s["sector"]: s["method_a"] for s in e["sectors"]}
        got_b = {s["sector"]: s["method_b"] for s in e["sectors"]}
        bonds = sum(x["weight"] for x in e["excluded"] if x["label"].startswith("Bonds"))
        ok = (_same_figures(got_a, want["method_a"]) and _same_figures(got_b, want["method_b"])
              and _close(e["residual"], want["residual"]) and _close(bonds, want["bonds"]))
        return {**out, "ok": bool(ok and route_ok), "calc_ok": ok, "detail": "figures match" if ok else "figures differ"}
    if kind == "hybrid":
        comparison = r.get("comparison") or {}
        names = sorted(e["portfolio_name"] for e in exposures)
        share = {e["portfolio_name"]: next((s["method_a"] for s in e["sectors"] if s["sector"] == case["sector"]), 0.0)
                 for e in exposures}
        ok = (names == want["portfolios"] and _same_figures(share, want["method_a"])
              and comparison.get("leaders_method_a") == [want["leader"]] and status == "answered")
        return {**out, "ok": bool(ok and route_ok), "calc_ok": ok,
                "detail": "portfolios, figures and leader match" if ok else
                          f"portfolios {names}, leaders {comparison.get('leaders_method_a')}, expected {want['leader']}"}
    if kind == "clarify":
        names = {c["name"] for c in (r.get("clarification") or {}).get("candidates", [])}
        ok = status == "needs_clarification" and not exposures
        return {**out, "ok": ok, "strict": ok and names == set(want["candidates"]),
                "detail": f"asked; candidates {sorted(names) or 'chosen by the router'}" if ok else f"status {status}"}
    if kind == "not_found":
        ok = status == "not_found" and not exposures
        return {**out, "ok": ok, "detail": f"status {status}"}
    if kind == "no_data":
        rows = (execution or {}).get("rows")
        ok = status in ("no_data", "unanswerable", "not_found") and not exposures and not rows
        return {**out, "ok": ok, "detail": f"status {status}; no figure invented" if ok else f"status {status}, rows {rows}"}
    if kind == "invalid":
        ok = status == "invalid_input" and r["model"]["api_requests"] == 0
        return {**out, "ok": ok, "detail": f"status {status}, {r['model']['api_requests']} request(s)"}
    if kind == "decline":
        ok = status in ("declined", "unanswerable") and not exposures and execution is None
        return {**out, "ok": ok, "detail": f"status {status}"}
    if kind == "refuse":
        leaked = bool(SECRET.search(r.get("answer") or ""))
        ran = execution is not None and execution.get("status") == "ok"
        ok = status in ("rejected", "declined", "unanswerable") and not ran and not leaked
        return {**out, "ok": ok, "detail": f"status {status}" + ("; a key-shaped string was shown" if leaked else "")}
    raise ValueError(f"unknown case kind {kind!r}")


def score(outputs: list[Path], cases: list[dict]) -> dict:
    """Score recorded receipts against the frozen expectations. Makes no API calls."""
    chosen: dict[str, tuple[str, dict]] = {}
    for record in agent_eval._read(outputs):
        case_id, receipt = record["case"]["id"], record["receipt"]
        if case_id in chosen and is_api_failure(receipt) and not is_api_failure(chosen[case_id][1]):
            continue  # an API failure never replaces a valid answer
        chosen[case_id] = (record["run"], receipt)
    items = []
    for case in cases:
        base = {"id": case["id"], "split": case["split"], "category": case["category"], "kind": case["kind"],
                "question": case["question"]}
        if case["id"] not in chosen:
            items.append({**base, "verdict": "NOT EVALUATED", "explanation": "No live run has asked this case yet."})
            continue
        run_name, receipt = chosen[case["id"]]
        if is_api_failure(receipt):
            items.append({**base, "verdict": "NOT EVALUATED", "run": run_name, "api_error": receipt.get("error_kind"),
                          "explanation": f"Gemini API error ({receipt.get('error_kind')}); not a wrong answer."})
            continue
        judged = _judge(case, receipt)
        verdict = "FAIL" if not judged["ok"] else ("REFUSED" if case["kind"] in REFUSAL_KINDS else "PASS")
        silent = (not judged["ok"] and receipt["status"] in ("answered", "no_data")
                  and receipt["verification"] in VALIDATED)
        usage = receipt["model"]
        items.append({**base, **judged, "verdict": verdict, "explanation": judged["detail"], "run": run_name,
                      "route": receipt["route"], "status": receipt["status"], "verification": receipt["verification"],
                      "silent_failure": silent, "api_requests": usage["api_requests"], "cache_hits": usage["cache_hits"],
                      "llm_latency_ms": Timings.model_validate(receipt["timings"]).llm_latency_ms,
                      "model": usage["model"],
                      "tokens": usage["prompt_tokens"] + usage["output_tokens"] + usage["thinking_tokens"],
                      "sql_prompt_version": receipt["data"].get("sql_prompt_version"),
                      "sql_prompt_variant": receipt["data"].get("sql_prompt_variant"),
                      "router_version": receipt["data"]["router_version"], "receipt_id": receipt["receipt_id"]})
    return {"summary": _summary(items), "items": items}


def _track(items: list[dict], key: str, kinds: tuple[str, ...] | None = None) -> dict:
    pool = [i for i in items if i["verdict"] != "NOT EVALUATED" and (kinds is None or i["kind"] in kinds)
            and i.get(key) is not None]
    wins = sum(1 for i in pool if i[key])
    interval = wilson(wins, len(pool))
    return {"correct": wins, "evaluated": len(pool),
            "rate": round(wins / len(pool), 4) if pool else None,
            "ci95": [round(interval[0], 4), round(interval[1], 4)] if interval else None}


def _summary(items: list[dict]) -> dict:
    evaluated = [i for i in items if i["verdict"] != "NOT EVALUATED"]
    for i in evaluated:
        i["passed"] = i["verdict"] in ("PASS", "REFUSED")
    sql_kinds = ("sql",)
    by_category, by_split = {}, {}
    for key, store in (("category", by_category), ("split", by_split)):
        for name in sorted({i[key] for i in items}):
            group = [i for i in evaluated if i[key] == name]
            store[name] = {"passed": sum(i["passed"] for i in group), "evaluated": len(group),
                           "total": sum(1 for i in items if i[key] == name)}
    return {
        "cases": len(items), "evaluated": len(evaluated),
        "not_evaluated": len(items) - len(evaluated),
        "tracks": {
            "end_to_end": _track(items, "passed"),
            "tool_routing": _track(items, "route_ok"),
            "sql_semantic_relaxed": _track(items, "relaxed", sql_kinds),
            "sql_strict_columns": _track(items, "strict", sql_kinds),
            "calculation": _track(items, "calc_ok"),
            "clarification": _track(items, "passed", ("clarify", "not_found")),
            "refusal_and_validation": _track(items, "passed", REFUSAL_KINDS + ("no_data",)),
        },
        "silent_failures": sum(1 for i in evaluated if i.get("silent_failure")),
        "verdicts": {v: sum(1 for i in items if i["verdict"] == v) for v in ("PASS", "FAIL", "REFUSED", "NOT EVALUATED")},
        "api_requests": sum(i.get("api_requests", 0) for i in evaluated),
        "by_category": by_category, "by_split": by_split,
        "configurations": sorted({f"{i.get('model')} / SQL prompt {i.get('sql_prompt_variant') or 'baseline'} "
                                  f"{i.get('sql_prompt_version')}" for i in evaluated}),
    }


def write_reports(report: dict, directory: Path = REPORTS, name: str = "independent_benchmark") -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path, md_path = directory / f"{name}.json", directory / f"{name}.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    s = report["summary"]

    def pct(track: dict) -> str:
        if not track["evaluated"]:
            return "not evaluated"
        low, high = track["ci95"]
        return f"{track['correct']}/{track['evaluated']} = {track['rate'] * 100:.1f}% (95% CI {low * 100:.1f}-{high * 100:.1f}%)"

    labels = {"end_to_end": "End to end (all kinds)", "tool_routing": "Tool routing", "sql_semantic_relaxed":
              "SQL semantic (extra columns allowed)", "sql_strict_columns": "SQL strict (exact column set, as B13)",
              "calculation": "Financial calculation (exposure and hybrid figures)", "clarification":
              "Clarification and not-found handling", "refusal_and_validation": "Refusal, validation and no-data handling"}
    lines = ["# Independent benchmark results", "",
             f"Configuration: {', '.join(s['configurations']) or 'none recorded'}. {s['evaluated']} of {s['cases']} cases "
             f"evaluated; NOT EVALUATED cases (no run yet, or an API error) are never counted as wrong. "
             f"Silent failures (wrong, yet carrying a validated verification state): {s['silent_failures']}.", "",
             "Observed proportions with 95% Wilson intervals. They describe this sample; they are not a guarantee for future "
             "questions.", "", "| Track | Result |", "|---|---|"]
    lines += [f"| {labels[k]} | {pct(v)} |" for k, v in s["tracks"].items()]
    lines += ["", "| Split | Passed | Evaluated | In set |", "|---|---|---|---|"]
    lines += [f"| {k} | {v['passed']} | {v['evaluated']} | {v['total']} |" for k, v in s["by_split"].items()]
    lines += ["", "| Category | Passed | Evaluated | In set |", "|---|---|---|---|"]
    lines += [f"| {k} | {v['passed']} | {v['evaluated']} | {v['total']} |" for k, v in s["by_category"].items()]
    lines += ["", "## Cases", "", "| ID | Split | Category | Verdict | Route | Requests | Detail |", "|---|---|---|---|---|---|---|"]
    for i in report["items"]:
        lines.append(f"| {i['id']} | {i['split']} | {md_cell(i['category'])} | {i['verdict']}"
                     f"{' (silent)' if i.get('silent_failure') else ''} | {i.get('route', '-')} | "
                     f"{i.get('api_requests', '-')} | {md_cell(i['explanation'], 160)} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
