"""Sector exposure engine: rules B1 to B3 in docs/BUSINESS_RULES.md.

Pure calculation over a SQLite connection, with no Streamlit and no Gemini. The same figures are
computed along two independent paths, Python aggregation over holding rows and SQL aggregation
inside the database. A result is marked INDEPENDENTLY_RECONCILED only when the two agree. The
reconciliation catches implementation errors; it cannot detect errors in the source data itself.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from portfolioiq.data_notes import ALL_US_NOTE, SECURITY_NOTES
from portfolioiq.formatting import pct
from portfolioiq.verification import Check, CheckStatus, VerificationStatus

RESIDUAL_TOLERANCE = 0.0001  # 0.01 percentage points (rule B3)
RECONCILIATION_TOLERANCE = 1e-9

BONDS_LABEL = "Bonds (excluded)"
UNCLASSIFIED_LABEL = "Stocks without a sector (excluded)"
RESIDUAL_LABEL = "Unattributed weight residual"
METHOD_A_DENOMINATOR = "Whole portfolio (100% of portfolio weight)"


class HoldingWeight(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    company_name: str
    weight: float | None


class SectorExposure(BaseModel):
    model_config = ConfigDict(frozen=True)

    sector: str
    method_a: float  # share of the whole portfolio
    method_b: float  # share of the eligible stock weight (the equity sleeve)
    holdings: list[HoldingWeight]


class ExcludedLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    weight: float
    holdings: list[HoldingWeight]


class Reconciliation(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: CheckStatus
    max_abs_difference: float | None
    detail: str


class ExposureResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    portfolio_id: int
    portfolio_name: str
    status: Literal["ok", "no_eligible_stocks", "invalid_data"]
    message: str
    method_a_denominator: str
    method_b_denominator: str
    eligible_stock_weight: float
    sectors: list[SectorExposure]
    excluded: list[ExcludedLine]
    total_weight: float
    residual: float
    residual_reported: bool
    checks: list[Check]
    reconciliation: Reconciliation
    verification: VerificationStatus
    caveats: list[str]

    @property
    def method_a_total(self) -> float:
        """Sectors + excluded lines + residual. Equals 1.0 whenever Method A reconciles."""
        parts = [s.method_a for s in self.sectors] + [e.weight for e in self.excluded]
        return math.fsum(parts + [self.residual])


@dataclass(frozen=True)
class _Holding:
    symbol: str
    company_name: str
    asset_type: str
    sector: str | None
    weight: float | None
    country: str | None

    @property
    def eligible(self) -> bool:  # rule B2: a stock with a sector
        return self.asset_type != "Bond" and self.sector is not None


@dataclass(frozen=True)
class _Figures:
    method_a: dict[str, float]
    method_b: dict[str, float]
    equity: float
    bonds: float
    unclassified: float
    total: float
    residual: float


_HOLDINGS_SQL = """
SELECT s.symbol, s.company_name, s.asset_type, sec.sector_name, h.current_weight, s.country
FROM holdings AS h
JOIN securities AS s ON s.security_id = h.security_id
LEFT JOIN sectors AS sec ON sec.sector_id = s.sector_id
WHERE h.portfolio_id = ?
ORDER BY s.symbol
"""

# Path 2: the whole calculation inside SQLite. TOTAL() returns 0.0 for an empty set.
_SQL_PATH = """
WITH weighted AS (
    SELECT CASE
               WHEN s.asset_type = 'Bond' THEN 'bond'
               WHEN sec.sector_name IS NULL THEN 'unclassified'
               ELSE 'stock'
           END AS kind,
           sec.sector_name AS sector,
           h.current_weight AS weight
    FROM holdings AS h
    JOIN securities AS s ON s.security_id = h.security_id
    LEFT JOIN sectors AS sec ON sec.sector_id = s.sector_id
    WHERE h.portfolio_id = ? AND h.current_weight IS NOT NULL
),
totals AS (
    SELECT TOTAL(weight) AS total,
           TOTAL(CASE WHEN kind = 'stock' THEN weight END) AS equity,
           TOTAL(CASE WHEN kind = 'bond' THEN weight END) AS bonds,
           TOTAL(CASE WHEN kind = 'unclassified' THEN weight END) AS unclassified
    FROM weighted
)
SELECT w.sector,
       TOTAL(w.weight) AS method_a,
       CASE WHEN t.equity > 0 THEN TOTAL(w.weight) / t.equity END AS method_b,
       t.equity, t.bonds, t.unclassified, t.total, 1.0 - t.total AS residual
FROM totals AS t
LEFT JOIN weighted AS w ON w.kind = 'stock'
GROUP BY w.sector
"""


def calculate_exposure(conn: sqlite3.Connection, portfolio_id: int) -> ExposureResult:
    """Sector exposure for one portfolio, by Method A and Method B, with checks and reconciliation."""
    row = conn.execute(
        "SELECT portfolio_name, strategy_type FROM portfolios WHERE portfolio_id = ?", (portfolio_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"No portfolio with id {portfolio_id}")
    name, strategy = row
    holdings = [_Holding(*r) for r in conn.execute(_HOLDINGS_SQL, (portfolio_id,))]

    missing = [h for h in holdings if h.weight is None]
    invalid = [
        h for h in holdings
        if h.weight is not None and not (math.isfinite(h.weight) and 0.0 <= h.weight <= 1.0)
    ]
    duplicates = conn.execute(
        "SELECT COUNT(*) FROM (SELECT security_id FROM holdings WHERE portfolio_id = ? "
        "GROUP BY security_id HAVING COUNT(*) > 1)",
        (portfolio_id,),
    ).fetchone()[0]
    input_checks = [_missing_check(missing, len(holdings)), _invalid_check(invalid), _duplicate_check(duplicates)]
    if invalid or duplicates:
        return _invalid_result(portfolio_id, name, holdings, input_checks, invalid, duplicates)

    figures = _python_path(holdings)
    reconciliation = _reconcile(figures, _sql_path(conn, portfolio_id))
    residual_reported = abs(figures.residual) > RESIDUAL_TOLERANCE
    weighted = [h for h in holdings if h.weight is not None]

    sectors = sorted(
        (
            SectorExposure(
                sector=sector,
                method_a=figures.method_a[sector],
                method_b=figures.method_b[sector],
                holdings=_weights(h for h in weighted if h.eligible and h.sector == sector),
            )
            for sector in figures.method_a
        ),
        key=lambda s: (-s.method_a, s.sector),
    )
    excluded = []
    bonds = [h for h in weighted if h.asset_type == "Bond"]
    if bonds:
        excluded.append(ExcludedLine(label=BONDS_LABEL, weight=figures.bonds, holdings=_weights(bonds)))
    unclassified = [h for h in weighted if h.asset_type != "Bond" and h.sector is None]
    if unclassified:
        excluded.append(
            ExcludedLine(label=UNCLASSIFIED_LABEL, weight=figures.unclassified, holdings=_weights(unclassified))
        )

    stocks = [h for h in holdings if h.asset_type != "Bond"]
    method_a_sum = math.fsum(
        [*figures.method_a.values(), figures.bonds, figures.unclassified, figures.residual]
    )
    checks = [
        *input_checks,
        _residual_check(figures),
        _sector_check(stocks, unclassified),
        Check(
            name="Method A reconciles to 100%",
            status=CheckStatus.PASS if abs(method_a_sum - 1.0) <= RECONCILIATION_TOLERANCE else CheckStatus.FAIL,
            detail=f"Sectors + excluded lines + residual = {pct(method_a_sum)}%.",
            rule="B2, B3",
        ),
        _method_b_check(figures),
        Check(name="Independent reconciliation (Python and SQL)", status=reconciliation.status,
              detail=reconciliation.detail),
    ]
    failed = any(c.status is CheckStatus.FAIL for c in checks)
    verification = (
        VerificationStatus.INDEPENDENTLY_RECONCILED
        if reconciliation.status is CheckStatus.PASS and not failed
        else VerificationStatus.VERIFICATION_FAILED
    )
    has_equity = figures.equity > 0
    return ExposureResult(
        portfolio_id=portfolio_id,
        portfolio_name=name,
        status="ok" if has_equity else "no_eligible_stocks",
        message=_message(name, holdings, figures, len(sectors)),
        method_a_denominator=METHOD_A_DENOMINATOR,
        method_b_denominator=(
            f"Eligible stock weight ({pct(figures.equity)}% of portfolio)"
            if has_equity else "Not applicable: no eligible stocks"
        ),
        eligible_stock_weight=figures.equity,
        sectors=sectors,
        excluded=excluded,
        total_weight=figures.total,
        residual=figures.residual,
        residual_reported=residual_reported,
        checks=checks,
        reconciliation=reconciliation,
        verification=verification,
        caveats=_caveats(name, strategy, holdings, figures, residual_reported, missing, unclassified),
    )


def _python_path(holdings: list[_Holding]) -> _Figures:
    by_sector: dict[str, list[float]] = {}
    bonds: list[float] = []
    unclassified: list[float] = []
    for h in holdings:
        if h.weight is None:
            continue
        if h.asset_type == "Bond":
            bonds.append(h.weight)
        elif h.sector is None:
            unclassified.append(h.weight)
        else:
            by_sector.setdefault(h.sector, []).append(h.weight)
    stock_weights = [w for ws in by_sector.values() for w in ws]
    method_a = {sector: math.fsum(ws) for sector, ws in by_sector.items()}
    equity = math.fsum(stock_weights)
    method_b = {sector: a / equity for sector, a in method_a.items()} if equity > 0 else {}
    total = math.fsum([*stock_weights, *bonds, *unclassified])
    return _Figures(method_a, method_b, equity, math.fsum(bonds), math.fsum(unclassified), total, 1.0 - total)


def _sql_path(conn: sqlite3.Connection, portfolio_id: int) -> _Figures:
    rows = conn.execute(_SQL_PATH, (portfolio_id,)).fetchall()
    equity, bonds, unclassified, total, residual = rows[0][3:]
    method_a = {r[0]: r[1] for r in rows if r[0] is not None}
    method_b = {r[0]: r[2] for r in rows if r[0] is not None and r[2] is not None}
    return _Figures(method_a, method_b, equity, bonds, unclassified, total, residual)


def _reconcile(python: _Figures, sql: _Figures) -> Reconciliation:
    if set(python.method_a) != set(sql.method_a) or set(python.method_b) != set(sql.method_b):
        return Reconciliation(status=CheckStatus.FAIL, max_abs_difference=None,
                              detail="The Python and SQL paths found different sectors.")
    differences = [abs(python.method_a[s] - sql.method_a[s]) for s in python.method_a]
    differences += [abs(python.method_b[s] - sql.method_b[s]) for s in python.method_b]
    differences += [
        abs(getattr(python, f) - getattr(sql, f)) for f in ("equity", "bonds", "unclassified", "total", "residual")
    ]
    worst = max(differences)
    if worst <= RECONCILIATION_TOLERANCE:
        return Reconciliation(status=CheckStatus.PASS, max_abs_difference=worst,
                              detail=f"Python and SQL paths agree (max difference {worst:.1e}).")
    return Reconciliation(status=CheckStatus.FAIL, max_abs_difference=worst,
                          detail=f"Python and SQL paths disagree (max difference {worst:.1e}).")


def _weights(items: Iterable[_Holding]) -> list[HoldingWeight]:
    weights = [HoldingWeight(symbol=h.symbol, company_name=h.company_name, weight=h.weight) for h in items]
    return sorted(weights, key=lambda w: (-(w.weight or 0.0), w.symbol))


def _missing_check(missing: list[_Holding], count: int) -> Check:
    if not missing:
        return Check(name="Weights present", status=CheckStatus.PASS,
                     detail=f"All {count} holdings have a current_weight.", rule="B1")
    symbols = ", ".join(h.symbol for h in missing)
    return Check(name="Weights present", status=CheckStatus.WARNING,
                 detail=f"{len(missing)} holding(s) without current_weight, excluded from every figure: {symbols}.",
                 rule="B1")


def _invalid_check(invalid: list[_Holding]) -> Check:
    if not invalid:
        return Check(name="Weights valid (0 to 1)", status=CheckStatus.PASS,
                     detail="Every current_weight is between 0 and 1.", rule="B1")
    listed = ", ".join(f"{h.symbol} ({h.weight})" for h in invalid)
    return Check(name="Weights valid (0 to 1)", status=CheckStatus.FAIL,
                 detail=f"Weights outside 0 to 1: {listed}.", rule="B1")


def _duplicate_check(duplicates: int) -> Check:
    if duplicates == 0:
        return Check(name="No duplicate holdings", status=CheckStatus.PASS,
                     detail="Each security appears once in the portfolio.")
    return Check(name="No duplicate holdings", status=CheckStatus.FAIL,
                 detail=f"{duplicates} security(ies) appear more than once; weights would be double counted.")


def _residual_check(figures: _Figures) -> Check:
    if abs(figures.residual) <= RESIDUAL_TOLERANCE:
        return Check(name="Weights total 100%", status=CheckStatus.PASS,
                     detail=f"Holdings weights total {pct(figures.total)}%.", rule="B3")
    return Check(name="Weights total 100%", status=CheckStatus.WARNING,
                 detail=f"Holdings weights total {pct(figures.total)}%; "
                        f"unattributed weight residual {pct(figures.residual)}%.", rule="B3")


def _sector_check(stocks: list[_Holding], unclassified: list[_Holding]) -> Check:
    if not stocks:
        return Check(name="Stocks have a sector", status=CheckStatus.NOT_APPLICABLE,
                     detail="The portfolio holds no stocks.", rule="B2")
    if unclassified:
        symbols = ", ".join(h.symbol for h in unclassified)
        return Check(name="Stocks have a sector", status=CheckStatus.WARNING,
                     detail=f"Stocks without a sector, excluded from both methods: {symbols}.", rule="B2")
    return Check(name="Stocks have a sector", status=CheckStatus.PASS,
                 detail=f"All {len(stocks)} stocks have a sector.", rule="B2")


def _method_b_check(figures: _Figures) -> Check:
    if figures.equity <= 0:
        return Check(name="Method B totals 100%", status=CheckStatus.NOT_APPLICABLE,
                     detail="No eligible stocks, so there is no equity sleeve to divide by.", rule="B2")
    total = math.fsum(figures.method_b.values())
    ok = abs(total - 1.0) <= RECONCILIATION_TOLERANCE
    return Check(name="Method B totals 100%", status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                 detail=f"Method B sectors total {pct(total)}%.", rule="B2")


def _message(name: str, holdings: list[_Holding], figures: _Figures, sector_count: int) -> str:
    if not holdings:
        return f"{name} has no holdings recorded, so sector exposure is not applicable."
    if figures.equity <= 0:
        return (f"{name} holds no eligible stocks, so sector exposure is not applicable. "
                f"Bonds make up {pct(figures.bonds)}% of the portfolio.")
    return (f"{name}: {sector_count} sector(s) across an eligible stock weight of "
            f"{pct(figures.equity)}% of the portfolio.")


def _caveats(name: str, strategy: str | None, holdings: list[_Holding], figures: _Figures,
             residual_reported: bool, missing: list[_Holding], unclassified: list[_Holding]) -> list[str]:
    caveats = []
    if residual_reported:
        caveats.append(
            f"Holdings weights total {pct(figures.total)}%. The unattributed weight residual of "
            f"{pct(figures.residual)}% is shown separately; it is not cash and is not redistributed (rule B3)."
        )
    if missing:
        caveats.append(f"{len(missing)} holding(s) have no current_weight and are excluded from every "
                       f"figure: {', '.join(h.symbol for h in missing)}.")
    if unclassified:
        caveats.append(f"Stocks without a sector are excluded from both methods: "
                       f"{', '.join(h.symbol for h in unclassified)}.")
    for symbol in sorted({h.symbol for h in holdings if h.eligible and h.weight is not None}):
        if symbol in SECURITY_NOTES:
            caveats.append(SECURITY_NOTES[symbol])
    international = strategy == "International" or "international" in name.lower()
    if holdings and international and all(h.country == "US" for h in holdings):
        caveats.append(ALL_US_NOTE)
    return caveats


def _invalid_result(portfolio_id: int, name: str, holdings: list[_Holding], checks: list[Check],
                    invalid: list[_Holding], duplicates: int) -> ExposureResult:
    total = math.fsum(h.weight for h in holdings if h.weight is not None and math.isfinite(h.weight))
    problems = [f"weights outside 0 to 1 ({', '.join(h.symbol for h in invalid)})"] if invalid else []
    if duplicates:
        problems.append(f"{duplicates} duplicated security(ies)")
    return ExposureResult(
        portfolio_id=portfolio_id,
        portfolio_name=name,
        status="invalid_data",
        message=f"{name} has {' and '.join(problems)}; exposure is not calculated from invalid source data.",
        method_a_denominator=METHOD_A_DENOMINATOR,
        method_b_denominator="Not applicable: invalid source data",
        eligible_stock_weight=0.0,
        sectors=[],
        excluded=[],
        total_weight=total,
        residual=1.0 - total,
        residual_reported=False,
        checks=[*checks, Check(name="Independent reconciliation (Python and SQL)",
                               status=CheckStatus.NOT_APPLICABLE,
                               detail="Not run: the source weights are invalid.")],
        reconciliation=Reconciliation(status=CheckStatus.NOT_APPLICABLE, max_abs_difference=None,
                                      detail="Not run: the source weights are invalid."),
        verification=VerificationStatus.VERIFICATION_FAILED,
        caveats=[],
    )
