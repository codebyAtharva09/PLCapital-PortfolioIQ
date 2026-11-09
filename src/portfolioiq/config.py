"""Runtime settings, read from environment variables (and an optional .env file).

Real environment variables (e.g. a Windows user variable) take precedence over .env.
The API key is excluded from the settings' repr, so printing settings never shows it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# gemini-3.6-flash is marked "Stable" on Google's model list (checked 2026-11-06).
# gemini-3.8-flash is newer and generally available, but returned 503/504 ("high demand")
# on all three attempts on 2026-11-06; set GEMINI_MODEL to use it instead.
# A pinned ID (not a "-latest" alias) keeps evaluation runs reproducible.
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"

# Values that mean "no key configured": empty, or the template's placeholder text.
PLACEHOLDER_API_KEYS = frozenset({"", "your_gemini_api_key_here"})


DEFAULT_THINKING_LEVEL = "LOW"  # GEMINI_THINKING_LEVEL; "DEFAULT" leaves the model's own setting

# The active production SQL configuration, promoted after its live evaluation (reports/sprint_v2_3/PROMOTION_EVIDENCE.md).
# PORTFOLIOIQ_SQL_PROMPT=baseline rolls back to the original configuration.
DEFAULT_SQL_PROMPT_VARIANT = "candidate-2.3"
# Where DEMO / FIXTURE MODE finds the production configuration's recorded Gemini responses, besides the usage ledger.
PRODUCTION_RECORDINGS = (PROJECT_ROOT / "reports" / "sprint_v2_3" / "candidate" / "api_usage.jsonl",
                         PROJECT_ROOT / "reports" / "production_v2_3" / "api_usage.jsonl")


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str | None = field(repr=False)
    gemini_model: str
    data_dir: Path
    schema_path: Path
    gemini_thinking_level: str | None = DEFAULT_THINKING_LEVEL
    llm_timeout_ms: int = 60_000
    llm_max_attempts: int = 2  # one request plus at most one retry (never after a daily-quota 429)
    llm_min_interval_s: float = 0.0  # pacing between requests; the free tier allows 5 per minute
    llm_request_cap: int | None = None  # most API requests one client may send, retries and failures included
    sql_prompt_variant: str = DEFAULT_SQL_PROMPT_VARIANT  # PORTFOLIOIQ_SQL_PROMPT: a named configuration (sql_tool.PROMPT_VARIANTS)
    sql_timeout_s: float = 2.0
    sql_max_rows: int = 500
    cache_dir: Path = PROJECT_ROOT / ".cache"
    llm_cache_dir: Path | None = None  # saved responses, reused for identical requests; None disables
    api_usage_log: Path | None = None  # one JSON line per request or cache hit; None disables
    replay_ledgers: tuple[Path, ...] = ()  # further recordings DEMO / FIXTURE MODE may replay from; never written to


def load_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    data_dir = Path(os.getenv("PORTFOLIO_DATA_DIR") or PROJECT_ROOT / "data")
    key = (os.getenv("GEMINI_API_KEY") or "").strip()
    thinking = (os.getenv("GEMINI_THINKING_LEVEL") or DEFAULT_THINKING_LEVEL).strip().upper()
    cache_dir = Path(os.getenv("PORTFOLIOIQ_CACHE_DIR") or PROJECT_ROOT / ".cache")
    cache_off = (os.getenv("PORTFOLIOIQ_LLM_CACHE") or "").strip().lower() in ("off", "0", "false", "no")
    variant = (os.getenv("PORTFOLIOIQ_SQL_PROMPT") or DEFAULT_SQL_PROMPT_VARIANT).strip()
    return Settings(
        gemini_api_key=None if key in PLACEHOLDER_API_KEYS else key,
        gemini_model=os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL,
        data_dir=data_dir,
        schema_path=data_dir / "database_schema.sql",
        gemini_thinking_level=None if thinking in ("", "DEFAULT") else thinking,
        llm_min_interval_s=float(os.getenv("PORTFOLIOIQ_LLM_MIN_INTERVAL") or 0.0),
        sql_prompt_variant=variant,
        replay_ledgers=PRODUCTION_RECORDINGS if variant == DEFAULT_SQL_PROMPT_VARIANT else (),
        cache_dir=cache_dir,
        llm_cache_dir=None if cache_off else cache_dir / "llm_responses",
        api_usage_log=Path(os.getenv("PORTFOLIOIQ_USAGE_LOG") or PROJECT_ROOT / "reports" / "api_usage.jsonl"),
    )
