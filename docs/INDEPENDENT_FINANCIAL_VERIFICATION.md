# Independent financial verification

Results are in [`reports/final_verification/independent_verification.json`](../reports/final_verification/independent_verification.json). Reproduce them, offline, with `python scripts/run_independent_verification.py`.

## Method

`src/portfolioiq_eval/independent_reference.py` recalculates each figure with pandas from the supplied CSV files. It imports nothing from the runtime package and uses no SQL, SQLite or model output, and a test enforces that. The SQL answers compared with it are the recorded live answers of the production configuration. Exposure and aggregate figures are computed now by the production code.

A figure is compared with a numeric tolerance of 0.005, or 0.0006 for a ratio commonly shown to three decimals. Exposure figures must agree within 1e-9. Row order counts only where the question fixes it. Relative dates are anchored to the latest date in the dataset.

## Result

29 items reconciled, 6 list-only results matched, 4 stored figures could not be recomputed, and 0 failed.

Agreement here shows that production and an independent calculation give the same figure on this dataset. It says nothing about questions that were not asked.

## Q8 diversification ratio

The ratio is distinct stock sectors divided by all holdings, bonds included. Rebuilt from the CSV files, Total Stock Market Index Fund has 6 distinct stock sectors and 12 holdings, which gives 0.500. It is the only portfolio above five sectors. The reference is tested for bonds, several stocks in one sector, missing sector mappings, no stocks, duplicates and zero holdings.

The recorded production query counts all distinct sectors and does not filter on stocks. It is correct on this dataset only because every bond has no sector. The runtime verifier would mark that query as failed if a bond with a sector entered the fund, and a test demonstrates this.

## Runtime verification

The verifier now also rebuilds averages, minimums, maximums, and counts and sums over transactions, prices, performance and risk records. Over the 35 recorded answers, 17 reconcile, 17 contain no calculation and 1 cannot be rebuilt.

## What stays unverified

| Figure | Why |
|---|---|
| Beta, NAV and other performance and risk values | They are stored in the source. Retrieval is checked, but the data has no return series to recompute them, and no units are documented |
| Stated AUM | It is stated in the source. Recorded holdings cover only part of it, so it cannot be derived |
| Latest date in a query such as `MAX(transaction_date)` | The runtime verifier handles numbers only, so it reports this as not verifiable |
| Calculations outside the supported forms, including joins it does not model | Reported as not verifiable, never as reconciled |

## Open decision: Method B and unclassified stocks

The current brief says unclassified stocks stay in the Method B denominator. Frozen rule B2 and `exposure.py` use only stocks with a sector. The supplied data has no unclassified stock, so every figure is identical today. The reference follows the brief, production follows B2, and production was not changed.

## Inventory

| Question or metric | Production calculation | Independent reference | Largest difference | Status |
|---|---|---|---:|---|
| Q1: How many portfolios do we have in total? | SELECT COUNT(*) AS total_portfolios FROM portfolios; | Row count of portfolios.csv. | 0.00e+00 | `INDEPENDENTLY_RECONCILED` |
| Q2: What are the names of all active portfolios? | SELECT portfolio_name FROM portfolios WHERE status = 'Active'; | Filter status == Active. | 0.00e+00 | `RESULT_VALIDATED` |
| Q3: Which securities are in the Technology sector? | SELECT s.symbol, s.company_name FROM securities AS s JOIN sectors AS sec ON sec.sector_id = s.… | Securities joined to sectors, filter Technology. | 0.00e+00 | `RESULT_VALIDATED` |
| Q4: What is the total Assets Under Management (AUM) for portfolios with high ta… | SELECT SUM(total_aum) AS total_aum FROM portfolios WHERE target_risk_level = 'High'; | Sum of stated total_aum where risk is High. | 0.00e+00 | `INDEPENDENTLY_RECONCILED` |
| Q5: Show me the top 5 holdings by cost basis in the Growth Equity Fund | SELECT h.holding_id, s.symbol, s.company_name, h.quantity, h.cost_basis FROM holdings h JOIN s… | Growth Equity Fund holdings sorted by cost_basis, first five. | 0.00e+00 | `INDEPENDENTLY_RECONCILED` |
| Q6: What is the average current price of securities in each sector? | SELECT sec.sector_name, AVG(s.current_price) AS average_current_price FROM securities s JOIN s… | Mean of current_price per sector, stocks with a sector only. | 5.68e-14 | `INDEPENDENTLY_RECONCILED` |
| Q7: For each portfolio, show the total value of Technology sector holdings and … | WITH tech_part AS (SELECT h.portfolio_id, SUM(h.quantity * s.current_price) AS tech_market_val… | quantity x current_price per holding. The denominator includes bonds. Portfolio… | 4.76e-03 | `INDEPENDENTLY_RECONCILED` |
| Q8: Find portfolios that have holdings in more than 5 different sectors and sho… | SELECT p.portfolio_name, COUNT(DISTINCT s.sector_id) AS distinct_sectors, COUNT(h.holding_id) … | Distinct stock sectors / all holdings including bonds; more than five sectors. | 0.00e+00 | `INDEPENDENTLY_RECONCILED` |
| U1: Which portfolios hold NVIDIA? | SELECT DISTINCT p.portfolio_name FROM holdings h JOIN securities s ON s.security_id = h.securi… | Portfolios holding NVDA. | 0.00e+00 | `RESULT_VALIDATED` |
| U2: How many holdings does the Dividend Aristocrats Fund have? | SELECT COUNT(*) AS holding_count FROM holdings h JOIN portfolios p ON p.portfolio_id = h.portf… | Row count of that fund's holdings. | 0.00e+00 | `INDEPENDENTLY_RECONCILED` |
| U3: At current prices, how much are the bonds held across all portfolios worth? | SELECT SUM(h.quantity * s.current_price) AS total_bond_market_value FROM holdings h JOIN secur… | quantity x current_price, bonds only. | 0.00e+00 | `INDEPENDENTLY_RECONCILED` |
| U4: List each bond with its coupon rate and maturity date. | SELECT symbol, coupon_rate, maturity_date FROM securities WHERE asset_type = 'Bond' | Bond rows from securities.csv. | 0.00e+00 | `RESULT_VALIDATED` |
| U5: Most recent beta for the Conservative Income Fund? | SELECT rm.beta FROM risk_metrics rm JOIN portfolios p ON p.portfolio_id = rm.portfolio_id WHER… | Stored beta at the latest calculation date. Retrieval is checked; the source ha… | 0.00e+00 | `NOT_INDEPENDENTLY_VERIFIABLE` |
| U6: How many BUY transactions were made in the past 7 days? | SELECT COUNT(*) AS buy_transaction_count FROM transactions WHERE transaction_type = 'BUY' AND … | BUY rows in the seven days ending at the dataset's latest transaction date, 202… | 0.00e+00 | `INDEPENDENTLY_RECONCILED` |
| U7: Which portfolios have never had their performance recorded? | SELECT portfolio_name FROM portfolios WHERE portfolio_id NOT IN (SELECT DISTINCT portfolio_id … | Portfolios absent from portfolio_performance.csv. | 0.00e+00 | `RESULT_VALIDATED` |
| U8: What is the average purchase price paid for Apple across all portfolios? | SELECT AVG(h.purchase_price) AS average_purchase_price FROM holdings h JOIN securities s ON s.… | Mean purchase_price of AAPL holdings. | 0.00e+00 | `INDEPENDENTLY_RECONCILED` |
| U9: Show all transactions from last year. | SELECT t.transaction_id, s.symbol, t.transaction_type, t.transaction_date, t.quantity, t.price… | Transactions in 2022, the calendar year before the dataset's latest date. | 0.00e+00 | `RESULT_VALIDATED` |
| U10: Which sector has the most securities? | SELECT sec.sector_name FROM sectors sec JOIN securities s ON sec.sector_id = s.sector_id GROUP… | Sector with the most securities (8). | 0.00e+00 | `INDEPENDENTLY_RECONCILED` |
| Exposure, Methods A and B, residual: Growth Equity Fund | calculate_exposure (Python and SQL paths) | pandas groupby over holdings, securities and sectors CSVs | 0.00e+00 | `INDEPENDENTLY_RECONCILED` |
| Exposure, Methods A and B, residual: Conservative Income Fund | calculate_exposure (Python and SQL paths) | pandas groupby over holdings, securities and sectors CSVs | 0.00e+00 | `INDEPENDENTLY_RECONCILED` |
| Exposure, Methods A and B, residual: Tech Innovation Fund | calculate_exposure (Python and SQL paths) | pandas groupby over holdings, securities and sectors CSVs | 1.11e-16 | `INDEPENDENTLY_RECONCILED` |
| Exposure, Methods A and B, residual: Balanced Portfolio | calculate_exposure (Python and SQL paths) | pandas groupby over holdings, securities and sectors CSVs | 0.00e+00 | `INDEPENDENTLY_RECONCILED` |
| Exposure, Methods A and B, residual: ESG Sustainable Fund | calculate_exposure (Python and SQL paths) | pandas groupby over holdings, securities and sectors CSVs | 0.00e+00 | `INDEPENDENTLY_RECONCILED` |
| Exposure, Methods A and B, residual: Small Cap Value Fund | calculate_exposure (Python and SQL paths) | pandas groupby over holdings, securities and sectors CSVs | 0.00e+00 | `INDEPENDENTLY_RECONCILED` |
| Exposure, Methods A and B, residual: International Equity Fund | calculate_exposure (Python and SQL paths) | pandas groupby over holdings, securities and sectors CSVs | 1.11e-16 | `INDEPENDENTLY_RECONCILED` |
| Exposure, Methods A and B, residual: Fixed Income Plus | calculate_exposure (Python and SQL paths) | pandas groupby over holdings, securities and sectors CSVs | 0.00e+00 | `INDEPENDENTLY_RECONCILED` |
| Exposure, Methods A and B, residual: Dividend Aristocrats Fund | calculate_exposure (Python and SQL paths) | pandas groupby over holdings, securities and sectors CSVs | 0.00e+00 | `INDEPENDENTLY_RECONCILED` |
| Exposure, Methods A and B, residual: Emerging Markets Fund | calculate_exposure (Python and SQL paths) | pandas groupby over holdings, securities and sectors CSVs | 1.11e-16 | `INDEPENDENTLY_RECONCILED` |
| Exposure, Methods A and B, residual: Total Stock Market Index Fund | calculate_exposure (Python and SQL paths) | pandas groupby over holdings, securities and sectors CSVs | 1.11e-16 | `INDEPENDENTLY_RECONCILED` |
| Exposure, Methods A and B, residual: Total Bond Market Index Fund | calculate_exposure (Python and SQL paths) | pandas groupby over holdings, securities and sectors CSVs | 0.00e+00 | `INDEPENDENTLY_RECONCILED` |
| Exposure, Methods A and B, residual: Total International Index Fund | calculate_exposure (Python and SQL paths) | pandas groupby over holdings, securities and sectors CSVs | 0.00e+00 | `INDEPENDENTLY_RECONCILED` |
| Holding count per portfolio | SELECT p.portfolio_name, COUNT(*) FROM holdings h JOIN portfolios p USING (portfolio_id) GROUP… | pandas over the CSVs | 0.00e+00 | `INDEPENDENTLY_RECONCILED` |
| Distinct securities per portfolio | SELECT p.portfolio_name, COUNT(DISTINCT h.security_id) FROM holdings h JOIN portfolios p USING… | pandas over the CSVs | 0.00e+00 | `INDEPENDENTLY_RECONCILED` |
| Cost basis per portfolio | SELECT p.portfolio_name, SUM(h.cost_basis) FROM holdings h JOIN portfolios p USING (portfolio_… | pandas over the CSVs | 0.00e+00 | `INDEPENDENTLY_RECONCILED` |
| Market value (quantity x current price) per portfolio | SELECT p.portfolio_name, SUM(h.quantity * s.current_price) FROM holdings h JOIN securities s U… | pandas over the CSVs | 0.00e+00 | `INDEPENDENTLY_RECONCILED` |
| Sum of weights per portfolio | SELECT p.portfolio_name, SUM(h.current_weight) FROM holdings h JOIN portfolios p USING (portfo… | pandas over the CSVs | 0.00e+00 | `INDEPENDENTLY_RECONCILED` |
| Stated AUM per portfolio (source-stated; not derivable from holdings, which cov… | SELECT portfolio_name, total_aum | portfolios.csv | 0.00e+00 | `NOT_INDEPENDENTLY_VERIFIABLE` |
| Latest NAV per portfolio (stored; no units documented) | SELECT p.portfolio_name, x.nav FROM portfolio_performance x JOIN portfolios p USING (portfolio… | portfolio_performance.csv at the latest date | 0.00e+00 | `NOT_INDEPENDENTLY_VERIFIABLE` |
| Latest beta per portfolio (stored; no return series to recompute it) | SELECT p.portfolio_name, x.beta FROM risk_metrics x JOIN portfolios p USING (portfolio_id) WHE… | risk_metrics.csv at the latest date | 0.00e+00 | `NOT_INDEPENDENTLY_VERIFIABLE` |
