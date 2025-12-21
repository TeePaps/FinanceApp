"""
Index management module.

Provides index definitions, constituent fetching from multiple sources
with automatic fallback, and a central registry for index metadata.
"""

# Re-export from providers
from .providers import (
    IndexResult,
    IndexProvider,
    IndexOrchestrator,
    get_index_orchestrator,
    fetch_index_tickers as fetch_index_tickers_from_provider,
)

# Re-export from registry
from .registry import (
    IndexDefinition,
    IndexRegistry,
    INDEX_DEFINITIONS,
    VALID_INDICES,
    INDIVIDUAL_INDICES,
    INDEX_NAMES,
    get_valid_indices,
    get_individual_indices,
    get_index_names,
    fetch_index_tickers,
)

__all__ = [
    # Providers
    'IndexResult',
    'IndexProvider',
    'IndexOrchestrator',
    'get_index_orchestrator',
    'fetch_index_tickers_from_provider',
    # Registry
    'IndexDefinition',
    'IndexRegistry',
    'INDEX_DEFINITIONS',
    'VALID_INDICES',
    'INDIVIDUAL_INDICES',
    'INDEX_NAMES',
    'get_valid_indices',
    'get_individual_indices',
    'get_index_names',
    'fetch_index_tickers',
]
