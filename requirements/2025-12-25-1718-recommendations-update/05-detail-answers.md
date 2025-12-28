# Detail Answers

## Q1: Should Recommendations tab always reload when switching to it?
**Answer:** Yes - ensures recommendations are always current when viewed

## Q2: Should we add a 'growth mode' that doesn't penalize no-dividend growth stocks?
**Answer:** No - current value-oriented scoring is intentional

## Q3: Should we show score breakdown?
**Answer:** No - current 'reasons' list is sufficient

## Q4: When screener runs, refresh recommendations in background?
**Answer:** No - only refresh when actively viewing Recommendations tab

## Q5: Should we show why stocks were filtered out?
**Answer:** Yes, but with important change: **Remove the minimum EPS years requirement**. Only exclude stocks that have NO EPS data at all, not those with fewer than 5 years.

---

## Key Requirement Discovered

The user clarified that the **RECOMMENDATION_MIN_EPS_YEARS filter should be removed or reduced to 0**. Stocks should only be excluded if they have NO EPS data, not if they have fewer than 5 years of data. This will dramatically increase the number of stocks eligible for recommendations, including more NASDAQ 100 growth stocks.
