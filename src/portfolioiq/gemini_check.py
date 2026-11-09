"""Milestone 1 connectivity check: one tiny Gemini request with the configured model.

Run:  python -m portfolioiq.gemini_check [--model MODEL_ID]

Exit codes: 0 success, 1 API or network failure, 2 missing API key.
The API key is never printed, not even inside error messages.
"""

from __future__ import annotations

import argparse
import sys
import time

from google import genai
from google.genai import errors, types

from portfolioiq.config import load_settings

PROMPT = "Reply with exactly one word: OK"
TIMEOUT_MS = 30_000


def check(model: str | None = None) -> int:
    settings = load_settings()
    model = model or settings.gemini_model
    key = settings.gemini_api_key
    if not key:
        print(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.",
            file=sys.stderr,
        )
        return 2

    client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=TIMEOUT_MS))
    started = time.perf_counter()
    try:
        response = client.models.generate_content(model=model, contents=PROMPT)
    except errors.APIError as exc:
        print(_redact(f"Gemini API error {exc.code} {exc.status}: {exc.message}", key), file=sys.stderr)
        return 1
    except Exception as exc:  # network failures and timeouts surface as other exception types
        print(_redact(f"Gemini request failed: {type(exc).__name__}: {exc}", key), file=sys.stderr)
        return 1
    latency_ms = (time.perf_counter() - started) * 1000

    print(f"requested model: {model}")
    print(f"served by:       {getattr(response, 'model_version', None) or 'not reported'}")
    print(f"sdk:             google-genai {genai.__version__}")
    print(f"latency:         {latency_ms:.0f} ms")
    print(f"reply:           {(response.text or '').strip()!r}")
    usage = response.usage_metadata
    if usage:
        print(
            f"tokens:          prompt={usage.prompt_token_count} "
            f"output={usage.candidates_token_count} thinking={usage.thoughts_token_count} "
            f"total={usage.total_token_count}"
        )
    return 0


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "***")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Gemini API connectivity.")
    parser.add_argument("--model", help="override GEMINI_MODEL for this check")
    sys.exit(check(parser.parse_args().model))


if __name__ == "__main__":
    main()
