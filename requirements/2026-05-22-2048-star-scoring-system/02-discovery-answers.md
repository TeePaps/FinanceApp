# Phase 2: Discovery Answers

## Q1: Should the star rating be visible on multiple tabs (Recommendations, Holdings, Screener) rather than only one?
**Answer:** No — show on a **new dedicated tab entirely** (sort of a new screener system).

**Implication:** Build a new tab in the UI (templates/index.html + static/app.js) for the star scoring system. Independent from Recommendations / Holdings / Screener tabs. Will likely have its own API endpoint, its own sorting, its own display logic.

---

## Q2: Are you willing to add new data sources (FMP analyst estimates, SEC 10-K balance sheet parsing) to power criteria the app does not currently fetch?
**Answer:** Yes — add what's needed.

**Implication:** Build new provider methods / extend providers to fetch:
- Analyst EPS estimates (forward / current-quarter consensus)
- Total debt + total equity (for debt-to-capital ratio)
- Dividend year-over-year history
- Shares outstanding (current + historical)

---

## Q3: Should stars be recalculated automatically as part of the existing screener batch run?
**Answer:** Yes — in screener batch.

**Implication:** Add a new screener phase (after the existing EPS / Dividends / Prices / Valuations phases). Stars stored in DB for fast tab loads. Phase progress tracked alongside existing ones in `screener_progress`.

---

## Q4: Should historical comparisons use point-in-time snapshots stored in the DB?
**Answer:** Yes — store snapshots.

**Implication:** New tables needed:
- `valuation_history` (ticker, date, estimated_value)
- `dividend_history` (ticker, year, annual_dividend) — or extend existing dividend storage
- `shares_outstanding_history` (ticker, date, shares)
- Snapshot once per screener run.

---

## Q5: Is it acceptable for missing data → no star (graceful degradation)?
**Answer:** Yes — missing = no star.

**Implication:** Each criterion check returns boolean (earned / not earned). If underlying data is missing, returns False. Ticker still gets a rating (out of 6 stars). UI just shows unearned stars as empty/dim. No special "N/A" state needed.
