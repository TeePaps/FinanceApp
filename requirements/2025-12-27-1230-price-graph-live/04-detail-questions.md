# Expert Detail Questions

Based on deep analysis of the codebase, these are technical clarification questions:

## Q1: Should the chart load automatically when viewing a company, or via a separate "Show Chart" button?
**Default if unknown:** Yes, auto-load (fetching is fast <0.5s, provides immediate value)

## Q2: Should the period selector default to 1 year (showing price trend in context of annual EPS)?
**Default if unknown:** Yes (1 year aligns with annual EPS/dividend data used in valuation)

## Q3: Should the chart show percentage change from period start (relative view) or absolute prices?
**Default if unknown:** Absolute prices (easier to compare to fair value line)

## Q4: Should the fair value line update if the user refreshes the valuation while viewing the chart?
**Default if unknown:** Yes (keeps chart and valuation data synchronized)

## Q5: Should we use Chart.js library (lightweight, simple) or a more feature-rich option like ApexCharts?
**Default if unknown:** Chart.js (60KB, simple API, sufficient for line chart with annotation)
