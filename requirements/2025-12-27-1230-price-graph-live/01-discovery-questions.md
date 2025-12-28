# Discovery Questions

## API Testing Results (Prerequisite)
Before starting requirements, we tested the yfinance API response times:

| Period | Time | Data Points |
|--------|------|-------------|
| 1 month | 0.394s | 20 points |
| 3 months | 0.098s | 63 points |
| 6 months | 0.081s | 127 points |
| 1 year | 0.146s | 250 points |
| 2 years | 0.113s | 502 points |
| 5 years | 0.184s | 1256 points |

**Conclusion**: API is fast enough (<0.5s) for live fetching. No caching/temporary storage required for reasonable timeframes.

---

## Q1: Should the graph show data on-demand when viewing a company (not stored)?
**Default if unknown:** Yes (based on fast API response times, live fetching is feasible and keeps data fresh)

## Q2: Will the graph be displayed within an existing stock detail view (modal or page)?
**Default if unknown:** Yes (most natural place for per-company graphs is the existing stock detail modal)

## Q3: Should users be able to select different time periods (1m, 3m, 1y, etc.)?
**Default if unknown:** Yes (standard feature for stock price graphs)

## Q4: Should the graph show additional data like fair value line or buy/sell transaction markers?
**Default if unknown:** No (start simple with just price history, can add overlays later)

## Q5: Is this feature for any company in the index, or only for stocks the user holds?
**Default if unknown:** Any company (users often want to research before buying)
