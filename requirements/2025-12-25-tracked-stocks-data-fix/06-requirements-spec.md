# Requirements Specification: Fix Tracked Stocks Data Display

## Problem Statement

The "All Tracked Stocks" table on the Data Sets page shows 144 tickers with only price data populated. The EPS-related fields (EPS Avg, EPS Years, Source, Est Value, vs Value) display as "-" even though SEC EPS data exists in the database for these tickers.

**Root Cause:** The Quick Update (`run_quick_price_update`) only copies EPS data from existing valuations. When a ticker has no existing valuation with EPS data, the Quick Update creates a valuation with price only, ignoring cached SEC data in the `sec_companies` table.

## Solution Overview

Modify the Quick Update function to check the `sec_companies` table for cached SEC EPS data when a ticker's existing valuation lacks `eps_avg`. This uses already-cached data without making additional API calls, keeping Quick Update fast while ensuring complete valuations.

## Functional Requirements

### FR1: Use Cached SEC Data in Quick Update
When building valuations in `run_quick_price_update()`:
- If `existing.get('eps_avg')` is None or missing
- Check `sec_companies` table for cached EPS history for that ticker
- If SEC data exists, calculate `eps_avg` from the cached `eps_history`
- Populate `eps_years`, `eps_source`, `company_name` from SEC data
- Calculate `estimated_value` and `price_vs_value` using the formula

### FR2: Maintain Quick Update Performance
- Only read from local database (no API calls)
- Use existing `db.get_sec_company(ticker)` function
- Process SEC data lookup inline during valuation building loop

### FR3: Automatic Fix for Existing Data
- No separate repair action needed
- Existing 144 tickers will be fixed on next Quick Update run
- Future tickers will be handled correctly from the start

## Technical Requirements

### TR1: File to Modify
`services/screener.py` - `run_quick_price_update()` function (lines 551-625)

### TR2: Database Function to Use
`database.py` - `get_sec_company(ticker)` returns:
```python
{
    'ticker': 'AAPL',
    'cik': '0000320193',
    'company_name': 'Apple Inc.',
    'sec_no_eps': False,
    'eps_history': [
        {'year': 2024, 'eps': 6.08, ...},
        {'year': 2023, 'eps': 6.13, ...},
        ...
    ]
}
```

### TR3: Implementation Location
In the valuation building loop (around line 560-625), after checking `existing.get('eps_avg')`:

```python
# Current code gets eps_avg from existing valuations
eps_avg = existing.get('eps_avg')

# NEW: If no eps_avg, check SEC cache
if eps_avg is None:
    sec_data = db.get_sec_company(ticker)
    if sec_data and sec_data.get('eps_history'):
        eps_history = sec_data['eps_history']
        if len(eps_history) > 0:
            eps_avg = sum(e['eps'] for e in eps_history) / len(eps_history)
            eps_years = len(eps_history)
            eps_source = 'sec'
            if sec_data.get('company_name') and sec_data['company_name'] != ticker:
                company_name = sec_data['company_name']
```

### TR4: Fields to Populate from SEC Cache
| Field | Source |
|-------|--------|
| `eps_avg` | Calculate: `sum(eps) / len(eps_history)` |
| `eps_years` | `len(eps_history)` |
| `eps_source` | `'sec'` |
| `company_name` | `sec_data['company_name']` (if not ticker) |
| `estimated_value` | Calculate: `(eps_avg + annual_dividend) * 10` |
| `price_vs_value` | Calculate: `((price - value) / value) * 100` |

## Implementation Hints

### Pattern to Follow
The `run_global_refresh()` function (lines 960-967) already has similar logic:
```python
sec_result = orchestrator.fetch_eps(ticker)
if sec_result.success and sec_result.data and sec_result.data.eps_history:
    eps_history = sec_result.data.eps_history
    if len(eps_history) > 0:
        eps_avg = sum(e['eps'] for e in eps_history) / len(eps_history)
        eps_years = len(eps_history)
        eps_source = 'sec'
```

But instead of calling `orchestrator.fetch_eps()` (which may hit API), use `db.get_sec_company()` to read from cache.

### Existing Code Reference
The current valuation building in `run_quick_price_update()` (lines 564-622):
```python
existing = existing_valuations.get(ticker, {})
eps_avg = existing.get('eps_avg')
annual_dividend = existing.get('annual_dividend', 0)
# ... rest of valuation building
```

## Acceptance Criteria

1. **AC1:** After running Quick Update, tickers with cached SEC data should show EPS Avg, EPS Years, Source, Est Value, and vs Value in the "All Tracked Stocks" table
2. **AC2:** Quick Update performance remains fast (no external API calls added)
3. **AC3:** Tickers without SEC data (sec_no_eps=True) continue to show "-" for EPS fields
4. **AC4:** The 144 currently affected tickers are fixed after one Quick Update run

## Verification Steps

After implementation:
1. Run Quick Update from the UI (Refresh → All Prices)
2. Navigate to Data Sets → All Tracked Stocks
3. Verify previously affected tickers (TSLA, AAPL, CSCO, etc.) now show EPS data
4. Filter by "Source: SEC EPS" to confirm SEC-sourced valuations

## Assumptions

1. SEC data in `sec_companies` table is current and valid
2. The `db.get_sec_company()` function is performant for inline use
3. No changes needed to frontend - it already handles the data correctly when present
