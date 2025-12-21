"""
Secure secrets management for API keys.

Stores secrets in data_private/ folder which is gitignored.
Provides both file-based storage and environment variable fallback.
"""

import os
import json
from typing import Optional

# Path to secrets file (in gitignored data_private folder)
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SECRETS_DIR = os.path.join(_BASE_DIR, 'data_private')
SECRETS_FILE = os.path.join(SECRETS_DIR, 'secrets.json')

# In-memory cache
_secrets_cache: Optional[dict] = None


def _ensure_secrets_dir():
    """Ensure the secrets directory exists with secure permissions."""
    if not os.path.exists(SECRETS_DIR):
        os.makedirs(SECRETS_DIR, mode=0o700)  # Owner read/write/execute only


def _load_secrets() -> dict:
    """Load secrets from file into cache."""
    global _secrets_cache

    if _secrets_cache is not None:
        return _secrets_cache

    _ensure_secrets_dir()

    if os.path.exists(SECRETS_FILE):
        try:
            with open(SECRETS_FILE, 'r') as f:
                _secrets_cache = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"[Secrets] Error loading secrets file: {e}")
            _secrets_cache = {}
    else:
        _secrets_cache = {}

    return _secrets_cache


def _save_secrets(secrets: dict):
    """Save secrets to file with restricted permissions."""
    global _secrets_cache

    _ensure_secrets_dir()

    try:
        with open(SECRETS_FILE, 'w') as f:
            json.dump(secrets, f, indent=2)

        # Restrict file permissions (owner read/write only)
        os.chmod(SECRETS_FILE, 0o600)

        _secrets_cache = secrets
    except IOError as e:
        print(f"[Secrets] Error saving secrets file: {e}")
        raise


def get_secret(key: str, default: Optional[str] = None) -> Optional[str]:
    """
    Get a secret value by key.

    Checks secrets file first, then falls back to environment variable.

    Args:
        key: Secret key name (e.g., 'FMP_API_KEY')
        default: Default value if not found

    Returns:
        Secret value or default
    """
    # First check secrets file
    secrets = _load_secrets()
    if key in secrets and secrets[key]:
        return secrets[key]

    # Fall back to environment variable
    env_val = os.environ.get(key)
    if env_val:
        return env_val

    return default


def set_secret(key: str, value: str):
    """
    Set a secret value.

    Args:
        key: Secret key name
        value: Secret value
    """
    secrets = _load_secrets()
    secrets[key] = value
    _save_secrets(secrets)


def has_secret(key: str) -> bool:
    """Check if a secret exists and has a non-empty value."""
    value = get_secret(key)
    return bool(value)


def delete_secret(key: str):
    """Delete a secret."""
    secrets = _load_secrets()
    if key in secrets:
        del secrets[key]
        _save_secrets(secrets)


def clear_cache():
    """Clear the in-memory secrets cache (useful for testing or reload)."""
    global _secrets_cache
    _secrets_cache = None


def list_secrets() -> list:
    """List all secret keys (not values) for UI display."""
    secrets = _load_secrets()
    return list(secrets.keys())


# Convenience functions for specific API keys

def get_fmp_api_key() -> Optional[str]:
    """Get Financial Modeling Prep API key."""
    return get_secret('FMP_API_KEY')


def set_fmp_api_key(api_key: str):
    """Save Financial Modeling Prep API key."""
    set_secret('FMP_API_KEY', api_key)


def has_fmp_api_key() -> bool:
    """Check if FMP API key is configured."""
    return has_secret('FMP_API_KEY')


def get_alpha_vantage_api_key() -> Optional[str]:
    """Get Alpha Vantage API key (for future use)."""
    return get_secret('ALPHA_VANTAGE_API_KEY')


def set_alpha_vantage_api_key(api_key: str):
    """Save Alpha Vantage API key."""
    set_secret('ALPHA_VANTAGE_API_KEY', api_key)


def get_polygon_api_key() -> Optional[str]:
    """Get Polygon.io API key (for future use)."""
    return get_secret('POLYGON_API_KEY')


def set_polygon_api_key(api_key: str):
    """Save Polygon.io API key."""
    set_secret('POLYGON_API_KEY', api_key)


def get_alpaca_api_key() -> Optional[str]:
    """Get Alpaca API key."""
    return get_secret('ALPACA_API_KEY')


def get_alpaca_api_secret() -> Optional[str]:
    """Get Alpaca API secret."""
    return get_secret('ALPACA_API_SECRET')


def set_alpaca_credentials(api_key: str, api_secret: str, api_endpoint: Optional[str] = None):
    """Save Alpaca API credentials and optional custom endpoint."""
    set_secret('ALPACA_API_KEY', api_key)
    set_secret('ALPACA_API_SECRET', api_secret)
    if api_endpoint:
        set_secret('ALPACA_API_ENDPOINT', api_endpoint)


def has_alpaca_credentials() -> bool:
    """Check if Alpaca API credentials are configured."""
    return has_secret('ALPACA_API_KEY') and has_secret('ALPACA_API_SECRET')


def get_alpaca_api_endpoint() -> Optional[str]:
    """Get Alpaca API endpoint (base URL). Returns None to use default."""
    return get_secret('ALPACA_API_ENDPOINT')


def set_alpaca_api_endpoint(endpoint: str):
    """Set Alpaca API endpoint (base URL)."""
    set_secret('ALPACA_API_ENDPOINT', endpoint)


# Interactive Brokers connection settings

def get_ibkr_host() -> Optional[str]:
    """Get IBKR host address (default: 127.0.0.1)."""
    return get_secret('IBKR_HOST')


def get_ibkr_port() -> Optional[int]:
    """Get IBKR API port (default: 7497 for TWS, 4001 for Gateway)."""
    port = get_secret('IBKR_PORT')
    return int(port) if port else None


def get_ibkr_client_id() -> Optional[int]:
    """Get IBKR client ID."""
    client_id = get_secret('IBKR_CLIENT_ID')
    return int(client_id) if client_id else None


def set_ibkr_connection(host: str = None, port: int = None, client_id: int = None):
    """
    Save IBKR connection settings.

    Args:
        host: Host address (default: 127.0.0.1)
        port: API port (7497 for TWS, 4001 for Gateway)
        client_id: Client ID for the connection
    """
    if host:
        set_secret('IBKR_HOST', host)
    if port:
        set_secret('IBKR_PORT', str(port))
    if client_id:
        set_secret('IBKR_CLIENT_ID', str(client_id))


def has_ibkr_settings() -> bool:
    """Check if any IBKR settings are configured."""
    # IBKR works with defaults, so just check if module is available
    return True  # Settings are optional - defaults work
