# Detail Answers

## Q1: Should the chart load automatically when viewing a company?
**Answer:** Yes, auto-load
**Rationale:** Fast (<0.5s), provides immediate value.

## Q2: Should the period selector default to 1 year?
**Answer:** Yes, 1 year default
**Rationale:** Matches annual valuation timeframe. Good balance of context and detail.

## Q3: Should the chart show absolute prices or percentage change?
**Answer:** Absolute prices
**Rationale:** Shows actual dollar values. Easy to compare with fair value line.

## Q4: Should the fair value line update if the user refreshes the valuation?
**Answer:** No, requires page reload
**Rationale:** Simpler implementation. User can reload if needed.

## Q5: Which charting library should we use?
**Answer:** Chart.js
**Rationale:** Lightweight (60KB), simple API, supports annotations. Most popular.
