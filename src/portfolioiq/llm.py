"""Gemini access through the official google-genai SDK, under a strict request budget.

One structured (JSON-schema) call at a time, with:

- the configured model only; there is no silent fallback to another model;
- a response cache: a byte-identical request (same model, prompt, schema and settings) is
  answered from the saved response instead of spending quota;
- a usage ledger: every real request (and every cache hit) is appended as one JSON line with its
  timestamp, model, status, latency and tokens; successful raw responses are saved immediately;
- a conservative retry policy: a daily-quota 429 is never retried; a per-minute 429 is retried
  once, only when Google's retry delay is 60 seconds or less; 5xx and network errors get at most
  one retry. The total number of attempts is capped (two by default);
- an optional hard cap on the API requests one client may send. Retries and failed requests count
  against it, and a request beyond it is never sent;
- optional pacing between requests (the free tier allows 5 requests per minute);
- error messages that never contain the API key.

Model output is returned as data only. Nothing here executes it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from typing import Generic, Literal, Protocol, TypeVar

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from portfolioiq import timing
from portfolioiq.config import Settings

T = TypeVar("T", bound=BaseModel)
SDK_VERSION = genai.__version__
SERVER_ERRORS = frozenset({500, 502, 503, 504})
MAX_RETRY_WAIT_S = 60.0  # never sleep longer than this inside a request
SERVER_RETRY_WAIT_S = 5.0
_RETRY_IN = re.compile(r"retry in\s+(?:(\d+)h)?\s*(?:(\d+)m(?!s))?\s*(?:([\d.]+)s)?", re.I)


class LLMError(Exception):
    """A Gemini failure, with a message that is safe to show to users."""

    def __init__(self, kind: str, message: str, retry_after_s: float | None = None, attempts: int = 0):
        super().__init__(message)
        # not_configured | quota_exhausted | rate_limited | unavailable | api_error | network
        # | malformed_output | blocked | budget_exhausted (the request cap was reached; nothing was sent)
        self.kind = kind
        self.message = message
        self.retry_after_s = retry_after_s
        self.attempts = attempts  # API requests sent before the failure; a failed request is not assumed free


class LLMCall(BaseModel):
    model: str
    model_version: str | None = None
    sdk_version: str
    # api: sent now; cache: identical request answered from a saved response; replay: demo mode, a recorded
    # response replayed offline (never sent)
    source: Literal["api", "cache", "replay"] = "api"
    cached_at: str | None = None  # when a cached or replayed response was originally obtained
    request_hash: str | None = None
    # Client-observed duration of the successful request (network + queueing + inference, not inference alone).
    # None when no request was sent: a cache hit or a replay measured nothing.
    latency_ms: float | None
    # A recording stores the historical value as written; None-valued calls are the current convention.
    wait_ms: float = 0.0  # pacing and retry waits before it
    attempts: int  # API requests sent (0 for a cache hit)
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class LLMResult(Generic[T]):
    value: T
    call: LLMCall


class StructuredLLM(Protocol):
    model: str

    def generate(self, *, system: str, prompt: str, schema: type[T], purpose: str = "") -> LLMResult[T]: ...


class GeminiLLM:
    def __init__(self, settings: Settings, *, client=None, sleep=time.sleep, clock=time.monotonic):
        if not settings.gemini_api_key:
            raise LLMError("not_configured",
                           "Gemini is not configured: the GEMINI_API_KEY environment variable is not set.")
        self.model = settings.gemini_model
        self._secret = settings.gemini_api_key
        self._client = client or genai.Client(api_key=settings.gemini_api_key,
                                              http_options=types.HttpOptions(timeout=settings.llm_timeout_ms))
        self._thinking = settings.gemini_thinking_level
        self._max_attempts = max(1, settings.llm_max_attempts)
        self._min_interval = settings.llm_min_interval_s
        self._cache_dir = settings.llm_cache_dir
        self._usage_log = settings.api_usage_log
        self._cap = settings.llm_request_cap
        self._sleep, self._clock = sleep, clock
        self._last_request: float | None = None
        self.requests_sent = 0  # API requests sent by this client, retries and failed requests included

    def generate(self, *, system: str, prompt: str, schema: type[T], purpose: str = "") -> LLMResult[T]:
        key = self._request_key(system, prompt, schema)
        cached = self._from_cache(key, schema, purpose)
        if cached is not None:
            return cached
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            thinking_config=types.ThinkingConfig(thinking_level=self._thinking) if self._thinking else None,
        )
        waited = 0.0
        previous: LLMError | None = None
        for attempt in range(1, self._max_attempts + 1):
            if self._cap is not None and self.requests_sent >= self._cap:
                raise self._budget_exhausted(purpose, key, attempt - 1, previous)
            waited += self._pace()
            started = timing.now_ns()
            self.requests_sent += 1  # counted before sending: a failed request is not assumed free
            try:
                response = self._client.models.generate_content(model=self.model, contents=prompt, config=config)
            except errors.APIError as exc:
                error = self._classify(exc)
                self._log(purpose, key, attempt, f"{exc.code} {exc.status}", timing.elapsed_ms(started), error=error)
                delay = self._retry_delay(error, exc.code)
                if delay is not None and attempt < self._max_attempts:
                    waited += self._wait(delay)
                    previous = error
                    continue
                error.attempts = attempt
                raise error from None
            except httpx.HTTPError as exc:  # network failures and client-side timeouts
                error = LLMError("network", self._redact(f"Gemini request failed: {type(exc).__name__}"))
                self._log(purpose, key, attempt, "network_error", timing.elapsed_ms(started), error=error)
                if attempt < self._max_attempts:
                    waited += self._wait(SERVER_RETRY_WAIT_S)
                    previous = error
                    continue
                error.attempts = attempt
                raise error from None
            latency_ms = timing.elapsed_ms(started)
            raw = _raw_text(response)
            try:
                value = _parse(response, raw, schema)
            except LLMError as error:
                self._log(purpose, key, attempt, error.kind, latency_ms, response=response, raw=raw, error=error)
                error.attempts = attempt
                raise
            call = _call_record(self.model, response, latency_ms, waited * 1000, attempt, key)
            self._save(key, raw, call)  # persisted before the result is used
            self._log(purpose, key, attempt, "ok", latency_ms, response=response, raw=raw)
            return LLMResult(value=value, call=call)
        raise AssertionError("unreachable")  # the loop always returns or raises

    # --- budget controls --------------------------------------------------------------------

    def _budget_exhausted(self, purpose: str, key: str, sent: int, previous: LLMError | None) -> LLMError:
        """The request cap is reached: record that the request was not sent, and report it."""
        note = f" The retry after the {previous.kind} error was not sent." if previous else ""
        self._write_log({"timestamp_utc": _now(), "event": "budget_exhausted", "api_request": False,
                         "purpose": purpose, "model": self.model, "request_hash": key[:16],
                         "requests_sent": self.requests_sent, "request_cap": self._cap})
        return LLMError("budget_exhausted", f"The request budget is used up: {self.requests_sent} of {self._cap} "
                                            f"API requests sent, so no further request was sent.{note}", attempts=sent)

    def _request_key(self, system: str, prompt: str, schema: type[BaseModel]) -> str:
        return request_key(self.model, self._thinking, system, prompt, schema)

    def _from_cache(self, key: str, schema: type[T], purpose: str) -> LLMResult[T] | None:
        if self._cache_dir is None:
            return None
        path = self._cache_dir / f"{key}.json"
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            value = schema.model_validate_json(saved["raw_response"])
        except (OSError, ValueError, KeyError, ValidationError):
            return None  # no usable saved response: ask the API
        call = LLMCall(model=saved.get("model", self.model), model_version=saved.get("model_version"),
                       sdk_version=genai.__version__, source="cache", cached_at=saved.get("created_utc"),
                       request_hash=key, latency_ms=None, attempts=0)
        self._write_log({"timestamp_utc": _now(), "event": "cache_hit", "api_request": False, "purpose": purpose,
                         "model": call.model, "request_hash": key[:16], "cached_at": call.cached_at})
        return LLMResult(value=value, call=call)

    def _save(self, key: str, raw: str | None, call: LLMCall) -> None:
        if self._cache_dir is None or not raw:
            return
        record = {"request_hash": key, "model": call.model, "model_version": call.model_version,
                  "created_utc": _now(), "raw_response": raw,
                  "usage": {"prompt_tokens": call.prompt_tokens, "output_tokens": call.output_tokens,
                            "thinking_tokens": call.thinking_tokens, "total_tokens": call.total_tokens}}
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=self._cache_dir, suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(record, stream)
        os.replace(temp, self._cache_dir / f"{key}.json")

    def _log(self, purpose: str, key: str, attempt: int, status: str, latency_ms: float, *, response=None,
             raw: str | None = None, error: LLMError | None = None) -> None:
        usage = getattr(response, "usage_metadata", None) if response is not None else None
        entry = {
            "timestamp_utc": _now(), "event": "request", "api_request": True, "purpose": purpose,
            "model": self.model, "model_version": getattr(response, "model_version", None) if response else None,
            "attempt": attempt, "status": status, "latency_ms": timing.round_ms(latency_ms), "request_hash": key[:16],
            "prompt_tokens": getattr(usage, "prompt_token_count", None),
            "output_tokens": getattr(usage, "candidates_token_count", None),
            "thinking_tokens": getattr(usage, "thoughts_token_count", None),
            "total_tokens": getattr(usage, "total_token_count", None),
        }
        if error is not None:
            entry.update(error_kind=error.kind, error=error.message[:300], retry_after_s=error.retry_after_s)
        if raw is not None:  # every response received, including one that failed validation
            entry["raw_response"] = raw
        self._write_log(entry)

    def _write_log(self, entry: dict) -> None:
        if self._usage_log is None:
            return
        self._usage_log.parent.mkdir(parents=True, exist_ok=True)
        with self._usage_log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry) + "\n")

    def _classify(self, exc: errors.APIError) -> LLMError:
        message = self._redact(f"Gemini API error {exc.code} {exc.status}: {exc.message}")
        if exc.code == 429:
            retry_after = _retry_after(exc)
            daily = "perday" in _quota_ids(exc).replace("_", "").lower() or "per day" in str(exc.message).lower()
            if daily or (retry_after is not None and retry_after > MAX_RETRY_WAIT_S):
                return LLMError("quota_exhausted", message, retry_after)
            return LLMError("rate_limited", message, retry_after)
        if exc.code in SERVER_ERRORS:
            return LLMError("unavailable", message, _retry_after(exc))
        return LLMError("api_error", message)

    @staticmethod
    def _retry_delay(error: LLMError, status: int | None) -> float | None:
        """Seconds to wait before the single permitted retry, or None for no retry."""
        if error.kind == "rate_limited" and error.retry_after_s is not None:
            return min(error.retry_after_s + 1.0, MAX_RETRY_WAIT_S)
        if error.kind == "unavailable":
            return min(error.retry_after_s or SERVER_RETRY_WAIT_S, MAX_RETRY_WAIT_S)
        return None  # daily quota, unknown rate-limit delay, client errors: stop

    def _pace(self) -> float:
        waited = 0.0
        if self._min_interval and self._last_request is not None:
            remaining = self._min_interval - (self._clock() - self._last_request)
            if remaining > 0:
                waited = self._wait(remaining)
        self._last_request = self._clock()
        return waited

    def _wait(self, seconds: float) -> float:
        self._sleep(seconds)
        return seconds

    def _redact(self, text: str) -> str:
        return text.replace(self._secret, "***")


def request_key(model: str, thinking: str | None, system: str, prompt: str, schema: type[BaseModel]) -> str:
    """SHA-256 identifying one request: model, thinking level, instruction, prompt and output schema.

    The response cache, the usage ledger (first 16 characters) and the demo replay all use this key.
    """
    payload = json.dumps({"model": model, "thinking": thinking, "system": system, "prompt": prompt,
                          "schema": schema.model_json_schema()}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _raw_text(response) -> str | None:
    try:
        text = response.text
    except (ValueError, AttributeError):
        text = None
    if text:
        return text
    parsed = getattr(response, "parsed", None)
    return parsed.model_dump_json() if isinstance(parsed, BaseModel) else None


def _parse(response, raw: str | None, schema: type[T]) -> T:
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, schema):
        return parsed
    if raw:
        try:
            return schema.model_validate_json(raw)
        except ValidationError:
            pass
    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None and getattr(feedback, "block_reason", None):
        raise LLMError("blocked", "Gemini declined to answer this request.")
    raise LLMError("malformed_output", "Gemini returned output that does not match the expected format.")


def _error_body(exc: errors.APIError) -> dict:
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        return details.get("error", details) if isinstance(details.get("error"), dict) else details
    return {}


def _quota_ids(exc: errors.APIError) -> str:
    ids = []
    for detail in _error_body(exc).get("details", []) or []:
        for violation in detail.get("violations", []) or []:
            ids.append(str(violation.get("quotaId", "")) + " " + str(violation.get("quotaMetric", "")))
    return " ".join(ids)


def _retry_after(exc: errors.APIError) -> float | None:
    """Google's retry delay: RetryInfo in the error details, a Retry-After header, or the message text."""
    for detail in _error_body(exc).get("details", []) or []:
        delay = detail.get("retryDelay")
        if isinstance(delay, str) and delay.endswith("s"):
            try:
                return float(delay[:-1])
            except ValueError:
                pass
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is not None:
        try:
            value = headers.get("retry-after")
            if value is not None:
                return float(value)
        except (TypeError, ValueError, AttributeError):
            pass
    match = _RETRY_IN.search(str(getattr(exc, "message", "") or ""))
    if match and any(match.groups()):
        hours, minutes, seconds = (float(g) if g else 0.0 for g in match.groups())
        return hours * 3600 + minutes * 60 + seconds
    return None


def _call_record(model: str, response, latency_ms: float, wait_ms: float, attempts: int, key: str) -> LLMCall:
    usage = getattr(response, "usage_metadata", None)
    return LLMCall(
        model=model,
        model_version=getattr(response, "model_version", None),
        sdk_version=genai.__version__,
        request_hash=key,
        latency_ms=timing.round_ms(latency_ms),
        wait_ms=timing.round_ms(wait_ms),
        attempts=attempts,
        prompt_tokens=getattr(usage, "prompt_token_count", None),
        output_tokens=getattr(usage, "candidates_token_count", None),
        thinking_tokens=getattr(usage, "thoughts_token_count", None),
        total_tokens=getattr(usage, "total_token_count", None),
    )


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
