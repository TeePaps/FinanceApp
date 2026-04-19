# Phase 2: Discovery Answers

## Q1: Should the warning be purely informational (a visible badge/note), rather than actually adjusting the calculated fair value?
**Answer:** Yes (default)
**Implication:** Ship a visible badge/note only. No auto-adjustment of EPS or fair value in this iteration.

## Q2: Should the warning trigger based on splits within a configurable lookback window that spans the EPS averaging period (~8 years)?
**Answer:** Yes (default)
**Implication:** Lookback window tied to `RECOMMENDED_EPS_YEARS` (8 years). Any split in that window triggers the warning.

## Q3: Is it acceptable for split data to come from a single provider (e.g., yfinance) rather than a multi-provider fallback chain?
**Answer:** No — build a full provider interface
**Implication:** Add a `SplitProvider` interface to `services/providers/base.py`, register it in the orchestrator, and wire multiple provider implementations (yfinance first, with room for FMP/Alpaca/SEC fallbacks). Matches the existing pattern for PRICE / EPS / DIVIDEND.

## Q4: Should stocks with an active Split Warning still appear in the Recommendations list (flagged, not filtered)?
**Answer:** Yes
**Implication:** Do not filter. Display a visible badge on the recommendation card. Do not let the warning change the score (at least not in this iteration — revisit if needed).

## Q5: Should split data be persisted to the database and refreshed as part of the existing screener run?
**Answer:** Yes (default)
**Implication:** Add schema (table or columns) for split data. Add a screener phase (or piggyback on an existing one) to refresh splits. Page renders read from the DB.
