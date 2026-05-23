# Requirements Spec: Company Profile Data Consistency

**Date:** 2026-05-23
**Requirement ID:** 2026-05-23-1357-company-profile-data-consistency
**Status:** Ready for implementation

---

## 1. Problem Statement

On the Company Profile page, the fair-value formula card and the star
explanation table can disagree because:

- The formula card calls `/api/valuation/<ticker>` which recomputes fresh
  (yfinance flakiness can produce $0 dividend → wrong fair value).
- The explain table calls `/api/stars/<ticker>/explanation` which reads the
  cached `valuations` row.
- Newly-tracked tickers (empty cache) show "missing" in the explain table
  even after the formula card displays values.

Result: same page, three different views of one ticker's fair value (formula
card / explain table / Stars tab).

## 2. Solution Overview

Three coordinated fixes:

1. **Lazy-write cache** — `/api/valuation/<ticker>` saves its computed result
   back to the `valuations` table on every call (matches what
   `/refresh` already does).
2. **Don't trust 0-dividend overwrites** — both `calculate_valuation()` and
   the screener's Phase 2 preserve a cached non-zero `annual_dividend` when
   the fresh fetch returns `0` or `None`.
3. **Share fresh data with the explain endpoint** — `/api/stars/<ticker>/explanation`
   accepts a `valuation` POST body. The frontend POSTs the fresh valuation
   it just received from `/api/valuation`, so both halves of the Company
   Profile render from the same numbers.

## 3. Functional Requirements

### 3.1 Lazy cache write in `/api/valuation/<ticker>`
After `calculate_valuation(ticker)` returns successfully:
- Save the result to the `valuations` table via `data_manager.update_valuation()`.
- If the computed `annual_dividend` is `0` but the existing cached value
  is non-zero, preserve the cached value (see 3.2).

### 3.2 Dividend "no regression to 0" guard
In `calculate_valuation()` (services/valuation.py) and `run_screener()`
Phase 2 (services/screener.py):
- After fetching dividends fresh, if `annual_dividend == 0` (or None) AND the
  cached/existing value is `> 0`, log a warning ("yfinance returned 0
  dividend for {ticker}; preserving cached ${X}") and use the cached value
  in the computation.
- Only update the cached dividend to 0 when:
  - The cached value is also 0 (no change), OR
  - The user explicitly clicks the per-ticker Refresh button (which should
    still allow the value to drop if confirmed twice).
- Note: this guard does NOT apply to the per-ticker `/refresh` POST endpoint
  used by the Refresh button — there the user is asking for the latest data,
  good or bad.

### 3.3 Explanation endpoint accepts pre-computed valuation
- Add `POST /api/stars/<ticker>/explanation` (in addition to the existing GET).
- Accepts JSON body: `{ "valuation": {"current_price": ..., "estimated_value": ..., "eps_avg": ..., "annual_dividend": ...} }`.
- `explain_stars(ticker, valuation_override=None)` uses the override for
  criteria 2/3/5 if provided; falls back to `db.get_valuation()` otherwise.
- The GET endpoint keeps its current behavior (cache-only).

### 3.4 Frontend updates
In `renderValuation()` (static/app.js):
- After receiving the valuation from `/api/valuation/<ticker>`, call
  `loadStarsExplanation(ticker, valuationData)` instead of just the ticker.
- The function now POSTs to `/api/stars/<ticker>/explanation` with the
  valuation in the body.
- Both views are now guaranteed to show the same numbers.

## 4. Technical Requirements

### 4.1 `routes/valuation.py`
After `result = calculate_valuation(ticker)`:
```python
# Lazy-write to cache so the Stars tab and explanation endpoint see fresh data
if result.get('current_price') and result.get('estimated_value') is not None:
    try:
        data_manager.update_valuation(ticker, result)
    except Exception:
        pass  # never fail the read because cache write failed
return jsonify(result)
```

### 4.2 `services/valuation.py`
In `calculate_valuation()`, after fetching dividends:
```python
if (annual_dividend == 0 or annual_dividend is None):
    import database as db
    cached = db.get_valuation(ticker) or {}
    cached_div = cached.get('annual_dividend') or 0
    if cached_div > 0:
        # yfinance is flaky on dividends — keep the cached value
        from logger import log
        log.warning(f"[{ticker}] fresh dividend fetch returned 0; preserving cached ${cached_div}")
        annual_dividend = cached_div
```

### 4.3 `services/screener.py`
Apply the same guard in Phase 2's dividend collection loop (after fetching
each ticker's dividend, before storing).

### 4.4 `routes/stars.py`
Update the explanation endpoint to accept POST with override:
```python
@stars_bp.route('/stars/<ticker>/explanation', methods=['GET', 'POST'])
def api_stars_explanation(ticker):
    from services.stars import explain_stars
    valuation_override = None
    if request.method == 'POST':
        body = request.get_json(silent=True) or {}
        valuation_override = body.get('valuation')
    try:
        data = explain_stars(ticker.upper(), valuation_override=valuation_override)
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
```

### 4.5 `services/stars.py`
Extend `explain_stars` to accept and use a `valuation_override`:
```python
def explain_stars(ticker: str, valuation_override: Optional[Dict] = None) -> Dict:
    ticker = ticker.upper()
    if valuation_override:
        # Merge over the cached row so we have current_price/eps_avg/etc from
        # the same source as the formula card on the Company Profile page.
        cached = db.get_valuation(ticker) or {}
        valuation = {**cached, **{k: v for k, v in valuation_override.items() if v is not None}}
    else:
        valuation = db.get_valuation(ticker) or {}
    # ... rest unchanged
```

### 4.6 `static/app.js`
Change `loadStarsExplanation` to accept (and POST) the valuation:
```javascript
async function loadStarsExplanation(ticker, valuation) {
    const target = document.getElementById('stars-explanation-target');
    if (!target) return;
    try {
        const res = await fetch(`/api/stars/${encodeURIComponent(ticker)}/explanation`, {
            method: valuation ? 'POST' : 'GET',
            headers: {'Content-Type': 'application/json'},
            body: valuation ? JSON.stringify({valuation}) : undefined,
        });
        // ... rest unchanged
```

Update the call site in `renderValuation()` to pass `data` (the valuation):
```javascript
loadStarsExplanation(data.ticker, data);
```

## 5. Acceptance Criteria

1. **USB regression**: Loading the Company Profile for USB shows the same
   `estimated_value` in both the formula card and the explain table's row 2,
   row 5. No "missing X" rows when the cached row was previously populated.
2. **Empty-cache backfill**: A ticker that's brand new to the `valuations`
   table (or has a partial row) shows real values on first Company Profile
   view, and subsequent calls to `/api/stars` see the same data without
   needing an explicit refresh.
3. **Flaky-dividend guard**: When yfinance returns `$0` for a dividend-paying
   stock, the cached non-zero value is preserved (not overwritten). A warning
   is logged. The formula card and explain table still show the correct fair
   value derived from the preserved dividend.
4. **Per-ticker Refresh still works**: Clicking the Refresh button (which
   hits `/api/valuation/<ticker>/refresh`) can still drop the cached
   dividend to `$0` if that's what the user wants — the guard applies only
   to the GET path, not the explicit refresh.
5. **Stars tab unchanged**: The Stars tab still reads cached values; it
   automatically benefits from the lazy-write cache (more tickers will have
   complete data).

## 6. Assumptions

- A: The "non-zero → 0" dividend regression guard ONLY watches dividends.
  EPS, price, and other fields keep their existing fresh-trust behavior.
- B: Browser caching of `/api/stars/<ticker>/explanation` (GET) shouldn't
  cause stale views since POST bodies aren't cached and we'll be switching
  to POST whenever fresh valuation data is available.
- C: We do NOT update `star_ratings` from the explanation endpoint. The
  cached `total_stars` may still differ briefly from the explanation table's
  computed total — the explanation table is the source of truth for the
  Company Profile view; the next screener run reconciles `star_ratings`.
- D: The lazy cache write may write more often than necessary (every page
  view). Acceptable — writes are cheap and idempotent.
