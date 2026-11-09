"""Run the offline test suite and save a summary for the Evaluation & Evidence page.

    python scripts/record_test_results.py

Writes reports/test_results.json from pytest's own JUnit output, so the figures shown in the app are from a real
run with a timestamp. Exit code is pytest's.
"""

from __future__ import annotations

import datetime as dt
import json
import platform
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "reports" / "test_results.json"
CATEGORIES = {
    "Security": {"test_sql_guard", "test_sql_engine", "test_sql_hardening", "test_no_secrets", "test_architecture"},
    "Independent verification": {"test_financial_verification"},
    "Data integrity": {"test_db", "test_invariants", "test_data_report"},
    "Regression and evaluation": {"test_reference_sql", "test_unseen_questions", "test_prompt_variants", "test_comparator", "test_sql_eval",
                                  "test_official_eval", "test_agent_eval", "test_stability", "test_independent_benchmark", "test_usage",
                                  "test_sql_policy", "test_sql_policy_v24", "test_sql_shape_set",
                                  "test_semantic_compare"},
    "Integration": {"test_agent", "test_sql_tool", "test_cli", "test_cli_ask", "test_cli_sql", "test_cli_smoke", "test_replay", "test_streamlit_app",
                    "test_streamlit_reviewer"},
    "Unit": {"test_config", "test_dates", "test_exposure", "test_formatting", "test_gemini_check", "test_latency", "test_llm", "test_resolver", "test_router",
             "test_semantic"},
}


def category_of(module: str) -> str:
    return next((name for name, modules in CATEGORIES.items() if module in modules), "Other")


def summarise(root: ElementTree.Element, python: str, recorded: str) -> dict:
    tallies: dict[str, dict] = {}
    totals = {"passed": 0, "failed": 0, "skipped": 0}
    for case in root.iter("testcase"):
        module = (case.get("classname") or "").split(".")[1] if "." in (case.get("classname") or "") else (case.get("classname") or "")
        outcome = "failed" if case.find("failure") is not None or case.find("error") is not None else "skipped" if case.find("skipped") is not None else "passed"
        entry = tallies.setdefault(category_of(module), {"passed": 0, "failed": 0, "skipped": 0, "modules": set()})
        entry[outcome] += 1
        entry["modules"].add(module)
        totals[outcome] += 1
    order = [*CATEGORIES, "Other"]
    categories = [{"category": name, "passed": tallies[name]["passed"], "failed": tallies[name]["failed"], "skipped": tallies[name]["skipped"],
                   "files": len(tallies[name]["modules"])} for name in order if name in tallies]
    return {"recorded_utc": recorded, "python": python, "total": sum(totals.values()), **totals, "categories": categories}


def main() -> int:
    with tempfile.TemporaryDirectory() as folder:
        xml_path = Path(folder) / "junit.xml"
        code = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junit-xml={xml_path}"], cwd=ROOT).returncode
        if not xml_path.exists():
            print("pytest produced no results; nothing was recorded.")
            return code or 1
        summary = summarise(ElementTree.parse(xml_path).getroot(), platform.python_version(),
                            dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    OUTPUT.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Recorded {summary['passed']} passed, {summary['failed']} failed, {summary['skipped']} skipped in {OUTPUT.relative_to(ROOT)}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
