import json

import pytest

from portfolioiq.config import PROJECT_ROOT, load_settings
from portfolioiq.db import create_reference_db
from portfolioiq.sql_engine import ensure_reference_db, read_schema


@pytest.fixture(scope="session")
def db():
    settings = load_settings()
    conn = create_reference_db(settings.data_dir, settings.schema_path)
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def readonly_db(tmp_path_factory):
    """The read-only database file queries run against, built in a temporary cache directory."""
    settings = load_settings()
    return ensure_reference_db(settings.data_dir, settings.schema_path, tmp_path_factory.mktemp("cache"))


@pytest.fixture(scope="session")
def schema(readonly_db):
    return read_schema(readonly_db)


@pytest.fixture(scope="session")
def reference_sql():
    """Supplied reference SQL for Q1-Q8 (test and evaluation code only, never runtime code)."""
    data = json.loads((PROJECT_ROOT / "evaluation" / "ground_truth_dataset.json").read_text(encoding="utf-8"))
    return {q["id"]: q["ground_truth"]["sql_query"] for q in data["questions"] if "sql_query" in q["ground_truth"]}
