"""Recommendations engine facade for AniSync.

Re-exports core recommendation functions from the seeding, genres,
history, and pipeline submodules for 100% backward compatibility.
"""

from app.services.recommendations.genres import generate_genre_recommendations
from app.services.recommendations.history import fetch_user_watchlist_history
from app.services.recommendations.pipeline import _update_recommendations_cache_impl
from app.services.recommendations.seeding import get_recommendations_for_seeds, select_weighted_seeds

__all__ = [
    "select_weighted_seeds",
    "get_recommendations_for_seeds",
    "generate_genre_recommendations",
    "fetch_user_watchlist_history",
    "_update_recommendations_cache_impl",
]
