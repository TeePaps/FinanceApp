# Phase 2: Discovery Questions

The bug is well-understood. The questions are about fix scope.

---

## Q1: Should we fix the underlying cached data (so ALL pages — Stars, Recommendations, Holdings, screener — see consistent fair values), rather than only patching the Stars API?
**Default if unknown:** Fix underlying data. The bug is in the valuations table — patching only the Stars API would create inconsistencies between tabs and leaves Recommendations/Holdings showing stale numbers.

## Q2: Should we make the screener self-healing for future runs (fallback to `eps_history` table when fresh SEC fetch fails, rather than reusing the prior cached valuation), in addition to a one-shot backfill for current stale rows?
**Default if unknown:** Yes — both. Without the fallback fix, the same drift returns next time SEC has a transient failure. The one-shot backfill rescues current state; the fallback fix prevents recurrence.

## Q3: Should the dividend refresh in the screener also be more aggressive (currently skips tickers with any non-stale cached dividend — PGR shows $4.90 cached vs $13.90 actual)?
**Default if unknown:** Yes — refresh dividends whenever the screener runs a full update, not just when missing/stale. Dividends change too often to trust caches for the canonical "fair value" calculation.
