# Stock Portfolio Tracker

A Flask-based web application for tracking stock portfolios, analyzing market valuations, and finding investment opportunities.

## Features

- **Portfolio Management**: Track your stock holdings, transactions, and realized profits using FIFO cost basis
- **Market Analysis**: Screen stocks across major indices (S&P 500, NASDAQ 100, Dow 30, S&P 600, Russell 2000)
- **Valuation Engine**: Calculate fair values based on historical EPS data from SEC filings
- **Recommendations**: AI-scored stock recommendations based on undervaluation, dividend yield, and selloff pressure
- **Company Lookup**: Research individual stocks with detailed valuation metrics and direct links to SEC 10-K filings
- **Profit Timeline**: Visualize your trading performance over time

## Tech Stack

- **Backend**: Python/Flask
- **Frontend**: Vanilla JavaScript, HTML, CSS
- **Data Sources**: Yahoo Finance (yfinance), SEC EDGAR API
- **Storage**: SQLite databases (separate public/private data)

## Data Architecture

The app uses two separate SQLite databases for data isolation:

```
data_private/private.db    # Personal data (holdings, transactions)
data_public/public.db      # Market data (valuations, SEC data, indexes)
```

This separation ensures your personal holdings data stays private while market data can be easily regenerated.

## Setup

1. Clone the repository

2. Create a virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Run the app:
   ```bash
   python app.py
   ```

5. Open http://localhost:8080 in your browser

The app will automatically create the database directories and initialize the schema on first run.

## File Structure

```
FinanceApp/
├── app.py              # Main Flask application
├── config.py           # Configuration constants
├── database.py         # SQLite database operations
├── data_manager.py     # Data persistence layer
├── sec_data.py         # SEC EDGAR API integration
├── logger.py           # Logging utilities
├── routes/             # Flask blueprints (modular routes)
├── services/           # Business logic services
├── static/
│   ├── app.js          # Main frontend JavaScript
│   └── css/            # Modular CSS files
├── templates/
│   └── index.html      # Single-page app template
├── data_public/        # Public market data (tracked in git)
│   └── public.db       # Market valuations, SEC data, indexes
└── data_private/       # Personal data (not in git)
    └── private.db      # Holdings, transactions
```

## Database Schema

### Private Database (data_private/private.db)
- `stocks` - Stock registry (ticker, name, type)
- `transactions` - Transaction history (buys, sells, dates, prices)

### Public Database (data_public/public.db)
- `indexes` - Index definitions (S&P 500, NASDAQ 100, etc.)
- `tickers` - Ticker metadata and SEC status
- `ticker_indexes` - Index membership mapping
- `valuations` - Calculated fair values and metrics
- `sec_companies` - SEC company data and CIK mappings
- `eps_history` - Historical EPS from 10-K filings
- `sec_filings` - Direct URLs to SEC 10-K documents
- `cik_mapping` - Ticker to CIK lookup cache
- `metadata` - System metadata and cache timestamps

## Key Features Explained

### Valuation Model
Fair value is calculated as: `(Average EPS + Annual Dividend) x 10`

EPS data is sourced from SEC 10-K filings when available, with fallback to Yahoo Finance.

### Recommendation Scoring
Stocks are scored based on:
- **Undervaluation** (1.0x weight): Distance below fair value
- **Dividend Yield** (1.5x weight): Higher yields preferred
- **Selloff Pressure** (0.8x weight): Stocks down from 52-week highs

### Data Updates
- **Quick Update**: Refreshes prices only (fast)
- **Smart Update**: Updates missing valuations + prices
- **Full Update**: Re-fetches all SEC data and valuations

### SEC Integration
- Fetches EPS data from SEC EDGAR XBRL API
- Stores direct links to 10-K filing documents
- Caches CIK mappings for fast ticker lookups
- Rate-limited to respect SEC guidelines (10 req/sec)

## Adding Holdings

Use the Holdings page to add your stocks and transactions through the web interface, or import via the API.

## License

MIT
