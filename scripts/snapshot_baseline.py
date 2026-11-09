"""Write evaluation/baseline_configuration.json: the frozen production baseline, recoverable and checkable.

    python scripts/snapshot_baseline.py          # writes the snapshot (refuses to overwrite an existing one)
    python scripts/snapshot_baseline.py --check  # compares the current code with the snapshot; exit 1 on drift

The snapshot holds the full baseline SQL instruction text (so it can be restored byte for byte), the three
version hashes recorded in every official run, and SHA-256 hashes of the pieces that must not change when a
candidate configuration is evaluated: the strict comparator, the SQL guard, the router instruction and the
evaluation data. Line endings are normalised before hashing so the check holds on any platform.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
SNAPSHOT = ROOT / "evaluation" / "baseline_configuration.json"
SECURITY_MANIFEST = ROOT / "evaluation" / "security_manifest.json"
HARDENED_FILES = ("src/portfolioiq/sql_engine.py", "src/portfolioiq/sql_guard.py")
HASHED_FILES = ["src/portfolioiq_eval/comparator.py", "src/portfolioiq/sql_guard.py", "src/portfolioiq/sql_engine.py",
                "evaluation/ground_truth_dataset.json", "evaluation/unseen_sql_questions.json",
                "evaluation/agent_integration_questions.json", "evaluation/reliability_scenarios.json"]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def shown(path: Path) -> Path:
    return path.relative_to(ROOT) if path.is_relative_to(ROOT) else path


def current() -> dict:
    from portfolioiq.agent import PortfolioAgent
    from portfolioiq.config import load_settings
    from portfolioiq.replay import RecordedLLM
    from portfolioiq.router import ROUTER_INSTRUCTION
    from portfolioiq.sql_tool import PROMPT_VARIANTS

    settings = dataclasses.replace(load_settings(), sql_prompt_variant="baseline")  # always the baseline, whatever is active
    agent = PortfolioAgent.from_settings(settings, llm=RecordedLLM(settings))  # offline: no request can be sent
    instruction = PROMPT_VARIANTS["baseline"].instruction
    return {
        "purpose": "Frozen production baseline used for every official Gemini 3.6 Flash evaluation. A candidate "
                   "configuration must leave all of this unchanged; compare with scripts/snapshot_baseline.py --check.",
        "model": "gemini-3.6-flash", "sdk": "google-genai 2.34.0", "thinking_level": "LOW",
        "versions": {"router": agent.router_version, "sql_prompt": agent.sql_tool.prompt_version,
                     "context": agent.context.version},
        "sql_instruction_sha256": sha256_text(instruction), "sql_instruction_text": instruction,
        "router_instruction_sha256": sha256_text(ROUTER_INSTRUCTION),
        "semantic_context_sha256": sha256_text(agent.context.text),
        "file_sha256": {name: sha256_text((ROOT / name).read_text(encoding="utf-8")) for name in HASHED_FILES},
    }


def drift(now: dict, saved: dict, manifest: dict | None) -> list[str]:
    """What differs from the frozen baseline. A hardened file counts only if it is the exact version the security manifest records."""
    allowed = (manifest or {}).get("files", {})
    found = [k for k in now if k not in ("purpose", "file_sha256") and now[k] != saved.get(k)]
    for name, digest in now["file_sha256"].items():
        record = allowed.get(name, {})
        expected = saved["file_sha256"].get(name)
        if digest != expected and not (record.get("baseline_sha256") == expected and record.get("sha256") == digest):
            found.append(name)
    return found


def write_security_manifest(saved: dict) -> None:
    from portfolioiq.sql_engine import MAX_RESULT_BYTES, MAX_VM_STEPS, SQLITE_LIMITS

    manifest = {
        "purpose": "Records the SQL security hardening made after the frozen baseline. The historical hashes in baseline_configuration.json are not rewritten.",
        "changes": ["SQLite resource limits on every read-only connection", "Bounded total result size", "Virtual-machine step budget beside the time limit",
                    "Table-scoped column validation through aliases, joins, CTEs and subqueries"],
        "limits": {"max_result_bytes": MAX_RESULT_BYTES, "max_vm_steps": MAX_VM_STEPS, "sqlite": {str(k): v for k, v in SQLITE_LIMITS.items()}},
        "files": {name: {"baseline_sha256": saved["file_sha256"][name], "sha256": sha256_text((ROOT / name).read_text(encoding="utf-8"))} for name in HARDENED_FILES},
    }
    SECURITY_MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {shown(SECURITY_MANIFEST)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write-security-manifest", action="store_true")
    args = parser.parse_args()
    if args.write_security_manifest:
        write_security_manifest(json.loads(SNAPSHOT.read_text(encoding="utf-8")))
        return 0
    now = current()
    if args.check:
        saved = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        manifest = json.loads(SECURITY_MANIFEST.read_text(encoding="utf-8")) if SECURITY_MANIFEST.exists() else None
        problems = drift(now, saved, manifest)
        print("Baseline unchanged (hardened files match the security manifest)." if not problems else f"Baseline DRIFT in: {', '.join(problems)}")
        return 1 if problems else 0
    if SNAPSHOT.exists():
        print(f"{shown(SNAPSHOT)} already exists; the baseline snapshot is never overwritten.")
        return 2
    SNAPSHOT.write_text(json.dumps(now, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {shown(SNAPSHOT)}: {now['versions']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
