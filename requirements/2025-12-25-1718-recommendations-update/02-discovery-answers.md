# Discovery Answers

## Q1: Do you want recommendations to include ALL indexes?
**Answer:** No - recommendations should respect enabled indexes setting

## Q2: Do you want the recommendation limit increased from 10?
**Answer:** No, 10 is good

## Q3: Should recommendations auto-refresh when valuation data is updated?
**Answer:** Yes - recommendations should automatically update when underlying data changes

## Q4: Do you want tracked portfolio stocks in recommendations?
**Answer:** No - tracked stocks have separate analysis in Holdings tab

## Q5: Are you seeing ALL enabled indexes or only S&P 500?
**Answer:** **Only S&P 500 shown** - even when other indexes are enabled, only S&P 500 stocks appear

---

## Key Finding: **BUG CONFIRMED**

The user confirms that even with multiple indexes enabled in Settings, only S&P 500 stocks are appearing in recommendations. This indicates a filtering or data bug that needs investigation.
