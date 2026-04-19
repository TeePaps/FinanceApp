# Phase 4: Expert Requirements Questions

Five yes/no questions to pin down the exact behavior and scope now that the codebase is mapped.

---

## Q6: Should the warning have a severity tier (e.g., "recent" for splits in last ~3 years vs "historical" for older splits in the 8-year window), mirroring the `selloff_severity` pattern in `services/recommendations.py`?
**Default if unknown:** Yes — a split last quarter distorts the EPS average far more than one 7 years ago; severity tiering lets the UI render `.split-badge.recent` vs `.split-badge.historical` and matches the existing selloff pattern users are familiar with.

## Q7: Should the warning ignore trivial splits (e.g., ratios under `SPLIT_WARNING_MIN_RATIO = 1.5`) so that 1.1-for-1 reverse-split rebalances don't noise up the warnings?
**Default if unknown:** Yes — splits below ~1.5x barely affect EPS; filtering keeps the warning signal strong. Store ALL splits in `split_history` but only trigger the badge when at least one split ≥ threshold falls in the lookback window.

## Q8: Should the initial release wire up only the yfinance `SplitProvider` implementation (with the interface designed for future FMP/SEC/Alpaca providers), rather than implementing all four provider backends up front?
**Default if unknown:** Yes — the full provider interface is required (per Q3) so architecture is future-proof, but shipping one working implementation (yfinance) unblocks the feature; additional backends can be added incrementally as fallbacks when yfinance breaks.

## Q9: Should splits be persisted to a new `split_history` table (matching the `eps_history` pattern) rather than added as flag columns on the `valuations` table?
**Default if unknown:** Yes — a dedicated table preserves the full split record (dates, ratios), supports future UI that lists splits, and mirrors the existing `eps_history` pattern. The `valuations` render can derive the warning on the fly or cache a summary flag.

## Q10: Should the `/api/valuation/<ticker>/refresh` endpoint also refresh split data when called (so a user-initiated refresh on the Analyze page picks up newly announced splits), rather than splits only refreshing during the background screener?
**Default if unknown:** Yes — user expectation for a manual "refresh" is that everything on the card updates; quietly skipping splits would produce inconsistent results between manual refresh and screener.
