# Expert Detail Questions

## Q1: Should Phase 4 fallback to database eps_history when in-memory eps_results is missing?
**Default if unknown:** Yes (this is a clear bug - data exists but isn't used)

**Context:** CAG has 8 years of EPS in eps_history table, but the Full Update didn't compute its valuation because Phase 1 didn't populate eps_results for it. A simple DB lookup fallback would fix this.

## Q2: For the 18 tickers that SHOULD work (SEC has USD EPS), should we investigate why Phase 1 skipped them?
**Default if unknown:** Yes (these are major companies like BRK-B, SHOP, MELI)

**Context:** Testing shows SEC API returns valid data for these tickers. The screener's Phase 1 either didn't try them or failed silently.

## Q3: For tickers without standard EPS fields (V, AZN, etc.), should we use yfinance as the authoritative EPS source?
**Default if unknown:** Yes (better to have yfinance EPS than no valuation)

**Context:** 9 tickers in active indexes don't have EarningsPerShareDiluted in SEC filings. yfinance can provide EPS for these companies.

## Q4: Should we add a "repair" function to recalculate valuations from existing eps_history data?
**Default if unknown:** Yes (quick fix for current data, doesn't require re-fetching from SEC)

**Context:** This would immediately fix CAG and any other tickers with eps_history but missing valuations. Could be triggered manually or automatically.

## Q5: Is 85-97% coverage acceptable for active indexes, or do you need 100%?
**Default if unknown:** 85%+ is acceptable (the missing ones are edge cases)

**Context:**
- dow30: 96.7% (missing V - Visa uses non-standard reporting)
- sp500: 97.4% (13 missing)
- nasdaq100: 85.1% (15 missing, including foreign companies)
