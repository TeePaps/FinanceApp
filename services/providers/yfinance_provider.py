"""
Yahoo Finance provider implementations.

Wraps yfinance library to provide prices, EPS, and dividends through
the standard provider interface.
"""

import time
import math
from typing import Dict, List, Optional
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

import config


def _last_close(data, ticker: str) -> Optional[float]:
    """
    Scalar-safe last close for `ticker` from a yf.download() frame.

    Handles both column layouts — yfinance >= 0.2.51 defaults to
    multi_level_index=True, so even a single-ticker download has MultiIndex
    columns. The old guards were broken for both shapes:
    - single ticker: data['Close'] is a DataFrame, .iloc[-1] a Series, and
      bool(Series) raised "truth value ... is ambiguous" — every
      single-ticker batch fetch crashed
    - multi ticker: .iloc[-1] is a numpy scalar with NO .isna() method, so
      the hasattr guard collapsed to bool(nan) == True and NaN was stored
      as a real price

    Returns the last non-NaN positive close, or None.
    """
    col = None
    if isinstance(data.columns, pd.MultiIndex):
        if ('Close', ticker) in data.columns:
            col = data[('Close', ticker)]
    elif 'Close' in data.columns:
        col = data['Close']
    if col is None:
        return None
    series = col.dropna()
    if series.empty:
        return None
    try:
        price = float(series.iloc[-1])
    except (TypeError, ValueError):
        return None
    if math.isnan(price) or math.isinf(price) or price <= 0:
        return None
    return price

from .base import (
    PriceProvider, EPSProvider, DividendProvider, SplitProvider, HistoricalPriceProvider, StockInfoProvider, SelloffProvider,
    AnalystEstimateProvider, SharesOutstandingProvider,
    ProviderResult, DataType, PriceData, EPSData, DividendData, SplitData, HistoricalPriceData, StockInfoData, SelloffData,
    AnalystEstimateData, SharesOutstandingData,
)


class YFinancePriceProvider(PriceProvider, HistoricalPriceProvider, StockInfoProvider, SelloffProvider):
    """
    Yahoo Finance price provider.

    Uses yfinance library for price data. Supports batch fetching via yf.download().
    Also provides historical price data for trend analysis, stock metadata, and selloff metrics.
    """

    @property
    def name(self) -> str:
        return "yfinance"

    @property
    def display_name(self) -> str:
        return "Yahoo Finance"

    @property
    def data_types(self) -> List[DataType]:
        return [DataType.PRICE, DataType.PRICE_HISTORY, DataType.STOCK_INFO, DataType.SELLOFF]

    def is_available(self) -> bool:
        # yfinance is always available (no API key required)
        return True

    @property
    def rate_limit(self) -> float:
        return 0.2  # 200ms between requests

    @property
    def supports_batch(self) -> bool:
        return True

    def fetch_price(self, ticker: str) -> ProviderResult:
        """Fetch price for a single ticker."""
        ticker = ticker.upper()

        try:
            stock = yf.Ticker(ticker)

            # Try fast_info first (fastest)
            try:
                price = stock.fast_info.get('lastPrice') or stock.fast_info.get('regularMarketPrice')
                if price and price > 0:
                    return ProviderResult(
                        success=True,
                        data=float(price),
                        source=self.name
                    )
            except Exception:
                pass

            # Fall back to history
            try:
                hist = stock.history(period='5d')
                if hist is not None and not hist.empty and 'Close' in hist.columns:
                    price = hist['Close'].iloc[-1]
                    if price and price > 0:
                        return ProviderResult(
                            success=True,
                            data=float(price),
                            source=self.name
                        )
            except Exception:
                pass

            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error=f"No price data available for {ticker}"
            )

        except Exception as e:
            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error=str(e)
            )

    def fetch_prices(self, tickers: List[str]) -> Dict[str, ProviderResult]:
        """Batch fetch prices using yf.download() with chunking to avoid rate limits."""
        import time
        from services.activity_log import activity_log

        tickers = [t.upper() for t in tickers]
        results = {}

        if not tickers:
            return results

        # Chunk tickers to avoid rate limits (50 at a time with longer delay)
        chunk_size = 50
        all_data = None
        total_chunks = (len(tickers) + chunk_size - 1) // chunk_size

        for i in range(0, len(tickers), chunk_size):
            chunk = tickers[i:i + chunk_size]
            chunk_num = i // chunk_size + 1

            try:
                # Add delay between chunks to avoid rate limiting
                if i > 0:
                    time.sleep(config.YAHOO_CHUNK_DELAY)

                # Only log chunk progress for larger batches (screener operations)
                if len(tickers) > 5:
                    activity_log.log("info", "yfinance", f"Prices: chunk {chunk_num}/{total_chunks} ({len(chunk)} tickers)...")
                chunk_data = yf.download(chunk, period='1d', progress=False, threads=True)

                if not chunk_data.empty:
                    if all_data is None:
                        all_data = chunk_data
                    else:
                        # Merge chunk data - for MultiIndex columns, concat works
                        import pandas as pd
                        all_data = pd.concat([all_data, chunk_data], axis=1)
                else:
                    if len(tickers) > 5:
                        activity_log.log("warning", "yfinance", f"Chunk {chunk_num}/{total_chunks} returned empty")

            except Exception as e:
                # Log but continue with other chunks
                if len(tickers) > 5:
                    activity_log.log("error", "yfinance", f"Chunk {chunk_num}/{total_chunks} failed: {str(e)[:50]}")
                continue

        data = all_data if all_data is not None else None

        try:
            if data is None or data.empty:
                # All failed
                for ticker in tickers:
                    results[ticker] = ProviderResult(
                        success=False,
                        data=None,
                        source=self.name,
                        error="No data returned from batch download"
                    )
                return results

            # _last_close handles both frame shapes (flat and MultiIndex
            # columns), so single- and multi-ticker batches share one path.
            for ticker in tickers:
                try:
                    price = _last_close(data, ticker)
                    if price is not None:
                        results[ticker] = ProviderResult(
                            success=True,
                            data=price,
                            source=self.name
                        )
                    else:
                        results[ticker] = ProviderResult(
                            success=False,
                            data=None,
                            source=self.name,
                            error="No usable close price (missing or NaN)"
                        )
                except Exception as e:
                    results[ticker] = ProviderResult(
                        success=False,
                        data=None,
                        source=self.name,
                        error=str(e)
                    )

            # Mark any tickers not in results as failed
            for ticker in tickers:
                if ticker not in results:
                    results[ticker] = ProviderResult(
                        success=False,
                        data=None,
                        source=self.name,
                        error="Ticker not processed"
                    )

            return results

        except Exception as e:
            # Batch failed entirely
            for ticker in tickers:
                results[ticker] = ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error=f"Batch download failed: {e}"
                )
            return results

    def fetch_price_history(self, ticker: str, period: str = '3mo') -> ProviderResult:
        """Fetch historical price data for a single ticker."""
        ticker = ticker.upper()

        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period=period)

            if hist is None or hist.empty or 'Close' not in hist.columns:
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error=f"No historical price data available for {ticker}"
                )

            # Get current price (last close)
            current_price = float(hist['Close'].iloc[-1])

            # Build prices dict {date_str: price}
            prices = {}
            for date, row in hist.iterrows():
                date_str = date.strftime('%Y-%m-%d')
                prices[date_str] = float(row['Close'])

            # Calculate 1m and 3m prices using oldest available data in range
            price_1m_ago = None
            price_3m_ago = None
            change_1m_pct = None
            change_3m_pct = None

            # Use oldest price in the range as the "start" price
            # This handles cases where fetched data doesn't go back exactly 30/90 days
            if len(hist) > 1:
                oldest_price = float(hist['Close'].iloc[0])

                # For 3-month period, use oldest as 3m ago
                price_3m_ago = oldest_price
                if price_3m_ago > 0:
                    change_3m_pct = ((current_price - price_3m_ago) / price_3m_ago) * 100

                # For 1-month, find the most recent price that's >= 30 days old
                # Iterate newest-to-oldest to find first date <= 30 days ago
                one_month_ago = datetime.now() - timedelta(days=30)
                for date in reversed(hist.index):
                    date_naive = date.replace(tzinfo=None) if hasattr(date, 'tzinfo') and date.tzinfo else date
                    if date_naive <= one_month_ago:
                        price_1m_ago = float(hist.loc[date, 'Close'])
                        break

                # Fallback: if no 30-day price found but we have data, use oldest
                if price_1m_ago is None:
                    price_1m_ago = oldest_price

                if price_1m_ago and price_1m_ago > 0:
                    change_1m_pct = ((current_price - price_1m_ago) / price_1m_ago) * 100

            # Fetch 52-week high/low from stock info
            fifty_two_week_high = None
            fifty_two_week_low = None
            try:
                info = stock.info
                if info and isinstance(info, dict):
                    fifty_two_week_high = info.get('fiftyTwoWeekHigh')
                    fifty_two_week_low = info.get('fiftyTwoWeekLow')
            except Exception:
                pass  # Graceful degradation - still return price data

            historical_data = HistoricalPriceData(
                ticker=ticker,
                source=self.name,
                current_price=current_price,
                prices=prices,
                price_1m_ago=price_1m_ago,
                price_3m_ago=price_3m_ago,
                change_1m_pct=change_1m_pct,
                change_3m_pct=change_3m_pct,
                fifty_two_week_high=fifty_two_week_high,
                fifty_two_week_low=fifty_two_week_low
            )

            return ProviderResult(
                success=True,
                data=historical_data,
                source=self.name
            )

        except Exception as e:
            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error=str(e)
            )

    def fetch_price_history_batch(self, tickers: List[str], period: str = '3mo') -> Dict[str, ProviderResult]:
        """Batch fetch historical prices using yf.download() with chunking to avoid rate limits."""
        import time
        import pandas as pd
        from services.activity_log import activity_log

        tickers = [t.upper() for t in tickers]
        results = {}

        if not tickers:
            return results

        # Chunk tickers to avoid rate limits (100 at a time)
        chunk_size = 100
        all_data = None
        total_chunks = (len(tickers) + chunk_size - 1) // chunk_size

        activity_log.log("info", "yfinance", f"Starting {period} history download: {len(tickers)} tickers in {total_chunks} chunks")

        for i in range(0, len(tickers), chunk_size):
            chunk = tickers[i:i + chunk_size]
            chunk_num = i // chunk_size + 1

            try:
                # Add delay between chunks to avoid rate limiting
                if i > 0:
                    time.sleep(config.YAHOO_HISTORY_BATCH_DELAY)

                activity_log.log("info", "yfinance", f"History: chunk {chunk_num}/{total_chunks} ({len(chunk)} tickers)...")
                chunk_data = yf.download(chunk, period=period, progress=False, threads=True)

                if not chunk_data.empty:
                    if all_data is None:
                        all_data = chunk_data
                    else:
                        # Merge chunk data
                        all_data = pd.concat([all_data, chunk_data], axis=1)
                else:
                    activity_log.log("warning", "yfinance", f"History chunk {chunk_num} returned empty")

            except Exception as e:
                activity_log.log("error", "yfinance", f"History chunk {chunk_num}/{total_chunks} failed: {str(e)[:50]}")
                continue

        data = all_data

        try:
            if data is None or data.empty:
                # All failed
                for ticker in tickers:
                    results[ticker] = ProviderResult(
                        success=False,
                        data=None,
                        source=self.name,
                        error="No historical data returned from batch download"
                    )
                return results

            # Handle single vs multiple tickers (different DataFrame structure)
            if len(tickers) == 1:
                ticker = tickers[0]
                try:
                    if 'Close' in data.columns:
                        results[ticker] = self._process_history_dataframe(ticker, data)
                    else:
                        results[ticker] = ProviderResult(
                            success=False,
                            data=None,
                            source=self.name,
                            error="No Close column in historical data"
                        )
                except Exception as e:
                    results[ticker] = ProviderResult(
                        success=False,
                        data=None,
                        source=self.name,
                        error=str(e)
                    )
            else:
                # Multiple tickers - MultiIndex columns
                for ticker in tickers:
                    try:
                        if ('Close', ticker) in data.columns:
                            # Extract single ticker's data
                            ticker_data = data.xs(ticker, level=1, axis=1)
                            results[ticker] = self._process_history_dataframe(ticker, ticker_data)
                        else:
                            results[ticker] = ProviderResult(
                                success=False,
                                data=None,
                                source=self.name,
                                error="Ticker not in historical results"
                            )
                    except Exception as e:
                        results[ticker] = ProviderResult(
                            success=False,
                            data=None,
                            source=self.name,
                            error=str(e)
                        )

            # Mark any tickers not in results as failed
            for ticker in tickers:
                if ticker not in results:
                    results[ticker] = ProviderResult(
                        success=False,
                        data=None,
                        source=self.name,
                        error="Ticker not processed"
                    )

            return results

        except Exception as e:
            # Batch failed entirely
            for ticker in tickers:
                results[ticker] = ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error=f"Batch historical download failed: {e}"
                )
            return results

    def _process_history_dataframe(self, ticker: str, hist_df) -> ProviderResult:
        """Helper to process a historical price DataFrame into HistoricalPriceData."""
        try:
            if hist_df.empty or 'Close' not in hist_df.columns:
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error="Empty or invalid historical data"
                )

            # Get current price (last NON-NaN close — in a multi-ticker batch
            # the frames share a union date index, so a ticker's latest rows
            # can legitimately be NaN)
            close = hist_df['Close'].dropna()
            if close.empty:
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error="All close prices are NaN"
                )
            current_price = float(close.iloc[-1])

            # Build prices dict {date_str: price}, skipping NaN gap rows
            prices = {}
            for date, row in hist_df.iterrows():
                px = row['Close']
                if px is None or (isinstance(px, float) and math.isnan(px)):
                    continue
                date_str = date.strftime('%Y-%m-%d')
                prices[date_str] = float(px)

            # Calculate 1m and 3m prices using oldest available data in range
            price_1m_ago = None
            price_3m_ago = None
            change_1m_pct = None
            change_3m_pct = None

            # Use oldest price in the range as the "start" price
            # This handles cases where fetched data doesn't go back exactly 30/90 days
            if len(close) > 1:
                oldest_price = float(close.iloc[0])

                # For 3-month period, use oldest as 3m ago
                price_3m_ago = oldest_price
                if price_3m_ago > 0:
                    change_3m_pct = ((current_price - price_3m_ago) / price_3m_ago) * 100

                # For 1-month, find the most recent price that's >= 30 days old
                # Iterate newest-to-oldest to find first date <= 30 days ago
                one_month_ago = datetime.now() - timedelta(days=30)
                for date in reversed(close.index):
                    date_naive = date.replace(tzinfo=None) if hasattr(date, 'tzinfo') and date.tzinfo else date
                    if date_naive <= one_month_ago:
                        price_1m_ago = float(close.loc[date])
                        break

                # Fallback: if no 30-day price found but we have data, use oldest
                if price_1m_ago is None:
                    price_1m_ago = oldest_price

                if price_1m_ago and price_1m_ago > 0:
                    change_1m_pct = ((current_price - price_1m_ago) / price_1m_ago) * 100

            # Note: 52-week data not available from batch download
            # Screener handles this in a separate phase
            historical_data = HistoricalPriceData(
                ticker=ticker,
                source=self.name,
                current_price=current_price,
                prices=prices,
                price_1m_ago=price_1m_ago,
                price_3m_ago=price_3m_ago,
                change_1m_pct=change_1m_pct,
                change_3m_pct=change_3m_pct,
                fifty_two_week_high=None,
                fifty_two_week_low=None
            )

            return ProviderResult(
                success=True,
                data=historical_data,
                source=self.name
            )

        except Exception as e:
            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error=str(e)
            )

    def fetch_stock_info(self, ticker: str) -> ProviderResult:
        """Fetch stock metadata for a single ticker."""
        ticker = ticker.upper()

        try:
            stock = yf.Ticker(ticker)
            info = stock.info

            if not info or not isinstance(info, dict):
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error=f"No info data available for {ticker}"
                )

            # Extract company name (required field)
            company_name = info.get('longName') or info.get('shortName') or ticker

            # Extract optional fields
            fifty_two_week_high = info.get('fiftyTwoWeekHigh')
            fifty_two_week_low = info.get('fiftyTwoWeekLow')
            market_cap = info.get('marketCap')
            sector = info.get('sector')
            industry = info.get('industry')
            pe_ratio = info.get('trailingPE') or info.get('forwardPE')
            dividend_yield = info.get('dividendYield')

            # Convert dividend yield from decimal to percentage if present
            if dividend_yield is not None:
                dividend_yield = dividend_yield * 100

            stock_info_data = StockInfoData(
                ticker=ticker,
                source=self.name,
                company_name=company_name,
                fifty_two_week_high=fifty_two_week_high,
                fifty_two_week_low=fifty_two_week_low,
                market_cap=market_cap,
                sector=sector,
                industry=industry,
                pe_ratio=pe_ratio,
                dividend_yield=dividend_yield
            )

            return ProviderResult(
                success=True,
                data=stock_info_data,
                source=self.name
            )

        except Exception as e:
            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error=str(e)
            )

    def fetch_selloff(self, ticker: str) -> ProviderResult:
        """
        Fetch selloff metrics for a single ticker.

        Calculates selling pressure by comparing volume on down days to average volume.
        """
        ticker = ticker.upper()

        try:
            # Import thresholds from config
            from config import SELLOFF_VOLUME_SEVERE, SELLOFF_VOLUME_HIGH, SELLOFF_VOLUME_MODERATE

            stock = yf.Ticker(ticker)

            # Get 30 days of history for monthly calculation
            hist = stock.history(period='1mo')
            if hist is None or hist.empty or len(hist) < 2:
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error=f"No historical data available for {ticker}"
                )

            # Get average volume from info (20-day average)
            info = stock.info
            avg_volume = info.get('averageVolume', 0) or info.get('averageDailyVolume10Day', 0)
            if not avg_volume or avg_volume == 0:
                # Calculate from history if not available
                avg_volume = hist['Volume'].mean()

            # Calculate daily price changes
            hist['price_change'] = hist['Close'].pct_change()
            hist['is_down_day'] = hist['price_change'] < 0

            # 1-Day selloff (today)
            today = hist.iloc[-1] if len(hist) > 0 else None
            day_selloff = None
            if today is not None and avg_volume > 0:
                if today['is_down_day']:
                    day_selloff = today['Volume'] / avg_volume
                else:
                    day_selloff = 0  # Not a down day

            # 1-Week selloff (last 5 trading days)
            week_data = hist.tail(5)
            week_down_days = week_data[week_data['is_down_day']]
            week_selloff = None
            if len(week_data) > 0 and avg_volume > 0:
                if len(week_down_days) > 0:
                    # Average volume on down days relative to normal
                    week_down_volume = week_down_days['Volume'].mean()
                    week_selloff = week_down_volume / avg_volume
                else:
                    week_selloff = 0  # No down days this week

            # 1-Month selloff (all available data, ~22 trading days)
            month_down_days = hist[hist['is_down_day']]
            month_selloff = None
            if len(hist) > 0 and avg_volume > 0:
                if len(month_down_days) > 0:
                    month_down_volume = month_down_days['Volume'].mean()
                    month_selloff = month_down_volume / avg_volume
                else:
                    month_selloff = 0

            # Calculate overall selloff severity using config thresholds
            severity = 'none'
            max_selloff = max(day_selloff or 0, week_selloff or 0, month_selloff or 0)
            if max_selloff >= SELLOFF_VOLUME_SEVERE:
                severity = 'severe'  # 3x+ normal volume on down days
            elif max_selloff >= SELLOFF_VOLUME_HIGH:
                severity = 'high'    # 2x-3x normal volume
            elif max_selloff >= SELLOFF_VOLUME_MODERATE:
                severity = 'moderate' # 1.5x-2x normal volume
            elif max_selloff >= 1.0:
                severity = 'normal'   # Normal volume on down days

            # Count down days
            down_days_week = len(week_down_days)
            down_days_month = len(month_down_days)
            total_days_month = len(hist)

            # Build SelloffData
            selloff_data = SelloffData(
                ticker=ticker,
                source=self.name,
                day={
                    'selloff_rate': round(day_selloff, 2) if day_selloff is not None else None,
                    'is_down': bool(today['is_down_day']) if today is not None else None,
                    'volume': int(today['Volume']) if today is not None else None,
                    'price_change_pct': round(today['price_change'] * 100, 2) if today is not None and not math.isnan(today['price_change']) else None
                },
                week={
                    'selloff_rate': round(week_selloff, 2) if week_selloff is not None else None,
                    'down_days': down_days_week,
                    'total_days': len(week_data)
                },
                month={
                    'selloff_rate': round(month_selloff, 2) if month_selloff is not None else None,
                    'down_days': down_days_month,
                    'total_days': total_days_month
                },
                avg_volume=int(avg_volume),
                severity=severity
            )

            return ProviderResult(
                success=True,
                data=selloff_data,
                source=self.name
            )

        except Exception as e:
            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error=str(e)
            )


class YFinanceEPSProvider(EPSProvider):
    """
    Yahoo Finance EPS provider.

    Uses yfinance income_stmt for EPS data. Not authoritative (use SEC for official data).
    """

    @property
    def name(self) -> str:
        return "yfinance"

    @property
    def display_name(self) -> str:
        return "Yahoo Finance"

    def is_available(self) -> bool:
        return True

    @property
    def rate_limit(self) -> float:
        return 0.3

    @property
    def is_authoritative(self) -> bool:
        return False  # SEC is authoritative for EPS

    def fetch_eps(self, ticker: str) -> ProviderResult:
        """Fetch EPS history from yfinance income statement."""
        ticker = ticker.upper()

        try:
            stock = yf.Ticker(ticker)
            income_stmt = stock.income_stmt

            if income_stmt is None or income_stmt.empty:
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error="No income statement data"
                )

            eps_history = []

            # Look for EPS rows
            eps_row_names = ['Diluted EPS', 'Basic EPS', 'EPS']
            eps_row = None

            for row_name in eps_row_names:
                if row_name in income_stmt.index:
                    eps_row = income_stmt.loc[row_name]
                    break

            if eps_row is None:
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error="No EPS data in income statement"
                )

            # Extract EPS values
            for col in eps_row.index:
                try:
                    year = col.year if hasattr(col, 'year') else int(str(col)[:4])
                    eps_value = float(eps_row[col])

                    if not (eps_value != eps_value):  # Check for NaN
                        eps_history.append({
                            'year': year,
                            'eps': eps_value,
                            'eps_type': 'Diluted EPS',
                            'source': self.name
                        })
                except (ValueError, TypeError, AttributeError):
                    continue

            # Sort by year descending
            eps_history.sort(key=lambda x: x['year'], reverse=True)

            # Limit to 10 years
            eps_history = eps_history[:10]

            if not eps_history:
                return ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error="No valid EPS values found"
                )

            # Get company name
            try:
                company_name = stock.info.get('shortName', ticker)
            except Exception:
                company_name = ticker

            eps_data = EPSData(
                ticker=ticker,
                source=self.name,
                eps_history=eps_history,
                company_name=company_name
            )

            return ProviderResult(
                success=True,
                data=eps_data,
                source=self.name
            )

        except Exception as e:
            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error=str(e)
            )


class YFinanceDividendProvider(DividendProvider):
    """
    Yahoo Finance dividend provider.

    Uses yfinance dividends Series for dividend history.
    """

    @property
    def name(self) -> str:
        return "yfinance"

    @property
    def display_name(self) -> str:
        return "Yahoo Finance"

    def is_available(self) -> bool:
        return True

    @property
    def rate_limit(self) -> float:
        return 0.2

    def fetch_dividends(self, ticker: str) -> ProviderResult:
        """Fetch dividend history for a ticker."""
        ticker = ticker.upper()

        try:
            stock = yf.Ticker(ticker)
            dividends = stock.dividends

            if dividends is None or dividends.empty:
                # No dividends - this is valid (stock doesn't pay dividends)
                dividend_data = DividendData(
                    ticker=ticker,
                    source=self.name,
                    annual_dividend=0,
                    payments=[]
                )
                return ProviderResult(
                    success=True,
                    data=dividend_data,
                    source=self.name
                )

            # Filter to last 12 months
            one_year_ago = datetime.now() - timedelta(days=365)
            recent_dividends = dividends[dividends.index >= one_year_ago.strftime('%Y-%m-%d')]

            payments = []
            annual_dividend = 0

            for date, amount in recent_dividends.items():
                payments.append({
                    'date': date.strftime('%Y-%m-%d'),
                    'amount': float(amount)
                })
                annual_dividend += float(amount)

            dividend_data = DividendData(
                ticker=ticker,
                source=self.name,
                annual_dividend=annual_dividend,
                payments=payments
            )

            return ProviderResult(
                success=True,
                data=dividend_data,
                source=self.name
            )

        except Exception as e:
            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error=str(e)
            )


class YFinanceSplitProvider(SplitProvider):
    """
    Yahoo Finance stock split provider.

    Uses yfinance .splits Series (date -> ratio) for split history.
    """

    @property
    def name(self) -> str:
        return "yfinance"

    @property
    def display_name(self) -> str:
        return "Yahoo Finance"

    def is_available(self) -> bool:
        return True

    @property
    def rate_limit(self) -> float:
        return 0.2

    def fetch_splits(self, ticker: str) -> ProviderResult:
        """Fetch split history for a ticker."""
        ticker = ticker.upper()

        try:
            stock = yf.Ticker(ticker)
            splits = stock.splits

            splits_list = []
            if splits is not None and not splits.empty:
                for date, ratio in splits.items():
                    try:
                        splits_list.append({
                            'date': date.strftime('%Y-%m-%d'),
                            'ratio': float(ratio),
                        })
                    except (ValueError, TypeError, AttributeError):
                        continue
                # Newest first
                splits_list.sort(key=lambda s: s['date'], reverse=True)

            return ProviderResult(
                success=True,
                data=SplitData(ticker=ticker, source=self.name, splits=splits_list),
                source=self.name,
            )

        except Exception as e:
            return ProviderResult(
                success=False,
                data=None,
                source=self.name,
                error=str(e)
            )


class YFinanceAnalystEstimateProvider(AnalystEstimateProvider):
    """
    Yahoo Finance analyst-estimate provider.

    Uses Ticker.earnings_history which returns a DataFrame indexed by quarter
    with columns including 'epsActual', 'epsEstimate', 'surprisePercent'.

    The yfinance API changes frequently — all access is defensive.
    """

    @property
    def name(self) -> str:
        return "yfinance"

    @property
    def display_name(self) -> str:
        return "Yahoo Finance"

    def is_available(self) -> bool:
        return True

    @property
    def rate_limit(self) -> float:
        return 0.3

    def fetch_analyst_estimates(self, ticker: str) -> ProviderResult:
        ticker = ticker.upper()
        try:
            stock = yf.Ticker(ticker)
            hist = None
            # yfinance has shipped this under both .earnings_history (attribute)
            # and .get_earnings_history() (method) over time.
            try:
                hist = stock.earnings_history
            except Exception:
                hist = None
            if hist is None:
                getter = getattr(stock, 'get_earnings_history', None)
                if callable(getter):
                    try:
                        hist = getter()
                    except Exception:
                        hist = None

            if hist is None or getattr(hist, 'empty', True):
                return ProviderResult(
                    success=False, data=None, source=self.name,
                    error="No analyst estimate data from yfinance"
                )

            cols = {c.lower(): c for c in hist.columns}
            actual_col = cols.get('epsactual') or cols.get('eps_actual')
            est_col = cols.get('epsestimate') or cols.get('eps_estimate')
            surprise_col = cols.get('surprisepercent') or cols.get('surprise_percent')

            def _f(value):
                if value is None:
                    return None
                try:
                    fv = float(value)
                except (TypeError, ValueError):
                    return None
                if math.isnan(fv) or math.isinf(fv):
                    return None
                return fv

            history = []
            for idx, row in hist.iterrows():
                try:
                    period_end = idx.strftime('%Y-%m-%d') if hasattr(idx, 'strftime') else str(idx)
                except Exception:
                    period_end = str(idx)
                history.append({
                    'period_end': period_end,
                    'eps_actual': _f(row[actual_col]) if actual_col else None,
                    'eps_estimate': _f(row[est_col]) if est_col else None,
                    'surprise_percent': _f(row[surprise_col]) if surprise_col else None,
                })

            # Sort newest first
            history.sort(key=lambda r: r['period_end'], reverse=True)

            # Latest entry where actual is present (the most recent REPORTED quarter)
            latest = next((r for r in history if r['eps_actual'] is not None), None)
            if latest is None:
                return ProviderResult(
                    success=False, data=None, source=self.name,
                    error="No reported quarters with actual EPS"
                )

            data = AnalystEstimateData(
                ticker=ticker,
                source=self.name,
                eps_actual=latest['eps_actual'],
                eps_estimate=latest['eps_estimate'],
                surprise_percent=latest['surprise_percent'],
                period_end=latest['period_end'],
                history=history,
            )
            return ProviderResult(success=True, data=data, source=self.name)

        except Exception as e:
            return ProviderResult(
                success=False, data=None, source=self.name, error=str(e)
            )


class YFinanceSharesOutstandingProvider(SharesOutstandingProvider):
    """
    Yahoo Finance shares-outstanding provider (current only).

    Uses Ticker.info['sharesOutstanding']. SEC EDGAR provides richer
    historical data — this provider is primarily a fallback when SEC
    doesn't have the ticker.
    """

    @property
    def name(self) -> str:
        return "yfinance"

    @property
    def display_name(self) -> str:
        return "Yahoo Finance"

    def is_available(self) -> bool:
        return True

    @property
    def rate_limit(self) -> float:
        return 0.3

    def fetch_shares_outstanding(self, ticker: str) -> ProviderResult:
        ticker = ticker.upper()
        try:
            stock = yf.Ticker(ticker)
            info = {}
            try:
                info = stock.info or {}
            except Exception:
                info = {}
            current = info.get('sharesOutstanding')
            if current is None:
                return ProviderResult(
                    success=False, data=None, source=self.name,
                    error="No sharesOutstanding in yfinance info"
                )
            try:
                current_f = float(current)
            except (TypeError, ValueError):
                return ProviderResult(
                    success=False, data=None, source=self.name,
                    error="sharesOutstanding not numeric"
                )

            today = datetime.now().strftime('%Y-%m-%d')
            data = SharesOutstandingData(
                ticker=ticker,
                source=self.name,
                current=current_f,
                history=[{'date': today, 'shares': current_f, 'source': self.name}],
            )
            return ProviderResult(success=True, data=data, source=self.name)

        except Exception as e:
            return ProviderResult(
                success=False, data=None, source=self.name, error=str(e)
            )


def fetch_yearly_dividends(ticker: str) -> Dict[int, float]:
    """
    Helper: return {calendar_year: sum_of_dividends_paid_that_year} from yfinance.

    Used by the Star Scoring system to detect dividend-growth year-over-year
    without needing to extend the DividendProvider interface.
    """
    try:
        stock = yf.Ticker(ticker.upper())
        dividends = stock.dividends
        if dividends is None or dividends.empty:
            return {}
        by_year: Dict[int, float] = {}
        for date, amount in dividends.items():
            try:
                year = int(date.year)
                by_year[year] = by_year.get(year, 0.0) + float(amount)
            except (AttributeError, ValueError, TypeError):
                continue
        return by_year
    except Exception:
        return {}
