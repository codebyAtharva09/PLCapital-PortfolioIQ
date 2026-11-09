"""PortfolioIQ evaluation commands.

    python -m portfolioiq_eval sql                     # ask Gemini only for unanswered cases, then score all runs
    python -m portfolioiq_eval sql --rerun Q5          # also re-ask Q5, which already has an answer
    python -m portfolioiq_eval sql --max-requests 15   # never send more than 15 API requests in this run
    python -m portfolioiq_eval sql --rescore           # score every recorded run again; no API calls
    python -m portfolioiq_eval agent                   # agent integration questions (M4), live for unanswered ones
    python -m portfolioiq_eval agent --rescore         # score recorded agent receipts; no API calls
    python -m portfolioiq_eval official --rescore      # Q1-Q10 (ground truth) on every evidence layer; no API calls
    python -m portfolioiq_eval official --max-requests 20   # live: Q1-Q10 through the agent (needs Gemini quota)
    python -m portfolioiq_eval benchmark               # 19 reliability scenarios: deterministic checks; no API calls
    python -m portfolioiq_eval usage --since 2026-11-07T08:00:00+00:00   # API usage from the ledger; no API calls
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from portfolioiq.config import PROJECT_ROOT, load_settings
from portfolioiq_eval import agent_eval, benchmark, independent, official, semantic_compare, sql_eval, stability, usage

REPORTS = PROJECT_ROOT / "reports"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="portfolioiq_eval", description="PortfolioIQ evaluation")
    commands = parser.add_subparsers(dest="command", required=True)
    sql = commands.add_parser("sql", help="evaluate the SQL Query Tool on Q1-Q8, unseen and safety questions")
    sql.add_argument("--rescore", type=Path, nargs="*",
                     help="score recorded sql_outputs.jsonl files (default: every recorded run); no API calls")
    _live_options(sql)
    sql.add_argument("--fresh", action="store_true", help="ignore recorded answers and ask every selected case")
    sql.add_argument("--set", choices=("standard", "shape"), default="standard",
                     help="'shape' runs the frozen 17-question result-shape set instead of Q1-Q8, unseen and safety; "
                          "it needs its own --runs-dir and --reports-dir")
    agent = commands.add_parser("agent", help="run the agent integration questions through the full agent")
    agent.add_argument("--rescore", action="store_true", help="score recorded receipts only; no API calls")
    _live_options(agent)
    official_cmd = commands.add_parser("official", help="Q1-Q10 from ground_truth_dataset.json through the agent")
    official_cmd.add_argument("--rescore", action="store_true",
                              help="score recorded receipts and the deterministic layers only; no API calls")
    official_cmd.add_argument("--replay-check", action="store_true",
                              help="re-run each recorded question offline from the saved Gemini responses and "
                                   "compare with the live receipt; no API calls, nothing is recorded")
    _live_options(official_cmd)
    stab = commands.add_parser("stability", help="R18: Q4, Q7, Q10, R01, R09 asked several times, cache off")
    stab.add_argument("--rescore", action="store_true", help="score recorded rounds only; no API calls")
    stab.add_argument("--rounds", type=_positive, default=3, help="rounds to run (default 3)")
    _live_options(stab)
    ind = commands.add_parser("independent", help="the independent 51-case reliability benchmark "
                                                  "(development, validation and held-out splits)")
    ind.add_argument("--split", nargs="+", choices=independent.SPLITS, default=["dev"],
                     help="which splits to run or score (default dev); never tune a prompt on 'test'")
    ind.add_argument("--rescore", action="store_true", help="score recorded receipts only; no API calls")
    ind.add_argument("--plan", action="store_true", help="print the request budget for these splits and exit; no API calls")
    ind.add_argument("--confirm-heldout", action="store_true",
                     help="required for a live run that includes the held-out 'test' split")
    _live_options(ind)
    diagnosis = commands.add_parser("diagnose", help="supplemental column-level diagnosis of recorded SQL results; not the "
                                                     "official score; no API calls")
    diagnosis.add_argument("--runs-dir", type=Path, default=REPORTS / "runs")
    diagnosis.add_argument("--reports-dir", type=Path, required=True, help="where to write the diagnosis")
    diagnosis.add_argument("--label", default="recorded runs")
    commands.add_parser("benchmark", help="the 19 reliability scenarios: deterministic checks plus recorded live "
                                          "results; no API calls")
    spent = commands.add_parser("usage", help="summarise API usage from the ledger; no API calls")
    spent.add_argument("--since", help="ISO time, for example 2026-11-07T08:00:00+00:00 (Pacific midnight)")
    spent.add_argument("--until", help="ISO time; default: now")
    args = parser.parse_args(argv)
    settings = load_settings()
    if args.command == "usage":
        return _usage(args, settings)
    if args.command == "official":
        return _official(args, settings)
    if args.command == "diagnose":
        report = semantic_compare.run(settings, args.runs_dir, args.reports_dir, args.label)
        print(f"Supplemental diagnosis ({args.label}): exact shape {report['exact_shape']}, semantic {report['semantic']}, "
              f"of {report['evaluated']}. Not the official score. No request was sent.")
        return 0
    if args.command == "independent":
        return _independent(args, settings)
    if args.command == "benchmark":
        return _benchmark(settings)
    if args.command == "stability":
        return _stability(args, settings)
    return _agent(args, settings) if args.command == "agent" else _sql(args, settings)


def _live_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--only", nargs="*", help="limit a live run to these case ids, for example Q7 U2")
    parser.add_argument("--rerun", nargs="*", default=[], help="re-ask these case ids even if they have an answer")
    parser.add_argument("--min-interval", type=float, default=13.0,
                        help="seconds between Gemini requests (default 13; the free tier allows 5 per minute)")
    parser.add_argument("--max-requests", type=_positive, default=None,
                        help="hard cap on the API requests this run may send, retries and failed requests "
                             "included; cases beyond it are reported as NOT EVALUATED")
    parser.add_argument("--runs-dir", type=Path, default=REPORTS / "runs",
                        help="where runs are recorded and read (default reports/runs); a separately labelled "
                             "evaluation, for example with another model, must use its own directory")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS, help="where reports are written (default reports)")


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _pending(cases, args, answered: set[str]) -> tuple[list, set[str]]:
    selected = [c for c in cases if not args.only or c.id in set(args.only)]
    skip = answered - set(args.rerun)
    pending = [c.id for c in selected if c.id not in skip]
    cap = f"; request cap {args.max_requests}" if args.max_requests else ""
    print(f"Cases to ask Gemini: {', '.join(pending) or 'none'} ({len(pending)}); "
          f"skipped with a recorded answer: {len([c for c in selected if c.id in skip])}{cap}")
    return (selected if pending else []), skip


def live_prompt_version(settings) -> str:
    """The SQL prompt version a live run with these settings would send: the chosen instruction variant plus the
    context. The baseline gives d5a914eb71d5."""
    from portfolioiq.semantic import SemanticContext
    from portfolioiq.sql_engine import ensure_reference_db
    from portfolioiq.sql_tool import sql_prompt_version, variant_for

    db_path = ensure_reference_db(settings.data_dir, settings.schema_path, settings.cache_dir)
    configuration = variant_for(settings.sql_prompt_variant)
    return sql_prompt_version(SemanticContext.from_db(db_path, examples=configuration.examples, latest_rule=configuration.latest_rule).text, configuration.instruction)


def mixed_models(outputs: list[Path], configured: str | None = None, configured_prompt: str | None = None) -> str | None:
    """Why recorded runs must not be scored together, or None.

    Answers from different models, or from different SQL prompt versions, are never merged: the evaluator keeps
    the latest valid answer per case, so a candidate run in the same folder would silently replace baseline
    answers. Runs recorded before prompt versions existed (no version in their metadata) are the baseline.
    """
    models, prompts = set(), set()
    for path in outputs:
        meta_path = path.parent / "run_meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("model"):
                models.add(meta["model"])
            version = meta.get("prompt_version") or meta.get("sql_prompt_version")
            if version:
                prompts.add(version)
    if configured and models and configured not in models:
        models.add(configured)  # a live run would add answers from a different model
    if configured_prompt and prompts and configured_prompt not in prompts:
        prompts.add(configured_prompt)  # a live run would add answers from a different SQL prompt version
    problems = []
    if len(models) > 1:
        problems.append(f"more than one model ({', '.join(sorted(models))})")
    if len(prompts) > 1:
        problems.append(f"more than one SQL prompt version ({', '.join(sorted(prompts))})")
    if problems:
        return ("Recorded runs come from " + " and ".join(problems) + ". They are not scored together, because a "
                "later answer would replace an earlier one. Keep each model's and each prompt version's runs in "
                "its own --runs-dir and --reports-dir.")
    return None


def _refuse(reason: str) -> int:
    print(f"Not scored: {reason}")
    return 2


def _sql(args, settings) -> int:
    shape = args.set == "shape"
    if shape and (args.runs_dir == REPORTS / "runs" or args.reports_dir == REPORTS):
        return _refuse("the result-shape set must use its own --runs-dir and --reports-dir, so that its answers never "
                       "mix with the official and unseen runs or replace their reports")
    cases = sql_eval.load_shape_cases() if shape else sql_eval.load_cases()
    runs_dir = getattr(args, "runs_dir", REPORTS / "runs")
    if args.rescore is not None:
        outputs = args.rescore or sql_eval.recorded_outputs(runs_dir)
    else:
        existing = sql_eval.recorded_outputs(runs_dir)
        reason = mixed_models(existing, settings.gemini_model, live_prompt_version(settings))
        if reason:
            return _refuse(reason)
        answered = set() if args.fresh else sql_eval.answered_case_ids(existing)
        selected, skip = _pending(cases, args, answered)
        if selected:
            sql_eval.run(settings, selected, args.min_interval, skip=skip, max_requests=args.max_requests,
                         runs_dir=runs_dir)
        outputs = sql_eval.recorded_outputs(runs_dir)
    reason = mixed_models(outputs)
    if reason:
        return _refuse(reason)
    report = sql_eval.score(outputs, settings, cases=cases)
    paths = sql_eval.write_reports(report, args.reports_dir)
    s = report["summary"]
    if shape:
        acc = s["shape_execution_accuracy"]
        print(f"\nResult-shape set strict {acc['strict']}/{acc['evaluated']} evaluated ({acc['total']} in set); "
              f"relaxed {acc['relaxed']}; database unchanged: {s['database_unchanged']}")
        print("Reports: " + ", ".join(str(p) for p in paths))
        return 0
    official, unseen, safety = s["official_execution_accuracy"], s["unseen_execution_accuracy"], s["safety_refusals"]
    print(f"\nOfficial strict {official['strict']}/{official['evaluated']} evaluated ({official['total']} in set); "
          f"unseen strict {unseen['strict']}/{unseen['evaluated']} evaluated ({unseen['total']} in set); "
          f"safety refused {safety['refused']}/{safety['evaluated']} evaluated ({safety['total']} in set); "
          f"database unchanged: {s['database_unchanged']}")
    print("Reports: " + ", ".join(str(p) for p in paths))
    return 0


def _agent(args, settings) -> int:
    cases = agent_eval.load_cases()
    existing = agent_eval.recorded_outputs(args.runs_dir)
    reason = mixed_models(existing, None if args.rescore else settings.gemini_model,
                          None if args.rescore else live_prompt_version(settings))
    if reason:
        return _refuse(reason)
    if not args.rescore:
        selected, skip = _pending(cases, args, agent_eval.answered_case_ids(existing))
        if selected:
            agent_eval.run(settings, selected, args.min_interval, skip=skip, max_requests=args.max_requests,
                           runs_dir=args.runs_dir)
    report = agent_eval.score(agent_eval.recorded_outputs(args.runs_dir), cases)
    paths = agent_eval.write_reports(report, args.reports_dir)
    s = report["summary"]
    print(f"\nBehaved as expected: {s['as_expected']}/{s['evaluated']} evaluated ({s['total']} in set); "
          f"API requests {s['api_requests']}, cache hits {s['cache_hits']}")
    print("Reports: " + ", ".join(str(p) for p in paths))
    return 0


def _official(args, settings) -> int:
    cases = official.load_cases()
    existing = official.recorded_outputs(args.runs_dir)
    if args.replay_check:
        reason = mixed_models(existing, None, live_prompt_version(settings))
        if reason:
            return _refuse(f"{reason} Replay must use the runs of the active configuration: pass its --runs-dir, or set "
                           "PORTFOLIOIQ_SQL_PROMPT to the configuration those runs used.")
        report = official.replay_check(settings, existing, cases)
        for item in report["items"]:
            detail = "identical" if item["identical"] else (", ".join(item["differences"]) or item["state"])
            print(f"{item['id']:4} {item['state']:12} {detail}")
        s = report["summary"]
        print(f"Replay reproduces the live receipt for {s['identical']} of {s['compared']} compared "
              f"({s['total']} questions). No request was sent.")
        return 0 if s["identical"] == s["compared"] else 1
    sql_outputs = sql_eval.recorded_outputs(args.runs_dir)
    reason = mixed_models(existing + sql_outputs, None if args.rescore else settings.gemini_model,
                          None if args.rescore else live_prompt_version(settings))
    if reason:
        return _refuse(reason)
    if not args.rescore:
        selected, skip = _pending(cases, args, official.answered_case_ids(existing))
        if selected:
            official.run(settings, selected, args.min_interval, skip=skip, max_requests=args.max_requests,
                         runs_dir=args.runs_dir)
    report = official.score(official.recorded_outputs(args.runs_dir), cases, settings, sql_outputs=sql_outputs)
    paths = official.write_reports(report, args.reports_dir)
    s = report["summary"]
    print(f"\nAgent end to end: {_tally(s['agent'])} (routing {s['agent']['routing_correct']}/"
          f"{s['agent']['routing_evaluated']} evaluated); SQL tool Q1-Q8: {_tally(s['sql_tool'])}; "
          f"calculation Q9-Q10: {_tally(s['calculation'])}")
    print("Reports: " + ", ".join(str(p) for p in paths))
    return 0


def _independent(args, settings) -> int:
    splits = tuple(args.split)
    cases = independent.load_cases(splits)
    budget = independent.request_budget(cases)
    if args.plan:
        print(f"Independent benchmark, splits {', '.join(splits)}: {budget['cases']} cases {budget['by_kind']}; "
              f"about {budget['minimum']} requests at minimum, {budget['maximum']} at most with retries and repairs "
              f"(free tier: 20 a day for gemini-3.6-flash). No request was sent.")
        return 0
    existing = independent.recorded_outputs(args.runs_dir)
    reason = mixed_models(existing, None if args.rescore else settings.gemini_model,
                          None if args.rescore else live_prompt_version(settings))
    if reason:
        return _refuse(reason)
    if not args.rescore:
        if "test" in splits and not args.confirm_heldout:
            print("Refused: the held-out 'test' split needs --confirm-heldout. Run it once, after the prompt and "
                  "configuration are final; never tune on it.")
            return 2
        chosen, skip = _pending([agent_eval.AgentCase(c["id"], c["question"], {}) for c in cases], args,
                                agent_eval.answered_case_ids(existing))
        selected = [c for c in cases if c["id"] in {s.id for s in chosen}]
        if selected:
            independent.run(settings, selected, args.min_interval, runs_dir=args.runs_dir, skip=skip,
                            max_requests=args.max_requests, tag="+".join(splits))
            existing = independent.recorded_outputs(args.runs_dir)
    report = independent.score(existing, cases)
    paths = independent.write_reports(report, args.reports_dir, name="independent_benchmark_" + "_".join(splits))
    s = report["summary"]
    print(f"\nIndependent benchmark ({', '.join(splits)}): {_tally(s['verdicts'])}; "
          f"end to end {s['tracks']['end_to_end']['correct']}/{s['tracks']['end_to_end']['evaluated']}; "
          f"silent failures {s['silent_failures']}; requests {s['api_requests']}")
    print("Reports: " + ", ".join(str(p) for p in paths))
    return 0


def _stability(args, settings) -> int:
    cases = stability.load_cases()
    existing = stability.recorded_outputs(args.runs_dir)
    reason = mixed_models(existing, None if args.rescore else settings.gemini_model,
                          None if args.rescore else live_prompt_version(settings))
    if reason:
        return _refuse(reason)
    if not args.rescore:
        budget = args.max_requests
        for number in range(1, args.rounds + 1):
            if budget is not None and budget <= 0:
                print(f"Round {number}: request budget used up; stopping.")
                break
            print(f"Round {number} of {args.rounds}")
            outputs = stability.run_round(settings, cases, args.min_interval, runs_dir=args.runs_dir,
                                          max_requests=budget)
            meta = json.loads((outputs.parent / "run_meta.json").read_text(encoding="utf-8"))
            if budget is not None:
                budget -= meta.get("requests_sent") or 0
            if meta.get("stopped"):
                print(f"Stopped in round {number}: {meta['stopped']['kind']}. No further rounds.")
                break
            lines = [line for line in outputs.read_text(encoding="utf-8").splitlines() if line.strip()]
            if lines and agent_eval.is_api_failure(json.loads(lines[-1])["receipt"]):
                print(f"Round {number} ended with an API failure; stopping so failures never run back to back.")
                break
    report = stability.score(stability.recorded_outputs(args.runs_dir), cases)
    paths = stability.write_reports(report, args.reports_dir)
    s = report["summary"]
    print(f"\nStability: {_tally(s['verdicts'])}; mean consistency {s['consistency_rate']}; requests {s['api_requests']}")
    print("Reports: " + ", ".join(str(p) for p in paths))
    return 0


def _benchmark(settings) -> int:
    report = benchmark.run(settings)
    paths = benchmark.write_reports(report)
    s = report["summary"]
    print(f"Reliability benchmark, {s['scenarios']} scenarios. Deterministic components: {_tally(s['component'])}. "
          f"Live end to end: {_tally(s['live'])}. Database unchanged: {s['database_unchanged']}")
    print("Reports: " + ", ".join(str(p) for p in paths))
    return 0


def _tally(counts: dict) -> str:
    return ", ".join(f"{k} {v}" for k, v in counts.items()
                     if k in ("PASS", "FAIL", "REFUSED", "NOT EVALUATED", "N/A") and v) or "none"


def _usage(args, settings) -> int:
    if settings.api_usage_log is None:
        print("The usage ledger is disabled.")
        return 1
    print(usage.render(usage.summarise(settings.api_usage_log, since=args.since, until=args.until)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
