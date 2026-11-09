# Business rules

Rules that the implementation and its tests must follow. Rule IDs are cited in code comments,
tests and Financial Answer Receipts.

- **Frozen:** approved by the project owner, changed only by an explicit new decision.
- **Proposed:** default adopted, owner confirmation needed before the milestone that uses it.

## Sector exposure

### B1: Weight basis (Frozen, 2026-11-06)

The exposure calculator weights each holding by `holdings.current_weight`, a fraction
(0.15 = 15%). It never uses market value. Market-value calculations (`quantity * current_price`)
stay separate and are used where the supplied SQL defines them (Q7, and SQL answers about value).

### B2: Two labelled methods (Frozen, 2026-11-06)

Every exposure result shows both methods, each with its label and denominator. Only stocks
(`asset_type = 'Stock'`) with a sector are eligible; bonds are excluded.

| Method | Formula | Denominator | Sectors sum to |
|---|---|---|---|
| **A: share of total portfolio** | sum of `current_weight` of the sector's stocks | the whole portfolio (100%) | the portfolio's equity weight |
| **B: equity-sleeve composition** | the same sum divided by the sum of `current_weight` of all eligible stocks | the equity sleeve | 100% |

### B3: Unattributed weight residual (Frozen, 2026-11-06)

`residual = 100% - SUM(current_weight)` over **all** holdings in the portfolio, stocks and bonds.
It is never computed from stock weights alone.

- Method A shows it as a separate line, "Unattributed weight residual: X.XX%", when its absolute
  value exceeds 0.01 percentage points.
- It is never classified as cash and never redistributed.
- It is reported as a data-integrity WARNING. A negative residual (weights above 100%) is also a
  WARNING.

Method A therefore always reconciles: sector exposures + excluded bond weight + residual = 100%.

### Worked results

| Portfolio | Method A (% of portfolio) | Method B (% of equity sleeve) |
|---|---|---|
| Balanced Portfolio | Technology 22.00, Financials 8.00, Healthcare 6.00; bonds excluded 64.00; residual 0.00 | Technology 61.11, Financials 22.22, Healthcare 16.67 |
| Total Stock Market Index Fund | Technology 48.00, Healthcare 16.00, Financials 14.00, Consumer Discretionary 10.00, Automotive 6.00, Consumer Staples 4.00 (total 98.00); residual 2.00 | 48.98, 16.33, 14.29, 10.20, 6.12, 4.08 |
| Tech Innovation Fund (Q9) | Technology 100.00 | Technology 100.00 |
| International Equity Fund (Q10) | Energy 38.00, Technology 36.00, Automotive 15.00, Consumer Staples 11.00 | Same values (all stocks, weights total 100%) |
| Fixed Income Plus | No eligible stocks: stated explicitly, nothing calculated | Not applicable |

International Equity Fund's Consumer Staples exposure is entirely Home Depot (see D6).

## Portfolio name resolution

### B4: Typos (Frozen, 2026-11-06)

No silent auto-correction. When no rule in B5 matches, the result is "not found" with up to
three suggestions ranked by string similarity, and the user confirms.

### B5: Resolution rules (Frozen, revised 2026-11-06; interpretations approved 2026-11-06)

Names are matched against portfolio names and approved aliases only. Holdings, strategy type,
benchmark and asset allocation never select a portfolio. Holdings may be used only to list
candidates for the user to choose from (rule 5).

**Normalisation:** case-insensitive; "&" equals "and"; punctuation ignored; spacing collapsed;
filler words ignored (the, a, an, our, my, for, of).

**Generic vocabulary** (asset-class, strategy and container words): bond, bonds, stock, stocks,
equity, equities, income, fixed, international, global, domestic, index, market, markets, total,
growth, value, dividend, esg, sustainable, balanced, conservative, aggressive, small, mid,
large, cap, emerging, sector, fund, funds, portfolio, portfolios.

**Approved aliases:** `tech` → Tech Innovation Fund, only in the portfolio-reference position
(rule 2).

Rules, applied in order:

1. **Full name.** The phrase equals a portfolio's full name after normalisation, so it resolves.
   The trailing "Fund" or "Portfolio" may be omitted only if at least two words remain and they
   are an exact canonical match ("International Equity" = International Equity Fund;
   "international equities" is not a match). A phrase containing exactly one such full name
   ("sector exposure of the Growth Equity Fund") also resolves. Two or more full names lead to
   clarification.
2. **Approved alias, by position.** The phrase, ignoring "fund" and "portfolio", equals an approved
   alias. Its role is read from the word that follows each use of the alias:
   - Followed by "fund" or "portfolio" ("the tech fund"), or the bare tool argument with no
     question: a portfolio reference. The alias resolves.
   - Followed by a sector word such as "exposure", "stocks", "holdings" or "weight" ("tech
     exposure of the Balanced Portfolio"): the Technology sector. The alias is not applied. If the
     question names exactly one portfolio in full, that portfolio is used; otherwise the user is
     asked which portfolio.
   - Any other position ("sector breakdown for tech"): ambiguous. The user is asked whether they
     mean Tech Innovation Fund or the Technology sector.
3. **Distinctive multiword phrase.** A phrase of two or more words that appears in exactly one
   portfolio name and contains at least one non-generic word resolves. Example: "income plus" →
   Fixed Income Plus ("plus" is not generic).
4. **Single words.** A single word resolves only through rule 2. Any other single word found in
   portfolio names produces a confirmation question.
5. **Generic phrases never select a portfolio.** A phrase made only of generic words (and not a
   full name under rule 1) produces a clarification question. Candidates are the portfolios whose
   names contain the phrase's words. When the phrase is exactly "bond", "bonds" or "fixed income"
   (ignoring "fund"/"portfolio"), portfolios holding at least 50% bonds by `current_weight` are
   added and shown with their bond weight, because only one name contains "bond".
6. **No match.** Rule B4 applies.
7. **Receipt.** Records the input phrase, normalised phrase, matched phrase, rule applied,
   candidates (and why each is listed), whether user confirmation was required, and whether the
   phrase was found in the user's question.

**Grounding check.** The portfolio phrase comes from the LLM's tool call. When the user's question
is available, a phrase that resolves is accepted only if the question, or an earlier tool result,
names that portfolio directly by rule 1, 2 or 3. Otherwise the result becomes a confirmation
question built from the words of the question. This stops the LLM from upgrading a generic phrase
("the balanced fund") into a full name and skipping the confirmation rule 5 requires.

| Input | Result | Rule |
|---|---|---|
| International Equity Fund | International Equity Fund | 1 |
| international equity (Q10's wording) | International Equity Fund | 1 |
| sector exposure of the Growth Equity Fund | Growth Equity Fund | 1 |
| Growth Equity Fund and Tech Innovation Fund | Asks which one | 1 |
| tech, tech fund | Tech Innovation Fund | 2 |
| income plus | Fixed Income Plus | 3 |
| aristocrats | Asks: "Did you mean Dividend Aristocrats Fund?" | 4 |
| balanced | Asks: "Did you mean Balanced Portfolio?" | 5 |
| bond, bonds, bond fund, fixed income | Asks to choose: Total Bond Market Index Fund, Fixed Income Plus (100% bonds), Conservative Income Fund (68%), Balanced Portfolio (64%) | 5 |
| international, international portfolio | Asks: International Equity Fund or Total International Index Fund | 5 |
| equity fund | Asks: Growth Equity Fund or International Equity Fund | 5 |
| index fund | Asks: one of the three Total ... Index Funds | 5 |
| market | Asks: Total Stock Market Index Fund or Total Bond Market Index Fund | 5 |
| stock, total stock market | Asks: "Did you mean Total Stock Market Index Fund?" | 5 |
| Tech Inovation Fund | Not found; "Did you mean Tech Innovation Fund?" | 6 |
| Bitcoin Fund | Not found; no close names | 6 |
| "tech" in "Show the sector exposures of the tech fund" | Tech Innovation Fund | 2 |
| "tech" in "What is the tech exposure of the Balanced Portfolio?" | Balanced Portfolio; "tech" read as the Technology sector | 2, then 1 |
| "tech" in "Show me the sector breakdown for tech" | Asks: Tech Innovation Fund or the Technology sector? | 2 |
| "Balanced Portfolio" proposed for "Show the balanced fund's exposure" | Asks: "Balanced Portfolio?" (not named in the question) | Grounding |

These rows are regression tests in `tests/test_resolver.py`.

## Data rules

| ID | Rule | Status |
|---|---|---|
| D1 | `current_weight` is a fraction: 0.15 = 15% | Frozen |
| D2 | Market value = `quantity * current_price`; portfolio value = sum of market value over all holdings, bonds included, as in Q7's supplied SQL. Never substituted by `current_weight` | Frozen |
| D3 | Diversification ratio = distinct stock sectors / total holdings (bonds count in the denominator). This is the assessment's definition from Q8, not a general financial formula; the semantic layer labels it as such | Frozen |
| D4 | "Portfolio value" means the market value of recorded holdings, `quantity * current_price`, when the question concerns holdings value. It is never equated with AUM. AUM is the supplied `portfolios.total_aum` field. Holdings cover only 1.7% to 14.7% of stated AUM, and answers say so when relevant. Official assessment questions follow their supplied SQL | Frozen (approved 2026-11-06) |
| D5 | `Active` and `Passive` are the supplied status values; "active portfolios" means `status = 'Active'`. No claim is made about operational availability | Frozen |
| D6 | Source records are never modified. Reported anomalies: Home Depot classified as Consumer Staples (preserved, flagged); all securities marked US (no geographic-diversification claims); holdings vs transactions gaps (reconciliation gaps, history may be incomplete); performance and risk data only for portfolios 1 to 4 (answers say the data is unavailable) | Frozen |
| D7 | Performance and risk values are reported as stored. No units, percentage interpretation, annualisation or financial definition is invented beyond what the schema or source documentation supports | Frozen (approved 2026-11-06) |

## Evaluation and query rules

| ID | Rule | Status |
|---|---|---|
| B6 | Portfolio IDs as input ("portfolio 7") | Withdrawn: conflicts with B5 (names and aliases only) |
| B7 | Row order is compared only where the reference SQL combines ORDER BY with LIMIT (a top-N question); other results are compared order-independently | Frozen (master directive, Part 12) |
| B8 | Replaced by D3 | Superseded |
| B9 | When no date is supplied, use the latest available date in the relevant table or time series, read from the data. Not every table ends on 2023-10-09: `holdings.purchase_date` ends on 2023-09-25 and `transactions.settlement_date` on 2023-10-11 | Frozen (approved 2026-11-06) |
| B10 | Relative dates are anchored to the latest date of the relevant dataset, never the computer's date, and resolved by code (`src/portfolioiq/dates.py`). "Last week/month/quarter/year" is the previous completed calendar period (weeks Monday to Sunday). "Past 7 days/30 days/90 days/12 months" is a rolling period ending on the anchor date. Exact start and end dates are recorded, the anchor is disclosed in the answer and receipt, and dataset-relative dates are never presented as current real-world dates | Frozen (approved 2026-11-06) |
| B11 | "Current price" means `securities.current_price` (as in Q7), not the last historical close | Frozen (follows D2) |
| B12 | Integers must match exactly; other numbers match within 0.005 absolute or 1e-6 relative; no unit or scale conversion (0.8467 does not match 84.67) | Proposed; applied by the M3 evaluator (`src/portfolioiq_eval/comparator.py`), confirmation pending |
| B13 | Strict accuracy requires the same set of columns, matched by values with aliases ignored; extra columns fail strict accuracy and are reported separately as relaxed accuracy | Frozen (master directive, Part 12) |
| SR1 | A portfolio reference containing SQL syntax is rejected before any name matching: the input is never sanitised and then matched. Rejected: `;`, `--`, `/*`, `*/`, statement shapes (DROP/ALTER TABLE, DELETE FROM, INSERT INTO, UPDATE ... SET, CREATE TABLE, ATTACH/DETACH DATABASE, PRAGMA, UNION SELECT, SELECT ... FROM) and always-true conditions (`' OR '1'='1`, `OR 1=1`). Allowed: single keywords ("Select Equity Fund") and ordinary punctuation (`& / - ' ( ) , .`) | Frozen (approved 2026-11-06) |

## Exposure data validation (engineering decisions, M2)

These make B1 to B3 safe on imperfect data without altering it. They are implemented in
`src/portfolioiq/exposure.py` and tested in `tests/test_exposure.py`.

| ID | Situation | Behaviour |
|---|---|---|
| E1 | A holding has no `current_weight` | WARNING; the holding is excluded from every figure and named in a caveat; its unknown weight stays inside the residual |
| E2 | A weight is negative, above 1 or not finite | FAIL; no exposure figures are produced; verification is VERIFICATION_FAILED |
| E3 | A security appears twice in one portfolio | FAIL; no figures (the schema's UNIQUE constraint normally prevents this) |
| E4 | A stock has no sector | WARNING; shown as the excluded line "Stocks without a sector" and left out of the Method B denominator |
| E5 | Tolerances | A residual is reported above 0.0001 (0.01 percentage points); the Python and SQL paths must agree within 1e-9 |
| E6 | Display rounding | Half up to 2 decimals at display time only (15.625% shows as 15.63); totals use unrounded values |

## Independent verification rules (November 2026 audit)

Checked against the original assessment files on 2026-11-09. All nine CSVs, the schema and the ground truth are byte-identical after line-ending normalisation. The database holds 9 tables, 13 portfolios, 30 securities (25 stocks, 5 bonds), 95 holdings, 24 transactions, 330 prices, 44 performance rows and 44 risk rows. There are no orphaned or duplicate holdings.

- **Diversification ratio.** Distinct stock sectors divided by all holdings, bonds included. It is rebuilt per portfolio from holding records, without reading the SQL. Total Stock Market Index Fund gives 6 / 12 = 0.500 and is the only portfolio above five sectors.
- **Bonds.** The five bonds have no sector in the supplied data, so they add to holdings and never to sectors. The supplied fund above holds no bonds, so errors that depend on a bond are tested on a modified copy of the database.
- **Column roles.** A component column is matched to its figure by name: `sector`, or `holding` or `total`. A column with another name is reported as not verifiable.
- **Selections.** A superlative or a top-N diversification question is reported as not verifiable. The check models thresholds on sectors, a named portfolio, and all portfolios.
- **Counts.** Counts are rebuilt from holdings, portfolios or securities. Counts over other tables are reported as not verifiable.
- **"No sector" groups.** A result that omits a group with no sector is not treated as incomplete, because an inner join on sectors rightly has no such group.
