# FinanceApp Refactoring Plan

This document outlines incomplete refactoring, hardcoded values, class opportunities, and inconsistent patterns identified in the codebase.

---

## Executive Summary

The codebase shows evidence of several half-completed migrations:
1. **Blueprint migration** - Routes prepared but disabled (~1,200 lines of duplicate code)
2. **Provider system migration** - yfinance calls should go through orchestrator
3. **Database migration** - Routes still use CSV, app.py uses SQLite
4. **Class-based refactoring** - Provider/index systems are well-structured, but database.py and app.py are procedural

**Recommended approach**: Complete one migration fully before starting another.

---

## Part 1: Half-Done Refactoring

### 1.1 Blueprint Routes (CRITICAL)

**Status**: 35+ routes prepared in `routes/` but disabled in `app.py`

**Location**: `app.py:44-47`
```python
# Blueprint registration disabled - blueprints have different response formats
# that need to be aligned with JavaScript expectations before enabling
# from routes import register_blueprints
# register_blueprints(app)
```

**Impact**: ~1,193 lines of duplicate code in `routes/` directory

**Files affected**:
| File | Lines | Duplicate Routes |
|------|-------|------------------|
| routes/holdings.py | 143 | /api/holdings, /api/holdings-analysis |
| routes/transactions.py | 127 | /api/transactions, /api/stocks |
| routes/valuation.py | 161 | /api/valuation/*, /api/sec-metrics/* |
| routes/data.py | 234 | /api/data-status, /api/excluded-tickers |
| routes/sec.py | 183 | /api/sec/* endpoints |
| routes/summary.py | 233 | /api/summary, /api/prices |
| routes/screener.py | 189 | /api/screener/*, /api/refresh |

**Decision needed**:
- [ ] Option A: Complete blueprint migration (align response formats)
- [ ] Option B: Delete routes/ directory (accept app.py as source of truth)

---

### 1.2 CSV vs Database Split

**Status**: Routes use CSV files, app.py uses SQLite database

**routes/transactions.py** (lines 21-48):
```python
def get_transactions():
    with open(TRANSACTIONS_FILE, 'r') as f:  # CSV
        reader = csv.DictReader(f)
        return list(reader)
```

**app.py** (lines 421-448):
```python
db.add_transaction(ticker=..., action=..., shares=..., price=...)  # SQLite
```

**Risk**: If blueprints were enabled, they would write to different storage

**Resolution**: Routes must use `database.py` module, not direct file I/O

---

### 1.3 yfinance Provider Migration

**Status**: `USE_ORCHESTRATOR` flag exists, legacy fallback code remains

**Files with direct yfinance references**:
| File | Issue |
|------|-------|
| services/stock_utils.py:34 | `USE_ORCHESTRATOR = True` toggle flag |
| services/stock_utils.py:43-44 | Legacy fallback: yfinance → FMP |
| services/valuation.py:27 | Source field distinguishes 'yfinance' vs 'sec_edgar' |
| app.py:1134 | Comment: "Override yfinance prices with provider system" |
| app.py:2476 | Comment about yfinance fiscal calendars |

**Cleanup needed**:
- [ ] Remove `USE_ORCHESTRATOR` flag (commit to orchestrator)
- [ ] Remove legacy fallback functions
- [ ] Update comments referencing old approach

---

### 1.4 Screener Circular Import Workaround

**Status**: routes/screener.py uses sys.path manipulation

**routes/screener.py:22-37**:
```python
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_app_module = None
def _get_app_module():
    """Lazy import of app module to avoid circular imports."""
    ...
```

**Problem**: Routes depend on app.py's internal state (`screener_running`, `screener_progress`)

**Fix required**: Move screener state to services/screener.py

---

### 1.5 Data Manager Compatibility Layer

**Status**: Wrapper functions exist for backward compatibility

**data_manager.py:78-86, 152-160**:
```python
def save_ticker_status(data: Dict):
    """For backward compatibility - updates database from dict structure."""

def save_valuations(data: Dict):
    """For backward compatibility - updates database from dict structure."""
```

**Cleanup**: These can be removed once all callers use database.py directly

---

## Part 2: Hardcoded Values

### 2.1 Time Delays (Should be in config.py)

| File                 | Line       | Value              | Suggested Config               |
| -------------------- | ---------- | ------------------ | ------------------------------ |
| app.py               | 1128, 1954 | `time.sleep(0.2)`  | RATE_LIMIT_DELAY_SEC = 0.2     |
| app.py               | 1999       | `time.sleep(0.15)` | REQUEST_DELAY_SEC = 0.15       |
| app.py               | 2285       | `time.sleep(0.3)`  | DIVIDEND_FETCH_DELAY_SEC = 0.3 |
| yfinance_provider.py | 120        | `time.sleep(1.5)`  | BATCH_CHUNK_DELAY_SEC = 1.5    |
| fmp_provider.py      | 209        | `time.sleep(5)`    | RATE_LIMIT_BACKOFF_SEC = 5     |

### 2.2 Retry Logic

| File | Line | Value | Suggested Config |
|------|------|-------|------------------|
| app.py | 754 | `max_retries = 2` | MAX_RETRY_ATTEMPTS = 2 |
| app.py | 808 | `2 ** retry_count` | RETRY_BACKOFF_BASE = 2 |

### 2.3 Batch Sizes (DUPLICATED)

**Current state** - same value defined multiple places:
| File | Line | Value |
|------|------|-------|
| config.py | 108 | `YAHOO_BATCH_SIZE = 100` |
| services/providers/config.py | 56 | `batch_size: int = 100` |
| yfinance_provider.py | 111 | `chunk_size = 50` (different!) |
| yfinance_provider.py | 324 | `chunk_size = 100` |
| fmp_provider.py | 18 | `FMP_BATCH_SIZE = 100` |
| ibkr_provider.py | 27 | `IBKR_BATCH_SIZE = 50` |

**Resolution**: Single source of truth in services/providers/config.py

### 2.4 Cache Duration (DUPLICATED)

| File | Line | Value |
|------|------|-------|
| config.py | 42 | `PRICE_CACHE_DURATION = 300` |
| services/providers/config.py | 48 | `price_cache_seconds: int = 300` |

**Resolution**: Pick one location

### 2.5 Data Periods

| File | Line | Value | Suggested Config |
|------|------|-------|------------------|
| app.py | 667, 1040, 1326 | `period='3mo'` | PRICE_HISTORY_PERIOD = '3mo' |
| yfinance_provider.py | 74 | `period='5d'` | SINGLE_PRICE_PERIOD = '5d' |
| yfinance_provider.py | 574 | `period='1mo'` | DIVIDEND_HISTORY_PERIOD = '1mo' |

### 2.6 Provider-Specific Constants

**Should move to services/providers/config.py**:
| File | Constants |
|------|-----------|
| fmp_provider.py | FMP_BASE_URL, FMP_REQUEST_TIMEOUT, FMP_BATCH_SIZE |
| alpaca_provider.py | ALPACA_REQUEST_TIMEOUT = 15 |
| ibkr_provider.py | DEFAULT_PORT = 7497, DEFAULT_CLIENT_ID = 10, MARKET_DATA_TIMEOUT = 5 |
| sec_provider.py | Returns 0.12 (rate limit) |

### 2.7 Index Providers (No config file exists)

**services/indexes/providers.py** - All timeout=30 hardcoded (lines 150, 239, 331, 437)

**Create**: `services/indexes/config.py`

---

## Part 3: Class-Based Refactoring Opportunities

### 3.1 database.py - Procedural CRUD (1,467 lines)

**Current**: 80+ standalone functions with repetitive patterns
```python
def get_valuation(ticker):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT...')
        ...

def update_valuation(ticker, data):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE...')
        ...
```

**Proposed**: Repository classes
```python
class ValuationRepository:
    def get(self, ticker: str) -> Valuation: ...
    def update(self, ticker: str, valuation: Valuation): ...
    def bulk_update(self, valuations: Dict[str, Valuation]): ...
    def get_undervalued(self, threshold: float) -> List[Valuation]: ...

class TickerRepository:
    def get_info(self, ticker: str) -> TickerInfo: ...
    def update_status(self, ticker: str, updates: Dict): ...
    def get_by_index(self, index: str) -> List[str]: ...

class HoldingsRepository:
    def get_stocks(self) -> List[Stock]: ...
    def get_transactions(self, ticker: str = None) -> List[Transaction]: ...
    def add_transaction(...) -> int: ...
```

**Benefits**:
- Eliminate ~300 lines of context-manager boilerplate
- Clearer domain boundaries
- Easier to test with mock repositories

### 3.2 services/screener.py - Global State (277 lines)

**Current**: Module-level mutable state
```python
_running = False
_progress = {'current': 0, 'total': 0, 'status': 'idle', ...}

def is_screener_running():
    return _running
```

**Proposed**: Encapsulated service
```python
@dataclass
class ScreenerProgress:
    current: int = 0
    total: int = 0
    ticker: str = ''
    status: str = 'idle'
    phase: str = ''

class ScreenerService:
    def __init__(self):
        self._running = False
        self._progress = ScreenerProgress()

    def start_quick_update(self, index_name='all') -> bool: ...
    def get_progress(self) -> ScreenerProgress: ...
```

**Benefits**:
- Thread-safe state management
- Enables multiple screener instances
- Testable

### 3.3 app.py - Business Logic in Routes (3,650 lines)

**Current**: Flask routes contain data transformation logic
```python
@app.route('/api/screener/quick-update', methods=['POST'])
def api_screener_quick_update():
    # 20 lines of validation, setup, orchestration
    ...
```

**Proposed**: Thin routes + service classes
```python
@app.route('/api/screener/quick-update', methods=['POST'])
def api_screener_quick_update():
    index = request.json.get('index', 'all')
    success = screener_service.start_quick_update(index)
    return jsonify({'success': success})
```

### 3.4 Dictionary Patterns → Dataclasses

**Current**: Data passed as dicts
```python
{'ticker': 'AAPL', 'price': 150.0, 'source': 'yfinance', 'eps_avg': 6.5}
```

**Proposed**: Typed dataclasses (following provider system pattern)
```python
@dataclass
class Valuation:
    ticker: str
    current_price: float
    estimated_value: float
    price_vs_value: float
    eps_average: float
    dividend: float
    source: str
```

---

## Part 4: Inconsistent Patterns

### 4.1 Error Handling (3+ patterns)

| Pattern | Example | Used In |
|---------|---------|---------|
| Return dict with error | `{'error': 'rate_limited'}` | app.py:810 |
| Return jsonify with error | `jsonify({'error': 'msg'}), 400` | app.py:1746 |
| Return ProviderResult | `ProviderResult(success=False, ...)` | providers/*.py |
| Silent exception | `except: pass` | routes/data.py |

**Resolution**: Standardize on `Result` pattern throughout

### 4.2 Logging (Mixed print/log)

| Uses log.* | Uses print() |
|------------|--------------|
| app.py (~60 calls) | services/stock_utils.py:218 |
| | services/valuation.py:194 |
| | services/screener.py (~10 prints) |
| | app.py:69, 85, 205, 215 |

**Resolution**: Replace all print() with proper logging

### 4.3 Response Formats

| Field | Example | Used In |
|-------|---------|---------|
| `success: true/false` | `{'success': True, 'id': 123}` | Most endpoints |
| `status` | `{'status': 'started'}` | screener endpoints |
| `error` only | `{'recommendations': [], 'error': 'msg'}` | api_recommendations |

**Resolution**: Standardize: `{success: bool, data?: any, error?: string}`

### 4.4 Import Patterns

| Pattern | Example | Used In |
|---------|---------|---------|
| Absolute | `from services.providers import ...` | app.py |
| Relative | `from .base import ...` | services/providers/*.py |
| sys.path hack | `sys.path.insert(0, ...)` | routes/screener.py, services/holdings.py |

**Resolution**: Fix module structure to eliminate sys.path manipulation

---

## Part 5: Recommended Priorities

### Phase 1: Quick Wins (1-2 days each)

- [ ] **4.2** Replace print() with log.* (affects 5 files)
- [ ] **2.1** Move time delays to config.py
- [ ] **2.3** Consolidate batch sizes to single location
- [ ] **2.4** Eliminate cache duration duplication

### Phase 2: Cleanup (2-3 days each)

- [ ] **1.3** Remove USE_ORCHESTRATOR flag, commit to orchestrator
- [ ] **1.5** Remove backward compatibility wrappers
- [ ] **4.3** Standardize JSON response format

### Phase 3: Structural (1 week each)

- [ ] **3.2** Refactor ScreenerService to encapsulate state
- [ ] **1.1** Decision: Complete blueprint migration OR delete routes/
- [ ] **1.4** Move screener state out of app.py (prerequisite for blueprints)

### Phase 4: Major Refactoring (2+ weeks each)

- [ ] **3.1** Create repository classes for database.py
- [ ] **3.3** Extract business logic from app.py to service classes
- [ ] **3.4** Convert dict patterns to dataclasses

---

## Appendix: Config File Consolidation

### Current State

```
config.py                      # App-level constants
services/providers/config.py   # Provider settings (ProviderConfig dataclass)
services/indexes/              # No config file
```

### Proposed Structure

```
config.py                      # App-level: thresholds, scoring, UI
services/providers/config.py   # Provider: timeouts, batch sizes, cache
services/indexes/config.py     # Index: fetch timeouts, sources (NEW)
```

### Values to Move

**From app.py/providers to config.py**:
- All time.sleep() delays
- Retry counts and backoff settings

**From provider files to services/providers/config.py**:
- FMP_BASE_URL, FMP_REQUEST_TIMEOUT, FMP_BATCH_SIZE
- ALPACA_REQUEST_TIMEOUT
- IBKR defaults (port, client ID, timeout)

**New services/indexes/config.py**:
- INDEX_FETCH_TIMEOUT = 30
- User-agent strings

---

## Next Steps

1. Review this plan and select priorities
2. For each selected item, create a focused task
3. Complete one refactoring fully before starting another
4. Update CLAUDE.md as patterns change
