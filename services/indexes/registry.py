"""
Index Registry - Single source of truth for index definitions.

Centralizes all index metadata and coordinates with the index provider system
for fetching constituents from multiple sources with automatic fallback.
"""

from dataclasses import dataclass
from typing import List, Optional, Dict
from enum import Enum

from .providers import (
    get_index_orchestrator, IndexOrchestrator, IndexResult,
    fetch_index_tickers as _fetch_from_provider
)


# =============================================================================
# Index Definitions
# =============================================================================

@dataclass
class IndexDefinition:
    """Definition of a stock market index."""
    id: str                       # 'sp500', 'russell2000'
    name: str                     # 'S&P 500'
    short_name: str               # 'S&P 500'


# All supported indexes
INDEX_DEFINITIONS: List[IndexDefinition] = [
    IndexDefinition(id='sp500', name='S&P 500', short_name='S&P 500'),
    IndexDefinition(id='nasdaq100', name='NASDAQ 100', short_name='NASDAQ 100'),
    IndexDefinition(id='dow30', name='Dow Jones Industrial Average', short_name='DJIA'),
    IndexDefinition(id='sp600', name='S&P SmallCap 600', short_name='S&P 600'),
    IndexDefinition(id='russell2000', name='Russell 2000', short_name='Russell 2000'),
]


# =============================================================================
# Index Registry Class
# =============================================================================

class IndexRegistry:
    """
    Central registry for all stock market indexes.

    Provides access to index metadata and coordinates with the provider
    system for fetching constituents.
    """

    _indexes: Dict[str, IndexDefinition] = {}
    _initialized: bool = False

    @classmethod
    def _ensure_initialized(cls):
        """Initialize registry if not already done."""
        if not cls._initialized:
            for index in INDEX_DEFINITIONS:
                cls._indexes[index.id] = index
            cls._initialized = True

    @classmethod
    def get(cls, index_id: str) -> Optional[IndexDefinition]:
        """Get an index definition by ID."""
        cls._ensure_initialized()
        return cls._indexes.get(index_id)

    @classmethod
    def list_ids(cls) -> List[str]:
        """Get list of all index IDs (excludes 'all')."""
        cls._ensure_initialized()
        return list(cls._indexes.keys())

    @classmethod
    def list_all_ids(cls) -> List[str]:
        """Get list of all index IDs including 'all'."""
        cls._ensure_initialized()
        return ['all'] + list(cls._indexes.keys())

    @classmethod
    def get_display_names(cls) -> Dict[str, tuple]:
        """Get dict of index_id -> (name, short_name) for all indexes."""
        cls._ensure_initialized()
        result = {'all': ('All Indexes', 'All')}
        for idx_id, idx_def in cls._indexes.items():
            result[idx_id] = (idx_def.name, idx_def.short_name)
        return result

    @classmethod
    def fetch_constituents(cls, index_id: str) -> IndexResult:
        """
        Fetch current constituents from providers with automatic fallback.

        Returns IndexResult with tickers list and metadata.
        """
        return get_index_orchestrator().fetch_constituents(index_id)

    @classmethod
    def get_provider_info(cls, index_id: str) -> Dict:
        """Get info about available providers for an index."""
        return get_index_orchestrator().get_index_info(index_id)

    @classmethod
    def list_providers(cls) -> List[Dict]:
        """Get status of all index providers."""
        return get_index_orchestrator().list_providers()


# =============================================================================
# Convenience exports for backward compatibility
# =============================================================================

def get_valid_indices() -> List[str]:
    """Get list of valid index IDs including 'all'."""
    return IndexRegistry.list_all_ids()


def get_individual_indices() -> List[str]:
    """Get list of individual index IDs (excludes 'all')."""
    return IndexRegistry.list_ids()


def get_index_names() -> Dict[str, tuple]:
    """Get dict of index_id -> (name, short_name)."""
    return IndexRegistry.get_display_names()


def fetch_index_tickers(index_id: str) -> List[str]:
    """
    Fetch current tickers for an index from providers.

    Uses automatic fallback if primary source fails.
    Returns empty list on complete failure.
    """
    result = IndexRegistry.fetch_constituents(index_id)
    if result.success:
        return result.tickers
    else:
        print(f"[IndexRegistry] Failed to fetch {index_id}: {result.error}")
        return []


# Module-level constants for backward compatibility
VALID_INDICES = get_valid_indices()
INDIVIDUAL_INDICES = get_individual_indices()
INDEX_NAMES = get_index_names()
