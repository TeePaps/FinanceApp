"""
Services module for the Finance App.

Contains business logic extracted from app.py for better modularity and testability.
"""

from .stock_utils import (
    fetch_stock_price,
    fetch_multiple_prices,
    get_stock_info
)

from .holdings import (
    calculate_fifo_cost_basis,
    calculate_holdings,
    HoldingsService
)

from .valuation import (
    get_validated_eps,
    calculate_valuation
)

from .recommendations import (
    score_stock,
    get_top_recommendations
)

from .screener import (
    ScreenerService,
    get_progress as get_screener_progress,  # Alias for backward compatibility
    is_running as is_screener_running,  # Alias for backward compatibility
    is_running,
    get_progress,
    stop,
    run_screener,
    run_quick_price_update,
    run_smart_update,
    run_global_refresh
)

from .activity_log import activity_log
