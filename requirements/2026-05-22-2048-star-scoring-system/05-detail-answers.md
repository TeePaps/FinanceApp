# Phase 4: Expert Detail Answers

## Q1: Tab scope — show all tickers or only holdings?
**Answer:** **Two sections.** One section for holdings (rated out of 6), another for non-holdings (rated out of 4), separated visually on the same tab.

**Implication:**
- API endpoint should return two arrays: `holdings: [...]` and `watchlist: [...]` (or similar)
- Frontend renders two distinct lists with their own headers
- Star display logic needs to know max possible (6 vs 4) for each section to show empty stars correctly
- Holdings section likely sorted to top of page

---

## Q2: Criterion 1 (earnings beat) — historical or forward?
**Answer:** Yes — historical beat. Most recent REPORTED EPS compared to the prior analyst consensus estimate that existed before the report.

**Implication:**
- Need to fetch: actual reported EPS for most recent quarter + the consensus analyst estimate that preceded it
- Star earned when `actual_EPS > consensus_estimate`
- yfinance `Ticker.earnings_history` exposes `epsActual`, `epsEstimate`, `surprisePercent` for the last 4 quarters
- Stable signal (updates only quarterly)

---

## Q3: Backfill historical comparisons on first run?
**Answer:** Yes — backfill on first run.

**Implication:**
- For Criterion 2 (fair value up): recompute prior-year fair value using `eps_history` shifted back 1 year. Formula: `(8-year EPS avg ending 1 year ago + annual_dividend 1 year ago) × 10`. Compare to current `estimated_value`.
- For Criterion 3 (dividend up): fetch full dividend history from yfinance, sum dividends paid in the trailing 12 months ending 1 year ago, compare to current `annual_dividend`.
- Feature works immediately on day one.
- After first run, snapshots are stored for future comparisons (we'll continue snapshotting in case raw history becomes unavailable later).

---

## Q4: Criterion 4 (debt-to-capital) — formula and source?
**Answer:** Yes — standard formula `Total Debt / (Total Debt + Stockholders Equity)` where Total Debt = LongTermDebtNoncurrent + DebtCurrent. Source: SEC EDGAR companyfacts.

**Implication:**
- Extend `services/providers/sec_provider.py` (or `sec_data.py`) to fetch additional us-gaap concepts: `LongTermDebtNoncurrent`, `DebtCurrent`, `StockholdersEquity`
- Compute ratio; star earned when ≤ 0.25
- Cache the latest values per ticker (likely in a new `balance_sheet` table or extend valuations)
- Reuse SEC rate-limiter and circuit breaker

---

## Q5: Criterion 6 (undervalued, holdings-only) — definition?
**Answer:** Yes — reuse existing `current_price < estimated_value` definition. Star earned when `price_vs_value < 1.0`.

**Implication:**
- No new threshold needed. Read `price_vs_value` directly from `valuations` table.
- Star earned when `price_vs_value < 1.0`
- Consistent with what users already see on the recommendations tab.
