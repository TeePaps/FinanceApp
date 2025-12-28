# Requirements Specification: Price History Graph

## Problem Statement
Users need to visualize stock price trends over time when researching companies in the Company Lookup tab. Currently, users only see static valuation data without historical context.

## Solution Overview
Add an interactive price history chart to the Company Lookup page that:
- Fetches live price data on-demand (no storage needed)
- Displays a line chart with selectable time periods
- Shows a horizontal fair value reference line
- Uses Chart.js for lightweight, simple charting

## API Performance Validation
| Period | Response Time | Data Points |
|--------|---------------|-------------|
| 1 month | 0.4s | 20 pts |
| 1 year | 0.15s | 250 pts |
| 5 years | 0.18s | 1256 pts |

Live fetching is fast enough - no caching/database storage required.

---

## Functional Requirements

### FR1: Price History API Endpoint
- **Route:** `GET /api/price-history/<ticker>?period=<period>`
- **Periods:** `1m`, `3m`, `6m`, `1y`, `2y`, `5y`
- **Response:**
```json
{
  "ticker": "AAPL",
  "period": "1y",
  "prices": [
    {"date": "2024-12-27", "price": 175.50},
    {"date": "2024-12-26", "price": 174.80}
  ],
  "current_price": 175.50
}
```

### FR2: Chart Display
- Chart loads automatically after valuation data loads
- Line chart showing closing prices over selected period
- X-axis: Date labels (monthly for 1y+, weekly for shorter)
- Y-axis: Price in dollars
- Absolute prices (not percentage change)

### FR3: Fair Value Line
- Horizontal dotted line at calculated fair value
- Different color (e.g., green) from price line
- Label showing "Fair Value: $X.XX"
- Uses `estimated_value` from valuation response

### FR4: Period Selector
- Button group: 1M | 3M | 6M | 1Y | 2Y | 5Y
- Default selection: 1Y (1 year)
- Clicking a period refreshes chart with new data
- Current period button highlighted

### FR5: Location
- Displayed in Company Lookup tab (`#research-results`)
- Below valuation summary cards, above EPS history table
- Chart container with consistent styling

---

## Technical Requirements

### TR1: Chart.js Library
- Add via CDN: `https://cdn.jsdelivr.net/npm/chart.js`
- Use annotation plugin for fair value line: `https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation`
- Add to `templates/index.html` before app.js

### TR2: Files to Modify

**templates/index.html**
- Add Chart.js and annotation plugin scripts

**static/app.js**
- Add `fetchPriceHistory(ticker, period)` async function
- Add `renderPriceChart(historyData, fairValue)` function
- Modify `renderValuation()` to include chart container and trigger chart load
- Add `changePriceChartPeriod(period)` for period buttons
- Store chart instance globally for destruction/recreation

**routes/valuation.py** (or new routes/price.py)
- Add `@valuation_bp.route('/price-history/<ticker>')` endpoint
- Use `orchestrator.fetch_price_history(ticker, period)`
- Transform `HistoricalPriceData.prices` dict to sorted array

**static/css/pages.css** (or layout.css)
- Add `.price-chart-container` styles
- Add `.period-selector` button group styles
- Add `.period-btn.active` state

### TR3: Data Flow
```
runValuation()
  → fetch /api/valuation/<ticker>
  → renderValuation(data)
    → render chart container with period buttons
    → fetchPriceHistory(ticker, '1y')
      → fetch /api/price-history/<ticker>?period=1y
      → renderPriceChart(historyData, data.estimated_value)
```

### TR4: Chart Configuration
```javascript
{
  type: 'line',
  data: {
    labels: dates,
    datasets: [{
      label: 'Price',
      data: prices,
      borderColor: '#4a90d9',
      tension: 0.1
    }]
  },
  options: {
    responsive: true,
    plugins: {
      annotation: {
        annotations: {
          fairValueLine: {
            type: 'line',
            yMin: fairValue,
            yMax: fairValue,
            borderColor: '#22c55e',
            borderDash: [5, 5],
            label: { content: `Fair Value: $${fairValue}` }
          }
        }
      }
    }
  }
}
```

---

## Implementation Hints

### Pattern to Follow
Follow existing `renderValuation()` pattern:
1. Build HTML string with container
2. Insert into `#research-results`
3. After DOM update, initialize Chart.js instance

### Provider System Usage
```python
from services.providers import get_orchestrator

orchestrator = get_orchestrator()
result = orchestrator.fetch_price_history(ticker, period)
if result.success:
    prices = result.data.prices  # Dict[str, float]
    # Convert to sorted list for JSON
```

### Chart Instance Management
```javascript
let priceChartInstance = null;

function renderPriceChart(data, fairValue) {
    // Destroy previous instance
    if (priceChartInstance) {
        priceChartInstance.destroy();
    }

    const ctx = document.getElementById('price-chart').getContext('2d');
    priceChartInstance = new Chart(ctx, config);
}
```

---

## Acceptance Criteria

1. [ ] User can view price history chart when looking up any company
2. [ ] Chart loads automatically after entering a ticker
3. [ ] Period buttons (1M, 3M, 6M, 1Y, 2Y, 5Y) change displayed data
4. [ ] Default period is 1 year
5. [ ] Fair value line displays at calculated estimated value
6. [ ] Chart shows absolute dollar prices
7. [ ] Loading state shown while fetching data
8. [ ] Error handling for failed API requests
9. [ ] Chart is responsive to container width

---

## Assumptions

1. Fair value line does NOT auto-update if user clicks "Refresh" button (requires page reload)
2. Chart is placed below valuation cards, above EPS table
3. yfinance is primary provider for price history (no additional API keys needed)
4. Chart uses default Chart.js styling with minor customization

---

## Out of Scope (Future Enhancements)
- Transaction markers (buy/sell points) on chart
- Volume overlay
- Multiple ticker comparison
- Candlestick chart option
- Price caching/database storage
- 52-week high/low markers
