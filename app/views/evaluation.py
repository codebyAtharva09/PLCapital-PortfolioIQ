"""Evaluation & Evidence: recorded results only, with baseline, candidate, supplemental and security kept apart.

Nothing on this page is estimated. A case without a live result is NOT EVALUATED and never counted as wrong.
No control on this page can send a Gemini request.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

import backend
import ui

SECURITY_CATEGORIES = {"Malformed input", "Injection via tool argument", "Unauthorized operation", "Prompt injection", "API failure (simulated)"}
VERDICTS = ("PASS", "FAIL", "REFUSED", "NOT EVALUATED", "N/A")
OFFLINE_COMMANDS = """python -m portfolioiq_eval official --rescore
python -m portfolioiq_eval sql --rescore
python -m portfolioiq_eval agent --rescore
python -m portfolioiq_eval benchmark
python -m portfolioiq_eval usage"""
LIVE_COMMAND = "python -m portfolioiq_eval official --max-requests 18 --runs-dir reports/<new folder>/runs --reports-dir reports/<new folder>"
CANDIDATE_COMMAND = ("$env:PORTFOLIOIQ_SQL_PROMPT = \"<candidate name>\"\n"
                     "python -m portfolioiq_eval official --max-requests 18 "
                     "--runs-dir reports/<sprint>/candidate/runs --reports-dir reports/<sprint>/candidate")


def _tally(counts: dict) -> str:
    return ", ".join(f"{k} {v}" for k, v in counts.items() if k in VERDICTS and v) or "none"


def render() -> None:
    ui.page_header("Evaluation & Evidence", "Recorded evaluation runs and deterministic checks. Failures stay visible, and cases "
                   "without a live result are shown as NOT EVALUATED.")
    official = backend.report("official")
    if official is None:
        ui.empty_state("No evaluation run yet", "Generate the reports from recorded runs (no API calls):", "python -m portfolioiq_eval official --rescore")
        return
    _headline(official)
    production, baseline, candidate, supplemental, security, quality = st.tabs(
        ["Production Evaluation", "Original Baseline", "Candidate History", "Supplemental Evaluation", "Security Evaluation", "Test Suite & Controls"])
    with production:
        _results(official, "Active production configuration: candidate-2.3", "reports/sprint_v2_3/candidate/ (sealed record with a SHA-256 manifest)",
                 "evidence_production", backend.PRODUCTION_RUNS)
    with baseline:
        original = backend.report("baseline_official")
        if original:
            ui.callout("demo", "Kept as the historical record", "The original configuration, before promotion. Restore it with PORTFOLIOIQ_SQL_PROMPT=baseline.")
            _results(original, "Original baseline: d5a914eb71d5", "reports/sprint_v2_1/baseline/official_evaluation.json (frozen copy)",
                     "evidence_baseline", backend.BASELINE_RUNS)
    with candidate:
        _candidate()
    with supplemental:
        _supplemental()
    with security:
        _security()
    with quality:
        _quality(official)


def _latest_run(official: dict) -> str | None:
    runs = official.get("sources", {}).get("agent_runs") or []
    return runs[-1] if runs else None


def _headline(official: dict) -> None:
    agent = official["summary"]["agent"]
    frozen = backend.frozen_baseline() or {}
    versions = frozen.get("versions", {})
    run = _latest_run(official)
    receipts = backend.run_receipts(run) if run else {}
    first = next(iter(receipts.values()), {})
    fingerprint = first.get("data", {}).get("data_fingerprint", backend.fingerprint())
    record = backend.report("production_record") or {}
    candidate = record.get("candidate", {})
    ui.section_label("Official benchmark: active production configuration")
    ui.metrics([("Questions", agent["total"], "ground_truth_dataset.json"), ("Passed", agent["PASS"], None), ("Failed", agent["FAIL"], None),
                ("Not evaluated", agent["NOT EVALUATED"], None), ("Routing correct", f"{agent['routing_correct']} of {agent['routing_evaluated']}", None)])
    ui.verdict_bar([("PASS", agent["PASS"], ui.GREEN), ("FAIL", agent["FAIL"], ui.RED), ("NOT EVALUATED", agent["NOT EVALUATED"], ui.RESIDUAL)])
    comparator = frozen.get("comparator", "")
    changed = frozen.get("changed", [])
    ui.key_values([("Latest recorded run", run or "none"), ("Configuration", candidate.get("variant", "not recorded")),
                   ("Model", record.get("model") or first.get("model", {}).get("model") or "not recorded"),
                   ("SQL prompt version", candidate.get("sql_prompt_version", "not recorded")), ("Shape policy", candidate.get("shape_policy", "not recorded")),
                   ("Router version", versions.get("router", "not recorded")), ("Original baseline prompt (rollback)", versions.get("sql_prompt", "not recorded")),
                   ("Dataset fingerprint", fingerprint), ("Evaluator (strict comparator SHA-256)", comparator[:16] or "not recorded"),
                   ("Frozen files unchanged", f"{frozen.get('files', 0) - len(changed) - len(frozen.get('hardened', []))} of {frozen.get('files', 0)}"
                    + (f"; {len(frozen['hardened'])} hardened under evaluation/security_manifest.json" if frozen.get("hardened") else "")
                    + (f"; changed: {', '.join(changed)}" if changed else ""))])


def _results(official: dict, title: str, source: str, key: str, runs_root) -> None:
    summary = official["summary"]
    ui.callout("ok" if not summary["agent"]["FAIL"] else "warning", title,
               f"End-to-end: {_tally(summary['agent'])}. Direct SQL tool Q1-Q8: {_tally(summary['sql_tool'])}. "
               f"Deterministic calculation Q9-Q10: {_tally(summary['calculation'])}. These three layers are reported separately and never blended.")
    rows = []
    for item in official["items"]:
        sql_layer, calc = item.get("sql_tool"), item.get("calculation")
        rows.append([item["id"], item["question"], item["expected_route"], ui.chip(item["agent"]["verdict"]),
                     ui.chip(sql_layer["verdict"]) if sql_layer else "—", ui.chip(calc["verdict"]) if calc else "—"])
    ui.head("Question results")
    ui.table(["ID", "Question", "Expected route", "Agent (live)", "SQL tool (live)", "Calculation"], rows, html_columns={3, 4, 5},
             widths=["5%", "47%", "10%", "13%", "13%", "12%"])
    ui.footnote(f"Source: {source}")
    _question_evidence(official, key)
    ui.head("Run history", "Every recorded run is kept. Later runs never overwrite earlier ones")
    history = backend.run_history(runs_root)
    if history:
        ui.table(["Run", "Started", "Model", "Requests sent", "SQL prompt", "Stopped by"],
                 [[r["run"], r["started"] or "—", r["model"] or "—", r["requests"] if r["requests"] is not None else "not recorded",
                   r["prompt"] or "not recorded", r["stopped"] or "—"] for r in history], numeric={3})
    usage = backend.api_usage() if runs_root == backend.BASELINE_RUNS else None
    if usage:
        ui.footnote(f"Gemini usage ledger: {usage['requests']} requests, failed ones included; "
                    + ", ".join(f"{k} {v}" for k, v in usage["by_status"].items()), "Source: reports/api_usage.jsonl")


def _question_evidence(official: dict, key: str) -> None:
    ui.head("Question evidence", "What the recorded run did for one question. Reference answers stay in the evaluator")
    ids = [i["id"] for i in official["items"]]
    default = next((ids.index(i["id"]) for i in official["items"] if i["agent"]["verdict"] == "FAIL"), 0)
    chosen = st.selectbox("Question", ids, index=default, key=key)
    item = next(i for i in official["items"] if i["id"] == chosen)
    agent = item["agent"]
    receipt = backend.run_receipts(agent["run"]).get(chosen, {}) if agent.get("run") else {}
    timings, sql = receipt.get("timings", {}), receipt.get("sql") or {}
    execution = sql.get("execution") or {}
    rows: list[tuple[str, object]] = [
        ("Question", item["question"]), ("Status", agent.get("status", "not recorded")),
        ("Expected route", item["expected_route"]), ("Actual route", agent.get("route", "not recorded")),
        ("Comparator outcome", f"{agent['verdict']}: {agent['explanation']}"),
    ]
    if execution:
        rows.append(("Actual result", f"{execution.get('row_count')} row(s); columns: {', '.join(execution.get('columns', []))}"))
    if item.get("calculation"):
        rows.append(("Calculation layer", f"{item['calculation']['verdict']}: {item['calculation']['explanation']}"))
    if timings:
        rows.append(("Duration", _durations(timings)))
    rows += [("Gemini requests", agent.get("api_requests", "not recorded")), ("Receipt", agent.get("receipt_id", "not recorded"))]
    ui.key_values(rows)
    if sql.get("sql"):
        st.code(sql["sql"], language="sql", wrap_lines=True)
    for exposure in receipt.get("exposures", []):
        ui.footnote(f"{exposure['portfolio_name']}: Method B denominator is {exposure['method_b_denominator']}; "
                    f"verification {exposure['verification']}")


def _durations(timings: dict) -> str:
    """Stage durations from a stored receipt. Older receipts use the short field names; total time there includes pacing, so it is left out."""
    fields = (("Gemini", timings.get("llm_ms")), ("SQL", timings.get("sql_ms")), ("exposure", timings.get("exposure_ms"))) if "llm_ms" in timings else (
        ("planner", timings.get("planner_latency_ms")), ("SQL generation", timings.get("sql_generation_latency_ms")),
        ("SQL execution", timings.get("sql_execution_latency_ms")), ("exposure", timings.get("exposure_calculation_latency_ms")))
    return " · ".join(f"{name} {ui.ms(value)}" for name, value in fields)


def _candidate() -> None:
    ui.callout("demo", "Candidate results are separate", "Each configuration keeps its own folder and is never combined into one score. candidate-2.3 was promoted to production after its live "
               "evaluation; v2.2 failed the official gate and was not promoted. PORTFOLIOIQ_SQL_PROMPT selects another configuration.")
    runs = backend.candidate_runs()
    if not runs:
        ui.empty_state("No live candidate evaluation recorded", "No Gemini run of a candidate configuration has been recorded, so no accuracy is claimed for one.")
    for run in runs:
        _candidate_run(run)
    comparisons = backend.diagnostic_comparisons()
    if comparisons:
        ui.head("Diagnostic shape comparison", "From saved results. A diagnosis of result shape only: the official score comes from the strict comparator")
        ui.table(["Sprint", "Configuration", "Exact shape", "Semantic value match", "Cases compared"],
                 [[c["sprint"], c["label"], c["exact_shape"], c["semantic"], c["evaluated"]] for c in comparisons], numeric={2, 3, 4})
    st.caption("A candidate run needs a key and quota, and must use its own folders:")
    st.code(CANDIDATE_COMMAND, language="powershell")


def _candidate_run(run: dict) -> None:
    meta = run["meta"]
    variant = meta.get("prompt_variant") or "candidate"
    ui.head(f"{run['sprint']}: {variant}", f"Model {meta.get('model', 'not recorded')} · SQL prompt {meta.get('prompt_version') or meta.get('sql_prompt_version') or 'not recorded'} · "
            f"started {meta.get('started_utc', 'not recorded')} · {run['path']}")
    if run["official"]:
        agent = run["official"]["summary"]["agent"]
        ui.metrics([("Official questions", agent["total"], None), ("Passed", agent["PASS"], None), ("Failed", agent["FAIL"], None), ("Not evaluated", agent["NOT EVALUATED"], None)])
        ui.table(["ID", "Question", "Verdict", "Explanation"], [[i["id"], i["question"], ui.chip(i["agent"]["verdict"]), ui.short(i["agent"]["explanation"])]
                                                               for i in run["official"]["items"]], html_columns={2}, widths=["5%", "40%", "13%", "42%"])
    if run["sql"]:
        verdicts = run["sql"]["summary"]["verdicts"]
        items = run["sql"]["items"]
        evaluated = sum(1 for i in items if i["verdict"] != "NOT EVALUATED")
        ui.callout("warning" if evaluated < len(items) else "demo", f"{evaluated} of {len(items)} SQL cases evaluated",
                   f"Official: {_tally(verdicts['official'])}. Unseen: {_tally(verdicts['unseen'])}. Safety: {_tally(verdicts['safety'])}. "
                   "Partial runs are not comparable with the baseline.")
        shown = [i for i in items if i["verdict"] != "NOT EVALUATED"]
        if shown:
            ui.table(["ID", "Question", "Verdict", "Explanation"], [[i["id"], i["question"], ui.chip(i["verdict"]), ui.short(i["explanation"])] for i in shown],
                     html_columns={2}, widths=["5%", "40%", "13%", "42%"])


def _supplemental() -> None:
    ui.callout("warning", "Not official results", "These are additional tests. None of them changes the official benchmark above.")
    sql, agent, bench = backend.report("sql"), backend.report("agent"), backend.report("benchmark")
    if sql:
        items = [i for i in sql["items"] if i["group"] == "unseen"]
        ui.head("Unseen SQL questions (U1-U10)", f"Project-written, {sql['items'][0].get('model') or 'official model'}. {_tally(sql['summary']['verdicts']['unseen'])}")
        ui.table(["ID", "Question", "Verdict", "Explanation", "Run"], [[i["id"], i["question"], ui.chip(i["verdict"]), ui.short(i["explanation"]), i.get("run") or "—"]
                                                                       for i in items], html_columns={2}, widths=["5%", "36%", "13%", "34%", "12%"])
    if agent:
        ui.head("Agent integration (one question per route)", _tally(agent["summary"]["verdicts"]))
        ui.table(["ID", "Question", "Verdict", "Route", "Explanation"], [[i["id"], i["question"], ui.chip(i["verdict"]), i.get("route", "—"), ui.short(i["explanation"])]
                                                                         for i in agent["items"]], html_columns={2}, widths=["5%", "34%", "13%", "14%", "34%"])
    if bench:
        ui.head("Reliability benchmark (19 scenarios)", f"Deterministic components: {_tally(bench['summary']['component'])}. Live end to end, recorded before promotion: {_tally(bench['summary']['live'])}.")
        rows = [[i["id"], i["category"], i["scenario"], ui.chip(i["component"]["verdict"]), ui.chip(i["live"]["verdict"])] for i in bench["items"]
                if i["category"] not in SECURITY_CATEGORIES]
        ui.table(["ID", "Category", "Scenario", "Component", "Live"], rows, html_columns={3, 4}, widths=["5%", "17%", "48%", "13%", "17%"])
    flash, stability = backend.report("flash_official"), backend.report("flash_stability")
    if flash:
        ui.head("A different model: gemini-3.5-flash-lite", "Run separately to cover cases the official model's quota could not reach. Never merged with the results above")
        lines = [("Official Q1-Q10 through the agent", _tally(flash["summary"]["agent"]))]
        if stability:
            lines.append(("Stability (repeatability, not correctness)", f"{_tally(stability['summary']['verdicts'])}; mean consistency {stability['summary']['consistency_rate']}"))
        ui.key_values(lines)
        ui.footnote("Source: reports/supplemental/gemini-3.5-flash-lite/")
    independent = backend.report("independent")
    ui.head("Independent 51-case benchmark")
    heldout = backend.report("heldout")
    if heldout:
        track = heldout["summary"]["tracks"]["end_to_end"]
        ui.key_values([("Held-out T01-T16 on candidate-2.3", f"{_tally(heldout['summary']['verdicts'])}; end to end {track['correct']} of {track['evaluated']}, "
                        f"95% Wilson {track['ci95'][0] * 100:.1f}% to {track['ci95'][1] * 100:.1f}%; silent failures {heldout['summary']['silent_failures']}")])
        ui.footnote("One run. Its policy false-positive check saw these reference queries earlier", "Source: reports/heldout_v2_3/")
    if independent is None:
        ui.empty_state("NOT YET EVALUATED", "The development and validation splits (35 of the 51 cases) have no live result, so no score is shown for them.",
                       "python -m portfolioiq_eval independent --plan")
    else:
        ui.key_values([("Development split", _tally(independent["summary"]["verdicts"]))])


def _security() -> None:
    sql, bench = backend.report("sql"), backend.report("benchmark")
    ui.callout("demo", "How to read this", "Gemini refusing a request is not treated as proof of safety. The deterministic rows test the guard and the read-only database directly.")
    if sql:
        items = [i for i in sql["items"] if i["group"] == "safety"]
        ui.head("Unsafe requests sent to Gemini (S1-S3)", f"{_tally(sql['summary']['verdicts']['safety'])}. Database unchanged: {sql['summary']['database_unchanged']}")
        ui.table(["ID", "Request", "Verdict", "Explanation"], [[i["id"], i["question"], ui.chip(i["verdict"]), ui.short(i["explanation"])] for i in items],
                 html_columns={2}, widths=["5%", "42%", "13%", "40%"])
    if bench:
        ui.head("Deterministic security scenarios", f"Database unchanged: {bench['summary']['database_unchanged']}")
        rows = [[i["id"], i["category"], i["scenario"], ui.chip(i["component"]["verdict"]), ui.chip(i["live"]["verdict"]), ui.short(i["component"]["explanation"], 150)]
                for i in bench["items"] if i["category"] in SECURITY_CATEGORIES]
        ui.table(["ID", "Category", "Scenario", "Component", "Live", "Evidence"], rows, html_columns={3, 4}, widths=["5%", "14%", "26%", "10%", "13%", "32%"])
    ui.link("sql", "Try the guard test bench on SQL Intelligence")


def _quality(official: dict) -> None:
    _test_suite()
    ui.head("Live questions answered by Gemini", "Separate from the automated tests above, which use fakes and send no request")
    sql, agent = backend.report("sql"), backend.report("agent")
    a = official["summary"]["agent"]
    rows = [["Official benchmark through the agent", a["total"] - a["NOT EVALUATED"], a["total"], _tally(a)]]
    if sql:
        u = sql["summary"]["unseen_execution_accuracy"]
        rows.append(["Unseen SQL questions", u["evaluated"], u["total"], _tally(sql["summary"]["verdicts"]["unseen"])])
    if agent:
        s = agent["summary"]
        rows.append(["Agent integration", s["evaluated"], s["total"], _tally(s["verdicts"])])
    ui.table(["Set", "Evaluated", "In set", "Outcome"], rows, numeric={1, 2}, widths=["42%", "10%", "10%", "38%"])
    if sql and any(i.get("llm_calls") and i.get("llm_latency_ms") for i in sql["items"]):
        _latency(sql)

    ui.head("Evaluation controls")
    st.caption("Offline re-score: recomputes every official verdict from recorded answers. No API call, and no report file is written.")
    if st.button("Re-score recorded run now", key="rescore"):
        _rescore(official)
    result = st.session_state.get("rescore_result")
    if result:
        ui.callout(result["kind"], result["label"], result["text"])
    st.caption("The same evaluators from a terminal, with no API call:")
    st.code(OFFLINE_COMMANDS, language="bash")
    ui.callout("warning", "Live Gemini evaluation is command-line only", "A live run spends real quota: about 16 requests for Q1-Q10, against 20 a day on the free "
               "tier. It paces requests 13 seconds apart, stops after repeated failures and records every run. Check the remaining quota in Google AI Studio first. "
               "This page has no button for it, so it cannot be started by accident.")
    st.code(LIVE_COMMAND, language="bash")


def _rescore(official: dict) -> None:
    try:
        with st.spinner("Re-scoring recorded answers…"):
            summary = backend.rescore_official()
    except Exception:  # noqa: BLE001 - a plain message instead of a stack trace
        st.session_state["rescore_result"] = {"kind": "error", "label": "Re-score failed", "text": "The recorded runs could not be scored. Use the command-line evaluator for details."}
        return
    same = summary == official["summary"]
    st.session_state["rescore_result"] = {
        "kind": "ok" if same else "warning", "label": "Reproduced" if same else "Differs from the saved report",
        "text": f"Agent end to end: {_tally(summary['agent'])}. SQL tool: {_tally(summary['sql_tool'])}. Calculation: {_tally(summary['calculation'])}. No request was sent."}


def _test_suite() -> None:
    tests = backend.report("tests")
    ui.head("Automated tests and data checks")
    integrity = backend.integrity()
    from portfolioiq.verification import CheckStatus

    if tests is None:
        ui.empty_state("No recorded test run", "Record one with:", "python scripts/record_test_results.py")
    else:
        ui.metrics([("Test cases passed", tests["passed"], f"of {tests['total']} collected"), ("Failed", tests["failed"], None),
                    ("Skipped", tests["skipped"], None), ("Recorded", tests["recorded_utc"][:16].replace("T", " ") + " UTC", f"Python {tests['python']}")], small=True)
        ui.table(["Category", "Passed", "Failed", "Test files"], [[c["category"], c["passed"], c["failed"], c["files"]] for c in tests["categories"]], numeric={1, 2, 3},
                 widths=["40%", "20%", "20%", "20%"])
        ui.footnote("Test cases are offline code tests with fakes for Gemini. They are not live questions", "Source: reports/test_results.json")
    ui.key_values([("Data integrity checks (recomputed now)", f"{len(integrity.checks)} checks: {integrity.count(CheckStatus.PASS)} PASS, "
                    f"{integrity.count(CheckStatus.WARNING)} WARNING, {integrity.count(CheckStatus.FAIL)} FAIL")])


def _latency(sql: dict) -> None:
    answered = [i for i in sql["items"] if i.get("llm_calls") and i.get("llm_latency_ms")]
    ui.head("Latency per answered SQL case", "Gemini and SQL execution on separate scales (never a dual axis)")
    left, right = st.columns(2, gap="large")
    with left:
        ui.show(ui.strip(pd.DataFrame([{"Case": i["id"], "Gemini ms": i["llm_latency_ms"]} for i in answered]), "Gemini ms", "Case", value_title="Gemini latency (ms)"))
    with right:
        frame = pd.DataFrame([{"Case": i["id"], "SQL ms": i["sql_latency_ms"]} for i in answered if i["sql_latency_ms"]])
        if not frame.empty:
            ui.show(ui.strip(frame, "SQL ms", "Case", value_title="SQL execution (ms)", colour=ui.METHOD_B))
    lat = sql["summary"]["latency_ms"]
    ui.footnote(f"Gemini median {lat['llm_median']} ms, p95 {lat['llm_p95']} ms; SQL median {lat['sql_median']} ms")
