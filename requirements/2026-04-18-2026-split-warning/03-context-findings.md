# Phase 3: Context Findings

Concrete file paths, line numbers, and code patterns for implementing Split Warning.

## 1. Provider Interface Pattern — `services/providers/base.py`

**`DataType` enum** (lines 16-26) — add `SPLIT = "split"` after `DIVIDEND`.

**`ProviderResult` dataclass** (lines 28-53) — standard return shape: `success`, `data`, `source`, `error`, `cached`, `timestamp`, `metadata`.

**`DividendProvider` interface** (lines 306-327) — **closest template** (splits are discrete events, like dividends):
```python
class DividendProvider(BaseProvider):
    @property
    def data_types(self) -> List[DataType]:
        return [DataType.DIVIDEND]
    @abstractmethod
    def fetch_dividends(self, ticker: str) -> ProviderResult: ...
```

**New interface to add** (after `DividendProvider`):
```python
@dataclass
class SplitData:
    ticker: str
    source: str
    splits: List[Dict]   # [{"date": "YYYY-MM-DD", "ratio": float}]
    timestamp: datetime = field(default_factory=datetime.now)

class SplitProvider(BaseProvider):
    @property
    def data_types(self): return [DataType.SPLIT]
    @abstractmethod
    def fetch_splits(self, ticker: str) -> ProviderResult: ...
```

## 2. Registry & Orchestrator — `services/providers/registry.py`

- `_by_type` dict (lines 38-47) — add `DataType.SPLIT: []`.
- `get_providers_ordered` switch (lines 113-129) — add `elif data_type == DataType.SPLIT: order = config.split_providers`.
- Clone `fetch_dividends` (lines 660-725) as `fetch_splits` — same circuit-breaker + rate-limit + timeout + fallback pattern.
- `init_providers` (lines 1219-1258) — register `YFinanceSplitProvider()`.

## 3. Provider Config — `services/providers/config.py`

- `dividend_providers` (lines 78-81) — clone as `split_providers: List[str] = field(default_factory=lambda: ["yfinance"])`.
- Cache knobs (lines 86-99) — add `split_cache_days: int = 7` (splits change rarely; longer cache is fine).

## 4. yfinance Split Access — `services/providers/yfinance_provider.py`

- Dividend fetch (lines 846-923) = template.
- yfinance exposes splits via `yf.Ticker(t).splits` (pandas Series, `date → ratio`).
- Build `YFinanceSplitProvider` that returns `SplitData` with `[{date, ratio}]` list.

## 5. Schema + DB — `database.py`

- `eps_history` schema template (lines 171-185).
- **Proposed new table** `split_history`:
```sql
CREATE TABLE IF NOT EXISTS split_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    split_date TEXT NOT NULL,
    split_ratio REAL,
    source TEXT,
    fetched_at TEXT,
    FOREIGN KEY (ticker) REFERENCES tickers(ticker) ON DELETE CASCADE,
    UNIQUE(ticker, split_date)
);
```
- Insert pattern: mirror `add_new_eps_years()` — `INSERT OR IGNORE` on UNIQUE constraint.
- Optional: add `last_split_check` column to `tickers` or `valuations` to avoid redundant fetches.

## 6. Screener Phases — `services/screener.py`

Existing 4 phases: EPS (line 171+), Dividends (line 190+), Prices (line 210+), Valuations (line 250+).

New "Splits" phase — insert **after Dividends, before Prices**. Progress dict update: `_progress['phase'] = 'splits'`. Calls `orchestrator.fetch_splits(ticker)` in the per-ticker loop, persists to `split_history`.

## 7. Valuation Service — `services/valuation.py`

`calculate_valuation` (lines 94-199) returns dict with `ticker`, `eps_data`, `estimated_value`, `price_vs_value`, `selloff`, etc. (lines 176-192).

Add `split_warning` field to the return dict — computed by reading `split_history` for the ticker and filtering to the 8-year lookback window:
```python
split_warning = {
    "active": True,
    "count": N,
    "most_recent_date": "YYYY-MM-DD",
    "splits": [{date, ratio}, ...],
    "note": "N stock split(s) in 8-year EPS window — fair value may be skewed"
}
```

Refresh endpoint (`/api/valuation/<ticker>/refresh`) must also populate `split_warning`.

## 8. Recommendations Service — `services/recommendations.py`

- `score_stock` (lines 21-76) consumes `price_vs_value`, `annual_dividend`, `off_high_pct`, `in_selloff`, `selloff_severity`. **No scoring change** — split is informational only (per Q1).
- `explain_score` (lines 79-130) — no reason added for splits.
- `get_top_recommendations` (lines 133-200) — **do not filter** by split (per Q4); just pass the `split_warning` field through.

## 9. Frontend — Analyze Page Render (`static/app.js` ~lines 2081-2160)

`renderValuation()` builds HTML for `#research-results`. Existing precedent: `data-source-badge`, `data-warning` div for insufficient-EPS-years warning. New `<div class="split-warning-note">` with badge goes alongside the data-warning block.

## 10. Frontend — Recommendations Render (`static/app.js` ~lines 3314-3438)

`loadRecommendations()` renders cards. Existing precedent: `${selloffClass}` on card and `${selloff_severity}` badge. Add `.split-badge` inline next to the company name/ticker.

## 11. CSS — `static/css/components.css`

Selloff badge (~lines 370-395) is the pattern. Add:
```css
.split-warning-badge, .split-badge { background:#6f42c1; color:#fff; ... }
.split-warning-note { background:#f3e5f5; border-left:4px solid #6f42c1; ... }
```

## Config (app-level) — `config.py`

Add new constants:
```python
SPLIT_WARNING_LOOKBACK_YEARS = RECOMMENDED_EPS_YEARS   # 8
SPLIT_WARNING_MIN_RATIO = 1.5                          # ignore trivial splits?
```

## Integration Points Summary

| # | File | Change |
|---|------|--------|
| 1 | `services/providers/base.py` | `DataType.SPLIT`, `SplitData`, `SplitProvider` |
| 2 | `services/providers/registry.py` | `_by_type`, `fetch_splits`, `get_providers_ordered`, `init_providers` |
| 3 | `services/providers/config.py` | `split_providers`, `split_cache_days` |
| 4 | `services/providers/yfinance_provider.py` | `YFinanceSplitProvider` class |
| 5 | `services/providers/__init__.py` | Export `SplitProvider`, `SplitData`, `YFinanceSplitProvider` |
| 6 | `database.py` | `split_history` table + CRUD helpers |
| 7 | `services/screener.py` | New splits phase |
| 8 | `services/valuation.py` | Inject `split_warning` into returned dict |
| 9 | `routes/valuation.py` | Pass through in refresh route too |
| 10 | `services/recommendations.py` | Pass through (no score impact) |
| 11 | `static/app.js` | Render in `renderValuation()` and `loadRecommendations()` |
| 12 | `static/css/components.css` | `.split-warning-badge`, `.split-warning-note`, `.split-badge` |
| 13 | `config.py` | `SPLIT_WARNING_LOOKBACK_YEARS`, optional min-ratio threshold |
