# Detail Questions

Based on codebase analysis, here are the key decisions needed:

## Q1: Should the Recommendations tab always reload when you switch to it (to ensure fresh data)?
**Default if unknown:** Yes - ensures recommendations are always current when viewed

## Q2: The current scoring penalizes stocks without dividends. Should we add a "growth mode" that doesn't penalize no-dividend growth stocks?
**Default if unknown:** No - current value-oriented scoring is intentional for this use case

## Q3: Should we show a breakdown of WHY certain stocks scored higher (e.g., "Undervalue: +30, Dividend: +5, Selloff: +10")?
**Default if unknown:** No - current "reasons" list is sufficient explanation

## Q4: When running the screener, should we refresh recommendations in the background even if user is on a different tab?
**Default if unknown:** Yes - this ensures recommendations are ready when user switches tabs

## Q5: Should we show how many stocks were filtered out and why (e.g., "85 stocks excluded: 50 insufficient EPS data, 35 overvalued")?
**Default if unknown:** No - keep UI simple, just show "Analyzed X stocks"
