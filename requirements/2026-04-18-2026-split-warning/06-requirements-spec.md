# Split Warning — Requirements Specification

**Status:** Complete — ready for implementation
**Feature slug:** `split-warning`
**Owner request date:** 2026-04-18

---

## 1. Problem Statement

When a stock undergoes a split, the share price is mechanically adjusted, but the historical EPS figures pulled from SEC EDGAR (via `sec_data.py` / `sec_provider.py` into the `eps_history` table) are **not** retroactively split-adjusted by our pipeline. Because the FinanceApp's fair value is computed as:

> `(8-year avg EPS + annual dividend) × 10`

a recent split can severely distort the 8-year EPS average — causing a stock to look dramatically under- or overvalued when it isn't. Users need a clear signal on the Analyze page and in the Recommendations list so they can discount or verify the valuation before acting.

## 2. Solution Overview

Add a **Split Warning** signal that:

1. Fetches stock split history via a new, pluggable **`SplitProvider`** interface (matching the existing `PriceProvider` / `EPSProvider` / `DividendProvider` pattern).
2. Persists splits in a new **`split_history`** table in `public.db`.
3. Refreshes split data (a) as a new phase of the background screener and (b) on manual `/api/valuation/<ticker>/refresh` calls.
4. Computes a `split_warning` field for the valuation dict — with severity tiers based on recency.
5. Renders an informational badge/note on the **Analyze page** and a compact badge on **Recommendation cards**.
6. Does **not** change the score, filter recommendations, or auto-adjust EPS/fair value. (Pure informational warning — deferred for a later iteration.)

## 3. Functional Requirements

### FR-1 Split detection
- The system MUST fetch split history for each tracked ticker via the provider system.
- Lookback window = `SPLIT_WARNING_LOOKBACK_YEARS = RECOMMENDED_EPS_YEARS` (i.e. 8 years).
- A ticker MUST be flagged with an active `split_warning` when **at least one split in the lookback window has a ratio ≥ `SPLIT_WARNING_MIN_RATIO` (= 1.1)**.
- All splits returned by providers MUST be persisted, regardless of ratio (the threshold applies only to badge activation, not storage).

### FR-2 Severity tiers
- `recent` — at least one qualifying split within the last 3 years (distorts EPS average significantly).
- `historical` — qualifying splits only in years 3–8 of the lookback (smaller but non-zero distortion).
- Tier is surfaced on the valuation dict as `split_warning.severity`.

### FR-3 Provider fallback chain
- Four providers MUST be implemented and registered, in this default order:
  1. `YFinanceSplitProvider` — primary (`yf.Ticker(t).splits`).
  2. `FMPSplitProvider` — FMP historical-stock-splits endpoint.
  3. `AlpacaSplitProvider` — Alpaca corporate-actions API.
  4. `SECSplitProvider` — best-effort; SEC does not expose splits via XBRL `companyfacts`. Implementation MAY return an empty success result. It exists so the interface is complete and can be filled in later via 8-K parsing.
- The orchestrator MUST apply circuit breaker, timeout, and rate limit to split fetches (cloned from dividend pattern).

### FR-4 Persistence
- Create table `split_history` in `public.db` with columns: `id`, `ticker`, `split_date`, `split_ratio`, `source`, `fetched_at`, `UNIQUE(ticker, split_date)`, `FOREIGN KEY (ticker) REFERENCES tickers(ticker) ON DELETE CASCADE`.
- Upserts use `INSERT OR IGNORE` on the UNIQUE constraint (match `add_new_eps_years` pattern).

### FR-5 Screener integration
- A new **"splits"** phase MUST be added to `services/screener.py`, inserted **between Dividends (phase 2) and Prices (phase 3)**.
- Progress reporting MUST update `_progress['phase'] = 'splits'` for UI polling.
- Split data is fetched per ticker, persisted, and errors are logged but do not halt the run.

### FR-6 Manual refresh
- `POST /api/valuation/<ticker>/refresh` MUST also call `orchestrator.fetch_splits(ticker)`, upsert the `split_history` table, and include the recomputed `split_warning` in the response.

### FR-7 Valuation response shape
- `services/valuation.py::calculate_valuation` MUST add a `split_warning` key to its returned dict. When active:

```python
split_warning = {
    "active": True,
    "severity": "recent" | "historical",
    "count": int,                            # number of qualifying splits in window
    "most_recent_date": "YYYY-MM-DD",
    "most_recent_ratio": float,
    "splits": [{"date": "YYYY-MM-DD", "ratio": float}, ...],  # all qualifying splits in window
    "note": "N stock split(s) in EPS window — fair value may be skewed"
}
```
When no qualifying splits: `split_warning = None` (or key absent).

### FR-8 Recommendations behavior
- `services/recommendations.py::get_top_recommendations` MUST pass through `split_warning` unchanged.
- `score_stock` and `explain_score` MUST NOT be altered in this iteration (per Q1, Q4 — pure informational).
- Flagged stocks remain in the list; they are badged, not filtered.

### FR-9 UI — Analyze page
- In `static/app.js::renderValuation()` (after the existing `data-warning` block), render a `<div class="split-warning-note">` containing:
  - The badge text: "⚠ Stock Split Warning" (tier class `recent` or `historical`).
  - A human-readable line: `"N split(s) since YYYY — most recent YYYY-MM-DD (R:1). EPS averages may be distorted; verify before acting."`
  - A compact list of the qualifying splits.
- Hidden when `split_warning` is null/absent.

### FR-10 UI — Recommendations
- In `static/app.js::loadRecommendations()` rendering loop, add a `.split-badge` (`recent` or `historical` modifier) next to existing badges on the recommendation card.
- Tooltip (`title` attribute) MUST show `split_warning.note`.
- No change to scoring, ordering, or filtering.

### FR-11 Styling
- New CSS rules in `static/css/components.css`:
  - `.split-badge` / `.split-warning-badge` / `.split-warning-note`.
  - Tier modifiers: `.recent` (darker purple, higher visual weight), `.historical` (lighter).
- Color palette: purple family (`#6f42c1`) to differentiate from selloff (red/amber) and staleness (gray).

## 4. Technical Requirements — Exact File Changes

| # | File | Change |
|---|------|--------|
| 1 | `services/providers/base.py` | Add `DataType.SPLIT` to enum. Add `SplitData` dataclass. Add `SplitProvider` abstract class with `fetch_splits(ticker) -> ProviderResult`. |
| 2 | `services/providers/registry.py` | Add `DataType.SPLIT: []` to `_by_type`. Add `fetch_splits()` method (clone `fetch_dividends`). Extend `get_providers_ordered` switch. Register all four split providers in `init_providers()`. |
| 3 | `services/providers/config.py` | Add `split_providers: List[str] = ["yfinance", "fmp", "alpaca", "sec"]`. Add `split_cache_days: int = 7`. |
| 4 | `services/providers/yfinance_provider.py` | Implement `YFinanceSplitProvider` using `yf.Ticker(t).splits`. |
| 5 | `services/providers/fmp_provider.py` | Implement `FMPSplitProvider` (FMP `historical-stock-splits` endpoint). |
| 6 | `services/providers/alpaca_provider.py` | Implement `AlpacaSplitProvider` (Alpaca corporate actions endpoint). |
| 7 | `services/providers/sec_provider.py` | Implement `SECSplitProvider` stub returning empty success (documented fallback). |
| 8 | `services/providers/__init__.py` | Export `SplitProvider`, `SplitData`, and all four implementations. |
| 9 | `database.py` | Add `CREATE TABLE split_history` to init. Add CRUD helpers: `upsert_splits(ticker, splits)`, `get_splits(ticker, since_date)`. |
| 10 | `services/screener.py` | Add "splits" phase between dividends and prices. Update progress reporting. |
| 11 | `services/valuation.py` | Compute `split_warning` (reads from `split_history`, applies lookback + min-ratio + severity). Add to returned dict. |
| 12 | `routes/valuation.py` | Ensure `/refresh` endpoint also calls the splits refresh path and returns `split_warning`. |
| 13 | `services/recommendations.py` | Pass `split_warning` through in the per-stock dict returned by `get_top_recommendations`. |
| 14 | `static/app.js` | Render warning note in `renderValuation()`; render compact badge in `loadRecommendations()`. |
| 15 | `static/css/components.css` | Add `.split-badge`, `.split-warning-badge`, `.split-warning-note` + `.recent` / `.historical` modifiers. |
| 16 | `config.py` | Add `SPLIT_WARNING_LOOKBACK_YEARS = RECOMMENDED_EPS_YEARS`, `SPLIT_WARNING_MIN_RATIO = 1.1`, `SPLIT_WARNING_RECENT_YEARS = 3`. |

## 5. Implementation Hints & Patterns

- **Mirror DividendProvider everywhere.** Dividends are the closest analog (discrete events). Clone `fetch_dividends` in the orchestrator byte-for-byte and rename.
- **Keep the ratio threshold in one place.** Use the `config.py` constant in the valuation/warning computation, not hard-coded in the provider.
- **Do not alter scoring.** `score_stock` MUST remain untouched in this iteration. Adding a score penalty is a deliberate follow-on decision.
- **Severity computation** lives in `services/valuation.py` (or a small helper), not in providers or the DB layer. Providers just return raw split events.
- **Respect the refactoring rules in CLAUDE.md** — grep all usages before changing imports, run the verification checklist after.

## 6. Acceptance Criteria

1. Running `./venv/bin/python -c "from services.providers import init_providers, get_orchestrator; init_providers(); print(get_orchestrator().fetch_splits('AAPL'))"` returns a `ProviderResult` whose `data.splits` includes Apple's 2020 4-for-1 split.
2. `split_history` table exists in `public.db` and is populated after a screener run.
3. The Analyze page for a ticker with a known recent split (e.g., NVDA 2024 10-for-1, AAPL 2020) shows the "Stock Split Warning" note with the correct severity tier.
4. The Recommendations list shows a `.split-badge` on any recommended stock flagged as such, WITHOUT changing the list's ordering or membership.
5. `POST /api/valuation/NVDA/refresh` returns `split_warning.active == True`.
6. The screener progress dashboard shows a "splits" phase.
7. A ticker with only a 1.05:1 reverse-split rebalance in the last 8 years is **not** flagged (below `SPLIT_WARNING_MIN_RATIO`).
8. Verification checklist from CLAUDE.md passes: `py_compile`, `import app`, smoke test, app launches and Analyze + Recommendations render without errors.

## 7. Assumptions

- **No auto-adjustment of EPS.** This spec ships only the visible warning. A future iteration may implement split-adjusted EPS (applying cumulative split factors to historical EPS values). That work is out of scope here.
- **No score penalty.** Flagged recommendations stay in place unchanged in rank. A future iteration may add a `split_penalty` term to `score_stock`.
- **SEC provider is a placeholder.** Because SEC EDGAR XBRL facts do not include split events, `SECSplitProvider` returns an empty-success result. The interface exists so a future 8-K parser can drop in without changing the orchestrator.
- **yfinance remains the de-facto primary.** If FMP / Alpaca credentials are missing (`is_available()` returns False), those providers are silently skipped by the orchestrator — existing behavior.
- **Cache duration of 7 days** for splits is acceptable because corporate split announcements have typically at least several weeks of lead time and the data rarely changes retroactively.
