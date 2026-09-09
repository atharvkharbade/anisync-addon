"""
Sorting and filtering package for AniSync catalogs and watchlists.
"""

from .dubs import apply_catalog_dub_filter
from .metadata import extract_item_metadata_fields
from .preferences import (
    get_catalog_sorting,
    is_catalog_dubbed_enabled,
    is_catalog_shuffle_enabled,
)
from .sorter import sort_watchlist_items

__all__ = [
    "apply_catalog_dub_filter",
    "extract_item_metadata_fields",
    "get_catalog_sorting",
    "is_catalog_dubbed_enabled",
    "is_catalog_shuffle_enabled",
    "sort_watchlist_items",
]
