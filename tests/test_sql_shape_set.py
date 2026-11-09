"""The frozen SQL result-shape set: verified references, no overlap with development material, strict scoring."""

import json

import pytest

from portfolioiq.config import PROJECT_ROOT
from portfolioiq.sql_engine import execute_sql
from portfolioiq.sql_tool import PROMPT_VARIANTS, SqlToolResult
from portfolioiq.verification import VerificationStatus
from portfolioiq_eval import __main__ as cli
from portfolioiq_eval import sql_eval

EVALUATION = PROJECT_ROOT / "evaluation"
PACKAGE = PROJECT_ROOT / "src" / "portfolioiq"


def _norm(text):
    return " ".join(text.lower().split())


@pytest.fixture(scope="module")
def cases():
    return json.loads(sql_eval.SHAPE_SET.read_text(encoding="utf-8"))["cases"]


def test_the_set_is_frozen_and_loads_as_the_shape_group():
    loaded = sql_eval.load_shape_cases()
    assert [c.id for c in loaded] == [f"S{n:02d}" for n in range(1, 18)]
    assert {c.group for c in loaded} == {"shape"}


def test_an_edited_set_is_refused(tmp_path, monkeypatch):
    edited = tmp_path / "sql_shape_set.json"
    edited.write_text(sql_eval.SHAPE_SET.read_text(encoding="utf-8").replace("S01", "S99", 1), encoding="utf-8")
    monkeypatch.setattr(sql_eval, "SHAPE_SET", edited)
    with pytest.raises(ValueError, match="frozen set was modified"):
        sql_eval.load_shape_cases()


def test_every_stored_expectation_equals_its_reference_result(cases, readonly_db):
    for case in cases:
        run = execute_sql(readonly_db, case["reference_sql"], max_rows=10_000)
        assert run.status == "ok", case["id"]
        assert len(run.columns) == len(case["expected"]["columns"]), case["id"]
        stored, actual = case["expected"]["rows"], run.rows
        assert len(stored) == len(actual) >= 1, case["id"]
        key = (lambda r: str(r))
        for a, b in zip(sorted(actual, key=key) if not case["ordered"] else actual,
                        sorted(stored, key=key) if not case["ordered"] else stored):
            for x, y in zip(a, b):
                assert x == pytest.approx(y, abs=0.005) if isinstance(x, float) else x == y, case["id"]


def test_no_question_is_shared_with_another_set_or_a_prompt_example(cases):
    mine = {_norm(c["question"]) for c in cases}
    assert len(mine) == len(cases)
    seen = set()
    for variant in PROMPT_VARIANTS.values():
        seen |= {_norm(e.question) for e in variant.examples}
    gold = json.loads((EVALUATION / "ground_truth_dataset.json").read_text(encoding="utf-8"))
    seen |= {_norm(q["question"]) for q in gold["questions"]}
    unseen = json.loads((EVALUATION / "unseen_sql_questions.json").read_text(encoding="utf-8"))
    seen |= {_norm(q["question"]) for q in unseen["questions"] + unseen["safety"]}
    for name in ("dev_validation", "heldout"):
        data = json.loads((EVALUATION / f"independent_benchmark_{name}.json").read_text(encoding="utf-8"))
        seen |= {_norm(c["question"]) for c in data["cases"]}
    assert not mine & seen, sorted(mine & seen)


def test_runtime_code_never_reads_the_set():
    for path in PACKAGE.glob("*.py"):
        assert "sql_shape_set" not in path.read_text(encoding="utf-8"), path.name


def test_the_set_is_scored_strictly_with_the_official_comparator(cases, readonly_db):
    case = next(c for c in cases if c["id"] == "S06")  # name and creation date of the active portfolios
    shape_case = sql_eval.Case(case["id"], "shape", case["question"], case["reference_sql"])

    def answer(sql):
        run = execute_sql(readonly_db, sql, max_rows=10_000)
        return SqlToolResult(question=case["question"], status="ok", message="", sql=sql, execution=run,
                             data_anchor_date="2023-10-09", context_version="test",
                             verification=VerificationStatus.RESULT_VALIDATED)

    exact = sql_eval._judge(shape_case, answer(case["reference_sql"]), readonly_db)
    assert exact["strict"] and exact["outcome"] == "correct"
    extra = sql_eval._judge(shape_case, answer(
        "SELECT portfolio_name, creation_date, status FROM portfolios WHERE status = 'Active'"), readonly_db)
    assert not extra["strict"] and extra["relaxed"] and extra["outcome"] == "relaxed_only"  # an extra column fails strictly
    assert sql_eval.verdict("shape", extra["outcome"], extra["passed"]) == "FAIL"


def test_the_shape_set_cannot_share_runs_or_reports_with_the_standard_evaluation(capsys):
    assert cli.main(["sql", "--set", "shape", "--rescore"]) == 2
    assert "its own --runs-dir and --reports-dir" in capsys.readouterr().out
