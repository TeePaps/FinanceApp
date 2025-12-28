# Context Findings

## Key Discovery: User's Perception vs Reality

The user reported "Recommendations seems to only be showing S&P 500" - but this is **not a bug**. The current top 10 scoring stocks all happen to be S&P 500-only stocks. NASDAQ and Dow stocks ARE included in the recommendations pool (71 analyzed stocks include 17 NASDAQ 100 and 3 DJIA stocks).

### Why Top 10 Are All S&P 500:
The scoring algorithm favors:
1. **Undervaluation** - negative price_vs_value
2. **Dividend yield** - higher is better, no dividend = penalty
3. **Selloff depth** - stocks down from highs

Many NASDAQ 100 stocks are high-growth tech with:
- No dividends (penalty in scoring)
- High valuations (price_vs_value is positive/overvalued)
- Lower eps_years (many don't meet 5-year minimum)

This naturally pushes value-oriented S&P 500 stocks to the top.

## Files Analyzed

### Core Recommendation Logic
- `services/recommendations.py` - Scoring algorithm, `get_top_recommendations()`
- `routes/screener.py:189-201` - `/api/recommendations` endpoint

### Data Flow
1. `data_manager.load_valuations()` - Gets all valuation data
2. `data_manager.get_all_ticker_indexes()` - Gets enabled index membership
3. `get_top_recommendations()` - Filters and scores

### Current Refresh Behavior
- `static/app.js:162` - `reloadCurrentView()` called after screener completes
- `static/app.js:1860` - If on recommendations tab, `loadRecommendations()` is called
- BUT: If user is on a different tab during screener, recommendations won't auto-refresh

## Actual Issues Found

### 1. Recommendations Only Refresh on Active Tab
When screener completes, `reloadCurrentView()` only refreshes the **current** tab. If user runs screener from Datasets tab and later switches to Recommendations, they see stale data.

### 2. Data Quality Limits NASDAQ Representation
Many NASDAQ stocks fail the `RECOMMENDATION_MIN_EPS_YEARS=5` filter:
- 84 of 101 NASDAQ stocks fail due to insufficient EPS years or missing data
- Only 17 NASDAQ stocks pass all filters

### 3. No Cache Invalidation
`data_manager._ticker_index_cache` is only rebuilt when enabled indexes change, not after screener updates valuations.

## Integration Points

### Where Recommendations Get Data From
```
public.db:valuations → data_manager.load_valuations()
public.db:ticker_indexes → data_manager.get_all_ticker_indexes()
```

### Where Updates Happen
- `services/screener.py` - Updates valuations during screener run
- `routes/screener.py` - Price refresh endpoints
- `app.py` - Various update routes

## Recommendation Engine Calculation Verification

The scoring in `services/recommendations.py:22-77` is working correctly:
- `score_stock()` calculates composite score
- Weights from `config.py:SCORING_WEIGHTS`
- Filtering at line 157-170 correctly excludes stocks without data
