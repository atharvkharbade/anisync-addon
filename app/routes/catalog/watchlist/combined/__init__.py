"""
Combined multi-tracker watchlist subpackage.
Handles parallel fetching, cross-tracker reconciliation, sorting, and meta formatting.
"""

from .handler import handle_combined_catalog

__all__ = ["handle_combined_catalog"]
