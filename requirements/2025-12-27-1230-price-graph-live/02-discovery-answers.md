# Discovery Answers

## Q1: Should the graph fetch data live on-demand when viewing a company (not stored/cached)?
**Answer:** Yes, fetch live
**Rationale:** API is fast (<0.5s for 5 years). Data always fresh, no storage complexity.

## Q2: Will the graph be displayed within the existing stock detail modal?
**Answer:** No - integrate into existing "Company Lookup" page
**User clarification:** "It's not a modal currently. It's already the 'Company Lookup' page"

## Q3: Should users be able to select different time periods (1m, 3m, 6m, 1y, etc.)?
**Answer:** Yes, selectable periods
**Rationale:** Standard feature for stock price graphs.

## Q4: Should the graph show additional overlays like fair value line or buy/sell markers?
**Answer:** Yes, include fair value line
**Scope:** Show calculated fair value as a horizontal reference line.

## Q5: Is this feature for any company in the index, or only for stocks the user holds?
**Answer:** Any company
**Rationale:** Research any company before buying. Works with Company Lookup for any ticker.
