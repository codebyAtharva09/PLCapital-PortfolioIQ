"""Write a timestamped manifest of the exact configuration an evaluation is about to use. Sends no request and changes no baseline.

    python scripts/freeze_eval_config.py
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import platform
import subprocess
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from portfolioiq.agent import PortfolioAgent  # noqa: E402
from portfolioiq.config import load_settings  # noqa: E402
from portfolioiq.replay import RecordedLLM  # noqa: E402

OUT_DIR = ROOT / "reports" / "final_verification"
FILES = {"sql_guard": "src/portfolioiq/sql_guard.py", "generated_sql_guard": "src/portfolioiq/sql_generated_guard.py", "sql_engine": "src/portfolioiq/sql_engine.py",
         "router": "src/portfolioiq/router.py", "comparator": "src/portfolioiq_eval/comparator.py", "official_evaluator": "src/portfolioiq_eval/official.py", "sql_evaluator": "src/portfolioiq_eval/sql_eval.py"}
QUESTION_SETS = ["evaluation/ground_truth_dataset.json", "evaluation/unseen_sql_questions.json"]
PACKAGES = ["google-genai", "pydantic", "sqlglot", "streamlit", "pandas", "httpx", "pytest"]


def digest(name: str) -> str:
    return hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    settings = load_settings()
    agent = PortfolioAgent.from_settings(dataclasses.replace(settings), llm=RecordedLLM(settings))
    now = dt.datetime.now(dt.UTC)
    manifest = {
        "created_utc": now.isoformat(timespec="seconds"), "git_commit": git("rev-parse", "HEAD"), "working_tree_clean": not git("status", "--porcelain"),
        "model": settings.gemini_model, "thinking_level": settings.gemini_thinking_level, "sql_prompt_variant": settings.sql_prompt_variant,
        "router_version": agent.router_version, "sql_prompt_version": agent.sql_tool.prompt_version, "context_version": agent.context.version,
        "file_sha256": {label: digest(path) for label, path in FILES.items()},
        "dataset_sha256": hashlib.sha256((ROOT / "data" / "SHA256SUMS").read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "question_set_sha256": {path: digest(path) for path in QUESTION_SETS},
        "python": platform.python_version(), "platform": platform.platform(), "packages": {name: metadata.version(name) for name in PACKAGES},
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"eval_config_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
