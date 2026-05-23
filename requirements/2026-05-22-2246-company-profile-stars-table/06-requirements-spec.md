# Requirements Spec: Stars Explanation Table on Company Profile

**Date:** 2026-05-22
**Requirement ID:** 2026-05-22-2246-company-profile-stars-table
**Status:** Ready for implementation

---

## 1. Problem Statement

The Stars tab tells you a ticker has, e.g., 3/5 stars — but doesn't tell you
*why*. Drilling into the company profile (Company Lookup tab) currently shows
the fair value and a price history chart but no star breakdown. Users want
to see, for each of the 6 criteria: whether the star is earned, and the
underlying numbers that drove that decision.

## 2. Solution Overview

Two pieces:

1. **Backend**: New `services/stars.py:explain_stars(ticker)` returns a list
   of 6 criterion explanations (status + underlying values + short
   human-readable summary). Exposed via a new endpoint
   `GET /api/stars/<ticker>/explanation`.
2. **Frontend**: In `renderValuation()` (Company Lookup render path), add a
   placeholder for the table directly below the fair-value formula. After
   render, fetch from the new endpoint and populate.

Always shows all 6 rows. Holdings-only criteria (Buybacks) show "Only
applies to holdings" when applicable. Missing-data rows show "—".

## 3. Functional Requirements

### 3.1 The Explanation Table
6 rows, in this order (matching the Stars tab numbering):

| # | Name | Star | Values column |
|---|---|---|---|
| 1 | Earnings Beat | ★/☆ | `EPS actual $X.XX vs estimate $Y.YY → +Z.Z%` (or "no data") |
| 2 | Fair Value Up | ★/☆ | `Now $X.XX / 1y ago $Y.YY (Δ +Z.Z%)` |
| 3 | Dividend Up | ★/☆ | `TTM $X.XX / prior yr $Y.YY (Δ +Z.Z%)` (or "No dividend") |
| 4 | Debt/Capital ≤ 25% | ★/☆ | `(LTD $X + STD $Y) / (debt + equity $Z) = N.N% — as of 2025-09-30` |
| 5 | Undervalued | ★/☆ | `Price $X.XX vs FV $Y.YY (Δ −Z.Z%)` |
| 6 | Share Buybacks (holdings) | ★/☆ | `First buy 2024-01-15: N shares out. Now: M shares (Δ −Z.Z%)` OR `Only applies to holdings` |

### 3.2 Behavior
- Table renders directly below the fair-value formula block, always visible.
- Loads asynchronously after the main valuation data (so the profile page
  loads at its current speed even if the explanation endpoint is slow).
- Loading state: row shows "Loading…" until the API responds.
- Error state: if the API fails, the table area shows "Star breakdown
  unavailable for this ticker" (no big red errors).
- Total stars summary row at the bottom: "Total: X / 5 (or 6 for holdings)".

## 4. Technical Requirements

### 4.1 Backend — `services/stars.py`
Add a new public function:

```python
def explain_stars(ticker: str) -> Dict:
    """
    Return per-criterion explanation suitable for the Company Profile table.

    Returns:
      {
        'ticker': str,
        'is_holding': bool,
        'max_stars': int,           # 5 for non-holdings, 6 for holdings
        'total_stars': int,
        'criteria': [
          {
            'num': 1, 'key': 'earnings_beat',
            'name': 'Earnings Beat',
            'earned': True,
            'summary': 'EPS actual $1.50 vs estimate $1.40 → +7.1%',
            'values': {...raw numbers used...},
            'note': None,   # or 'Only applies to holdings' / 'No data'
          },
          ...
        ]
      }
    """
```

Use existing helpers / DB tables:
- `_check_earnings_beat` already calls `orch.fetch_analyst_estimates(ticker)` —
  return the `result.data` AnalystEstimateData so we have actual + estimate +
  surprise + period_end.
- `_check_fair_value_up`: look up `db.get_valuation_history(ticker)` for the
  ≤1-yr-ago snapshot; if none, backfill from eps_history with
  `_prior_year_fair_value_from_eps`. Return both values + delta.
- `_check_dividend_up`: pull current TTM + prior-year TTM from
  `fetch_yearly_dividends()` (or db.get_dividend_history fallback).
- `_check_debt_to_capital_low`: read `db.get_balance_sheet(ticker)` — return
  LTD/STD/equity/ratio/as_of_date.
- `_check_undervalued`: get current_price + estimated_value from the
  valuations row.
- `_check_shares_buyback_since_buy`: get first_buy_date + earliest +
  current shares from `db.get_shares_outstanding_history`.

### 4.2 Backend — `routes/stars.py`
Add:

```python
@stars_bp.route('/stars/<ticker>/explanation', methods=['GET'])
def api_stars_explanation(ticker):
    from services.stars import explain_stars
    try:
        data = explain_stars(ticker.upper())
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
```

### 4.3 Frontend — `static/app.js`
In `renderValuation()`, after the formula block, insert:
```html
<div class="stars-explanation-block">
    <h4>Star Rating Breakdown</h4>
    <div id="stars-explanation-${data.ticker}">
        <div class="loading-indicator">Loading…</div>
    </div>
</div>
```
Then after the HTML is set on `resultsDiv`, call:
```javascript
loadStarsExplanation(data.ticker);
```

New function `loadStarsExplanation(ticker)`:
- fetch `/api/stars/<ticker>/explanation`
- on success: render a `<table>` with header (Star | Criterion | Calculation) and 6 rows + a total row.
- on failure: show "Star breakdown unavailable for this ticker".

Row rendering reuses the `★ / ☆` star icons (with tooltip-on-hover, same
pattern as the Stars tab). The Calculation column shows the `summary` string.
If `note` is set (e.g., "Only applies to holdings"), display it in the
Calculation column with a muted style.

### 4.4 Frontend — `static/css/pages.css`
Add styles:
- `.stars-explanation-block` — section spacing, header style
- `.stars-explanation-table` — table layout (3 columns: Star, Criterion, Calculation)
- `.stars-explanation-table .row-disabled` — muted styling for rows where the criterion doesn't apply
- `.stars-explanation-total` — bold/separated total row at the bottom

Bump `pages.css` cache version. Bump `app.js` cache version.

## 5. Acceptance Criteria

1. **Endpoint**: `GET /api/stars/PGR/explanation` returns 6 criteria with their underlying numbers.
2. **PGR shows in table**: On Company Lookup for PGR, table shows 6 rows. Criterion 5 (Undervalued) is earned (Price $199.51 vs FV $223.81 → −10.9%). Criterion 4 (Debt/Capital) shows the actual ratio. Criterion 6 (Buybacks) shows "Only applies to holdings".
3. **Non-data**: Tickers without analyst estimates (e.g., a small foreign issuer) show row 1 with "No data" in Calculation column and ☆ star.
4. **Holding example**: For AAPL (which is held), row 6 shows the actual shares-then vs shares-now computation, not the "Only applies" message.
5. **Async load**: The existing valuation card renders immediately; the table appears moments later. Page doesn't block on the new endpoint.
6. **Total stars**: Sums match what the Stars tab shows for the same ticker (e.g., PGR shows 4/5 here and 4/5 on the Stars tab — same value).
7. **Layout**: Table is directly below the fair-value formula. No collapsing. Looks consistent with the rest of the Company Lookup styling.

## 6. Assumptions

- A: The explanation endpoint may call `fetch_analyst_estimates` (yfinance)
  fresh each time since that data isn't persisted. ~0.3s extra latency per
  call. Acceptable for an on-demand drilldown.
- B: For dividend / fair-value history, we prefer cached snapshots
  (`valuation_history`, `dividend_history`); fall back to fresh fetch +
  backfill if missing.
- C: We do NOT update the cached `star_ratings` table from this endpoint —
  it's strictly read/derive for display.
- D: Total stars in the table equals what's in `star_ratings`. If they
  differ (e.g., user just changed a holding), the table is the source of
  truth for THIS view; full reconciliation requires a screener run / recalc.
