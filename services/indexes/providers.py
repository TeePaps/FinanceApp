"""
Index constituent providers with fallback support.

Similar to the market data provider system, this module provides a pluggable
architecture for fetching index constituents from multiple sources with
automatic fallback when a source fails or returns stale data.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime
import time
import pandas as pd
import requests
from io import StringIO

# How long a tripped index-provider circuit stays open before a half-open retry
INDEX_CIRCUIT_COOLDOWN = 600  # seconds (10 min)

from config import INDEX_PROVIDER_TIMEOUT


def _build_index_session():
    """Pooled, retrying session shared by all index-constituent providers.

    These providers hit the same few hosts (Wikipedia, Slickcharts, iShares,
    GitHub) repeatedly, each with a bare requests.get() that re-handshakes TLS
    every time. A shared session also gives the fallback chain sane retry
    behaviour on transient 5xx/429 responses.
    """
    session = requests.Session()
    try:
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        retry = Retry(
            total=2,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(['GET']),
        )
        session.mount('https://', HTTPAdapter(pool_maxsize=4, max_retries=retry))
        session.mount('http://', HTTPAdapter(pool_maxsize=4, max_retries=retry))
    except Exception:
        pass
    return session


_INDEX_SESSION = _build_index_session()


# =============================================================================
# Cross-Platform HTML Parser Detection
# =============================================================================

def _get_html_parser_flavor() -> Optional[str]:
    """
    Detect the best available HTML parser for pd.read_html().

    Returns:
        None: Use lxml (fastest, default when available)
        'bs4': Use html5lib via BeautifulSoup (cross-platform fallback)
    """
    try:
        import lxml
        return None  # Let pandas use lxml (default, fastest)
    except ImportError:
        try:
            import html5lib
            import bs4
            return 'bs4'  # Use html5lib via BeautifulSoup
        except ImportError:
            return None  # Will fail, but let pandas give the error


def _read_html_safe(html_content: str, **kwargs) -> list:
    """
    Cross-platform wrapper for pd.read_html() that uses available parser.

    Uses lxml when available (faster), falls back to html5lib on Windows.
    """
    flavor = _get_html_parser_flavor()
    if flavor:
        kwargs['flavor'] = flavor
    return pd.read_html(StringIO(html_content), **kwargs)


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class IndexResult:
    """Result from an index provider fetch operation."""
    success: bool
    tickers: List[str]
    source: str
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Base Provider Class
# =============================================================================

class IndexProvider(ABC):
    """
    Base class for index constituent providers.

    Each provider knows how to fetch constituents for one or more indexes
    from a specific source (Wikipedia, iShares, etc.).
    """

    # Standard headers for web requests
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    }

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique provider identifier."""
        pass

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name for UI."""
        pass

    @property
    @abstractmethod
    def supported_indexes(self) -> List[str]:
        """List of index IDs this provider supports."""
        pass

    @abstractmethod
    def fetch_constituents(self, index_id: str) -> IndexResult:
        """
        Fetch current constituents for an index.

        Args:
            index_id: Index identifier (e.g., 'sp500', 'russell2000')

        Returns:
            IndexResult with tickers list on success
        """
        pass

    def is_available(self) -> bool:
        """Check if provider is available (can be overridden for API key checks)."""
        return True

    def supports_index(self, index_id: str) -> bool:
        """Check if this provider supports a specific index."""
        return index_id in self.supported_indexes

    def _normalize_ticker(self, ticker: str) -> str:
        """Normalize ticker symbol (e.g., BRK.B -> BRK-B)."""
        return ticker.replace('.', '-').upper().strip()


# =============================================================================
# Wikipedia Provider
# =============================================================================

class WikipediaIndexProvider(IndexProvider):
    """
    Fetches index constituents from Wikipedia tables.

    Supports: S&P 500, Nasdaq 100, Dow 30, S&P 600
    Generally accurate and updated by Wikipedia editors.
    """

    # Configuration for each index: (URL, table_index, column_name)
    INDEX_CONFIG = {
        'sp500': (
            'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies',
            0, 'Symbol'
        ),
        'nasdaq100': (
            'https://en.wikipedia.org/wiki/Nasdaq-100',
            4, 'Ticker'
        ),
        'dow30': (
            'https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average',
            2, 'Symbol'
        ),
        'sp600': (
            'https://en.wikipedia.org/wiki/List_of_S%26P_600_companies',
            0, 'Symbol'
        ),
    }

    @property
    def name(self) -> str:
        return 'wikipedia'

    @property
    def display_name(self) -> str:
        return 'Wikipedia'

    @property
    def supported_indexes(self) -> List[str]:
        return list(self.INDEX_CONFIG.keys())

    def fetch_constituents(self, index_id: str) -> IndexResult:
        if index_id not in self.INDEX_CONFIG:
            return IndexResult(
                success=False,
                tickers=[],
                source=self.name,
                error=f"Index '{index_id}' not supported by {self.display_name}"
            )

        url, table_idx, col_name = self.INDEX_CONFIG[index_id]

        try:
            resp = _INDEX_SESSION.get(url, headers=self.HEADERS, timeout=INDEX_PROVIDER_TIMEOUT)
            resp.raise_for_status()

            tables = _read_html_safe(resp.text)
            if table_idx >= len(tables):
                return IndexResult(
                    success=False,
                    tickers=[],
                    source=self.name,
                    error=f"Table index {table_idx} out of range (found {len(tables)} tables)"
                )

            table = tables[table_idx]
            if col_name not in table.columns:
                return IndexResult(
                    success=False,
                    tickers=[],
                    source=self.name,
                    error=f"Column '{col_name}' not found. Available: {list(table.columns)}"
                )

            # Coerce to str first (like the other providers): pandas.read_html
            # can yield NaN/numeric cells for blank or footnote rows, and
            # _normalize_ticker's .replace() would raise AttributeError on a
            # float, failing the ENTIRE fetch.
            tickers = [self._normalize_ticker(str(t)) for t in table[col_name].tolist()]
            tickers = [t for t in tickers if t and t != 'NAN']  # Remove empty / NaN

            return IndexResult(
                success=True,
                tickers=tickers,
                source=self.name,
                metadata={'url': url, 'count': len(tickers)}
            )

        except requests.RequestException as e:
            return IndexResult(
                success=False,
                tickers=[],
                source=self.name,
                error=f"Request failed: {str(e)}"
            )
        except Exception as e:
            return IndexResult(
                success=False,
                tickers=[],
                source=self.name,
                error=f"Parse error: {str(e)}"
            )


# =============================================================================
# Slickcharts Provider (Backup for Wikipedia)
# =============================================================================

class SlickchartsIndexProvider(IndexProvider):
    """
    Fetches index constituents from Slickcharts.com.

    Supports: S&P 500, Nasdaq 100, Dow 30
    Good backup source if Wikipedia fails.
    """

    INDEX_CONFIG = {
        'sp500': ('https://www.slickcharts.com/sp500', 'Symbol'),
        'nasdaq100': ('https://www.slickcharts.com/nasdaq100', 'Symbol'),
        'dow30': ('https://www.slickcharts.com/dowjones', 'Symbol'),
    }

    @property
    def name(self) -> str:
        return 'slickcharts'

    @property
    def display_name(self) -> str:
        return 'Slickcharts'

    @property
    def supported_indexes(self) -> List[str]:
        return list(self.INDEX_CONFIG.keys())

    def fetch_constituents(self, index_id: str) -> IndexResult:
        if index_id not in self.INDEX_CONFIG:
            return IndexResult(
                success=False,
                tickers=[],
                source=self.name,
                error=f"Index '{index_id}' not supported by {self.display_name}"
            )

        url, col_name = self.INDEX_CONFIG[index_id]

        try:
            resp = _INDEX_SESSION.get(url, headers=self.HEADERS, timeout=INDEX_PROVIDER_TIMEOUT)
            resp.raise_for_status()

            tables = _read_html_safe(resp.text)
            if not tables:
                return IndexResult(
                    success=False,
                    tickers=[],
                    source=self.name,
                    error="No tables found on page"
                )

            # Find the table with the Symbol column
            table = None
            for t in tables:
                if col_name in t.columns:
                    table = t
                    break

            if table is None:
                return IndexResult(
                    success=False,
                    tickers=[],
                    source=self.name,
                    error=f"No table with '{col_name}' column found"
                )

            tickers = [self._normalize_ticker(str(t)) for t in table[col_name].tolist()]
            tickers = [t for t in tickers if t and t != 'NAN']

            return IndexResult(
                success=True,
                tickers=tickers,
                source=self.name,
                metadata={'url': url, 'count': len(tickers)}
            )

        except Exception as e:
            return IndexResult(
                success=False,
                tickers=[],
                source=self.name,
                error=str(e)
            )


# =============================================================================
# iShares ETF Provider (For Russell 2000)
# =============================================================================

class iSharesIndexProvider(IndexProvider):
    """
    Fetches index constituents from iShares ETF holdings.

    Uses ETF holdings as a proxy for index constituents.
    Updated daily by BlackRock.

    Supports: Russell 2000 (via IWM ETF)
    """

    # ETF holdings URLs: index_id -> (url, ticker_column)
    ETF_CONFIG = {
        'russell2000': (
            'https://www.ishares.com/us/products/239710/ishares-russell-2000-etf/1467271812596.ajax?fileType=csv&fileName=IWM_holdings&dataType=fund',
            'Ticker'
        ),
    }

    @property
    def name(self) -> str:
        return 'ishares'

    @property
    def display_name(self) -> str:
        return 'iShares ETF Holdings'

    @property
    def supported_indexes(self) -> List[str]:
        return list(self.ETF_CONFIG.keys())

    def fetch_constituents(self, index_id: str) -> IndexResult:
        if index_id not in self.ETF_CONFIG:
            return IndexResult(
                success=False,
                tickers=[],
                source=self.name,
                error=f"Index '{index_id}' not supported by {self.display_name}"
            )

        url, ticker_col = self.ETF_CONFIG[index_id]

        try:
            resp = _INDEX_SESSION.get(url, headers=self.HEADERS, timeout=INDEX_PROVIDER_TIMEOUT)
            resp.raise_for_status()

            # iShares CSV has metadata rows at the top
            lines = resp.text.split('\n')

            # Find the header row (starts with "Ticker,"). None sentinel, not
            # 0 — a header legitimately at line 0 was misread as "not found".
            data_start = None
            as_of_date = None
            for i, line in enumerate(lines):
                if line.startswith('Fund Holdings as of,'):
                    as_of_date = line.split(',')[1].strip('"')
                if line.startswith('Ticker,'):
                    data_start = i
                    break

            if data_start is None:
                return IndexResult(
                    success=False,
                    tickers=[],
                    source=self.name,
                    error="Could not find header row in iShares CSV"
                )

            # Parse from data start
            csv_data = '\n'.join(lines[data_start:])
            df = pd.read_csv(StringIO(csv_data))

            # Filter to equities only (exclude cash, derivatives)
            if 'Asset Class' in df.columns:
                df = df[df['Asset Class'] == 'Equity']

            if ticker_col not in df.columns:
                return IndexResult(
                    success=False,
                    tickers=[],
                    source=self.name,
                    error=f"Column '{ticker_col}' not found in CSV"
                )

            tickers = [self._normalize_ticker(str(t)) for t in df[ticker_col].tolist()]
            tickers = [t for t in tickers if t and t != 'NAN' and not t.startswith('-')]

            return IndexResult(
                success=True,
                tickers=tickers,
                source=self.name,
                metadata={
                    'url': url,
                    'count': len(tickers),
                    'as_of_date': as_of_date
                }
            )

        except Exception as e:
            return IndexResult(
                success=False,
                tickers=[],
                source=self.name,
                error=str(e)
            )


# =============================================================================
# GitHub Provider (Legacy fallback)
# =============================================================================

class GitHubIndexProvider(IndexProvider):
    """
    Fetches index constituents from GitHub repositories.

    WARNING: These sources may be outdated. Use as last resort.
    """

    INDEX_CONFIG = {
        'russell2000': (
            'https://raw.githubusercontent.com/ikoniaris/Russell2000/master/russell_2000_components.csv',
            'Ticker',
            '2016-02'  # Last update date
        ),
    }

    @property
    def name(self) -> str:
        return 'github'

    @property
    def display_name(self) -> str:
        return 'GitHub (Legacy)'

    @property
    def supported_indexes(self) -> List[str]:
        return list(self.INDEX_CONFIG.keys())

    def fetch_constituents(self, index_id: str) -> IndexResult:
        if index_id not in self.INDEX_CONFIG:
            return IndexResult(
                success=False,
                tickers=[],
                source=self.name,
                error=f"Index '{index_id}' not supported"
            )

        url, col_name, last_update = self.INDEX_CONFIG[index_id]

        try:
            resp = _INDEX_SESSION.get(url, headers=self.HEADERS, timeout=INDEX_PROVIDER_TIMEOUT)
            resp.raise_for_status()

            df = pd.read_csv(StringIO(resp.text))

            if col_name not in df.columns:
                return IndexResult(
                    success=False,
                    tickers=[],
                    source=self.name,
                    error=f"Column '{col_name}' not found"
                )

            tickers = [self._normalize_ticker(str(t)) for t in df[col_name].tolist()]
            tickers = [t for t in tickers if t]

            return IndexResult(
                success=True,
                tickers=tickers,
                source=self.name,
                metadata={
                    'url': url,
                    'count': len(tickers),
                    'last_update': last_update,
                    'warning': f'Source last updated {last_update} - data may be stale'
                }
            )

        except Exception as e:
            return IndexResult(
                success=False,
                tickers=[],
                source=self.name,
                error=str(e)
            )


# =============================================================================
# Index Orchestrator
# =============================================================================

class IndexOrchestrator:
    """
    Coordinates fetching index constituents with automatic fallback.

    Tries providers in priority order until one succeeds.
    Tracks provider health and can skip failing providers.
    """

    # Provider priority order per index
    # First provider is tried first, then fallbacks
    PROVIDER_ORDER = {
        'sp500': ['wikipedia', 'slickcharts'],
        'nasdaq100': ['wikipedia', 'slickcharts'],
        'dow30': ['wikipedia', 'slickcharts'],
        'sp600': ['wikipedia'],
        'russell2000': ['ishares', 'github'],  # iShares first, GitHub as fallback
    }

    def __init__(self):
        # Register all providers
        self._providers: Dict[str, IndexProvider] = {}
        self._register_providers()

        # Track failures for circuit breaker pattern. Records the last failure
        # time too, so an open circuit can self-heal after a cooldown — without
        # it, 3 cumulative failures abandoned a provider for the entire process
        # lifetime (reset_circuit_breaker was never called anywhere).
        self._failures: Dict[str, int] = {}
        self._last_failure: Dict[str, float] = {}
        self._failure_threshold = 3

    def _circuit_open(self, name: str) -> bool:
        """True if the provider's circuit is open AND still in cooldown.

        Once the cooldown elapses this returns False for a single half-open
        probe; a subsequent failure re-opens it (count keeps climbing)."""
        if self._failures.get(name, 0) < self._failure_threshold:
            return False
        last = self._last_failure.get(name, 0)
        return (time.time() - last) < INDEX_CIRCUIT_COOLDOWN

    def _record_failure(self, name: str):
        self._failures[name] = self._failures.get(name, 0) + 1
        self._last_failure[name] = time.time()

    def _register_providers(self):
        """Register all available index providers."""
        providers = [
            WikipediaIndexProvider(),
            SlickchartsIndexProvider(),
            iSharesIndexProvider(),
            GitHubIndexProvider(),
        ]
        for p in providers:
            self._providers[p.name] = p

    def get_provider(self, name: str) -> Optional[IndexProvider]:
        """Get a provider by name."""
        return self._providers.get(name)

    def list_providers(self) -> List[Dict]:
        """Get status of all providers."""
        return [
            {
                'name': p.name,
                'display_name': p.display_name,
                'available': p.is_available(),
                'supported_indexes': p.supported_indexes
            }
            for p in self._providers.values()
        ]

    def get_providers_for_index(self, index_id: str) -> List[IndexProvider]:
        """Get providers that support an index, in priority order."""
        order = self.PROVIDER_ORDER.get(index_id, [])
        providers = []

        for name in order:
            if name in self._providers:
                p = self._providers[name]
                if p.is_available() and p.supports_index(index_id):
                    providers.append(p)

        return providers

    def fetch_constituents(self, index_id: str) -> IndexResult:
        """
        Fetch constituents for an index, trying providers in order.

        Returns result from first successful provider.
        If all fail, returns error with combined failure messages.
        """
        providers = self.get_providers_for_index(index_id)

        try:
            from services.activity_log import activity_log
            activity_log.log("info", "index", f"Fetching {index_id} constituents...")
        except Exception:
            pass

        if not providers:
            try:
                from services.activity_log import activity_log
                activity_log.log("error", "index", f"No providers for {index_id}")
            except Exception:
                pass
            return IndexResult(
                success=False,
                tickers=[],
                source='none',
                error=f"No providers available for index '{index_id}'"
            )

        errors = []
        for provider in providers:
            # Skip providers whose circuit is open and still cooling down
            if self._circuit_open(provider.name):
                errors.append(f"{provider.name}: circuit breaker open (cooling down)")
                continue

            try:
                result = provider.fetch_constituents(index_id)

                if result.success and len(result.tickers) > 0:
                    # Reset failure count + timer on success
                    self._failures[provider.name] = 0
                    self._last_failure.pop(provider.name, None)
                    try:
                        from services.activity_log import activity_log
                        activity_log.log("success", "index", f"{index_id}: {len(result.tickers)} tickers from {provider.name}")
                    except Exception:
                        pass
                    return result
                else:
                    errors.append(f"{provider.name}: {result.error or 'empty result'}")
                    self._record_failure(provider.name)

            except Exception as e:
                errors.append(f"{provider.name}: {str(e)}")
                self._record_failure(provider.name)

        try:
            from services.activity_log import activity_log
            activity_log.log("error", "index", f"{index_id}: all providers failed")
        except Exception:
            pass
        return IndexResult(
            success=False,
            tickers=[],
            source='none',
            error=f"All providers failed: {'; '.join(errors)}"
        )

    def reset_circuit_breaker(self, provider_name: Optional[str] = None):
        """Reset failure counts for provider(s)."""
        if provider_name:
            self._failures[provider_name] = 0
        else:
            self._failures.clear()

    def get_index_info(self, index_id: str) -> Dict:
        """Get info about available providers for an index."""
        providers = self.get_providers_for_index(index_id)
        return {
            'index_id': index_id,
            'providers': [
                {
                    'name': p.name,
                    'display_name': p.display_name,
                    'failures': self._failures.get(p.name, 0)
                }
                for p in providers
            ],
            'has_providers': len(providers) > 0
        }


# =============================================================================
# Module-level singleton
# =============================================================================

_orchestrator: Optional[IndexOrchestrator] = None


def get_index_orchestrator() -> IndexOrchestrator:
    """Get the global index orchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = IndexOrchestrator()
    return _orchestrator


def fetch_index_tickers(index_id: str) -> List[str]:
    """
    Convenience function to fetch index constituents.

    Returns empty list on failure (errors logged internally).
    """
    result = get_index_orchestrator().fetch_constituents(index_id)
    if result.success:
        return result.tickers
    else:
        # Error already logged in orchestrator
        return []
