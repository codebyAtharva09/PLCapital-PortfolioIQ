"""Milestone 3: the semantic layer is accurate, data-driven and free of ground-truth leakage."""

import difflib
import json
import re

import pytest

from portfolioiq.config import PROJECT_ROOT
from portfolioiq.semantic import VERIFIED_EXAMPLES, SemanticContext
from portfolioiq.sql_engine import execute_sql
from portfolioiq.sql_guard import validate_sql

GROUND_TRUTH = json.loads((PROJECT_ROOT / "evaluation" / "ground_truth_dataset.json").read_text(encoding="utf-8"))


def squash(text):
    return re.sub(r"\s+", " ", text).strip().lower().rstrip(";")


@pytest.fixture(scope="module")
def context(readonly_db):
    return SemanticContext.from_db(readonly_db)


def test_context_carries_the_data_driven_facts(context):
    text = context.text
    assert "'Active', 'Passive'" in text and "'High', 'Low', 'Medium'" in text
    assert "'Growth Equity Fund'" in text and "NVDA = NVIDIA Corporation (Stock)" in text
    assert "portfolios.benchmark_index = benchmarks.benchmark_symbol" in text
    assert "1.7% to 14.7% of stated AUM" in text  # computed from the data (D4)
    assert "transactions.transaction_date 2023-10-09" in text and "holdings.purchase_date 2023-09-25" in text
    assert "contain only portfolio_id 1, 2, 3, 4" in text
    assert "fraction of the portfolio" in text and "without converting, annualising" in text  # D1, D7
    assert context.aum_coverage == pytest.approx((0.0168, 0.1472), abs=0.0005)
    assert len(context.version) == 12


@pytest.mark.parametrize("example", VERIFIED_EXAMPLES, ids=lambda e: e.id)
def test_verified_examples_pass_the_guard_and_return_rows(context, readonly_db, example):
    validation = validate_sql(example.sql, context.schema)
    assert validation.ok, validation.reason
    result = execute_sql(readonly_db, example.sql)
    assert result.status == "ok" and result.row_count > 0


def test_no_ground_truth_question_or_sql_reaches_the_prompt(context):
    prompt = squash(context.text)
    for question in GROUND_TRUTH["questions"]:
        assert squash(question["question"]) not in prompt, question["id"]
        reference = question["ground_truth"].get("sql_query")
        if reference:
            assert squash(reference) not in prompt, question["id"]


def test_verified_examples_are_not_paraphrases_of_assessment_questions():
    for example in VERIFIED_EXAMPLES:
        for question in GROUND_TRUTH["questions"]:
            similarity = difflib.SequenceMatcher(None, example.question.lower(), question["question"].lower()).ratio()
            assert similarity < 0.6, (example.id, question["id"], round(similarity, 2))
