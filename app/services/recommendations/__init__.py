"""
Recommendations Subpackage for AniSync.
Provides personalized anime recommendations based on tracker watchlists (MAL, AniList, Simkl),
heuristic seed weighting, community suggestions, Gemini AI enhancement, and MongoDB caching.
"""

from app.services.recommendations.ai_enhancer import enhance_recommendations_with_gemini
from app.services.recommendations.cache import (
    POPULAR_FALLBACKS,
    currently_updating_users,
    get_cached_recommendations,
    get_popular_fallbacks,
    popular_fallbacks_collection,
    popular_fallbacks_loop,
    recommendations_cache_collection,
    trigger_popular_fallbacks_update_background,
    trigger_recommendation_update_background,
    update_popular_fallbacks_cache,
    update_recommendations_cache,
)
from app.services.recommendations.engine import (
    _update_recommendations_cache_impl,
    fetch_user_watchlist_history,
    generate_genre_recommendations,
    get_recommendations_for_seeds,
    select_weighted_seeds,
)
from app.services.recommendations.fetchers import (
    get_anilist_recommendations_bulk,
    get_mal_recommendations_for_id,
    get_top_anime_by_genre,
)
from app.services.recommendations.utils import clean_html, is_proper_anime, normalize_user_status

__all__ = [
    "clean_html",
    "is_proper_anime",
    "normalize_user_status",
    "get_mal_recommendations_for_id",
    "get_anilist_recommendations_bulk",
    "get_top_anime_by_genre",
    "enhance_recommendations_with_gemini",
    "select_weighted_seeds",
    "get_recommendations_for_seeds",
    "generate_genre_recommendations",
    "fetch_user_watchlist_history",
    "_update_recommendations_cache_impl",
    "update_recommendations_cache",
    "get_cached_recommendations",
    "trigger_recommendation_update_background",
    "get_popular_fallbacks",
    "update_popular_fallbacks_cache",
    "popular_fallbacks_loop",
    "trigger_popular_fallbacks_update_background",
    "POPULAR_FALLBACKS",
    "currently_updating_users",
    "recommendations_cache_collection",
    "popular_fallbacks_collection",
]
