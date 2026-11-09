"""Financial Invariant Engine: database-wide checks on the supplied data (M5; scope moved from M2).

Every check is read-only and deterministic. Statuses use the shared vocabulary:

- PASS: the invariant holds.
- WARNING: a documented anomaly or limitation. Results stay usable, and answers carry a caveat.
- FAIL: results built on this data would be wrong. Nothing depending on it may be presented as verified.
- NOT_APPLICABLE: the check cannot run (for example, no checksum file).

The supplied records are never modified or corrected (rule D6); anomalies are reported, not fixed.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from portfolioiq.data_notes import SECURITY_NOTES
from portfolioiq.db import row_counts
from portfolioiq.exposure import RESIDUAL_TOLERANCE
from portfolioiq.formatting import pct
from portfolioiq.verification import CheckStatus

MONEY_TOLERANCE = 0.005  # half a cent: values are compared as stored, never rounded first
MAX_DETAIL_ROWS = 50


class InvariantCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    status: CheckStatus
    scope: str
    detail: str
    effect: str  # what the result means for answers built on this data
    rule: str | None = None
    rows: list[dict] = []  # supporting evidence, at most MAX_DETAIL_ROWS


class WeightTotal(BaseModel):
    model_config = ConfigDict(frozen=True)

    portfolio_id: int
    portfolio_name: str
    holdings: int
    total_weight: float
    stock_weight: float
    bond_weight: float
    residual: float


class IntegrityReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    as_of: str | None
    row_counts: dict[str, int]
    checks: list[InvariantCheck]
    weight_totals: list[WeightTotal]

    def count(self, status: CheckStatus) -> int:
        return sum(1 for c in self.checks if c.status is status)

    @property
    def has_failures(self) -> bool:
        return any(c.status is CheckStatus.FAIL for c in self.checks)


def open_for_checks(db_path: Path) -> sqlite3.Connection:
    """A read-only connection for these trusted, fixed checks.

    It has no authorizer because PRAGMA integrity_check and foreign_key_check must run; the URI is
    still read-only, so nothing can be written. User or model SQL never runs on this connection.
    """
    return sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)


def run_invariants(conn: sqlite3.Connection, data_dir: Path | None = None) -> IntegrityReport:
    """Run every invariant check. ``data_dir`` enables the source-file checksum check."""
    totals = weight_totals(conn)
    checks = [
        _checksums(data_dir),
        _integrity(conn),
        _foreign_keys(conn),
        _weights_valid(conn),
        _duplicates(conn),
        _weights_total(totals),
        _classification(conn),
        _bond_terms(conn),
        _cost_basis(conn),
        _prices_valid(conn),
        _current_vs_close(conn),
        _aum_coverage(conn),
        _reconciliation(conn),
        _transactions_valid(conn),
        _coverage(conn),
        _stored_values(conn),
        _benchmarks(conn),
        _sector_notes(conn),
        _country_currency(conn),
        _weight_basis(conn),
        _empty_sectors(conn),
        _date_anchors(conn),
    ]
    return IntegrityReport(as_of=_as_of(conn), row_counts=row_counts(conn), checks=checks, weight_totals=totals)


def weight_totals(conn: sqlite3.Connection) -> list[WeightTotal]:
    rows = conn.execute(
        """
        SELECT p.portfolio_id, p.portfolio_name, COUNT(h.holding_id),
               TOTAL(h.current_weight),
               TOTAL(CASE WHEN s.asset_type = 'Stock' THEN h.current_weight END),
               TOTAL(CASE WHEN s.asset_type = 'Bond' THEN h.current_weight END)
        FROM portfolios p
        LEFT JOIN holdings h ON h.portfolio_id = p.portfolio_id
        LEFT JOIN securities s ON s.security_id = h.security_id
        GROUP BY p.portfolio_id
        ORDER BY p.portfolio_id
        """
    ).fetchall()
    return [WeightTotal(portfolio_id=pid, portfolio_name=name, holdings=n, total_weight=total, stock_weight=stocks,
                        bond_weight=bonds, residual=1.0 - total) for pid, name, n, total, stocks, bonds in rows]


# --- individual checks ---------------------------------------------------------------------------

def _checksums(data_dir: Path | None) -> InvariantCheck:
    name, scope = "Source files match their recorded checksums", "supplied CSV files and schema"
    sums = data_dir / "SHA256SUMS" if data_dir is not None else None
    if sums is None or not sums.exists():
        return InvariantCheck(id="I01", name=name, status=CheckStatus.NOT_APPLICABLE, scope=scope,
                              detail="No SHA256SUMS file was provided, so the files could not be compared.",
                              effect="The data's provenance is not confirmed by this check.", rule="D6")
    rows, bad = [], []
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected, relative = line.split(None, 1)
        relative = relative.strip().lstrip("*")
        if not relative.startswith("data/"):
            continue  # runtime checks cover the data the app uses; other files are checked by the test suite
        path = data_dir / relative.removeprefix("data/")  # entries are recorded relative to the project root
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        ok = actual == expected
        rows.append({"file": relative, "matches": ok})
        if not ok:
            bad.append(relative)
    if bad:
        return InvariantCheck(id="I01", name=name, status=CheckStatus.FAIL, scope=f"{len(rows)} files",
                              detail=f"{len(bad)} file(s) differ from the recorded checksum: {', '.join(bad)}.",
                              effect="The data is not the supplied data; no result can be trusted.", rule="D6",
                              rows=rows)
    return InvariantCheck(id="I01", name=name, status=CheckStatus.PASS, scope=f"{len(rows)} files",
                          detail=f"All {len(rows)} files are byte-identical to the supplied originals.",
                          effect="Results are computed from the supplied data, unmodified.", rule="D6", rows=rows)


def _integrity(conn: sqlite3.Connection) -> InvariantCheck:
    result = [r[0] for r in conn.execute("PRAGMA integrity_check")]
    ok = result == ["ok"]
    return InvariantCheck(
        id="I02", name="Database integrity", status=CheckStatus.PASS if ok else CheckStatus.FAIL,
        scope="whole database", detail="SQLite integrity_check returned ok." if ok
        else f"SQLite integrity_check reported {len(result)} problem(s).",
        effect="The database file is structurally sound." if ok else "Query results may be wrong.",
        rows=[] if ok else [{"problem": r} for r in result[:MAX_DETAIL_ROWS]])


def _foreign_keys(conn: sqlite3.Connection) -> InvariantCheck:
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if not violations:
        return InvariantCheck(id="I03", name="Foreign keys resolve", status=CheckStatus.PASS, scope="all tables",
                              detail="0 violations: every holding, transaction, price and metric points to an "
                                     "existing portfolio or security.",
                              effect="Joins between tables lose no rows.")
    rows = [{"table": t, "rowid": r, "parent": p} for t, r, p, _ in violations[:MAX_DETAIL_ROWS]]
    return InvariantCheck(id="I03", name="Foreign keys resolve", status=CheckStatus.FAIL, scope="all tables",
                          detail=f"{len(violations)} row(s) reference a parent row that does not exist.",
                          effect="Joins silently drop these rows, so totals and counts would be wrong.", rows=rows)


def _weights_valid(conn: sqlite3.Connection) -> InvariantCheck:
    rows = conn.execute(
        """
        SELECT p.portfolio_name, s.symbol, h.current_weight
        FROM holdings h JOIN portfolios p ON p.portfolio_id = h.portfolio_id
        JOIN securities s ON s.security_id = h.security_id
        WHERE h.current_weight IS NULL OR h.current_weight < 0 OR h.current_weight > 1
        ORDER BY p.portfolio_id, s.symbol
        """
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
    invalid = [r for r in rows if r[2] is not None]  # below 0 or above 1 (SQLite stores NaN as NULL)
    missing = [r for r in rows if r[2] is None]
    evidence = [{"portfolio": p, "symbol": s, "current_weight": w} for p, s, w in rows[:MAX_DETAIL_ROWS]]
    if invalid:
        return InvariantCheck(id="I04", name="Holding weights present and between 0 and 1", status=CheckStatus.FAIL,
                              scope=f"{total} holdings", detail=f"{len(invalid)} weight(s) outside 0 to 1.",
                              effect="Exposure is not calculated for the affected portfolios (rule E2).",
                              rule="B1, E1, E2", rows=evidence)
    if missing:
        return InvariantCheck(id="I04", name="Holding weights present and between 0 and 1", status=CheckStatus.WARNING,
                              scope=f"{total} holdings", detail=f"{len(missing)} holding(s) have no current_weight.",
                              effect="Those holdings are excluded from exposure and named in a caveat (rule E1).",
                              rule="B1, E1, E2", rows=evidence)
    return InvariantCheck(id="I04", name="Holding weights present and between 0 and 1", status=CheckStatus.PASS,
                          scope=f"{total} holdings", detail=f"All {total} holdings have a current_weight between 0 and 1.",
                          effect="Exposure can be calculated for every portfolio.", rule="B1, E1, E2")


def _duplicates(conn: sqlite3.Connection) -> InvariantCheck:
    rows = conn.execute(
        """
        SELECT p.portfolio_name, s.symbol, COUNT(*)
        FROM holdings h JOIN portfolios p ON p.portfolio_id = h.portfolio_id
        JOIN securities s ON s.security_id = h.security_id
        GROUP BY h.portfolio_id, h.security_id HAVING COUNT(*) > 1
        """
    ).fetchall()
    if rows:
        return InvariantCheck(id="I05", name="No duplicate holdings", status=CheckStatus.FAIL, scope="all portfolios",
                              detail=f"{len(rows)} security(ies) appear more than once in the same portfolio.",
                              effect="Weights would be double counted; exposure is not calculated (rule E3).",
                              rule="E3", rows=[{"portfolio": p, "symbol": s, "count": n} for p, s, n in rows])
    return InvariantCheck(id="I05", name="No duplicate holdings", status=CheckStatus.PASS, scope="all portfolios",
                          detail="Each security appears at most once per portfolio.",
                          effect="No weight is counted twice.", rule="E3")


def _weights_total(totals: list[WeightTotal]) -> InvariantCheck:
    off = [t for t in totals if t.holdings and abs(t.residual) > RESIDUAL_TOLERANCE]
    rows = [{"portfolio": t.portfolio_name, "total_weight_pct": pct(t.total_weight), "residual_pct": pct(t.residual)}
            for t in off]
    if off:
        listed = "; ".join(f"{t.portfolio_name} {pct(t.total_weight)}% (residual {pct(t.residual)}%)" for t in off)
        return InvariantCheck(
            id="I06", name="Holding weights total 100% per portfolio", status=CheckStatus.WARNING,
            scope=f"{len(totals)} portfolios", detail=f"{len(off)} portfolio(s) do not total 100%: {listed}.",
            effect="Method A shows the difference as an unattributed weight residual; it is never treated as cash "
                   "or redistributed (rule B3).", rule="B3", rows=rows)
    return InvariantCheck(id="I06", name="Holding weights total 100% per portfolio", status=CheckStatus.PASS,
                          scope=f"{len(totals)} portfolios", detail="Every portfolio's weights total 100%.",
                          effect="Method A needs no residual line.", rule="B3")


def _classification(conn: sqlite3.Connection) -> InvariantCheck:
    stocks = [r[0] for r in conn.execute(
        "SELECT symbol FROM securities WHERE asset_type = 'Stock' AND sector_id IS NULL ORDER BY symbol")]
    bonds = [r[0] for r in conn.execute(
        "SELECT symbol FROM securities WHERE asset_type = 'Bond' AND sector_id IS NOT NULL ORDER BY symbol")]
    counts = dict(conn.execute("SELECT asset_type, COUNT(*) FROM securities GROUP BY asset_type").fetchall())
    scope = f"{sum(counts.values())} securities ({counts.get('Stock', 0)} stocks, {counts.get('Bond', 0)} bonds)"
    if stocks or bonds:
        parts = ([f"stocks without a sector: {', '.join(stocks)}"] if stocks else []) + (
            [f"bonds with a sector: {', '.join(bonds)}"] if bonds else [])
        return InvariantCheck(id="I07", name="Asset classification is complete", status=CheckStatus.WARNING,
                              scope=scope, detail="; ".join(parts) + ".",
                              effect="Stocks without a sector are shown as an excluded line (rule E4); bonds are "
                                     "always excluded from exposure, whatever their sector field says.",
                              rule="B2, E4")
    return InvariantCheck(id="I07", name="Asset classification is complete", status=CheckStatus.PASS, scope=scope,
                          detail="Every stock has a sector and no bond has one.",
                          effect="Every stock is eligible for sector exposure (rule B2).", rule="B2, E4")


def _bond_terms(conn: sqlite3.Connection) -> InvariantCheck:
    missing = [r[0] for r in conn.execute(
        "SELECT symbol FROM securities WHERE asset_type = 'Bond' AND (maturity_date IS NULL OR coupon_rate IS NULL)")]
    bonds = conn.execute("SELECT COUNT(*) FROM securities WHERE asset_type = 'Bond'").fetchone()[0]
    if not bonds:
        return InvariantCheck(id="I08", name="Bond terms recorded", status=CheckStatus.NOT_APPLICABLE,
                              scope="0 bonds", detail="The data holds no bonds.", effect="None.")
    if missing:
        return InvariantCheck(id="I08", name="Bond terms recorded", status=CheckStatus.WARNING, scope=f"{bonds} bonds",
                              detail=f"Bonds without a maturity date or coupon rate: {', '.join(missing)}.",
                              effect="Questions about those bond terms return missing values.")
    return InvariantCheck(id="I08", name="Bond terms recorded", status=CheckStatus.PASS, scope=f"{bonds} bonds",
                          detail=f"All {bonds} bonds have a maturity date and a coupon rate.",
                          effect="Bond-term questions can be answered from the data.")


def _cost_basis(conn: sqlite3.Connection) -> InvariantCheck:
    rows = conn.execute(
        """
        SELECT p.portfolio_name, s.symbol, h.quantity, h.purchase_price, h.cost_basis
        FROM holdings h JOIN portfolios p ON p.portfolio_id = h.portfolio_id
        JOIN securities s ON s.security_id = h.security_id
        WHERE h.cost_basis IS NULL OR h.purchase_price IS NULL
           OR ABS(h.cost_basis - h.quantity * h.purchase_price) > ?
        """, (MONEY_TOLERANCE,)
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
    if rows:
        evidence = [{"portfolio": p, "symbol": s, "quantity": q, "purchase_price": pp, "cost_basis": cb}
                    for p, s, q, pp, cb in rows[:MAX_DETAIL_ROWS]]
        return InvariantCheck(id="I09", name="Cost basis equals quantity x purchase price", status=CheckStatus.WARNING,
                              scope=f"{total} holdings", detail=f"{len(rows)} holding(s) differ or have missing values.",
                              effect="Cost-basis answers report the stored cost_basis; it is not recomputed.",
                              rows=evidence)
    return InvariantCheck(id="I09", name="Cost basis equals quantity x purchase price", status=CheckStatus.PASS,
                          scope=f"{total} holdings", detail=f"0 mismatches across {total} holdings (tolerance 0.005).",
                          effect="Stored cost basis is internally consistent.")


def _prices_valid(conn: sqlite3.Connection) -> InvariantCheck:
    bad_current = conn.execute(
        "SELECT symbol, current_price FROM securities WHERE current_price IS NULL OR current_price <= 0").fetchall()
    bad_history = conn.execute(
        """
        SELECT s.symbol, hp.price_date FROM historical_prices hp JOIN securities s ON s.security_id = hp.security_id
        WHERE hp.close_price <= 0 OR hp.high_price < hp.low_price
           OR hp.close_price > hp.high_price OR hp.close_price < hp.low_price
           OR hp.open_price > hp.high_price OR hp.open_price < hp.low_price
        """
    ).fetchall()
    securities = conn.execute("SELECT COUNT(*) FROM securities").fetchone()[0]
    prices = conn.execute("SELECT COUNT(*) FROM historical_prices").fetchone()[0]
    scope = f"{securities} current prices, {prices} daily prices"
    if bad_current or bad_history:
        rows = [{"symbol": s, "issue": "current_price missing or not positive"} for s, _ in bad_current]
        rows += [{"symbol": s, "issue": f"inconsistent prices on {d}"} for s, d in bad_history]
        return InvariantCheck(id="I10", name="Prices are positive and internally consistent", status=CheckStatus.FAIL,
                              scope=scope, detail=f"{len(bad_current)} invalid current price(s), "
                                                  f"{len(bad_history)} inconsistent daily price row(s).",
                              effect="Market values built on these prices would be wrong.", rule="D2, B11",
                              rows=rows[:MAX_DETAIL_ROWS])
    return InvariantCheck(id="I10", name="Prices are positive and internally consistent", status=CheckStatus.PASS,
                          scope=scope, detail="Every current price is positive; every daily row has low <= open, "
                                              "close <= high and a positive close.",
                          effect="Market values (quantity x current_price) use valid prices.", rule="D2, B11")


def _current_vs_close(conn: sqlite3.Connection) -> InvariantCheck:
    latest = conn.execute("SELECT MAX(price_date) FROM historical_prices").fetchone()[0]
    rows = conn.execute(
        """
        SELECT s.symbol, s.current_price, hp.close_price
        FROM securities s JOIN historical_prices hp ON hp.security_id = s.security_id AND hp.price_date = ?
        ORDER BY s.symbol
        """, (latest,)
    ).fetchall()
    differ = [r for r in rows if r[1] is not None and abs(r[1] - r[2]) > MONEY_TOLERANCE]
    if not rows:
        return InvariantCheck(id="I11", name="Current price versus latest daily close", status=CheckStatus.NOT_APPLICABLE,
                              scope="no daily prices", detail="No daily prices to compare.", effect="None.", rule="B11")
    evidence = [{"symbol": s, "current_price": cp, f"close_{latest}": close} for s, cp, close in differ[:MAX_DETAIL_ROWS]]
    if differ:
        return InvariantCheck(
            id="I11", name="Current price versus latest daily close", status=CheckStatus.WARNING,
            scope=f"{len(rows)} securities", detail=f"securities.current_price differs from the {latest} close for "
                                                   f"{len(differ)} of {len(rows)} securities.",
            effect="'Current price' and market value use securities.current_price as supplied (rule B11, as in "
                   "Q7's reference SQL), never the last historical close. The two can give different values.",
            rule="B11", rows=evidence)
    return InvariantCheck(id="I11", name="Current price versus latest daily close", status=CheckStatus.PASS,
                          scope=f"{len(rows)} securities", detail=f"current_price equals the {latest} close for "
                                                                 "every security.",
                          effect="Either price basis gives the same values.", rule="B11")


def _aum_coverage(conn: sqlite3.Connection) -> InvariantCheck:
    rows = conn.execute(
        """
        SELECT p.portfolio_name, p.total_aum, TOTAL(h.quantity * s.current_price)
        FROM portfolios p LEFT JOIN holdings h ON h.portfolio_id = p.portfolio_id
        LEFT JOIN securities s ON s.security_id = h.security_id
        GROUP BY p.portfolio_id ORDER BY p.portfolio_id
        """
    ).fetchall()
    shares = [(name, aum, value, value / aum) for name, aum, value in rows if aum]
    if not shares:
        return InvariantCheck(id="I12", name="Recorded holdings cover stated AUM", status=CheckStatus.NOT_APPLICABLE,
                              scope="0 portfolios with AUM", detail="No stated AUM to compare.", effect="None.", rule="D4")
    low, high = min(s[3] for s in shares), max(s[3] for s in shares)
    evidence = [{"portfolio": n, "total_aum": a, "holdings_market_value": round(v, 2), "coverage_pct": pct(c)}
                for n, a, v, c in shares]
    status = CheckStatus.PASS if all(abs(c - 1.0) <= 0.001 for *_, c in shares) else CheckStatus.WARNING
    return InvariantCheck(
        id="I12", name="Recorded holdings cover stated AUM", status=status, scope=f"{len(shares)} portfolios",
        detail=f"Holdings market value is {pct(low)}% to {pct(high)}% of stated total_aum.",
        effect="AUM is the stated total_aum field and is never summed from holdings; holdings value is never "
               "called AUM, and answers say so when it matters (rule D4).", rule="D4", rows=evidence)


def _reconciliation(conn: sqlite3.Connection) -> InvariantCheck:
    rows = conn.execute(
        """
        WITH tx AS (
            SELECT portfolio_id, security_id,
                   TOTAL(CASE WHEN transaction_type = 'BUY' THEN quantity ELSE -quantity END) AS net
            FROM transactions GROUP BY portfolio_id, security_id
        )
        SELECT p.portfolio_name, s.symbol, h.quantity, tx.net
        FROM tx JOIN portfolios p ON p.portfolio_id = tx.portfolio_id
        JOIN securities s ON s.security_id = tx.security_id
        LEFT JOIN holdings h ON h.portfolio_id = tx.portfolio_id AND h.security_id = tx.security_id
        ORDER BY tx.portfolio_id, s.symbol
        """
    ).fetchall()
    if not rows:
        return InvariantCheck(id="I13", name="Holdings reconcile with transactions", status=CheckStatus.NOT_APPLICABLE,
                              scope="0 positions with transactions", detail="No transactions recorded.",
                              effect="None.", rule="D6")
    gaps = [(n, s, held, net) for n, s, held, net in rows if held is None or abs(held - net) > 1e-9]
    evidence = [{"portfolio": n, "symbol": s, "holdings_quantity": held, "net_transactions": net,
                 "gap": None if held is None else held - net} for n, s, held, net in gaps[:MAX_DETAIL_ROWS]]
    if gaps:
        return InvariantCheck(
            id="I13", name="Holdings reconcile with transactions", status=CheckStatus.WARNING,
            scope=f"{len(rows)} positions with transactions",
            detail=f"{len(gaps)} of {len(rows)} positions do not reconcile to the holdings quantity.",
            effect="These are reconciliation gaps, not proven errors: the transaction history may be incomplete. "
                   "Holdings are used as recorded; transaction answers carry this caveat (rule D6).",
            rule="D6", rows=evidence)
    return InvariantCheck(id="I13", name="Holdings reconcile with transactions", status=CheckStatus.PASS,
                          scope=f"{len(rows)} positions with transactions",
                          detail="Every position's net transactions equal its holdings quantity.",
                          effect="Transaction history explains the recorded positions.", rule="D6")


def _transactions_valid(conn: sqlite3.Connection) -> InvariantCheck:
    rows = conn.execute(
        """
        SELECT transaction_id, transaction_type, quantity, price, fees, transaction_date, settlement_date
        FROM transactions
        WHERE quantity <= 0 OR price <= 0 OR fees < 0
           OR (settlement_date IS NOT NULL AND settlement_date < transaction_date)
        """
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    if rows:
        evidence = [dict(zip(("transaction_id", "type", "quantity", "price", "fees", "trade_date", "settlement_date"), r))
                    for r in rows[:MAX_DETAIL_ROWS]]
        return InvariantCheck(id="I14", name="Transactions have valid values", status=CheckStatus.FAIL,
                              scope=f"{total} transactions", detail=f"{len(rows)} transaction(s) have a non-positive "
                                                                   "quantity or price, negative fees, or settle before "
                                                                   "the trade date.",
                              effect="Answers about these transactions would be wrong.", rows=evidence)
    return InvariantCheck(id="I14", name="Transactions have valid values", status=CheckStatus.PASS,
                          scope=f"{total} transactions",
                          detail="Quantities and prices are positive, fees are not negative, and no transaction "
                                 "settles before its trade date.", effect="Transaction values can be used as stored.")


def _coverage(conn: sqlite3.Connection) -> InvariantCheck:
    portfolios = conn.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0]
    rows = []
    for table, column in (("portfolio_performance", "performance_date"), ("risk_metrics", "calculation_date")):
        ids = [r[0] for r in conn.execute(f"SELECT DISTINCT portfolio_id FROM {table} ORDER BY 1")]
        first, last = conn.execute(f"SELECT MIN({column}), MAX({column}) FROM {table}").fetchone()
        rows.append({"table": table, "portfolios": ", ".join(map(str, ids)) or "none", "count": len(ids),
                     "dates": f"{first} to {last}" if first else "none"})
    covered = max(r["count"] for r in rows)
    status = CheckStatus.PASS if all(r["count"] == portfolios for r in rows) else CheckStatus.WARNING
    return InvariantCheck(
        id="I15", name="Performance and risk data cover every portfolio", status=status,
        scope=f"{portfolios} portfolios",
        detail=f"Performance and risk data exist for {covered} of {portfolios} portfolios "
               f"(portfolio_id {rows[0]['portfolios']}).",
        effect="Questions about other portfolios are answered 'no data', never estimated (rule D6).",
        rule="D6", rows=rows)


def _stored_values(conn: sqlite3.Connection) -> InvariantCheck:
    bad_nav = conn.execute("SELECT COUNT(*) FROM portfolio_performance WHERE nav <= 0").fetchone()[0]
    var_order = conn.execute(
        "SELECT COUNT(*) FROM risk_metrics WHERE var_95 IS NOT NULL AND var_99 IS NOT NULL "
        "AND ABS(var_99) < ABS(var_95)").fetchone()[0]
    rows = conn.execute("SELECT COUNT(*) FROM portfolio_performance").fetchone()[0] + \
        conn.execute("SELECT COUNT(*) FROM risk_metrics").fetchone()[0]
    if bad_nav or var_order:
        return InvariantCheck(id="I16", name="Performance and risk values are plausible", status=CheckStatus.WARNING,
                              scope=f"{rows} rows", detail=f"{bad_nav} non-positive NAV value(s); {var_order} row(s) "
                                                           "where 99% VaR is smaller in magnitude than 95% VaR.",
                              effect="Values are still reported as stored, with this caveat (rule D7).", rule="D7")
    return InvariantCheck(id="I16", name="Performance and risk values are plausible", status=CheckStatus.PASS,
                          scope=f"{rows} rows", detail="NAV is positive and 99% VaR is never smaller in magnitude "
                                                       "than 95% VaR. Units are not documented in the source.",
                          effect="Values are reported as stored, with no invented units or annualisation (rule D7).",
                          rule="D7")


def _benchmarks(conn: sqlite3.Connection) -> InvariantCheck:
    rows = conn.execute(
        """
        SELECT p.portfolio_name, p.benchmark_index, b.benchmark_name
        FROM portfolios p LEFT JOIN benchmarks b ON b.benchmark_symbol = p.benchmark_index
        ORDER BY p.portfolio_id
        """
    ).fetchall()
    unmatched = [r for r in rows if r[1] is not None and r[2] is None]
    evidence = [{"portfolio": n, "benchmark_index": i, "benchmark_name": b} for n, i, b in rows]
    if unmatched:
        return InvariantCheck(id="I17", name="Portfolio benchmarks link to the benchmark list", status=CheckStatus.WARNING,
                              scope=f"{len(rows)} portfolios",
                              detail=f"{len(unmatched)} benchmark_index value(s) match no benchmark symbol.",
                              effect="Benchmark names cannot be shown for those portfolios.", rows=evidence)
    return InvariantCheck(id="I17", name="Portfolio benchmarks link to the benchmark list", status=CheckStatus.PASS,
                          scope=f"{len(rows)} portfolios",
                          detail="Every portfolios.benchmark_index matches a benchmarks.benchmark_symbol. The schema "
                                 "declares no foreign key, so the link is checked here.",
                          effect="Benchmark names can be shown beside each portfolio.", rows=evidence)


def _sector_notes(conn: sqlite3.Connection) -> InvariantCheck:
    present = [r[0] for r in conn.execute("SELECT symbol FROM securities ORDER BY symbol") if r[0] in SECURITY_NOTES]
    if not present:
        return InvariantCheck(id="I18", name="Sector classification review", status=CheckStatus.PASS,
                              scope="manual review notes", detail="No reviewed classification anomaly is present.",
                              effect="None.", rule="D6")
    affected = conn.execute(
        f"SELECT p.portfolio_name, s.symbol FROM holdings h JOIN securities s ON s.security_id = h.security_id "
        f"JOIN portfolios p ON p.portfolio_id = h.portfolio_id WHERE s.symbol IN ({','.join('?' * len(present))}) "
        "ORDER BY p.portfolio_id", present).fetchall()
    return InvariantCheck(
        id="I18", name="Sector classification review", status=CheckStatus.WARNING,
        scope=f"{_n(len(present), 'security', 'securities')}, {_n(len(affected), 'holding')}",
        detail=" ".join(SECURITY_NOTES[s] for s in present),
        effect="The supplied classification is used as is and flagged in every affected exposure answer (rule D6).",
        rule="D6", rows=[{"portfolio": p, "symbol": s} for p, s in affected])


def _country_currency(conn: sqlite3.Connection) -> InvariantCheck:
    countries = conn.execute("SELECT country, COUNT(*) FROM securities GROUP BY country ORDER BY 2 DESC").fetchall()
    currencies = conn.execute("SELECT currency, COUNT(*) FROM securities GROUP BY currency ORDER BY 2 DESC").fetchall()
    total = sum(n for _, n in countries)
    detail = (f"Countries: {', '.join(f'{c} ({n})' for c, n in countries)}. "
              f"Currencies: {', '.join(f'{c} ({n})' for c, n in currencies)}.")
    if len(countries) == 1:
        return InvariantCheck(id="I19", name="Geographic and currency coverage", status=CheckStatus.WARNING,
                              scope=f"{total} securities", detail=detail,
                              effect="Every security has the same country, so no answer claims geographic "
                                     "diversification, including for funds named International or Emerging "
                                     "Markets (rule D6).", rule="D6")
    return InvariantCheck(id="I19", name="Geographic and currency coverage", status=CheckStatus.PASS,
                          scope=f"{total} securities", detail=detail, effect="Country fields vary.", rule="D6")


def _weight_basis(conn: sqlite3.Connection) -> InvariantCheck:
    rows = conn.execute(
        """
        WITH v AS (
            SELECT h.portfolio_id, s.symbol, h.current_weight AS stated,
                   h.quantity * s.current_price / SUM(h.quantity * s.current_price) OVER (PARTITION BY h.portfolio_id)
                     AS by_value
            FROM holdings h JOIN securities s ON s.security_id = h.security_id
        )
        SELECT p.portfolio_name, v.symbol, v.stated, v.by_value
        FROM v JOIN portfolios p ON p.portfolio_id = v.portfolio_id
        WHERE v.stated IS NOT NULL
        """
    ).fetchall()
    if not rows:
        return InvariantCheck(id="I20", name="Stated weights versus market-value weights", status=CheckStatus.NOT_APPLICABLE,
                              scope="0 holdings", detail="No holdings.", effect="None.", rule="B1")
    worst: dict[str, tuple] = {}
    for name, symbol, stated, by_value in rows:
        gap = abs(stated - by_value)
        if name not in worst or gap > worst[name][3]:
            worst[name] = (symbol, stated, by_value, gap)
    biggest = max(worst.items(), key=lambda item: item[1][3])
    evidence = [{"portfolio": n, "largest_gap_symbol": s, "stated_pct": pct(st), "market_value_pct": pct(bv)}
                for n, (s, st, bv, _) in worst.items()]
    name, (symbol, stated, by_value, gap) = biggest
    status = CheckStatus.WARNING if gap > RESIDUAL_TOLERANCE else CheckStatus.PASS
    return InvariantCheck(
        id="I20", name="Stated weights versus market-value weights", status=status, scope=f"{len(rows)} holdings",
        detail=f"current_weight differs from each holding's share of market value; the largest gap is {symbol} in "
               f"{name}: {pct(stated)}% stated versus {pct(by_value)}% by value.",
        effect="Exposure always uses current_weight (rule B1). Market-value answers, such as Q7's reference SQL, "
               "can therefore differ from exposure figures for the same portfolio.", rule="B1, D2", rows=evidence)


def _empty_sectors(conn: sqlite3.Connection) -> InvariantCheck:
    empty = [r[0] for r in conn.execute(
        "SELECT sector_name FROM sectors WHERE sector_id NOT IN "
        "(SELECT sector_id FROM securities WHERE sector_id IS NOT NULL) ORDER BY sector_name")]
    total = conn.execute("SELECT COUNT(*) FROM sectors").fetchone()[0]
    detail = (f"{len(empty)} of {total} sectors have no securities: {', '.join(empty)}." if empty
              else f"All {total} sectors have at least one security.")
    return InvariantCheck(id="I21", name="Sector reference list", status=CheckStatus.PASS, scope=f"{total} sectors",
                          detail=detail, effect="Sector listings built from securities show only populated sectors "
                                                "(Q6's reference result has 7 rows, not 10).")


def _date_anchors(conn: sqlite3.Connection) -> InvariantCheck:
    anchors = [
        ("historical_prices", "price_date"), ("portfolio_performance", "performance_date"),
        ("risk_metrics", "calculation_date"), ("transactions", "transaction_date"),
        ("transactions", "settlement_date"), ("holdings", "purchase_date"),
    ]
    rows = []
    for table, column in anchors:
        first, last = conn.execute(f"SELECT MIN({column}), MAX({column}) FROM {table}").fetchone()
        rows.append({"dataset": f"{table}.{column}", "first": first, "latest": last})
    return InvariantCheck(
        id="I22", name="Date anchors are read from the data", status=CheckStatus.PASS, scope=f"{len(rows)} date columns",
        detail="; ".join(f"{r['dataset']} ends {r['latest']}" for r in rows) + ".",
        effect="Relative dates ('past 7 days', 'last month') are anchored to the latest date of the relevant dataset, "
               "never the computer's date (rules B9, B10).", rule="B9, B10", rows=rows)


def _n(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else plural or singular + 's'}"


def _as_of(conn: sqlite3.Connection) -> str | None:
    return conn.execute(
        """
        SELECT MAX(d) FROM (
            SELECT MAX(price_date) AS d FROM historical_prices
            UNION ALL SELECT MAX(performance_date) FROM portfolio_performance
            UNION ALL SELECT MAX(calculation_date) FROM risk_metrics
        )
        """
    ).fetchone()[0]


__all__ = ["InvariantCheck", "IntegrityReport", "WeightTotal", "open_for_checks", "run_invariants", "weight_totals"]
