# Discovery Questions

## Q1: Is the issue affecting all tracked stocks, or only specific tickers?
**Default if unknown:** Only specific tickers (the data shows some stocks have complete data while others only have prices)

## Q2: Was a price-only update recently run (Quick or All Prices update) rather than a Full Update?
**Default if unknown:** Yes (likely, since price data exists but EPS/valuation data is missing for some tickers)

## Q3: Are the missing fields specifically EPS-related fields (EPS Avg, EPS Years, Source, Est Value, vs Value)?
**Default if unknown:** Yes (based on the description, all fields except Price are showing as "-")

## Q4: Are the affected tickers newly added to the index that haven't been processed by a Full Update yet?
**Default if unknown:** Yes (this explains why they have price but no EPS data)

## Q5: Do you expect these tickers to have SEC EPS data available, or are they companies without 10-K filings (like foreign companies, REITs, etc.)?
**Default if unknown:** Most should have SEC data available (assuming standard US companies in major indexes)
