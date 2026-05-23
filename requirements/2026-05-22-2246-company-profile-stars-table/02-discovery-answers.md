# Phase 2: Discovery Answers

## Q1: Always show all 6 rows?
**Answer:** Show all 6 always.

**Implication:** Each row always renders. For criterion 6 (Buybacks) on a non-holding, the row shows "Only applies to holdings" in the values column. For missing data (e.g., no analyst estimates for an obscure ticker), the row shows "—" or "no data" with a short note.

---

## Q2: New endpoint vs embed in /api/valuation?
**Answer:** New dedicated endpoint.

**Implication:** Create `/api/stars/<ticker>/explanation` returning the 6 criteria with underlying numbers. Keeps the main valuation endpoint lean and lets the table lazy-load after the rest of the company profile renders.

---

## Q3: Where on the page?
**Answer:** Directly under the existing fair-value formula.

**Implication:** Insert the table placeholder in `renderValuation()` right after the formula block. Always visible (no collapsing).
