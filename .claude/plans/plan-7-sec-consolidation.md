# Plan 7: SEC Data Consolidation - Comprehensive Implementation Plan

## Executive Summary

This plan consolidates all SEC data access through the provider system, eliminating direct `sec_data.py` imports across 8 files. The refactor extends `sec_provider.py` to expose all SEC functionality and migrates 16 unique function calls to use the orchestrator pattern.

---

## Part 1: Current State Analysis

### 1.1 sec_data.py Public Functions (27 total)

| # | Function | Line | HTTP | DB | Thread | Purpose |
|---|----------|------|------|-----|--------|---------|
| 1 | `rate_limit()` | 37 | - | - | State | Enforce 10 req/sec SEC limit |
| 2 | `load_metadata()` | 48 | - | R | - | Load cache version/timestamp |
| 3 | `save_metadata()` | 58 | - | W | - | Save cache version/timestamp |
| 4 | `load_cik_mapping()` | 68 | - | R | - | Load ticker→CIK cache |
| 5 | `save_cik_mapping()` | 73 | - | W | - | Save ticker→CIK cache |
| 6 | `update_cik_mapping()` | 78 | YES | W | - | Fetch fresh CIK from SEC |
| 7 | `get_cik_for_ticker()` | 111 | - | R | - | Get CIK with auto-refresh |
| 8 | `load_company_cache()` | 134 | - | R | - | Load cached company data |
| 9 | `save_company_cache()` | 139 | - | W | - | Save company data |
| 10 | `fetch_company_eps()` | 144 | YES | - | - | Fetch EPS from SEC XBRL |
| 11 | `fetch_company_metrics()` | 253 | YES | - | - | Fetch multi-year EPS matrix |
| 12 | `get_sec_metrics()` | 357 | YES | - | - | **PUBLIC**: Get SEC metrics (always fresh) |
| 13 | `get_sec_eps()` | 366 | Cond | R/W | - | **PUBLIC**: Get EPS with caching |
| 14 | `is_cache_stale()` | 394 | - | R | - | Check EPS cache staleness |
| 15 | `has_cached_eps()` | 408 | - | R | - | Check if cached data exists |
| 16 | `fetch_sec_eps_if_missing()` | 413 | Cond | R/W | - | Fetch only if not cached |
| 17 | `force_refresh_sec_eps()` | 460 | YES | R/W | - | **PUBLIC**: Check for new years |
| 18 | `update_sec_data_for_tickers()` | 522 | YES | W | Worker | Batch update in thread |
| 19 | `start_background_update()` | 565 | - | - | Spawn | **PUBLIC**: Start background job |
| 20 | `stop_update()` | 578 | - | - | Control | **PUBLIC**: Stop background job |
| 21 | `get_update_progress()` | 584 | - | - | Read | **PUBLIC**: Poll job progress |
| 22 | `check_and_update_on_startup()` | 589 | Cond | R/W | Spawn | **PUBLIC**: Startup initialization |
| 23 | `get_cache_status()` | 620 | - | R | - | **PUBLIC**: Cache stats for UI |
| 24 | `get_eps_update_recommendations()` | 638 | - | R | - | **PUBLIC**: Smart update scheduling |
| 25 | `fetch_10k_filings()` | 790 | YES | - | - | Fetch 10-K URLs from SEC |
| 26 | `is_filings_stale()` | 854 | - | R | - | Check filing cache staleness |
| 27 | `get_10k_filings()` | 867 | Cond | R/W | - | **PUBLIC**: Get 10-K URLs |

### 1.2 sec_provider.py Current Coverage

**Currently Wrapped (2 functions):**
- `get_sec_eps()` → `SECEPSProvider.fetch_eps()`
- `force_refresh_sec_eps()` → `SECEPSProvider.fetch_eps_fresh()`

**NOT Wrapped (9 public functions):**
| Function | Gap Type | Priority |
|----------|----------|----------|
| `get_sec_metrics()` | Data type | HIGH |
| `get_10k_filings()` | Data type | HIGH |
| `get_cache_status()` | Status/monitoring | MEDIUM |
| `get_update_progress()` | Status/monitoring | MEDIUM |
| `start_background_update()` | Infrastructure | MEDIUM |
| `stop_update()` | Infrastructure | MEDIUM |
| `check_and_update_on_startup()` | Infrastructure | LOW |
| `get_eps_update_recommendations()` | Analytics | MEDIUM |
| `get_cik_for_ticker()` | Internal helper | LOW |

### 1.3 Current Usage Map (16 unique calls across 8 files)

```
app.py (2 calls)
├── Line 422: sec_data.get_10k_filings(ticker)
└── Line 713: sec_data.check_and_update_on_startup(tickers)

routes/admin.py (1 call)
└── Line 87: sec_data.get_sec_eps(ticker)  # company name fallback

routes/data.py (2 calls)
├── Line 56: sec_data.get_cache_status()
└── Line 162: sec_data.get_eps_update_recommendations()

routes/valuation.py (2 calls)
├── Line 32: sec_data.get_sec_metrics(ticker)
└── Line 73: sec_data.get_sec_eps(ticker)  # company name

routes/sec.py (7 calls)
├── Line 25: sec_data.get_cache_status()
├── Line 26: sec_data.get_update_progress()
├── Line 46: sec_data.start_background_update(tickers)
├── Line 56: sec_data.stop_update()
├── Line 63: sec_data.get_update_progress()
├── Line 71: sec_data.get_sec_eps(ticker)
└── Line 85: sec_data.get_sec_eps(ticker)

services/screener.py (2 calls)
├── Line 182: sec_data.get_sec_eps(ticker)
└── Line 954: sec_data.get_sec_eps(ticker)

services/stock_utils.py (1 call)
└── Line 189: sec_data.get_sec_eps(ticker)  # company name fallback

services/providers/sec_provider.py (internal - already wrapped)
├── Line 64: sec_data.get_sec_eps(ticker)
└── Line 167: sec_data.force_refresh_sec_eps(ticker)
```

---

## Part 2: Database Schema Reference

### 2.1 SEC-Related Tables

```sql
-- Company metadata and SEC status
CREATE TABLE sec_companies (
    ticker TEXT PRIMARY KEY,
    cik TEXT,
    company_name TEXT,
    sec_no_eps INTEGER DEFAULT 0,
    reason TEXT,
    updated TEXT
);

-- Annual EPS records (8-year rolling window)
CREATE TABLE eps_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    year INTEGER NOT NULL,
    eps REAL,
    filed TEXT,
    period_start TEXT,
    period_end TEXT,
    eps_type TEXT,
    UNIQUE(ticker, year),
    FOREIGN KEY(ticker) REFERENCES sec_companies(ticker) ON DELETE CASCADE
);

-- Cached ticker→CIK mapping (30-day TTL)
CREATE TABLE cik_mapping (
    ticker TEXT PRIMARY KEY,
    cik TEXT,
    name TEXT,
    updated TEXT
);

-- 10-K filing URLs (7-day TTL)
CREATE TABLE sec_filings (
    ticker TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    form_type TEXT NOT NULL,
    accession_number TEXT NOT NULL,
    filing_date TEXT NOT NULL,
    document_url TEXT NOT NULL,
    updated TEXT NOT NULL,
    PRIMARY KEY (ticker, fiscal_year, form_type)
);

-- System metadata (cache timestamps)
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated TEXT
);
-- Keys: 'sec_cache_version', 'sec_last_full_update'
```

---

## Part 3: Provider System Architecture

### 3.1 Current Interfaces (base.py)

```python
class EPSProvider(BaseProvider):
    """Current interface - SEC provider implements this"""
    data_types = [DataType.EPS]

    @abstractmethod
    def fetch_eps(self, ticker: str) -> ProviderResult: ...
```

### 3.2 New Interfaces Needed

```python
class SECMetricsProvider(BaseProvider):
    """Multi-year EPS matrix + dividend data"""
    data_types = [DataType.SEC_METRICS]

    @abstractmethod
    def fetch_metrics(self, ticker: str) -> ProviderResult: ...

class FilingsProvider(BaseProvider):
    """10-K filing URLs"""
    data_types = [DataType.FILINGS]

    @abstractmethod
    def fetch_filings(self, ticker: str) -> ProviderResult: ...
```

### 3.3 Orchestrator Extension Pattern

```python
# In registry.py DataOrchestrator class:

def fetch_sec_metrics(self, ticker: str) -> ProviderResult:
    """Get multi-year SEC metrics (EPS matrix + dividends)"""
    # Direct call - SEC is only source

def fetch_filings(self, ticker: str) -> ProviderResult:
    """Get 10-K filing URLs"""
    # Direct call - SEC is only source

# Status methods (not provider-pattern, but on orchestrator)
def get_sec_cache_status(self) -> Dict: ...
def get_sec_update_progress(self) -> Dict: ...
def start_sec_background_update(self, tickers: List[str]) -> bool: ...
def stop_sec_background_update(self) -> None: ...
def get_eps_update_recommendations(self) -> Dict: ...
```

---

## Part 4: Phased Implementation Plan

### Phase 4A: Extend SECProvider with New Methods (No Breaking Changes)

**Files to modify:**
- `services/providers/base.py`
- `services/providers/sec_provider.py`
- `services/providers/__init__.py`

**Step 4A.1: Add new DataTypes to base.py**
```python
# In DataType enum, add:
class DataType(Enum):
    PRICE = "price"
    EPS = "eps"
    DIVIDEND = "dividend"
    # ... existing ...
    SEC_METRICS = "sec_metrics"    # NEW
    FILINGS = "filings"            # NEW
```

**Step 4A.2: Add new data classes to base.py**
```python
@dataclass
class SECMetricsData:
    ticker: str
    source: str
    eps_matrix: List[Dict]      # Multi-year EPS by type
    dividend_history: List[Dict] # Annual dividends
    company_name: Optional[str] = None
    cik: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class FilingsData:
    ticker: str
    source: str
    filings: List[Dict]  # [{fiscal_year, form_type, filing_date, document_url, ...}]
    timestamp: datetime = field(default_factory=datetime.now)
```

**Step 4A.3: Extend SECEPSProvider in sec_provider.py**
```python
class SECEPSProvider(EPSProvider):
    # ... existing methods ...

    # NEW: SEC Metrics
    def fetch_metrics(self, ticker: str) -> ProviderResult:
        """Wrap sec_data.get_sec_metrics()"""
        import sec_data
        data = sec_data.get_sec_metrics(ticker)
        if not data:
            return ProviderResult(success=False, data=None, source=self.name,
                                  error="No SEC metrics available")
        return ProviderResult(
            success=True,
            data=SECMetricsData(
                ticker=ticker,
                source=self.name,
                eps_matrix=data.get('eps_by_year', []),
                dividend_history=data.get('dividends', []),
                company_name=data.get('company_name'),
                cik=data.get('cik')
            ),
            source=self.name
        )

    # NEW: 10-K Filings
    def fetch_filings(self, ticker: str) -> ProviderResult:
        """Wrap sec_data.get_10k_filings()"""
        import sec_data
        filings = sec_data.get_10k_filings(ticker)
        if not filings:
            return ProviderResult(success=False, data=None, source=self.name,
                                  error="No 10-K filings found")
        return ProviderResult(
            success=True,
            data=FilingsData(ticker=ticker, source=self.name, filings=filings),
            source=self.name
        )

    # NEW: Cache/Status methods (class methods, not instance)
    @staticmethod
    def get_cache_status() -> Dict:
        """Wrap sec_data.get_cache_status()"""
        import sec_data
        return sec_data.get_cache_status()

    @staticmethod
    def get_update_progress() -> Dict:
        """Wrap sec_data.get_update_progress()"""
        import sec_data
        return sec_data.get_update_progress()

    @staticmethod
    def start_background_update(tickers: List[str]) -> bool:
        """Wrap sec_data.start_background_update()"""
        import sec_data
        return sec_data.start_background_update(tickers)

    @staticmethod
    def stop_background_update() -> None:
        """Wrap sec_data.stop_update()"""
        import sec_data
        sec_data.stop_update()

    @staticmethod
    def get_eps_update_recommendations() -> Dict:
        """Wrap sec_data.get_eps_update_recommendations()"""
        import sec_data
        return sec_data.get_eps_update_recommendations()

    @staticmethod
    def check_and_update_on_startup(tickers: List[str]) -> None:
        """Wrap sec_data.check_and_update_on_startup()"""
        import sec_data
        sec_data.check_and_update_on_startup(tickers)
```

**Step 4A.4: Update __init__.py exports**
```python
from .sec_provider import SECEPSProvider
from .base import SECMetricsData, FilingsData, DataType
# Add to __all__
```

---

### Phase 4B: Add Orchestrator Methods

**Files to modify:**
- `services/providers/registry.py`

**Step 4B.1: Add fetch methods to DataOrchestrator**
```python
def fetch_sec_metrics(self, ticker: str) -> ProviderResult:
    """Fetch SEC metrics (EPS matrix + dividends) for a ticker."""
    provider = self._registry.get_provider("sec_edgar")
    if not provider:
        return ProviderResult(success=False, data=None, source="none",
                              error="SEC provider not available")
    return provider.fetch_metrics(ticker)

def fetch_filings(self, ticker: str) -> ProviderResult:
    """Fetch 10-K filing URLs for a ticker."""
    provider = self._registry.get_provider("sec_edgar")
    if not provider:
        return ProviderResult(success=False, data=None, source="none",
                              error="SEC provider not available")
    return provider.fetch_filings(ticker)
```

**Step 4B.2: Add status methods to DataOrchestrator**
```python
def get_sec_cache_status(self) -> Dict:
    """Get SEC cache statistics."""
    provider = self._registry.get_provider("sec_edgar")
    if provider and hasattr(provider, 'get_cache_status'):
        return provider.get_cache_status()
    return {}

def get_sec_update_progress(self) -> Dict:
    """Get SEC background update progress."""
    provider = self._registry.get_provider("sec_edgar")
    if provider and hasattr(provider, 'get_update_progress'):
        return provider.get_update_progress()
    return {'status': 'unknown'}

def start_sec_background_update(self, tickers: List[str]) -> bool:
    """Start SEC background update for tickers."""
    provider = self._registry.get_provider("sec_edgar")
    if provider and hasattr(provider, 'start_background_update'):
        return provider.start_background_update(tickers)
    return False

def stop_sec_background_update(self) -> None:
    """Stop SEC background update."""
    provider = self._registry.get_provider("sec_edgar")
    if provider and hasattr(provider, 'stop_background_update'):
        provider.stop_background_update()

def get_eps_update_recommendations(self) -> Dict:
    """Get recommendations for which tickers need EPS updates."""
    provider = self._registry.get_provider("sec_edgar")
    if provider and hasattr(provider, 'get_eps_update_recommendations'):
        return provider.get_eps_update_recommendations()
    return {}

def check_sec_startup(self, tickers: List[str]) -> None:
    """Run SEC startup checks and background updates if needed."""
    provider = self._registry.get_provider("sec_edgar")
    if provider and hasattr(provider, 'check_and_update_on_startup'):
        provider.check_and_update_on_startup(tickers)
```

---

### Phase 4C: Migrate Consumers (File by File)

#### 4C.1: Migrate app.py

**Current:**
```python
import sec_data  # Line 8

# Line 422
filings = sec_data.get_10k_filings(ticker)

# Line 713
sec_data.check_and_update_on_startup(tickers)
```

**After:**
```python
# Remove: import sec_data

# Line 422
orchestrator = get_orchestrator()
result = orchestrator.fetch_filings(ticker)
filings = result.data.filings if result.success else []

# Line 713
orchestrator = get_orchestrator()
orchestrator.check_sec_startup(tickers)
```

#### 4C.2: Migrate routes/admin.py

**Current:**
```python
import sec_data  # Line 10

# Line 87
sec_result = sec_data.get_sec_eps(ticker)
if sec_result and sec_result.get('company_name'):
    company_name = sec_result['company_name']
```

**After:**
```python
# Remove: import sec_data
from services.providers import get_orchestrator

# Line 87
orchestrator = get_orchestrator()
result = orchestrator.fetch_eps(ticker)
if result.success and result.data.company_name:
    company_name = result.data.company_name
```

#### 4C.3: Migrate routes/data.py

**Current:**
```python
import sec_data  # Line 19

# Line 56
sec_cache = sec_data.get_cache_status()

# Line 162
recommendations = sec_data.get_eps_update_recommendations()
```

**After:**
```python
# Remove: import sec_data
from services.providers import get_orchestrator

# Line 56
orchestrator = get_orchestrator()
sec_cache = orchestrator.get_sec_cache_status()

# Line 162
recommendations = orchestrator.get_eps_update_recommendations()
```

#### 4C.4: Migrate routes/valuation.py

**Current:**
```python
import sec_data  # Line 13

# Line 32
metrics = sec_data.get_sec_metrics(ticker)

# Line 73
sec_eps = sec_data.get_sec_eps(ticker)
```

**After:**
```python
# Remove: import sec_data
from services.providers import get_orchestrator

# Line 32
orchestrator = get_orchestrator()
result = orchestrator.fetch_sec_metrics(ticker)
metrics = result.data if result.success else None

# Line 73
result = orchestrator.fetch_eps(ticker)
sec_eps = {'company_name': result.data.company_name} if result.success else None
```

#### 4C.5: Migrate routes/sec.py

**Current:**
```python
import sec_data  # Line 14

# Lines 25-26
cache_status = sec_data.get_cache_status()
update_progress = sec_data.get_update_progress()

# Line 46
sec_data.start_background_update(tickers)

# Line 56
sec_data.stop_update()

# Line 63
progress = sec_data.get_update_progress()

# Lines 71, 85
sec_data.get_sec_eps(ticker)
```

**After:**
```python
# Remove: import sec_data
from services.providers import get_orchestrator

# Get orchestrator once at module level or in each route
orchestrator = get_orchestrator()

# Lines 25-26
cache_status = orchestrator.get_sec_cache_status()
update_progress = orchestrator.get_sec_update_progress()

# Line 46
orchestrator.start_sec_background_update(tickers)

# Line 56
orchestrator.stop_sec_background_update()

# Line 63
progress = orchestrator.get_sec_update_progress()

# Lines 71, 85
result = orchestrator.fetch_eps(ticker)
sec_eps = result.data if result.success else None
```

#### 4C.6: Migrate services/screener.py

**Current:**
```python
import sec_data  # Line 23

# Line 182
sec_eps = sec_data.get_sec_eps(ticker)

# Line 954
sec_eps = sec_data.get_sec_eps(ticker)
```

**After:**
```python
# Remove: import sec_data
from services.providers import get_orchestrator

# Line 182, 954
orchestrator = get_orchestrator()
result = orchestrator.fetch_eps(ticker)
if result.success:
    sec_eps = {
        'eps_history': [
            {'year': e['year'], 'eps': e['eps'], 'filed': e.get('filed')}
            for e in result.data.eps_history
        ],
        'company_name': result.data.company_name
    }
else:
    sec_eps = None
```

#### 4C.7: Migrate services/stock_utils.py

**Current:**
```python
# Line 160 (lazy import inside function)
import sec_data

# Line 189
sec_result = sec_data.get_sec_eps(ticker_upper)
```

**After:**
```python
# Line 189
from services.providers import get_orchestrator
orchestrator = get_orchestrator()
result = orchestrator.fetch_eps(ticker_upper)
if result.success and result.data.company_name:
    return result.data.company_name
```

---

### Phase 4D: Deprecate Direct sec_data.py Usage

**Step 4D.1: Add deprecation warnings to sec_data.py public functions**
```python
import warnings

def get_sec_eps(ticker):
    warnings.warn(
        "Direct sec_data.get_sec_eps() is deprecated. "
        "Use orchestrator.fetch_eps() instead.",
        DeprecationWarning,
        stacklevel=2
    )
    # ... existing implementation ...
```

**Step 4D.2: Update CLAUDE.md documentation**
- Document new orchestrator methods
- Mark sec_data.py as internal/deprecated

---

## Part 5: Testing Plan

### 5.1 Unit Tests for New Provider Methods

```python
# Test fetch_metrics
def test_fetch_metrics_success():
    provider = SECEPSProvider()
    result = provider.fetch_metrics("AAPL")
    assert result.success
    assert isinstance(result.data, SECMetricsData)
    assert len(result.data.eps_matrix) > 0

# Test fetch_filings
def test_fetch_filings_success():
    provider = SECEPSProvider()
    result = provider.fetch_filings("AAPL")
    assert result.success
    assert isinstance(result.data, FilingsData)
    assert len(result.data.filings) > 0
```

### 5.2 Integration Tests

```bash
# Test all migrated routes still work
./venv/bin/python -c "
from services.providers import init_providers, get_orchestrator
init_providers()
orch = get_orchestrator()

# Test EPS
result = orch.fetch_eps('AAPL')
print(f'EPS: {result.success}, years: {len(result.data.eps_history) if result.success else 0}')

# Test Metrics
result = orch.fetch_sec_metrics('AAPL')
print(f'Metrics: {result.success}')

# Test Filings
result = orch.fetch_filings('AAPL')
print(f'Filings: {result.success}, count: {len(result.data.filings) if result.success else 0}')

# Test Cache Status
status = orch.get_sec_cache_status()
print(f'Cache: {status}')
"
```

### 5.3 Manual Testing Checklist

- [ ] `/api/sec/status` returns cache status and progress
- [ ] `/api/sec/update` starts background update
- [ ] `/api/sec/stop` stops background update
- [ ] `/api/sec/progress` returns update progress
- [ ] `/api/sec/eps/<ticker>` returns EPS data
- [ ] `/api/sec/compare/<ticker>` returns comparison data
- [ ] `/api/sec-metrics/<ticker>` returns metrics
- [ ] `/api/sec-filings/<ticker>` returns 10-K URLs
- [ ] `/api/eps-recommendations` returns recommendations
- [ ] Screener Phase 1 loads SEC EPS correctly
- [ ] Valuation refresh uses SEC company name
- [ ] Company name backfill works in admin

---

## Part 6: Risk Assessment

### 6.1 High Risk Areas

| Area | Risk | Mitigation |
|------|------|------------|
| Screener EPS loading | Core functionality | Test thoroughly, keep fallback |
| Background updates | Thread management | Wrapper just delegates, no logic change |
| Rate limiting | SEC compliance | Rate limit stays in sec_data.py |

### 6.2 Low Risk Areas

| Area | Why Low Risk |
|------|--------------|
| 10-K filings | Simple wrapper, single source |
| Cache status | Read-only, no side effects |
| Metrics | Read-only, always fresh fetch |

### 6.3 Rollback Plan

If issues arise:
1. Revert consumer files to use `import sec_data` directly
2. Keep new provider methods (they're additive)
3. Remove deprecation warnings from sec_data.py

---

## Part 7: File Change Summary

### Files to Create
- None (extending existing files)

### Files to Modify

| File | Changes |
|------|---------|
| `services/providers/base.py` | Add DataType.SEC_METRICS, DataType.FILINGS, SECMetricsData, FilingsData |
| `services/providers/sec_provider.py` | Add 8 new methods |
| `services/providers/registry.py` | Add 7 orchestrator methods |
| `services/providers/__init__.py` | Export new types |
| `app.py` | Remove sec_data import, use orchestrator |
| `routes/admin.py` | Remove sec_data import, use orchestrator |
| `routes/data.py` | Remove sec_data import, use orchestrator |
| `routes/valuation.py` | Remove sec_data import, use orchestrator |
| `routes/sec.py` | Remove sec_data import, use orchestrator |
| `services/screener.py` | Remove sec_data import, use orchestrator |
| `services/stock_utils.py` | Remove sec_data import, use orchestrator |

### Files to Keep (Internal)
- `sec_data.py` - Becomes internal implementation detail

---

## Part 8: Execution Order

1. **Phase 4A** - Extend sec_provider.py with new methods (additive, no breaking changes)
2. **Phase 4B** - Add orchestrator methods (additive, no breaking changes)
3. **Phase 4C.5** - Migrate routes/sec.py first (most SEC calls, good test)
4. **Phase 4C.3** - Migrate routes/data.py
5. **Phase 4C.4** - Migrate routes/valuation.py
6. **Phase 4C.2** - Migrate routes/admin.py
7. **Phase 4C.1** - Migrate app.py
8. **Phase 4C.7** - Migrate services/stock_utils.py
9. **Phase 4C.6** - Migrate services/screener.py (most critical, do last)
10. **Phase 4D** - Add deprecation warnings

---

## Appendix A: Data Format Mappings

### sec_data.get_sec_eps() → orchestrator.fetch_eps()

**Input:** `ticker: str`

**sec_data output:**
```python
{
    'ticker': 'AAPL',
    'company_name': 'Apple Inc.',
    'cik': '0000320193',
    'eps_history': [
        {'year': 2024, 'eps': 6.42, 'filed': '2024-11-01', 'period_start': '2023-10-01', 'period_end': '2024-09-30', 'eps_type': 'Diluted EPS'},
        ...
    ],
    'updated': '2024-12-20T10:30:00'
}
```

**Orchestrator output:**
```python
ProviderResult(
    success=True,
    data=EPSData(
        ticker='AAPL',
        source='sec_edgar',
        company_name='Apple Inc.',
        eps_history=[
            {'year': 2024, 'eps': 6.42, 'filed': '2024-11-01', 'period_start': '2023-10-01', 'period_end': '2024-09-30', 'eps_type': 'Diluted EPS', 'source': 'sec_edgar'},
            ...
        ]
    ),
    source='sec_edgar'
)
```

### sec_data.get_10k_filings() → orchestrator.fetch_filings()

**Input:** `ticker: str`

**sec_data output:**
```python
[
    {'fiscal_year': 2024, 'form_type': '10-K', 'filing_date': '2024-11-01', 'document_url': 'https://...', 'accession_number': '...'},
    ...
]
```

**Orchestrator output:**
```python
ProviderResult(
    success=True,
    data=FilingsData(
        ticker='AAPL',
        source='sec_edgar',
        filings=[
            {'fiscal_year': 2024, 'form_type': '10-K', 'filing_date': '2024-11-01', 'document_url': 'https://...', 'accession_number': '...'},
            ...
        ]
    ),
    source='sec_edgar'
)
```
