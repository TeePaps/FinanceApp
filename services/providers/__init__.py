"""
Market Data Providers Package

A pluggable system for fetching market data from multiple sources
with configurable priority and automatic fallbacks.

Usage:
    from services.providers import get_orchestrator, init_providers

    # Initialize all providers (call once at app startup)
    init_providers()

    # Get the orchestrator for fetching data
    orchestrator = get_orchestrator()

    # Fetch prices (uses configured provider priority with fallbacks)
    prices = orchestrator.fetch_prices(["AAPL", "GOOGL", "MSFT"])

    # Fetch EPS (SEC first, yfinance fallback)
    eps_result = orchestrator.fetch_eps("AAPL")
"""

from .base import (
    DataType,
    ProviderResult,
    PriceData,
    EPSData,
    DividendData,
    HistoricalPriceData,
    StockInfoData,
    SelloffData,
    BaseProvider,
    PriceProvider,
    EPSProvider,
    DividendProvider,
    HistoricalPriceProvider,
    StockInfoProvider,
    SelloffProvider,
)

from .config import (
    ProviderConfig,
    get_config,
    reload_config,
    update_config,
    get_price_providers,
    set_price_providers,
    get_eps_providers,
    set_eps_providers,
    get_dividend_providers,
    set_dividend_providers,
    get_provider_order,
    set_provider_order,
    get_disabled_providers,
    set_disabled_providers,
    is_provider_enabled,
    enable_provider,
    disable_provider,
)

from .secrets import (
    get_secret,
    set_secret,
    has_secret,
    get_fmp_api_key,
    set_fmp_api_key,
    has_fmp_api_key,
    get_alpaca_api_key,
    get_alpaca_api_secret,
    set_alpaca_credentials,
    has_alpaca_credentials,
    get_alpaca_api_endpoint,
    set_alpaca_api_endpoint,
    get_ibkr_host,
    get_ibkr_port,
    get_ibkr_client_id,
    set_ibkr_connection,
    has_ibkr_settings,
)

from .registry import (
    ProviderRegistry,
    DataOrchestrator,
    get_registry,
    get_orchestrator,
    init_providers,
)

from .circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    get_circuit_breaker,
    reset_circuit_breaker,
)

# Provider implementations
from .yfinance_provider import (
    YFinancePriceProvider,
    YFinanceEPSProvider,
    YFinanceDividendProvider,
)

from .sec_provider import SECEPSProvider

from .fmp_provider import FMPPriceProvider, validate_fmp_api_key

from .alpaca_provider import AlpacaPriceProvider, validate_alpaca_api_key

from .ibkr_provider import IBKRPriceProvider, validate_ibkr_connection, disconnect_ibkr

from .defeatbeta_provider import DefeatBetaPriceProvider, DefeatBetaEPSProvider

__all__ = [
    # Base classes
    'DataType',
    'ProviderResult',
    'PriceData',
    'EPSData',
    'DividendData',
    'HistoricalPriceData',
    'StockInfoData',
    'SelloffData',
    'BaseProvider',
    'PriceProvider',
    'EPSProvider',
    'DividendProvider',
    'HistoricalPriceProvider',
    'StockInfoProvider',
    'SelloffProvider',

    # Configuration
    'ProviderConfig',
    'get_config',
    'reload_config',
    'update_config',
    'get_price_providers',
    'set_price_providers',
    'get_eps_providers',
    'set_eps_providers',
    'get_dividend_providers',
    'set_dividend_providers',
    'get_provider_order',
    'set_provider_order',
    'get_disabled_providers',
    'set_disabled_providers',
    'is_provider_enabled',
    'enable_provider',
    'disable_provider',

    # Secrets
    'get_secret',
    'set_secret',
    'has_secret',
    'get_fmp_api_key',
    'set_fmp_api_key',
    'has_fmp_api_key',
    'get_alpaca_api_key',
    'get_alpaca_api_secret',
    'set_alpaca_credentials',
    'has_alpaca_credentials',
    'get_alpaca_api_endpoint',
    'set_alpaca_api_endpoint',
    'get_ibkr_host',
    'get_ibkr_port',
    'get_ibkr_client_id',
    'set_ibkr_connection',
    'has_ibkr_settings',

    # Registry and Orchestrator
    'ProviderRegistry',
    'DataOrchestrator',
    'get_registry',
    'get_orchestrator',
    'init_providers',

    # Circuit Breaker
    'CircuitBreaker',
    'CircuitState',
    'get_circuit_breaker',
    'reset_circuit_breaker',

    # Provider implementations
    'YFinancePriceProvider',
    'YFinanceEPSProvider',
    'YFinanceDividendProvider',
    'SECEPSProvider',
    'FMPPriceProvider',
    'validate_fmp_api_key',
    'AlpacaPriceProvider',
    'validate_alpaca_api_key',
    'IBKRPriceProvider',
    'validate_ibkr_connection',
    'disconnect_ibkr',
    'DefeatBetaPriceProvider',
    'DefeatBetaEPSProvider',
]
