# Phase 4: Detail Answers

## Q6: Severity tiers (`recent` vs `historical`)?
**Answer:** Yes (default)
**Implication:** Two tiers. `recent` = split within last 3 years; `historical` = split in years 3–8. Badge CSS mirrors `.selloff-badge.severe/.moderate` pattern.

## Q7: Ignore trivial splits below a minimum ratio?
**Answer:** Yes, but **very conservative threshold**
**Implication:** Use `SPLIT_WARNING_MIN_RATIO = 1.1` (filters only extreme rebalances like 1.05:1; 1.1:1 and anything larger still warns). Store ALL splits in `split_history` regardless; only the badge-trigger logic applies the threshold.

## Q8: Ship only yfinance provider, or all four (yfinance + FMP + Alpaca + SEC)?
**Answer:** All four
**Implication:** Implement `YFinanceSplitProvider`, `FMPSplitProvider`, `AlpacaSplitProvider`, and `SECSplitProvider`. Order in config: `["yfinance", "fmp", "alpaca", "sec"]`.
**Caveat:** SEC EDGAR does not expose splits via the standard `companyfacts` XBRL API. The SEC implementation is best-effort (parse 8-K filings or return empty). Document clearly that it is a fallback-of-last-resort.

## Q9: Persist to dedicated `split_history` table (not flag columns on `valuations`)?
**Answer:** Yes
**Implication:** New table with full date+ratio history; UNIQUE(ticker, split_date); `ON DELETE CASCADE` from `tickers`.

## Q10: `/api/valuation/<ticker>/refresh` also refreshes splits?
**Answer:** Yes
**Implication:** Refresh endpoint calls `orchestrator.fetch_splits(ticker)`, upserts `split_history`, and recomputes `split_warning` for the response. Consistent with how it already refreshes price/EPS/dividends.
