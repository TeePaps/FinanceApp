"""
Services module for the Finance App.

Contains business logic extracted from app.py for better modularity and testability.
"""

from .yahoo_finance import (
    fetch_stock_price,
    fetch_multiple_prices,
    get_stock_info,
    calculate_selloff_metrics,
    extract_yf_eps
)

from .holdings import (
    calculate_fifo_cost_basis,
    calculate_holdings,
    HoldingsService
)

from .valuation import (
    get_validated_eps,
    calculate_valuation,
    ValuationService
)

from .recommendations import (
    score_stock,
    get_top_recommendations,
    RecommendationService
)

from .screener import (
    ScreenerService,
    get_screener_progress,
    is_screener_running
)
