"""Independent verification of financial SQL answers, and a measurement-by-measurement reconciliation of a portfolio.

The generated SQL is never executed a second time. Its AST says which figure each output column claims to be. Plain Python then
rebuilds that figure from the source records, loaded by fixed queries, and compares it with the row the query returned.

Supported figures: market value (quantity x current price, per holding or summed), cost basis (summed), AUM (stated, summed),
stated weights (summed), and any percentage whose two sides are among these. Rows may be keyed by any source column, and
filtered by simple comparisons on source columns.

The status never claims more than was done:

- INDEPENDENTLY_RECONCILED: every calculated output column was rebuilt from source records and agrees.
- RESULT_VALIDATED: the result has no calculated figure, only values read directly from the database.
- NOT_INDEPENDENTLY_VERIFIABLE: at least one calculated figure could not be rebuilt. Nothing is said about its correctness.
- VERIFICATION_FAILED: a rebuilt figure disagrees with the returned row. The answer must not be used.
"""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import sqlglot
from pydantic import BaseModel, ConfigDict
from sqlglot import exp

from portfolioiq.sql_policy import Measure, OutputColumn, QueryShape, _find_ratio, _measure, _Scope, _strip, analyse
from portfolioiq.verification import VerificationStatus

MATCH_RELATIVE = 1e-9
MATCH_ABSOLUTE = 0.005  # the evaluation comparator's numeric tolerance
_KIND_LABEL = {"market_value": "market value (quantity x current price)", "cost_basis": "cost basis", "aum": "stated AUM", "weight": "stated weight"}


class VerifiedFigure(BaseModel):
    model_config = ConfigDict(frozen=True)

    column: str
    figure: str  # what the column was recognised as
    outcome: str  # reconciled | mismatch | not_verifiable
    detail: str
    max_difference: float | None = None


class FinancialVerification(BaseModel):
    """Recorded on the SQL result, apart from the structural checks, so earlier receipts keep their meaning."""

    model_config = ConfigDict(frozen=True)

    status: VerificationStatus
    summary: str
    rows_checked: int = 0
    figures: list[VerifiedFigure] = []


@dataclass(frozen=True)
class SourceRecords:
    """The source tables as plain Python rows. Keys are 'table.column'."""

    holdings: tuple[dict[str, Any], ...]  # joined in Python to their security, sector and portfolio
    portfolios: tuple[dict[str, Any], ...]

    @classmethod
    def load(cls, conn: sqlite3.Connection) -> SourceRecords:
        def table(name: str) -> list[dict[str, Any]]:
            cursor = conn.execute(f"SELECT * FROM {name}")  # fixed table names
            names = [f"{name}.{d[0].lower()}" for d in cursor.description]
            return [dict(zip(names, row)) for row in cursor]

        portfolios, securities, sectors = table("portfolios"), table("securities"), table("sectors")
        portfolio_by_id = {p["portfolios.portfolio_id"]: p for p in portfolios}
        security_by_id = {s["securities.security_id"]: s for s in securities}
        sector_by_id = {s["sectors.sector_id"]: s for s in sectors}
        empty_sector = {key: None for key in (sectors[0] if sectors else {})}
        holdings = []
        for holding in table("holdings"):
            security = security_by_id[holding["holdings.security_id"]]
            sector = sector_by_id.get(security["securities.sector_id"], empty_sector)
            holdings.append({**holding, **security, **sector, **portfolio_by_id[holding["holdings.portfolio_id"]]})
        return cls(tuple(holdings), tuple(portfolios))


@dataclass(frozen=True)
class _Recipe:
    """How to rebuild one measure from the source records."""

    kind: str
    aggregated: bool
    keep: Callable[[dict[str, Any]], bool]
    fill_zero: bool  # entities with no qualifying records are shown as zero by this query
    selects_rows: bool  # a HAVING-style condition decides which rows appear
    grouped: bool  # False: one value for the whole table, whatever the row's key

    @property
    def records_of_portfolios(self) -> bool:
        return self.kind == "aum"

    def value(self, record: dict[str, Any]) -> float | None:
        if self.kind == "market_value":
            quantity, price = record["holdings.quantity"], record["securities.current_price"]
            return None if quantity is None or price is None else float(quantity) * float(price)
        column = {"cost_basis": "holdings.cost_basis", "aum": "portfolios.total_aum", "weight": "holdings.current_weight"}[self.kind]
        return None if record[column] is None else float(record[column])


def verify_sql_result(sql: str, columns: Sequence[str], rows: Sequence[Sequence[Any]], schema: dict[str, dict[str, str]],
                      source: SourceRecords | None, *, truncated: bool = False) -> FinancialVerification:
    """Never raises: a defect here reports 'not verifiable' and can never mark an answer as reconciled."""
    try:
        return _verify(sql, columns, rows, schema, source, truncated)
    except Exception as exc:  # noqa: BLE001
        return _not_verifiable(f"The answer could not be analysed for independent verification ({type(exc).__name__}).")


def _not_verifiable(summary: str, figures: Sequence[VerifiedFigure] = (), rows: int = 0) -> FinancialVerification:
    return FinancialVerification(status=VerificationStatus.NOT_INDEPENDENTLY_VERIFIABLE, summary=summary, rows_checked=rows, figures=list(figures))


def _verify(sql: str, columns: Sequence[str], rows: Sequence[Sequence[Any]], schema, source: SourceRecords | None, truncated: bool) -> FinancialVerification:
    tree = sqlglot.parse_one(sql, read="sqlite")
    if not isinstance(tree, exp.Select):
        return _not_verifiable("A set operation cannot be reconstructed from source records.")
    shape, scope = analyse(tree, schema)
    if any(o.kind == "star" for o in shape.outputs):
        return FinancialVerification(status=VerificationStatus.RESULT_VALIDATED, summary="The result lists stored columns; it has no calculated figure.")
    calculated = [i for i, o in enumerate(shape.outputs) if o.kind in ("aggregate", "calculated")]
    if not calculated:
        return FinancialVerification(status=VerificationStatus.RESULT_VALIDATED, summary="The result has no calculated figure, only values read from the database.")
    if source is None:
        return _not_verifiable("The source records are unavailable.")
    if truncated:
        return _not_verifiable("The result was cut at an output limit, so it cannot be compared in full.")
    if len(columns) != len(shape.outputs):
        return _not_verifiable("The result's columns do not line up with the query's output.")
    if not rows:
        return _not_verifiable("The query returned no rows to compare.")
    keys = [i for i, o in enumerate(shape.outputs) if o.kind == "column"]
    figures = [_check_output(index, tree, shape, scope, keys, rows, source) for index in calculated]
    failed = [f for f in figures if f.outcome == "mismatch"]
    if failed:
        return FinancialVerification(status=VerificationStatus.VERIFICATION_FAILED, rows_checked=len(rows), figures=figures,
                                     summary="; ".join(f"{f.column}: {f.detail}" for f in failed))
    if any(f.outcome == "not_verifiable" for f in figures):
        return _not_verifiable("Not every calculated figure could be rebuilt from source records: "
                               + "; ".join(f"{f.column} ({f.detail})" for f in figures if f.outcome == "not_verifiable"), figures, len(rows))
    return FinancialVerification(status=VerificationStatus.INDEPENDENTLY_RECONCILED, rows_checked=len(rows), figures=figures,
                                 summary=f"{len(figures)} calculated column(s) rebuilt from source records across {len(rows)} row(s); all agree.")


def _not_supported(output: OutputColumn, why: str) -> VerifiedFigure:
    return VerifiedFigure(column=output.name, figure="unrecognised calculation", outcome="not_verifiable", detail=why)


def _mismatch(output: OutputColumn, figure: str, detail: str, difference: float | None = None) -> VerifiedFigure:
    return VerifiedFigure(column=output.name, figure=figure, outcome="mismatch", detail=detail, max_difference=difference)


def _check_output(index: int, tree: exp.Select, shape: QueryShape, scope: _Scope, keys: list[int], rows: Sequence[Sequence[Any]],
                  source: SourceRecords) -> VerifiedFigure:
    output, expression = shape.outputs[index], tree.expressions[index].unalias()
    division, ratio_scope, scaled = _find_ratio(expression, scope)
    tolerance = _rounding_tolerance(expression)
    key_columns = [(i, next(iter(shape.outputs[i].base_columns))) for i in keys if len(shape.outputs[i].base_columns) == 1]
    if len(key_columns) != len(keys):
        return _not_supported(output, "a row key is not a single source column")
    if division is not None:
        return _check_ratio(index, output, division, ratio_scope, scaled, key_columns, rows, source, tolerance)
    if output.measure is None:
        return _not_supported(output, "no measure could be traced")
    factor = _literal_factor(output.sql)
    if factor is None:
        return _not_supported(output, "it is scaled in a way that cannot be reconstructed")
    recipe = _recipe(output.measure, _is_grouped(expression, scope), scope.schema)
    if isinstance(recipe, str):
        return _not_supported(output, recipe)
    if (problem := _keys_available(recipe, key_columns, source)):
        return _not_supported(output, problem)
    label, worst, count = _KIND_LABEL[recipe.kind], 0.0, 0
    groups = _groups(recipe, key_columns, source)
    for row in rows:
        expected = _expected(recipe, key_columns, row, source)
        if expected is None:
            return _mismatch(output, label, f"row {_row_label(key_columns, row)} has no source records")
        expected *= factor
        got = row[index]
        if not _agree(got, expected, tolerance):
            return _mismatch(output, label, f"row {_row_label(key_columns, row)}: returned {got}, source records give {expected:.4f}",
                             None if not _is_number(got) else abs(got - expected))
        worst, count = max(worst, abs(got - expected)), count + 1
    # Rows can be narrowed by another measure (a share lists only entities with the part), so completeness applies only when one population is in play.
    populations = {o.measure.conditions for o in shape.outputs if o.kind in ("aggregate", "calculated") and o.measure is not None}
    completeness = _completeness(recipe, key_columns, rows, groups, shape, index, factor) if len(populations) == 1 else None
    if completeness:
        return _mismatch(output, label, completeness)
    return VerifiedFigure(column=output.name, figure=label, outcome="reconciled", max_difference=worst,
                          detail=f"{count} value(s) rebuilt from {'portfolio' if recipe.records_of_portfolios else 'holding'} records.")


def _check_ratio(index: int, output: OutputColumn, division: exp.Div, scope: _Scope, scaled: bool, key_columns, rows, source: SourceRecords,
                 tolerance: float) -> VerifiedFigure:
    if isinstance(_strip(division.right), exp.Literal):
        return _not_supported(output, "it divides by a fixed number")
    sides = []
    for side in (division.left, division.right):
        recipe = _recipe(_measure(scope.trace(side), scope.schema, ratio_side=True), _is_grouped(side, scope), scope.schema)
        if isinstance(recipe, str):
            return _not_supported(output, recipe)
        if (problem := _keys_available(recipe, key_columns, source)):
            return _not_supported(output, problem)
        sides.append(recipe)
    label = f"percentage: {_KIND_LABEL[sides[0].kind]} over {_KIND_LABEL[sides[1].kind]}" if scaled else f"ratio: {_KIND_LABEL[sides[0].kind]} over {_KIND_LABEL[sides[1].kind]}"
    worst = 0.0
    for row in rows:
        numerator, denominator = _expected(sides[0], key_columns, row, source), _expected(sides[1], key_columns, row, source)
        if numerator is None or not denominator:
            return _mismatch(output, label, f"row {_row_label(key_columns, row)} has no source records for one side of the division")
        expected = (100.0 if scaled else 1.0) * numerator / denominator
        if not _agree(row[index], expected, tolerance):
            return _mismatch(output, label, f"row {_row_label(key_columns, row)}: returned {row[index]}, source records give {expected:.4f}",
                             None if not _is_number(row[index]) else abs(row[index] - expected))
        worst = max(worst, abs(row[index] - expected))
    return VerifiedFigure(column=output.name, figure=label, outcome="reconciled", max_difference=worst, detail=f"{len(rows)} value(s) rebuilt from both sides of the division.")


def _recipe(measure: Measure, grouped: bool, schema) -> _Recipe | str:
    """How to rebuild a measure, or the reason it cannot be rebuilt."""
    node = _strip(sqlglot.parse_one(measure.value, read="sqlite"))
    aggregated = isinstance(node, exp.Sum)
    inner = _strip(node.this) if aggregated else node
    kind = None
    if isinstance(inner, exp.Mul) and {_name(_strip(inner.left)), _name(_strip(inner.right))} == {"holdings.quantity", "securities.current_price"}:
        kind = "market_value"
    elif aggregated and _name(inner) == "holdings.cost_basis":
        kind = "cost_basis"
    elif aggregated and _name(inner) == "portfolios.total_aum":
        kind = "aum"
    elif aggregated and _name(inner) == "holdings.current_weight":
        kind = "weight"
    if kind is None:
        return f"{measure.value} is not a supported financial calculation"
    predicates, selects_rows = [], False
    for text in measure.conditions:
        condition = sqlglot.parse_one(text, read="sqlite")
        if condition.find(exp.AggFunc) is not None:
            selects_rows = True
            continue
        predicate = _predicate(condition)
        if predicate is None:
            return f"the condition {text} cannot be reconstructed"
        predicates.append(predicate)
    if measure.drops_unclassified:
        predicates.append(lambda record: record.get("sectors.sector_id") is not None)
    if kind == "aum" and any("holdings." in t or "securities." in t or "sectors." in t for t in measure.conditions):
        return "AUM filtered through holdings cannot be reconstructed"
    if kind == "aum" and measure.drops_unclassified:
        return "AUM filtered through holdings cannot be reconstructed"
    return _Recipe(kind=kind, aggregated=aggregated, keep=lambda record: all(p(record) for p in predicates),
                   fill_zero=measure.conditional or measure.zero_filled, selects_rows=selects_rows, grouped=grouped)


def _name(node: exp.Expression) -> str | None:
    return f"{node.table}.{node.name}" if isinstance(node, exp.Column) and node.table else None


def _predicate(condition: exp.Expression) -> Callable[[dict[str, Any]], bool] | None:
    """A simple comparison between one source column and fixed values; anything else is not reconstructed."""
    compare = {exp.EQ: lambda a, b: a == b, exp.NEQ: lambda a, b: a != b, exp.GT: lambda a, b: a > b, exp.GTE: lambda a, b: a >= b,
               exp.LT: lambda a, b: a < b, exp.LTE: lambda a, b: a <= b}
    condition = _strip(condition)
    if type(condition) in compare and _name(_strip(condition.left)) and _is_value(condition.right):
        column, wanted, test = _name(_strip(condition.left)), _value(condition.right), compare[type(condition)]
        return lambda record: record.get(column) is not None and test(record[column], wanted)
    if isinstance(condition, exp.In) and _name(_strip(condition.this)) and condition.expressions and all(_is_value(v) for v in condition.expressions):
        column, wanted = _name(_strip(condition.this)), {_value(v) for v in condition.expressions}
        return lambda record: record.get(column) in wanted
    if isinstance(condition, exp.Not) and isinstance(condition.this, exp.Is) and isinstance(condition.this.expression, exp.Null) and _name(condition.this.this):
        column = _name(condition.this.this)
        return lambda record: record.get(column) is not None
    if isinstance(condition, exp.Is) and isinstance(condition.expression, exp.Null) and _name(condition.this):
        column = _name(condition.this)
        return lambda record: record.get(column) is None
    return None


def _is_value(node: exp.Expression) -> bool:
    return isinstance(_strip(node), exp.Literal)


def _value(node: exp.Expression) -> Any:
    literal = _strip(node)
    return literal.this if literal.is_string else float(literal.this)


def _is_grouped(expression: exp.Expression, scope: _Scope) -> bool:
    """Whether the figure is computed per group, following aliases and CTEs. A scalar subquery is one value for the whole query."""
    node, current = expression, scope
    for _ in range(12):
        node = _strip(node)
        if isinstance(node, exp.Mul):  # a fixed factor does not change where the value comes from
            node = _strip(node.left) if not isinstance(_strip(node.left), exp.Literal) else _strip(node.right)
            continue
        if isinstance(node, exp.Subquery):
            return False
        if isinstance(node, exp.Column):
            found = current.definition(node)
            if found is None:
                return True  # a stored value belongs to its row
            current, node = found
            continue
        return current.select.args.get("group") is not None or node.find(exp.AggFunc) is None
    return True


def _keys_available(recipe: _Recipe, key_columns, source: SourceRecords) -> str | None:
    if not recipe.grouped:
        return None
    pool = source.portfolios if recipe.records_of_portfolios else source.holdings
    missing = [column for _, column in key_columns if pool and column not in pool[0]]
    return f"the row key {missing[0]} is not available from these records" if missing else None


def _pool(recipe: _Recipe, source: SourceRecords) -> Sequence[dict[str, Any]]:
    return source.portfolios if recipe.records_of_portfolios else source.holdings


def _expected(recipe: _Recipe, key_columns, row: Sequence[Any], source: SourceRecords) -> float | None:
    """The figure for this row from the source records; None when no record supports the row."""
    wanted = [(column, row[i]) for i, column in key_columns] if recipe.grouped else []
    in_key = [r for r in _pool(recipe, source) if all(_same(r.get(column), value) for column, value in wanted)]
    matching = [r for r in in_key if recipe.keep(r)]
    values = [v for v in (recipe.value(r) for r in matching) if v is not None]
    if not values:
        return 0.0 if recipe.fill_zero and in_key else None
    if not recipe.aggregated and len(values) != 1:
        return None
    return math.fsum(values)


def _groups(recipe: _Recipe, key_columns, source: SourceRecords) -> dict[tuple, float]:
    totals: dict[tuple, list[float]] = defaultdict(list)
    for record in _pool(recipe, source):
        if recipe.keep(record):
            value = recipe.value(record)
            if value is not None:
                totals[tuple(record.get(column) for _, column in key_columns)].append(value)
    return {key: math.fsum(values) for key, values in totals.items()}


def _completeness(recipe: _Recipe, key_columns, rows, groups: dict[tuple, float], shape: QueryShape, index: int, factor: float) -> str | None:
    """Nothing that should be listed is missing, and a top-N really holds the top values."""
    if not key_columns or not recipe.grouped or recipe.selects_rows or recipe.fill_zero or not recipe.aggregated:
        return None
    returned = {tuple(row[i] for i, _ in key_columns) for row in rows}
    if shape.limit is None:
        absent = [key for key in groups if key not in returned]
        return f"{len(absent)} group(s) with source records are missing from the result" if absent else None
    if shape.order_by and shape.outputs[index].base_columns and shape.order_by[0].base_columns == shape.outputs[index].base_columns:
        expected = sorted((v * factor for v in groups.values()), reverse=shape.order_by[0].descending)[:shape.limit]
        got = [row[index] for row in rows]
        if len(got) == len(expected) and not all(_agree(a, b, MATCH_ABSOLUTE) for a, b in zip(got, expected)):
            return "the rows returned are not the top values in the source records"
    return None


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)
    return a == b


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _agree(got: Any, expected: float, tolerance: float) -> bool:
    return _is_number(got) and math.isclose(got, expected, rel_tol=MATCH_RELATIVE, abs_tol=tolerance)


def _rounding_tolerance(expression: exp.Expression) -> float:
    rounding = next(iter(expression.find_all(exp.Round)), None)
    if rounding is None:
        return MATCH_ABSOLUTE
    digits = rounding.args.get("decimals")
    places = int(digits.this) if isinstance(digits, exp.Literal) and digits.is_int else 0
    return max(MATCH_ABSOLUTE, 0.51 * 10 ** (-places))


def _literal_factor(sql: str) -> float | None:
    """A fixed multiplier on the whole value, 1.0 when there is none, None when the value is scaled some other way."""
    node = _strip(sqlglot.parse_one(sql, read="sqlite"))
    if isinstance(node, exp.Div):
        return None
    if isinstance(node, exp.Mul):
        for side in (node.left, node.right):
            literal = _strip(side)
            if isinstance(literal, exp.Literal) and not literal.is_string:
                return float(literal.this)
    return 1.0


def _row_label(key_columns, row: Sequence[Any]) -> str:
    return ", ".join(str(row[i]) for i, _ in key_columns) or "(single figure)"


# --- portfolio reconciliation: the separate measurements of one portfolio, never merged -----------------------------------

class PortfolioReconciliation(BaseModel):
    model_config = ConfigDict(frozen=True)

    portfolio_name: str
    stated_aum: float | None
    market_value_total: float
    market_value_stocks: float
    market_value_bonds: float
    holdings_value_share_of_aum: float | None
    weight_recorded: float
    weight_stocks_classified: float
    weight_stocks_unclassified: float
    weight_bonds: float
    missing_weights: int
    residual: float
    sectors: list[dict[str, Any]]
    warnings: list[str]


def reconcile_portfolio(source: SourceRecords, portfolio_id: int) -> PortfolioReconciliation:
    """Weights, market values, AUM and residual for one portfolio, each with its own denominator, rebuilt from source records."""
    portfolio = next(p for p in source.portfolios if p["portfolios.portfolio_id"] == portfolio_id)
    holdings = [h for h in source.holdings if h["holdings.portfolio_id"] == portfolio_id]
    weights: dict[str, list[float]] = defaultdict(list)
    values: dict[str, list[float]] = defaultdict(list)
    missing, stock_mv, bond_mv, bond_weight, classified, unclassified = 0, [], [], [], [], []
    for holding in holdings:
        weight, is_bond, sector = holding["holdings.current_weight"], holding["securities.asset_type"] == "Bond", holding["sectors.sector_name"]
        value = float(holding["holdings.quantity"] or 0) * float(holding["securities.current_price"] or 0)
        (bond_mv if is_bond else stock_mv).append(value)
        if weight is None:
            missing += 1
        elif is_bond:
            bond_weight.append(float(weight))
        elif sector is None:
            unclassified.append(float(weight))
        else:
            classified.append(float(weight))
            weights[sector].append(float(weight))
        if not is_bond and sector is not None:
            values[sector].append(value)
    recorded = math.fsum([*classified, *unclassified, *bond_weight])
    total_value = math.fsum([*stock_mv, *bond_mv])
    aum = portfolio["portfolios.total_aum"]
    sectors = [{"sector": name, "weight": math.fsum(weights.get(name, [])), "market_value": math.fsum(values.get(name, [])),
                "market_value_share": math.fsum(values.get(name, [])) / total_value if total_value else None}
               for name in sorted(set(weights) | set(values), key=lambda s: -math.fsum(weights.get(s, [])))]
    warnings = []
    if missing:
        warnings.append(f"{missing} holding(s) have no recorded weight and are left out of the weight totals.")
    if unclassified:
        warnings.append("Some stocks have no sector. Their weight is reported separately and is in no sector row.")
    if abs(1.0 - recorded) > 0.0001:
        warnings.append(f"Recorded weights total {recorded * 100:.2f}%. The residual is unattributed; it is not cash.")
    if aum and total_value / float(aum) < 0.5:
        warnings.append(f"Recorded holdings are worth {total_value / float(aum) * 100:.2f}% of stated AUM, so the two figures measure different things.")
    return PortfolioReconciliation(
        portfolio_name=portfolio["portfolios.portfolio_name"], stated_aum=float(aum) if aum is not None else None, market_value_total=total_value,
        market_value_stocks=math.fsum(stock_mv), market_value_bonds=math.fsum(bond_mv), holdings_value_share_of_aum=total_value / float(aum) if aum else None,
        weight_recorded=recorded, weight_stocks_classified=math.fsum(classified), weight_stocks_unclassified=math.fsum(unclassified), weight_bonds=math.fsum(bond_weight),
        missing_weights=missing, residual=1.0 - recorded, sectors=sectors, warnings=warnings)
