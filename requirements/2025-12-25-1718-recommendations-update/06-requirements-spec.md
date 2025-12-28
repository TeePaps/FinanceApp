# Requirements Specification: Recommendations Update

## Problem Statement

The user observed that Investment Recommendations only shows S&P 500 stocks. Investigation revealed this is not a bug - the scoring algorithm naturally favors value-oriented S&P 500 stocks due to:
1. NASDAQ growth stocks being penalized for no dividends
2. Many stocks filtered out by the 5-year minimum EPS requirement
3. Top 10 stocks happening to all be S&P 500-only stocks

However, two legitimate issues were identified:
1. The Recommendations tab doesn't always show current data after updates
2. The EPS years filter is too restrictive, excluding stocks with some EPS data

## Solution Overview

1. **Always reload recommendations when tab is selected** - Ensures fresh data
2. **Remove minimum EPS years filter** - Only exclude stocks with NO EPS data
3. **Show filter statistics** - Help user understand why stocks were excluded

## Functional Requirements

### FR1: Auto-reload on Tab Switch
- When user clicks the Recommendations tab, `loadRecommendations()` should always be called
- Current behavior: Only reloads on first visit or after screener completes while on that tab
- **File:** `static/app.js` - `showTab()` function, line ~590

### FR2: Remove Minimum EPS Years Requirement
- Change `RECOMMENDATION_MIN_EPS_YEARS` filter to only exclude stocks with `eps_years == 0`
- Current behavior: Excludes stocks with `eps_years < 5`
- **File:** `services/recommendations.py` - `get_top_recommendations()`, line 169

### FR3: Show Filter Statistics
- Add to API response: count of stocks excluded and why
- Display in UI: "X stocks analyzed, Y excluded (Z no EPS data, W no price, etc.)"
- **Files:**
  - `services/recommendations.py` - `get_top_recommendations()` return value
  - `static/app.js` - `loadRecommendations()` function, line ~3055

## Technical Requirements

### TR1: Modify `showTab()` in app.js
```javascript
// Around line 590, ensure recommendations always reload
} else if (tabName === 'recommendations') {
    loadRecommendations();  // Already does this, but verify it runs every time
}
```

### TR2: Modify filter in recommendations.py
```python
# Line 169 - Change from:
if eps_years < RECOMMENDATION_MIN_EPS_YEARS:
    continue

# To:
if eps_years == 0:
    continue
```

### TR3: Add exclusion tracking in recommendations.py
```python
# Add counters for exclusion reasons
excluded = {'no_price': 0, 'no_valuation': 0, 'no_eps': 0}

# Track each exclusion type
if not val.get('current_price') or val.get('current_price', 0) <= 0:
    excluded['no_price'] += 1
    continue
if val.get('price_vs_value') is None:
    excluded['no_valuation'] += 1
    continue
if eps_years == 0:
    excluded['no_eps'] += 1
    continue

# Include in return value
return {
    'recommendations': top_n,
    'total_analyzed': len(scored_stocks),
    'excluded': excluded,
    ...
}
```

### TR4: Display exclusion info in app.js
```javascript
// In loadRecommendations(), around line 3056
html += `
    <div class="recommendations-header">
        <span class="analyzed-count">
            Analyzed ${data.total_analyzed} stocks
            ${data.excluded ? ` (${Object.values(data.excluded).reduce((a,b)=>a+b,0)} excluded)` : ''}
        </span>
        ...
    </div>
`;
```

## Implementation Hints

### Pattern to Follow
The existing code in `get_top_recommendations()` already tracks why stocks are skipped with `continue` statements. Simply add counters before each `continue`.

### Integration Points
- No database changes needed
- No new API endpoints needed
- Minimal frontend changes - just display the new data

## Acceptance Criteria

1. ✅ Clicking Recommendations tab always shows current data
2. ✅ Stocks with 1+ years of EPS data are included in recommendations
3. ✅ UI shows how many stocks were excluded and why
4. ✅ Existing scoring algorithm unchanged
5. ✅ Stocks from all enabled indexes can appear in recommendations (they already can, just need more to pass filters)

## Assumptions

- Current 10-stock limit is acceptable
- Value-oriented scoring (dividend preference) is intentional
- No changes to scoring weights needed
