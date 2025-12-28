# Context Findings

## API Performance Testing Results
Tested yfinance price history API - all timeframes return in under 0.5s:

| Period | Response Time | Data Points |
|--------|---------------|-------------|
| 1 month | 0.394s | 20 points |
| 3 months | 0.098s | 63 points |
| 6 months | 0.081s | 127 points |
| 1 year | 0.146s | 250 points |
| 2 years | 0.113s | 502 points |
| 5 years | 0.184s | 1256 points |

**Conclusion:** Live fetching is viable. No database caching needed.

## Existing Infrastructure

### Provider System
- `fetch_price_history(ticker, period)` already exists in `DataOrchestrator` (registry.py:845)
- Returns `HistoricalPriceData` with:
  - `prices: Dict[str, float]` - daily close prices keyed by date string
  - `current_price: float`
  - `price_1m_ago`, `price_3m_ago` - optional period comparisons
  - `change_1m_pct`, `change_3m_pct` - percentage changes
- yfinance provider supports periods: `'1mo', '3mo', '6mo', '1y', '2y', '5y'`

### Company Lookup Page (templates/index.html:212-227)
- Tab: `research-tab`, Section: `research-section`
- Input: `#research-ticker` with autocomplete
- Results: `#research-results` div
- Main function: `runValuation()` in app.js:2045

### Current Valuation Display (app.js:2081)
- `renderValuation(data)` builds HTML for:
  - Header with ticker, company name, source badge
  - Valuation cards: Current Price, Estimated Value, Assessment
  - Formula section
  - EPS History table
  - Dividend History table
- Already has `data.estimated_value` available for fair value line

### No Charting Library Currently
- Only external script is app.js
- No chart.js, d3, plotly, etc. loaded
- Simple CSS-based bars used for month profit display

## Files That Will Need Modification

1. **templates/index.html**
   - Add charting library (Chart.js recommended - lightweight, simple)
   - Add chart container to research section

2. **static/app.js**
   - Add `fetchPriceHistory(ticker, period)` function
   - Add `renderPriceChart(historyData, fairValue)` function
   - Modify `renderValuation()` to include chart section
   - Add period selector buttons

3. **routes/valuation.py** (or new route file)
   - Add `/api/price-history/<ticker>` endpoint
   - Accept period parameter (1m, 3m, 6m, 1y, 5y)
   - Return price history from orchestrator

4. **static/css/pages.css** (or layout.css)
   - Add chart container styles
   - Add period selector button styles

## Similar Features Analyzed
- `renderValuation()` - Shows how to integrate data display into research results
- Activity log viewer - Example of dynamic content updates

## Technical Constraints
- No existing charting library - will need to add one
- yfinance rate limit: 0.2s between requests
- Provider timeout: configurable (default ~10s)

## Recommended Charting Library: Chart.js
- Lightweight (~60KB minified)
- Simple line chart API
- Supports annotations for fair value line
- CDN available: `https://cdn.jsdelivr.net/npm/chart.js`

## Integration Points
1. User enters ticker → `runValuation()` called
2. After valuation data loads → fetch price history in parallel or sequentially
3. Render chart below/alongside valuation cards
4. Period buttons trigger re-fetch with new period

## Data Flow
```
User selects ticker
    ↓
runValuation() → /api/valuation/<ticker>
    ↓
fetchPriceHistory() → /api/price-history/<ticker>?period=1y
    ↓
renderPriceChart(historyData, fairValue)
    ↓
Chart.js line chart with:
  - Price history line
  - Fair value horizontal line
  - Period selector buttons
```
