"""Gemini API usage, read from the usage ledger (`reports/api_usage.jsonl`). Makes no API calls.

Every request the client sends is one ledger line, failed requests and retries included, so this
is the project's own count of what was spent. Google's console remains the authority on the quota
itself: requests sent from elsewhere with the same project (for example AI Studio) are not here.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
from pathlib import Path

from portfolioiq.timing import format_ms, summarise_ms


def summarise(ledger: Path, since: str | None = None, until: str | None = None) -> dict:
    """Requests, outcomes, tokens and latency for ledger entries in [since, until] (ISO times; open if None)."""
    start, end = _time(since), _time(until)
    entries = []
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entry = json.loads(line)
                stamp = _time(entry["timestamp_utc"])
                if (start is None or stamp >= start) and (end is None or stamp <= end):
                    entries.append(entry)
    requests = [e for e in entries if e.get("api_request")]
    ok = [e for e in requests if e.get("status") == "ok"]
    latencies = sorted(e["latency_ms"] for e in ok if e.get("latency_ms") is not None)
    failed = [e for e in requests if e.get("status") != "ok"]
    return {
        "ledger": ledger.as_posix(), "since": since, "until": until,
        "requests": len(requests),
        "by_status": _count(e.get("status", "unknown") for e in requests),
        "by_model": _count(e.get("model", "unknown") for e in requests),
        "served_versions": _count(e.get("model_version") or "unknown" for e in ok),
        "cache_hits": sum(1 for e in entries if e.get("event") == "cache_hit"),
        "budget_stops": sum(1 for e in entries if e.get("event") == "budget_exhausted"),
        "tokens": {key: sum(e.get(f"{key}_tokens") or 0 for e in requests)
                   for key in ("prompt", "output", "thinking", "total")},
        # Milliseconds, client-observed (network + queueing + inference). The first two keys are the answered
        # requests only; "successful" and "all_attempts" are the full statistics, the latter including failures.
        "latency_ms": {"median": round(statistics.median(latencies), 1) if latencies else None,
                       "max": latencies[-1] if latencies else None,
                       "successful": summarise_ms(latencies),
                       "all_attempts": summarise_ms(e.get("latency_ms") for e in requests),
                       "failed_requests": len(failed)},
        "first_request_utc": requests[0]["timestamp_utc"] if requests else None,
        "last_request_utc": requests[-1]["timestamp_utc"] if requests else None,
        "requests_by_purpose": [{"time_utc": e["timestamp_utc"], "purpose": e.get("purpose", ""),
                                 "status": e.get("status"), "attempt": e.get("attempt"),
                                 "latency_ms": e.get("latency_ms"), "total_tokens": e.get("total_tokens"),
                                 "request_hash": e.get("request_hash")} for e in requests],
    }


def render(summary: dict) -> str:
    window = f"{summary['since'] or 'the start'} to {summary['until'] or 'now'}"
    tokens, latency = summary["tokens"], summary["latency_ms"]
    lines = [f"Gemini API usage from {summary['ledger']}, {window}",
             f"  API requests sent:   {summary['requests']}  (by status: {_pairs(summary['by_status'])})",
             f"  models:              {_pairs(summary['by_model'])}; served versions: {_pairs(summary['served_versions'])}",
             f"  cache hits:          {summary['cache_hits']} (no quota used)",
             f"  budget stops:        {summary['budget_stops']} (requests not sent because the cap was reached)",
             f"  tokens:              prompt {tokens['prompt']}, output {tokens['output']}, "
             f"thinking {tokens['thinking']}, total {tokens['total']}",
             f"  latency (answered):  {_stats(latency['successful'])}",
             f"  latency (all sent):  {_stats(latency['all_attempts'])}; {latency['failed_requests']} failed request(s)"]
    for r in summary["requests_by_purpose"]:
        used = "no response" if r["total_tokens"] is None else f"{r['total_tokens']} tokens"
        lines.append(f"    {r['time_utc']}  {r['status']:<22} attempt {r['attempt']}  {format_ms(r['latency_ms'])}  "
                     f"{used:<12} {r['purpose']}")
    return "\n".join(lines)


def _stats(s: dict) -> str:
    if not s["count"]:
        return "n/a"
    return (f"min {format_ms(s['min_ms'])}, median {format_ms(s['median_ms'])}, mean {format_ms(s['mean_ms'])}, "
            f"p95 {format_ms(s['p95_ms'])}, max {format_ms(s['max_ms'])} (n = {s['count']})")


def _time(value: str | None) -> dt.datetime | None:
    if value is None:
        return None
    parsed = dt.datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _count(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return counts


def _pairs(counts: dict[str, int]) -> str:
    return ", ".join(f"{k} {v}" for k, v in counts.items()) or "none"
