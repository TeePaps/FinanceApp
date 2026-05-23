# Requirements Spec: Stars Page — Sort, Filter, Click-to-Research

**Date:** 2026-05-22
**Requirement ID:** 2026-05-22-2206-stars-sort-filter-research-link
**Status:** Ready for implementation

---

## 1. Problem Statement

The Stars tab currently has no way to surface "best ratings" or "best deals"
without scanning the entire list manually. It also doesn't let users dig
deeper into a ticker the way the rest of the app does. The user wants:

1. Sort the list by stars (current behavior, made explicit) or by deepest discount.
2. Filter to "high-quality" tickers (e.g., ≥ N stars) or to undervalued ones only.
3. Click a tile to open Company Lookup for that ticker (existing pattern, app-wide).

## 2. Solution Overview

**Frontend-only change** (no backend / no schema). All work in `static/app.js`,
`static/css/pages.css`, and `templates/index.html`.

- Insert a controls bar above the Holdings section: one sort dropdown, one
  min-stars number input, one "undervalued only" checkbox.
- State stored in module-level variables; re-renders the cached data on
  control change (no extra `/api/stars` calls).
- Wrap each ticker tile in a click handler calling `lookupTicker(ticker, event)`.
- Add `cursor: pointer` + hover styling so the tile reads as clickable.

## 3. Functional Requirements

### 3.1 Sort
A dropdown with these options:
| Label | Behavior |
|---|---|
| Stars (most first) | `(b.total_stars - a.total_stars)`, ties broken by ticker asc — current default |
| Discount (most undervalued first) | `(a.price_vs_value - b.price_vs_value)`, null `price_vs_value` sinks to bottom |

Default selection: **Stars (most first)** (matches current UX).

### 3.2 Filters
Two controls, applied together (AND logic):
1. **Min stars**: number input (range 0-6), default 0. Hides tickers with `total_stars < min_stars`.
2. **Undervalued only**: checkbox, default off. When on, hides tickers where `price_vs_value >= 0` or `price_vs_value == null`.

### 3.3 Application
- One set of controls above both sections.
- Filter applied first, then sort, separately for `holdings` and `watchlist` arrays.
- Section headings update to show filtered counts: `"My Holdings (3 shown / 6 total) — out of 6"`.
- Empty filtered section shows: `"No tickers match the current filter."`

### 3.4 Click-to-Research
- Each `.star-row` becomes clickable. Clicking anywhere on the tile calls
  `lookupTicker(row.ticker, event)` — which sets the research-tab input,
  switches to the research tab, and triggers `runValuation()`.
- Visual feedback: `cursor: pointer`, subtle hover background change,
  slight border accent (already present via `:hover` rule).
- No nested clickable elements (tooltip is hover-only). No `<a>` tags needed.

## 4. Technical Requirements

### 4.1 `templates/index.html`
Insert a controls bar between the legend `<aside>` and the holdings section:
```html
<div class="stars-controls-bar">
    <label class="stars-control">
        Sort:
        <select id="stars-sort">
            <option value="stars" selected>Stars (most first)</option>
            <option value="discount">Discount (most undervalued first)</option>
        </select>
    </label>
    <label class="stars-control">
        Min stars:
        <input type="number" id="stars-min" min="0" max="6" value="0" step="1">
    </label>
    <label class="stars-control">
        <input type="checkbox" id="stars-undervalued-only">
        Undervalued only
    </label>
</div>
```

Bump `app.js` cache-busting version.

### 4.2 `static/app.js`
- Module-level cache: `let _starsData = { holdings: [], watchlist: [] };`
- `loadStars()` stores the response in `_starsData`, then calls a new
  `renderStarsTab()` which applies filter+sort and renders.
- New `renderStarsTab()`:
  ```javascript
  function renderStarsTab() {
      const sortBy = document.getElementById('stars-sort').value;
      const minStars = parseInt(document.getElementById('stars-min').value, 10) || 0;
      const undervaluedOnly = document.getElementById('stars-undervalued-only').checked;

      const apply = (rows) => {
          let r = rows.filter(x => x.total_stars >= minStars);
          if (undervaluedOnly) r = r.filter(x => x.price_vs_value != null && x.price_vs_value < 0);
          if (sortBy === 'discount') {
              r.sort((a, b) => {
                  const av = a.price_vs_value == null ? Infinity : a.price_vs_value;
                  const bv = b.price_vs_value == null ? Infinity : b.price_vs_value;
                  return av - bv;
              });
          } else {
              r.sort((a, b) => (b.total_stars - a.total_stars) || a.ticker.localeCompare(b.ticker));
          }
          return r;
      };

      const filteredHoldings = apply(_starsData.holdings);
      const filteredWatchlist = apply(_starsData.watchlist);

      renderStarsList(holdingsEl, filteredHoldings, 6);
      renderStarsList(watchlistEl, filteredWatchlist, 5);

      // Update count labels in the section headers
      updateStarsSectionCount('holdings', filteredHoldings.length, _starsData.holdings.length);
      updateStarsSectionCount('watchlist', filteredWatchlist.length, _starsData.watchlist.length);
  }
  ```
- Wire control `change`/`input` events to call `renderStarsTab()`.
- Update `renderStarRow()` to add `onclick="lookupTicker('${row.ticker}', event)"` to the
  outer `.star-row` div. Add `role="button"` and `tabindex="0"` for accessibility (with
  a `keypress` handler for Enter key).

### 4.3 `static/css/pages.css`
- Add `.stars-controls-bar` styles (flex layout, margin-bottom).
- Add `.stars-control` styles (label + input alignment).
- Update `.star-row` to include `cursor: pointer` (replace the existing rule
  if needed) and strengthen the `:hover` background-color tint.

### 4.4 Section header counts
Modify the HTML in `index.html` so the section headers have ID'd `<span>`s for
filtered/total counts, OR rebuild the heading text from `renderStarsTab()`.
Recommended: simpler approach — replace the entire `<h3>` content via JS.

## 5. Acceptance Criteria

1. **Sort**: Changing the dropdown to "Discount" reorders both holdings + watchlist by `price_vs_value` ascending. Null values sink to bottom.
2. **Sort**: Switching back to "Stars" restores stars-desc order.
3. **Filter**: Setting "Min stars" to 3 hides any ticker with `total_stars < 3`.
4. **Filter**: Checking "Undervalued only" hides any ticker where `price_vs_value >= 0` or is null.
5. **Filters combine**: Min-stars=4 + undervalued-only shows only tickers meeting BOTH conditions.
6. **Click**: Clicking anywhere on a `.star-row` triggers `lookupTicker(ticker, event)` — research tab loads, ticker input populated, valuation runs.
7. **Click**: Tile shows `cursor: pointer` and a subtle hover background.
8. **Counts**: Section headers update to show "(N shown / M total)" when a filter is active. Without filter, shows just "M total".
9. **Empty state**: When all tickers are filtered out of a section, the section shows an empty-state message (not an empty grid).
10. **No backend changes**: All work is in `static/app.js`, `static/css/pages.css`, `templates/index.html`. No new API calls.

## 6. Assumptions

- Min-stars filter is integer 0-6 (Holdings can have 0-6; Watchlist 0-5; setting min=6 with watchlist gives them all 0 matches — that's expected).
- Filter+sort state is NOT persisted across page reloads (acceptable v1; could add localStorage later).
- No URL query-string sync (e.g., `?stars-min=4`) — internal state only for now.
- The current `cursor: help` rule on `.star` (for tooltip) is fine on top of the row's `cursor: pointer` — children's cursor wins on hover.
