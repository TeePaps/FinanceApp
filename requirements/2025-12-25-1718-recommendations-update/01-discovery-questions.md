# Discovery Questions

Based on my analysis of the codebase, the Investment Recommendations feature:
- Filters stocks by **enabled indexes** (stored in `indexes` table with `enabled` flag)
- Uses `get_all_ticker_indexes()` which only returns tickers from enabled indexes
- Has `filter_by_index=True` hardcoded in `routes/screener.py:200`

The recommendations only show S&P 500 stocks because that may be the only enabled index.

## Q1: Do you want recommendations to include ALL indexes (not just enabled ones)?
**Default if unknown:** No - recommendations should respect the enabled indexes setting in the Settings tab

## Q2: Do you want the recommendation limit increased from 10 stocks?
**Default if unknown:** No - 10 is a good default for focused recommendations

## Q3: Should recommendations auto-refresh when valuation data is updated (screener runs, price refresh, etc.)?
**Default if unknown:** Yes - recommendations should always reflect current data

## Q4: Do you want to see stocks from your tracked portfolio in recommendations (even if not in an enabled index)?
**Default if unknown:** No - tracked stocks have separate analysis in Holdings tab

## Q5: Are you seeing ALL your enabled indexes in recommendations, or only S&P 500 stocks even when other indexes are enabled?
**Default if unknown:** Only S&P 500 even when others enabled - this would indicate a filtering bug
