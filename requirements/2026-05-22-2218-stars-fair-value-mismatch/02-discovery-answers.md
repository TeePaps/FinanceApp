# Phase 2: Discovery Answers

## Q1: Fix scope?
**Answer:** Fix the cached data in the `valuations` table — all pages benefit.

**Implication:** Backfill script that rewrites `valuations.estimated_value` (and
its inputs) for any ticker where the cached value diverges from what the
canonical SEC data + fresh dividends would produce. Stars/Recommendations/
Holdings all read from this table and will see correct numbers without changes.

---

## Q2: Make screener self-healing?
**Answer:** Yes — fix screener fallback too.

**Implication:** When Phase 1's `orchestrator.fetch_eps(ticker)` fails, check
the local `eps_history` table BEFORE falling back to the prior valuation row
or yfinance. This way a one-off SEC outage doesn't permanently overwrite a
ticker's good SEC data with stale yfinance values.

---

## Q3: Dividend refresh?
**Answer:** Yes — always refresh on full screener runs.

**Implication:** Drop the `needs_dividend_update()` skip-when-cached logic for
the full screener. Quick price updates can keep the skip (they're meant to be
fast). Full screener gets fresh dividends every time.
