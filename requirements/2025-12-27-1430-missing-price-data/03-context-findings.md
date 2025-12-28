# Context Findings

## Files That Need Modification

### 1. services/providers/base.py (lines 87-98)
**Current State:** `HistoricalPriceData` dataclass has price change fields but no 52-week fields
```python
@dataclass
class HistoricalPriceData:
    ticker: str
    source: str
    current_price: float
    prices: Dict[str, float]
    price_1m_ago: Optional[float] = None
    price_3m_ago: Optional[float] = None
    change_1m_pct: Optional[float] = None
    change_3m_pct: Optional[float] = None
    timestamp: datetime = field(default_factory=datetime.now)
```

**Required Change:** Add `fifty_two_week_high` and `fifty_two_week_low` fields

### 2. services/providers/yfinance_provider.py

#### Bug in 3m Change Calculation (lines 286-292, 483-488)
**Current Logic:** Searches for a date `<= 90 days ago`, fails if data doesn't go back that far
```python
three_months_ago = datetime.now() - timedelta(days=90)
for date in hist_df.index:
    if date <= three_months_ago:
        price_3m_ago = float(hist_df.loc[date, 'Close'])
        break
```

**Problem:** When fetching 3-month data, the oldest date is often only 88-89 days old (e.g., Sept 29 for Dec 27), so no match is found.

**Required Fix:** Use the oldest available price in the fetched range:
```python
# Use first (oldest) price in the range
price_3m_ago = float(hist_df['Close'].iloc[0])
```

#### Missing 52-Week Data (fetch_price_history methods)
**Current State:**
- `fetch_price_history()` (line 246) - Only fetches price data, no 52-week info
- `_process_history_dataframe()` (line 450) - Same issue
- `fetch_price_history_batch()` (line 326) - Uses `yf.download()` which doesn't include 52-week data

**Required Change:** After fetching price history, also fetch stock info to get 52-week high/low:
- For single fetch: Call `stock.info` in `fetch_price_history()`
- For batch fetch: Need to add concurrent info fetches after the batch download

#### Reference Implementation - fetch_stock_info() (lines 522-579)
This method already correctly fetches 52-week data:
```python
fifty_two_week_high = info.get('fiftyTwoWeekHigh')
fifty_two_week_low = info.get('fiftyTwoWeekLow')
```

### 3. services/screener.py (lines 336-386, 450-465)

**Current State:** Screener reads 52-week data from `info_cache` which is populated from existing database values only (not freshly fetched):
```python
info = info_cache.get(ticker, {})
fifty_two_week_high = info.get('fiftyTwoWeekHigh', 0)
```

**Required Change:** Read 52-week data from `HistoricalPriceData` returned by provider:
```python
if hist_data.fifty_two_week_high is not None:
    fifty_two_week_high = hist_data.fifty_two_week_high
```

## Technical Constraints

1. **yf.download() limitation:** Batch download only returns OHLCV data, not stock info. Must use `yf.Ticker(symbol).info` for 52-week data.

2. **Performance consideration:** Adding info fetches to batch operations will slow them down. Options:
   - Sequential: Simple but slow
   - Concurrent (ThreadPoolExecutor): Faster but more complex
   - User accepted slower refreshes for complete data

3. **Two places with same logic:** Both `fetch_price_history()` and `_process_history_dataframe()` have the same 3m calculation bug - both need fixing.

## Patterns to Follow

1. **Optional fields in dataclass:** Use `Optional[float] = None` pattern (already used for other fields)
2. **Graceful degradation:** If 52-week fetch fails, still return price data (user approved partial data)
3. **Provider result pattern:** Always return `ProviderResult` with success/error info

## Integration Points

1. `base.py` → `yfinance_provider.py` → `screener.py` → `database.py`
2. All screener functions (`run_screener`, `run_quick_price_update`, `run_smart_update`, `run_global_refresh`) use the same data flow pattern
