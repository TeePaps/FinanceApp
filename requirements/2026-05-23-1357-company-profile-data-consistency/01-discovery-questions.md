# Phase 2: Discovery Questions

Two related fixes. Questions are about which data source wins and what to
do when sources disagree.

---

## Q1: Should everything on the Company Profile page (fair-value formula card + star explanation table) read from a SINGLE data source, so the two halves of the page always agree?
**Default if unknown:** Yes. Have `/api/valuation/<ticker>` return the explanation breakdown alongside the formula data in one response (single round trip, guaranteed consistency).

## Q2: When `calculate_valuation()` fetches dividends fresh and gets `$0.00` but the cached row has a non-zero dividend, should it prefer the cached value (treat the zero as a flaky-fetch and don't overwrite)?
**Default if unknown:** Yes. yfinance has a known flakiness where it returns 0 for dividend-paying stocks. Treat a `$0 → non-zero` regression as suspicious and keep the cached value. Only overwrite a cached non-zero dividend with `$0` if we get repeated confirmations.

## Q3: When a ticker's cached `valuations` row is empty/partial (e.g., newly tracked), should the Company Profile lazily backfill it (refresh + save) on first view, so future visits show consistent data immediately?
**Default if unknown:** Yes. Detecting "this row is missing eps_avg or estimated_value" and triggering a backfill on view is small and prevents the "fresh page shows missing data" state the user saw for USB.
