# Stock Portfolio Tracker

A Flask-based web application for tracking stock portfolios, analyzing market valuations, and finding undervalued investment opportunities.

## Features

- **Portfolio Management**: Track holdings, transactions, and realized profits using FIFO cost basis
- **Market Analysis**: Screen stocks across major indices (S&P 500, NASDAQ 100, Dow 30, S&P 600, Russell 2000)
- **Valuation Engine**: Calculate fair values based on historical EPS data from SEC filings
- **Recommendations**: AI-scored stock recommendations based on undervaluation, dividend yield, and selloff pressure
- **Company Lookup**: Research individual stocks with detailed valuation metrics and SEC 10-K filing links
- **Profit Timeline**: Visualize trading performance over time
- **Multi-Provider Data**: Pluggable data sources with automatic fallback chains

## Quick Start

```bash
# Clone the repository
git clone <repo-url>
cd FinanceApp

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the app
python app.py
```

Open http://localhost:8080 in your browser. Databases are created automatically on first run.

## Tech Stack

- **Backend**: Python/Flask
- **Frontend**: Vanilla JavaScript SPA
- **Storage**: SQLite (separate public/private databases)
- **Data Providers**: yfinance, SEC EDGAR, Alpaca, FMP, DefeatBeta

## Project Structure

```
FinanceApp/
├── app.py                 # Flask app with 40+ REST API routes
├── database.py            # SQLite CRUD operations
├── config.py              # Configuration constants
├── sec_data.py            # SEC EDGAR API integration
├── data_manager.py        # High-level data operations
│
├── services/
│   ├── providers/         # Multi-source data provider system
│   │   ├── base.py        # Provider interfaces
│   │   ├── registry.py    # DataOrchestrator - coordinates fetching
│   │   ├── circuit_breaker.py  # Fault tolerance
│   │   ├── yfinance_provider.py
│   │   ├── alpaca_provider.py
│   │   ├── fmp_provider.py
│   │   ├── sec_provider.py
│   │   └── defeatbeta_provider.py
│   │
│   ├── screener.py        # Background batch processing
│   ├── valuation.py       # Fair value calculations
│   ├── recommendations.py # Scoring algorithm
│   └── holdings.py        # FIFO cost basis
│
├── static/
│   ├── app.js             # Main frontend SPA
│   └── css/               # Stylesheets
│
├── data_public/           # Market data (can be rebuilt)
│   └── public.db
└── data_private/          # User data (backup this!)
    └── private.db
```

## Databases

The app uses two SQLite databases for data isolation:

**private.db** - Personal data (protect this):
- `stocks` - User's tracked stocks
- `transactions` - Buy/sell history with FIFO tracking

**public.db** - Market data (rebuildable):
- `tickers` - Company info and SEC status
- `valuations` - Prices, EPS averages, fair values
- `eps_history` - Annual EPS from SEC filings
- `indexes`, `ticker_indexes` - Index membership

## Valuation Model

Fair value is calculated as:

```
Fair Value = (8-year Average EPS + Annual Dividend) × 10
```

EPS data is sourced from SEC 10-K filings when available, with fallback to other providers.

## Provider System

The app uses a pluggable provider architecture with automatic failover:

| Data Type | Primary Provider | Fallbacks |
|-----------|-----------------|-----------|
| Prices | Alpaca, yfinance | FMP, DefeatBeta |
| EPS | SEC EDGAR | yfinance, DefeatBeta |
| Dividends | yfinance | - |

Features:
- **Circuit Breaker**: Skip failing providers temporarily (3 failures → 2 min cooldown)
- **Timeouts**: 10s per provider call prevents hanging
- **Rate Limiting**: Respects API limits (SEC: 10 req/sec, etc.)

## Recommendation Scoring

Stocks are scored based on weighted factors:

| Factor | Weight | Description |
|--------|--------|-------------|
| Undervaluation | 1.0× | Distance below fair value |
| Dividend Yield | 1.5× | Higher yields preferred |
| Selloff Pressure | 0.8× | Stocks down from 52-week highs |

## Data Updates

- **Quick Update**: Refreshes prices only
- **Smart Update**: Updates missing valuations + prices
- **Full Update**: Re-fetches all SEC data and valuations

## API Keys (Optional)

For enhanced data access, add API keys to `data_private/api_keys.json`:

```json
{
  "alpaca_key": "your-key",
  "alpaca_secret": "your-secret",
  "fmp_api_key": "your-key"
}
```

## License

MIT
