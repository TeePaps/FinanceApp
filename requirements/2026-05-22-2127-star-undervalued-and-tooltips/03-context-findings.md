# Phase 3: Context Findings

## Files to Modify

| File | Change | Why |
|---|---|---|
| `services/stars.py` | `calculate_stars()` — call `_check_undervalued` unconditionally (not just when `is_holding`) | Criterion 5 (new ordering) applies to all tickers |
| `static/app.js` | `STAR_CRITERIA` array — swap entries 5 & 6, move `holdingsOnly: true` from `undervalued` to `shares_buyback` | UI rendering order + max-stars logic |
| `static/app.js` | `renderStarRow()` — replace native `title` tooltip with custom CSS tooltip markup | Q1 — instant, themed tooltips |
| `static/css/pages.css` | Add `.star-tooltip` rules with `:hover` show/hide | Visual tooltip implementation |
| `templates/index.html` | Update legend — reorder entries 5 & 6, change "(holdings only)" tag from criterion 6 to new criterion 6 (Buybacks) | Match UI numbering |
| `templates/index.html` | Watchlist `<h3>` — change "(out of 4)" to "(out of 5)" | Match new max-stars for non-holdings |

## Why These Are All There Is

- **Database**: No schema change needed. The `star_ratings` table has named columns (`undervalued`, `shares_buyback`) so ordering is purely a UI concern. `total_stars` is a sum so position doesn't matter.
- **API**: No change needed. `/api/stars` returns `criteria` as a named-key object; the frontend controls display order.
- **Backend criterion logic**: `_check_undervalued()` itself already doesn't check `is_holding` — it only checks `current_price < estimated_value`. The holdings-only gate is in the *caller* (`calculate_stars()`), which is one line to change.

## Tooltip Implementation Pattern

CSS-only tooltips need a wrapper that holds both the star and the tooltip:
```html
<span class="star-wrap">
  <span class="star star-filled">★</span>
  <span class="star-tooltip">1. Earnings beat consensus ✓</span>
</span>
```
CSS shows `.star-tooltip` on `.star-wrap:hover`. Positioning: absolute, centered above the star, with a small arrow.

## Recompute Plan

After code changes:
```python
from services.providers import init_providers
init_providers()
from services.stars import calculate_all_star_ratings
calculate_all_star_ratings()  # No arg = all tickers in valuations table
```
For the existing 12-ticker sample, this finishes in ~1 minute.
