"""Deterministic portfolio-name resolution: rules B4, B5 and SR1 in docs/BUSINESS_RULES.md.

Only portfolio names and approved aliases can select a portfolio. Holdings are used solely to
build a transparent candidate list for the user to choose from. No LLM is involved.

When the user's question is supplied, a portfolio phrase proposed by the LLM is accepted only if
the question itself names that portfolio directly (full name, alias in portfolio position, or a
distinctive phrase); otherwise the user is asked to confirm.
"""

from __future__ import annotations

import difflib
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from portfolioiq.formatting import pct

FILLER_WORDS = frozenset({"the", "a", "an", "our", "my", "for", "of"})
CONTAINER_WORDS = frozenset({"fund", "funds", "portfolio", "portfolios"})
GENERIC_WORDS = frozenset({
    "bond", "bonds", "stock", "stocks", "equity", "equities", "income", "fixed", "international",
    "global", "domestic", "index", "market", "markets", "total", "growth", "value", "dividend",
    "esg", "sustainable", "balanced", "conservative", "aggressive", "small", "mid", "large", "cap",
    "emerging", "sector",
}) | CONTAINER_WORDS
APPROVED_ALIASES = {"tech": "Tech Innovation Fund"}
SECTOR_ALIASES = {"tech": "Technology"}
# A word right after an alias that makes the alias a sector reference, as in "tech exposure".
SECTOR_CONTEXT_WORDS = frozenset({
    "exposure", "exposures", "sector", "sectors", "stock", "stocks", "holding", "holdings",
    "allocation", "allocations", "weight", "weights", "weighting", "name", "names", "company",
    "companies", "security", "securities", "share", "shares", "equity", "equities", "position",
    "positions",
})
BOND_PHRASES = frozenset({("bond",), ("bonds",), ("fixed", "income")})
BOND_CANDIDATE_MIN_WEIGHT = 0.5
MAX_PHRASE_LENGTH = 200
SUGGESTION_CUTOFF = 0.75
MAX_SUGGESTIONS = 3
RULE_QUESTION_FULL_NAME = "B5.1 (full name in the question)"

# SR1: SQL syntax that never appears in a legitimate portfolio name. Single keywords alone
# ("Select Equity Fund") and ordinary punctuation (& / - ' ( ) , .) are deliberately allowed.
_SQL_SYNTAX = [
    (re.compile(r";"), "statement separator ';'"),
    (re.compile(r"--"), "comment marker '--'"),
    (re.compile(r"/\*|\*/"), "block comment"),
    (re.compile(r"\b(drop|alter|truncate)\s+(table|index|view|trigger|database)\b", re.I), "schema statement"),
    (re.compile(r"\bdelete\s+from\b", re.I), "DELETE statement"),
    (re.compile(r"\binsert\s+(or\s+\w+\s+)?into\b", re.I), "INSERT statement"),
    (re.compile(r"\bupdate\s+\w+\s+set\b", re.I), "UPDATE statement"),
    (re.compile(r"\bcreate\s+(temp\s+|temporary\s+)?(table|index|view|trigger)\b", re.I), "CREATE statement"),
    (re.compile(r"\b(attach|detach)\s+(database\b|['\"])", re.I), "ATTACH or DETACH statement"),
    (re.compile(r"\bpragma\b", re.I), "PRAGMA"),
    (re.compile(r"\bunion\s+(all\s+)?select\b", re.I), "UNION SELECT"),
    (re.compile(r"\bselect\b.+\bfrom\b", re.I), "SELECT ... FROM"),
    (re.compile(r"['\"]\s*(or|and)\s+['\"]?\w*['\"]?\s*=", re.I), "always-true condition"),
    (re.compile(r"\b(or|and)\s+\d+\s*=\s*\d+", re.I), "always-true condition"),
]


class Candidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["portfolio", "sector"]
    name: str
    portfolio_id: int | None = None
    reason: str


class Resolution(BaseModel):
    """The resolver's answer; every field is recorded in the Financial Answer Receipt (B5.7)."""

    model_config = ConfigDict(frozen=True)

    status: Literal["resolved", "needs_clarification", "not_found", "rejected", "invalid_input"]
    rule: str
    message: str
    input_phrase: str
    normalised_phrase: str = ""
    portfolio_id: int | None = None
    portfolio_name: str | None = None
    matched_phrase: str | None = None
    candidates: list[Candidate] = []
    confirmation_required: bool = False
    grounded_in_question: bool | None = None
    sector_terms: list[str] = []


@dataclass(frozen=True)
class PortfolioName:
    portfolio_id: int
    name: str
    tokens: tuple[str, ...]

    @property
    def forms(self) -> list[tuple[str, ...]]:
        """The full name, plus the name without a trailing Fund/Portfolio if two or more words remain."""
        forms = [self.tokens]
        if self.tokens[-1] in ("fund", "portfolio") and len(self.tokens) >= 3:
            forms.append(self.tokens[:-1])
        return forms


@dataclass(frozen=True)
class Catalog:
    portfolios: tuple[PortfolioName, ...]
    bond_weights: dict[int, float]
    sectors: dict[tuple[str, ...], str]

    @classmethod
    def from_db(cls, conn: sqlite3.Connection) -> Catalog:
        portfolios = tuple(
            PortfolioName(pid, name, tuple(normalise(name)))
            for pid, name in conn.execute("SELECT portfolio_id, portfolio_name FROM portfolios ORDER BY portfolio_id")
        )
        bond_weights = dict(conn.execute(
            "SELECT h.portfolio_id, TOTAL(h.current_weight) FROM holdings AS h "
            "JOIN securities AS s ON s.security_id = h.security_id "
            "WHERE s.asset_type = 'Bond' GROUP BY h.portfolio_id"
        ))
        sectors = {tuple(normalise(n)): n for (n,) in conn.execute("SELECT sector_name FROM sectors")}
        return cls(portfolios, bond_weights, sectors)

    def by_id(self, portfolio_id: int) -> PortfolioName:
        return next(p for p in self.portfolios if p.portfolio_id == portfolio_id)

    def by_name(self, name: str) -> PortfolioName:
        return next(p for p in self.portfolios if p.name == name)


def normalise(text: str) -> list[str]:
    """Case-folded word tokens: '&' read as 'and', punctuation ignored, filler words dropped."""
    cleaned = re.sub(r"[^\w\s]", " ", text.casefold().replace("&", " and "))
    return [t for t in cleaned.split() if t not in FILLER_WORDS]


def sql_syntax_reason(text: str) -> str | None:
    """Rule SR1: the kind of SQL syntax found in a portfolio reference, or None."""
    return next((reason for pattern, reason in _SQL_SYNTAX if pattern.search(text)), None)


def resolve_portfolio(catalog: Catalog, phrase: str | None, question: str | None = None,
                      context: Sequence[str] = ()) -> Resolution:
    """Resolve a portfolio reference by rules B4, B5 and SR1.

    ``question`` is the user's original question; ``context`` holds earlier tool results (for
    example portfolio names returned by a SQL query) that may legitimately name a portfolio.
    """
    raw = (phrase or "").strip()
    if not raw:
        return Resolution(status="invalid_input", rule="input", message="No portfolio name was given.",
                          input_phrase=raw)
    if len(raw) > MAX_PHRASE_LENGTH:
        return Resolution(status="invalid_input", rule="input", input_phrase=raw[:MAX_PHRASE_LENGTH],
                          message=f"The portfolio reference is longer than {MAX_PHRASE_LENGTH} characters.")
    reason = sql_syntax_reason(raw)
    if reason:
        return Resolution(status="rejected", rule="SR1", input_phrase=raw,
                          message=f"The portfolio reference contains SQL syntax ({reason}). It was rejected "
                                  f"before any name matching, and nothing was run.")

    tokens = normalise(raw)
    question_tokens = normalise(question) if question is not None else None
    sector_terms = _sector_terms(catalog, question_tokens if question_tokens is not None else tokens,
                                 in_question=question_tokens is not None)
    result = _resolve_phrase(catalog, raw, tokens, question_tokens, sector_terms)

    context_lists = ([question_tokens] if question_tokens is not None else []) + [normalise(c) for c in context]
    if not context_lists:
        return result
    if result.status == "resolved" and result.rule != RULE_QUESTION_FULL_NAME:
        if result.portfolio_id not in _direct_mentions(catalog, context_lists):
            return _confirm_from_context(catalog, result, context_lists)
        return result.model_copy(update={"grounded_in_question": True})
    content = [t for t in tokens if t not in CONTAINER_WORDS]
    available = {t for words in context_lists for t in words}
    grounded = result.rule == RULE_QUESTION_FULL_NAME or all(t in available for t in content)
    return result.model_copy(update={"grounded_in_question": grounded})


def _resolve_phrase(catalog: Catalog, raw: str, tokens: list[str], question_tokens: list[str] | None,
                    sector_terms: list[str]) -> Resolution:
    common = {"input_phrase": raw, "normalised_phrase": " ".join(tokens), "sector_terms": sector_terms}
    content = [t for t in tokens if t not in CONTAINER_WORDS]

    full = _full_name_matches(catalog, tokens)  # rule B5.1
    if len(full) == 1:
        portfolio, form = full[0]
        return Resolution(status="resolved", rule="B5.1", message=f"Resolved to {portfolio.name} (full name).",
                          portfolio_id=portfolio.portfolio_id, portfolio_name=portfolio.name,
                          matched_phrase=" ".join(form), **common)
    if len(full) > 1:
        return Resolution(status="needs_clarification", rule="B5.1", confirmation_required=True,
                          message="More than one portfolio is named. Which one do you mean?",
                          candidates=[_candidate(p, "full name mentioned") for p, _ in full], **common)

    if len(content) == 1 and content[0] in APPROVED_ALIASES:  # rule B5.2
        return _resolve_alias(catalog, content[0], tokens, question_tokens, common)
    if not content:
        return Resolution(status="not_found", rule="B5", message=f"No portfolio name was recognised in '{raw}'.",
                          **common)

    distinctive = any(t not in GENERIC_WORDS for t in content)
    if len(content) >= 2 and distinctive:  # rule B5.3
        matches = [p for p in catalog.portfolios if _contains(p.tokens, tuple(content))]
        if len(matches) == 1:
            return Resolution(status="resolved", rule="B5.3",
                              message=f"Resolved to {matches[0].name} (distinctive phrase).",
                              portfolio_id=matches[0].portfolio_id, portfolio_name=matches[0].name,
                              matched_phrase=" ".join(content), **common)
        if len(matches) > 1:
            return Resolution(status="needs_clarification", rule="B5.3", confirmation_required=True,
                              message=f"'{raw}' matches more than one portfolio. Which one do you mean?",
                              candidates=[_candidate(p, f"name contains '{' '.join(content)}'") for p in matches],
                              **common)

    if not distinctive:  # rule B5.5: generic words never select a portfolio
        candidates = _generic_candidates(catalog, content)
        if candidates:
            return Resolution(status="needs_clarification", rule="B5.5", confirmation_required=True,
                              message=f"'{raw}' is a generic description, so it cannot select a portfolio on its "
                                      f"own. Which portfolio do you mean?", candidates=candidates, **common)
    elif len(content) == 1:  # rule B5.4: single words other than approved aliases are confirmed
        matches = [p for p in catalog.portfolios if content[0] in p.tokens]
        if matches:
            names = " or ".join(p.name for p in matches)
            return Resolution(status="needs_clarification", rule="B5.4", confirmation_required=True,
                              message=f"Did you mean {names}?",
                              candidates=[_candidate(p, f"name contains '{content[0]}'") for p in matches],
                              **common)
    return _not_found(catalog, raw, tokens, common)


def _resolve_alias(catalog: Catalog, alias: str, tokens: list[str], question_tokens: list[str] | None,
                   common: dict) -> Resolution:
    target = catalog.by_name(APPROVED_ALIASES[alias])
    sector = SECTOR_ALIASES.get(alias)
    role = _alias_role(alias, tokens, question_tokens)
    if role == "portfolio":
        return Resolution(status="resolved", rule="B5.2", matched_phrase=alias,
                          message=f"Resolved to {target.name} (approved alias '{alias}').",
                          portfolio_id=target.portfolio_id, portfolio_name=target.name, **common)
    if role == "sector":
        named = _full_name_matches(catalog, question_tokens or [])
        if len(named) == 1:
            portfolio, form = named[0]
            return Resolution(status="resolved", rule=RULE_QUESTION_FULL_NAME, matched_phrase=" ".join(form),
                              message=f"'{alias}' refers to the {sector} sector here, so the alias is not "
                                      f"applied. The portfolio is {portfolio.name}, named in full in the question.",
                              portfolio_id=portfolio.portfolio_id, portfolio_name=portfolio.name, **common)
        return Resolution(status="needs_clarification", rule="B5.2 (alias context)", confirmation_required=True,
                          message=f"'{alias}' refers to the {sector} sector here, not to {target.name}. "
                                  f"Which portfolio do you mean?",
                          candidates=[_candidate(p, "full name in the question") for p, _ in named], **common)
    return Resolution(status="needs_clarification", rule="B5.2 (alias context)", confirmation_required=True,
                      message=f"Do you mean the {target.name} or the {sector} sector?",
                      candidates=[_candidate(target, f"approved alias '{alias}'"),
                                  Candidate(kind="sector", name=sector, reason=f"'{alias}' is also the {sector} sector")],
                      **common)


def _alias_role(alias: str, tokens: list[str], question_tokens: list[str] | None) -> str:
    """'portfolio', 'sector' or 'ambiguous', from the word that follows each use of the alias."""
    in_question = question_tokens is not None
    words = question_tokens if in_question else tokens
    roles = set()
    for i, word in enumerate(words):
        if word != alias:
            continue
        following = words[i + 1] if i + 1 < len(words) else None
        if following in CONTAINER_WORDS:
            roles.add("portfolio")
        elif following in SECTOR_CONTEXT_WORDS:
            roles.add("sector")
        elif following is None and not in_question:
            roles.add("portfolio")  # the bare tool argument "tech" sits in the portfolio position
        else:
            roles.add("ambiguous")
    if "portfolio" in roles:
        return "portfolio"
    return "sector" if roles == {"sector"} else "ambiguous"


def _direct_mentions(catalog: Catalog, token_lists: list[list[str]]) -> set[int]:
    """Portfolios named directly in the given texts by rules B5.1, B5.2 or B5.3."""
    ids: set[int] = set()
    for words in token_lists:
        ids |= {p.portfolio_id for p, _ in _full_name_matches(catalog, words)}
        for alias, target in APPROVED_ALIASES.items():
            if alias in words and _alias_role(alias, words, words) == "portfolio":
                ids.add(catalog.by_name(target).portfolio_id)
        content = [t for t in words if t not in CONTAINER_WORDS]
        for size in range(2, len(content) + 1):
            for start in range(len(content) - size + 1):
                gram = tuple(content[start:start + size])
                if all(t in GENERIC_WORDS for t in gram):
                    continue
                matches = [p for p in catalog.portfolios if _contains(p.tokens, gram)]
                if len(matches) == 1:
                    ids.add(matches[0].portfolio_id)
    return ids


def _confirm_from_context(catalog: Catalog, proposed: Resolution, context_lists: list[list[str]]) -> Resolution:
    words = {t for tokens in context_lists for t in tokens if t not in CONTAINER_WORDS}
    candidates = []
    for p in catalog.portfolios:
        shared = [t for t in p.tokens if t in words]
        if shared:
            candidates.append(_candidate(p, f"name contains '{shared[0]}' from the question"))
    if proposed.portfolio_id not in {c.portfolio_id for c in candidates}:
        candidates.append(_candidate(catalog.by_id(proposed.portfolio_id), "proposed, but not named in the question"))
    return proposed.model_copy(update={
        "status": "needs_clarification",
        "rule": "B5 grounding check",
        "message": f"{proposed.portfolio_name} is not named in the question, so it is not used without "
                   f"confirmation. Which portfolio do you mean?",
        "portfolio_id": None,
        "portfolio_name": None,
        "candidates": candidates,
        "confirmation_required": True,
        "grounded_in_question": False,
    })


def _generic_candidates(catalog: Catalog, content: list[str]) -> list[Candidate]:
    phrase = tuple(content)
    named = [p for p in catalog.portfolios if _contains(p.tokens, phrase)]
    candidates = [_candidate(p, f"name contains '{' '.join(phrase)}'") for p in named]
    if not named:
        for p in catalog.portfolios:
            shared = [t for t in content if t in p.tokens]
            if shared:
                candidates.append(_candidate(p, f"name contains '{shared[0]}'"))
    if phrase in BOND_PHRASES:  # holdings only list choices for the user; they never select (B5.5)
        listed = {c.portfolio_id for c in candidates}
        bond_heavy = sorted(
            ((catalog.by_id(pid), w) for pid, w in catalog.bond_weights.items() if w >= BOND_CANDIDATE_MIN_WEIGHT),
            key=lambda pw: (-pw[1], pw[0].name),
        )
        for portfolio, weight in bond_heavy:
            if portfolio.portfolio_id not in listed:
                candidates.append(_candidate(portfolio, f"{pct(weight)}% bonds by current_weight"))
    return candidates


def _not_found(catalog: Catalog, raw: str, tokens: list[str], common: dict) -> Resolution:
    forms = {}
    for p in catalog.portfolios:
        for form in p.forms:
            forms.setdefault(" ".join(form), p)
    suggestions: list[Candidate] = []
    for match in difflib.get_close_matches(" ".join(tokens), list(forms), n=2 * MAX_SUGGESTIONS,
                                           cutoff=SUGGESTION_CUTOFF):
        portfolio = forms[match]
        if portfolio.portfolio_id not in {s.portfolio_id for s in suggestions}:
            suggestions.append(_candidate(portfolio, "similar name"))
    suggestions = suggestions[:MAX_SUGGESTIONS]
    hint = f" Did you mean {' or '.join(s.name for s in suggestions)}?" if suggestions else ""
    return Resolution(status="not_found", rule="B4", message=f"No portfolio matches '{raw}'.{hint}",
                      candidates=suggestions, confirmation_required=bool(suggestions), **common)


def _sector_terms(catalog: Catalog, words: list[str], in_question: bool) -> list[str]:
    found = {name for seq, name in catalog.sectors.items() if _contains(words, seq)}
    for alias, sector in SECTOR_ALIASES.items():
        if alias in words and _alias_role(alias, words, words if in_question else None) == "sector":
            found.add(sector)
    return sorted(found)


def _full_name_matches(catalog: Catalog, tokens: list[str]) -> list[tuple[PortfolioName, tuple[str, ...]]]:
    found = []
    for portfolio in catalog.portfolios:
        form = next((f for f in portfolio.forms if _contains(tokens, f)), None)
        if form:
            found.append((portfolio, form))
    return found


def _contains(tokens: Sequence[str], seq: tuple[str, ...]) -> bool:
    size = len(seq)
    return size > 0 and any(tuple(tokens[i:i + size]) == seq for i in range(len(tokens) - size + 1))


def _candidate(portfolio: PortfolioName, reason: str) -> Candidate:
    return Candidate(kind="portfolio", name=portfolio.name, portfolio_id=portfolio.portfolio_id, reason=reason)
