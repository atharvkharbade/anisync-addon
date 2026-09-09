"""
Recommendations pipeline package for AniSync.
Orchestrates multi-phase recommendation candidate generation, scoring, AI enhancement, and caching.
"""

from app.services.db import get_user
from app.services.recommendations.cache import get_popular_fallbacks

from .orchestrator import _update_recommendations_cache_impl

__all__ = [
    "_update_recommendations_cache_impl",
    "get_popular_fallbacks",
    "get_user",
]
