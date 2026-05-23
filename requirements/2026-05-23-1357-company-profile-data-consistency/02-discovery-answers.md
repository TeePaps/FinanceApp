# Phase 2: Discovery Answers

## Q1: Single source on Company Profile?
**Answer:** Yes — keep two endpoints but share data.

**Implication:** The frontend calls `/api/valuation/<ticker>` first, then passes
the freshly-computed valuation to `/api/stars/<ticker>/explanation` so both
the formula card and the explain table render from the same numbers. The
explanation endpoint accepts an optional valuation override.

---

## Q2: Don't overwrite cached non-zero dividend with $0 from flaky fetch?
**Answer:** Yes — keep cached non-zero.

**Implication:** Inside `calculate_valuation()` (and the screener's Phase 2),
when `fetch_dividends()` returns `0` or `None` for a ticker whose cached row
already has a non-zero `annual_dividend`, treat the fetch as suspect and
preserve the cached value. Only overwrite a non-zero dividend with `0` if
SEC dividend data also confirms it (or by explicit user "force refresh").

---

## Q3: Lazy backfill on first Company Profile view?
**Answer:** Yes — backfill on first view.

**Implication:** `/api/valuation/<ticker>` should save its computed result
back to the `valuations` table (matching what `/api/valuation/<ticker>/refresh`
already does), so subsequent views and the Stars tab see the same numbers.
