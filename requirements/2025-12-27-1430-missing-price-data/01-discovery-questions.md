# Discovery Questions

## Q1: Should both fields (52-week high AND 3-month change) be fixed in this update?
**Default if unknown:** Yes (both are broken and should be fixed together)

## Q2: Is it acceptable if the batch refresh takes slightly longer to fetch the additional 52-week data?
**Default if unknown:** Yes (data completeness is more important than speed)

## Q3: Should the 3-month change use the oldest available price in the fetched range (rather than requiring data from exactly 90 days ago)?
**Default if unknown:** Yes (using available data is better than showing nothing)

## Q4: Do you want the "off high %" column to also be populated (calculated from price vs 52-week high)?
**Default if unknown:** Yes (it's a derived field that requires 52-week high)

## Q5: Should stocks that fail to return 52-week data still show their other data (prices, valuations) rather than being marked as failed?
**Default if unknown:** Yes (partial data is better than no data)
