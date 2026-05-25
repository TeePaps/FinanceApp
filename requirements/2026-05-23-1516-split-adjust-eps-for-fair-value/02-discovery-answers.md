# Phase 2: Discovery Answers

## Q1: Split-adjust EPS instead of suppressing?
**Answer:** Yes — adjust EPS.

**Implication:** New helper `get_split_adjusted_eps_history(ticker)` that
reads `eps_history` + `split_history` and returns adjusted EPS values. Used
by every code path that averages EPS for fair value.

---

## Q2: Handle reverse splits?
**Answer:** Yes — handle both.

**Implication:** `adjusted_eps = pre_split_eps / split_ratio` works for both
forward (ratio > 1 → divides) and reverse (ratio < 1 → effectively multiplies).
No special-casing needed.

---

## Q3: Apply to Stars criterion 2 backfill?
**Answer:** Yes — use everywhere.

**Implication:** `services/stars.py:_prior_year_fair_value_from_eps()` and
the `_check_fair_value_up` / `_explain_fair_value_up` paths consume the new
split-adjusted helper, not raw `db.get_eps_history()`.
