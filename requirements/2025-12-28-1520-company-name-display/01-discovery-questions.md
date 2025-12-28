# Discovery Questions

## Q1: Does this happen for specific tickers, or can you provide an example ticker that shows the issue?
**Default if unknown:** Yes - likely specific tickers where yfinance or SEC data doesn't have the company name

## Q2: Does the issue occur on first lookup of a ticker, or also for tickers you've looked up before?
**Default if unknown:** Both - likely a data source issue rather than caching

## Q3: Is the issue visible immediately when the Company Research page loads, or after clicking something?
**Default if unknown:** Immediately on load - it's about how the API returns data

## Q4: Have you noticed if this happens more with newer or smaller companies?
**Default if unknown:** Yes - smaller/newer companies may not have complete data in yfinance

## Q5: Is there a "N/A" or similar indicator, or does it literally show the ticker twice (e.g., "AAPL - AAPL")?
**Default if unknown:** Shows ticker twice (based on description)
