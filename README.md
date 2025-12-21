# Stock Portfolio Tracker

A Flask-based web application for tracking stock portfolios, analyzing market valuations, and finding undervalued investment opportunities using data from multiple providers.

## Features

- **Portfolio Management**: Track holdings, transactions, and realized profits using FIFO cost basis
- **Market Analysis**: Screen stocks across major indices (S&P 500, NASDAQ 100, Dow 30, S&P 600, Russell 2000)
- **Index Management**: Enable/disable indices and refresh constituent lists from multiple sources
- **Valuation Engine**: Calculate fair values based on historical EPS data from SEC filings
- **Recommendations**: Scored stock recommendations based on undervaluation, dividend yield, and selloff pressure
- **Company Lookup**: Research individual stocks with detailed valuation metrics and SEC 10-K filing links
- **Profit Timeline**: Visualize trading performance over time
- **Multi-Provider Architecture**: Pluggable data sources with automatic fallback chains

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
- **Data Providers**: yfinance, SEC EDGAR, Alpaca, FMP, IBKR, DefeatBeta

## Project Structure

```
FinanceApp/
├── app.py                 # Flask app with 40+ REST API routes
├── database.py            # SQLite CRUD operations for both databases
├── config.py              # Application constants and thresholds
├── sec_data.py            # SEC EDGAR API integration
├── data_manager.py        # High-level data operations
│
├── services/
│   ├── providers/         # Market data provider system
│   │   ├── base.py        # Provider interfaces and data types
│   │   ├── registry.py    # DataOrchestrator - coordinates fetching
│   │   ├── config.py      # Provider ordering and settings
│   │   ├── circuit_breaker.py  # Fault tolerance
│   │   ├── secrets.py     # API key management
│   │   ├── yfinance_provider.py
│   │   ├── alpaca_provider.py
│   │   ├── fmp_provider.py
│   │   ├── ibkr_provider.py
│   │   ├── sec_provider.py
│   │   └── defeatbeta_provider.py
│   │
│   ├── indexes/           # Index provider system
│   │   ├── providers.py   # Index constituent fetching (Wikipedia, iShares, etc.)
│   │   └── registry.py    # Index definitions and management
│   │
│   ├── screener.py        # Background batch processing
│   ├── valuation.py       # Fair value calculations
│   ├── recommendations.py # Scoring algorithm
│   └── holdings.py        # FIFO cost basis calculations
│
├── static/
│   ├── app.js             # Main frontend SPA
│   └── css/               # Stylesheets
│
├── data_public/           # Market data (can be rebuilt)
│   └── public.db
└── data_private/          # User data (backup this!)
    ├── private.db
    ├── api_keys.json
    └── provider_config.json
```

## Databases

The app uses two SQLite databases for data isolation:

### private.db (User Data - Protect This!)

| Table | Description |
|-------|-------------|
| `stocks` | User's tracked stock symbols |
| `transactions` | Buy/sell history with FIFO tracking |

### public.db (Market Data - Rebuildable)

| Table | Description |
|-------|-------------|
| `indexes` | Index definitions with enabled/disabled state |
| `tickers` | Company info, CIK, SEC status, enabled state |
| `ticker_indexes` | Many-to-many index membership with active flag |
| `valuations` | Prices, EPS averages, fair values, selloff metrics |
| `eps_history` | Annual EPS from SEC filings by year |
| `sec_companies` | SEC company metadata |
| `sec_filings` | 10-K document URLs by fiscal year |
| `cik_mapping` | Ticker to SEC CIK mappings |
| `ticker_failures` | Failed ticker tracking for exclusion |
| `metadata` | Cache timestamps and system state |

## Valuation Model

Fair value is calculated as:

```
Fair Value = (8-year Average EPS + Annual Dividend) × 10
```

EPS data is sourced from SEC 10-K filings when available, with fallback to other providers.

## Market Data Provider System

The app uses a pluggable provider architecture with automatic failover:

### Data Types

| Type | Description |
|------|-------------|
| `PRICE` | Current stock price |
| `PRICE_HISTORY` | Historical prices with 1m/3m changes |
| `EPS` | Earnings per share history |
| `DIVIDEND` | Annual dividend data |
| `STOCK_INFO` | Company metadata (52-week high/low, sector) |
| `SELLOFF` | Volume-based selloff metrics |

### Available Providers

| Provider | Data Types | Notes |
|----------|------------|-------|
| **yfinance** | Price, EPS, Dividend, History, StockInfo, Selloff | Real-time, no API key |
| **SEC EDGAR** | EPS | Authoritative 10-K data |
| **Alpaca** | Price | Real-time, requires API key |
| **IBKR** | Price | Real-time, requires TWS connection |
| **FMP** | Price | Real-time, requires API key |
| **DefeatBeta** | Price, EPS | Historical only (weekly snapshots) |

### Provider Priority

Providers are tried in configured order with automatic fallback:

- **Prices**: IBKR → Alpaca → yfinance → FMP → DefeatBeta
- **EPS**: SEC EDGAR → yfinance → DefeatBeta
- **Dividends**: yfinance

Real-time providers are always prioritized before historical-only providers.

### Fault Tolerance

- **Circuit Breaker**: Skip failing providers temporarily (3 failures → 2 min cooldown)
- **Timeouts**: 10s per provider call prevents hanging
- **Rate Limiting**: Respects API limits (SEC: 10 req/sec)

## Index Provider System

Index constituents are fetched from multiple sources with fallback:

| Index | Primary Source | Fallback |
|-------|---------------|----------|
| S&P 500 | Wikipedia | Slickcharts |
| NASDAQ 100 | Wikipedia | Slickcharts |
| Dow 30 | Wikipedia | Slickcharts |
| S&P 600 | Wikipedia | - |
| Russell 2000 | iShares ETF | GitHub |

### Index Management

- Enable/disable specific indices from the Settings tab
- Disabling an index excludes its tickers from screener runs
- Tickers belonging only to disabled indices are automatically disabled
- Refresh index membership from providers to sync with current constituents

## Recommendation Scoring

Stocks are scored based on weighted factors:

| Factor | Weight | Description |
|--------|--------|-------------|
| Undervaluation | 1.0× | Distance below fair value |
| Dividend Yield | 1.5× | Higher yields preferred |
| Selloff Pressure | 0.8× | Stocks down from 52-week highs |

Requirements:
- Minimum 5 years of EPS data
- Must have both price and EPS data

## Data Updates

| Update Type | Description |
|-------------|-------------|
| **Quick Update** | Refreshes prices only |
| **Smart Update** | Updates missing valuations + prices |
| **Full Update** | Re-fetches all SEC data and valuations |
| **Single Refresh** | Update one company's data |

## Screener Phases

When running the screener (background batch processing):

1. **EPS** - Fetch earnings data from SEC/providers
2. **Dividends** - Fetch dividend data
3. **Prices** - Fetch current prices with 3-month history
4. **Valuations** - Calculate fair values and save to database

## API Keys (Optional)

For enhanced data access, add API keys to `data_private/api_keys.json`:

```json
{
  "alpaca_key": "your-key",
  "alpaca_secret": "your-secret",
  "fmp_api_key": "your-key"
}
```

For Interactive Brokers, TWS or Gateway must be running with API enabled.

## Configuration

Key settings in `config.py`:

| Setting | Value | Description |
|---------|-------|-------------|
| `PE_RATIO_MULTIPLIER` | 10 | Fair value multiplier |
| `RECOMMENDED_EPS_YEARS` | 8 | Years of EPS to average |
| `PRICE_CACHE_DURATION` | 300 | Seconds to cache prices |
| `FAILURE_THRESHOLD` | 3 | Failures before excluding ticker |

Provider settings in `data_private/provider_config.json`:

| Setting | Default | Description |
|---------|---------|-------------|
| `price_providers` | ["ibkr", "alpaca", "yfinance", "fmp", "defeatbeta"] | Provider priority order |
| `disabled_providers` | ["fmp"] | Providers to skip |
| `provider_timeout_seconds` | 10 | Max time per provider call |
| `circuit_breaker_enabled` | true | Enable fault tolerance |

## API Endpoints

The app exposes 40+ REST endpoints. Key ones:

| Endpoint | Description |
|----------|-------------|
| `GET /api/all-tickers` | All tickers with valuations |
| `GET /api/recommendations` | Top 10 scored recommendations |
| `GET /api/data-status` | Comprehensive data status |
| `GET /api/providers/status` | Provider health and availability |
| `POST /api/indexes/<id>/toggle` | Enable/disable an index |
| `POST /api/refresh-indexes` | Refresh index constituents |

## License

MIT
