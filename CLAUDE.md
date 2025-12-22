# Claude Code Guide for FinanceApp

## Project Overview

A stock portfolio valuation app that fetches market data from multiple providers, calculates fair values, and recommends undervalued stocks. Two SQLite databases separate public market data from private holdings.

## Key Concepts

- **Fair Value**: `(8-year avg EPS + annual dividend) × 10`
- **Provider System**: Pluggable data sources with fallback chains
- **FIFO Cost Basis**: Sells match against oldest buy lots first
- **Circuit Breaker**: Skip failing providers temporarily

## Directory Structure

```
FinanceApp/
├── app.py                 # Flask app, 40+ routes (LARGE FILE - 2000+ lines)
├── database.py            # SQLite CRUD for both databases
├── config.py              # All constants and thresholds
├── sec_data.py            # SEC EDGAR API integration
├── data_manager.py        # High-level data operations
│
├── services/
│   ├── providers/         # *** MARKET DATA PROVIDER SYSTEM ***
│   │   ├── base.py        # Interfaces: PriceProvider, EPSProvider, etc.
│   │   ├── registry.py    # DataOrchestrator - coordinates fetching
│   │   ├── config.py      # Provider ordering, timeouts, circuit breaker
│   │   ├── circuit_breaker.py
│   │   ├── secrets.py     # API keys (stored in data_private/)
│   │   ├── yfinance_provider.py
│   │   ├── alpaca_provider.py
│   │   ├── fmp_provider.py
│   │   ├── sec_provider.py
│   │   └── defeatbeta_provider.py
│   │
│   ├── indexes/           # *** INDEX PROVIDER SYSTEM ***
│   │   ├── providers.py   # Index constituent providers (Wikipedia, iShares, etc.)
│   │   └── registry.py    # Index definitions and central registry
│   │
│   ├── screener.py        # Background batch processing (partial refactor)
│   ├── valuation.py       # Fair value calculations
│   ├── recommendations.py # Scoring algorithm
│   ├── holdings.py        # FIFO cost basis calculations
│   └── stock_utils.py     # Stock data utilities (uses provider system)
│
├── routes/                # Flask blueprints (registered via register_blueprints())
│   └── *.py               # API route handlers
│
├── data_public/           # Market data (public.db, can be rebuilt)
├── data_private/          # User data (private.db, API keys, BACKUP THIS)
└── static/                # Frontend (app.js is main SPA)
```

## Databases

**public.db** (data_public/) - Rebuildable market data:
- `tickers` - Company info, CIK, SEC status
- `valuations` - Prices, EPS averages, fair values
- `eps_history` - Annual EPS by year
- `indexes`, `ticker_indexes` - Index membership

**private.db** (data_private/) - User data (PROTECT THIS):
- `stocks` - User's tracked stocks
- `transactions` - Buy/sell history

## Provider System Architecture

### Data Types (base.py)
```python
DataType.PRICE          # Current price
DataType.PRICE_HISTORY  # Historical prices with changes
DataType.EPS            # Earnings per share history
DataType.DIVIDEND       # Dividend data
```

### Provider Interfaces (base.py)
Each provider implements one or more interfaces:
- `PriceProvider` → `fetch_price()`, `fetch_prices()`
- `HistoricalPriceProvider` → `fetch_price_history()`, `fetch_price_history_batch()`
- `EPSProvider` → `fetch_eps()`
- `DividendProvider` → `fetch_dividends()`

### Key Properties
- `name` - Unique identifier (e.g., "yfinance")
- `is_realtime` - True for live data, False for historical-only
- `supports_batch` - Can fetch multiple tickers at once
- `is_available()` - Check if dependencies/API keys present

### Orchestrator Flow (registry.py)
```
Request → Check Cache → Get Ordered Providers → For Each:
    → Check Circuit Breaker (skip if open)
    → Apply Rate Limit
    → Execute with Timeout
    → Record Success/Failure
    → Return on Success or Try Next
```

## Files That Change Together

### Adding a New Provider
1. `services/providers/base.py` - Add interface if new data type
2. `services/providers/{name}_provider.py` - Create provider class
3. `services/providers/registry.py` - Register in `init_providers()`
4. `services/providers/__init__.py` - Export classes
5. `services/providers/config.py` - Add to default provider order

### Adding a New Data Type
1. `services/providers/base.py` - Add to `DataType` enum, create interface
2. `services/providers/registry.py`:
   - Add to `_by_type` dict in `ProviderRegistry.__init__`
   - Add `fetch_xxx()` method in `DataOrchestrator`
   - Add to `get_providers_ordered()` switch
3. `services/providers/config.py` - Add `xxx_providers` list
4. `services/providers/__init__.py` - Export new types

### Modifying Circuit Breaker / Timeout
1. `services/providers/config.py` - Settings
2. `services/providers/circuit_breaker.py` - Logic
3. `services/providers/registry.py` - Usage in fetch methods

### Index System (services/indexes/)
The index system follows the same provider pattern as market data:
- `services/indexes/providers.py` - Index constituent providers (Wikipedia, Slickcharts, iShares, GitHub)
- `services/indexes/registry.py` - Index definitions and IndexRegistry class

Usage:
```python
from services.indexes import VALID_INDICES, INDEX_NAMES, fetch_index_tickers

# Get list of tickers in an index
tickers = fetch_index_tickers('sp500')
```

## Common Patterns

### Fetching Data (Correct Way)
```python
from services.providers import get_orchestrator

orchestrator = get_orchestrator()
result = orchestrator.fetch_price("AAPL")
if result.success:
    price = result.data
    source = result.source
```

### Accessing Database
```python
import database as db

# Read
valuation = db.get_valuation("AAPL")
ticker_info = db.get_ticker_info("AAPL")

# Write
db.update_valuation("AAPL", {"current_price": 150.0})
db.bulk_update_valuations({"AAPL": {...}, "GOOGL": {...}})
```

### Background Tasks
```python
# Screener runs in background thread
# Check screener_running flag before starting
# Update screener_progress dict for UI polling
```

## Testing

**No test suite exists.** When making changes:
1. Run the app: `./venv/bin/python app.py`
2. Test the affected UI tab manually
3. Check terminal for errors
4. For provider changes, test with:
   ```python
   ./venv/bin/python -c "
   from services.providers import init_providers, get_orchestrator
   init_providers()
   orch = get_orchestrator()
   result = orch.fetch_price('AAPL')
   print(result)
   "
   ```

## Key Configuration (config.py)

| Constant | Value | Purpose |
|----------|-------|---------|
| `PE_RATIO_MULTIPLIER` | 10 | Fair value multiplier |
| `RECOMMENDED_EPS_YEARS` | 8 | Years of EPS to average |
| `PRICE_CACHE_DURATION` | 300 | Seconds to cache prices |
| `FAILURE_THRESHOLD` | 3 | Failures before excluding ticker |

## Gotchas & Pitfalls

1. **app.py is huge** - 2000+ lines, routes should move to blueprints
2. **Two config files** - `config.py` (app) vs `services/providers/config.py` (providers)
3. **Blueprints active** - Routes in `routes/` are registered via `register_blueprints()`
4. **No tests** - Manual testing only
5. **yfinance is fragile** - Often changes API, breaks things
6. **SEC rate limit** - 10 req/sec, be careful with batch operations
7. **Provider ordering matters** - For prices, realtime providers tried before historical

## API Response Format

Most endpoints return:
```json
{
  "success": true,
  "data": { ... }
}
// or
{
  "success": false,
  "error": "Error message"
}
```

## Screener Phases

When running screener (services/screener.py):
1. **EPS** - Fetch from SEC/providers
2. **Dividends** - Fetch dividend data
3. **Prices** - Fetch current prices
4. **Valuations** - Calculate fair values

Order matters for some calculations.
