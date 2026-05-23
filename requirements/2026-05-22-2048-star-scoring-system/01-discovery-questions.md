# Phase 2: Discovery Questions

These are high-level yes/no questions to scope the star scoring system. Each has a smart default.

---

## Q1: Should the star rating be visible on multiple tabs (Recommendations, Holdings, Screener) rather than only one?
**Default if unknown:** Yes (a unified rating system is most useful when consistently visible across views)

## Q2: Are you willing to add new data sources (e.g., FMP analyst estimates, SEC 10-K balance sheet parsing) to power criteria that the app does not currently fetch (analyst EPS projections, debt/equity, shares outstanding, dividend history)?
**Default if unknown:** Yes (4 of the 6 criteria cannot be computed from existing data; either we add new providers or we shrink the criteria set)

## Q3: Should stars be recalculated automatically as part of the existing screener batch run (rather than on-demand per user click)?
**Default if unknown:** Yes (the screener is the natural place — it already fetches EPS, dividends, and prices in phases)

## Q4: Should the historical comparisons (fair value vs. last year, dividend vs. last year, shares outstanding vs. buy date) use point-in-time snapshots stored in the database, rather than recomputed from raw data each time?
**Default if unknown:** Yes (snapshots are simpler and faster; raw recomputation requires deeper historical data we may not have)

## Q5: Is it acceptable for a ticker to receive 0 stars for a criterion when the required data is missing (rather than excluding the ticker or showing "N/A")?
**Default if unknown:** Yes (graceful degradation; missing data → unearned star, but ticker still shows a rating)
