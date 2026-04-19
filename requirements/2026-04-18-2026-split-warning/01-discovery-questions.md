# Phase 2: Discovery Questions

Five yes/no questions to scope the Split Warning feature.

---

## Q1: Should the warning be purely informational (a visible badge/note), rather than actually adjusting the calculated fair value?
**Default if unknown:** Yes — ship a visible warning first; auto-adjusting split-skewed EPS is a larger, more error-prone effort best done as a follow-on.

## Q2: Should the warning trigger based on splits within a configurable lookback window that spans the EPS averaging period (e.g., any split in the last ~8 years, since EPS uses 8-year averages)?
**Default if unknown:** Yes — a split anywhere in the averaging window distorts the average, so the lookback should match `RECOMMENDED_EPS_YEARS` (8 years), not just the last year.

## Q3: Is it acceptable for split data to come from a single provider (e.g., yfinance) rather than requiring the same multi-provider fallback chain used for prices/EPS?
**Default if unknown:** Yes — split data is low-frequency and yfinance exposes it reliably; a full provider interface can be added later if yfinance proves fragile.

## Q4: Should stocks with an active Split Warning still appear in the Recommendations list (just flagged), rather than being filtered out entirely?
**Default if unknown:** Yes — excluding them silently would hide opportunities and surprise the user; flag-and-show preserves transparency.

## Q5: Should split data be persisted to the database (so it doesn't need re-fetching on every page load) and refreshed as part of the existing screener run?
**Default if unknown:** Yes — matches the existing pattern (EPS, dividends, prices all persist in `public.db` and refresh via the screener); avoids adding live API calls to page renders.
