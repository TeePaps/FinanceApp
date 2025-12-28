# Detail Answers

## Q1: For the 3-month change, should we simply use the oldest available price from the fetched data range?
**Answer:** Yes

## Q2: Should we apply the same fix to the 1-month change calculation (use oldest price if 30-day target isn't available)?
**Answer:** Yes

## Q3: Should we fetch 52-week data in parallel (multiple stocks at once) to minimize the speed impact on batch refreshes?
**Answer:** No - add it as a final phase in the existing refresh algorithms that runs after the current phases complete, with appropriate delays between calls to respect API rate limits.

## Q4: While fetching 52-week data, should we also update the company name if it's currently just the ticker symbol?
**Answer:** No - company name is a separate issue that should be fixed at its source, not as a side effect of this fix.

## Q5: If the 52-week data fetch fails for a specific stock, should the system retry it once before giving up?
**Answer:** No (rely on existing orchestrator retry logic)
