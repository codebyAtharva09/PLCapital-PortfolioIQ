"""Latency measurement and display. Every duration in PortfolioIQ is reported in milliseconds.

Durations come from the monotonic performance clock (`time.perf_counter_ns`), never from wall-clock
timestamps. A stage that did not run, or that was not measured, is `None` ("n/a"), never 0 ms.
"""

from __future__ import annotations

import math
import statistics
import time
from collections.abc import Iterable, Sequence

NS_PER_MS = 1_000_000
MS_PER_SECOND = 1_000
P95_MIN_SAMPLE = 20  # a 95th percentile from fewer observations is just the maximum


class InstrumentationError(ValueError):
    """A measured duration is impossible (negative or not finite): the instrumentation is wrong."""


def now_ns() -> int:
    """Monotonic, high-resolution time in nanoseconds. Only differences between two readings mean anything."""
    return time.perf_counter_ns()


def ms_from_ns(duration_ns: int | float) -> float:
    ms = duration_ns / NS_PER_MS
    _check(ms)
    return ms


def ms_from_seconds(seconds: float) -> float:
    """Convert a duration recorded in seconds to milliseconds, exactly once."""
    ms = seconds * MS_PER_SECOND
    _check(ms)
    return ms


def elapsed_ms(start_ns: int, end_ns: int | None = None) -> float:
    """Milliseconds between two `now_ns()` readings (the second defaults to now), unrounded."""
    return ms_from_ns((now_ns() if end_ns is None else end_ns) - start_ns)


def round_ms(value: float | None) -> float | None:
    """Rounded for storage: 3 decimals (1 microsecond), enough for sub-millisecond SQL runs."""
    return None if value is None else round(value, 3)


def sum_measured(values: Iterable[float | None]) -> float | None:
    """Total of the measured values; None when none of them was measured (so "not run" never becomes 0 ms)."""
    measured = [v for v in values if v is not None]
    return round_ms(math.fsum(measured)) if measured else None


def format_ms(value: float | None) -> str:
    """`1,248 ms`, `245 ms`, `12.45 ms`, `0.412 ms`; `n/a` when the stage was not measured."""
    if value is None:
        return "n/a"
    _check(value)
    if value >= 100:
        return f"{value:,.0f} ms"
    return f"{value:.2f} ms" if value >= 1 else f"{value:.3f} ms"


def format_stages(stages: Sequence[tuple[str, float | None]], sep: str = " · ") -> str:
    """`label value` pairs joined with `sep`, leaving out stages that were not measured.

    The default separator is for screens that render Unicode; the CLI passes an ASCII one, because it
    converts every non-ASCII character to `?`.
    """
    return sep.join(f"{label} {format_ms(value)}" for label, value in stages if value is not None)


def summarise_ms(values: Iterable[float | None], *, unavailable: int = 0) -> dict:
    """Min, median, mean and p95 of the measured values (milliseconds).

    `unavailable` counts requests with no usable measurement (failures, timeouts, cases never run); it is
    reported next to the statistics so that nothing is dropped silently. p95 is None below 20 observations.
    """
    measured = sorted(v for v in values if v is not None)
    for v in measured:
        _check(v)
    count = len(measured)
    return {
        "count": count, "unavailable": unavailable,
        "min_ms": round_ms(measured[0]) if count else None,
        "median_ms": round_ms(statistics.median(measured)) if count else None,
        "mean_ms": round_ms(statistics.fmean(measured)) if count else None,
        "p95_ms": nearest_rank(measured, 0.95) if count >= P95_MIN_SAMPLE else None,
        "max_ms": round_ms(measured[-1]) if count else None,
    }


def nearest_rank(sorted_values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile of an ascending list."""
    if not sorted_values:
        return None
    return round_ms(sorted_values[max(0, math.ceil(q * len(sorted_values)) - 1)])


def _check(ms: float) -> None:
    if not math.isfinite(ms) or ms < 0:
        raise InstrumentationError(f"invalid duration: {ms!r} ms")
