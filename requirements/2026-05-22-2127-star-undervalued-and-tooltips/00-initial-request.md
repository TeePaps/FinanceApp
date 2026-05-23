# Initial Request: Make Undervalued Apply to All + Star Tooltips

**Date:** 2026-05-22
**Requested by:** tp@promenet.com
**Related work:** Extends the recently-shipped `2026-05-22-2048-star-scoring-system` feature.

## Original Request

> Undervalued — current price < fair value (holdings only) star should really be
> for non-holdings too. So you should change order of 5 and 6. Also, from UI, I
> should be able to hover the star to give a tip about what the star indicates

## Two Changes

### Change A — Make "Undervalued" apply to all tickers

Currently Criterion 6 (Undervalued: `price < fair value`) is holdings-only.
Move it to apply to ALL tickers (holdings AND watchlist). Swap positions 5↔6 so:

| # | Old | New |
|---|---|---|
| 5 | Share Buybacks (holdings only) | **Undervalued (all tickers)** |
| 6 | Undervalued (holdings only)    | **Share Buybacks (holdings only)** |

After the change, Share Buybacks is the only holdings-only criterion. Watchlist
max becomes 5 stars (up from 4); Holdings max stays at 6.

### Change B — Hover tooltips on star icons

Each star icon should show a tooltip on hover explaining what the criterion
represents. (The current implementation already sets the HTML `title` attribute
on each star, so this requires verifying behavior — possibly upgrading to a
richer CSS-based tooltip if native browser tooltips aren't satisfactory.)
