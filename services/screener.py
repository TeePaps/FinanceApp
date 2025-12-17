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
import yfinance as yf
import numpy as np
from datetime import datetime
from config import YAHOO_BATCH_SIZE, PE_RATIO_MULTIPLIER
import data_manager

# Module-level state for background tasks
_running = False
_progress = {
    'current': 0,
    'total': 0,
    'ticker': '',
    'status': 'idle',
    'phase': '',
    'index': 'all',
    'index_name': 'All Indexes'
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
    Download prices for tickers in batches.

    Args:
        tickers: List of ticker symbols
        progress_callback: Optional callback for progress updates

    Returns:
        Combined DataFrame with price data, or None if failed
    """
    all_price_data = []
    total_batches = (len(tickers) + YAHOO_BATCH_SIZE - 1) // YAHOO_BATCH_SIZE

    for batch_idx in range(0, len(tickers), YAHOO_BATCH_SIZE):
        if not _running:
            return None

        batch = tickers[batch_idx:batch_idx + YAHOO_BATCH_SIZE]
        batch_num = batch_idx // YAHOO_BATCH_SIZE + 1

        if progress_callback:
            progress_callback(batch_idx, f'Downloading batch {batch_num}/{total_batches}...')

        # Try up to 3 times with increasing delays
        for attempt in range(3):
            try:
                batch_data = yf.download(batch, period='3mo', progress=False, threads=True)
                if batch_data is not None and not batch_data.empty:
                    all_price_data.append(batch_data)
                    print(f"[Screener] Batch {batch_num}/{total_batches}: {len(batch)} tickers OK")
                    break
            except Exception as e:
                if 'Rate' in str(e) and attempt < 2:
                    wait_time = (attempt + 1) * 5
                    print(f"[Screener] Batch {batch_num} rate limited, waiting {wait_time}s...")
                    if progress_callback:
                        progress_callback(batch_idx, f'Rate limited, waiting {wait_time}s...')
                    time.sleep(wait_time)
                else:
                    print(f"[Screener] Batch {batch_num} error: {e}")
                    break

        # Delay between batches to avoid rate limiting
        if batch_idx + YAHOO_BATCH_SIZE < len(tickers):
            time.sleep(2)

    if not all_price_data:
        return None

    # Combine all batch data
    try:
        import pandas as pd
        combined = pd.concat(all_price_data, axis=1)
        # Remove duplicate columns
        combined = combined.loc[:, ~combined.columns.duplicated()]
        return combined
    except Exception as e:
        print(f"[Screener] Error combining batch data: {e}")
        return all_price_data[0] if all_price_data else None


def _calculate_price_changes(combined_data, tickers):
    """
    Calculate price changes from combined price data.

    Args:
        combined_data: Combined DataFrame from batch downloads
        tickers: List of ticker symbols

    Returns:
        Dict mapping ticker to price change data
    """
    results = {}

    try:
        # Get current prices and historical prices
        if len(tickers) == 1:
            ticker = tickers[0]
            if 'Close' in combined_data.columns:
                current_prices = combined_data['Close'].iloc[-1]
                prices_3m_ago = combined_data['Close'].iloc[0]
                prices_1m_ago = combined_data['Close'].iloc[len(combined_data)//3] if len(combined_data) > 3 else prices_3m_ago

                results[ticker] = {
                    'current_price': float(current_prices),
                    'price_3m_ago': float(prices_3m_ago),
                    'price_1m_ago': float(prices_1m_ago),
                    'price_change_3m': ((current_prices - prices_3m_ago) / prices_3m_ago * 100) if prices_3m_ago else 0,
                    'price_change_1m': ((current_prices - prices_1m_ago) / prices_1m_ago * 100) if prices_1m_ago else 0
                }
        else:
            for ticker in tickers:
                try:
                    if ('Close', ticker) in combined_data.columns:
                        ticker_data = combined_data[('Close', ticker)]
                        current_price = ticker_data.iloc[-1]
                        price_3m_ago = ticker_data.iloc[0]
                        price_1m_ago = ticker_data.iloc[len(ticker_data)//3] if len(ticker_data) > 3 else price_3m_ago

                        if current_price and not np.isnan(current_price):
                            results[ticker] = {
                                'current_price': float(current_price),
                                'price_3m_ago': float(price_3m_ago) if not np.isnan(price_3m_ago) else None,
                                'price_1m_ago': float(price_1m_ago) if not np.isnan(price_1m_ago) else None,
                                'price_change_3m': float((current_price - price_3m_ago) / price_3m_ago * 100) if price_3m_ago and not np.isnan(price_3m_ago) else None,
                                'price_change_1m': float((current_price - price_1m_ago) / price_1m_ago * 100) if price_1m_ago and not np.isnan(price_1m_ago) else None
                            }
                except Exception:
                    pass
    except Exception as e:
        print(f"[Screener] Error calculating price changes: {e}")

    return results


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
        if index_name == 'all':
            tickers = list(data_manager.load_valuations().get('valuations', {}).keys())
            index_display_name = 'All Indexes'
        else:
            index_tickers = data_manager.get_index_tickers(index_name)
            tickers = list(index_tickers) if index_tickers else []
            index_display_name = index_name.upper()

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

        # Download prices in batches
        combined_data = _download_prices_in_batches(tickers, progress_callback)

        if not _running:
            _progress['status'] = 'cancelled'
            return

        if combined_data is None:
            print("[Quick Update] No price data returned from any batch")
            _progress['status'] = 'complete'
            _running = False
            return

        # Calculate price changes
        _progress['phase'] = 'calculating'
        _progress['ticker'] = 'Calculating price changes...'

        price_results = _calculate_price_changes(combined_data, tickers)

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
