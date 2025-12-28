"""
Configuration constants for the Finance App.

Loads configuration from config.yaml and exposes values as Python constants.
All magic numbers and thresholds are centralized for easy tuning and testing.
"""

import os
import yaml

# ============================================================================
# Load Configuration from YAML
# ============================================================================
_CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'config.yaml')

def _load_config():
    """Load configuration from YAML file."""
    try:
        with open(_CONFIG_FILE, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print(f"[Config] Warning: {_CONFIG_FILE} not found, using defaults")
        return {}
    except yaml.YAMLError as e:
        print(f"[Config] Error parsing config.yaml: {e}")
        return {}

_config = _load_config()

def _get(path: str, default=None):
    """Get a nested config value using dot notation."""
    keys = path.split('.')
    value = _config
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
        else:
            return default
        if value is None:
            return default
    return value

# ============================================================================
# Data Directories & Databases
# ============================================================================
# Two separate databases for data isolation:
# - data_public/public.db: Market data (SEC, indexes, valuations)
# - data_private/private.db: Personal data (holdings, transactions)

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data_public')  # Public data
USER_DATA_DIR = os.path.join(os.path.dirname(__file__), 'data_private')  # Private data
ARCHIVE_DIR = os.path.join(os.path.dirname(__file__), 'archive')

# Database paths (used by database.py)
PUBLIC_DB_PATH = os.path.join(DATA_DIR, 'public.db')
PRIVATE_DB_PATH = os.path.join(USER_DATA_DIR, 'private.db')

# Legacy file paths (kept for migration scripts, data now in databases)
STOCKS_FILE = os.path.join(USER_DATA_DIR, 'stocks.csv')
TRANSACTIONS_FILE = os.path.join(USER_DATA_DIR, 'transactions.csv')
EXCLUDED_TICKERS_FILE = os.path.join(DATA_DIR, 'excluded_tickers.json')
TICKER_FAILURES_FILE = os.path.join(DATA_DIR, 'ticker_failures.json')

# ============================================================================
# Auto-create required directories
# ============================================================================
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(USER_DATA_DIR, exist_ok=True)

# ============================================================================
# Cache Settings
# ============================================================================
PRICE_CACHE_DURATION = _get('cache.price_cache_duration', 300)
STALE_DATA_HOURS = _get('cache.stale_data_hours', 24)

# ============================================================================
# Ticker Health / Exclusion
# ============================================================================
FAILURE_THRESHOLD = _get('ticker_health.failure_threshold', 3)

# ============================================================================
# Valuation Settings
# ============================================================================
PE_RATIO_MULTIPLIER = _get('valuation.pe_ratio_multiplier', 10)
MIN_EPS_YEARS = _get('valuation.min_eps_years', 3)
RECOMMENDED_EPS_YEARS = _get('valuation.recommended_eps_years', 8)

# ============================================================================
# Recommendation Scoring Weights
# ============================================================================
SCORING_WEIGHTS = _get('scoring.weights', {
    'undervaluation': 1.0,
    'dividend': 1.5,
    'selloff': 0.8
})

# Dividend scoring
DIVIDEND_NO_DIVIDEND_PENALTY = _get('scoring.dividend.no_dividend_penalty', -30)
DIVIDEND_POINTS_PER_PERCENT = _get('scoring.dividend.points_per_percent', 5)
DIVIDEND_MAX_POINTS = _get('scoring.dividend.max_points', 30)

# Selloff bonuses (for stocks in active selloff)
SELLOFF_SEVERE_BONUS = _get('scoring.selloff_bonuses.severe', 15)
SELLOFF_MODERATE_BONUS = _get('scoring.selloff_bonuses.moderate', 10)
SELLOFF_RECENT_BONUS = _get('scoring.selloff_bonuses.recent', 5)

# Recommendation minimum data quality
RECOMMENDATION_MIN_EPS_YEARS = _get('scoring.min_eps_years', 5)

# ============================================================================
# Sell Candidate Thresholds
# ============================================================================
SELL_OVERVALUED_THRESHOLD = _get('sell_thresholds.overvalued_percent', 10)
SELL_GAIN_THRESHOLD = _get('sell_thresholds.gain_percent', 30)

# ============================================================================
# Selloff Detection Thresholds (price changes)
# ============================================================================
# Severe selloff thresholds
SELLOFF_SEVERE_1M = _get('selloff_detection.severe.one_month', -15)
SELLOFF_SEVERE_3M = _get('selloff_detection.severe.three_month', -25)

# Moderate selloff thresholds
SELLOFF_MODERATE_1M = _get('selloff_detection.moderate.one_month', -10)
SELLOFF_MODERATE_3M = _get('selloff_detection.moderate.three_month', -15)

# Recent/mild selloff thresholds
SELLOFF_RECENT_1M = _get('selloff_detection.recent.one_month', -5)
SELLOFF_RECENT_3M = _get('selloff_detection.recent.three_month', -8)

# Volume-based selloff severity thresholds
SELLOFF_VOLUME_SEVERE = _get('selloff_detection.volume.severe', 3.0)
SELLOFF_VOLUME_HIGH = _get('selloff_detection.volume.high', 2.0)
SELLOFF_VOLUME_MODERATE = _get('selloff_detection.volume.moderate', 1.5)

# ============================================================================
# Yahoo Finance Rate Limiting
# ============================================================================
YAHOO_BATCH_SIZE = _get('rate_limits.yahoo.batch_size', 100)
YAHOO_BATCH_DELAY = _get('rate_limits.yahoo.batch_delay', 0.5)
YAHOO_SINGLE_DELAY = _get('rate_limits.yahoo.single_delay', 0.3)
YAHOO_CHUNK_DELAY = _get('rate_limits.yahoo.chunk_delay', 1.5)
YAHOO_HISTORY_BATCH_DELAY = _get('rate_limits.yahoo.history_batch_delay', 0.5)

# ============================================================================
# FMP Rate Limiting
# ============================================================================
FMP_RATE_LIMIT = _get('rate_limits.fmp.rate_limit', 0.2)
FMP_RATE_LIMIT_BACKOFF = _get('rate_limits.fmp.rate_limit_backoff', 5)
FMP_BATCH_INTERVAL = _get('rate_limits.fmp.batch_interval', 0.2)

# ============================================================================
# SEC Rate Limiting
# ============================================================================
SEC_RATE_LIMIT = _get('rate_limits.sec.rate_limit', 0.12)
SEC_REQUEST_TIMEOUT = _get('rate_limits.sec.request_timeout', 30)
SEC_CIK_CACHE_DAYS = _get('rate_limits.sec.cik_cache_days', 7)
SEC_EPS_CACHE_DAYS = _get('rate_limits.sec.eps_cache_days', 1)

# ============================================================================
# IBKR Rate Limiting
# ============================================================================
IBKR_POLL_INTERVAL = _get('rate_limits.ibkr.poll_interval', 0.1)
IBKR_SNAPSHOT_WAIT = _get('rate_limits.ibkr.snapshot_wait', 2)

# ============================================================================
# Screener Rate Limiting
# ============================================================================
SCREENER_DIVIDEND_BACKOFF = _get('rate_limits.screener.dividend_backoff', 0.3)
SCREENER_TICKER_PAUSE = _get('rate_limits.screener.ticker_pause', 0.5)
SCREENER_PRICE_DELAY = _get('rate_limits.screener.price_delay', 0.2)

# ============================================================================
# General Rate Limiting
# ============================================================================
DIVIDEND_FETCH_DELAY = _get('rate_limits.dividend_fetch_delay', 0.3)
EPS_BACKOFF_BASE = _get('rate_limits.eps_backoff_base', 2)

# ============================================================================
# Index Provider Settings
# ============================================================================
INDEX_PROVIDER_TIMEOUT = _get('index_providers.request_timeout', 30)

# ============================================================================
# Background Scheduler Settings
# ============================================================================
SCHEDULER_ENABLED = _get('scheduler.enabled', True)
PRICE_REFRESH_INTERVAL = _get('scheduler.price_refresh_interval_minutes', 15)
MARKET_HOURS_START = _get('scheduler.market_hours.start', '09:30')
MARKET_HOURS_END = _get('scheduler.market_hours.end', '16:00')
MARKET_TIMEZONE = _get('scheduler.market_hours.timezone', 'America/New_York')

# ============================================================================
# Staleness Dashboard Thresholds
# ============================================================================
STALENESS_PRICE_FRESH_MINUTES = _get('staleness.price_fresh_minutes', 60)
STALENESS_PRICE_STALE_HOURS = _get('staleness.price_stale_hours', 24)
STALENESS_DIVIDEND_FRESH_DAYS = _get('staleness.dividend_fresh_days', 7)
STALENESS_DIVIDEND_STALE_DAYS = _get('staleness.dividend_stale_days', 30)

# ============================================================================
# Fallback Data Sources (Optional)
# ============================================================================
# Set these environment variables to enable fallback price sources when
# yfinance fails (e.g., rate limiting, temporary outages).
#
# Financial Modeling Prep (free tier: 250 requests/day):
#   export FMP_API_KEY="your_api_key_here"
#   Get a free key at: https://financialmodelingprep.com/developer/docs/
#
# The app will automatically use these as fallbacks - no code changes needed.

# ============================================================================
# Valid Indices
# ============================================================================
# Index definitions moved to services/indexes/ for single source of truth
from services.indexes import VALID_INDICES, INDEX_NAMES as INDEX_DISPLAY_NAMES
