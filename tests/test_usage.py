"""API usage summary: read from the usage ledger only; no network, no API calls."""

import json

from portfolioiq_eval import usage
from portfolioiq_eval.__main__ import main


def write_ledger(path, entries):
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")


def request(time, status="ok", tokens=100, purpose="Q7 sql_tool draft 1", **extra):
    answered = tokens is not None
    return {"timestamp_utc": time, "event": "request", "api_request": True, "purpose": purpose,
            "model": "gemini-3.6-flash", "model_version": "gemini-3.6-flash" if answered else None,
            "attempt": 1, "status": status, "latency_ms": 900.0, "request_hash": "ab" * 8, "prompt_tokens": tokens,
            "output_tokens": 10 if answered else None, "thinking_tokens": 5 if answered else None,
            "total_tokens": tokens + 15 if answered else None, **extra}


ENTRIES = [
    request("2026-11-07T07:59:59+00:00"),  # before the window
    request("2026-11-07T08:22:10+00:00"),
    request("2026-11-07T08:22:30+00:00", status="503 UNAVAILABLE", tokens=None, error_kind="unavailable"),
    {"timestamp_utc": "2026-11-07T08:23:00+00:00", "event": "cache_hit", "api_request": False, "purpose": "A1"},
    {"timestamp_utc": "2026-11-07T08:24:00+00:00", "event": "budget_exhausted", "api_request": False, "purpose": "A4"},
]


def test_summary_counts_every_request_in_the_window_including_failures(tmp_path):
    ledger = tmp_path / "api_usage.jsonl"
    write_ledger(ledger, ENTRIES)
    summary = usage.summarise(ledger, since="2026-11-07T08:00:00+00:00")
    assert summary["requests"] == 2 and summary["by_status"] == {"ok": 1, "503 UNAVAILABLE": 1}
    assert (summary["cache_hits"], summary["budget_stops"]) == (1, 1)
    assert summary["tokens"] == {"prompt": 100, "output": 10, "thinking": 5, "total": 115}
    latency = summary["latency_ms"]
    assert (latency["median"], latency["max"]) == (900.0, 900.0)
    assert latency["failed_requests"] == 1  # the 503 is counted apart, not dropped
    assert latency["successful"]["count"] == 1 and latency["all_attempts"]["count"] == 2
    assert latency["successful"]["p95_ms"] is None  # too few observations for a 95th percentile
    assert usage.summarise(ledger)["requests"] == 3


def test_missing_ledger_means_no_recorded_requests(tmp_path):
    assert usage.summarise(tmp_path / "absent.jsonl")["requests"] == 0


def test_usage_command_prints_the_summary(tmp_path, monkeypatch, capsys):
    ledger = tmp_path / "api_usage.jsonl"
    write_ledger(ledger, ENTRIES)
    monkeypatch.setenv("PORTFOLIOIQ_USAGE_LOG", str(ledger))
    assert main(["usage", "--since", "2026-11-07T08:00:00+00:00"]) == 0
    printed = capsys.readouterr().out
    assert "API requests sent:   2" in printed and "budget stops:        1" in printed
    assert "no response" in printed and "None tokens" not in printed  # the 503 request returned nothing
