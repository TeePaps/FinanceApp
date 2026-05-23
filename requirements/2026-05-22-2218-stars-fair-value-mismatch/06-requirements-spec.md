# Requirements Spec: Fix Stars/Cache Fair-Value Drift

**Date:** 2026-05-22
**Requirement ID:** 2026-05-22-2218-stars-fair-value-mismatch
**Status:** Ready for implementation

---

## 1. Problem Statement

The Stars tab (and Recommendations, Holdings, screener results) shows
incorrect fair values for ~half of all tracked tickers because the cached
`valuations.estimated_value` was computed from stale yfinance EPS (4 years)
and/or stale dividends — not the authoritative SEC 8-year data already
present in the `eps_history` table.

For PGR specifically: cache shows $152.50 (using yfinance 4-yr EPS + an old
$4.90 dividend); fresh calculation via Company Lookup shows $223.81 (SEC
8-yr EPS + current $13.90 dividend). ~294 tickers are affected at this
magnitude across the database.

## 2. Solution Overview

Three changes, all backend-only:

1. **Smarter screener Phase 1 fallback chain**: `fresh SEC → eps_history table → prior valuation`. Prevents future drift when SEC has transient failures.
2. **Always refresh dividends in the full screener**: drop the cache-skip in Phase 2 for `run_screener()` (keep it for `run_quick_price_update()`).
3. **One-shot backfill script**: rewrites `valuations.estimated_value` (and inputs) for any ticker where `eps_history` has more/different data than the cached value reflects.

No UI changes. No schema changes. No API changes.

## 3. Functional Requirements

### 3.1 Screener Phase 1 fallback chain
When `orchestrator.fetch_eps(ticker)` fails or returns no data:
1. Check `db.get_eps_history(ticker)`. If ≥1 row, compute `eps_avg` from up
   to 8 most recent years; set `eps_source = 'sec_cache'`; treat as success.
2. Only if eps_history is also empty, fall back to the prior cached
   valuation as today.

### 3.2 Full screener dividend refresh
In `run_screener()`, refresh dividends for every ticker (skip the
`needs_dividend_update()` filter). Leave `run_quick_price_update()` and
`run_smart_update()` unchanged.

### 3.3 Backfill script
`scripts/backfill_valuations.py` (or a one-shot `--backfill-stars` flag in
an existing admin route — see Q in Section 6). For each ticker in the
`valuations` table:
- Look up `eps_history`. If empty, skip.
- Compute `eps_avg` from up to 8 most recent years.
- Fetch fresh dividend via orchestrator (rate-limited).
- Compute `estimated_value = (eps_avg + annual_dividend) * 10`.
- If any of `eps_avg`, `eps_years`, `eps_source='sec_cache'`,
  `annual_dividend`, `estimated_value`, `price_vs_value` differ from cache,
  update the valuations row.
- Recompute and update `price_vs_value` from current_price.
- Print final summary: examined / updated / unchanged / skipped.

## 4. Technical Requirements

### 4.1 `services/screener.py`
- Modify Phase 1 loop (lines 181-212) to add the eps_history fallback.
- Modify Phase 2 (line 237: `tickers_needing_dividends = ...`) to use
  `tickers_needing_dividends = list(tickers)` when called from `run_screener`,
  OR — simpler — gate the existing filter behind an arg/flag.

### 4.2 `scripts/backfill_valuations.py` (NEW)
```python
"""
One-shot: recompute valuations.estimated_value for every ticker where
eps_history (authoritative SEC data) diverges from the cached eps_avg.

Run:
    ./venv/bin/python scripts/backfill_valuations.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database as db
from services.providers import init_providers, get_orchestrator
from config import PE_RATIO_MULTIPLIER

def main():
    init_providers()
    orch = get_orchestrator()
    all_v = db.get_all_valuations()
    examined = updated = unchanged = skipped = 0
    for ticker, row in all_v.items():
        examined += 1
        hist = db.get_eps_history(ticker)
        if not hist:
            skipped += 1
            continue
        use = hist[:8]
        eps_avg = round(sum(h['eps'] for h in use) / len(use), 2)
        # Fresh dividends
        div_result = orch.fetch_dividends(ticker)
        annual_div = (div_result.data.annual_dividend
                      if div_result.success and div_result.data else
                      row.get('annual_dividend') or 0)
        annual_div = round(annual_div, 2)
        estimated_value = round((eps_avg + annual_div) * PE_RATIO_MULTIPLIER, 2)
        cp = row.get('current_price')
        pvv = round(((cp - estimated_value) / estimated_value) * 100, 1) if cp and estimated_value else None

        if (eps_avg == row.get('eps_avg')
            and annual_div == row.get('annual_dividend')
            and estimated_value == row.get('estimated_value')):
            unchanged += 1
            continue

        db.bulk_update_valuations({ticker: {
            **row,
            'eps_avg': eps_avg,
            'eps_years': len(use),
            'eps_source': 'sec_cache',
            'annual_dividend': annual_div,
            'estimated_value': estimated_value,
            'price_vs_value': pvv,
        }})
        updated += 1
        if updated % 25 == 0:
            print(f'  ... {updated} updated so far ({examined}/{len(all_v)})')
    print(f'\nDone. examined={examined} updated={updated} unchanged={unchanged} skipped={skipped}')

if __name__ == '__main__':
    main()
```

### 4.3 Recompute Stars after backfill
After the backfill changes `estimated_value`, the cached
`star_ratings.undervalued` flag may be wrong (criterion 5 depends on
`current_price < estimated_value`). Run
`calculate_all_star_ratings()` once after the backfill — same one-shot we
ran earlier.

## 5. Acceptance Criteria

1. **PGR specifically**: after backfill, `db.get_valuation('PGR')['estimated_value']` is close to $223 (matches the Company Lookup fresh calc within rounding).
2. **Audit query**: the SQL/python check that counted 294 divergent tickers now returns 0 (or ≤ some small number from tickers without eps_history).
3. **Stars tab**: refresh `/api/stars` — PGR shows fair value ~$223 (matches Company Lookup), and the **Undervalued** star is correctly earned (price $199.51 < $223).
4. **Screener regression test**: running `run_screener()` on a small index (e.g., dow30) doesn't reintroduce divergence — post-run `eps_source` should be `sec_edgar` or `sec_cache` (not `yfinance`) for tickers that have SEC data.
5. **No UI changes** required. All tabs (Stars, Recommendations, Holdings) show the corrected values automatically because they read from the same cache.

## 6. Assumptions / Open Items

- **A**: The script + screener changes don't touch `eps_source = 'yfinance'`
  for tickers where `eps_history` is empty (legitimately no SEC data — e.g.,
  foreign issuers). Those stay on yfinance.
- **B**: Backfill runs as a one-shot CLI script, not as a Flask endpoint —
  keeps surface area minimal. A future admin button could be added if needed.
- **C**: The dividend refresh in the backfill is rate-limited per provider
  (yfinance ~0.2s per call). ~1483 tickers → ~5-8 minutes.
- **D**: If `current_price` is null in the cached row, `price_vs_value` is
  left null (don't recompute without a price).
