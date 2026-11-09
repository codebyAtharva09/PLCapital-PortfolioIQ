"""Deterministic date interpretation: rules B9 and B10 in docs/BUSINESS_RULES.md.

Relative dates are anchored to the latest date in the relevant table, never to today's date. The
exact start and end dates are computed here, by code, and recorded; the LLM only receives the
resulting ranges.

- "last/previous week, month, quarter, year": the previous completed calendar period
  (weeks run Monday to Sunday).
- "past/last N days, weeks, months, quarters, years" and "past week/month/quarter/year": a rolling
  period ending on the anchor date, inclusive.
- "this week/month/quarter/year", "year to date", "YTD", "MTD", "QTD": from the start of the
  period to the anchor date.
- "today" and "yesterday": the anchor date and the day before it.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

# Time-series date columns and the words that point a question at each (first match wins).
TIME_SERIES = (
    ("transactions", "settlement_date", ("settlement", "settled", "settle")),
    ("holdings", "purchase_date", ("purchase", "purchased")),
    ("portfolio_performance", "performance_date",
     ("performance", "return", "returns", "nav", "sharpe", "volatility", "drawdown")),
    ("risk_metrics", "calculation_date", ("risk", "var", "beta", "tracking", "sortino", "correlation", "cvar")),
    ("transactions", "transaction_date",
     ("transaction", "transactions", "trade", "trades", "traded", "trading", "bought", "sold", "buy", "buys",
      "sell", "sells", "purchases")),
    ("historical_prices", "price_date", ("price", "prices", "priced", "close", "closing", "volume")),
)
# The default anchor when a question names no table: the latest date across these columns.
DATASET_COLUMNS = (("transactions", "transaction_date"), ("historical_prices", "price_date"),
                   ("portfolio_performance", "performance_date"), ("risk_metrics", "calculation_date"))


class DateInterpretation(BaseModel):
    model_config = ConfigDict(frozen=True)

    phrase: str
    rule: str
    table: str | None
    column: str | None
    anchor_date: str
    anchor_source: str
    start: str
    end: str

    def describe(self) -> str:
        return (f"'{self.phrase}' = {self.start} to {self.end} ({self.rule}; anchored to {self.anchor_date}, "
                f"the {self.anchor_source}, not today's date)")


@dataclass(frozen=True)
class DateCatalog:
    ranges: dict[tuple[str, str], tuple[str, str]]  # (table, column) -> (earliest, latest)
    dataset_anchor: str

    @classmethod
    def from_db(cls, db_path: Path) -> DateCatalog:
        conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            ranges = {
                (table, column): tuple(conn.execute(f"SELECT MIN({column}), MAX({column}) FROM {table}").fetchone())
                for table, column, _ in TIME_SERIES
            }
        finally:
            conn.close()
        return cls(ranges=ranges, dataset_anchor=max(ranges[key][1] for key in DATASET_COLUMNS))

    def latest(self, table: str, column: str) -> str:
        return self.ranges[(table, column)][1]


_UNIT = r"(day|days|week|weeks|month|months|quarter|quarters|year|years)"
_PATTERNS = (
    ("rolling_n", re.compile(rf"\b(?:past|last|previous|prior)\s+(\d{{1,3}})\s+{_UNIT}\b")),
    ("previous", re.compile(r"\b(?:last|previous|prior)\s+(week|month|quarter|year)\b")),
    ("rolling_1", re.compile(r"\bpast\s+(week|month|quarter|year)\b")),
    ("to_date", re.compile(r"\b(?:this|current)\s+(week|month|quarter|year)\b")),
    ("to_date", re.compile(r"\b(week|month|quarter|year)[\s-]to[\s-]date\b")),
    ("to_date_abbr", re.compile(r"\b(ytd|qtd|mtd)\b")),
    ("today", re.compile(r"\btoday\b")),
    ("yesterday", re.compile(r"\byesterday\b")),
)


def interpret_dates(question: str, catalog: DateCatalog) -> list[DateInterpretation]:
    """Every relative-date phrase in the question, resolved to exact dates (empty if none)."""
    text = question.lower()
    table, column = _target_column(text)
    if table:
        anchor_iso, source = catalog.latest(table, column), f"latest {table}.{column}"
    else:
        anchor_iso, source = catalog.dataset_anchor, "latest date across the time-series tables"
    anchor = dt.date.fromisoformat(anchor_iso)

    found: list[DateInterpretation] = []
    taken: list[tuple[int, int]] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            if any(match.start() < end and start < match.end() for start, end in taken):
                continue
            start_date, end_date, rule = _resolve(kind, match, anchor)
            taken.append(match.span())
            found.append(DateInterpretation(
                phrase=match.group(0), rule=rule, table=table, column=column, anchor_date=anchor_iso,
                anchor_source=source, start=start_date.isoformat(), end=end_date.isoformat(),
            ))
    return sorted(found, key=lambda d: text.index(d.phrase))


def _target_column(text: str) -> tuple[str | None, str | None]:
    words = set(re.findall(r"[a-z]+", text))
    for table, column, keywords in TIME_SERIES:
        if words & set(keywords):
            return table, column
    return None, None


def _resolve(kind: str, match: re.Match, anchor: dt.date) -> tuple[dt.date, dt.date, str]:
    if kind == "rolling_n":
        return _rolling(int(match.group(1)), match.group(2), anchor)
    if kind == "rolling_1":
        return _rolling(1, match.group(1), anchor)
    if kind == "previous":
        return _previous(match.group(1), anchor)
    if kind == "to_date":
        return _to_date(match.group(1), anchor)
    if kind == "to_date_abbr":
        return _to_date({"ytd": "year", "qtd": "quarter", "mtd": "month"}[match.group(1)], anchor)
    if kind == "today":
        return anchor, anchor, "B10: the anchor date"
    return anchor - dt.timedelta(days=1), anchor - dt.timedelta(days=1), "B10: the day before the anchor date"


def _rolling(count: int, unit: str, anchor: dt.date) -> tuple[dt.date, dt.date, str]:
    unit = unit.rstrip("s")
    if unit == "day":
        start = anchor - dt.timedelta(days=count - 1)
    elif unit == "week":
        start = anchor - dt.timedelta(days=7 * count - 1)
    else:
        months = count * {"month": 1, "quarter": 3, "year": 12}[unit]
        start = _add_months(anchor, -months) + dt.timedelta(days=1)
    plural = unit if count == 1 else f"{unit}s"
    return start, anchor, f"B10: rolling {count} {plural} ending on the anchor date"


def _previous(unit: str, anchor: dt.date) -> tuple[dt.date, dt.date, str]:
    if unit == "week":
        start = anchor - dt.timedelta(days=anchor.weekday() + 7)
        return start, start + dt.timedelta(days=6), "B10: previous calendar week (Monday to Sunday)"
    if unit == "month":
        end = anchor.replace(day=1) - dt.timedelta(days=1)
        return end.replace(day=1), end, "B10: previous calendar month"
    if unit == "quarter":
        quarter_start = dt.date(anchor.year, 3 * ((anchor.month - 1) // 3) + 1, 1)
        return _add_months(quarter_start, -3), quarter_start - dt.timedelta(days=1), "B10: previous calendar quarter"
    return dt.date(anchor.year - 1, 1, 1), dt.date(anchor.year - 1, 12, 31), "B10: previous calendar year"


def _to_date(unit: str, anchor: dt.date) -> tuple[dt.date, dt.date, str]:
    starts = {
        "week": anchor - dt.timedelta(days=anchor.weekday()),
        "month": anchor.replace(day=1),
        "quarter": dt.date(anchor.year, 3 * ((anchor.month - 1) // 3) + 1, 1),
        "year": dt.date(anchor.year, 1, 1),
    }
    return starts[unit], anchor, f"B10: {unit} to date"


def _add_months(day: dt.date, months: int) -> dt.date:
    years, month_index = divmod(day.month - 1 + months, 12)
    year, month = day.year + years, month_index + 1
    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))
