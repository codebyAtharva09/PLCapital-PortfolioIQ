"""Milestone 3: the CLI sql command renders the SQL tool's result and uses distinct exit codes."""

import json

import pytest

from portfolioiq import cli
from portfolioiq.llm import LLMCall, LLMError, LLMResult
from portfolioiq.semantic import SemanticContext
from portfolioiq.sql_tool import SqlDraft, SqlTool


class ScriptedLLM:
    model = "fake-model"

    def __init__(self, item):
        self.item = item

    def generate(self, *, system, prompt, schema, purpose=""):
        if isinstance(self.item, Exception):
            raise self.item
        return LLMResult(value=self.item, call=LLMCall(model="fake-model", sdk_version="test", latency_ms=5.0, attempts=1))


@pytest.fixture
def use(monkeypatch, readonly_db):
    context = SemanticContext.from_db(readonly_db)

    def install(item):
        monkeypatch.setattr(cli, "build_sql_tool", lambda: SqlTool(readonly_db, context, ScriptedLLM(item)))
    return install


def draft(sql, explanation="Counts every portfolio — including passive ones.", answerable=True, reason="none"):
    return SqlDraft(answerable=answerable, decline_reason=reason, sql=sql, explanation=explanation)


def test_sql_command_shows_answer_sql_result_and_verification(use, capsys):
    use(draft("SELECT COUNT(*) AS portfolio_count FROM portfolios"))
    code = cli.main(["sql", "How many portfolios do we have?"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Status: OK, 1 row(s)" in out and "SELECT COUNT(*) AS portfolio_count FROM portfolios" in out
    assert "portfolio_count" in out and "13" in out
    assert "Verification: RESULT_VALIDATED" in out
    assert "including passive ones" in out and out.isascii()  # model text made ASCII-safe


def test_sql_command_json_output(use, capsys):
    use(draft("SELECT portfolio_name FROM portfolios WHERE status = 'Passive'"))
    code = cli.main(["sql", "Which portfolios are passive?", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert code == 0 and data["status"] == "ok" and data["execution"]["row_count"] == 3


@pytest.mark.parametrize("item, code", [
    (draft("DELETE FROM holdings"), 3),
    (draft("", "This would change data.", answerable=False, reason="modifies_data"), 3),
    (draft("", "Not in the database.", answerable=False, reason="not_in_data"), 2),
    (LLMError("unavailable", "Gemini API error 503 UNAVAILABLE: high demand"), 4),
])
def test_sql_command_exit_codes(use, capsys, item, code):
    use(item)
    assert cli.main(["sql", "Some question"]) == code
    assert "Status:" in capsys.readouterr().out
