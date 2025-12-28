# Discovery Answers

## Q1: Is the issue affecting all tracked stocks, or only specific tickers?
**Answer:** Only specific tickers (confirmed)

## Q2: Was a price-only update recently run (Quick or All Prices update) rather than a Full Update?
**Answer:** Unknown - using default: Yes

## Q3: Are the missing fields specifically EPS-related fields (EPS Avg, EPS Years, Source, Est Value, vs Value)?
**Answer:** Yes (confirmed)

## Q4: Are the affected tickers newly added to the index that haven't been processed by a Full Update yet?
**Answer:** No - these are existing stocks that should already have EPS data

## Q5: Do you expect these tickers to have SEC EPS data available, or are they companies without 10-K filings?
**Answer:** Yes - they should have SEC data available

---

## Summary
The issue affects existing tickers that should have SEC EPS data. The EPS-related fields (EPS Avg, EPS Years, Source, Est Value, vs Value) are showing as "-" while Price is populated. This suggests either:
1. EPS fetching failed for these specific tickers
2. SEC data was never fetched for them
3. Data is missing in the database for these records
