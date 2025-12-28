# Expert Detail Answers

## Q1: Should Phase 4 fallback to database eps_history when in-memory eps_results is missing?
**Answer:** Yes

**Action:** Add database lookup fallback in Phase 4 of screener to use existing eps_history data when eps_results dict doesn't have the ticker.

## Q2: For the 18 tickers that SHOULD work (SEC has USD EPS), should we investigate why Phase 1 skipped them?
**Answer:** Yes

**Action:** Add logging/debugging to Phase 1 to track which tickers are attempted, which succeed, and which fail. Identify why BRK-B, SHOP, MELI, etc. weren't fetched.

## Q3: For tickers without standard EPS fields (V, AZN, etc.), should we use yfinance as the authoritative EPS source?
**Answer:** No - leave them without valuations

**Rationale:** SEC should be authoritative. If a company doesn't report standard EPS fields in SEC filings, we accept that they won't have valuations rather than using less reliable yfinance data. Only use yfinance if SEC API is completely inaccessible.

## Q4: Should we add a "repair" function to recalculate valuations from existing eps_history data?
**Answer:** No - implement permanent solutions instead

**Rationale:** One-time fixes are band-aids. The permanent fix (Q1: database fallback in Phase 4) will automatically use existing eps_history data on every Full Update run. No need for separate repair utilities.

## Q5: Is 85-97% coverage acceptable for active indexes, or do you need closer to 100%?
**Answer:** Closer to 100% - investigate harder for missing tickers

**Rationale:** If SEC has the data, we should be able to get it. Need to investigate alternative EPS fields for companies like V, AZN, KKR that don't use standard reporting.
