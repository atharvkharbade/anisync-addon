import logging
from quart import request

from app.lib.id_resolver import bulk_resolve_to_kitsu
from app.routes.catalog.formatting import format_catalog_metas
from app.routes.catalog.sorting import get_catalog_sorting
from app.routes.utils import respond_with

from ..common import fetch_anilist_details_in_bulk
from .airing import filter_airing_candidates
from .fetcher import fetch_combined_tracker_lists
from .formatter import format_combined_page_metas
from .merger import merge_tracker_entries
from .sorter import sort_and_paginate_combined_items


async def handle_combined_catalog(user, user_id, catalog_type, catalog_id, filters, extras=""):
    """
    Main catalog handler for unified multi-tracker watchlists (comb_watching, comb_plan_to_watch, etc.).
    Aggregates and deduplicates items across MAL, AniList, and Simkl, applies sorting and filtering,
    and returns a paginated Stremio catalog response.
    """
    mal_enabled = user.get("mal_access_token") and user.get("mal_enabled", True) and not user.get("mal_token_expired")
    anilist_enabled = user.get("anilist_token") and user.get("anilist_enabled", True) and not user.get("anilist_token_expired")
    simkl_enabled = user.get("simkl_access_token") and user.get("simkl_enabled", True) and not user.get("simkl_token_expired")

    if not mal_enabled and not anilist_enabled and not simkl_enabled:
        return await respond_with({"metas": []})

    comb_status = catalog_id.split("comb_")[1]

    # Map combined status to individual tracker statuses
    mal_status = None
    al_status = None
    simkl_status = None
    if comb_status == "watching":
        mal_status = "watching"
        al_status = "CURRENT"
        simkl_status = "watching"
    elif comb_status == "plan_to_watch":
        mal_status = "plan_to_watch"
        al_status = "PLANNING"
        simkl_status = "plantowatch"
    elif comb_status == "completed":
        mal_status = "completed"
        al_status = "COMPLETED"
        simkl_status = "completed"
    elif comb_status == "paused_on_hold":
        mal_status = "on_hold"
        al_status = "PAUSED"
        simkl_status = "hold"
    elif comb_status == "dropped":
        mal_status = "dropped"
        al_status = "DROPPED"
        simkl_status = "dropped"

    metas = []
    title_lang = user.get("title_language", "english")

    try:
        offset = max(0, int(filters.get("skip", 0)))
    except (ValueError, TypeError):
        offset = 0
    try:
        limit_val = request.args.get("limit") or filters.get("limit")
        page_limit = max(1, min(100, int(limit_val))) if limit_val else 40
    except (ValueError, TypeError):
        page_limit = 40

    try:
        mal_entries, anilist_entries, simkl_entries = await fetch_combined_tracker_lists(
            user, user_id, mal_enabled, anilist_enabled, simkl_enabled, mal_status, al_status, simkl_status
        )

        combined_items = merge_tracker_entries(mal_entries, anilist_entries, simkl_entries)

        comb_map = {
            "watching": "watching",
            "plan_to_watch": "planning",
            "completed": "completed",
            "paused_on_hold": "on_hold",
            "dropped": "dropped",
        }
        category_key = comb_map.get(comb_status, "watching")

        custom_sort_enabled, sort_by, _ = get_catalog_sorting(
            user, catalog_id, category_key, url_filters=filters
        )

        bulk_details = {}
        enable_new_ep_badge = user.get("enable_new_episodes_badge", user.get("sort_by_new_episodes", True))
        sort_by_new_ep = user.get("sort_by_new_episodes", False)
        needs_bulk = ((enable_new_ep_badge or sort_by_new_ep) and comb_status in ["watching", "plan_to_watch"]) or (
            custom_sort_enabled and sort_by in ["airing_date", "score"] and comb_status in ["watching", "plan_to_watch"]
        )
        if needs_bulk:
            airing_mal_ids, airing_al_ids = filter_airing_candidates(combined_items)
            if airing_mal_ids or airing_al_ids:
                bulk_details = await fetch_anilist_details_in_bulk(airing_mal_ids, anilist_ids=airing_al_ids)

        paged_items = sort_and_paginate_combined_items(
            combined_items,
            user,
            catalog_id,
            comb_status,
            filters,
            bulk_details=bulk_details,
            offset=offset,
            page_limit=page_limit,
        )

        # Resolve Kitsu IDs in bulk
        mal_ids = [str(x["mal_id"]) for x in paged_items if x["mal_id"]]
        anilist_ids = [str(x["anilist_id"]) for x in paged_items if x["anilist_id"]]
        simkl_ids = [str(x["simkl_id"]) for x in paged_items if x["simkl_id"]]
        kitsu_mappings = await bulk_resolve_to_kitsu(
            mal_ids=mal_ids, anilist_ids=anilist_ids, simkl_ids=simkl_ids, skip_external=True
        )

        # If title language is not romaji, resolve missing titles for Simkl-exclusive items on the current page
        if title_lang != "romaji":
            missing_simkl_al_ids = []
            missing_simkl_mal_ids = []
            for p_item in paged_items:
                if p_item.get("simkl_item") and not p_item.get("anilist_item") and not p_item.get("mal_item"):
                    s_obj = p_item["simkl_item"].get("show") or p_item["simkl_item"].get("anime") or p_item["simkl_item"]
                    if title_lang == "english" and s_obj.get("en_title"):
                        continue
                    s_al_id = p_item.get("anilist_id")
                    s_mal_id = p_item.get("mal_id")
                    if s_al_id and s_al_id not in bulk_details:
                        missing_simkl_al_ids.append(s_al_id)
                    elif s_mal_id and s_mal_id not in bulk_details:
                        missing_simkl_mal_ids.append(s_mal_id)
            if missing_simkl_al_ids or missing_simkl_mal_ids:
                page_titles_bulk = await fetch_anilist_details_in_bulk(
                    missing_simkl_mal_ids, anilist_ids=missing_simkl_al_ids
                )
                bulk_details.update(page_titles_bulk)

        metas = format_combined_page_metas(
            paged_items,
            user,
            user_id,
            comb_status,
            bulk_details,
            kitsu_mappings,
        )

    except Exception:
        logging.exception("Combined watchlist catalog load failed for status %s", comb_status)

    return await respond_with(
        {"metas": format_catalog_metas(metas, user, catalog_type, catalog_id)},
        max_age=300,
        stale_while_revalidate=600,
    )
