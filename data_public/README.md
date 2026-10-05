# data_public/

Public market data. Everything here can be deleted and rebuilt; contents are gitignored.

- `public.db` — SQLite: tickers, valuations, EPS history, indexes, provider feed events.
  Created empty on first run and filled by the screener / scheduled syncs.

In an installed copy this folder lives at `<install home>/data/data_public/` (see `paths.py`).
