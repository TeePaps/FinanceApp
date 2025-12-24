"""
Base classes and interfaces for market data providers.

This module defines the abstract interfaces that all data providers must implement,
enabling a pluggable architecture for fetching prices, EPS, and dividends from
multiple sources with configurable fallback order.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime


class DataType(Enum):
    """Types of market data that providers can supply."""
    PRICE = "price"
    PRICE_HISTORY = "price_history"
    EPS = "eps"
    DIVIDEND = "dividend"
    STOCK_INFO = "stock_info"
    SELLOFF = "selloff"
    SEC_METRICS = "sec_metrics"  # Multi-year EPS matrix + dividend data from SEC
    FILINGS = "filings"  # 10-K filing URLs from SEC


@dataclass
class ProviderResult:
    """
    Standard result container from any provider operation.

    Attributes:
        success: Whether the operation succeeded
        data: The fetched data (type varies by operation)
        source: Provider name that supplied the data
        error: Error message if success=False
        cached: Whether this result came from cache
        timestamp: When the data was fetched/cached
        metadata: Optional dict for provider-specific metadata (e.g., new_years_added)
    """
    success: bool
    data: Any
    source: str
    error: Optional[str] = None
    cached: bool = False
    timestamp: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


@dataclass
class PriceData:
    """Price data for a single ticker."""
    ticker: str
    price: float
    source: str
    timestamp: datetime = field(default_factory=datetime.now)
    change: Optional[float] = None
    change_percent: Optional[float] = None
    volume: Optional[int] = None


@dataclass
class EPSData:
    """EPS history data for a single ticker."""
    ticker: str
    source: str
    eps_history: List[Dict]  # [{'year': int, 'eps': float, 'filed': str, ...}]
    company_name: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class DividendData:
    """Dividend data for a single ticker."""
    ticker: str
    source: str
    annual_dividend: float
    payments: List[Dict]  # [{'date': str, 'amount': float}]
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class HistoricalPriceData:
    """Historical price data for a single ticker."""
    ticker: str
    source: str
    current_price: float
    prices: Dict[str, float]  # {'date_str': price} - daily close prices
    price_1m_ago: Optional[float] = None
    price_3m_ago: Optional[float] = None
    change_1m_pct: Optional[float] = None
    change_3m_pct: Optional[float] = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class StockInfoData:
    """Stock metadata for a single ticker."""
    ticker: str
    source: str
    company_name: str
    fifty_two_week_high: Optional[float] = None
    fifty_two_week_low: Optional[float] = None
    market_cap: Optional[int] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    pe_ratio: Optional[float] = None
    dividend_yield: Optional[float] = None


@dataclass
class SelloffData:
    """Selloff metrics data for a single ticker."""
    ticker: str
    source: str
    day: Dict  # {'selloff_rate': float, 'is_down': bool, 'volume': int, 'price_change_pct': float}
    week: Dict  # {'selloff_rate': float, 'down_days': int, 'total_days': int}
    month: Dict  # {'selloff_rate': float, 'down_days': int, 'total_days': int}
    avg_volume: int
    severity: str  # 'none', 'normal', 'moderate', 'high', 'severe'


@dataclass
class SECMetricsData:
    """SEC metrics data including multi-year EPS matrix and dividends."""
    ticker: str
    source: str
    eps_matrix: List[Dict]  # Multi-year EPS by type from SEC filings
    dividend_history: List[Dict]  # Annual dividends from SEC
    company_name: Optional[str] = None
    cik: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class FilingsData:
    """SEC 10-K filing URLs."""
    ticker: str
    source: str
    filings: List[Dict]  # [{fiscal_year, form_type, filing_date, document_url, accession_number}]
    timestamp: datetime = field(default_factory=datetime.now)


class BaseProvider(ABC):
    """
    Base class for all market data providers.

    All providers must implement the core abstract methods and properties.
    The provider system uses these to determine capabilities and availability.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """
        Unique provider identifier.

        Examples: 'yfinance', 'sec_edgar', 'fmp', 'alpha_vantage'
        """
        pass

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable provider name for UI display."""
        pass

    @property
    @abstractmethod
    def data_types(self) -> List[DataType]:
        """List of data types this provider can supply."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """
        Check if provider is configured and available for use.

        Returns False if required API keys are missing, service is down, etc.
        """
        pass

    @property
    def rate_limit(self) -> float:
        """
        Minimum seconds between requests to this provider.

        Returns 0 if no rate limiting needed.
        """
        return 0

    @property
    def supports_batch(self) -> bool:
        """Whether this provider supports batch requests for multiple tickers."""
        return False

    @property
    def is_authoritative(self) -> bool:
        """
        Whether this source is considered authoritative/official.

        For example, SEC EDGAR is authoritative for EPS data from 10-K filings.
        """
        return False

    @property
    def is_realtime(self) -> bool:
        """
        Whether this provider offers real-time or near-real-time data.

        Returns False for providers that only offer historical/delayed data
        (e.g., weekly snapshots). Used by the orchestrator to prioritize
        real-time providers for price data before falling back to historical.
        """
        return True

    def get_status(self) -> Dict:
        """Get provider status for API/UI display."""
        return {
            'name': self.name,
            'display_name': self.display_name,
            'available': self.is_available(),
            'data_types': [dt.value for dt in self.data_types],
            'supports_batch': self.supports_batch,
            'is_authoritative': self.is_authoritative,
            'is_realtime': self.is_realtime
        }


class PriceProvider(BaseProvider):
    """
    Interface for price data providers.

    Implementations must provide fetch_price() at minimum.
    Batch-capable providers should override fetch_prices() for efficiency.
    """

    @property
    def data_types(self) -> List[DataType]:
        return [DataType.PRICE]

    @abstractmethod
    def fetch_price(self, ticker: str) -> ProviderResult:
        """
        Fetch current price for a single ticker.

        Args:
            ticker: Stock ticker symbol (e.g., 'AAPL')

        Returns:
            ProviderResult with data=float (price) on success
        """
        pass

    def fetch_prices(self, tickers: List[str]) -> Dict[str, ProviderResult]:
        """
        Fetch prices for multiple tickers.

        Default implementation loops over fetch_price().
        Batch-capable providers should override for efficiency.

        Args:
            tickers: List of ticker symbols

        Returns:
            Dict mapping ticker to ProviderResult
        """
        results = {}
        for ticker in tickers:
            results[ticker] = self.fetch_price(ticker)
        return results


class EPSProvider(BaseProvider):
    """
    Interface for EPS (Earnings Per Share) data providers.

    EPS data typically includes multiple years of historical data.
    """

    @property
    def data_types(self) -> List[DataType]:
        return [DataType.EPS]

    @abstractmethod
    def fetch_eps(self, ticker: str) -> ProviderResult:
        """
        Fetch EPS history for a ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            ProviderResult with data=EPSData on success
        """
        pass


class DividendProvider(BaseProvider):
    """
    Interface for dividend data providers.
    """

    @property
    def data_types(self) -> List[DataType]:
        return [DataType.DIVIDEND]

    @abstractmethod
    def fetch_dividends(self, ticker: str) -> ProviderResult:
        """
        Fetch dividend history for a ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            ProviderResult with data=DividendData on success
        """
        pass


class HistoricalPriceProvider(BaseProvider):
    """
    Interface for historical price data providers.

    Used for fetching price history (e.g., 3-month data for calculating price changes).
    """

    @property
    def data_types(self) -> List[DataType]:
        return [DataType.PRICE_HISTORY]

    @abstractmethod
    def fetch_price_history(self, ticker: str, period: str = '3mo') -> ProviderResult:
        """
        Fetch historical price data for a ticker.

        Args:
            ticker: Stock ticker symbol
            period: Time period ('1mo', '3mo', '6mo', '1y', etc.)

        Returns:
            ProviderResult with data=HistoricalPriceData on success
        """
        pass

    def fetch_price_history_batch(self, tickers: List[str], period: str = '3mo') -> Dict[str, ProviderResult]:
        """
        Fetch historical prices for multiple tickers.

        Default implementation loops over fetch_price_history().
        Batch-capable providers should override for efficiency.

        Args:
            tickers: List of ticker symbols
            period: Time period ('1mo', '3mo', '6mo', '1y', etc.)

        Returns:
            Dict mapping ticker to ProviderResult
        """
        results = {}
        for ticker in tickers:
            results[ticker] = self.fetch_price_history(ticker, period)
        return results


class StockInfoProvider(BaseProvider):
    """
    Interface for stock metadata providers.

    Provides company information such as name, sector, market cap, etc.
    """

    @property
    def data_types(self) -> List[DataType]:
        return [DataType.STOCK_INFO]

    @abstractmethod
    def fetch_stock_info(self, ticker: str) -> ProviderResult:
        """
        Fetch stock metadata for a ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            ProviderResult with data=StockInfoData on success
        """
        pass


class SelloffProvider(BaseProvider):
    """
    Interface for selloff metrics providers.

    Provides volume-based selloff metrics for analyzing selling pressure.
    """

    @property
    def data_types(self) -> List[DataType]:
        return [DataType.SELLOFF]

    @abstractmethod
    def fetch_selloff(self, ticker: str) -> ProviderResult:
        """
        Fetch selloff metrics for a ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            ProviderResult with data=SelloffData on success
        """
        pass


class MultiDataProvider(BaseProvider):
    """
    Base class for providers that supply multiple data types.

    For example, yfinance can provide prices, EPS, and dividends.
    """

    @property
    @abstractmethod
    def data_types(self) -> List[DataType]:
        """Override to specify all supported data types."""
        pass
