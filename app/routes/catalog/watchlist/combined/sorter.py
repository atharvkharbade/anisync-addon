import datetime
import random
import time

from app.routes.catalog.formatting import parse_iso_timestamp
from app.routes.catalog.sorting import (
    get_catalog_sorting,
    is_catalog_shuffle_enabled,
    resolve_title_lang,
    sort_watchlist_items,
)

from .airing import compute_comb_flags


def sort_and_paginate_combined_items(
    combined_items: list[dict],
    user: dict,
    catalog_id: str,
    comb_status: str,
    filters: dict,
    bulk_details: dict = None,
    offset: int = 0,
    page_limit: int = 40,
) -> list[dict]:
    """
    Sorts and paginates combined watchlist items based on custom sorting settings,
    shuffle configuration, new episode prioritization, or default updated timestamp.
    Returns: paged_items (list of combined item dicts for the current page)
    """
    comb_map = {
        "watching": "watching",
        "plan_to_watch": "planning",
        "completed": "completed",
        "paused_on_hold": "on_hold",
        "dropped": "dropped",
    }
    category_key = comb_map.get(comb_status, "watching")

    custom_sort_enabled, sort_by, sort_order = get_catalog_sorting(
        user, catalog_id, category_key, url_filters=filters
    )

    sort_by_new_ep = user.get("sort_by_new_episodes", False)
    enable_new_ep_badge = user.get("enable_new_episodes_badge", sort_by_new_ep)
    current_time = int(time.time())

    if is_catalog_shuffle_enabled(user, catalog_id):
        items_copy = list(combined_items)
        random.shuffle(items_copy)
        return items_copy[offset : offset + page_limit]

    if custom_sort_enabled and sort_by != "default":
        sorted_items = sort_watchlist_items(
            combined_items, sort_by, sort_order, "combined", bulk_details=bulk_details, title_lang=resolve_title_lang(user)
        )
        return sorted_items[offset : offset + page_limit]

    if sort_by_new_ep and comb_status in ["watching", "plan_to_watch"]:

        def get_comb_priority(item):
            is_new_ep, _, next_airing_at = compute_comb_flags(
                item,
                bulk_details=bulk_details,
                current_time=current_time,
                enable_new_ep_badge=enable_new_ep_badge,
                sort_by_new_ep=sort_by_new_ep,
            )

            # Determine combined updatedAt
            mal_updated_ts = 0
            al_updated_ts = 0
            simkl_updated_ts = 0
            if item.get("mal_item"):
                mal_node = (item["mal_item"].get("node") or {}) if isinstance(item["mal_item"], dict) else {}
                status = mal_node.get("my_list_status") or {}
                mal_updated_ts = parse_iso_timestamp(status.get("updated_at", ""))
            if item.get("anilist_item"):
                al_updated_ts = item["anilist_item"].get("updatedAt") or 0
            if item.get("simkl_item"):
                simkl_updated_ts = parse_iso_timestamp(item["simkl_item"].get("last_watched_at"))
            updated_ts = max(mal_updated_ts, al_updated_ts, simkl_updated_ts)

            # Determine airing state
            is_airing = False
            al_media = {}
            if item.get("anilist_item"):
                al_media = (item["anilist_item"].get("media") or {}) if isinstance(item["anilist_item"], dict) else {}
                al_status_str = al_media.get("status", "")
                is_airing = al_status_str in ["RELEASING", "NOT_YET_RELEASED"]
            elif item.get("mal_item"):
                mal_node = (item["mal_item"].get("node") or {}) if isinstance(item["mal_item"], dict) else {}
                mal_status_str = mal_node.get("status", "")
                is_airing = mal_status_str in ["currently_airing", "not_yet_aired"]
                if item.get("mal_id") and bulk_details:
                    al_media = bulk_details.get(item["mal_id"]) or {}
            elif item.get("simkl_item"):
                s_item = item["simkl_item"]
                show_obj = (s_item.get("show") or s_item.get("anime") or s_item) if isinstance(s_item, dict) else {}
                if not isinstance(show_obj, dict):
                    show_obj = {}
                simkl_status_str = show_obj.get("status", "")
                if item.get("mal_id") and bulk_details:
                    al_media = bulk_details.get(item["mal_id"]) or {}
                if al_media:
                    al_status_str = al_media.get("status", "")
                    is_airing = al_status_str in ["RELEASING", "NOT_YET_RELEASED"]
                else:
                    is_airing = simkl_status_str in ["airing", "currently airing"]

            progress = 0
            if item.get("mal_item"):
                mal_node = (item["mal_item"].get("node") or {}) if isinstance(item["mal_item"], dict) else {}
                progress = max(progress, (mal_node.get("my_list_status") or {}).get("num_episodes_watched", 0) or 0)
            if item.get("anilist_item"):
                progress = max(progress, item["anilist_item"].get("progress", 0) or 0)
            if item.get("simkl_item"):
                s_item = item["simkl_item"]
                simkl_progress = (
                    s_item.get("watched_episodes_count")
                    or s_item.get("episodes_watched")
                    or s_item.get("progress")
                    or 0
                )
                progress = max(progress, simkl_progress)

            recently_finished = False
            al_status_cur = al_media.get("status") if isinstance(al_media, dict) else ""
            if al_status_cur == "FINISHED":
                end_date = al_media.get("endDate") if isinstance(al_media, dict) else None
                total_eps = (al_media.get("episodes") if isinstance(al_media, dict) else None)
                if isinstance(end_date, dict) and total_eps:
                    y = end_date.get("year")
                    m = end_date.get("month") or 1
                    d = end_date.get("day") or 1
                    if y:
                        try:
                            dt = datetime.datetime(y, m, d, tzinfo=datetime.timezone.utc)
                            end_ts = int(dt.timestamp())
                            if (current_time - end_ts) <= (604800 + 86400) and progress < total_eps:
                                recently_finished = True
                        except Exception:
                            pass

            if is_new_ep:
                group_idx = 0
                secondary_sort = (-next_airing_at, -updated_ts)
            elif not is_airing and not recently_finished:
                group_idx = 1
                secondary_sort = (-updated_ts, 0)
            else:
                group_idx = 2
                secondary_sort = (next_airing_at, -updated_ts)

            return (group_idx, *secondary_sort)

        sorted_items = sorted(combined_items, key=get_comb_priority)
        return sorted_items[offset : offset + page_limit]

    def get_default_updated_ts(item):
        mal_updated_ts = 0
        al_updated_ts = 0
        simkl_updated_ts = 0
        if item.get("mal_item"):
            mal_node = (item["mal_item"].get("node") or {}) if isinstance(item["mal_item"], dict) else {}
            status = mal_node.get("my_list_status") or {}
            mal_updated_ts = parse_iso_timestamp(status.get("updated_at", ""))
        if item.get("anilist_item"):
            al_updated_ts = item["anilist_item"].get("updatedAt") or 0
        if item.get("simkl_item"):
            simkl_updated_ts = parse_iso_timestamp(item["simkl_item"].get("last_watched_at"))
        return -max(mal_updated_ts, al_updated_ts, simkl_updated_ts)

    sorted_items = sorted(combined_items, key=get_default_updated_ts)
    return sorted_items[offset : offset + page_limit]
