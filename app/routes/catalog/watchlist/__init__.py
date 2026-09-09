"""Watchlist catalog subpackage for AniSync.

Handles fetching, caching, reconciling, sorting, and formatting tracker watchlists
(MAL, AniList, Simkl, and Combined) into Stremio catalog responses.
"""

from app.routes.catalog.watchlist.common import (
    fetch_anilist_details_in_bulk,
    get_cached_anilist_user_anime_list,
    get_cached_mal_user_anime_list,
    get_cached_simkl_user_anime_list,
)
from app.routes.catalog.watchlist.combined import handle_combined_catalog
from app.routes.catalog.watchlist.simkl import handle_simkl_catalog
from app.routes.catalog.watchlist.mal import handle_mal_catalog
from app.routes.catalog.watchlist.anilist import handle_anilist_catalog

__all__ = [
    "fetch_anilist_details_in_bulk",
    "get_cached_anilist_user_anime_list",
    "get_cached_mal_user_anime_list",
    "get_cached_simkl_user_anime_list",
    "handle_combined_catalog",
    "handle_simkl_catalog",
    "handle_mal_catalog",
    "handle_anilist_catalog",
]
