# Discovery Questions — Split-Aware EPS Correction

Five yes/no questions to scope the problem space. Asked one at a time. Defaults reflect what the codebase suggests is the safest assumption.

---

## Q1: When a ticker has a qualifying split in the 8-year EPS window, should the system always replace EDGAR EPS values with split-adjusted EPS from a non-SEC provider (yfinance / defeatbeta / FMP)?
**Default if unknown:** Yes — this is the core intent of the feature; without replacement, the warning is informational only (which is what the prior iteration already shipped).

## Q2: Should the corrected-EPS fetch run automatically as part of every screener run for any ticker currently flagged with a split (i.e. with rows in `split_history`)?
**Default if unknown:** Yes — user said "automatic anytime a split is found" and "part of the data fetch process."

## Q3: Should the corrected-EPS fetch also run on the manual `POST /api/valuation/<ticker>/refresh` endpoint when a split exists for that ticker?
**Default if unknown:** Yes — manual refresh should produce the same end state as a screener run for that ticker; otherwise the warning state would be inconsistent between paths.

## Q4: Should the per-year EPS source (provider name, e.g. `sec`, `yfinance`, `defeatbeta`) be persisted in the `eps_history` table so the UI/valuation can tell, per row, which years came from which provider?
**Default if unknown:** Yes — user explicitly asked: "we should note the source of the EPS data so we can easily see if it's EDGAR or not." A dedicated column is the cleanest representation; the existing `eps_type` column stores the EPS *kind* (e.g. "Diluted EPS"), not the provider.

## Q5: When corrected (split-adjusted) EPS successfully replaces EDGAR data for the affected years, should the existing Stock Split Warning transition to a "corrected — valuation now accurate" state (instead of disappearing entirely)?
**Default if unknown:** Yes — user explicitly asked: "If it's the accurate EPS data, the warning should also reflect that so I can tell the valuation is actually accurate again." This means a tri-state badge: *uncorrected* (original warning) / *corrected* (success state) / *no warning at all* (no qualifying splits).
