"""Result-shape policy v2.4, a separate candidate. The production v2.3 policy stays in sql_policy.py, unchanged.

v2.4 differs in four ways: a GROUP BY on a column the question names is a grouped answer, not a defect; COUNT(*) keeps its
filters when traced; a latest value for an entity must use that entity's own latest date; and the operands of a ratio are
judged by what the question asks to see.

Does a safe query return what was asked for?

sql_guard.py decides whether a query is safe. This module decides whether a safe query has the right shape.
It reads two independent things and compares them:

- the question, as a QuestionIntent: the entity asked about, the operation, the grain, the named attributes, the
  ranking metric and the population. These are deterministic wording rules. When a rule is unsure it leaves the
  field empty, and the checks that depend on that field do not run.
- the SQL, as a QueryShape from the SQLGlot AST: only the outermost SELECT's output columns count, and every output
  and ORDER BY key is traced through aliases, subqueries and CTEs to the base expression that defines it, so an alias
  such as total_value proves nothing by itself.

assess() compares them before execution; assess_result() adds what only executed rows can settle (the arithmetic of a
percentage, a zero denominator, duplicates, sort order, the independently computed portfolio value).

An issue is an "error" only when the defect is established; anything static analysis cannot settle is a "warning" or
is left to the result checks. The policy repairs nothing: an error means the draft needs repair, and the SQL tool asks
Gemini once more with the suggested actions. Only the candidate SQL prompt uses this module; the baseline never does.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

import sqlglot
from sqlglot import exp

from portfolioiq.sql_policy import PolicyFinding, PolicyReport

POLICY_VERSION = "2.4"
MAX_DEPTH = 12  # how far an alias may be followed; deeper queries are reported as not analysed
PERCENT_TOLERANCE = 0.06  # percentage points: allows rounding to one decimal, catches a wrong formula
VALUE_TOLERANCE = 0.01

Severity = Literal["error", "warning"]
Population = Literal["not_applicable", "qualifying_only", "all_entities"]

ENTITY_WORDS = {
    "portfolio": "portfolio", "portfolios": "portfolio", "fund": "portfolio", "funds": "portfolio",
    "holding": "holding", "holdings": "holding", "position": "holding", "positions": "holding",
    "security": "security", "securities": "security", "stock": "security", "stocks": "security",
    "bond": "security", "bonds": "security", "company": "security", "companies": "security",
    "sector": "sector", "sectors": "sector",
    "transaction": "transaction", "transactions": "transaction", "trade": "transaction", "trades": "transaction",
    "benchmark": "benchmark", "benchmarks": "benchmark",
}
DIMENSIONS = ("asset class", "asset type", "risk level", "strategy type", "strategy", "exchange", "country", "currency",
              "benchmark", "sector", "portfolio", "fund", "security", "stock", "bond", "month", "year", "status", "type")
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_NUMBER = r"\d+|" + "|".join(NUMBER_WORDS)
_STOP = r"(?:\s+(?:in|for|across|among|of|from|within|at|on|and|with)\b|[,.?!]|$)"
_METRIC_AFTER_BY = re.compile(rf"\bby\s+([a-z_ ]+?){_STOP}")
_SUPERLATIVE = r"highest|largest|biggest|greatest|lowest|smallest"
_METRIC_AFTER_SUPERLATIVE = re.compile(rf"\b(?:{_SUPERLATIVE})\s+([a-z_ ]+?){_STOP}")
_TOP_N = re.compile(rf"\b(?:top|first|bottom)\s+({_NUMBER})\b")
_SEVERAL = r"\d+|" + "|".join(w for w in NUMBER_WORDS if w != "one")  # "which one has the largest" is not a top-N with a LIMIT
_N_NEAR_SUPERLATIVE = re.compile(rf"\b({_SEVERAL})\s+(?:\w+\s+){{0,3}}?(?:{_SUPERLATIVE}|cheapest)\b|"
                                 rf"\b({_SEVERAL})\s+\w+\s+(?:with|having|that have)\s+the\s+(?:{_SUPERLATIVE}|cheapest|most|least)\b")
_DESCENDING = re.compile(r"(?<!at )\b(top|highest|largest|biggest|greatest|most)\b(?!\s+recent)")
_ASCENDING = re.compile(r"(?<!at )\b(bottom|lowest|smallest|cheapest|least|fewest)\b")
_SHARE = re.compile(r"\b(?:percent|percentage|percentages|proportion)\b|%|\bshare\s+of\b|\bratio\s+of\b.*\bto\b|\bshare\b.*\btotal\b")
_AGGREGATE = re.compile(r"\b(?:how many|how much|count|number of|total|sum|average|avg|mean|combined)\b")
_ALL_ENTITIES = re.compile(r"\b(?:all|every)\s+(?:of\s+)?(?:the\s+|our\s+)?(?:\w+\s+)?(?:portfolios?|funds?|sectors?)\b|"
                           r"\binclud\w*\s+(?:those|any|portfolios|funds|sectors)\b|\beven\s+(?:those|if)\b|"
                           r"\b(?:show|with|as)\s+(?:0|zero)\b|\bzero\s+rows?\b")
_STATED_POPULATION = re.compile(r"\b(?:that|which|who)\s+(?:holds?|have|has|owns?)\b|\bholding\s+\w+|\bonly\b|\bwith\s+(?:any|some)\b")
_WHOLE_IS_PORTFOLIO_VALUE = re.compile(r"\bportfolio(?:'s)?\s+(?:market\s+)?value\b|\btotal\s+(?:market\s+)?value\s+of\s+the\s+portfolio\b")
_LATEST = re.compile(r"\b(?:latest|most recent|newest)\b")
_LATEST_OVERALL = re.compile(r"\bacross (?:all|the whole|the entire)\b|\bin the (?:\w+ )?(?:database|dataset|table|history)\b|\boverall latest\b")
_ASKS_OPERANDS = re.compile(r"\bmetrics\b|\bcomponents?\b|\bnumerator\b|\bdenominator\b|\bbehind\b|\bhow (?:it|this) is calculated\b")
_VALUE_WORD = r"(?:how many|number|count|value|amount|total)"
_ASKS_VALUES = re.compile(rf"\b{_VALUE_WORD}\b[^.?!]*\b(?:and|with)\b[^.?!]*\b(?:percent|percentage|share|proportion)\b|"
                          rf"\b(?:percent|percentage|share|proportion)\b[^.?!]*\b(?:and|with)\b[^.?!]*\b{_VALUE_WORD}\b")
_ASKS_WHICH = re.compile(r"(?:^\s*|[,.;]\s*)which\b")
_ALSO_SHOW = re.compile(r"\b(?:show|display|list|along with|together with|including|(?:and|with) (?:its|their))\b")
_ALIASES = {"aum": ("total_aum",), "coupon": ("coupon_rate",), "maturity": ("maturity_date",), "weight": ("current_weight",),
            "market value": ("quantity", "current_price"), "value": ("quantity", "current_price")}
_PERCENT_ALIAS = re.compile(r"pct|percent|share|ratio|proportion")
_TOTAL_ALIAS = re.compile(r"total|whole|denominator|overall|portfolio_value")
RECORD_KEYS = {"holding": "holdings.holding_id", "transaction": "transactions.transaction_id"}
POSITION_COLUMNS = ("holdings.holding_id", "securities.symbol", "securities.company_name", "holdings.quantity")
NUMERIC_FAILURES = frozenset({"percentage_mismatch", "numerator_exceeds_denominator", "denominator_zero", "denominator_mismatch"})


@dataclass(frozen=True)
class Vocabulary:
    """Names read from the database at start-up, so the policy holds no data of its own."""

    portfolios: tuple[str, ...] = ()
    sectors: tuple[str, ...] = ()


@dataclass(frozen=True)
class QuestionIntent:
    """What the question asks for; empty fields mean the wording rules could not tell."""

    primary_entity: str | None
    operation: Literal["list", "rank", "aggregate", "share"]
    grain: Literal["scalar", "per_group", "per_record", "unknown"]
    group_entity: str | None
    top_n: int | None
    direction: Literal["desc", "asc"] | None
    rank_metric: tuple[str, ...]  # base column names the ORDER BY must use; empty when no column could be named
    named_columns: frozenset[str]  # schema columns the question mentions
    subset_terms: tuple[str, ...]  # the sector or asset class a share question restricts its part to
    scoped_portfolio: str | None  # the one portfolio named in the question
    population: Population
    population_stated: bool  # False when the wording leaves open whether entities with nothing are listed
    whole_is_portfolio_value: bool
    which_only: bool = False  # "which X has the most Y" asks for X; Y only sorts, unless the question also asks to see it
    asks_operands: bool = False  # "metrics", "components" and the like ask to see the values behind a ratio
    asks_values: bool = False  # a share question that also asks to see a count or value
    latest_scope: Literal["entity", "overall"] | None = None  # whose latest date a "latest" question means
    single_entity_lookup: bool = False  # one named entity and its attributes: the entity needs no identifying column


@dataclass(frozen=True)
class OutputColumn:
    """One column of the outermost SELECT, traced to its base expression."""

    name: str
    kind: Literal["column", "aggregate", "calculated", "literal", "star"]
    base_columns: frozenset[str]  # table.column
    sql: str
    measure: Measure | None = None


@dataclass(frozen=True)
class Measure:
    """A traced value with everything that restricts it, so that two figures can be compared by meaning."""

    value: str  # canonical SQL of the value, with any CASE wrapper removed
    conditions: frozenset[str]  # WHERE, HAVING, ON-with-literal and CASE WHEN conditions that apply to it
    columns: frozenset[str]
    drops_unclassified: bool  # an INNER JOIN to sectors removes securities with no sector, which means every bond
    conditional: bool  # CASE-based: rows outside the condition still count, as a zero or a NULL
    zero_filled: bool  # COALESCE(x, 0) or a LEFT JOIN keeps entities that have nothing
    is_integer: bool = False  # COUNT or an integer column, with no real number in the expression
    grouped: bool = False  # computed per group, so it can differ from row to row

    @property
    def key(self) -> tuple[str, frozenset[str], bool]:
        return self.value, self.conditions, self.grouped


@dataclass(frozen=True)
class OrderKey:
    sql: str
    base_columns: frozenset[str]
    descending: bool


@dataclass(frozen=True)
class ShareRoles:
    """Where the parts of a percentage are: output positions, or None when the query does not return them."""

    percentage: int
    numerator: int | None
    denominator: int | None
    numerator_measure: Measure
    denominator_measure: Measure


@dataclass(frozen=True)
class QueryShape:
    outputs: tuple[OutputColumn, ...]
    order_by: tuple[OrderKey, ...]
    limit: int | None
    offset: int | None
    distinct: bool
    grouped: bool
    group_columns: frozenset[str]  # bare names of the columns the outermost GROUP BY uses
    nested_order: bool  # an ORDER BY exists only inside a subquery or CTE
    share: ShareRoles | None = None


@dataclass(frozen=True)
class PolicyIssue:
    code: str
    severity: Severity
    detail: str
    repair: str = ""  # what a corrected query must do; shown to Gemini only on a retry

    @property
    def numeric(self) -> bool:
        return self.code in NUMERIC_FAILURES


@dataclass(frozen=True)
class PolicyAssessment:
    intent: QuestionIntent
    shape: QueryShape | None
    issues: tuple[PolicyIssue, ...] = ()
    version: str = POLICY_VERSION

    @property
    def needs_repair(self) -> bool:
        """The query has an established defect. Nothing has been changed: a corrected query must be requested."""
        return any(i.severity == "error" for i in self.issues)

    @property
    def suggested_repairs(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(i.repair or i.detail for i in self.issues if i.severity == "error"))

    def retry_feedback(self) -> str:
        return "The result does not fit the question:\n" + "\n".join(f"- {text}" for text in self.suggested_repairs)

    def roles(self) -> dict[str, str]:
        return {} if self.shape is None else {o.name: _role(o, self.intent, self.shape) for o in self.shape.outputs}

    def report(self, *, repaired: bool = False, retry_failed: str | None = None) -> PolicyReport:
        intent = self.intent
        return PolicyReport(
            version=self.version,
            intent={"primary_entity": intent.primary_entity, "operation": intent.operation, "grain": intent.grain,
                    "group_entity": intent.group_entity, "top_n": intent.top_n, "direction": intent.direction,
                    "ranking_metric": list(intent.rank_metric), "named_columns": sorted(intent.named_columns),
                    "population": intent.population},
            output_roles=self.roles(), repaired=repaired, retry_failed=retry_failed,
            findings=[PolicyFinding(code=i.code, severity=i.severity, detail=i.detail) for i in self.issues])

    def caveats(self) -> list[str]:
        return [i.detail for i in self.issues if i.code == "population_assumed"]


# --- the question -----------------------------------------------------------------------------------------------

def read_intent(question: str, schema: dict[str, dict[str, str]], vocabulary: Vocabulary = Vocabulary()) -> QuestionIntent:
    """Deterministic reading of the question. It knows the schema and the database's names, and nothing else."""
    text, scoped, sectors = _masked(question, vocabulary)
    columns = {c.lower() for cols in schema.values() for c in cols}
    group = _group_entity(text)
    top_n = _top_n(text)
    direction = "asc" if _ASCENDING.search(text) else "desc" if _DESCENDING.search(text) else None
    if _SHARE.search(text):
        operation = "share"
    elif top_n is not None or direction is not None:
        operation = "rank"
    elif _AGGREGATE.search(text):
        operation = "aggregate"
    else:
        operation = "list"
    remainder = re.sub(r"\b(?:for each|in each|at each|within each|across each|for every|for all|per|of each)\s+(?:the\s+)?\w+", " ", text) if group else text
    primary = next((ENTITY_WORDS[w] for w in re.findall(r"[a-z_]+", remainder) if w in ENTITY_WORDS), None)
    if operation == "share" and group:
        primary = ENTITY_WORDS.get(group, group)
    if operation in ("aggregate", "share"):
        grain = "per_group" if group else "scalar"
    elif primary is not None:
        grain = "per_record"
    else:
        grain = "unknown"
    subset = list(sectors)
    if operation == "share" and not subset:
        subset += [word.capitalize() for word in ("bond", "stock") if re.search(rf"\b{word}s?\b", text)]
    population: Population = "not_applicable"
    stated = True
    if operation == "share" and group and subset:
        population = "all_entities" if _ALL_ENTITIES.search(text) else "qualifying_only"
        stated = population == "all_entities" or bool(_STATED_POPULATION.search(text))
    named = _named_columns(text, columns)
    latest_scope = None
    if _LATEST.search(text):
        latest_scope = "overall" if _LATEST_OVERALL.search(text) else "entity" if scoped or group or re.search(r"\beach\b", text) else None
    return QuestionIntent(
        asks_operands=bool(_ASKS_OPERANDS.search(text)), asks_values=bool(_ASKS_VALUES.search(text)), latest_scope=latest_scope,
        primary_entity=primary, operation=operation, grain=grain, group_entity=group, top_n=top_n, direction=direction,
        rank_metric=_rank_metric(text, columns) if operation == "rank" else (),
        which_only=(operation == "rank" and primary in ("portfolio", "security", "sector") and bool(_ASKS_WHICH.search(text))
                    and not _ALSO_SHOW.search(text)),
        single_entity_lookup=len(scoped) == 1 and primary is None and operation == "list" and bool(named),
        named_columns=named, subset_terms=tuple(subset),
        scoped_portfolio=scoped[0] if len(scoped) == 1 else None, population=population, population_stated=stated,
        whole_is_portfolio_value=bool(_WHOLE_IS_PORTFOLIO_VALUE.search(text)) and not re.search(r"\b(?:equity|stock)\s+(?:value|sleeve)\b", text),
    )


def _masked(question: str, vocabulary: Vocabulary) -> tuple[str, list[str], list[str]]:
    """Lower-case the question and hide proper names, so 'Growth Equity Fund' or 'Technology sector' is not an entity word."""
    text = f" {question.lower().strip()} "
    portfolios, sectors = [], []
    for name in sorted(vocabulary.portfolios, key=len, reverse=True):
        pattern = rf"\b{re.escape(name.lower())}(?:'s)?\b"
        if re.search(pattern, text):
            portfolios.append(name)
            text = re.sub(pattern, " @p ", text)  # placeholders must not themselves look like an entity word
    for name in sorted(vocabulary.sectors, key=len, reverse=True):
        pattern = rf"\b{re.escape(name.lower())}\b(?:\s+sector\b)?"
        if re.search(pattern, text):
            sectors.append(name)
            text = re.sub(pattern, " @s ", text)
    return text, portfolios, sectors


def _group_entity(text: str) -> str | None:
    match = re.search(rf"\b(?:for each|in each|at each|within each|across each|for every|for all|per|of each|by)\s+(?:the\s+)?(?:\w+\s+)?({'|'.join(DIMENSIONS)})(?:s|es)?\b", text)
    return match.group(1) if match else None


def _top_n(text: str) -> int | None:
    match = _TOP_N.search(text) or _N_NEAR_SUPERLATIVE.search(text)
    if not match:
        return None
    token = next(g for g in match.groups() if g)
    return int(token) if token.isdigit() else NUMBER_WORDS[token]


def _rank_metric(text: str, columns: set[str]) -> tuple[str, ...]:
    for pattern in (_METRIC_AFTER_BY, _METRIC_AFTER_SUPERLATIVE):
        match = pattern.search(text)
        if match:
            found = _metric_columns(match.group(1).strip(), columns)
            if found:
                return found
    return ()


def _metric_columns(phrase: str, columns: set[str]) -> tuple[str, ...]:
    words = phrase.split()
    for start in range(len(words)):  # "stated aum" also tries "aum"
        candidate = " ".join(words[start:])
        key = candidate.replace(" ", "_")
        if key in columns:
            return (key,)
        if candidate in _ALIASES:
            return _ALIASES[candidate]
    return ()


def _named_columns(text: str, columns: set[str]) -> frozenset[str]:
    named = {c for c in columns if re.search(rf"\b{c.replace('_', ' ')}s?\b", text)}
    if re.search(r"\b(?:the|a|its|their|which|what)\s+dates?\b", text):  # "the date" names whichever date column the row has
        named |= {c for c in columns if c.endswith("_date")}
    for alias, targets in _ALIASES.items():
        if alias != "value" and re.search(rf"\b{alias}\b", text):
            named |= set(targets)
    return frozenset(named)


# --- the SQL ----------------------------------------------------------------------------------------------------

@dataclass
class _Traced:
    node: exp.Expression
    conditions: set[str] = field(default_factory=set)
    drops_unclassified: bool = False
    zero_filled: bool = False
    grouped: bool = False


def _has_own_aggregate(expression: exp.Expression) -> bool:
    """An aggregate computed in this SELECT itself, not inside a nested query."""
    return any(isinstance(node, exp.AggFunc) for node in expression.walk(prune=lambda n: isinstance(n, exp.Subquery)))


class _Scope:
    """One SELECT: what its FROM clause provides, and how a name resolves inside it."""

    def __init__(self, select: exp.Select, schema: dict[str, dict[str, str]], ctes: dict[str, exp.Expression], depth: int = 0):
        if depth > MAX_DEPTH:
            raise ValueError("query nesting too deep to analyse")
        self.select, self.schema, self.depth = select, schema, depth
        self.columns = {t.lower(): {c.lower() for c in cols} for t, cols in schema.items()}
        self.ctes = dict(ctes)
        clause = select.args.get("with_")
        if clause is not None:
            for cte in clause.expressions:
                self.ctes[cte.alias_or_name.lower()] = cte.this
        self.sources: dict[str, str | _Scope | None] = {}
        self.left_joined: set[str] = set()
        self.inner_sector_join = False
        from_clause = select.args.get("from_")
        if from_clause is not None:
            self._add(from_clause.this)
        for join in select.args.get("joins") or []:
            key, base = self._add(join.this)
            if (join.side or "").upper() == "LEFT":
                self.left_joined.add(key)
            elif base == "sectors":
                self.inner_sector_join = True

    def _add(self, item: exp.Expression) -> tuple[str, str | None]:
        key = item.alias_or_name.lower()
        if isinstance(item, exp.Table):
            name = item.name.lower()
            if name in self.ctes:
                inner = self.ctes[name]
                self.sources[key] = _Scope(inner, self.schema, self.ctes, self.depth + 1) if isinstance(inner, exp.Select) else None
                return key, None
            self.sources[key] = name
            return key, name
        if isinstance(item, exp.Subquery) and isinstance(item.this, exp.Select):
            self.sources[key] = _Scope(item.this, self.schema, self.ctes, self.depth + 1)
            return key, None
        self.sources[key] = None
        return key, None

    def outputs(self) -> dict[str, exp.Expression]:
        found: dict[str, exp.Expression] = {}
        for expression in self.select.expressions:
            if expression.is_star:
                for alias, source in self.sources.items():
                    names = self.columns.get(source, set()) if isinstance(source, str) else source.outputs() if source else ()
                    for name in names:
                        found.setdefault(name, exp.column(name, table=alias))
            else:
                found[expression.alias_or_name.lower()] = expression.unalias()
        return found

    def _provides(self, source: str | _Scope | None, name: str) -> bool:
        if isinstance(source, str):
            return name in self.columns.get(source, set())
        return source is not None and name in source.outputs()

    def _source_of(self, column: exp.Column) -> tuple[str, str | _Scope] | None:
        qualifier, name = column.table.lower(), column.name.lower()
        if qualifier:
            source = self.sources.get(qualifier)
            return (qualifier, source) if source is not None else None
        for alias, source in self.sources.items():
            if self._provides(source, name):
                return alias, source
        return None

    def trace(self, expression: exp.Expression, *, aliases: bool = False, with_conditions: bool = True) -> _Traced:
        """A copy of the expression with every column written as table.column and every derived column inlined.

        ``aliases`` lets a bare name refer to this SELECT's own output columns, as ORDER BY may in SQLite.
        """
        traced = _Traced(node=expression)
        own_outputs = {e.alias.lower(): e.unalias() for e in self.select.expressions if isinstance(e, exp.Alias)} if aliases else {}
        used_base = [False]

        def visit(node: exp.Expression) -> exp.Expression:
            if isinstance(node, exp.Subquery) and isinstance(node.this, exp.Select):
                inner = _Scope(node.this, self.schema, self.ctes, self.depth + 1)
                outputs = list(inner.outputs().values())
                if len(outputs) == 1:
                    return exp.Paren(this=self._merge(traced, inner.trace(outputs[0])))
                return node
            if not isinstance(node, exp.Column) or node.is_star:
                return node
            if aliases and not node.table and node.name.lower() in own_outputs:
                return exp.Paren(this=self._merge(traced, self.trace(own_outputs[node.name.lower()], with_conditions=with_conditions)))
            located = self._source_of(node)
            if located is None:
                return node
            alias, source = located
            if isinstance(source, str):
                used_base[0] = True
                if alias in self.left_joined:
                    traced.zero_filled = True
                return exp.column(node.name.lower(), table=source)
            inner = source.trace(source.outputs()[node.name.lower()], with_conditions=with_conditions)
            if alias in self.left_joined:
                traced.zero_filled = True
            return exp.Paren(this=self._merge(traced, inner))

        traced.node = expression.copy().transform(visit)
        own_aggregate = _has_own_aggregate(expression)
        if (used_base[0] or own_aggregate) and with_conditions:  # COUNT(*) reads this scope's rows without naming a column
            traced.conditions |= self.conditions()
            traced.drops_unclassified = traced.drops_unclassified or self.inner_sector_join
        if own_aggregate and self.select.args.get("group") is not None:
            traced.grouped = True
        return traced

    @staticmethod
    def _merge(traced: _Traced, inner: _Traced) -> exp.Expression:
        traced.conditions |= inner.conditions
        traced.drops_unclassified = traced.drops_unclassified or inner.drops_unclassified
        traced.zero_filled = traced.zero_filled or inner.zero_filled
        traced.grouped = traced.grouped or inner.grouped
        return inner.node

    def conditions(self) -> set[str]:
        found: set[str] = set()
        where, having = self.select.args.get("where"), self.select.args.get("having")
        for clause in (where, having):
            if clause is not None:
                found |= {self._canonical(p) for p in _conjuncts(clause.this)}
        for join in self.select.args.get("joins") or []:
            on = join.args.get("on")
            if on is not None:
                found |= {self._canonical(p) for p in _conjuncts(on) if p.find(exp.Literal) is not None}
        return found

    def _canonical(self, predicate: exp.Expression) -> str:
        return self.trace(predicate, with_conditions=False, aliases=True).node.sql(dialect="sqlite")

    def definition(self, column: exp.Column) -> tuple[_Scope, exp.Expression] | None:
        """The (scope, expression) that defines a derived column; None for a base-table column."""
        located = self._source_of(column)
        if located is None or isinstance(located[1], str):
            return None
        return located[1], located[1].outputs()[column.name.lower()]


def analyse(tree: exp.Select, schema: dict[str, dict[str, str]]) -> tuple[QueryShape, _Scope]:
    scope = _Scope(tree, schema, {})
    outputs = tuple(_output(e, scope) for e in tree.expressions)
    order_clause, limit_clause, offset_clause = (tree.args.get(k) for k in ("order", "limit", "offset"))
    order: list[OrderKey] = []
    for ordered in (order_clause.expressions if order_clause is not None else []):
        traced = scope.trace(ordered.this, aliases=True, with_conditions=False)
        order.append(OrderKey(sql=traced.node.sql(dialect="sqlite"), base_columns=_bases(traced.node),
                              descending=bool(ordered.args.get("desc"))))
    group_clause = tree.args.get("group")
    group_columns = frozenset(_short(b) for key in (group_clause.expressions if group_clause is not None else [])
                              for b in _bases(scope.trace(key, aliases=True, with_conditions=False).node))
    nested = order_clause is None and any(s.args.get("order") is not None for s in tree.find_all(exp.Select) if s is not tree)
    return QueryShape(outputs=outputs, order_by=tuple(order), limit=_integer(limit_clause), offset=_integer(offset_clause),
                      distinct=bool(tree.args.get("distinct")), grouped=tree.args.get("group") is not None, group_columns=group_columns,
                      nested_order=nested), scope


def _output(expression: exp.Expression, scope: _Scope) -> OutputColumn:
    name = (expression.alias_or_name or expression.sql(dialect="sqlite")).lower()
    if expression.is_star:
        return OutputColumn(name="*", kind="star", base_columns=frozenset(), sql=expression.sql(dialect="sqlite"))
    traced = scope.trace(expression.unalias())
    top = _strip(traced.node)
    if not list(traced.node.find_all(exp.Column)) and traced.node.find(exp.AggFunc) is None:
        kind = "literal"
    elif isinstance(top, exp.AggFunc):
        kind = "aggregate"
    elif isinstance(top, exp.Column):
        kind = "column"
    else:
        kind = "calculated"
    return OutputColumn(name=name, kind=kind, base_columns=_bases(traced.node), sql=traced.node.sql(dialect="sqlite"),
                        measure=_measure(traced, scope.schema))


def _bases(node: exp.Expression) -> frozenset[str]:
    return frozenset(f"{c.table}.{c.name}" for c in node.find_all(exp.Column) if c.table and not c.is_star)


def _integer(clause: exp.Expression | None) -> int | None:
    if clause is None:
        return None
    value = clause.expression
    return int(value.this) if isinstance(value, exp.Literal) and value.is_int else None


def _conjuncts(node: exp.Expression) -> Iterator[exp.Expression]:
    if isinstance(node, exp.And):
        yield from _conjuncts(node.left)
        yield from _conjuncts(node.right)
    elif isinstance(node, exp.Paren):
        yield from _conjuncts(node.this)
    else:
        yield node


def _strip(node: exp.Expression) -> exp.Expression:
    """Remove wrappers that do not change what a value is: brackets, ROUND, CAST, COALESCE and NULLIF."""
    while True:
        if isinstance(node, (exp.Paren, exp.Round, exp.Cast, exp.Coalesce, exp.Nullif)):
            node = node.this
        elif isinstance(node, exp.Alias):
            node = node.this
        else:
            return node


def _is_hundred(node: exp.Expression) -> bool:
    node = _strip(node)
    return isinstance(node, exp.Literal) and not node.is_string and float(node.this) == 100.0


def _is_literal_number(node: exp.Expression, value: float | None = None) -> bool:
    node = _strip(node)
    return isinstance(node, exp.Literal) and not node.is_string and (value is None or float(node.this) == value)


def _without_scale(node: exp.Expression, any_factor: bool = False) -> exp.Expression:
    """The value without a factor of 100 or 1.0 (or, for one side of a ratio, any fixed factor)."""
    def factor(side: exp.Expression) -> bool:
        return _is_literal_number(side) if any_factor else _is_hundred(side) or _is_literal_number(side, 1.0)

    node = _strip(node)
    while isinstance(node, exp.Mul):
        if factor(node.right):
            node = _strip(node.left)
        elif factor(node.left):
            node = _strip(node.right)
        else:
            break
    return node


def _measure(traced: _Traced, schema: dict[str, dict[str, str]], *, ratio_side: bool = False) -> Measure:
    conditions = set(traced.conditions)
    zero_filled = traced.zero_filled or any(_is_literal_number(c.expressions[-1], 0.0)
                                            for c in traced.node.find_all(exp.Coalesce) if c.expressions)
    conditional = False
    root = _without_scale(traced.node, any_factor=ratio_side)
    for case in list(root.find_all(exp.Case)):
        ifs = case.args.get("ifs") or []
        if len(ifs) == 1:
            conditions.add(ifs[0].this.sql(dialect="sqlite"))
            conditional = True
            default = case.args.get("default")
            zero_filled = zero_filled or (default is not None and _is_literal_number(default, 0.0))
            if case is root:
                root = ifs[0].args["true"].copy()
            else:
                case.replace(ifs[0].args["true"].copy())
    is_integer = (isinstance(root, exp.Count) or _integer_only(root, schema)) and root.find(exp.Cast) is None
    return Measure(value=root.sql(dialect="sqlite"), conditions=frozenset(conditions), columns=_bases(root),
                   drops_unclassified=traced.drops_unclassified, conditional=conditional, zero_filled=zero_filled,
                   is_integer=is_integer, grouped=traced.grouped)


def _integer_only(node: exp.Expression, schema: dict[str, dict[str, str]]) -> bool:
    """True when every value in the expression is an integer: integer columns and whole-number literals only."""
    types = {t.lower(): {c.lower(): d.upper() for c, d in cols.items()} for t, cols in schema.items()}
    columns, literals = list(node.find_all(exp.Column)), [l for l in node.find_all(exp.Literal) if not l.is_string]
    return bool(columns or literals) and all("INT" in types.get(c.table, {}).get(c.name, "") for c in columns) \
        and all(l.is_int for l in literals)


def _find_ratio(expression: exp.Expression, scope: _Scope) -> tuple[exp.Div | None, _Scope, bool]:
    """The division behind a percentage expression, following derived columns until one appears; and whether x100 applies."""
    node, current, scaled = expression, scope, False
    for _ in range(MAX_DEPTH):
        scaled = scaled or any(_is_hundred(m.left) or _is_hundred(m.right) for m in node.find_all(exp.Mul))
        division = node.find(exp.Div)
        if division is not None:
            return division, current, scaled
        inner = _strip(node)
        if isinstance(inner, exp.Mul):
            inner = _without_scale(inner)
        if not isinstance(inner, exp.Column):
            return None, current, scaled
        found = current.definition(inner)
        if found is None:
            return None, current, scaled
        current, node = found
    return None, current, scaled


# --- comparing the two ------------------------------------------------------------------------------------------

def assess(question: str, sql: str, schema: dict[str, dict[str, str]], vocabulary: Vocabulary = Vocabulary(),
           version: str = POLICY_VERSION) -> PolicyAssessment:
    """Compare what the question asks for with what the query returns. Never raises: a bug here must not block an answer."""
    intent = read_intent(question, schema, vocabulary)
    try:
        tree = sqlglot.parse_one(sql, read="sqlite")
        if not isinstance(tree, exp.Select):
            return PolicyAssessment(intent, None, (_warn("not_analysed", "A set operation is not shape-checked."),), version)
        shape, scope = analyse(tree, schema)
        issues: list[PolicyIssue] = [*_projection(intent, shape, version), *_ranking(intent, shape), *_aggregation(intent, shape)]
        issues += _latest_date(intent, tree)
        if intent.operation != "share":
            issues += _ratio_components(intent, shape, tree, scope)
        if intent.operation == "share":
            share, share_issues = _share(intent, shape, tree, scope)
            issues += share_issues
            shape = replace(shape, share=share)
    except Exception as exc:  # noqa: BLE001 - a defect in the policy is reported, never raised
        return PolicyAssessment(intent, None, (_warn("not_analysed", f"The query could not be shape-checked ({type(exc).__name__})."),), version)
    return PolicyAssessment(intent, shape, tuple(issues), version)


def _error(code: str, detail: str, repair: str = "") -> PolicyIssue:
    return PolicyIssue(code, "error", detail, repair)


def _warn(code: str, detail: str) -> PolicyIssue:
    return PolicyIssue(code, "warning", detail)


def _is_star(shape: QueryShape) -> bool:
    return any(o.kind == "star" for o in shape.outputs)


def _direct(shape: QueryShape) -> set[str]:
    return {b for o in shape.outputs if o.kind == "column" for b in o.base_columns}


def _short(name: str) -> str:
    return name.split(".")[-1]


def _role(output: OutputColumn, intent: QuestionIntent, shape: QueryShape) -> str:
    bare = {_short(b) for b in output.base_columns}
    if shape.share is not None:
        index = shape.outputs.index(output)
        if index == shape.share.percentage:
            return "calculated: percentage"
        if index == shape.share.numerator:
            return "calculated: part"
        if index == shape.share.denominator:
            return "calculated: whole"
    if output.kind in ("aggregate", "calculated"):
        return "ranking metric" if intent.rank_metric and set(intent.rank_metric) <= bare else "calculated"
    if bare & set(intent.rank_metric):
        return "ranking metric"
    if bare & {"holding_id", "transaction_id", "symbol", "company_name", "portfolio_name", "sector_name", "quantity"}:
        return "identity" if not bare & intent.named_columns else "requested attribute"
    if bare & intent.named_columns:
        return "requested attribute"
    if any(b.endswith("_id") for b in bare):
        return "join key"
    return "unrelated"


def _projection(intent: QuestionIntent, shape: QueryShape, version: str = POLICY_VERSION) -> list[PolicyIssue]:
    issues: list[PolicyIssue] = []
    if _is_star(shape):
        return [_warn("star_projection", "SELECT * returns every column, so the output columns were not checked.")]
    direct = _direct(shape)
    seen: dict[tuple, str] = {}
    for output in shape.outputs:
        identity = output.measure.key if output.measure is not None else (output.sql, frozenset())  # same value, same filters
        if identity in seen and output.kind != "literal":
            issues.append(_error("duplicate_output_column", f"'{output.name}' repeats '{seen[identity]}'.",
                                 f"Return '{seen[identity]}' once and remove '{output.name}'."))
        seen.setdefault(identity, output.name)
    required: tuple[str, ...] = ()
    allowed: set[str] | None = None
    entity, named = intent.primary_entity, intent.named_columns
    current_rules = version != "2.1"
    which_only = current_rules and intent.which_only
    if which_only:  # the measure was only named to say how to choose; its last word ("price") is not a request either
        named = named - set(intent.rank_metric) - {m.split("_")[-1] for m in intent.rank_metric}
    bare_named = set(named) | (set() if which_only else set(intent.rank_metric))
    record_listing = intent.operation in ("list", "rank") and intent.grain == "per_record"
    if record_listing and entity == "holding":
        required = POSITION_COLUMNS
        allowed = {_short(c) for c in POSITION_COLUMNS} | bare_named
        if intent.scoped_portfolio is None:
            allowed.add("portfolio_name")
        if shape.grouped:
            issues.append(_error("grain_mismatch", "The question lists individual holdings but the query groups them.",
                                 "Return one row per holding, without GROUP BY."))
    elif record_listing and entity == "transaction" and not named:
        required = (RECORD_KEYS["transaction"],)  # with named attributes, those are what the question asked to see
    elif record_listing and entity == "security":
        if named:
            allowed = {"symbol"} | bare_named
        else:
            required = ("securities.symbol", "securities.company_name")
            allowed = {"symbol", "company_name"} | bare_named
    elif record_listing and entity == "portfolio":
        allowed = {"portfolio_name"} | bare_named
    elif current_rules and record_listing and entity == "sector":
        allowed = {"sector_name"} | bare_named
    elif current_rules and intent.single_entity_lookup:
        allowed = set(named)
    for column in required:
        if column not in direct:
            code = "missing_record_identity" if column in RECORD_KEYS.values() else "missing_identity_column"
            issues.append(_error(code, f"The result lacks {_short(column)}, which identifies each {entity}.",
                                 f"Add {column} to the outermost SELECT."))
    wanted = {_short(c) for c in required} | named | set(intent.rank_metric)
    for output in shape.outputs:
        if output.name in wanted and output.name not in {_short(b) for b in output.base_columns} and output.kind != "literal":
            issues.append(_error("wrong_source_expression", f"'{output.name}' is not computed from the column of that name: {output.sql}.",
                                 f"Make '{output.name}' come from the {output.name} column."))
    bare_outputs = [o for o in shape.outputs if o.kind == "column"]
    for output in bare_outputs:
        bare = {_short(b) for b in output.base_columns}
        if any(b.endswith("_id") and b not in {_short(c) for c in required} and b not in {_short(k) for k in RECORD_KEYS.values()}
               for b in bare) and not bare & named:
            issues.append(_error("join_key_in_output", f"'{output.name}' is a join key the question did not ask for.",
                                 f"Remove {output.name}; join keys are not returned."))
        elif allowed is not None and not bare & allowed:
            issues.append(_error("over_projection", f"'{output.name}' was not asked for and does not identify the row.",
                                 f"Remove {output.name} from the outermost SELECT."))
    if allowed is not None and entity in ("holding",):
        allowed_calculated = allowed
        for output in shape.outputs:
            if output.kind in ("calculated", "aggregate") and not {_short(b) for b in output.base_columns} <= allowed_calculated:
                issues.append(_error("over_projection", f"'{output.name}' was not asked for.",
                                     f"Remove {output.name} from the outermost SELECT."))
    if which_only:
        for output in shape.outputs:
            if output.kind in ("calculated", "aggregate"):
                issues.append(_error("over_projection", f"'{output.name}' only decides the order; the question asks which one.",
                                     f"Keep {output.name} in ORDER BY and remove it from the outermost SELECT."))
    if intent.operation == "rank" and entity in ("holding", "transaction") and intent.rank_metric:
        returned = {_short(b) for o in shape.outputs for b in o.base_columns}
        if not set(intent.rank_metric) <= returned:
            issues.append(_error("missing_ranking_metric", f"The ranked records do not show {', '.join(intent.rank_metric)}.",
                                 f"Return {', '.join(intent.rank_metric)} in the outermost SELECT."))
    return issues


def _ranking(intent: QuestionIntent, shape: QueryShape) -> list[PolicyIssue]:
    if intent.top_n is None and not (intent.operation == "rank" and shape.order_by):
        if intent.operation == "rank" and shape.nested_order:
            return [_warn("order_only_in_subquery", "Only a nested query is sorted; the final result is not.")]
        return []
    issues: list[PolicyIssue] = []
    if not shape.order_by:
        code = "order_only_in_subquery" if shape.nested_order else "missing_order_by"
        issues.append(_error(code, "The final result is not sorted by the ranking metric.",
                             "Put ORDER BY on the outermost SELECT, using the metric the question ranks by."))
    else:
        first = shape.order_by[0]
        if intent.rank_metric and not set(intent.rank_metric) <= {_short(b) for b in first.base_columns}:
            issues.append(_error("wrong_order_metric", f"The result is sorted by {first.sql}, not by {', '.join(intent.rank_metric)}.",
                                 f"ORDER BY {', '.join(intent.rank_metric)} on the outermost SELECT."))
        if intent.direction is not None and first.descending != (intent.direction == "desc"):
            wanted = "DESC" if intent.direction == "desc" else "ASC"
            issues.append(_error("wrong_order_direction", f"The sort direction is not the one the question asks for ({wanted}).",
                                 f"Sort {wanted}."))
    if intent.top_n is not None:
        if shape.limit is None:
            issues.append(_error("missing_limit", f"The question asks for {intent.top_n} results but the query has no LIMIT.",
                                 f"Add LIMIT {intent.top_n} to the outermost SELECT."))
        elif shape.limit != intent.top_n or shape.offset:
            issues.append(_error("wrong_limit", f"The query limits to {shape.limit} (offset {shape.offset or 0}), not {intent.top_n}.",
                                 f"Use LIMIT {intent.top_n} with no OFFSET."))
    return issues


def _groups_by_named_column(intent: QuestionIntent, shape: QueryShape) -> bool:
    """The wording rules can miss a grouping; a GROUP BY on a column the question itself names cannot be an accident."""
    return bool(shape.group_columns & intent.named_columns)


def _aggregation(intent: QuestionIntent, shape: QueryShape) -> list[PolicyIssue]:
    if intent.operation != "aggregate" or intent.grain != "scalar" or _groups_by_named_column(intent, shape):
        return []
    has_figure = any(o.kind in ("aggregate", "calculated") for o in shape.outputs)
    extras = [o.name for o in shape.outputs if o.kind == "column"]
    if has_figure and extras:
        return [_error("unrelated_record_columns", f"A single total should not also return {', '.join(extras)}.",
                       f"Return only the figure; remove {', '.join(extras)}.")]
    return []


def _share(intent: QuestionIntent, shape: QueryShape, tree: exp.Select, scope: _Scope) -> tuple[ShareRoles | None, list[PolicyIssue]]:
    issues: list[PolicyIssue] = []
    labelled = [i for i, o in enumerate(shape.outputs) if _PERCENT_ALIAS.search(o.name)]
    divided = [i for i, e in enumerate(tree.expressions) if _find_ratio(e.unalias(), scope)[0] is not None]
    candidates = labelled or divided
    if not candidates:
        return None, [_error("missing_percentage_column", "The question asks for a percentage but no output column is one.",
                             "Return the percentage as its own column: part * 100.0 / whole.")]
    index = next((i for i in candidates if i in divided), candidates[0])
    expression = tree.expressions[index].unalias()
    output = shape.outputs[index]
    if output.kind == "literal":  # COUNT(*) has no base column and is still a calculation
        return None, [_error("percentage_hardcoded", f"'{output.name}' is a fixed value, not a calculation: {output.sql}.",
                             "Calculate the percentage as part * 100.0 / whole from the data.")]
    division, ratio_scope, scaled = _find_ratio(expression, scope)
    if division is None:
        return None, [_error("percentage_without_division", f"'{output.name}' has no division by a total: {output.sql}.",
                             "Calculate the percentage as part * 100.0 / whole.")]
    if _is_literal_number(division.right):
        code = "denominator_literal_zero" if _is_literal_number(division.right, 0.0) else "percentage_without_division"
        return None, [_error(code, f"'{output.name}' divides by a fixed number: {output.sql}.",
                             "Divide by the total computed from the data.")]
    numerator = _measure(ratio_scope.trace(division.left), scope.schema, ratio_side=True)
    denominator = _measure(ratio_scope.trace(division.right), scope.schema, ratio_side=True)
    if not scaled and not _is_literal_number(_strip(division.left)):
        issues.append(_error("percentage_missing_scale", f"'{output.name}' is a fraction, not a 0 to 100 percentage.",
                             "Multiply by 100.0."))
    if numerator.key == denominator.key:
        issues.append(_error("denominator_is_numerator", "The percentage divides a value by itself, so it is always 100.",
                             "Divide by the total computed without the part's filter."))
    subset_present = _has_subset(numerator, intent)
    subset_in_whole = _has_subset(denominator, intent)
    if intent.subset_terms and subset_in_whole and not subset_present:
        issues.append(_error("ratio_inverted", "The total is restricted to the part's subset and the part is not: the ratio is upside down.",
                             "Divide the restricted value by the unrestricted total."))
    elif intent.subset_terms and not subset_present:
        issues.append(_error("numerator_missing_subset", f"The part is not restricted to {', '.join(intent.subset_terms)}.",
                             f"Restrict the part to {', '.join(intent.subset_terms)}."))
    elif subset_in_whole and numerator.key != denominator.key:
        issues.append(_error("denominator_filtered", "The total carries the part's filter, so it is not the whole.",
                             "Compute the total without the part's filter."))
    bare_whole = {_short(c) for c in denominator.columns}
    if bare_whole & {"total_aum", "current_weight"} and "total_aum" not in intent.named_columns:
        issues.append(_error("wrong_denominator_source", f"The total uses {', '.join(sorted(bare_whole & {'total_aum', 'current_weight'}))}, which is not a market value.",
                             "Use SUM(holdings.quantity * securities.current_price) over all holdings."))
    if intent.whole_is_portfolio_value:
        if denominator.drops_unclassified:
            issues.append(_error("denominator_drops_bonds", "An inner join to sectors removes every bond from the total, because bonds have no sector.",
                                 "Compute the total without an inner join to sectors, so that bonds are counted."))
        if any("asset_type" in c or "sector" in c for c in denominator.conditions) and not subset_in_whole:
            issues.append(_error("denominator_filtered", "The total is restricted to part of the portfolio.",
                                 "Compute the total over all holdings, bonds included."))
    if numerator.is_integer and denominator.is_integer and not scaled_as_real(division, expression):
        issues.append(_error("integer_division", "Both sides are integers, so SQLite truncates the division.",
                             "Multiply by 100.0 before dividing."))
    if division.right.find(exp.Nullif) is None:
        issues.append(_warn("denominator_zero_unguarded", "The division has no zero guard; the executed rows are checked for a zero total."))
    position = {o.measure.key: i for i, o in reversed(list(enumerate(shape.outputs))) if o.measure is not None and i != index}
    part_index, whole_index = position.get(numerator.key), position.get(denominator.key)
    if intent.grain == "per_group":
        if part_index is None and (denominator.grouped or intent.asks_values):
            issues.append(_error("missing_numerator_column", "The value being divided is not returned as a column.",
                                 "Return the part as its own column."))
        if denominator.grouped and whole_index is None:
            issues.append(_error("missing_denominator_column", "Each entity has its own total, and it is not returned as a column.",
                                 "Return the total as its own column."))
        if not denominator.grouped and whole_index is not None:
            issues.append(_error("constant_whole_returned", "The total is the same for every row and the question did not ask for it.",
                                 f"Remove {shape.outputs[whole_index].name} from the outermost SELECT."))
    elif not intent.asks_values:
        extras = [o.name for i, o in enumerate(shape.outputs) if i != index]
        if extras:
            issues.append(_error("unrequested_operands", f"One percentage was asked for, and the query also returns {', '.join(extras)}.",
                                 f"Return only the percentage; remove {', '.join(extras)}."))
    for i, o in enumerate(shape.outputs):
        if i != index and o.measure is not None and _TOTAL_ALIAS.search(o.name) and o.measure.key == numerator.key != denominator.key:
            issues.append(_error("misleading_alias", f"'{o.name}' is named as a total but holds the part.",
                                 f"Make '{o.name}' the total, or rename it."))
    issues += _population(intent, numerator)
    if shape.order_by == () and intent.top_n is None:
        issues.append(_warn("percentage_not_ordered", "The percentages are not sorted."))
    if expression.find(exp.Round) is None:
        issues.append(_warn("percentage_not_rounded", "The percentage is not rounded to two decimals."))
    roles = ShareRoles(percentage=index, numerator=part_index, denominator=whole_index,
                       numerator_measure=numerator, denominator_measure=denominator)
    return roles, issues


def _latest_date(intent: QuestionIntent, tree: exp.Select) -> list[PolicyIssue]:
    """A latest value for an entity must not be read at the table-wide latest date."""
    if intent.latest_scope != "entity":
        return []
    for select in tree.find_all(exp.Select):
        if select is tree or len(select.expressions) != 1 or select.args.get("where") is not None or select.args.get("group") is not None:
            continue
        value = select.expressions[0].unalias()
        if isinstance(value, exp.Max) and isinstance(value.this, exp.Column) and value.this.name.lower().endswith("_date"):
            return [_error("global_latest_date", "The latest date is taken across the whole table, not for the entity asked about.",
                           "Use that entity's own latest date: filter the subquery to the entity, correlate it, or use a grouped CTE or a window function.")]
    return []


def _ratio_components(intent: QuestionIntent, shape: QueryShape, tree: exp.Select, scope: _Scope) -> list[PolicyIssue]:
    """A ratio's operands are judged by what the question asks to see."""
    issues: list[PolicyIssue] = []
    returned = {o.measure.key for o in shape.outputs if o.measure is not None}
    for expression, output in zip(tree.expressions, shape.outputs):
        if expression.is_star:
            continue
        division, ratio_scope, _ = _find_ratio(expression.unalias(), scope)
        if division is None or _is_literal_number(division.right):
            continue
        sides = [ratio_scope.trace(division.left), ratio_scope.trace(division.right)]
        if any(side.node.find(exp.AggFunc) is None for side in sides):
            continue
        present = [_measure(side, scope.schema, ratio_side=True).key in returned for side in sides]
        if intent.asks_operands and not all(present):
            issues.append(_error("missing_ratio_components", f"'{output.name}' is returned without the two values it is calculated from.",
                                 f"Return the numerator and the denominator of {output.name} as their own columns."))
        elif not intent.asks_operands and all(present):
            issues.append(_error("unrequested_operands", f"'{output.name}' was asked for without the values behind it.",
                                 f"Return {output.name} without its numerator and denominator."))
    return issues


def scaled_as_real(division: exp.Div, expression: exp.Expression) -> bool:
    """True when a real number or a CAST takes part before or in the division, so SQLite does not truncate."""
    def real(node: exp.Expression) -> bool:
        return node.find(exp.Cast) is not None or any(not l.is_string and not l.is_int for l in node.find_all(exp.Literal))
    return real(division.left) or real(division.right)


def _has_subset(measure: Measure, intent: QuestionIntent) -> bool:
    text = " ".join(measure.conditions).lower()
    return any(f"'{term.lower()}'" in text for term in intent.subset_terms)


def _population(intent: QuestionIntent, numerator: Measure) -> list[PolicyIssue]:
    if intent.population == "not_applicable":
        return []
    includes_all = numerator.conditional or numerator.zero_filled
    if intent.population == "qualifying_only" and includes_all:
        return [_error("population_includes_non_qualifying",
                       "Entities with nothing in the subset are listed, as a zero or a NULL.",
                       "Return only entities that have at least one qualifying row: compute the part with a WHERE filter and "
                       "join it to the total with an inner join.")]
    if intent.population == "all_entities" and not includes_all:
        return [_error("population_excludes_requested", "The question asks for all entities, but those with nothing in the subset are left out.",
                       "Keep every entity and show 0 for those with nothing in the subset.")]
    if intent.population == "qualifying_only" and not intent.population_stated:
        return [_warn("population_assumed",
                      f"Only {intent.group_entity or 'entitie'}s with a holding in {', '.join(intent.subset_terms)} are listed; the question did not ask for the others.")]
    return []


# --- the executed result ----------------------------------------------------------------------------------------

def assess_result(assessment: PolicyAssessment, columns: Sequence[str], rows: Sequence[Sequence[Any]], *,
                  portfolio_values: dict[str, float] | None = None) -> PolicyAssessment:
    """Add the checks that only executed rows can settle. The query has already been validated and run read-only."""
    intent, shape = assessment.intent, assessment.shape
    if shape is None:
        return assessment
    found: list[PolicyIssue] = []
    if intent.grain == "scalar" and intent.operation == "aggregate" and len(rows) > 1 and not _groups_by_named_column(intent, shape):
        found.append(_error("scalar_expected", f"The question asks for one figure but the query returned {len(rows)} rows.",
                            "Aggregate to a single row."))
    if intent.operation in ("list", "rank") and intent.primary_entity in ("portfolio", "security", "sector") and rows \
            and len({tuple(map(str, r)) for r in rows}) < len(rows):
        found.append(_error("duplicate_rows", "The same entity appears more than once.", "Use DISTINCT, or fix the join that repeats rows."))
    if intent.top_n is not None and len(rows) > intent.top_n:
        found.append(_error("wrong_limit", f"{len(rows)} rows were returned for a top {intent.top_n}.", f"Use LIMIT {intent.top_n}."))
    found += _sorted(intent, shape, rows)
    if shape.share is not None and len(columns) == len(shape.outputs):
        found += _share_rows(intent, shape, rows, portfolio_values)
    known = {i.code for i in assessment.issues}
    return replace(assessment, issues=(*assessment.issues, *(i for i in found if i.code not in known)))


def _sorted(intent: QuestionIntent, shape: QueryShape, rows: Sequence[Sequence[Any]]) -> list[PolicyIssue]:
    if not intent.rank_metric or intent.direction is None or len(rows) < 2:
        return []
    for index, output in enumerate(shape.outputs):
        if set(intent.rank_metric) <= {_short(b) for b in output.base_columns} and output.kind != "literal":
            values = [r[index] for r in rows if isinstance(r[index], (int, float))]
            ordered = sorted(values, reverse=intent.direction == "desc")
            if values != ordered:
                return [_error("ranking_not_sorted", f"The rows are not sorted by {output.name}.", f"Sort by {output.name}.")]
            return []
    return []


def _share_rows(intent: QuestionIntent, shape: QueryShape, rows: Sequence[Sequence[Any]], portfolio_values: dict[str, float] | None) -> list[PolicyIssue]:
    roles = shape.share
    issues: list[PolicyIssue] = []
    name_index = next((i for i, o in enumerate(shape.outputs) if "portfolios.portfolio_name" in o.base_columns), None)
    for row in rows:
        part = row[roles.numerator] if roles.numerator is not None else None
        whole = row[roles.denominator] if roles.denominator is not None else None
        percentage = row[roles.percentage]
        if roles.denominator is not None and (whole is None or whole == 0):
            issues.append(_error("denominator_zero", f"A row has a total of {whole}.", "Exclude rows with no total, or guard the division."))
            break
        if intent.population == "qualifying_only" and roles.numerator is not None and not part:
            issues.append(_error("population_includes_non_qualifying", "A listed entity has nothing in the subset.",
                                 "Return only entities with at least one qualifying row."))
            break
        if roles.numerator is None or roles.denominator is None or not _numbers(part, whole):
            continue
        if part > whole * (1 + 1e-9):
            issues.append(_error("numerator_exceeds_denominator", f"A part ({part}) exceeds its total ({whole}).", "Fix the part and the total."))
            break
        if _numbers(percentage) and abs(percentage - 100.0 * part / whole) > PERCENT_TOLERANCE:
            issues.append(_error("percentage_mismatch", f"The stated {percentage} is not {100.0 * part / whole:.2f}, part over total.",
                                 "Recalculate the percentage as part * 100.0 / whole."))
            break
    if intent.whole_is_portfolio_value and roles.denominator is not None and name_index is not None and portfolio_values:
        for row in rows:
            expected = portfolio_values.get(row[name_index])
            whole = row[roles.denominator]
            if expected is not None and _numbers(whole) and not math.isclose(whole, expected, rel_tol=1e-6, abs_tol=VALUE_TOLERANCE):
                issues.append(_error("denominator_mismatch", f"{row[name_index]}: the total {whole} differs from the independently computed {expected:.2f}.",
                                     "Compute the total as SUM(holdings.quantity * securities.current_price) over all the portfolio's holdings."))
                break
    return issues


def _numbers(*values: Any) -> bool:
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)
