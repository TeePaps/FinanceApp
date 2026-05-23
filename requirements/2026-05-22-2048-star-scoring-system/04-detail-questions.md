# Phase 4: Expert Detail Questions

These are technical questions a senior developer with deep codebase knowledge would ask before implementation.

---

## Q1: Should the new tab show ALL tracked tickers (with non-holdings capped at 4 stars since criteria 5 & 6 are holdings-only), rather than only the user's holdings?
**Default if unknown:** Yes — show all tracked tickers. Non-holdings get max 4 stars (criteria 5 & 6 always unearned). This matches user's "sort of a new screener system" framing and lets users discover quality stocks they don't own yet.

## Q2: For Criterion 1 (earnings beat), should we compare the most recent REPORTED EPS to the prior analyst consensus estimate (i.e., did the company actually beat consensus when results came out), rather than comparing forward guidance to forward estimates?
**Default if unknown:** Yes — use historical "did they beat" rather than forward guidance. Reasons: (1) yfinance and FMP both expose this cleanly via earnings history endpoints; (2) forward company guidance is rarely available in free APIs; (3) "did they beat last quarter" is a stable, well-defined signal that updates only quarterly.

## Q3: For historical comparisons (Criterion 2 fair value vs. last year, Criterion 3 dividend vs. last year), should we BACKFILL historical values by recomputing from existing data (EPS history table, yfinance dividend history) on first run, rather than waiting a year for snapshots to accumulate?
**Default if unknown:** Yes — backfill on first run. EPS history is already stored (`eps_history` table); we can recompute the 8-year fair value formula for any past date by shifting the EPS window. yfinance returns full dividend history. Otherwise the feature is useless for ~12 months.

## Q4: For Criterion 4 (debt-to-capital ≤ 25%), should we use the standard formula `Total Debt / (Total Debt + Book Equity)` where Total Debt = Long-Term Debt + Short-Term Debt, sourced from SEC EDGAR us-gaap concepts (`LongTermDebtNoncurrent`, `DebtCurrent`, `StockholdersEquity`)?
**Default if unknown:** Yes — standard formula, SEC source. Reasons: (1) free + authoritative; (2) `sec_provider.py` already has the companyfacts integration pattern; (3) avoids dependency on paid FMP balance-sheet endpoint.

## Q5: For Criterion 6 (undervalued, holdings-only), should we reuse the existing undervaluation logic from `services/recommendations.py` (a ticker is "undervalued" when `current_price < estimated_value`, i.e. `price_vs_value < 1.0`), rather than introducing a new threshold?
**Default if unknown:** Yes — reuse existing `price_vs_value < 1.0` definition. Consistent with recommendations tab. If you want a stricter "deeply undervalued" threshold (e.g., `< 0.8`), specify it here.
