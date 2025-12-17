# FinanceApp Refactoring Plan

## Overview

The application has grown to ~9,200 lines across 3 main files. This plan outlines a systematic refactoring to improve maintainability, testability, and developer experience.

**Current State:**
- `app.py`: 3,247 lines - routes, business logic, data fetching, background tasks all mixed
- `app.js`: 2,897 lines - rendering, API calls, state, event handlers all mixed
- `style.css`: 3,027 lines - all styles in one file

**Goal State:**
- Modular backend with clear separation of concerns
- Organized frontend with page-specific modules
- Maintainable CSS split by purpose
- Configuration extracted for easy tuning

---

## Phase 1: Configuration & Constants (Priority: High, Effort: Low)

### 1.1 Create config.py

**Why:** Magic numbers scattered throughout app.py make it hard to understand thresholds and tune behavior. Centralizing configuration improves readability and makes A/B testing scoring algorithms trivial.

**File:** `config.py`

```python
# Ticker failure/exclusion settings
FAILURE_THRESHOLD = 3  # Consecutive failures before excluding ticker

# Valuation settings
PE_RATIO_MULTIPLIER = 10  # Fair value = EPS * this
MIN_EPS_YEARS = 3  # Minimum years of EPS data required

# Recommendation scoring weights
SCORING_WEIGHTS = {
    'undervaluation': 1.0,
    'dividend': 1.5,
    'selloff': 0.8
}

# Recommendation scoring thresholds
DIVIDEND_NO_DIVIDEND_PENALTY = -30
DIVIDEND_POINTS_PER_PERCENT = 5
DIVIDEND_MAX_POINTS = 30

SELLOFF_SEVERE_BONUS = 15
SELLOFF_MODERATE_BONUS = 10
SELLOFF_RECENT_BONUS = 5

# Sell candidate thresholds
SELL_OVERVALUED_THRESHOLD = 10  # % above fair value
SELL_GAIN_THRESHOLD = 30  # % gain from cost basis

# Selloff detection thresholds
SELLOFF_SEVERE_1M = -15  # 1-month change %
SELLOFF_SEVERE_3M = -25  # 3-month change %
SELLOFF_MODERATE_1M = -10
SELLOFF_MODERATE_3M = -15
SELLOFF_RECENT_1M = -5
SELLOFF_RECENT_3M = -8

# Data freshness
CACHE_DURATION = 300  # seconds
STALE_DATA_HOURS = 24  # hours before data considered stale

# Rate limiting
YAHOO_BATCH_SIZE = 50
YAHOO_BATCH_DELAY = 0.5  # seconds between batches

# File paths
DATA_DIR = 'data'
EXCLUDED_TICKERS_FILE = 'data/excluded_tickers.json'
TICKER_FAILURES_FILE = 'data/ticker_failures.json'
```

**Changes to app.py:**
- Import from config.py
- Replace all hardcoded values with config references
- Remove duplicate constant definitions

---

## Phase 2: Backend Service Extraction (Priority: High, Effort: Medium)

### 2.1 Create services/yahoo_finance.py

**Why:** Yahoo Finance API calls are scattered throughout app.py with inconsistent error handling and rate limiting. Centralizing these calls:
- Provides single point for rate limiting logic
- Makes retry logic consistent
- Easier to mock for testing
- Could swap to different data provider later

**File:** `services/yahoo_finance.py`

**Functions to extract:**
- `fetch_stock_price(ticker)` - from app.py:150
- `fetch_multiple_prices(tickers)` - from app.py:175
- `_extract_yf_eps(stock, income_stmt)` - from app.py:316
- `calculate_selloff_metrics(stock)` - from app.py:340
- Price/dividend fetching from `fetch_eps_for_ticker()` - from app.py:1008
- Batch downloading logic from `run_quick_price_update()` - from app.py:1316

**New unified interface:**
```python
class YahooFinanceService:
    def __init__(self, batch_size=50, batch_delay=0.5):
        self.batch_size = batch_size
        self.batch_delay = batch_delay

    def get_price(self, ticker) -> float | None
    def get_prices(self, tickers: list) -> dict[str, float]
    def get_stock_info(self, ticker) -> dict  # price, dividend, 52w high/low, etc.
    def get_batch_stock_info(self, tickers: list) -> dict[str, dict]
    def get_eps_data(self, ticker) -> dict  # EPS from income statement
```

### 2.2 Create services/valuation.py

**Why:** Valuation logic (EPS averaging, fair value calculation) is mixed with API routes. Extracting it:
- Makes the algorithm testable
- Allows reuse across different entry points
- Clearer separation of "what to calculate" vs "how to get data"

**File:** `services/valuation.py`

**Functions to extract:**
- `get_validated_eps(ticker, stock, income_stmt)` - from app.py:272
- `calculate_valuation(ticker)` - from app.py:894
- Fair value calculation logic
- EPS averaging logic

**New interface:**
```python
class ValuationService:
    def __init__(self, yahoo_service, sec_data_manager, data_manager):
        ...

    def calculate_fair_value(self, ticker) -> dict
    def get_eps_history(self, ticker) -> list[float]
    def get_valuation_summary(self, ticker) -> dict
```

### 2.3 Create services/holdings.py

**Why:** Holdings calculations (FIFO, cost basis, gains) are embedded in route handlers. Extracting enables:
- Unit testing of financial calculations
- Reuse in multiple routes
- Clearer business logic

**File:** `services/holdings.py`

**Functions to extract:**
- `calculate_fifo_cost_basis(ticker, transactions)` - from app.py:225
- `calculate_holdings(confirmed_only)` - from app.py:439
- Gain/loss calculation logic from `api_holdings_analysis()`

**New interface:**
```python
class HoldingsService:
    def __init__(self, data_manager):
        ...

    def get_holdings(self, confirmed_only=False) -> dict
    def calculate_cost_basis(self, ticker, transactions) -> dict
    def get_unrealized_gains(self, holdings, current_prices) -> dict
    def get_sell_candidates(self, holdings, valuations) -> list
```

### 2.4 Create services/recommendations.py

**Why:** The recommendation scoring algorithm is complex and buried in a route handler. Extracting it:
- Makes the algorithm tweakable and testable
- Separates scoring logic from data fetching
- Allows experimentation with different scoring models

**File:** `services/recommendations.py`

**Functions to extract:**
- Scoring logic from `api_recommendations()` - from app.py:2111

**New interface:**
```python
class RecommendationService:
    def __init__(self, weights=None):
        self.weights = weights or SCORING_WEIGHTS

    def score_stock(self, valuation_data: dict) -> float
    def get_top_recommendations(self, valuations: dict, limit=10) -> list
    def explain_score(self, valuation_data: dict) -> list[str]
```

### 2.5 Create services/screener.py

**Why:** Screener logic (filtering, sorting, background updates) is complex. Extracting it:
- Separates the "engine" from the API layer
- Makes progress tracking cleaner
- Enables different update strategies

**File:** `services/screener.py`

**Functions to extract:**
- `run_screener(index_name)` - from app.py:1075
- `run_quick_price_update(index_name)` - from app.py:1316
- `run_smart_update(index_name)` - from app.py:1517
- `run_global_refresh()` - from app.py:1795
- Progress tracking globals and logic

---

## Phase 3: Route Organization with Blueprints (Priority: High, Effort: Medium)

### 3.1 Create routes/ directory structure

**Why:** Flask Blueprints allow logical grouping of routes. Benefits:
- Smaller, focused files
- Clear URL namespace ownership
- Easier to find relevant code
- Can add route-specific middleware

**Structure:**
```
routes/
├── __init__.py          # Register all blueprints
├── holdings.py          # /api/holdings*
├── transactions.py      # /api/transactions*
├── screener.py          # /api/screener*
├── recommendations.py   # /api/recommendations
├── valuation.py         # /api/valuation*
├── refresh.py           # /api/refresh
├── data.py              # /api/data-status, /api/indices
└── sec.py               # /api/sec*
```

### 3.2 Blueprint Implementation

**Example: routes/holdings.py**
```python
from flask import Blueprint, jsonify
from services.holdings import HoldingsService

holdings_bp = Blueprint('holdings', __name__, url_prefix='/api')

@holdings_bp.route('/holdings')
def get_holdings():
    service = HoldingsService(data_manager)
    return jsonify(service.get_holdings())

@holdings_bp.route('/holdings-analysis')
def get_holdings_analysis():
    service = HoldingsService(data_manager)
    return jsonify(service.get_holdings_with_analysis())
```

**routes/__init__.py:**
```python
from .holdings import holdings_bp
from .transactions import transactions_bp
from .screener import screener_bp
# ... etc

def register_blueprints(app):
    app.register_blueprint(holdings_bp)
    app.register_blueprint(transactions_bp)
    app.register_blueprint(screener_bp)
    # ... etc
```

**Updated app.py (~50 lines):**
```python
from flask import Flask
from routes import register_blueprints

app = Flask(__name__)
register_blueprints(app)

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True, port=8080)
```

---

## Phase 4: Frontend Modularization (Priority: Medium, Effort: Medium)

### 4.1 Create static/js/ structure

**Why:** Single 2,900-line JavaScript file is hard to navigate. Splitting by page:
- Easier to find relevant code
- Can lazy-load page scripts
- Reduces merge conflicts
- Clearer ownership

**Structure:**
```
static/js/
├── app.js               # Entry point, initialization, tab routing
├── api.js               # All API calls
├── state.js             # Global state management
├── utils.js             # Shared utilities
│
└── pages/
    ├── summary.js       # loadSummary, renderSummary, renderPerformance
    ├── holdings.js      # loadHoldings, renderHoldings, renderSellRecommendations
    ├── profit.js        # loadProfitTimeline, renderProfitTimeline
    ├── transactions.js  # Form handling, CRUD operations
    ├── research.js      # runValuation, renderValuation
    ├── screener.js      # loadScreener, renderScreener, sorting/filtering
    ├── recommendations.js # loadRecommendations, rendering
    └── datasets.js      # loadDatasets, refresh status
```

### 4.2 Create api.js

**Why:** API calls scattered everywhere with inconsistent error handling. Centralizing:
- Single source of truth for endpoints
- Consistent error handling
- Easy to add auth headers later
- Mockable for testing

**File:** `static/js/api.js`
```javascript
const API_BASE = '/api';

async function apiCall(endpoint, options = {}) {
    const response = await fetch(`${API_BASE}${endpoint}`, options);
    if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
    }
    return response.json();
}

export const api = {
    // Holdings
    getHoldings: () => apiCall('/holdings'),
    getHoldingsAnalysis: () => apiCall('/holdings-analysis'),

    // Transactions
    getTransactions: () => apiCall('/transactions'),
    createTransaction: (data) => apiCall('/transactions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    }),
    updateTransaction: (id, data) => apiCall(`/transactions/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    }),
    deleteTransaction: (id) => apiCall(`/transactions/${id}`, { method: 'DELETE' }),

    // Screener
    getScreener: (index = 'all') => apiCall(`/screener?index=${index}`),
    getScreenerProgress: () => apiCall('/screener/progress'),
    startScreener: (index) => apiCall('/screener/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ index })
    }),
    stopScreener: () => apiCall('/screener/stop', { method: 'POST' }),

    // Recommendations
    getRecommendations: () => apiCall('/recommendations'),

    // Valuation
    getValuation: (ticker) => apiCall(`/valuation/${ticker}`),
    refreshValuation: (ticker) => apiCall(`/valuation/${ticker}/refresh`, { method: 'POST' }),

    // Summary
    getSummary: () => apiCall('/summary'),
    getPerformance: () => apiCall('/performance'),
    getPrices: () => apiCall('/prices'),

    // Refresh
    globalRefresh: () => apiCall('/refresh', { method: 'POST' }),

    // Data status
    getDataStatus: () => apiCall('/data-status'),
};
```

### 4.3 Create utils.js

**Why:** Utility functions like formatMoney, formatTimeAgo are reused everywhere.

**File:** `static/js/utils.js`
```javascript
export function formatMoney(amount) {
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD'
    }).format(amount);
}

export function formatTimeAgo(timestamp) {
    if (!timestamp) return '';
    const updateDate = new Date(timestamp);
    const now = new Date();
    const diffMs = now - updateDate;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return 'now';
    if (diffMins < 60) return `${diffMins}m`;
    if (diffHours < 24) return `${diffHours}h`;
    return `${diffDays}d`;
}

export function formatDate(dateStr) {
    if (!dateStr) return '-';
    return new Date(dateStr).toLocaleDateString();
}

export function formatPercent(value, showSign = true) {
    if (value === null || value === undefined) return 'N/A';
    const sign = showSign && value > 0 ? '+' : '';
    return `${sign}${value.toFixed(1)}%`;
}
```

### 4.4 Module Loading Strategy

**Option A: Simple script tags (no build step)**
```html
<script src="/static/js/utils.js"></script>
<script src="/static/js/api.js"></script>
<script src="/static/js/state.js"></script>
<script src="/static/js/pages/summary.js"></script>
<!-- ... etc -->
<script src="/static/js/app.js"></script>
```

**Option B: ES6 modules (modern browsers)**
```html
<script type="module" src="/static/js/app.js"></script>
```

**Option C: Bundle with Vite/esbuild (recommended for production)**
- Single bundled file
- Tree shaking removes unused code
- Minification
- Source maps for debugging

---

## Phase 5: CSS Organization (Priority: Medium, Effort: Low)

### 5.1 Split CSS by purpose

**Why:** 3,000-line CSS file is hard to navigate and leads to specificity wars.

**Structure:**
```
static/css/
├── variables.css        # CSS custom properties (colors, spacing, etc.)
├── base.css             # Reset, typography, body styles
├── layout.css           # Container, header, tabs, grid systems
├── components.css       # Buttons, cards, tables, forms, badges, modals
└── pages.css            # Page-specific overrides
```

### 5.2 variables.css

**Why:** CSS variables are already used but not fully leveraged. Expanding them:
- Easier theming
- Consistent spacing/sizing
- Dark mode becomes trivial

```css
:root {
    /* Colors */
    --color-primary: #007bff;
    --color-success: #28a745;
    --color-danger: #dc3545;
    --color-warning: #ffc107;

    /* Spacing */
    --spacing-xs: 4px;
    --spacing-sm: 8px;
    --spacing-md: 16px;
    --spacing-lg: 24px;
    --spacing-xl: 32px;

    /* Typography */
    --font-size-sm: 0.85em;
    --font-size-base: 1em;
    --font-size-lg: 1.1em;

    /* Borders */
    --border-radius-sm: 4px;
    --border-radius-md: 8px;

    /* Shadows */
    --shadow-sm: 0 1px 3px rgba(0,0,0,0.1);
    --shadow-md: 0 4px 12px rgba(0,0,0,0.15);
}
```

---

## Phase 6: Data Layer Improvements (Priority: Low, Effort: High)

### 6.1 Consider SQLite Migration

**Why:** JSON files work but have limitations:
- `valuations.json` is ~900KB and growing
- No indexing = slow filtering for 5000+ stocks
- Risk of corruption on concurrent writes
- No query capability

**Benefits of SQLite:**
- Fast indexed queries
- ACID transactions
- Built-in aggregations (SUM, AVG, etc.)
- Works with existing Python stdlib

**Tables:**
```sql
CREATE TABLE stocks (
    ticker TEXT PRIMARY KEY,
    name TEXT,
    type TEXT  -- 'stock' or 'index'
);

CREATE TABLE transactions (
    id INTEGER PRIMARY KEY,
    ticker TEXT,
    action TEXT,
    shares INTEGER,
    price REAL,
    gain_pct REAL,
    date TEXT,
    status TEXT,
    FOREIGN KEY (ticker) REFERENCES stocks(ticker)
);

CREATE TABLE valuations (
    ticker TEXT PRIMARY KEY,
    company_name TEXT,
    current_price REAL,
    eps_avg REAL,
    eps_years INTEGER,
    eps_source TEXT,
    estimated_value REAL,
    price_vs_value REAL,
    annual_dividend REAL,
    fifty_two_week_high REAL,
    fifty_two_week_low REAL,
    off_high_pct REAL,
    in_selloff INTEGER,
    selloff_severity TEXT,
    updated TEXT,
    FOREIGN KEY (ticker) REFERENCES stocks(ticker)
);

CREATE TABLE index_membership (
    ticker TEXT,
    index_name TEXT,
    PRIMARY KEY (ticker, index_name)
);
```

**Migration path:**
1. Create SQLite alongside JSON (write to both)
2. Read from SQLite, fall back to JSON
3. Validate data matches
4. Remove JSON dependency

### 6.2 Add Caching Layer

**Why:** Some calculations are expensive and don't change often.

**Options:**
- In-memory cache with TTL (simple, works for single instance)
- Redis (if scaling to multiple workers)

**What to cache:**
- Recommendations (TTL: 5 minutes)
- Holdings analysis (TTL: 1 minute)
- Index ticker lists (TTL: 1 hour)

---

## Implementation Order

### Week 1: Foundation
1. **config.py** - Extract all constants (2 hours)
2. **services/yahoo_finance.py** - Centralize YF calls (3 hours)
3. **Split CSS** into 4-5 files (2 hours)

### Week 2: Core Services
4. **services/valuation.py** - Extract valuation logic (2 hours)
5. **services/holdings.py** - Extract holdings logic (2 hours)
6. **services/recommendations.py** - Extract scoring (2 hours)

### Week 3: Routes
7. **Create routes/ structure** with Blueprints (4 hours)
8. **Slim down app.py** to ~50 lines (included in above)

### Week 4: Frontend
9. **static/js/api.js** - Centralize API calls (2 hours)
10. **static/js/utils.js** - Extract utilities (1 hour)
11. **Split page JS** into modules (4 hours)

### Future
12. SQLite migration (8+ hours)
13. Add unit tests for services (ongoing)
14. Add proper logging (2 hours)

---

## Testing Strategy

After refactoring, add tests for:

**Unit tests (services/):**
- Valuation calculations with known inputs
- FIFO cost basis calculations
- Recommendation scoring algorithm
- Selloff detection logic

**Integration tests (routes/):**
- API endpoints return expected shapes
- Error handling works correctly

**Example test:**
```python
# tests/test_recommendations.py
from services.recommendations import RecommendationService

def test_score_undervalued_with_dividend():
    service = RecommendationService()
    stock = {
        'price_vs_value': -30,  # 30% undervalued
        'dividend_yield': 4.0,
        'off_high_pct': -20,
        'in_selloff': True,
        'selloff_severity': 'moderate'
    }
    score = service.score_stock(stock)
    assert score > 50  # Should be high score

def test_score_overvalued_no_dividend():
    service = RecommendationService()
    stock = {
        'price_vs_value': 20,  # 20% overvalued
        'dividend_yield': 0,
        'off_high_pct': 0,
        'in_selloff': False,
        'selloff_severity': None
    }
    score = service.score_stock(stock)
    assert score < 0  # Should be negative
```

---

## Notes

- Each phase can be done incrementally without breaking the app
- Keep the app running throughout - refactor one piece at a time
- Commit after each logical change
- The existing `data_manager.py` and `sec_data.py` are good examples of the target modularity
