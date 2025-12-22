"""
Provider configuration management.

Handles loading, saving, and accessing provider settings including:
- Provider priority order for each data type
- Cache durations
- Provider-specific settings

All configuration is stored in config.yaml at the project root.
"""

import os
from ruamel.yaml import YAML
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field, asdict

# Initialize YAML handler with comment and format preservation
_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.default_flow_style = False
_yaml.indent(mapping=2, sequence=2, offset=2)

# Path to config file
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
CONFIG_FILE = os.path.join(_BASE_DIR, 'config.yaml')

# Global config instance
_config: Optional['ProviderConfig'] = None
_full_yaml: Optional[Any] = None  # Cache full yaml (CommentedMap) for preservation on save


def _load_yaml() -> Dict[str, Any]:
    """Load the full config.yaml file."""
    global _full_yaml
    try:
        with open(CONFIG_FILE, 'r') as f:
            _full_yaml = _yaml.load(f)
            if _full_yaml is None:
                _full_yaml = {}
            return _full_yaml
    except (FileNotFoundError, Exception) as e:
        print(f"[ProviderConfig] Warning: Could not load config.yaml: {e}")
        _full_yaml = {}
        return {}


def _get_provider_section() -> Dict[str, Any]:
    """Get the providers section from config.yaml."""
    yaml_config = _load_yaml()
    return yaml_config.get('providers', {})


def _get_value(providers: Dict, key: str, default: Any) -> Any:
    """Get a value from providers dict, handling nested keys."""
    if '.' in key:
        parts = key.split('.')
        value = providers
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return default
            if value is None:
                return default
        return value
    return providers.get(key, default)


@dataclass
class ProviderConfig:
    """
    User-configurable provider settings.

    Provider lists define priority order - first provider is tried first,
    with subsequent providers used as fallbacks.
    """

    # Provider priority order (first = highest priority)
    price_providers: List[str] = field(default_factory=lambda: ["ibkr", "yfinance", "alpaca", "fmp", "defeatbeta"])
    eps_providers: List[str] = field(default_factory=lambda: ["sec_edgar", "yfinance", "defeatbeta"])
    dividend_providers: List[str] = field(default_factory=lambda: ["yfinance"])

    # Disabled providers (excluded from fetching even if configured)
    disabled_providers: List[str] = field(default_factory=lambda: ["fmp"])

    # Cache settings
    price_cache_seconds: int = 3600
    eps_cache_days: int = 1
    dividend_cache_days: int = 1

    # Rate limiting
    default_rate_limit: float = 0.2

    # Batch settings
    batch_size: int = 100
    prefer_batch: bool = True

    # Timeout settings
    provider_timeout_seconds: int = 10

    # Circuit breaker settings
    circuit_breaker_enabled: bool = True
    failure_threshold: int = 3
    failure_window_seconds: int = 300
    cooldown_seconds: int = 120

    @classmethod
    def load(cls) -> 'ProviderConfig':
        """Load configuration from config.yaml."""
        providers = _get_provider_section()

        # Copy lists to avoid sharing references with _full_yaml
        # (which would cause issues when modifying in save())
        return cls(
            price_providers=list(_get_value(providers, 'price_providers', ["ibkr", "yfinance", "alpaca", "fmp", "defeatbeta"])),
            eps_providers=list(_get_value(providers, 'eps_providers', ["sec_edgar", "yfinance", "defeatbeta"])),
            dividend_providers=list(_get_value(providers, 'dividend_providers', ["yfinance"])),
            disabled_providers=list(_get_value(providers, 'disabled_providers', ["fmp"])),
            price_cache_seconds=_get_value(providers, 'price_cache_seconds', 3600),
            eps_cache_days=_get_value(providers, 'eps_cache_days', 1),
            dividend_cache_days=_get_value(providers, 'dividend_cache_days', 1),
            default_rate_limit=_get_value(providers, 'default_rate_limit', 0.2),
            batch_size=_get_value(providers, 'batch_size', 100),
            prefer_batch=_get_value(providers, 'prefer_batch', True),
            provider_timeout_seconds=_get_value(providers, 'provider_timeout_seconds', 10),
            circuit_breaker_enabled=_get_value(providers, 'circuit_breaker.enabled', True),
            failure_threshold=_get_value(providers, 'circuit_breaker.failure_threshold', 3),
            failure_window_seconds=_get_value(providers, 'circuit_breaker.failure_window_seconds', 300),
            cooldown_seconds=_get_value(providers, 'circuit_breaker.cooldown_seconds', 120),
        )

    def save(self):
        """Save configuration back to config.yaml, preserving comments."""
        global _full_yaml

        # Reload to get latest (in case of manual edits)
        if _full_yaml is None:
            _load_yaml()

        # Update the providers section
        if 'providers' not in _full_yaml:
            _full_yaml['providers'] = {}

        providers = _full_yaml['providers']

        # Update lists in-place to preserve comments
        def update_list(key, new_values):
            if key in providers and hasattr(providers[key], 'clear'):
                providers[key].clear()
                providers[key].extend(new_values)
            else:
                providers[key] = new_values

        update_list('price_providers', self.price_providers)
        update_list('eps_providers', self.eps_providers)
        update_list('dividend_providers', self.dividend_providers)
        update_list('disabled_providers', self.disabled_providers)

        # Scalar values can be replaced directly
        providers['price_cache_seconds'] = self.price_cache_seconds
        providers['eps_cache_days'] = self.eps_cache_days
        providers['dividend_cache_days'] = self.dividend_cache_days
        providers['default_rate_limit'] = self.default_rate_limit
        providers['batch_size'] = self.batch_size
        providers['prefer_batch'] = self.prefer_batch
        providers['provider_timeout_seconds'] = self.provider_timeout_seconds

        # Handle circuit breaker nested structure
        if 'circuit_breaker' not in providers:
            providers['circuit_breaker'] = {}
        providers['circuit_breaker']['enabled'] = self.circuit_breaker_enabled
        providers['circuit_breaker']['failure_threshold'] = self.failure_threshold
        providers['circuit_breaker']['failure_window_seconds'] = self.failure_window_seconds
        providers['circuit_breaker']['cooldown_seconds'] = self.cooldown_seconds

        try:
            with open(CONFIG_FILE, 'w') as f:
                _yaml.dump(_full_yaml, f)
        except IOError as e:
            print(f"[ProviderConfig] Error saving config: {e}")
            raise

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    def update(self, **kwargs):
        """Update configuration values and save."""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self.save()


def get_config() -> ProviderConfig:
    """Get the current provider configuration."""
    global _config
    if _config is None:
        _config = ProviderConfig.load()
    return _config


def reload_config():
    """Force reload configuration from file."""
    global _config, _full_yaml
    _full_yaml = None
    _config = ProviderConfig.load()
    return _config


def update_config(**kwargs):
    """Update and save configuration."""
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
    """Get provider order for a specific data type."""
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
    """Set provider order for a specific data type."""
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
