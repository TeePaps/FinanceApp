# Phase 2: Discovery Questions

The data + endpoint architecture for stars already exists. Open questions are
about UX placement and edge cases.

---

## Q1: Should the table always show all 6 rows (with "N/A" or a dash for missing data and for holdings-only criteria on non-held tickers), rather than hiding rows that don't apply?
**Default if unknown:** Always show all 6 rows. Transparency about *why* a star wasn't earned is more useful than hiding the row. For non-holdings, criterion 6 (Buybacks) and any data-missing rows show a dash + a short reason.

## Q2: Should the underlying calculation details (numbers like "EPS actual $1.50 vs estimate $1.40 → +7.1%") come from a new dedicated endpoint (e.g., `/api/stars/<ticker>/explanation`), rather than embedding them in the existing `/api/valuation/<ticker>` response?
**Default if unknown:** New dedicated endpoint. Keeps `/api/valuation/<ticker>` lean (still fast for the existing page), lets the new table lazy-load after the rest of the page renders. Most of the data is already cached server-side after the screener.

## Q3: Should the table appear directly under the existing fair-value formula on the Company Lookup page (so it reads as "here's the fair value, here's how each star was earned"), rather than at the top or as a separate collapsed section?
**Default if unknown:** Directly under the formula. Natural reading flow — value first, then quality breakdown. No collapsing — table is small (6 rows).
