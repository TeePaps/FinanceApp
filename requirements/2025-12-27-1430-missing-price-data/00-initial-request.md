# Initial Request

## User Request
Why am I missing data for most/all stocks on these two fields: 52-week high and 3-month change?

The screener shows 501 stocks but the 52w high and 3m change columns show "-" for most/all entries.

Example row:
```
CAG    CAG    $17.24    $31.80    -45.8%    $1.40    -    -    OK    1d
```

## Initial Investigation Findings

### Database Analysis
- Total valuations: 2539
- Has 52-week high: 0 (0%)
- Has 3m change: 1997 (79%)
- Has 1m change: 2499 (98%)
- Has off_high_pct: 0 (0%)

### Root Causes Identified

**52-Week High (never populated):**
- The screener/refresh algorithms call `fetch_price_history_batch()` but never call `fetch_stock_info()` which returns 52-week data
- The `HistoricalPriceData` dataclass doesn't include 52-week fields
- The data exists in Yahoo Finance and can be fetched via `fetch_stock_info()`

**3-Month Change (partially populated - 79%):**
- The calculation in `yfinance_provider.py` looks for a price from exactly 90 days ago
- When fetching 3 months of data, the oldest price may only be 88-89 days old
- Should use the oldest price in the fetched range, not look for exactly 90 days ago

## Proposed Solution
Add 52-week high/low to `HistoricalPriceData` and have the yfinance provider fetch it alongside price history, plus fix the 3m change calculation logic.
