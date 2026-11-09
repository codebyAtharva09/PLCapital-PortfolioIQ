"""Write one immutable record of an evaluation experiment. Makes no API call and never reads a key.

    python scripts/write_evaluation_record.py --variant candidate-2.2 --label "Candidate v2.2" --dir reports/sprint_v2_2/candidate

Reads the experiment's own folder (reports, runs and usage ledger) and writes evaluation_record.{json,md} there, with a
SHA-256 manifest of every file in the folder. An existing record is never overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
EVALUATOR_FILES = ("src/portfolioiq_eval/comparator.py", "src/portfolioiq_eval/official.py", "src/portfolioiq_eval/sql_eval.py",
                   "evaluation/ground_truth_dataset.json", "evaluation/unseen_sql_questions.json",
                   "evaluation/sql_shape_set.json")


def sha256_text(path: Path) -> str:
    return hashlib.sha256(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")).hexdigest()


def main() -> int:
    from portfolioiq.config import load_settings
    from portfolioiq.semantic import SemanticContext
    from portfolioiq.sql_engine import data_fingerprint, ensure_reference_db
    from portfolioiq.sql_tool import sql_prompt_version, variant_for

    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    folder = args.dir if args.dir.is_absolute() else ROOT / args.dir
    target = folder / "evaluation_record.json"
    if target.exists():
        print(f"{target.relative_to(ROOT)} already exists; records are never overwritten.")
        return 2
    settings = load_settings()
    db_path = ensure_reference_db(settings.data_dir, settings.schema_path, settings.cache_dir)
    configuration = variant_for(args.variant)
    context = SemanticContext.from_db(db_path, examples=configuration.examples, latest_rule=configuration.latest_rule)
    ledger = [json.loads(line) for line in (folder / "api_usage.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    requests = [e for e in ledger if e.get("api_request")]
    latencies = sorted(e["latency_ms"] for e in requests if e.get("status") == "ok" and e.get("latency_ms") is not None)
    official = json.loads((folder / "official_evaluation.json").read_text(encoding="utf-8")) if (folder / "official_evaluation.json").exists() else None
    sql = json.loads((folder / "sql_evaluation.json").read_text(encoding="utf-8")) if (folder / "sql_evaluation.json").exists() else None
    agent_runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((folder / "runs").glob("*/run_meta.json"))]
    record = {
        "label": args.label, "note": args.note,
        "production_baseline": {"sql_prompt_version": "d5a914eb71d5", "official_end_to_end": "8/10",
                                "record": "reports/sprint_v2_1/baseline/ and reports/runs/"},
        "candidate": {"variant": args.variant, "sql_prompt_version": sql_prompt_version(context.text, configuration.instruction),
                      "instruction_sha256": hashlib.sha256(configuration.instruction.encode("utf-8")).hexdigest(),
                      "context_version": context.version, "shape_policy": configuration.shape_policy},
        "model": settings.gemini_model, "thinking_level": settings.gemini_thinking_level,
        "dataset_fingerprint": data_fingerprint(settings.data_dir, settings.schema_path),
        "evaluator_sha256": {name: sha256_text(ROOT / name) for name in EVALUATOR_FILES},
        "first_request_utc": requests[0]["timestamp_utc"] if requests else None,
        "last_request_utc": requests[-1]["timestamp_utc"] if requests else None,
        "runs": len(agent_runs),
        "api": {"requests": len(requests), "succeeded": sum(1 for e in requests if e.get("status") == "ok"),
                "failed": [f"{e.get('purpose')}: {e.get('status')}" for e in requests if e.get("status") != "ok"],
                "cache_hits": sum(1 for e in ledger if e.get("event") == "cache_hit"),
                "prompt_tokens": sum(e.get("prompt_tokens") or 0 for e in requests),
                "output_tokens": sum(e.get("output_tokens") or 0 for e in requests),
                "thinking_tokens": sum(e.get("thinking_tokens") or 0 for e in requests),
                "latency_ms": {"median": round(statistics.median(latencies), 1) if latencies else None,
                               "max": latencies[-1] if latencies else None}},
        "official_agent": {i["id"]: i["agent"]["verdict"] for i in official["items"]} if official else {},
        "official_summary": official["summary"]["agent"] if official else {},
        "sql_tool": {i["id"]: i["verdict"] for i in sql["items"]} if sql else {},
    }
    target.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    api = record["api"]
    lines = [f"# Evaluation record: {args.label}", "", args.note, "",
             f"- Variant `{args.variant}`; SQL prompt `{record['candidate']['sql_prompt_version']}`; instruction SHA-256 `{record['candidate']['instruction_sha256']}`; "
             f"context `{context.version}`; shape policy {configuration.shape_policy}.",
             f"- Production baseline for comparison: SQL prompt `d5a914eb71d5`, official end to end 8/10, kept in `reports/sprint_v2_1/baseline/`.",
             f"- Model `{record['model']}`, thinking {record['thinking_level']}. Dataset fingerprint `{record['dataset_fingerprint']}`.",
             f"- Requests {api['requests']} ({api['succeeded']} succeeded), cache hits {api['cache_hits']}; first {record['first_request_utc']}, last {record['last_request_utc']} UTC.",
             f"- Failed requests: {', '.join(api['failed']) or 'none'}.",
             f"- Tokens: prompt {api['prompt_tokens']:,}, output {api['output_tokens']:,}, thinking {api['thinking_tokens']:,}. Gemini latency median {api['latency_ms']['median']} ms, max {api['latency_ms']['max']} ms.",
             "", "| Case | Official agent (end to end) | SQL tool (strict) |", "|---|---|---|"]
    standard = [f"Q{i}" for i in range(1, 11)] + [f"U{i}" for i in range(1, 11)] + ["S1", "S2", "S3"]
    shape = sorted(c for c in record["sql_tool"] if re.fullmatch(r"S\d\d", c))  # the frozen result-shape set
    for case in (shape if shape and not any(c in record["sql_tool"] for c in standard) else standard):
        lines.append(f"| {case} | {record['official_agent'].get(case, '-')} | {record['sql_tool'].get(case, '-')} |")
    lines += ["", "Evaluator file hashes are in `evaluation_record.json`. Scores come from the unchanged comparator and ground truth."]
    (folder / "evaluation_record.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(folder.rglob("*")) if p.is_file() and p.name != "MANIFEST.sha256"}
    (folder / "MANIFEST.sha256").write_text("".join(f"{h}  {n}\n" for n, h in manifest.items()), encoding="utf-8")
    print(f"Wrote {target.relative_to(ROOT)}: {len(manifest)} files in the manifest; requests {api['requests']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
