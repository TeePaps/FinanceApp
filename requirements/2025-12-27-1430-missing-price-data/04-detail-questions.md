# Expert Requirements Questions

## Q1: For the 3-month change, should we simply use the oldest available price from the fetched data range?
**Context:** Currently the code looks for a price from exactly 90 days ago. When only 88-89 days of data is available, it returns nothing. The fix would use whatever the oldest price is in the range.
**Default if unknown:** Yes (oldest available price is a reasonable approximation)

## Q2: Should we apply the same fix to the 1-month change calculation (use oldest price if 30-day target isn't available)?
**Context:** The 1-month calculation has similar logic but works more often because 3 months of data usually includes 30+ days. However, for consistency and edge cases, the same fix could apply.
**Default if unknown:** Yes (consistent logic across both calculations)

## Q3: Should we fetch 52-week data in parallel (multiple stocks at once) to minimize the speed impact on batch refreshes?
**Context:** Getting 52-week data requires individual API calls per stock. Running these in parallel would be faster but uses more system resources.
**Default if unknown:** Yes (speed is important for batch operations with 500+ stocks)

## Q4: While fetching 52-week data, should we also update the company name if it's currently just the ticker symbol?
**Context:** Some stocks show "CAG" as the company name instead of "Conagra Brands, Inc." The 52-week fetch also returns the proper company name and could fix this.
**Default if unknown:** Yes (improves data quality with no extra API calls)

## Q5: If the 52-week data fetch fails for a specific stock, should the system retry it once before giving up?
**Context:** API calls occasionally fail due to rate limits or network issues. A single retry could recover from transient failures.
**Default if unknown:** No (existing retry logic in the orchestrator handles this; avoid duplicate retries)
