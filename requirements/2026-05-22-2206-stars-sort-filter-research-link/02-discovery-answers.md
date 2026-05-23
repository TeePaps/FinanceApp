# Phase 2: Discovery Answers

## Q1: Control scope?
**Answer:** Global — one set of controls above both sections.

**Implication:** Add a single controls bar between the legend and "My Holdings" section. Sort + filter inputs there affect both `renderStarsList()` calls.

---

## Q2: Click area?
**Answer:** Entire tile.

**Implication:** Wrap each `.star-row` div in something with a click handler calling `lookupTicker(row.ticker, event)`. Add `cursor: pointer` + hover state. No additional buttons or links inside the row.

---

## Q3: Value sort direction?
**Answer:** Most undervalued first (largest negative `price_vs_value`).

**Implication:** Sort ascending by `price_vs_value`. Tickers with `price_vs_value == null` go to the bottom (sentinel = `+Infinity`).
