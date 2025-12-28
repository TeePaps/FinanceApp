# Requirements Spec: Fix Valuation Refresh Issues

## Problem Statement

The Full Update feature is not computing valuations for all tickers in active indexes. Investigation revealed multiple issues causing ~15-20% of tickers to be missing valuations.

## Root Causes Identified

### Issue 1: Phase 4 Doesn't Use Database EPS
**Severity:** High
**Impact:** Tickers with existing `eps_history` data (e.g., CAG, MELI) don't get valuations

Phase 4 of the screener only looks at the in-memory `eps_results` dict from Phase 1. If Phase 1 didn't populate a ticker (for any reason), the valuation is skipped even if `eps_history` has data in the database.

### Issue 2: Phase 1 Not Completing for All Tickers
**Severity:** High
**Impact:** 18+ tickers with valid SEC EPS data weren't fetched

The Full Update only fetched SEC data for 533 out of 2,539 tickers. Major companies like BRK-B, SHOP, MELI have valid SEC EPS but weren't processed.

### Issue 3: SEC Code Only Checks US-GAAP
**Severity:** Medium
**Impact:** 4 IFRS-reporting companies (AZN, CCEP, FER, TRI) have no valuations

Foreign companies using IFRS accounting have EPS in `ifrs-full` section, not `us-gaap`. The current code only checks `us-gaap`.

### Issue 4: No Fallback EPS Calculation
**Severity:** Medium
**Impact:** 2 companies (ARES, KKR) missing valuations

Some companies don't report `EarningsPerShareDiluted` but have `NetIncomeLoss` and `CommonStockSharesOutstanding` which can be used to calculate EPS.

## Functional Requirements

### FR-1: Database Fallback in Phase 4
**Priority:** P0 - Critical

When building valuations in Phase 4, if `eps_results` dict doesn't have data for a ticker:
1. Query `eps_history` table for that ticker
2. Calculate `eps_avg` from available years
3. Use this data to compute valuation

**Acceptance Criteria:**
- [ ] CAG gets a valuation (has 8 years in eps_history)
- [ ] Any ticker with eps_history data gets a valuation on next Full Update

### FR-2: Phase 1 Completion Logging
**Priority:** P0 - Critical

Add logging to Phase 1 to track:
1. Total tickers to process
2. Tickers attempted vs completed
3. Any errors/skips and reasons
4. Summary of SEC hits vs misses

**Acceptance Criteria:**
- [ ] Logs show "Processing ticker X of Y" progress
- [ ] Errors are logged with ticker and reason
- [ ] Summary shows: "Phase 1: 500/632 SEC hits, 132 no data"

### FR-3: Check IFRS Section for Foreign Companies
**Priority:** P1 - High

Enhance SEC provider to also check `ifrs-full` section for:
- `DilutedEarningsLossPerShare`
- `BasicEarningsLossPerShare`
- `BasicAndDilutedEarningsLossPerShare`

**Acceptance Criteria:**
- [ ] AZN gets valuation (IFRS, USD)
- [ ] TRI gets valuation (IFRS, USD)
- [ ] CCEP gets valuation (IFRS, EUR - may need currency note)
- [ ] FER gets valuation (IFRS, EUR - may need currency note)

### FR-4: Calculate EPS from Net Income / Shares
**Priority:** P1 - High

When no direct EPS field exists, attempt to calculate:
```
EPS = NetIncomeLoss / CommonStockSharesOutstanding
```

Only use this when:
1. `EarningsPerShareDiluted/Basic` not available
2. Both `NetIncomeLoss` and share count data exist

**Acceptance Criteria:**
- [ ] ARES gets valuation (calculated EPS)
- [ ] KKR gets valuation (calculated EPS)

### FR-5: Handle Non-USD EPS Gracefully
**Priority:** P2 - Medium

For companies reporting EPS in non-USD currencies (EUR, GBP, etc.):
1. Mark the valuation with currency source
2. Either skip valuation OR convert to USD (user preference)

**Acceptance Criteria:**
- [ ] ASML (EUR) is handled consistently
- [ ] CCEP (EUR) is handled consistently
- [ ] User understands why some tickers may be skipped

## Technical Requirements

### TR-1: Modify services/screener.py Phase 4
**Location:** `services/screener.py` lines 379-396

```python
# Current code
eps_info = eps_results.get(ticker) or existing_valuations.get(ticker, {})
eps_avg = eps_info.get('eps_avg')

# New code - add database fallback
eps_info = eps_results.get(ticker)
if not eps_info or not eps_info.get('eps_avg'):
    # Fallback to database
    eps_history = db.get_eps_history(ticker)
    if eps_history and len(eps_history) > 0:
        eps_avg = sum(e['eps'] for e in eps_history) / len(eps_history)
        eps_info = {
            'eps_avg': round(eps_avg, 2),
            'eps_years': len(eps_history),
            'eps_source': 'sec_cache'
        }
if not eps_info:
    eps_info = existing_valuations.get(ticker, {})
eps_avg = eps_info.get('eps_avg') if eps_info else None
```

### TR-2: Add db.get_eps_history() Function
**Location:** `database.py`

```python
def get_eps_history(ticker):
    """Get EPS history for a ticker from eps_history table."""
    conn = get_public_db()
    cursor = conn.execute(
        "SELECT year, eps FROM eps_history WHERE ticker = ? ORDER BY year DESC",
        (ticker.upper(),)
    )
    return [{'year': row[0], 'eps': row[1]} for row in cursor.fetchall()]
```

### TR-3: Enhance SEC Provider for IFRS
**Location:** `services/providers/sec_provider.py` and `sec_data.py`

Add IFRS EPS field checking after US-GAAP check:
```python
# After checking us-gaap EPS fields...
if not eps_history:
    # Check IFRS for foreign companies
    ifrs = data.get('facts', {}).get('ifrs-full', {})
    ifrs_eps_fields = [
        ('DilutedEarningsLossPerShare', 'Diluted EPS (IFRS)'),
        ('BasicEarningsLossPerShare', 'Basic EPS (IFRS)'),
        ('BasicAndDilutedEarningsLossPerShare', 'EPS (IFRS)'),
    ]
    # Extract similar to US-GAAP logic
```

### TR-4: Add Calculated EPS Fallback
**Location:** `sec_data.py`

When no direct EPS field found:
```python
# Fallback: Calculate from Net Income / Shares
if not eps_history:
    net_income = us_gaap.get('NetIncomeLoss', {}).get('units', {}).get('USD', [])
    shares = us_gaap.get('CommonStockSharesOutstanding', {}).get('units', {}).get('shares', [])
    # Match by fiscal year and calculate
```

## Acceptance Criteria Summary

After implementing all requirements:

| Index | Current | Target | Notes |
|-------|---------|--------|-------|
| dow30 | 96.7% | 97%+ | V may remain (no shares data) |
| sp500 | 97.4% | 99%+ | Most recoverable |
| nasdaq100 | 85.1% | 95%+ | IFRS companies recoverable |

**Expected remaining gaps (truly no SEC data):**
- V (Visa) - no shares outstanding in SEC
- STZ (Constellation Brands) - no shares outstanding in SEC
- SOLS (Solaris) - no financial data in SEC

## Implementation Order

1. **FR-1 + TR-1 + TR-2**: Database fallback (immediate fix for CAG, etc.)
2. **FR-2**: Add logging (diagnose Phase 1 issues)
3. **FR-3 + TR-3**: IFRS support (recover AZN, TRI, CCEP, FER)
4. **FR-4 + TR-4**: Calculated EPS (recover ARES, KKR)
5. **FR-5**: Non-USD handling (polish)

## Files to Modify

| File | Changes |
|------|---------|
| `services/screener.py` | Phase 4 database fallback, Phase 1 logging |
| `database.py` | Add `get_eps_history()` function |
| `sec_data.py` | IFRS support, calculated EPS fallback |
| `services/providers/sec_provider.py` | May need updates for IFRS |
