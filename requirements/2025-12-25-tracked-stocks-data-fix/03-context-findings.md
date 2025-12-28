# Context Findings

## Root Cause Analysis

### The Problem
144 tickers in the "All Tracked Stocks" table have price data but no EPS-related fields (EPS Avg, EPS Years, Source, Est Value, vs Value are all "-").

### Key Discovery
**The SEC EPS data EXISTS** in the `sec_companies` table for affected tickers (TSLA, AAPL, CSCO, etc. all have full EPS history stored). However, the `valuations` table is missing the calculated values.

### How This Happened
The disconnect occurs between different update types:

| Update Type | Price | SEC EPS | Dividends | Valuations |
|-------------|-------|---------|-----------|------------|
| Quick Update (All Prices) | ✅ | ❌ | ❌ | Reuses existing only |
| Smart Update | ✅ | For missing only | ❌ | Partial |
| Full Update | ✅ | ✅ | ✅ | ✅ |
| Global Refresh (/api/refresh) | ✅ | ✅ | ❌ | ✅ |

When a "Quick Update" or "All Prices" update runs:
1. It creates/updates valuation rows with price data
2. It copies eps_avg from existing valuations (`existing.get('eps_avg')`)
3. **If no existing valuation exists, eps_avg stays None**
4. The SEC data in sec_companies is never consulted

## Files Involved

### Backend Flow
1. `services/screener.py` - Contains all update functions:
   - `run_screener()` (lines 119-460) - Full update, fetches SEC EPS
   - `run_quick_price_update()` (lines 465-639) - Price only, reuses existing EPS
   - `run_smart_update()` (lines 642-823) - Fetches missing EPS data
   - `run_global_refresh()` (lines 826-1050) - Fetches SEC EPS

2. `app.py` (lines 382-416) - `/api/all-tickers` endpoint
   - Reads from `data_manager.load_valuations()`
   - Returns valuation data including eps_avg, estimated_value, etc.

3. `database.py`:
   - `valuations` table (lines 137-157) - Stores calculated valuations
   - `sec_companies` table (lines 161-169) - Stores SEC EPS history

4. `data_manager.py`:
   - `load_valuations()` (lines 115-135) - Reads from valuations table

### Frontend
- `static/app.js`:
  - `renderAllTickersTable()` (lines 3751-3926) - Renders the table
  - Shows "-" for null values (correct behavior)

## Data State Evidence

```
Total valuations: 2505
Tickers with price but no EPS: 144

Example: TSLA
- valuations table: current_price=$485.40, eps_avg=None, eps_source=None
- sec_companies table: Has 8 years of EPS history (2017-2024)
```

## Similar Features Analyzed

The "Smart Update" (`run_smart_update`) already addresses this partially:
- It identifies tickers missing valuation data
- Fetches EPS for those missing tickers
- But only runs when explicitly triggered

## Technical Constraints

1. SEC API rate limit: 10 requests/second
2. Price-only updates are designed to be fast (seconds vs minutes for full update)
3. Existing architecture correctly separates SEC data storage from valuation calculations

## Solution Options

### Option A: Enhance Quick Update
Modify `run_quick_price_update()` to check SEC data for tickers without eps_avg and calculate valuations.

**Pros:** Fixes issue automatically, transparent to user
**Cons:** Makes "quick" update slower, may hit SEC API rate limits

### Option B: Always Calculate from SEC Cache
When building valuations, if eps_avg is missing but SEC data exists in sec_companies, calculate and use it.

**Pros:** Uses existing cached data, no API calls needed
**Cons:** Requires code changes in valuation building logic

### Option C: Run Full Update
User runs "Full Update" to recalculate all valuations from SEC data.

**Pros:** No code changes needed
**Cons:** Takes longer, user must know to do this

### Recommended: Option B
The SEC data already exists in the database. The Quick Update should use cached SEC data when existing valuations lack EPS data, without making additional API calls.
