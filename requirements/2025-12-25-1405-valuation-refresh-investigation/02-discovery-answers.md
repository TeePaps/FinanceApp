# Discovery Answers

## Q1: Did you run a full screener refresh recently (the one that fetches EPS from SEC)?
**Answer:** Yes - ran "Full Update" from Refresh dropdown

**Analysis:** The Full Update should run all 4 phases including SEC EPS fetch. However, current DB shows only 533 tickers with SEC data vs 2,433 in old DB. Something is limiting the SEC fetch.

## Q2: Were you using this app with the old database for an extended period before some recent change?
**Answer:** Yes

**Analysis:** This confirms the old database accumulated SEC EPS data over many refresh cycles. The current database is starting fresh and would need to rebuild that cache.

## Q3: Is the current valuation coverage acceptable for active indexes?
**Answer:** User clarified they only care about active indexes.

**Findings:**
- dow30: 96.7% coverage (29/30)
- sp500: 97.4% coverage (488/501)
- nasdaq100: 85.1% coverage (86/101)

Active indexes have good coverage. The low 22% overall was due to disabled indexes.
