# Phase 3: Context Findings

## Files to Modify

| File | Change |
|---|---|
| `services/screener.py:175-216` (Phase 1) | When SEC fetch fails, check `eps_history` table BEFORE falling back to prior valuation |
| `services/screener.py:226-280` (Phase 2) | Remove the cache-aware skip for full screener runs (always refresh dividends) |
| `services/screener.py:381-518` (Phase 4) | After building valuation_batch, also recompute `eps_avg` from `eps_history` if cached value is stale |
| `scripts/backfill_valuations.py` (NEW) | One-shot script to fix the 294 currently-divergent tickers |

## Code Snippets

### Phase 1 fallback chain (lines 188-211)
Current:
```python
sec_result = orchestrator.fetch_eps(t)
if sec_result.success and sec_result.data and sec_result.data.eps_history:
    eps_history = sec_result.data.eps_history
    # ... use SEC data, continue
# Fall back to existing valuations
existing = existing_valuations.get(t, {})
if existing.get('eps_avg') is not None:
    eps_results[t] = existing
```

Proposed:
```python
sec_result = orchestrator.fetch_eps(t)
if sec_result.success and sec_result.data and sec_result.data.eps_history:
    # ... use SEC data (unchanged)
    continue

# Fallback 1: check eps_history table (prior SEC fetch is still authoritative)
cached_history = db.get_eps_history(t)
if cached_history:
    # Use up to 8 most recent years
    use = cached_history[:8]
    eps_avg = sum(h['eps'] for h in use) / len(use)
    eps_results[t] = {
        'ticker': t,
        'company_name': existing_valuations.get(t, {}).get('company_name') or t,
        'eps_avg': round(eps_avg, 2),
        'eps_years': len(use),
        'eps_source': 'sec_cache',
        'has_enough_years': len(use) >= 8,
        'annual_dividend': existing_valuations.get(t, {}).get('annual_dividend', 0),
    }
    sec_hits += 1
    continue

# Fallback 2: existing valuation (last resort)
existing = existing_valuations.get(t, {})
if existing.get('eps_avg') is not None:
    eps_results[t] = existing
    existing_hits += 1
else:
    sec_failures += 1
```

### Phase 2 dividend always-refresh
Current `needs_dividend_update()` keeps cache when fresh. Change `run_screener`
to always include all tickers (remove the filter) — but keep the function for
`run_quick_price_update()` if it's used there too.

### Phase 4 — also reuse eps_history when re-building
Existing code at line 446-456 already has a fallback to `db.get_eps_history(ticker)`
when `eps_avg` is missing. Extend this: if cached eps_avg uses `<8 years` AND
`eps_history` has more, prefer the eps_history version. This makes Phase 4
self-correct for any tickers where Phase 1 SEC succeeded but a prior bad value
got reused.

## Why a backfill script is needed
The screener changes only fix new screener runs. Existing 294 stale rows need
a one-shot recompute. The script:
1. For each ticker in `valuations`:
   - Read `eps_history` table.
   - If it has ≥1 row, compute `eps_avg` from up to 8 most recent years.
   - Refresh dividends via `orchestrator.fetch_dividends(ticker)`.
   - Recompute `estimated_value = (eps_avg + annual_dividend) * 10`.
   - Update the valuations row (only the affected fields).
2. Report counts: examined / unchanged / corrected.

Single ticker per ~0.5s (rate-limited dividends) → ~12 minutes for 1483 tickers.

## Why NOT to touch the Stars/Recommendations API endpoints
Once the cache is correct, all reads are correct. Keeping the API endpoints
unchanged means a small change footprint and no cross-page consistency risk.
