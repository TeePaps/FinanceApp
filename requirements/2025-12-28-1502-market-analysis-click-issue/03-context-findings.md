# Context Findings

## Files Analyzed

### Primary Files
- `static/app.js` - Main JavaScript file containing all click handlers
- `static/css/pages.css` - CSS for screener and clickable elements
- `templates/index.html` - HTML structure for screener tab

### Key Code Locations

1. **Undervalued Cards Click Handler** (`static/app.js:2784-2795`)
   - Uses event delegation on `.undervalued-grid`
   - Finds `.undervalued-card` via `e.target.closest()`
   - Checks `window.getSelection()` before navigating
   - Calls `viewCompany(card.dataset.ticker)`

2. **Table Rows Click Handler** (`static/app.js:2959-2972`)
   - Uses event delegation on `.screener-table`
   - Finds `.clickable-row` via `e.target.closest()`
   - Same selection check pattern
   - Same `viewCompany(row.dataset.ticker)` call

3. **viewCompany Function** (`static/app.js:1716-1741`)
   - Sets ticker in research input
   - Switches to research tab
   - Runs valuation
   - No error handling - could fail silently if DOM elements missing

## Potential Causes Identified

### 1. Event Listener Accumulation (Less Likely)
- `renderScreenerTable()` adds new listener each call (lines 2959-2972)
- However, innerHTML replaces the table element, so old listeners are GC'd
- But `renderScreener()` only runs once per load for the cards

### 2. Text Selection Check (Possible)
```javascript
const selection = window.getSelection().toString();
if (!selection) {
    viewCompany(row.dataset.ticker);
}
```
- If browser has lingering selection state, clicks would be ignored
- Could explain "hover works but click doesn't"

### 3. Missing data-ticker Attribute (Possible)
- If `stock.ticker` is undefined/empty, `data-ticker=""` would be set
- `viewCompany('')` would still run but might cause silent issues

### 4. DOM Element Missing (Possible)
- `viewCompany` doesn't use try-catch
- If `research-ticker` or `research-tab` elements don't exist, throws error
- Error would be swallowed, appearing as "click does nothing"

### 5. Race Condition with Re-rendering (Possible)
- If data is being re-rendered while user clicks, element could be replaced mid-click
- Event listener would be on now-removed element

### 6. Document Click Handlers Interference (Less Likely)
- Multiple document-level click handlers exist (lines 403, 675, 1766)
- None use stopPropagation
- None should interfere with screener clicks

## Patterns to Follow

The codebase uses event delegation pattern consistently:
```javascript
container.addEventListener('click', function(e) {
    const target = e.target.closest('.target-class');
    if (target) {
        doSomething(target.dataset.value);
    }
});
```

## Recommended Debugging Approach

1. Add console.log to click handlers to confirm they fire
2. Check if selection.toString() returns unexpected values
3. Verify data-ticker attributes are always populated
4. Add error handling to viewCompany function
