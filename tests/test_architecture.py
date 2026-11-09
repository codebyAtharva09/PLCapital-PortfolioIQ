"""Architecture guards: the calculation backend stays independent of the UI and the LLM, and
runtime code never reads evaluation material (ground truth or fixtures)."""

import re

from portfolioiq.config import PROJECT_ROOT

PACKAGE = PROJECT_ROOT / "src" / "portfolioiq"
ENGINE_MODULES = ["exposure", "resolver", "verification", "formatting", "data_notes", "cli", "sql_guard",
                  "sql_engine", "dates", "semantic", "sql_tool", "router", "receipt", "agent"]
GEMINI_MODULES = {"llm.py", "gemini_check.py"}  # the only modules allowed to import the Gemini SDK


def test_runtime_never_imports_the_evaluation_package():
    for path in PACKAGE.glob("*.py"):
        assert "portfolioiq_eval" not in path.read_text(encoding="utf-8"), path.name


def test_only_the_llm_modules_import_the_gemini_sdk():
    for path in PACKAGE.glob("*.py"):
        if path.name not in GEMINI_MODULES:
            assert not any("google" in line for line in _imports(path.stem)), path.name


def _imports(module):
    source = (PACKAGE / f"{module}.py").read_text(encoding="utf-8")
    return [line for line in source.splitlines() if re.match(r"\s*(import|from)\s", line)]


def test_engine_does_not_depend_on_streamlit_or_gemini():
    for module in ENGINE_MODULES:
        offending = [line for line in _imports(module) if "streamlit" in line or "google" in line]
        assert not offending, f"{module} imports {offending}"


EVALUATION_REFERENCE = re.compile(r"""["']evaluation["']|evaluation[/\\]|ground_truth|fixtures""", re.I)


def test_leakage_guard_detects_references_but_not_ordinary_words():
    assert EVALUATION_REFERENCE.search('PROJECT_ROOT / "evaluation" / "ground_truth_dataset.json"')
    assert EVALUATION_REFERENCE.search("open('evaluation/fixtures/q10.json')")
    assert not EVALUATION_REFERENCE.search("# a pinned model keeps evaluation runs reproducible")


def test_runtime_code_never_reads_evaluation_material():
    for path in PACKAGE.glob("*.py"):
        match = EVALUATION_REFERENCE.search(path.read_text(encoding="utf-8"))
        assert match is None, f"{path.name} references evaluation material: {match.group(0)!r}"
