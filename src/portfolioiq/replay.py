"""DEMO / FIXTURE MODE: replay recorded Gemini responses offline.

``RecordedLLM`` stands in for the Gemini client when no live request may be sent (no key, no quota, or a
demonstration without network access). It answers a request only when a successful response to the
byte-identical request is recorded in the usage ledger or in the active configuration's recordings
(``Settings.replay_ledgers``), matched by the same request key the live client and its response cache use.
It never contacts the API.

Only Gemini's output is replayed: everything after it (SQL validation and read-only execution, portfolio
resolution, exposure calculation, checks and verification) runs normally, now. Every replayed call is
marked ``source="replay"`` with the time it was originally recorded, so a receipt can never be mistaken
for a live answer. A request with no recorded response fails with ``not_recorded``; nothing is invented.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from portfolioiq.config import Settings
from portfolioiq.llm import SDK_VERSION, LLMCall, LLMError, LLMResult, request_key, strict_value

T = TypeVar("T", bound=BaseModel)
LEDGER_HASH_LENGTH = 16  # the ledger records the first 16 hex characters of each request key
DEMO_LABEL = "DEMO / FIXTURE MODE"
NOT_RECORDED_MESSAGE = (
    f"{DEMO_LABEL}: no recorded Gemini response exists for this exact request, so it cannot be answered "
    "offline. Nothing was sent to the API. Use one of the recorded example questions, or run in live mode "
    "with a Gemini API key."
)


class RecordedLLM:
    """A structured-LLM client that only replays recorded responses. It has no network code at all."""

    replay = True

    def __init__(self, settings: Settings, ledger: Path | None = None):
        self.model = settings.gemini_model
        self._thinking = settings.gemini_thinking_level
        self.ledger = ledger or settings.api_usage_log
        self._recorded: dict[str, dict] = {}
        # Requests are matched byte for byte, so a response can only answer the configuration it was recorded for.
        for path in ([ledger] if ledger else [settings.api_usage_log, *settings.replay_ledgers]):
            if path:
                self._recorded.update(load_recorded(path, self.model))

    @property
    def recorded_count(self) -> int:
        return len(self._recorded)

    def generate(self, *, system: str, prompt: str, schema: type[T], purpose: str = "") -> LLMResult[T]:
        key = request_key(self.model, self._thinking, system, prompt, schema)
        entry = self._recorded.get(key[:LEDGER_HASH_LENGTH])
        if entry is None:
            raise LLMError("not_recorded", NOT_RECORDED_MESSAGE)
        try:
            value = strict_value(entry["raw_response"], schema)
        except (ValidationError, ValueError):
            raise LLMError("malformed_output", f"{DEMO_LABEL}: the recorded response does not match the expected "
                                               "format, so it was not used.") from None
        call = LLMCall(model=entry.get("model") or self.model, model_version=entry.get("model_version"),
                       sdk_version=SDK_VERSION, source="replay", cached_at=entry.get("timestamp_utc"),
                       request_hash=key, latency_ms=None, attempts=0)
        return LLMResult(value=value, call=call)


def load_recorded(ledger: Path, model: str) -> dict[str, dict]:
    """Successful responses in the ledger for ``model``, keyed by request-hash prefix (latest wins)."""
    recorded: dict[str, dict] = {}
    if not ledger.exists():
        return recorded
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue  # a damaged line is skipped, never guessed at
        if not isinstance(entry, dict):
            continue
        if (entry.get("event") == "request" and entry.get("status") == "ok" and entry.get("model") == model
                and entry.get("raw_response") and entry.get("request_hash")):
            recorded[entry["request_hash"][:LEDGER_HASH_LENGTH]] = entry
    return recorded


def is_replay(llm: object) -> bool:
    return bool(getattr(llm, "replay", False))
