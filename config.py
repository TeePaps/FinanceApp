"""
Configuration constants for the Finance App.

Centralizes all magic numbers and thresholds for easy tuning and testing.
"""

import os

# ============================================================================
# File Paths
# ============================================================================
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
USER_DATA_DIR = os.path.join(os.path.dirname(__file__), 'user_data')
EXCLUDED_TICKERS_FILE = os.path.join(DATA_DIR, 'excluded_tickers.json')
TICKER_FAILURES_FILE = os.path.join(DATA_DIR, 'ticker_failures.json')

# User data files (personal holdings - excluded from git)
STOCKS_FILE = os.path.join(USER_DATA_DIR, 'stocks.csv')
TRANSACTIONS_FILE = os.path.join(USER_DATA_DIR, 'transactions.csv')

# ============================================================================
# Auto-create required directories and files
# ============================================================================
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(USER_DATA_DIR, exist_ok=True)

# Create empty CSV files with headers if they don't exist
if not os.path.exists(STOCKS_FILE):
    with open(STOCKS_FILE, 'w') as f:
        f.write('ticker,name,type\n')

if not os.path.exists(TRANSACTIONS_FILE):
    with open(TRANSACTIONS_FILE, 'w') as f:
        f.write('id,ticker,action,shares,price,gain_pct,date,status\n')

# ============================================================================
# Cache Settings
# ============================================================================
PRICE_CACHE_DURATION = 300  # 5 minutes - how long to cache stock prices
STALE_DATA_HOURS = 24  # Hours before valuation data is considered stale

# ============================================================================
# Ticker Health / Exclusion
# ============================================================================
FAILURE_THRESHOLD = 3  # Number of consecutive failures before excluding a ticker

# ============================================================================
# Valuation Settings
# ============================================================================
PE_RATIO_MULTIPLIER = 10  # Fair value = (Avg EPS + Annual Dividend) * this
MIN_EPS_YEARS = 3  # Minimum years of EPS data required for valuation
RECOMMENDED_EPS_YEARS = 8  # Recommended years of EPS data for reliable valuation

# ============================================================================
# Recommendation Scoring Weights
# ============================================================================
SCORING_WEIGHTS = {
    'undervaluation': 1.0,  # Weight for undervaluation score
    'dividend': 1.5,        # Weight for dividend score (higher = more important)
    'selloff': 0.8          # Weight for selloff/pullback score
}

# Dividend scoring
DIVIDEND_NO_DIVIDEND_PENALTY = -30  # Penalty for stocks with no dividend
DIVIDEND_POINTS_PER_PERCENT = 5     # Points per 1% dividend yield
DIVIDEND_MAX_POINTS = 30            # Maximum dividend score

# Selloff bonuses (for stocks in active selloff)
SELLOFF_SEVERE_BONUS = 15
SELLOFF_MODERATE_BONUS = 10  # Also used for 'high' severity
SELLOFF_RECENT_BONUS = 5

# Recommendation minimum data quality
RECOMMENDATION_MIN_EPS_YEARS = 5  # Minimum EPS years to qualify for recommendations

# ============================================================================
# Sell Candidate Thresholds
# ============================================================================
SELL_OVERVALUED_THRESHOLD = 10  # % above fair value to consider selling
SELL_GAIN_THRESHOLD = 30        # % gain from cost basis to consider selling

# ============================================================================
# Selloff Detection Thresholds (price changes)
# ============================================================================
# Severe selloff thresholds
SELLOFF_SEVERE_1M = -15   # 1-month price change %
SELLOFF_SEVERE_3M = -25   # 3-month price change %

# Moderate selloff thresholds
SELLOFF_MODERATE_1M = -10
SELLOFF_MODERATE_3M = -15

# Recent/mild selloff thresholds
SELLOFF_RECENT_1M = -5
SELLOFF_RECENT_3M = -8

# Volume-based selloff severity thresholds
SELLOFF_VOLUME_SEVERE = 3.0    # 3x+ normal volume on down days
SELLOFF_VOLUME_HIGH = 2.0      # 2x-3x normal volume
SELLOFF_VOLUME_MODERATE = 1.5  # 1.5x-2x normal volume

# ============================================================================
# Yahoo Finance Rate Limiting
# ============================================================================
YAHOO_BATCH_SIZE = 100        # Number of tickers per batch
YAHOO_BATCH_DELAY = 0.5       # Seconds between batches
YAHOO_SINGLE_DELAY = 0.3      # Seconds between single ticker requests

# ============================================================================
# Valid Indices
# ============================================================================
# Note: Also defined in data_manager.py - these should stay in sync
VALID_INDICES = ['all', 'sp500', 'nasdaq100', 'dow30', 'sp600', 'russell2000']

INDEX_DISPLAY_NAMES = {
    'all': ('All Indexes', 'All'),
    'sp500': ('S&P 500', 'S&P 500'),
    'nasdaq100': ('NASDAQ 100', 'NASDAQ'),
    'dow30': ('Dow Jones Industrial Average', 'DJIA'),
    'sp600': ('S&P SmallCap 600', 'S&P 600'),
    'russell2000': ('Russell 2000', 'Russell 2000')
}
