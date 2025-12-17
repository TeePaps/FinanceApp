# Stock Portfolio Tracker

A Flask-based web application for tracking stock portfolios, analyzing market valuations, and finding investment opportunities.

## Features

- **Portfolio Management**: Track your stock holdings, transactions, and realized profits using FIFO cost basis
- **Market Analysis**: Screen stocks across major indices (S&P 500, NASDAQ 100, Dow 30, S&P 600, Russell 2000)
- **Valuation Engine**: Calculate fair values based on historical EPS data from SEC filings
- **Recommendations**: AI-scored stock recommendations based on undervaluation, dividend yield, and selloff pressure
- **Company Lookup**: Research individual stocks with detailed valuation metrics
- **Profit Timeline**: Visualize your trading performance over time

## Tech Stack

- **Backend**: Python/Flask
- **Frontend**: Vanilla JavaScript, HTML, CSS
- **Data Sources**: Yahoo Finance (yfinance), SEC EDGAR API
- **Storage**: CSV files for holdings, JSON for market data

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
4. Create the user_data directory for your holdings:
   ```bash
   mkdir -p user_data
   ```
5. Add your holdings files:
   - `user_data/stocks.csv` - Your stock registry
   - `user_data/transactions.csv` - Your transaction history

6. Run the app:
   ```bash
   python app.py
   ```
7. Open http://localhost:8080 in your browser

## File Structure

```
FinanceApp/
├── app.py              # Main Flask application
├── config.py           # Configuration constants
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
├── data/               # Market data cache (auto-generated)
└── user_data/          # Personal holdings (not tracked in git)
```

## CSV File Formats

### stocks.csv
```csv
ticker,name,type
AAPL,Apple Inc.,stock
VTI,Vanguard Total Stock Market ETF,index
```

### transactions.csv
```csv
id,ticker,action,shares,price,gain_pct,date,status
1,AAPL,buy,10,150.00,,2024-01-15,complete
2,AAPL,sell,5,175.00,16.67,2024-06-01,complete
```

## Key Features Explained

### Valuation Model
Fair value is calculated as: `(Average EPS + Annual Dividend) × 10`

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

## License

MIT
