# Initial Request — Split-Aware EPS Correction

**Captured:** 2026-04-19 08:06
**Slug:** `split-eps-correction`
**Builds on:** `2026-04-18-2026-split-warning` (Split Warning feature)

## Verbatim user request

> given we know that splits will cause the edgar data to be invalid, maybe anything we have marked in that split database should have some automatic functionality to pull the EPS and overwrite the Edgar data with the corrected EPS data after the split. I don't know where we'd need to do tracking across the different databases, but the fetch should be part of the data fetch process and automatic anytime a split is found, to ensure we have the correct data, and we should note the source of the EPS data so we can easily see if it's EDGAR or not. If it's the accurate EPS data, the warning should also reflect that so I can tell the valuation is actually accurate again, but show the warning if the post-split EPS is not present as the original warning

## Plain-English restatement

When a stock split is detected (i.e. a row exists in the new `split_history` table from the prior Split Warning feature), the system should automatically attempt to fetch **post-split-adjusted EPS** from a non-SEC provider and use it to overwrite the unadjusted EPS values that EDGAR returned. We need to:

1. Track the **source** of each EPS value in `eps_history` so EDGAR vs. corrected (split-adjusted) data is distinguishable.
2. Trigger the corrected-EPS fetch automatically whenever a split is found, as part of the data fetch pipeline (screener + manual refresh).
3. Update the existing **Split Warning** so:
   - If post-split EPS *is* present (i.e. corrected data successfully replaced EDGAR's), the warning indicates the valuation is now accurate (or shows a "corrected" state).
   - If post-split EPS is *not* present, the original warning is shown unchanged.

## Out-of-scope (assumed)

- Changing the fair-value formula itself.
- Re-deriving EPS from raw SEC XBRL filings (we are using a different provider for the corrected values, not transforming SEC data ourselves).
