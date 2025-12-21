"""
Provider configuration management.

Handles loading, saving, and accessing provider settings including:
- Provider priority order for each data type
- Cache durations
- Provider-specific settings

Configuration is stored in data_private/provider_config.json
"""

import os
import json
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field, asdict

# Path to config file
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
CONFIG_DIR = os.path.join(_BASE_DIR, 'data_private')
CONFIG_FILE = os.path.join(CONFIG_DIR, 'provider_config.json')

# Global config instance
_config: Optional['ProviderConfig'] = None


@dataclass
class ProviderConfig:
    """
    User-configurable provider settings.

    Provider lists define priority order - first provider is tried first,
    with subsequent providers used as fallbacks.
    """

    # Provider priority order (first = highest priority)
    # These are provider 'name' values
    # Only enabled providers are used; order determines fallback priority
    # Note: For prices, real-time providers are automatically prioritized over
    # historical-only providers (like defeatbeta) regardless of this order
    price_providers: List[str] = field(default_factory=lambda: ["ibkr", "alpaca", "yfinance", "fmp", "defeatbeta"])
    eps_providers: List[str] = field(default_factory=lambda: ["sec_edgar", "yfinance", "defeatbeta"])
    dividend_providers: List[str] = field(default_factory=lambda: ["yfinance"])

    # Disabled providers (excluded from fetching even if configured)
    disabled_providers: List[str] = field(default_factory=lambda: ["fmp"])

    # Cache settings
    price_cache_seconds: int = 300  # 5 minutes
    eps_cache_days: int = 1
    dividend_cache_days: int = 1

    # Rate limiting
    default_rate_limit: float = 0.2  # seconds between requests

    # Batch settings
    batch_size: int = 100  # Max tickers per batch request
    prefer_batch: bool = True  # Prefer batch-capable providers

    # Timeout settings
    provider_timeout_seconds: int = 10  # Max time per provider call

    # Circuit breaker settings
    circuit_breaker_enabled: bool = True
    failure_threshold: int = 3  # Failures before opening circuit
    failure_window_seconds: int = 120  # 2 min window to count failures
    cooldown_seconds: int = 120  # 2 min before retry after circuit opens

    @classmethod
    def load(cls) -> 'ProviderConfig':
        """
        Load configuration from file.

        Falls back to defaults if file doesn't exist or is invalid.
        """
        if not os.path.exists(CONFIG_FILE):
            return cls()

        try:
            with open(CONFIG_FILE, 'r') as f:
                data = json.load(f)

            return cls(
                price_providers=data.get('price_providers', ["ibkr", "alpaca", "yfinance", "fmp", "defeatbeta"]),
                eps_providers=data.get('eps_providers', ["sec_edgar", "yfinance", "defeatbeta"]),
                dividend_providers=data.get('dividend_providers', ["yfinance"]),
                disabled_providers=data.get('disabled_providers', ["fmp"]),
                price_cache_seconds=data.get('price_cache_seconds', 300),
                eps_cache_days=data.get('eps_cache_days', 1),
                dividend_cache_days=data.get('dividend_cache_days', 1),
                default_rate_limit=data.get('default_rate_limit', 0.2),
                batch_size=data.get('batch_size', 100),
                prefer_batch=data.get('prefer_batch', True),
                provider_timeout_seconds=data.get('provider_timeout_seconds', 10),
                circuit_breaker_enabled=data.get('circuit_breaker_enabled', True),
                failure_threshold=data.get('failure_threshold', 3),
                failure_window_seconds=data.get('failure_window_seconds', 300),
                cooldown_seconds=data.get('cooldown_seconds', 120),
            )
        except (json.JSONDecodeError, IOError, KeyError) as e:
            print(f"[Config] Error loading config, using defaults: {e}")
            return cls()

    def save(self):
        """Save configuration to file."""
        # Ensure directory exists
        if not os.path.exists(CONFIG_DIR):
            os.makedirs(CONFIG_DIR, mode=0o700)

        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(asdict(self), f, indent=2)
        except IOError as e:
            print(f"[Config] Error saving config: {e}")
            raise

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    def update(self, **kwargs):
        """
        Update configuration values.

        Args:
            **kwargs: Key-value pairs to update
        """
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self.save()


def get_config() -> ProviderConfig:
    """
    Get the current provider configuration.

    Loads from file on first call, then returns cached instance.
    """
    global _config
    if _config is None:
        _config = ProviderConfig.load()
    return _config


def reload_config():
    """Force reload configuration from file."""
    global _config
    _config = ProviderConfig.load()
    return _config


def update_config(**kwargs):
    """
    Update and save configuration.

    Args:
        **kwargs: Configuration values to update
    """
    # Reload from file first to pick up any manual edits
    config = reload_config()
    config.update(**kwargs)
    return config


def get_price_providers() -> List[str]:
    """Get ordered list of price provider names."""
    return get_config().price_providers


def set_price_providers(providers: List[str]):
    """Set price provider order."""
    update_config(price_providers=providers)


def get_eps_providers() -> List[str]:
    """Get ordered list of EPS provider names."""
    return get_config().eps_providers


def set_eps_providers(providers: List[str]):
    """Set EPS provider order."""
    update_config(eps_providers=providers)


def get_dividend_providers() -> List[str]:
    """Get ordered list of dividend provider names."""
    return get_config().dividend_providers


def set_dividend_providers(providers: List[str]):
    """Set dividend provider order."""
    update_config(dividend_providers=providers)


def get_provider_order(data_type: str) -> List[str]:
    """
    Get provider order for a specific data type.

    Args:
        data_type: 'price', 'eps', or 'dividend'

    Returns:
        List of provider names in priority order
    """
    config = get_config()
    if data_type == 'price':
        return config.price_providers
    elif data_type == 'eps':
        return config.eps_providers
    elif data_type == 'dividend':
        return config.dividend_providers
    else:
        raise ValueError(f"Unknown data type: {data_type}")


def set_provider_order(data_type: str, providers: List[str]):
    """
    Set provider order for a specific data type.

    Args:
        data_type: 'price', 'eps', or 'dividend'
        providers: List of provider names in priority order
    """
    if data_type == 'price':
        set_price_providers(providers)
    elif data_type == 'eps':
        set_eps_providers(providers)
    elif data_type == 'dividend':
        set_dividend_providers(providers)
    else:
        raise ValueError(f"Unknown data type: {data_type}")


def get_disabled_providers() -> List[str]:
    """Get list of disabled provider names."""
    return get_config().disabled_providers


def set_disabled_providers(providers: List[str]):
    """Set list of disabled providers."""
    update_config(disabled_providers=providers)


def is_provider_enabled(provider_name: str) -> bool:
    """Check if a provider is enabled."""
    return provider_name not in get_config().disabled_providers


def enable_provider(provider_name: str):
    """Enable a provider."""
    config = get_config()
    if provider_name in config.disabled_providers:
        new_disabled = [p for p in config.disabled_providers if p != provider_name]
        update_config(disabled_providers=new_disabled)


def disable_provider(provider_name: str):
    """Disable a provider."""
    config = get_config()
    if provider_name not in config.disabled_providers:
        new_disabled = config.disabled_providers + [provider_name]
        update_config(disabled_providers=new_disabled)
