# Requirements Spec: Make Undervalued Apply to All + Star Tooltips

**Date:** 2026-05-22
**Requirement ID:** 2026-05-22-2127-star-undervalued-and-tooltips
**Status:** Ready for implementation

---

## 1. Problem Statement

After shipping the Star Scoring System, the user observed that the Undervalued
criterion is more useful when applied to the watchlist (it's a *discovery*
signal — "this stock I don't own is cheap"), and that the order should reflect
this (holdings-only criteria grouped at the end). The user also wants tooltips
on each star icon explaining what the criterion represents.

## 2. Solution Overview

Two scoped changes:
1. Move "Undervalued" from holdings-only to all-tickers, and swap positions 5↔6
   so that Buybacks (the only remaining holdings-only criterion) sits at the end.
2. Replace the native browser `title` tooltip on each star with a custom
   CSS-only tooltip that appears instantly and matches the app's theme.

## 3. Functional Requirements

### 3.1 New Criterion Order
| # | Name | Applies To |
|---|---|---|
| 1 | Earnings Beat | All |
| 2 | Fair Value Up | All |
| 3 | Dividend Up | All |
| 4 | Debt-to-Capital ≤ 25% | All |
| **5** | **Undervalued (price < fair value)** | **All** *(was holdings-only)* |
| **6** | **Share Buybacks since buy** | **Holdings only** *(was #5)* |

### 3.2 Maximum Possible Stars
- **Holdings**: 6 (unchanged)
- **Watchlist**: **5** (was 4)

### 3.3 Tooltips
- Hovering over any star (filled or empty) displays a tooltip explaining the criterion.
- Tooltip appears instantly (no delay).
- Tooltip is positioned above the star with a small arrow.
- Tooltip styling matches the app's theme (uses CSS variables for bg/text/border colors).
- Tooltip text format: `"{number}. {description}{ ✓ if earned}"` — for example, `"5. Undervalued (price < fair value) ✓"` or `"6. Shares outstanding down since buy"`.
- Native `title` attribute is retained for accessibility / screen reader users.

### 3.4 Data Migration
- After code is deployed, `calculate_all_star_ratings()` is run once to recompute
  star ratings for all existing rows so the watchlist correctly shows Undervalued stars.
- No schema migration needed; `star_ratings` columns are named (`undervalued`,
  `shares_buyback`) and `total_stars` is computed from the sum.

## 4. Technical Requirements

### 4.1 Backend — `services/stars.py`
In `calculate_stars()`, remove the `if is_holding` gate on `_check_undervalued`:

```python
# Before:
undervalued = _check_undervalued(current_price, estimated_value) if is_holding else False
# After:
undervalued = _check_undervalued(current_price, estimated_value)
```

No changes to `_check_undervalued()` itself — it already returns False when
data is missing.

### 4.2 Frontend — `static/app.js`
Update `STAR_CRITERIA` array. New order + `holdingsOnly` flag moves from
`undervalued` to `shares_buyback`:

```javascript
const STAR_CRITERIA = [
    { key: 'earnings_beat',       label: 'Earnings beat consensus',           num: 1 },
    { key: 'fair_value_up',       label: 'Fair value up year-over-year',      num: 2 },
    { key: 'dividend_up',         label: 'Dividend up year-over-year',        num: 3 },
    { key: 'debt_to_capital_low', label: 'Debt-to-capital ≤ 25%',             num: 4 },
    { key: 'undervalued',         label: 'Price < fair value',                num: 5 },
    { key: 'shares_buyback',      label: 'Shares outstanding down since buy', num: 6, holdingsOnly: true },
];
```

Update `renderStarRow()` to wrap each star in a `.star-wrap` span with a
`.star-tooltip` sibling:
```javascript
return `<span class="star-wrap">
    <span class="${cls}" title="${title}">${earned ? '★' : '☆'}</span>
    <span class="star-tooltip">${title}</span>
</span>`;
```

(`title` attribute stays for accessibility.)

### 4.3 Frontend — `static/css/pages.css`
Add new tooltip rules near the existing `.star` styles. Pattern:

```css
.star-wrap {
    position: relative;
    display: inline-block;
}

.star-tooltip {
    position: absolute;
    bottom: calc(100% + 6px);
    left: 50%;
    transform: translateX(-50%);
    background: var(--bg-tertiary, #2a2a2a);
    color: var(--text-primary);
    border: 1px solid var(--border-color);
    padding: 6px 10px;
    border-radius: 4px;
    font-size: 0.85em;
    white-space: nowrap;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.1s;
    z-index: 10;
}

.star-tooltip::after {
    content: '';
    position: absolute;
    top: 100%;
    left: 50%;
    transform: translateX(-50%);
    border: 5px solid transparent;
    border-top-color: var(--border-color);
}

.star-wrap:hover .star-tooltip {
    opacity: 1;
}
```

### 4.4 Frontend — `templates/index.html`
Update the legend in the `#stars-tab` section:
- Swap entries 5 and 6.
- Remove "(holdings only)" from Undervalued.
- Keep "(holdings only)" on Buybacks.

Update the watchlist section heading:
```html
<!-- Before: -->
<h3>Watchlist <span class="stars-section-sub">(out of 4)</span></h3>
<!-- After: -->
<h3>Watchlist <span class="stars-section-sub">(out of 5)</span></h3>
```

Bump the CSS / JS cache-busting versions (e.g., `pages.css?v=44`, `app.js?v=50`).

### 4.5 Recompute
After code changes are deployed, run:
```python
./venv/bin/python -c "
from services.providers import init_providers
init_providers()
from services.stars import calculate_all_star_ratings
calculate_all_star_ratings()
"
```

## 5. Acceptance Criteria

1. **Backend**: `calculate_stars()` computes `undervalued = True` for a non-holding ticker where `current_price < estimated_value`.
2. **API**: `/api/stars` returns `criteria.undervalued = true` for non-holding rows that meet the threshold.
3. **UI**: The Stars tab shows criterion 5 as Undervalued (with star ★ when earned) for both holdings and watchlist rows.
4. **UI**: The Stars tab shows criterion 6 as Buybacks; watchlist rows skip this position (only show 5 stars total).
5. **UI**: Watchlist section header reads "Watchlist (out of 5)".
6. **UI**: Hovering over any star displays a tooltip (instant, themed) describing the criterion. Tooltip is centered above the star with an arrow.
7. **UI**: Native `title` attribute is still present on each star (verified via DOM inspector or accessibility tree).
8. **Legend**: The 6-criterion legend at the top of the Stars tab matches the new order (Undervalued = #5, Buybacks = #6 with holdings-only tag).
9. **Migration**: After running `calculate_all_star_ratings()`, the existing watchlist tickers in the DB have updated star totals reflecting the Undervalued criterion.

## 6. Assumptions

- The user is fine with the existing tooltip text format (`"5. Undervalued (price < fair value) ✓"`). No new copy needed.
- No mobile-specific tooltip behavior required (CSS `:hover` doesn't fire on touch, but the native `title` fallback covers mobile).
- The recompute is a one-shot script run by the developer (not a permanent API endpoint or background job).
