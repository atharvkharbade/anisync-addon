import random

from quart import request

from app.routes.catalog.formatting import format_catalog_metas
from app.routes.catalog.sorting import (
    apply_catalog_dub_filter,
    get_catalog_sorting,
    is_catalog_shuffle_enabled,
    sort_watchlist_items,
)
from app.routes.utils import respond_with
from app.services.recommendations import (
    get_cached_recommendations,
    get_popular_fallbacks,
    trigger_recommendation_update_background,
)


async def handle_recommendations_catalog(user, user_id, catalog_type, catalog_id, filters, extras=""):
    if not user.get("enable_recommendations", True):
        return await respond_with({"metas": []})

    cache = get_cached_recommendations(user_id)

    # Trigger background update if cache is missing or stale
    trigger_recommendation_update_background(user_id)

    if not cache:
        # Return popular anime as temporary fallback while background generates recommendations
        fallbacks = get_popular_fallbacks()
        if catalog_id == "anisync_rec":
            metas = fallbacks[:15]
        elif catalog_id == "anisync_loved":
            metas = fallbacks[15:30]
        else:
            metas = fallbacks[30:45]
    else:
        if catalog_id == "anisync_rec":
            metas = cache.get("rec_items", [])
        elif catalog_id == "anisync_loved":
            metas = cache.get("loved_items", [])
        else:
            metas = cache.get("liked_items", [])

    # Filter to only dubbed anime if user has enabled dubbed for this catalog
    metas = await apply_catalog_dub_filter(metas, user, catalog_id)

    # Apply Custom Sorting for Recommendation Catalogs if enabled
    is_custom_sort, sort_by, sort_order = get_catalog_sorting(user, catalog_id, None, url_filters=filters)
    if is_custom_sort:
        metas = sort_watchlist_items(metas, sort_by, sort_order, tracker_type="stremio")

    # Shuffle if enabled (only when not custom sorted)
    if is_catalog_shuffle_enabled(user, catalog_id) and (not is_custom_sort or sort_by == "default"):
        metas = list(metas)
        random.shuffle(metas)

    # Handle pagination skip
    try:
        offset = max(0, int(filters.get("skip", 0)))
    except (ValueError, TypeError):
        offset = 0
    try:
        limit_val = request.args.get("limit") or filters.get("limit")
        page_limit = max(1, min(100, int(limit_val))) if limit_val else 40
    except (ValueError, TypeError):
        page_limit = 40

    metas = metas[offset : offset + page_limit]
    return await respond_with(
        {"metas": format_catalog_metas(metas, user, catalog_type, catalog_id)},
        max_age=3600,
        stale_while_revalidate=7200,
    )
