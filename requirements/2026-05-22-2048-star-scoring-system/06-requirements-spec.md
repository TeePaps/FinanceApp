# Requirements Spec: Star Scoring System

**Date:** 2026-05-22
**Requirement ID:** 2026-05-22-2048-star-scoring-system
**Status:** Ready for implementation

---

## 1. Problem Statement

The user wants a new at-a-glance quality rating for tracked stocks. Today, the recommendations tab produces a single numeric score from price/dividend/selloff metrics, but it does not capture fundamental quality signals like earnings momentum, balance sheet strength, or buyback activity. The user wants a 6-criterion star rating system on a dedicated new tab to help evaluate both holdings and watchlist stocks.

## 2. Solution Overview

Build a new "Stars" tab that displays every tracked ticker, rated 0–6 (holdings) or 0–4 (non-holdings), based on six independent boolean criteria. Stars are computed as a new phase of the existing screener batch run and stored in the database. Two sections on the tab: **Holdings** (max 6 stars) and **Watchlist** (max 4 stars). Missing data → criterion unearned (no special UX).

## 3. Functional Requirements

### 3.1 The Six Criteria

| # | Star | Definition | Data Needed | Holdings Only? |
|---|---|---|---|---|
| 1 | Earnings Beat | Most recent reported quarterly EPS > prior analyst consensus estimate | Quarterly earnings history with `epsActual` + `epsEstimate` | No |
| 2 | Fair Value Up | Current `estimated_value` > `estimated_value` from 1 year ago (backfilled on first run from `eps_history`) | EPS history (already stored); snapshot table going forward | No |
| 3 | Dividend Up | Trailing 12-month dividend > TTM dividend from 1 year ago | Full dividend payment history from yfinance | No |
| 4 | Debt-to-Capital ≤ 25% | `(LongTermDebtNoncurrent + DebtCurrent) / (LongTermDebtNoncurrent + DebtCurrent + StockholdersEquity) ≤ 0.25` | SEC EDGAR companyfacts (us-gaap concepts) | No |
| 5 | Share Buybacks Since Buy | Current shares outstanding < shares outstanding at earliest buy date | SEC EDGAR `CommonStockSharesOutstanding` history + transactions table | **Yes** |
| 6 | Undervalued | `current_price < estimated_value` (i.e., existing `price_vs_value < 1.0`) | Existing `valuations` row | **Yes** |

### 3.2 Star Calculation Rules
- Each criterion is binary: earned (1 star) or not earned (0 stars).
- Missing data → not earned (0). No "N/A" state.
- Holdings: rated out of 6. Watchlist (non-holdings): rated out of 4 (criteria 5 & 6 always 0 / not shown).
- A ticker counts as a "holding" when current shares (computed via FIFO in `services/holdings.py`) > 0.

### 3.3 UI Behavior
- New tab labeled **"Stars"** added to main navigation in `templates/index.html`.
- Two sections in tab body:
  - **My Holdings** — sorted by total stars descending, then by ticker.
  - **Watchlist** — sorted by total stars descending, then by ticker.
- Each row shows: ticker, company name, star icons (filled vs. empty up to max), current price, fair value, price-vs-value %.
- Empty stars display as outlined/dim icons so the max-possible is visible.
- Tab loads via `loadStars()` on first switch; subsequent switches reuse cached data unless explicitly refreshed.

### 3.4 Computation Timing
- Stars are computed as a new **Phase 5** in the existing screener batch (`services/screener.py:run_screener()`), after the existing "combining" phase.
- Phase progress is reported via `_progress['phase'] = 'stars'` for the UI progress bar.
- The Stars tab reads pre-computed values; no on-demand calculation.

### 3.5 Backfill
- On first run, historical comparisons (criteria 2 and 3) are computed by recomputing from existing data:
  - Fair value from 1 year ago: 8-year EPS average ending 1 year ago, from `eps_history` table.
  - Dividend from 1 year ago: trailing 12-month dividend total ending 1 year ago, from yfinance `Ticker.dividends` Series.
- Snapshots are also written to the new history tables on every screener run for future use.

## 4. Technical Requirements

### 4.1 Database Schema (`database.py`)
Add to `_init_public_database()` (around line 242, before index creation):

```sql
CREATE TABLE IF NOT EXISTS star_ratings (
    ticker TEXT PRIMARY KEY,
    earnings_beat INTEGER NOT NULL DEFAULT 0,
    fair_value_up INTEGER NOT NULL DEFAULT 0,
    dividend_up INTEGER NOT NULL DEFAULT 0,
    debt_to_capital_low INTEGER NOT NULL DEFAULT 0,
    shares_buyback INTEGER NOT NULL DEFAULT 0,
    undervalued INTEGER NOT NULL DEFAULT 0,
    total_stars INTEGER NOT NULL DEFAULT 0,
    is_holding INTEGER NOT NULL DEFAULT 0,
    updated TEXT,
    FOREIGN KEY (ticker) REFERENCES tickers(ticker)
);

CREATE TABLE IF NOT EXISTS valuation_history (
    ticker TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    estimated_value REAL,
    eps_avg REAL,
    annual_dividend REAL,
    PRIMARY KEY (ticker, snapshot_date)
);

CREATE TABLE IF NOT EXISTS dividend_history (
    ticker TEXT NOT NULL,
    year INTEGER NOT NULL,
    annual_dividend REAL,
    PRIMARY KEY (ticker, year)
);

CREATE TABLE IF NOT EXISTS shares_outstanding_history (
    ticker TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    shares REAL,
    source TEXT,
    PRIMARY KEY (ticker, as_of_date)
);

CREATE TABLE IF NOT EXISTS balance_sheet (
    ticker TEXT PRIMARY KEY,
    long_term_debt REAL,
    short_term_debt REAL,
    stockholders_equity REAL,
    debt_to_capital REAL,
    as_of_date TEXT,
    source TEXT,
    updated TEXT
);
```

Add CRUD helpers to `database.py`:
- `update_star_rating(ticker, dict)` and `bulk_update_star_ratings(dict)`
- `get_star_ratings()` (returns all, joined with valuations + holdings flag)
- `snapshot_valuation(ticker, date, estimated_value, eps_avg, annual_dividend)`
- `snapshot_dividend_year(ticker, year, annual_dividend)`
- `snapshot_shares_outstanding(ticker, as_of_date, shares, source)`
- `update_balance_sheet(ticker, dict)`
- `get_balance_sheet(ticker)`
- `get_first_buy_date(ticker)` → wraps `SELECT MIN(date) FROM transactions WHERE ticker = ? AND action = 'buy'`

### 4.2 Provider System (`services/providers/`)

**`base.py`** — add to `DataType` enum and new abstract interfaces + dataclasses:
```python
class DataType(Enum):
    # existing...
    ANALYST_ESTIMATES = "analyst_estimates"
    BALANCE_SHEET = "balance_sheet"
    SHARES_OUTSTANDING = "shares_outstanding"

@dataclass
class AnalystEstimateData:
    ticker: str
    most_recent_quarter: str
    eps_actual: Optional[float]
    eps_estimate: Optional[float]
    surprise_percent: Optional[float]
    history: List[Dict]  # last N quarters

@dataclass
class BalanceSheetData:
    ticker: str
    long_term_debt: Optional[float]
    short_term_debt: Optional[float]
    stockholders_equity: Optional[float]
    as_of_date: str

@dataclass
class SharesOutstandingData:
    ticker: str
    current: Optional[float]
    history: List[Dict]  # [{date, shares, source}, ...]

class AnalystEstimateProvider(BaseProvider):
    @abstractmethod
    def fetch_analyst_estimates(self, ticker: str) -> ProviderResult: ...

class BalanceSheetProvider(BaseProvider):
    @abstractmethod
    def fetch_balance_sheet(self, ticker: str) -> ProviderResult: ...

class SharesOutstandingProvider(BaseProvider):
    @abstractmethod
    def fetch_shares_outstanding(self, ticker: str) -> ProviderResult: ...
```

**`yfinance_provider.py`** — extend with `YFinanceAnalystEstimateProvider` (uses `Ticker.earnings_history`) and helper to fetch full dividend history (extend existing dividend provider's logic to return per-year totals).

**`sec_provider.py`** — extend with `SECBalanceSheetProvider` and `SECSharesOutstandingProvider` using SEC EDGAR companyfacts for us-gaap concepts:
- `LongTermDebtNoncurrent` (or `LongTermDebt`)
- `DebtCurrent` (or `ShortTermBorrowings`)
- `StockholdersEquity`
- `CommonStockSharesOutstanding`

**`registry.py`** — register new providers, add `fetch_analyst_estimates()`, `fetch_balance_sheet()`, `fetch_shares_outstanding()` methods to `DataOrchestrator`, extend `_by_type` and `get_providers_ordered()`.

**`config.py`** — add provider ordering lists for the 3 new types. Suggested:
- `analyst_estimate_providers = ['yfinance', 'fmp']`
- `balance_sheet_providers = ['sec', 'yfinance', 'fmp']`
- `shares_outstanding_providers = ['sec', 'yfinance']`

**`__init__.py`** — export the new dataclasses, interfaces, and provider classes.

### 4.3 Star Calculation Module
**Create `services/stars.py`** with:
```python
def calculate_stars(ticker: str, valuation: dict, is_holding: bool) -> dict:
    """Returns {earnings_beat, fair_value_up, dividend_up, debt_to_capital_low,
               shares_buyback, undervalued, total_stars}"""

def _check_earnings_beat(ticker) -> bool: ...
def _check_fair_value_up(ticker, current_estimated_value) -> bool: ...
def _check_dividend_up(ticker, current_annual_dividend) -> bool: ...
def _check_debt_to_capital_low(ticker) -> bool: ...
def _check_shares_buyback_since_buy(ticker, first_buy_date) -> bool: ...
def _check_undervalued(price_vs_value) -> bool: ...

def calculate_all_star_ratings() -> dict:
    """Loops tickers, calls calculate_stars, bulk_updates star_ratings table."""
```

### 4.4 Screener Phase Integration
**`services/screener.py`** — after Phase 4 (combining) completes at ~line 522, add:
```python
_progress['phase'] = 'stars'
_progress['status'] = 'Calculating star ratings'
from services.stars import calculate_all_star_ratings
calculate_all_star_ratings()
```

### 4.5 API Route
**Create `routes/stars.py`**:
```python
from flask import Blueprint, jsonify
import database as db
from services.holdings import get_current_holdings_set

stars_bp = Blueprint('stars', __name__, url_prefix='/api')

@stars_bp.route('/stars')
def api_stars():
    ratings = db.get_star_ratings()  # joined with valuations
    holdings_set = get_current_holdings_set()  # set of tickers with shares > 0
    holdings = [r for r in ratings if r['ticker'] in holdings_set]
    watchlist = [r for r in ratings if r['ticker'] not in holdings_set]
    # Sort by total_stars desc, ticker asc
    holdings.sort(key=lambda r: (-r['total_stars'], r['ticker']))
    watchlist.sort(key=lambda r: (-r['total_stars'], r['ticker']))
    return jsonify({
        'success': True,
        'data': {'holdings': holdings, 'watchlist': watchlist}
    })
```

Register in **`routes/__init__.py`**:
```python
from .stars import stars_bp
app.register_blueprint(stars_bp)
```

### 4.6 Frontend
**`templates/index.html`** — add tab button in `<nav class="tabs">` (around line 71):
```html
<button class="tab-btn" onclick="showTab('stars')">Stars</button>
```
And tab content container:
```html
<div id="stars-tab" class="tab-content">
    <h2>Star Ratings</h2>
    <section id="stars-holdings"><h3>My Holdings (out of 6)</h3><div id="stars-holdings-list"></div></section>
    <section id="stars-watchlist"><h3>Watchlist (out of 4)</h3><div id="stars-watchlist-list"></div></section>
</div>
```

**`static/app.js`** — in `showTab()` (around line 657) add:
```javascript
else if (tabName === 'stars') loadStars();
```
Implement `loadStars()`:
```javascript
async function loadStars() {
    const res = await fetch('/api/stars');
    const json = await res.json();
    renderStars('stars-holdings-list', json.data.holdings, 6);
    renderStars('stars-watchlist-list', json.data.watchlist, 4);
}
function renderStars(containerId, rows, maxStars) {
    // Render rows with filled/empty star icons up to maxStars
}
```

**`static/css/pages.css`** — add `.star-filled` and `.star-empty` classes (use Unicode ★ / ☆ or CSS-styled spans).

### 4.7 Config (`config.py`)
Add constants:
```python
DEBT_TO_CAPITAL_THRESHOLD = 0.25  # criterion 4
EARNINGS_HISTORY_QUARTERS = 4     # how many quarters to fetch for earnings beat check
SHARES_BUYBACK_TOLERANCE = 0.0    # require strict decrease
```

## 5. Implementation Hints & Patterns

- **Follow existing provider patterns** when adding `AnalystEstimateProvider`, `BalanceSheetProvider`, `SharesOutstandingProvider`. Mirror the shape of `EPSProvider` in `services/providers/base.py:161-244`.
- **SEC EDGAR companyfacts endpoint:** `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json`. The existing `sec_data.py` already fetches this; just extract additional concepts. Respect the 10 req/sec rate limit (already enforced).
- **yfinance earnings history:** `yf.Ticker(ticker).earnings_history` returns a DataFrame with `epsActual`, `epsEstimate`, `surprisePercent` columns. May break — wrap in try/except.
- **Holdings check:** Use `services/holdings.py:get_current_holdings()` (already FIFO-aware) to determine which tickers have shares > 0. Build a set for the API endpoint.
- **Snapshot strategy:** Snapshot tables (`valuation_history`, `dividend_history`, `shares_outstanding_history`) are append-only with `UNIQUE` primary keys. Use `INSERT OR REPLACE` so re-running the screener on the same day doesn't duplicate.
- **Backfill on first run:** When `valuation_history` is empty for a ticker, compute the prior-year estimated_value from `eps_history` directly (don't depend on a snapshot existing). After first run, snapshots accumulate.

## 6. Acceptance Criteria

1. **Database**: 5 new tables (`star_ratings`, `valuation_history`, `dividend_history`, `shares_outstanding_history`, `balance_sheet`) exist after app startup.
2. **Providers**: New `AnalystEstimateProvider`, `BalanceSheetProvider`, `SharesOutstandingProvider` interfaces are defined; at least one concrete implementation per interface (yfinance for analyst, SEC for balance sheet + shares) is registered.
3. **Screener phase**: Running the screener produces a "Calculating star ratings" status update and populates the `star_ratings` table for every ticker in the run.
4. **API**: `GET /api/stars` returns `{success, data: {holdings: [...], watchlist: [...]}}`. Holdings sorted by stars desc. Each row includes the 6 individual booleans + total + ticker metadata.
5. **UI**: New "Stars" tab visible in nav. Tab body shows two sections (Holdings, Watchlist) with star icons. Empty stars rendered to indicate max possible.
6. **First-run backfill**: Criteria 2 (fair value up) and 3 (dividend up) produce meaningful results on the very first screener run after this feature deploys (not 0 for everyone).
7. **Holdings-only criteria**: Criteria 5 & 6 are always 0 for non-holdings; can be earned for holdings.
8. **Missing data**: A ticker with no analyst estimates from any provider gets `earnings_beat = 0`, not an error.
9. **Refactoring checklist**: After implementation, the verification checklist in CLAUDE.md passes (syntax, `import app`, screener smoke test).

## 7. Assumptions

- **Provider availability**: yfinance `earnings_history` and SEC EDGAR companyfacts are reachable. If yfinance changes its API again (a known risk), the earnings-beat criterion may temporarily break — graceful degradation handles this.
- **No new external API keys required**: SEC EDGAR is free and already integrated; yfinance is free; FMP API key already present (and used as fallback only).
- **EPS history depth**: The `eps_history` table contains at least 9 years of EPS for backfill of Criterion 2 to work (current 8-year average + 1-year shift). Tickers with shorter history get 0 for this criterion.
- **Star icons**: Plain Unicode ★/☆ acceptable for v1; can upgrade to SVG later.
- **Refresh cadence**: Stars are only as fresh as the most recent screener run. No real-time recalculation. User triggers fresh values by running the screener.
- **Buy date**: "Time it was bought" interpreted as `MIN(date) WHERE action='buy'` per ticker. If a user has sold and re-bought, the earliest buy date is still used (not the most recent re-entry).
- **TTM dividend**: For Criterion 3 backfill, "annual dividend 1 year ago" = sum of yfinance dividend payments in the 365 days ending 365 days before today.
