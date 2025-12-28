# Discovery Questions

Based on initial codebase and database analysis, here are the key questions to understand the problem:

## Q1: Did you run a full screener refresh recently (the one that fetches EPS from SEC)?
**Default if unknown:** Yes (based on timestamps showing today's date in both DBs)

**Why this matters:** The current DB shows only 533 tickers with EPS history vs 2433 in the old DB. The full screener's Phase 1 fetches EPS from SEC - if this didn't complete properly, valuations would be missing.

## Q2: Were you using this app with the old database (in _ARCHIVE) for an extended period before some recent change?
**Default if unknown:** Yes (the old DB has 1552 valuations vs only 555 in current)

**Why this matters:** The old database has substantially more EPS data accumulated over time. If the current DB was reset or recreated recently, historical EPS data would be lost.

## Q3: Did you recently modify or reset the public.db database file?
**Default if unknown:** Yes (current DB appears to have fresh structure but missing historical EPS data)

**Why this matters:** The schema is identical, but current DB has 78% less EPS history data. This suggests database was recreated or data was lost.

## Q4: When you run a "full refresh" from the UI, does it complete all 4 phases (EPS, Dividends, Prices, Valuations)?
**Default if unknown:** No (based on data patterns - prices exist but EPS is mostly missing)

**Why this matters:** Current DB has 2539 tickers with price data but only 555 with complete valuations. This suggests Phase 3 (prices) ran but Phase 1 (EPS) or Phase 4 (valuation calculation) didn't complete.

## Q5: Are you expecting the SEC EPS data fetch to work for all ~2500 tickers in a single refresh run?
**Default if unknown:** Yes (SEC API has rate limits - 10 req/sec - but should work with backoff)

**Why this matters:** SEC rate limits might be causing the EPS fetch to fail silently for most tickers, resulting in missing valuations since the formula requires EPS.
