# Discovery Questions

## Q1: Does the click issue happen specifically after filtering or sorting the table?
**Default if unknown:** Yes (filtering/sorting triggers `renderScreenerTable()` which adds a NEW event listener each time without removing the old one)

## Q2: Does clicking on the undervalued cards at the top work correctly?
**Default if unknown:** Yes (these are rendered once, so less likely affected)

## Q3: Does the issue occur more often after using the page for a while (vs immediately on page load)?
**Default if unknown:** Yes (suggests event listener accumulation or DOM manipulation issue)

## Q4: When clicking doesn't work, can you still see the hover effect (card moves up or row highlights)?
**Default if unknown:** Yes (CSS hover still works, meaning element exists but click handler fails)

## Q5: Does refreshing the page (F5) temporarily fix the issue?
**Default if unknown:** Yes (suggests accumulated state or multiple event handlers issue)
