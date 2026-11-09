"""Milestone 2: portfolio resolution follows the frozen rules B4, B5 and SR1."""

import pytest

from portfolioiq.resolver import RULE_QUESTION_FULL_NAME, Catalog, resolve_portfolio

BOND_CANDIDATES = {"Total Bond Market Index Fund", "Fixed Income Plus", "Conservative Income Fund",
                   "Balanced Portfolio"}
INTERNATIONAL = {"International Equity Fund", "Total International Index Fund"}
INDEX_FUNDS = {"Total Stock Market Index Fund", "Total Bond Market Index Fund", "Total International Index Fund"}


@pytest.fixture(scope="module")
def catalog(db):
    return Catalog.from_db(db)


def names(resolution):
    if resolution.status == "resolved":
        return {resolution.portfolio_name}
    return {c.name for c in resolution.candidates}


# --- the B5 outcome table in docs/BUSINESS_RULES.md -------------------------------------------

@pytest.mark.parametrize("phrase, status, expected, rule", [
    ("International Equity Fund", "resolved", {"International Equity Fund"}, "B5.1"),
    ("international equity", "resolved", {"International Equity Fund"}, "B5.1"),
    ("sector exposure of the Growth Equity Fund", "resolved", {"Growth Equity Fund"}, "B5.1"),
    ("Growth Equity Fund and Tech Innovation Fund", "needs_clarification",
     {"Growth Equity Fund", "Tech Innovation Fund"}, "B5.1"),
    ("tech", "resolved", {"Tech Innovation Fund"}, "B5.2"),
    ("tech fund", "resolved", {"Tech Innovation Fund"}, "B5.2"),
    ("income plus", "resolved", {"Fixed Income Plus"}, "B5.3"),
    ("aristocrats", "needs_clarification", {"Dividend Aristocrats Fund"}, "B5.4"),
    ("balanced", "needs_clarification", {"Balanced Portfolio"}, "B5.5"),
    ("bond", "needs_clarification", BOND_CANDIDATES, "B5.5"),
    ("bonds", "needs_clarification", BOND_CANDIDATES, "B5.5"),
    ("bond fund", "needs_clarification", BOND_CANDIDATES, "B5.5"),
    ("fixed income", "needs_clarification", BOND_CANDIDATES, "B5.5"),
    ("international", "needs_clarification", INTERNATIONAL, "B5.5"),
    ("international portfolio", "needs_clarification", INTERNATIONAL, "B5.5"),
    ("equity fund", "needs_clarification", {"Growth Equity Fund", "International Equity Fund"}, "B5.5"),
    ("index fund", "needs_clarification", INDEX_FUNDS, "B5.5"),
    ("market", "needs_clarification", {"Total Stock Market Index Fund", "Total Bond Market Index Fund"}, "B5.5"),
    ("stock", "needs_clarification", {"Total Stock Market Index Fund"}, "B5.5"),
    ("total stock market", "needs_clarification", {"Total Stock Market Index Fund"}, "B5.5"),
    ("Tech Inovation Fund", "not_found", {"Tech Innovation Fund"}, "B4"),
    ("Bitcoin Fund", "not_found", set(), "B4"),
])
def test_b5_outcome_table(catalog, phrase, status, expected, rule):
    result = resolve_portfolio(catalog, phrase)
    assert (result.status, names(result), result.rule) == (status, expected, rule)
    if status != "resolved":
        assert result.portfolio_id is None
        assert result.confirmation_required == bool(expected)


def test_interpretation_1_requires_an_exact_canonical_name(catalog):
    assert resolve_portfolio(catalog, "international equity").portfolio_name == "International Equity Fund"
    assert resolve_portfolio(catalog, "Balanced Portfolio").status == "resolved"
    # Not exact canonical names: no direct resolution.
    assert resolve_portfolio(catalog, "international equities").status != "resolved"
    assert resolve_portfolio(catalog, "balanced").status != "resolved"  # one word left: confirmation


def test_interpretation_2_holdings_only_list_bond_candidates(catalog):
    result = resolve_portfolio(catalog, "bond")
    assert result.status == "needs_clarification" and result.portfolio_id is None
    assert [(c.name, c.reason) for c in result.candidates] == [
        ("Total Bond Market Index Fund", "name contains 'bond'"),
        ("Fixed Income Plus", "100.00% bonds by current_weight"),
        ("Conservative Income Fund", "68.00% bonds by current_weight"),
        ("Balanced Portfolio", "64.00% bonds by current_weight"),
    ]


def test_typo_gets_suggestions_never_a_silent_correction(catalog):
    result = resolve_portfolio(catalog, "Tech Inovation Fund")
    assert result.status == "not_found" and result.portfolio_id is None
    assert "Did you mean Tech Innovation Fund?" in result.message
    assert [(c.name, c.reason) for c in result.candidates] == [("Tech Innovation Fund", "similar name")]


# --- interpretation 3: the tech alias only in the portfolio-reference position ----------------

def test_tech_as_sector_does_not_select_tech_innovation_fund(catalog):
    question = "What is the tech exposure of the Balanced Portfolio?"
    result = resolve_portfolio(catalog, "tech", question=question)
    assert result.status == "resolved"
    assert result.portfolio_name == "Balanced Portfolio"
    assert result.rule == RULE_QUESTION_FULL_NAME
    assert result.sector_terms == ["Technology"]
    assert "Technology sector" in result.message


def test_portfolio_named_alongside_tech_sector_resolves_to_that_portfolio(catalog):
    question = "What is the tech exposure of the Balanced Portfolio?"
    result = resolve_portfolio(catalog, "Balanced Portfolio", question=question)
    assert (result.status, result.portfolio_name, result.grounded_in_question) == (
        "resolved", "Balanced Portfolio", True)
    assert result.sector_terms == ["Technology"]


def test_tech_in_portfolio_position_resolves_the_alias(catalog):
    result = resolve_portfolio(catalog, "tech", question="Show the sector exposures of the tech fund")
    assert (result.status, result.portfolio_name, result.rule) == ("resolved", "Tech Innovation Fund", "B5.2")
    assert result.grounded_in_question is True


def test_tech_with_ambiguous_role_asks_portfolio_or_sector(catalog):
    result = resolve_portfolio(catalog, "tech", question="Show me the sector breakdown for tech")
    assert result.status == "needs_clarification" and result.portfolio_id is None
    assert {(c.kind, c.name) for c in result.candidates} == {
        ("portfolio", "Tech Innovation Fund"), ("sector", "Technology")}


def test_tech_as_sector_without_a_portfolio_asks_which_portfolio(catalog):
    result = resolve_portfolio(catalog, "tech", question="What is our tech exposure?")
    assert result.status == "needs_clarification"
    assert "Technology sector" in result.message and result.portfolio_id is None


# --- grounding: an LLM-proposed name must be named in the user's words ------------------------

def test_name_not_in_question_needs_confirmation(catalog):
    result = resolve_portfolio(catalog, "International Equity Fund",
                               question="Sector exposure for the international portfolio")
    assert result.status == "needs_clarification"
    assert result.grounded_in_question is False and result.confirmation_required
    assert names(result) == INTERNATIONAL


def test_name_supported_by_question_resolves(catalog):
    result = resolve_portfolio(catalog, "International Equity Fund",
                               question="Please give the sector exposure breakdown for international equity")
    assert (result.status, result.portfolio_name, result.grounded_in_question) == (
        "resolved", "International Equity Fund", True)


def test_llm_cannot_upgrade_a_generic_phrase_to_a_full_name(catalog):
    result = resolve_portfolio(catalog, "Balanced Portfolio", question="Show the balanced fund's exposure")
    assert result.status == "needs_clarification"
    assert names(result) == {"Balanced Portfolio"} and result.confirmation_required


def test_earlier_tool_results_can_ground_a_name(catalog):
    result = resolve_portfolio(
        catalog, "Small Cap Value Fund",
        question="Which high-risk portfolio has the largest technology exposure?",
        context=["Growth Equity Fund", "Tech Innovation Fund", "Small Cap Value Fund", "Emerging Markets Fund"],
    )
    assert (result.status, result.portfolio_name, result.grounded_in_question) == (
        "resolved", "Small Cap Value Fund", True)
    assert result.sector_terms == ["Technology"]


# --- SR1: SQL syntax is rejected before any matching ------------------------------------------

@pytest.mark.parametrize("phrase", [
    "Growth Equity Fund'; DROP TABLE holdings;--",
    "Balanced Portfolio -- ignore the rest",
    "Tech Innovation Fund /* note */",
    "x' OR '1'='1",
    "Balanced Portfolio OR 1=1",
    "Growth Equity Fund UNION SELECT * FROM holdings",
    "SELECT portfolio_name FROM portfolios",
    "ATTACH DATABASE 'evil.db' AS evil",
    "PRAGMA table_info(holdings)",
    "DELETE FROM portfolios",
    "update portfolios set total_aum = 0",
    "insert into holdings values (1)",
    "drop table holdings",
])
def test_sr1_rejects_sql_syntax_without_matching(catalog, phrase):
    result = resolve_portfolio(catalog, phrase)
    assert (result.status, result.rule) == ("rejected", "SR1")
    assert result.portfolio_id is None and result.candidates == []
    assert result.normalised_phrase == ""  # rejected before normalisation or matching


@pytest.mark.parametrize("phrase, expected", [
    ("Fixed Income Plus", "Fixed Income Plus"),
    ("Balanced Portfolio.", "Balanced Portfolio"),
    ("Dividend Aristocrats Fund, please", "Dividend Aristocrats Fund"),
    ("Small-Cap Value", "Small Cap Value Fund"),
    ("ESG (Sustainable) Fund", "ESG Sustainable Fund"),
    ("Update on the Balanced Portfolio", "Balanced Portfolio"),
    ("Total Bond Market Index Fund (TBM)", "Total Bond Market Index Fund"),
])
def test_sr1_allows_legitimate_names_and_punctuation(catalog, phrase, expected):
    result = resolve_portfolio(catalog, phrase)
    assert (result.status, result.portfolio_name) == ("resolved", expected)


@pytest.mark.parametrize("phrase", ["Growth & Value Fund", "60/40 Portfolio", "Investor's Growth Fund",
                                    "Select Equity Fund", "O'Neil Income Fund"])
def test_sr1_does_not_flag_unknown_but_legitimate_names(catalog, phrase):
    assert resolve_portfolio(catalog, phrase).status != "rejected"


@pytest.mark.parametrize("phrase", ["", "   ", None, "x" * 250])
def test_empty_or_oversized_input_is_invalid(catalog, phrase):
    assert resolve_portfolio(catalog, phrase).status == "invalid_input"


# --- receipt fields (B5.7) --------------------------------------------------------------------

def test_resolution_records_what_the_receipt_needs(catalog):
    result = resolve_portfolio(catalog, "International Portfolio")
    assert result.input_phrase == "International Portfolio"
    assert result.normalised_phrase == "international portfolio"
    assert result.rule == "B5.5"
    assert result.confirmation_required is True
    assert [(c.name, c.reason) for c in result.candidates] == [
        ("International Equity Fund", "name contains 'international'"),
        ("Total International Index Fund", "name contains 'international'"),
    ]
    assert result.grounded_in_question is None  # no question supplied
    resolved = resolve_portfolio(catalog, "international equity")
    assert (resolved.matched_phrase, resolved.confirmation_required) == ("international equity", False)
