"""
Interactive Brokers provider implementation.

Provides real-time price data from Interactive Brokers TWS/Gateway.
Requires a running TWS or IB Gateway with API enabled.

Connection settings stored in data_private/secrets.json:
- IBKR_HOST: Host address (default: 127.0.0.1)
- IBKR_PORT: API port (default: 7497 for TWS, 4001 for Gateway)
- IBKR_CLIENT_ID: Client ID for the connection (default: 1)
"""

import time
import asyncio
from typing import Dict, List, Optional
from datetime import datetime
from threading import Lock

import config
from .base import PriceProvider, ProviderResult, PriceData, DataType
from .secrets import get_secret

# Default connection settings
DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 7497  # TWS default; use 4001 for IB Gateway
DEFAULT_CLIENT_ID = 10  # Use a unique client ID to avoid conflicts
MARKET_DATA_TIMEOUT = 5  # Seconds to wait for market data
IBKR_BATCH_SIZE = 50  # Max tickers per batch - IB has limits on concurrent requests
IBKR_POLL_INTERVAL = config.IBKR_POLL_INTERVAL  # Interval for polling market data
IBKR_SNAPSHOT_WAIT = config.IBKR_SNAPSHOT_WAIT  # Wait time for snapshot data


def _get_ib_module():
    """Get the IB async module (tries ib_async first, then ib_insync)."""
    try:
        import ib_async
        return ib_async, 'ib_async'
    except ImportError:
        pass

    try:
        import ib_insync
        return ib_insync, 'ib_insync'
    except ImportError:
        pass

    return None, None


class IBKRConnection:
    """
    Manages a persistent connection to Interactive Brokers.

    The connection is shared across all requests and automatically
    reconnects if disconnected. Uses a singleton pattern.
    """

    _instance: Optional['IBKRConnection'] = None
    _lock = Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._ib = None
        self._ib_module = None
        self._module_name = None
        self._connected = False
        self._last_connect_attempt = 0
        self._connect_cooldown = 5  # Seconds between reconnect attempts
        self._initialized = True

    def _get_connection_params(self) -> tuple:
        """Get connection parameters from secrets or defaults."""
        host = get_secret('IBKR_HOST') or DEFAULT_HOST
        port = get_secret('IBKR_PORT')
        client_id = get_secret('IBKR_CLIENT_ID')

        # Convert to int if string
        if port:
            port = int(port)
        else:
            port = DEFAULT_PORT

        if client_id:
            client_id = int(client_id)
        else:
            client_id = DEFAULT_CLIENT_ID

        return host, port, client_id

    def _ensure_module(self) -> bool:
        """Ensure we have the IB module loaded."""
        if self._ib_module is None:
            self._ib_module, self._module_name = _get_ib_module()
        return self._ib_module is not None

    def connect(self) -> bool:
        """
        Connect to TWS/Gateway if not already connected.

        Returns:
            True if connected successfully, False otherwise
        """
        if self._connected and self._ib and self._ib.isConnected():
            return True

        # Rate limit reconnect attempts
        now = time.time()
        if now - self._last_connect_attempt < self._connect_cooldown:
            return False
        self._last_connect_attempt = now

        if not self._ensure_module():
            return False

        try:
            host, port, client_id = self._get_connection_params()

            # Create new IB instance
            self._ib = self._ib_module.IB()

            # Connect synchronously
            self._ib.connect(host, port, clientId=client_id, readonly=True)

            if self._ib.isConnected():
                self._connected = True
                # Request delayed data if no market data subscription
                self._ib.reqMarketDataType(3)  # 3 = delayed data
                return True
            else:
                self._connected = False
                return False

        except Exception as e:
            self._connected = False
            self._ib = None
            return False

    def disconnect(self):
        """Disconnect from TWS/Gateway."""
        if self._ib:
            try:
                self._ib.disconnect()
            except Exception:
                pass
        self._connected = False
        self._ib = None

    def is_connected(self) -> bool:
        """Check if currently connected."""
        return self._connected and self._ib and self._ib.isConnected()

    @property
    def ib(self):
        """Get the IB instance (connect if needed)."""
        if not self.is_connected():
            self.connect()
        return self._ib

    @property
    def module(self):
        """Get the IB module."""
        self._ensure_module()
        return self._ib_module


def get_ibkr_connection() -> IBKRConnection:
    """Get the singleton IBKR connection instance."""
    return IBKRConnection()


class IBKRPriceProvider(PriceProvider):
    """
    Interactive Brokers price provider.

    Provides real-time prices from TWS/IB Gateway.
    Requires TWS or IB Gateway running with API enabled.
    Supports batch fetching for efficiency.
    """

    def __init__(self):
        self._connection = get_ibkr_connection()

    @property
    def name(self) -> str:
        return "ibkr"

    @property
    def display_name(self) -> str:
        return "Interactive Brokers"

    @property
    def data_types(self) -> List[DataType]:
        return [DataType.PRICE]

    def is_available(self) -> bool:
        """
        Check if IBKR provider is available.

        Returns True if:
        1. ib_async or ib_insync is installed
        2. TWS/Gateway port is reachable
        """
        # Check if module is available
        module, _ = _get_ib_module()
        if module is None:
            return False

        # Check if port is reachable (without consuming client ID)
        import socket
        host, port, _ = self._connection._get_connection_params()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except Exception:
            return False

    @property
    def rate_limit(self) -> float:
        return 0.05  # 50ms between requests - IBKR is fast

    @property
    def supports_batch(self) -> bool:
        return True

    @property
    def is_realtime(self) -> bool:
        return True  # IBKR provides real-time data

    def _create_stock_contract(self, ticker: str):
        """Create and qualify a stock contract for the given ticker."""
        module = self._connection.module
        ib = self._connection.ib
        if module is None or ib is None:
            return None

        contract = module.Stock(ticker.upper(), 'SMART', 'USD')

        # Qualify the contract to get conId
        try:
            qualified = ib.qualifyContracts(contract)
            if qualified:
                return qualified[0]
        except Exception:
            pass

        return contract

    def _wait_for_market_data(self, ticker, timeout: float = MARKET_DATA_TIMEOUT) -> Optional[float]:
        """
        Wait for market data to be populated.

        Args:
            ticker: The ticker object from reqMktData
            timeout: Maximum time to wait in seconds

        Returns:
            The last price if available, None otherwise
        """
        ib = self._connection.ib
        if not ib:
            return None

        start = time.time()
        while time.time() - start < timeout:
            # Check if we have data
            if ticker.last and ticker.last > 0:
                return float(ticker.last)
            if ticker.close and ticker.close > 0:
                return float(ticker.close)
            if ticker.bid and ticker.bid > 0 and ticker.ask and ticker.ask > 0:
                return float((ticker.bid + ticker.ask) / 2)

            # Wait a bit and let IB process events
            ib.sleep(IBKR_POLL_INTERVAL)

        return None

    def fetch_price(self, ticker: str) -> ProviderResult:
        """Fetch price for a single ticker."""
        result = self.fetch_prices([ticker])
        return result.get(ticker.upper(), ProviderResult(
            success=False,
            data=None,
            source=self.name,
            error="Ticker not found"
        ))

    def fetch_prices(self, tickers: List[str]) -> Dict[str, ProviderResult]:
        """
        Batch fetch prices from Interactive Brokers.

        Uses snapshot mode for automatic subscription cancellation and
        processes tickers in batches to avoid overwhelming IB's API.
        """
        tickers = [t.upper() for t in tickers]
        results = {}

        # Ensure connected
        if not self._connection.connect():
            module, _ = _get_ib_module()
            if module is None:
                error = "ib_async/ib_insync not installed"
            else:
                error = "Cannot connect to TWS/IB Gateway"

            return {
                t: ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error=error
                ) for t in tickers
            }

        ib = self._connection.ib
        module = self._connection.module

        if not ib or not module:
            return {
                t: ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error="IB connection not available"
                ) for t in tickers
            }

        # Process in batches to avoid overwhelming IB's API
        for batch_start in range(0, len(tickers), IBKR_BATCH_SIZE):
            batch = tickers[batch_start:batch_start + IBKR_BATCH_SIZE]
            batch_results = self._fetch_batch_snapshot(ib, batch)
            results.update(batch_results)

        # Ensure all tickers have a result
        for symbol in tickers:
            if symbol not in results:
                results[symbol] = ProviderResult(
                    success=False,
                    data=None,
                    source=self.name,
                    error="Ticker not processed"
                )

        return results

    def _fetch_batch_snapshot(self, ib, tickers: List[str]) -> Dict[str, ProviderResult]:
        """
        Fetch a batch of tickers using snapshot mode.

        Snapshot mode (snapshot=True) automatically cancels subscriptions
        after receiving data, reducing the need for manual cleanup.
        """
        results = {}
        ticker_data = {}  # Maps ticker symbol to IB ticker object

        try:
            # Request snapshot data for all tickers in batch
            for symbol in tickers:
                try:
                    contract = self._create_stock_contract(symbol)
                    if contract:
                        # Request market data with snapshot=True for auto-cancel
                        # Parameters: contract, genericTickList, snapshot, regulatorySnapshot
                        ticker_obj = ib.reqMktData(contract, '', True, False)
                        ticker_data[symbol] = (ticker_obj, contract)
                except Exception as e:
                    results[symbol] = ProviderResult(
                        success=False,
                        data=None,
                        source=self.name,
                        error=f"Failed to request data: {str(e)}"
                    )

            # Wait for snapshot data to arrive (with timeout)
            start = time.time()
            pending = set(ticker_data.keys())

            while pending and (time.time() - start) < MARKET_DATA_TIMEOUT:
                ib.sleep(IBKR_POLL_INTERVAL)

                for symbol in list(pending):
                    ticker_obj, contract = ticker_data[symbol]

                    # Check for price data
                    price = None

                    # Try last traded price first
                    if ticker_obj.last and ticker_obj.last > 0:
                        price = float(ticker_obj.last)
                    # Then try close price
                    elif ticker_obj.close and ticker_obj.close > 0:
                        price = float(ticker_obj.close)
                    # Then try mid of bid/ask
                    elif ticker_obj.bid and ticker_obj.bid > 0 and ticker_obj.ask and ticker_obj.ask > 0:
                        price = float((ticker_obj.bid + ticker_obj.ask) / 2)

                    if price:
                        results[symbol] = ProviderResult(
                            success=True,
                            data=price,
                            source=self.name
                        )
                        pending.discard(symbol)

            # Mark remaining as failed
            for symbol in pending:
                if symbol not in results:
                    results[symbol] = ProviderResult(
                        success=False,
                        data=None,
                        source=self.name,
                        error="Timeout waiting for market data"
                    )

        finally:
            # Snapshot mode auto-cancels, but clean up any stragglers just in case
            for symbol, (ticker_obj, contract) in ticker_data.items():
                try:
                    ib.cancelMktData(contract)
                except Exception:
                    pass

        return results


def validate_ibkr_connection(host: str = None, port: int = None, client_id: int = None) -> tuple:
    """
    Validate IBKR connection settings.

    Args:
        host: Host address (default: 127.0.0.1)
        port: API port (default: 7497)
        client_id: Client ID (default: 10)

    Returns:
        Tuple of (is_valid: bool, message: str)
    """
    module, module_name = _get_ib_module()

    if module is None:
        return False, "ib_async or ib_insync not installed. Run: pip install ib_async"

    host = host or DEFAULT_HOST
    port = port or DEFAULT_PORT
    client_id = client_id or DEFAULT_CLIENT_ID

    try:
        ib = module.IB()
        ib.connect(host, port, clientId=client_id, readonly=True)

        if ib.isConnected():
            # Try to get a price to verify data access
            try:
                contract = module.Stock('AAPL', 'SMART', 'USD')

                # Qualify the contract first
                qualified = ib.qualifyContracts(contract)
                if qualified:
                    contract = qualified[0]

                ib.reqMarketDataType(3)  # Delayed data
                # Use snapshot mode (True) for auto-cancel after receiving data
                ticker = ib.reqMktData(contract, '', True, False)
                ib.sleep(IBKR_SNAPSHOT_WAIT)  # Wait for data

                if ticker.last or ticker.close or (ticker.bid and ticker.ask):
                    # Snapshot auto-cancels, but call anyway for safety
                    try:
                        ib.cancelMktData(contract)
                    except Exception:
                        pass
                    ib.disconnect()
                    return True, f"Connected successfully via {module_name}"
                else:
                    try:
                        ib.cancelMktData(contract)
                    except Exception:
                        pass
                    ib.disconnect()
                    return True, f"Connected but no market data received (check market hours)"
            except Exception as e:
                ib.disconnect()
                return True, f"Connected but data request failed: {str(e)}"
        else:
            return False, "Failed to connect - is TWS/Gateway running with API enabled?"

    except Exception as e:
        error_msg = str(e).lower()
        if "connection refused" in error_msg:
            return False, f"Connection refused on {host}:{port}. Is TWS/Gateway running with API enabled?"
        if "already connected" in error_msg:
            return True, "Already connected to TWS/Gateway"
        if "client id" in error_msg:
            return False, f"Client ID {client_id} already in use. Try a different client ID."
        return False, f"Connection error: {str(e)}"


def disconnect_ibkr():
    """Disconnect from IBKR (useful for cleanup)."""
    connection = get_ibkr_connection()
    connection.disconnect()
