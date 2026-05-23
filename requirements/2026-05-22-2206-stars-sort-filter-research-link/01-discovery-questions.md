# Phase 2: Discovery Questions

The change is mostly UX. Logic is straightforward because `lookupTicker(ticker, event)`
already exists in `app.js` at line 2036 (used by Recommendations and Sell-candidates).

---

## Q1: Should the sort/filter controls apply globally (one set of controls affecting both Holdings and Watchlist sections), or independently per-section (each section has its own controls)?
**Default if unknown:** Single global control set above both sections. Simpler UI, and the typical user workflow ("show me only 5+ star tickers from anywhere") spans both.

## Q2: Should the ENTIRE tile (ticker card) be clickable to trigger Company Lookup, or only the ticker symbol within the tile?
**Default if unknown:** Entire tile. Larger click target, matches how recommendation tiles on the Recommendations tab feel. The tile has no other interactive elements (tooltips are hover-only) so no risk of accidental clicks.

## Q3: For the "highest percentage fair value" sort, do you mean "most undervalued first" (i.e., largest negative `price_vs_value` — best deals at top), or "highest fair value relative to price" (which is the same thing said differently)?
**Default if unknown:** Most undervalued first (largest negative `price_vs_value`). Matches the framing of the Recommendations tab. Tickers with no fair value sink to the bottom.
