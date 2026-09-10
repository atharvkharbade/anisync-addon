import logging

from quart import Blueprint

from app.routes.catalog.discovery import (
    DISCOVERY_CAT_IDS,
    discovery_catalogs_loop,
    handle_discovery_catalog,
    trigger_discovery_catalogs_prefetch,
    update_discovery_catalogs_cache,
)
from app.routes.catalog.formatting import (
    _parse_stremio_filters,
    background_fetch_and_cache_filler,
    currently_fetching_pages,
    currently_fetching_pairs,
    format_catalog_metas,
    get_anilist_title,
    get_jikan_semaphore,
    get_kitsu_title,
    get_mal_title,
    get_simkl_display_title,
    is_nsfw_meta,
    parse_iso_timestamp,
)
from app.routes.catalog.recommendations import handle_recommendations_catalog
from app.routes.catalog.search import handle_search_catalog
from app.routes.catalog.sorting import (
    apply_catalog_dub_filter,
    extract_item_metadata_fields,
    get_catalog_sorting,
    is_catalog_dubbed_enabled,
    is_catalog_shuffle_enabled,
    sort_watchlist_items,
)
from app.routes.catalog.watchlist import (
    fetch_anilist_details_in_bulk,
    get_cached_anilist_user_anime_list,
    get_cached_mal_user_anime_list,
    get_cached_simkl_user_anime_list,
    handle_anilist_catalog,
    handle_combined_catalog,
    handle_mal_catalog,
    handle_simkl_catalog,
)
from app.routes.utils import is_valid_user_id, rate_limit, respond_with
# CRITICAL: Expose get_user and store_user for mock patches in tests (e.g. test_dubbed_catalogs)
from app.services.db import get_user, store_user

catalog_bp = Blueprint("catalog", __name__)


@catalog_bp.route("/catalog/<string:catalog_type>/<string:catalog_id>.json")
@catalog_bp.route("/catalog/<string:catalog_type>/<string:catalog_id>/<path:extras>.json")
@rate_limit(limit=60, period_seconds=60)
async def handle_root_catalog(catalog_type: str, catalog_id: str, extras: str = ""):
    return await respond_with({"metas": []})


@catalog_bp.route("/<user_id>/catalog/<string:catalog_type>/<string:catalog_id>.json")
@catalog_bp.route("/<user_id>/catalog/<string:catalog_type>/<string:catalog_id>/<path:extras>.json")
@rate_limit(limit=60, period_seconds=60)
async def handle_catalog(user_id: str, catalog_type: str, catalog_id: str, extras: str = ""):
    if not is_valid_user_id(user_id):
        return await respond_with({"metas": []})

    if catalog_id == "anime_tracker_search":
        catalog_id = "anisync_search"

    # We handle 'anime', 'series', and 'movie' catalog types, plus custom tracker types
    allowed_types = [
        "anime",
        "series",
        "movie",
        "Watching",
        "Plan to Watch",
        "Completed",
        "On Hold",
        "Dropped",
        "Planning",
        "Paused",
        "Repeating",
    ]
    if catalog_type not in allowed_types:
        return await respond_with({"metas": []})

    user = get_user(user_id, for_manifest=True)
    if not user:
        logging.warning("Catalog request: Unknown user_id=%s", user_id)
        return await respond_with({"metas": []})

    from app.services.db import is_anilist_in_cooldown
    if is_anilist_in_cooldown(user):
        user["anilist_enabled"] = False

    filters = _parse_stremio_filters(extras)

    # 1. Discovery Catalogs
    if catalog_id in DISCOVERY_CAT_IDS:
        return await handle_discovery_catalog(user, user_id, catalog_type, catalog_id, filters, extras)

    # 2. Search Catalog
    if catalog_id == "anisync_search":
        return await handle_search_catalog(user, user_id, catalog_type, catalog_id, filters, extras)

    # 3. Recommendations Catalogs
    if catalog_id in ["anisync_rec", "anisync_loved", "anisync_liked"]:
        return await handle_recommendations_catalog(user, user_id, catalog_type, catalog_id, filters, extras)

    # 4. Combined Watchlists
    if catalog_id.startswith("comb_"):
        return await handle_combined_catalog(user, user_id, catalog_type, catalog_id, filters, extras)

    # 5. Simkl Watchlists
    if catalog_id.startswith("simkl_"):
        return await handle_simkl_catalog(user, user_id, catalog_type, catalog_id, filters, extras)

    # 6. MAL Watchlists
    if catalog_id.startswith("mal_"):
        return await handle_mal_catalog(user, user_id, catalog_type, catalog_id, filters, extras)

    # 7. AniList Watchlists
    if catalog_id.startswith("anilist_"):
        return await handle_anilist_catalog(user, user_id, catalog_type, catalog_id, filters, extras)

    return await respond_with({"metas": []})


__all__ = [
    "catalog_bp",
    "handle_root_catalog",
    "handle_catalog",
    "get_user",
    "store_user",
    "_parse_stremio_filters",
    "parse_iso_timestamp",
    "get_anilist_title",
    "get_kitsu_title",
    "get_mal_title",
    "get_simkl_display_title",
    "currently_fetching_pairs",
    "currently_fetching_pages",
    "get_jikan_semaphore",
    "background_fetch_and_cache_filler",
    "is_nsfw_meta",
    "format_catalog_metas",
    "extract_item_metadata_fields",
    "sort_watchlist_items",
    "get_catalog_sorting",
    "is_catalog_shuffle_enabled",
    "is_catalog_dubbed_enabled",
    "apply_catalog_dub_filter",
    "DISCOVERY_CAT_IDS",
    "update_discovery_catalogs_cache",
    "discovery_catalogs_loop",
    "trigger_discovery_catalogs_prefetch",
    "handle_discovery_catalog",
    "handle_search_catalog",
    "handle_recommendations_catalog",
    "get_cached_mal_user_anime_list",
    "get_cached_anilist_user_anime_list",
    "get_cached_simkl_user_anime_list",
    "fetch_anilist_details_in_bulk",
    "handle_combined_catalog",
    "handle_simkl_catalog",
    "handle_mal_catalog",
    "handle_anilist_catalog",
]
