"""
Screener service for background stock screening tasks.

Provides:
- Full screener update (prices + EPS for all stocks)
- Quick price-only update
- Smart selective update (new tickers + stale valuations)
- Global refresh across all indexes
- Progress tracking
"""

import threading
import time
from datetime import datetime
from config import PE_RATIO_MULTIPLIER
import data_manager
from services.indexes import INDEX_NAMES

# Module-level state for background tasks
_running = False
_progress = {
    'current': 0,
    'total': 0,
    'ticker': '',
    'status': 'idle',
    'phase': '',
    'index': 'all',
    'index_name': 'All'  # Updated dynamically from INDEX_NAMES
}


def is_screener_running():
    """Check if screener is currently running."""
    return _running


def get_screener_progress():
    """Get current screener progress."""
    return _progress.copy()


def stop_screener():
    """Stop the running screener."""
    global _running
    _running = False
    _progress['status'] = 'cancelled'


def _download_prices_in_batches(tickers, progress_callback=None):
    """
    Download prices for tickers using orchestrator.

    Args:
        tickers: List of ticker symbols
        progress_callback: Optional callback for progress updates

    Returns:
        Dict mapping ticker to price change data
    """
    if not _running:
        return None

    if progress_callback:
        progress_callback(0, 'Downloading price history...')

    try:
        # Log provider activity
        try:
            from app import log_provider_activity
            log_provider_activity(f"Fetching 3mo history for {len(tickers)} tickers...")
        except ImportError:
            pass

        # Use orchestrator to fetch historical prices
        from services.providers import get_orchestrator
        orchestrator = get_orchestrator()
        history_results = orchestrator.fetch_price_history_batch(tickers, period='3mo')

        try:
            from app import log_provider_activity
            log_provider_activity(f"✓ 3mo history: {len(history_results)} tickers downloaded")
        except ImportError:
            pass

        print(f"[Screener] Downloaded price history for {len(history_results)} tickers")

        if progress_callback:
            progress_callback(len(tickers), 'Processing price data...')

        # Extract price change data from HistoricalPriceData objects
        results = {}
        for ticker, result in history_results.items():
            if result.success and result.data:
                hist_data = result.data
                results[ticker] = {
                    'current_price': hist_data.current_price,
                    'price_source': result.source,
                    'price_3m_ago': hist_data.price_3m_ago,
                    'price_1m_ago': hist_data.price_1m_ago,
                    'price_change_3m': hist_data.change_3m_pct,
                    'price_change_1m': hist_data.change_1m_pct
                }

        return results

    except Exception as e:
        try:
            from app import log_provider_activity
            log_provider_activity(f"✗ 3mo history fetch failed: {str(e)[:50]}")
        except ImportError:
            pass
        print(f"[Screener] Error fetching price history: {e}")
        return None




class ScreenerService:
    """
    Service class for screener operations.

    Provides background screening tasks with progress tracking.
    """

    def __init__(self):
        """Initialize the screener service."""
        pass

    def start_quick_update(self, index_name='all'):
        """
        Start a quick price-only update in background.

        Args:
            index_name: Index to update ('all', 'sp500', etc.)

        Returns:
            True if started, False if already running
        """
        global _running
        if _running:
            return False

        thread = threading.Thread(target=self._run_quick_update, args=(index_name,))
        thread.daemon = True
        thread.start()
        return True

    def _run_quick_update(self, index_name):
        """
        Run quick price update (internal).

        Updates only prices, preserving cached EPS data.
        """
        global _running, _progress
        _running = True

        # Get tickers for the index
        # Use registry for display name lookup
        index_info = INDEX_NAMES.get(index_name, (index_name.upper(), index_name.upper()))
        index_display_name = index_info[1] if len(index_info) > 1 else index_info[0]

        # Import database to filter enabled tickers
        import database as db

        if index_name == 'all':
            # Get all valuations but filter to only enabled tickers
            all_valuations = data_manager.load_valuations().get('valuations', {})
            enabled_tickers = set(db.get_enabled_tickers())
            tickers = [t for t in all_valuations.keys() if t in enabled_tickers]
        else:
            # get_index_tickers already filters disabled tickers via get_active_index_tickers
            index_tickers = data_manager.get_index_tickers(index_name)
            tickers = list(index_tickers) if index_tickers else []

        if not tickers:
            print(f"[Quick Update] No tickers found for {index_name}")
            _progress['status'] = 'complete'
            _running = False
            return

        _progress.update({
            'current': 0,
            'total': len(tickers),
            'ticker': 'Downloading prices...',
            'status': 'running',
            'phase': 'prices',
            'index': index_name,
            'index_name': index_display_name
        })

        def progress_callback(current, message):
            _progress['current'] = current
            _progress['ticker'] = message

        # Download prices using orchestrator
        price_results = _download_prices_in_batches(tickers, progress_callback)

        if not _running:
            _progress['status'] = 'cancelled'
            return

        if price_results is None:
            print("[Quick Update] No price data returned")
            _progress['status'] = 'complete'
            _running = False
            return

        # Mark tickers that failed across all providers as delisted
        failed_tickers = [t for t in tickers if t not in price_results]
        if failed_tickers:
            import database as db
            db.mark_tickers_delisted(failed_tickers)
            print(f"[Quick Update] Marked {len(failed_tickers)} tickers as delisted")

        # Price changes already calculated by orchestrator
        _progress['phase'] = 'calculating'
        _progress['ticker'] = 'Processing price changes...'

        # Update valuations with new prices
        _progress['phase'] = 'saving'
        _progress['ticker'] = 'Saving updates...'

        valuations = data_manager.load_valuations().get('valuations', {})
        updates = {}

        for ticker, price_data in price_results.items():
            if ticker in valuations:
                current_val = valuations[ticker]
                current_price = price_data['current_price']

                # Recalculate fair value if we have EPS
                eps_avg = current_val.get('eps_avg', 0)
                annual_dividend = current_val.get('annual_dividend', 0)
                if eps_avg:
                    estimated_value = (eps_avg + annual_dividend) * PE_RATIO_MULTIPLIER
                    price_vs_value = ((current_price - estimated_value) / estimated_value * 100) if estimated_value > 0 else None
                else:
                    estimated_value = current_val.get('estimated_value')
                    price_vs_value = current_val.get('price_vs_value')

                updates[ticker] = {
                    **current_val,
                    'current_price': round(current_price, 2),
                    'price_source': price_data.get('price_source'),
                    'price_change_1m': round(price_data.get('price_change_1m', 0) or 0, 2),
                    'price_change_3m': round(price_data.get('price_change_3m', 0) or 0, 2),
                    'estimated_value': round(estimated_value, 2) if estimated_value else None,
                    'price_vs_value': round(price_vs_value, 1) if price_vs_value is not None else None,
                    'updated': datetime.now().isoformat()
                }

        if updates:
            data_manager.bulk_update_valuations(updates)
            print(f"[Quick Update] Updated {len(updates)} valuations")

        _progress['status'] = 'complete'
        _progress['current'] = len(tickers)
        _running = False

    def stop(self):
        """Stop the running screener."""
        stop_screener()

    def get_progress(self):
        """Get current progress."""
        return get_screener_progress()

    def is_running(self):
        """Check if screener is running."""
        return is_screener_running()


# Convenience functions for backwards compatibility
def start_quick_price_update(index_name='all'):
    """Start quick price update (convenience function)."""
    service = ScreenerService()
    return service.start_quick_update(index_name)
