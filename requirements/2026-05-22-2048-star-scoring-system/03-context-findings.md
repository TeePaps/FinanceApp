# Phase 3: Context Findings

## Architecture Summary

### Existing Patterns to Reuse
| Concern | File | Pattern |
|---|---|---|
| Screener phase | `services/screener.py:119-553` | Phases run sequentially in `run_screener()`. Each updates `_progress['phase']`. Add Phase 5 "stars" after Phase 4 "combining" (line 380-553). |
| Tab UI | `templates/index.html:58-72` + `static/app.js:608-658` | Add tab button in `<nav class="tabs">`, add `<div id="stars-tab" class="tab-content">`, add `else if (tabName === 'stars') loadStars()` in `showTab()`. |
| API route | `routes/screener.py:1-43` + `routes/__init__.py:17-26` | Create `routes/stars.py` with `stars_bp = Blueprint('stars', __name__, url_prefix='/api')`. Register in `routes/__init__.py`. |
| DB table | `database.py:86-287` | Use `CREATE TABLE IF NOT EXISTS` in `_init_public_database()`. ALTER TABLE migrations use `PRAGMA table_info()` (lines 251-279). |
| Provider interface | `services/providers/base.py:161-244` | Add `AnalystEstimateProvider`, `BalanceSheetProvider`, `SharesOutstandingProvider` abstract classes. Add DataType enum values. |
| Holdings query | `database.py:1545-1550` | `SELECT MIN(date) FROM transactions WHERE ticker = ? AND action = 'buy'` |

### What Doesn't Exist Yet (and where to add it)

**1. Analyst Estimates (criterion 1)** — NOT FETCHED. Options:
- **yfinance** has `Ticker(t).earnings_estimate` (DataFrame with `avg`, `low`, `high` columns by period). Not currently used by `yfinance_provider.py`. Free.
- **FMP** has `/v3/analyst-estimates/{ticker}` endpoint. User has FMP API key (data_private/secrets.json exists, `fmp_provider.py:14` imports `get_fmp_api_key()`). Already paid.

**2. Balance Sheet / Debt (criterion 4)** — NOT FETCHED. Options:
- **yfinance** has `Ticker(t).balance_sheet` / `.quarterly_balance_sheet` (long-term debt, short-term debt, stockholders equity).
- **FMP** has `/v3/balance-sheet-statement/{ticker}`.
- **SEC EDGAR companyfacts** — exposes us-gaap concepts: `Liabilities`, `LongTermDebtNoncurrent`, `StockholdersEquity`, `CommonStockSharesOutstanding`. Free, already integrated for EPS (`sec_provider.py:46-119`). Reuse pattern from `sec_data.py`.

**3. Dividend History (criterion 3)** — Current annual dividend stored (`valuations.annual_dividend`). yfinance `.dividends` returns full Series of all dividends ever paid (already used in `yfinance_provider.py:868-913` but only summed for annual). Need to extend to retain year-by-year totals in new `dividend_history` table.

**4. Shares Outstanding (criterion 5)** — NOT STORED. Options:
- **yfinance** `.info['sharesOutstanding']` (current only).
- **yfinance** `.balance_sheet` → `Ordinary Shares Number` or `Share Issued` rows give quarterly history.
- **SEC EDGAR** `CommonStockSharesOutstanding` gives per-filing history.

**5. Fair Value Historical (criterion 2)** — Only current `estimated_value` stored. EPS history exists in `eps_history` table. Can backfill: recompute fair value at any prior date using `eps_history` (shifted-window 8-year avg) + dividend from that period.

### Provider Recommendations
- **Analyst estimates**: yfinance free path (no extra cost, simple). FMP fallback if yfinance breaks.
- **Balance sheet**: SEC EDGAR (free, authoritative, already integrated infrastructure). FMP/yfinance fallback.
- **Dividend history**: yfinance (already in use, just extend extraction).
- **Shares outstanding**: SEC EDGAR for historical, yfinance for current snapshot.

### Holdings-Only Criteria
Criteria 5 (share buybacks since buy) and 6 (undervalued for holdings) only apply to user's holdings. Need decision: does new tab show non-holdings (max 4 stars) or only holdings (max 6 stars)?

### Current Recommendations / Undervalued Logic
- `services/recommendations.py:21` `score_stock()` already computes a composite score using `price_vs_value` (undervaluation), dividend yield, selloff. The "undervalued" criterion (6) can reuse: a ticker is undervalued when `price_vs_value < some threshold` (likely already in config.py).
- Need to confirm threshold. Default: `price_vs_value < 1.0` (price below fair value).

### Files That Will Need Changes
1. `database.py` — add 4 new tables (`star_ratings`, `valuation_history`, `dividend_history`, `shares_outstanding_history`)
2. `services/providers/base.py` — add 3 new provider interfaces, 3 new DataTypes, 3 new dataclasses
3. `services/providers/yfinance_provider.py` — implement new providers
4. `services/providers/sec_provider.py` — implement balance sheet, shares outstanding from EDGAR
5. `services/providers/registry.py` — register new providers, add `fetch_xxx` methods to `DataOrchestrator`
6. `services/providers/config.py` — add provider ordering for new types
7. `services/providers/__init__.py` — export new types
8. `services/screener.py` — add Phase 5 (stars calculation) after line 522
9. `services/stars.py` (NEW) — star calculation logic (6 criteria functions, aggregate)
10. `routes/stars.py` (NEW) — Flask blueprint for `/api/stars`
11. `routes/__init__.py` — register stars blueprint
12. `templates/index.html` — add tab button + tab content div
13. `static/app.js` — add `loadStars()`, render function, add to `showTab()`
14. `static/css/pages.css` — star icon styling
15. `config.py` — add star thresholds (debt-to-capital ≤ 25%, undervalued price ratio, etc.)
