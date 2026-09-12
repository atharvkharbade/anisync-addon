import asyncio
import datetime
import logging
import random

from quart import request

from app.routes.catalog.formatting import format_catalog_metas
from app.routes.catalog.sorting import (
    apply_catalog_dub_filter,
    get_catalog_sorting,
    is_catalog_shuffle_enabled,
    resolve_title_lang,
    sort_watchlist_items,
)
from app.routes.utils import respond_with
from app.services.db import db

from .builder import update_discovery_catalogs_cache


async def discovery_catalogs_loop():
    """Background loop to periodically pre-fetch and update discovery catalogs cache."""
    # Wait a short bit after startup to avoid overloading AniList API during other startup tasks
    await asyncio.sleep(5)
    while True:
        try:
            logging.info("Pre-fetching discovery catalogs cache...")
            await update_discovery_catalogs_cache()
            logging.info("Discovery catalogs cache successfully updated.")
        except Exception as e:
            logging.error("Error in discovery catalogs pre-fetch loop: %s", e)
        # Sleep for 12 hours (matching 12h cache TTL) minus a 5-minute buffer
        await asyncio.sleep(12 * 3600 - 300)


_discovery_prefetch_task = None


def trigger_discovery_catalogs_prefetch():
    """Start the background discovery catalogs prefetch loop if not already running."""
    global _discovery_prefetch_task
    if _discovery_prefetch_task is None or _discovery_prefetch_task.done():
        _discovery_prefetch_task = asyncio.create_task(discovery_catalogs_loop())


async def handle_discovery_catalog(user, user_id, catalog_type, catalog_id, filters, extras=""):
    is_preview = bool(request.args.get("preview") or request.args.get("limit"))
    if not is_preview and not user.get("enable_discovery_catalogs", True if user.get("is_guest") else False):
        return await respond_with({"metas": []})

    discovery_col = db.get_collection("discovery_catalogs_cache")
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)

    genre = filters.get("genre")
    cache_key = f"{catalog_id}:{genre}" if genre else catalog_id

    cached = None
    base_cached = None
    try:
        base_cached = discovery_col.find_one({"catalog_id": catalog_id})
        if genre:
            cached = discovery_col.find_one({"catalog_id": cache_key})
            if (not cached or not cached.get("metas")) and base_cached:
                base_metas = base_cached.get("metas", [])
                if catalog_id == "anisync_schedule":
                    if genre == "Airing Today":
                        sub_metas = [m for m in base_metas if m.get("is_today")]
                    else:
                        sub_metas = [m for m in base_metas if m.get("airing_day") == genre]
                    cached = {"metas": sub_metas, "expires_at": base_cached.get("expires_at", now)}
                elif catalog_id == "anisync_seasonal":
                    if genre in ["Winter", "Spring", "Summer", "Fall"]:
                        sub_metas = [m for m in base_metas if m.get("season") == genre.upper()]
                        cached = {"metas": sub_metas or base_metas, "expires_at": base_cached.get("expires_at", now)}
                    else:
                        cached = base_cached
                else:
                    cached = base_cached
        else:
            cached = base_cached
    except Exception as e:
        logging.error("Failed to query discovery_catalogs_cache: %s", e)

    metas = []
    cached_exp = cached.get("expires_at") if cached else None
    base_exp = base_cached.get("expires_at") if base_cached else None

    if cached and cached.get("metas") and (cached_exp is None or cached_exp > now):
        metas = cached["metas"]
    elif base_cached and base_cached.get("metas"):
        # Serve parent base catalog immediately — zero latency stall for invalid/missing genres!
        metas = base_cached["metas"]
        if base_exp is not None and base_exp <= now:
            trigger_discovery_catalogs_prefetch()
    else:
        try:
            all_metas = await update_discovery_catalogs_cache()
            metas = all_metas.get(cache_key) or all_metas.get(catalog_id, [])
        except Exception as e:
            logging.error("Failed to update discovery catalogs from AniList: %s", e)
            metas = []

    # Filter to only dubbed anime if user has enabled dubbed for this catalog or globally
    metas = await apply_catalog_dub_filter(metas, user, catalog_id)

    # Apply Custom Sorting for Discovery Catalogs if enabled
    is_custom_sort, sort_by, sort_order = get_catalog_sorting(user, catalog_id, "watching", url_filters=filters)
    if is_custom_sort:
        metas = sort_watchlist_items(metas, sort_by, sort_order, tracker_type="stremio", title_lang=resolve_title_lang(user))

    # Shuffle if enabled and not explicitly custom sorted (deterministic daily seed per user/catalog for stable pagination)
    if is_catalog_shuffle_enabled(user, catalog_id) and (not is_custom_sort or sort_by == "default"):
        metas = list(metas)
        today_str = datetime.date.today().isoformat()
        seed_key = f"{user_id}_{catalog_id}_{today_str}"
        random.Random(seed_key).shuffle(metas)

    # Filter NSFW before slicing so the returned page length equals page_limit, preventing premature end-of-catalog
    hide_nsfw = user.get("hide_nsfw", True) if user else True
    if hide_nsfw:
        from app.routes.catalog.formatting import is_nsfw_meta

        metas = [m for m in metas if not is_nsfw_meta(m)]

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
