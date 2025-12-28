# Requirements Specification: Missing Price Data Fix

## Problem Statement

The screener displays 501 stocks but the **52-week high** and **3-month change** columns show "-" for most/all entries:
- 52-week high: 0% populated (never fetched)
- 3-month change: 79% populated (calculation bug)
- off_high_pct: 0% populated (depends on 52-week high)

## Solution Overview

### Fix 1: 3-Month Change Calculation
Update the yfinance provider to use the oldest available price in the fetched range instead of looking for a price from exactly 90 days ago.

### Fix 2: 52-Week High Data
Add a new phase to existing refresh algorithms that fetches 52-week high/low data with rate limiting to avoid API limits.

---

## Functional Requirements

### FR1: Fix Price Change Calculations
- **FR1.1:** Use the oldest available price in the fetched range for 3-month change calculation
- **FR1.2:** Apply the same logic to 1-month change calculation for consistency
- **FR1.3:** Both `fetch_price_history()` and `_process_history_dataframe()` must be updated

### FR2: Add 52-Week Data to HistoricalPriceData
- **FR2.1:** Add `fifty_two_week_high: Optional[float]` field to `HistoricalPriceData` dataclass
- **FR2.2:** Add `fifty_two_week_low: Optional[float]` field to `HistoricalPriceData` dataclass

### FR3: New Phase in Refresh Algorithms
- **FR3.1:** Add a "52-week data" phase that runs after existing phases complete
- **FR3.2:** Phase fetches stock info for each ticker to get 52-week high/low
- **FR3.3:** Include appropriate delays between API calls to respect rate limits
- **FR3.4:** Integrate into all refresh functions: `run_screener`, `run_quick_price_update`, `run_smart_update`, `run_global_refresh`

### FR4: Graceful Degradation
- **FR4.1:** If 52-week fetch fails for a stock, still save other data (prices, valuations)
- **FR4.2:** Do not retry failed 52-week fetches within the same run (rely on orchestrator retry logic)

### FR5: Calculate off_high_pct
- **FR5.1:** When 52-week high is available, calculate `off_high_pct = ((current_price - fifty_two_week_high) / fifty_two_week_high) * 100`

---

## Technical Requirements

### TR1: Files to Modify

| File | Changes |
|------|---------|
| `services/providers/base.py` | Add 52-week fields to `HistoricalPriceData` |
| `services/providers/yfinance_provider.py` | Fix price change calculation, add 52-week fetching |
| `services/screener.py` | Add 52-week phase to all refresh functions, read 52-week from provider data |

### TR2: Price Change Calculation Fix (yfinance_provider.py)

**Current (broken):**
```python
three_months_ago = datetime.now() - timedelta(days=90)
for date in hist_df.index:
    if date <= three_months_ago:
        price_3m_ago = float(hist_df.loc[date, 'Close'])
        break
```

**Required (use oldest available):**
```python
# Use oldest price in the range
if len(hist_df) > 0:
    price_3m_ago = float(hist_df['Close'].iloc[0])
```

### TR3: Locations Requiring Price Change Fix
1. `fetch_price_history()` - lines 277-292
2. `_process_history_dataframe()` - lines 476-488

### TR4: 52-Week Phase Design
- Run sequentially (not parallel) to avoid rate limits
- Add configurable delay between calls (e.g., 0.5-1 second)
- Update progress indicator to show "52-week data" phase
- Only fetch for tickers that don't already have recent 52-week data (optimization)

---

## Implementation Hints

### Pattern for 52-Week Fetch in Provider
```python
# In fetch_price_history, after getting price history:
try:
    info = stock.info
    fifty_two_week_high = info.get('fiftyTwoWeekHigh')
    fifty_two_week_low = info.get('fiftyTwoWeekLow')
except Exception:
    fifty_two_week_high = None
    fifty_two_week_low = None
```

### Pattern for New Screener Phase
```python
# After existing phases complete:
_progress['phase'] = '52-week'
for i, ticker in enumerate(tickers):
    if i > 0:
        time.sleep(0.5)  # Rate limiting
    try:
        info_result = orchestrator.fetch_stock_info(ticker)
        if info_result.success and info_result.data:
            # Update valuation with 52-week data
            ...
    except Exception:
        continue  # Graceful degradation
```

---

## Acceptance Criteria

1. [ ] After running a full screener refresh, >95% of stocks have 52-week high populated
2. [ ] After running a full screener refresh, >95% of stocks have 3-month change populated
3. [ ] off_high_pct is calculated and displayed for stocks with 52-week high data
4. [ ] Refresh process does not hit API rate limits (no errors during normal operation)
5. [ ] Stocks that fail 52-week fetch still show price and valuation data
6. [ ] All refresh functions (run_screener, run_quick_price_update, run_smart_update, run_global_refresh) include the fix

---

## Out of Scope

- Company name fixes (separate issue to investigate)
- Parallel 52-week fetching (rejected due to rate limit concerns)
- Custom retry logic for 52-week fetches (use existing orchestrator logic)
