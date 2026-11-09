"""Milestone 3: the unseen evaluation questions have verified reference answers and are truly unseen."""

import difflib
import json

import pytest

from portfolioiq.config import PROJECT_ROOT
from portfolioiq.semantic import VERIFIED_EXAMPLES
from portfolioiq.sql_engine import execute_sql
from portfolioiq.sql_guard import validate_sql

UNSEEN = json.loads((PROJECT_ROOT / "evaluation" / "unseen_sql_questions.json").read_text(encoding="utf-8"))
GOLD = json.loads((PROJECT_ROOT / "evaluation" / "ground_truth_dataset.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("item", UNSEEN["questions"], ids=lambda i: i["id"])
def test_reference_answer_is_valid_and_as_documented(schema, readonly_db, item):
    assert validate_sql(item["reference_sql"], schema).ok
    result = execute_sql(readonly_db, item["reference_sql"])
    assert result.status == "ok" and result.row_count == item["expected_rows"]
    if "expected_value" in item:
        assert result.rows[0][0] == pytest.approx(item["expected_value"])


def test_unseen_questions_are_neither_assessment_questions_nor_example_templates():
    others = [q["question"] for q in GOLD["questions"]] + [e.question for e in VERIFIED_EXAMPLES]
    for item in UNSEEN["questions"] + UNSEEN["safety"]:
        for other in others:
            similarity = difflib.SequenceMatcher(None, item["question"].lower(), other.lower()).ratio()
            assert similarity < 0.6, (item["id"], other, round(similarity, 2))


def test_safety_prompts_cover_writes_stacked_statements_and_system_tables():
    text = " ".join(s["question"] for s in UNSEEN["safety"]).lower()
    assert "delete" in text and "update" in text and "sqlite_master" in text
