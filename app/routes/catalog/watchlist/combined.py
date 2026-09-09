import asyncio
import datetime
import logging
import time
import urllib.parse

from quart import request

from app.api import anilist as anilist_api
from app.routes.catalog.formatting import (
    format_catalog_metas,
    get_anilist_title,
    get_mal_title,
    get_simkl_display_title,
    parse_iso_timestamp,
)
from app.routes.catalog.sorting import (
    extract_item_metadata_fields,
    get_catalog_sorting,
    is_catalog_shuffle_enabled,
    sort_watchlist_items,
)
from app.routes.utils import respond_with
from app.services.db import store_user
from config import Config

from .common import (
    fetch_anilist_details_in_bulk,
    get_cached_anilist_user_anime_list,
    get_cached_mal_user_anime_list,
    get_cached_simkl_user_anime_list,
)


async def handle_combined_catalog(user, user_id, catalog_type, catalog_id, filters, extras=""):
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
        # Fetch lists in parallel
        mal_entries = []
        anilist_entries = []
        simkl_entries = []

        async def fetch_mal():
            nonlocal mal_entries
            if mal_enabled and mal_status:
                try:
                    from app.services.db import get_or_refresh_mal_token

                    mal_token = await get_or_refresh_mal_token(user_id)
                    if mal_token:
                        mal_entries = await get_cached_mal_user_anime_list(user_id, mal_token, mal_status)
                except Exception as ex:
                    logging.error("Combined: Failed to fetch MAL list for %s: %s", mal_status, ex)

        async def fetch_al():
            nonlocal anilist_entries
            if anilist_enabled and al_status:
                try:
                    anilist_uid = user.get("anilist_id")
                    if anilist_uid:
                        anilist_uid = int(anilist_uid)
                    else:
                        viewer = await anilist_api.get_viewer(user["anilist_token"])
                        anilist_uid = int(viewer["id"])
                        user["anilist_id"] = str(anilist_uid)
                        store_user(user)

                    statuses = [al_status]
                    if al_status == "CURRENT":
                        statuses.append("REPEATING")

                    for stat in statuses:
                        collection = await get_cached_anilist_user_anime_list(
                            user_id, user["anilist_token"], anilist_uid=anilist_uid, status=stat
                        )
                        lists = collection.get("lists", [])
                        for user_list in lists:
                            anilist_entries.extend(user_list.get("entries", []))
                except anilist_api.AnilistTokenInvalidError as ex:
                    logging.warning("AniList token invalid during combined list fetch for user %s: %s", user_id, ex)
                    from app.services.db import handle_invalid_anilist_token

                    handle_invalid_anilist_token(user_id)
                except Exception as ex:
                    logging.error("Combined: Failed to fetch AniList list for %s: %s", al_status, ex)

        async def fetch_simkl():
            nonlocal simkl_entries
            if simkl_enabled and simkl_status:
                try:
                    simkl_entries = await get_cached_simkl_user_anime_list(user_id, user["simkl_access_token"], simkl_status)
                except Exception as ex:
                    logging.error("Combined: Failed to fetch Simkl list for %s: %s", simkl_status, ex)

        await asyncio.gather(fetch_mal(), fetch_al(), fetch_simkl())

        # 1. Match AniList entries and MAL entries
        al_by_mal_id = {}
        al_by_al_id = {}
        for entry in anilist_entries:
            al_id = str(entry["media"]["id"])
            al_by_al_id[al_id] = entry

            id_mal = entry["media"].get("idMal")
            if id_mal:
                al_by_mal_id[str(id_mal)] = entry

        # 2. Match Simkl entries
        simkl_by_mal_id = {}
        simkl_by_al_id = {}

        def get_simkl_ids(item) -> dict:
            if "show" in item and isinstance(item["show"], dict):
                return item["show"].get("ids") or {}
            elif "anime" in item and isinstance(item["anime"], dict):
                return item["anime"].get("ids") or {}
            return item.get("ids") or {}

        for entry in simkl_entries:
            ids = get_simkl_ids(entry)
            s_mal = ids.get("mal")
            if s_mal:
                simkl_by_mal_id[str(s_mal)] = entry
            s_al = ids.get("anilist")
            if s_al:
                simkl_by_al_id[str(s_al)] = entry

        combined_items = []
        processed_mal_ids = set()
        processed_al_ids = set()
        processed_simkl_ids = set()

        for mal_item in mal_entries:
            mal_id = str(mal_item["node"]["id"])
            processed_mal_ids.add(mal_id)

            al_entry = al_by_mal_id.get(mal_id)
            al_id = None
            if al_entry:
                al_id = str(al_entry["media"]["id"])
                processed_al_ids.add(al_id)

            simkl_entry = simkl_by_mal_id.get(mal_id)
            if not simkl_entry and al_id:
                simkl_entry = simkl_by_al_id.get(al_id)

            simkl_id = None
            if simkl_entry:
                simkl_id = str(get_simkl_ids(simkl_entry).get("simkl") or "")
                if simkl_id:
                    processed_simkl_ids.add(simkl_id)

            combined_items.append(
                {
                    "mal_item": mal_item,
                    "anilist_item": al_entry,
                    "simkl_item": simkl_entry,
                    "mal_id": mal_id,
                    "anilist_id": al_id,
                    "simkl_id": simkl_id,
                }
            )

        for al_entry in anilist_entries:
            al_id = str(al_entry["media"]["id"])
            if al_id in processed_al_ids:
                continue

            processed_al_ids.add(al_id)
            mal_id = str(al_entry["media"].get("idMal") or "") or None
            if mal_id:
                processed_mal_ids.add(mal_id)

            simkl_entry = simkl_by_al_id.get(al_id)
            if not simkl_entry and mal_id:
                simkl_entry = simkl_by_mal_id.get(mal_id)

            simkl_id = None
            if simkl_entry:
                simkl_id = str(get_simkl_ids(simkl_entry).get("simkl") or "")
                if simkl_id:
                    processed_simkl_ids.add(simkl_id)

            combined_items.append(
                {
                    "mal_item": None,
                    "anilist_item": al_entry,
                    "simkl_item": simkl_entry,
                    "mal_id": mal_id,
                    "anilist_id": al_id,
                    "simkl_id": simkl_id,
                }
            )

        for simkl_item in simkl_entries:
            ids = get_simkl_ids(simkl_item)
            simkl_id = str(ids.get("simkl") or "")
            if not simkl_id or simkl_id in processed_simkl_ids:
                continue

            processed_simkl_ids.add(simkl_id)
            mal_id = str(ids.get("mal") or "") or None
            al_id = str(ids.get("anilist") or "") or None

            combined_items.append(
                {
                    "mal_item": None,
                    "anilist_item": None,
                    "simkl_item": simkl_item,
                    "mal_id": mal_id,
                    "anilist_id": al_id,
                    "simkl_id": simkl_id,
                }
            )

        current_time = int(time.time())

        # Map combined status to watchlist category key for sorting settings
        comb_map = {
            "watching": "watching",
            "plan_to_watch": "planning",
            "completed": "completed",
            "paused_on_hold": "on_hold",
            "dropped": "dropped",
        }
        category_key = comb_map.get(comb_status, "watching")

        custom_sort_enabled, sort_by, sort_order = get_catalog_sorting(user, catalog_id, category_key, url_filters=filters)

        # Bulk fetch AniList next airing details ONLY for combined items that are airing (drastically reduces query volume)
        bulk_details = {}
        enable_new_ep_badge = user.get("enable_new_episodes_badge", user.get("sort_by_new_episodes", True))
        sort_by_new_ep = user.get("sort_by_new_episodes", False)
        needs_bulk = ((enable_new_ep_badge or sort_by_new_ep) and comb_status in ["watching", "plan_to_watch"]) or (
            custom_sort_enabled and sort_by in ["airing_date", "score"] and comb_status in ["watching", "plan_to_watch"]
        )
        if needs_bulk:
            airing_mal_ids = []
            airing_al_ids = []
            for item in combined_items:
                mal_id = item.get("mal_id")
                al_id = item.get("anilist_id")
                if not mal_id and not al_id:
                    continue
                is_candidate = False
                if item.get("anilist_item"):
                    al_media = (item["anilist_item"].get("media") or {}) if isinstance(item["anilist_item"], dict) else {}
                    al_status_str = al_media.get("status", "")
                    is_candidate = al_status_str in ["RELEASING", "NOT_YET_RELEASED"] or not al_status_str
                elif item.get("mal_item"):
                    mal_node = (item["mal_item"].get("node") or {}) if isinstance(item["mal_item"], dict) else {}
                    mal_status_str = mal_node.get("status", "")
                    is_candidate = mal_status_str in ["currently_airing", "not_yet_aired"] or not mal_status_str
                elif item.get("simkl_item"):
                    s_item = item["simkl_item"] if isinstance(item.get("simkl_item"), dict) else {}
                    show_obj = s_item.get("show") or s_item.get("anime") or s_item
                    simkl_status_str = (show_obj.get("status") or "").lower()
                    is_candidate = simkl_status_str not in ["ended", "completed", "canceled", "cancelled"]
                else:
                    is_candidate = True

                if is_candidate:
                    if mal_id:
                        airing_mal_ids.append(mal_id)
                    if al_id:
                        airing_al_ids.append(al_id)

            if airing_mal_ids or airing_al_ids:
                bulk_details = await fetch_anilist_details_in_bulk(airing_mal_ids, anilist_ids=airing_al_ids)

        # Helper to compute flags for combined items
        def compute_comb_flags(item):
            is_new_ep = False
            latest_aired_at = 0
            next_airing_at = 2**31 - 1

            if not isinstance(item, dict):
                return is_new_ep, latest_aired_at, next_airing_at

            # Check status/airing state first
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

            # Extract progress
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

            # Extract total episodes
            total = 0
            if item.get("mal_item"):
                mal_node = (item["mal_item"].get("node") or {}) if isinstance(item["mal_item"], dict) else {}
                total = max(total, mal_node.get("num_episodes", 0) or 0)
            if item.get("anilist_item"):
                al_m = (item["anilist_item"].get("media") or {}) if isinstance(item["anilist_item"], dict) else {}
                total = max(total, al_m.get("episodes") or 0)
            if item.get("simkl_item"):
                s_item = item["simkl_item"]
                show_obj = (s_item.get("show") or s_item.get("anime") or s_item) if isinstance(s_item, dict) else {}
                if not isinstance(show_obj, dict):
                    show_obj = {}
                simkl_total = show_obj.get("episodes_count") or show_obj.get("num_episodes") or 0
                total = max(total, simkl_total)

            # Airing calculations using AniList data
            next_ep_num = None
            next_ep_airing_at = None

            if item.get("anilist_item"):
                al_m = (item["anilist_item"].get("media") or {}) if isinstance(item["anilist_item"], dict) else {}
                next_ep = al_m.get("nextAiringEpisode")
                if next_ep and isinstance(next_ep, dict):
                    next_ep_num = next_ep.get("episode")
                    next_ep_airing_at = next_ep.get("airingAt")
            elif item.get("mal_id"):
                if not al_media:
                    al_media = bulk_details.get(item["mal_id"]) or {}
                next_ep = al_media.get("nextAiringEpisode") if isinstance(al_media, dict) else None
                if next_ep and isinstance(next_ep, dict):
                    next_ep_num = next_ep.get("episode")
                    next_ep_airing_at = next_ep.get("airingAt")

            latest_aired_num = 0
            if next_ep_num and next_ep_airing_at:
                latest_aired_num = next_ep_num - 1
                latest_aired_at = next_ep_airing_at - 604800
                next_airing_at = next_ep_airing_at

            # Check for recently finished show
            recently_finished = False
            al_status_cur = al_media.get("status") if isinstance(al_media, dict) else ""
            if al_status_cur == "FINISHED":
                end_date = al_media.get("endDate") if isinstance(al_media, dict) else None
                total_eps = (al_media.get("episodes") if isinstance(al_media, dict) else None) or total
                if total_eps and isinstance(end_date, dict):
                    y = end_date.get("year")
                    m = end_date.get("month") or 1
                    d = end_date.get("day") or 1
                    if y:
                        try:
                            dt = datetime.datetime(y, m, d, tzinfo=datetime.timezone.utc)
                            end_ts = int(dt.timestamp())
                            if (current_time - end_ts) <= (604800 + 86400) and progress < total_eps:
                                recently_finished = True
                                latest_aired_num = total_eps
                                latest_aired_at = end_ts
                        except Exception:
                            pass

            # Fallback to MAL's own end_date if we couldn't determine from AniList
            if not recently_finished and item.get("mal_item"):
                mal_node = (item["mal_item"].get("node") or {}) if isinstance(item["mal_item"], dict) else {}
                if mal_node.get("status") == "finished_airing":
                    mal_end_date = mal_node.get("end_date")
                    mal_total = mal_node.get("num_episodes", 0) or total
                    if mal_end_date and mal_total > 0:
                        try:
                            parts = [int(p) for p in mal_end_date.split("-")]
                            if len(parts) == 3:
                                dt = datetime.datetime(parts[0], parts[1], parts[2], tzinfo=datetime.timezone.utc)
                                end_ts = int(dt.timestamp())
                                if (current_time - end_ts) <= (604800 + 86400) and progress < mal_total:
                                    recently_finished = True
                                    latest_aired_num = mal_total
                                    latest_aired_at = end_ts
                        except Exception:
                            pass

            if (
                (is_airing or recently_finished)
                and (enable_new_ep_badge or sort_by_new_ep)
                and latest_aired_num > 0
                and progress < latest_aired_num
            ):
                time_since_air = current_time - latest_aired_at
                if time_since_air <= 604800 or recently_finished:
                    is_new_ep = True

            return is_new_ep, latest_aired_at, next_airing_at

        # Sorting & Shuffle
        if is_catalog_shuffle_enabled(user, catalog_id):
            import random

            combined_items = list(combined_items)
            random.shuffle(combined_items)
            paged_items = combined_items[offset : offset + page_limit]
        elif custom_sort_enabled and sort_by != "default":
            sorted_items = sort_watchlist_items(
                combined_items, sort_by, sort_order, "combined", bulk_details=bulk_details
            )
            paged_items = sorted_items[offset : offset + page_limit]
        elif sort_by_new_ep and comb_status in ["watching", "plan_to_watch"]:

            def get_comb_priority(item):
                is_new_ep, _, next_airing_at = compute_comb_flags(item)

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
            paged_items = sorted_items[offset : offset + page_limit]
        else:

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
            paged_items = sorted_items[offset : offset + page_limit]

        # Resolve Kitsu IDs in bulk
        from app.lib.id_resolver import bulk_resolve_to_kitsu

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

        # Build meta items
        for item in paged_items:
            try:
                name = "Unknown"
                poster = ""
                is_movie = False
                total_eps = "?"
                progress = 0

                if item.get("anilist_item"):
                    al_media = item["anilist_item"]["media"]
                    name = get_anilist_title(al_media.get("title"), title_lang)
                    poster = (al_media.get("coverImage") or {}).get("large") or ""
                    is_movie = al_media.get("format") == "MOVIE"
                    total_eps = al_media.get("episodes") or total_eps
                    progress = item["anilist_item"].get("progress", 0)

                if (name == "Unknown" or not poster) and item.get("mal_item"):
                    mal_node = item["mal_item"]["node"]
                    if name == "Unknown":
                        name = get_mal_title(mal_node, title_lang)
                    if not poster:
                        main_pic = mal_node.get("main_picture") or {}
                        poster = main_pic.get("large") or main_pic.get("medium") or ""
                    mal_type = (mal_node.get("media_type") or "").lower()
                    if mal_type == "movie":
                        is_movie = True
                    if total_eps == "?":
                        total_eps = mal_node.get("num_episodes") or total_eps
                    if progress == 0:
                        progress = (mal_node.get("my_list_status") or {}).get("num_episodes_watched", 0)

                if (name == "Unknown" or not poster) and item.get("simkl_item"):
                    s_item = item["simkl_item"]
                    show_obj = (s_item.get("show") or s_item.get("anime") or s_item) if isinstance(s_item, dict) else {}
                    if not isinstance(show_obj, dict):
                        show_obj = {}
                    if name == "Unknown":
                        name = get_simkl_display_title(show_obj, title_lang, bulk_details=bulk_details)
                    if not poster:
                        p = show_obj.get("poster") or show_obj.get("poster_image") or ""
                        if p and not p.startswith("http"):
                            p = f"https://simkl.in/posters/{p}_m.jpg"
                        poster = p
                    simkl_type = (show_obj.get("anime_type") or show_obj.get("type") or "").lower()
                    if simkl_type == "movie":
                        is_movie = True
                    if total_eps == "?":
                        total_eps = (
                            show_obj.get("episodes_count")
                            or show_obj.get("num_episodes")
                            or s_item.get("total_episodes_count")
                            or total_eps
                        )
                    if progress == 0:
                        progress = (
                            s_item.get("watched_episodes_count")
                            or s_item.get("episodes_watched")
                            or s_item.get("progress")
                            or 0
                        )

                is_new_ep = False
                if comb_status in ["watching", "plan_to_watch"]:
                    is_new_ep, _, _ = compute_comb_flags(item)

                if is_new_ep and enable_new_ep_badge and poster:
                    badge_id = item.get("mal_id") or item.get("anilist_id") or item.get("simkl_id") or "new"
                    badge_tracker = "mal" if item.get("mal_id") else ("anilist" if item.get("anilist_id") else "simkl")
                    encoded_url = urllib.parse.quote_plus(poster)
                    badge_style = user.get("badge_style", "modern")
                    poster = f"{Config.PROTOCOL}://{Config.REDIRECT_URL}/{user_id}/poster/comb_{badge_id}_c_22.jpg?url={encoded_url}&badge=new&tracker={badge_tracker}&style={badge_style}&v=hd_poster_v1"

                mal_id = item.get("mal_id")
                anilist_id = item.get("anilist_id")
                simkl_id = item.get("simkl_id")
                kitsu_id = None
                if mal_id:
                    kitsu_id = kitsu_mappings.get(f"mal:{mal_id}")
                if not kitsu_id and anilist_id:
                    kitsu_id = kitsu_mappings.get(f"anilist:{anilist_id}")
                if not kitsu_id and simkl_id:
                    kitsu_id = kitsu_mappings.get(f"simkl:{simkl_id}")

                stremio_id = (
                    f"kitsu:{kitsu_id}"
                    if kitsu_id
                    else (
                        f"mal:{mal_id}" if mal_id else (f"anilist:{anilist_id}" if anilist_id else f"simkl:{simkl_id}")
                    )
                )
                stremio_type = "movie" if is_movie else "series"

                simkl_fanart = None
                if item.get("simkl_item"):
                    s_item = item["simkl_item"]
                    show_obj = (s_item.get("show") or s_item.get("anime") or s_item) if isinstance(s_item, dict) else {}
                    if isinstance(show_obj, dict):
                        sf = show_obj.get("fanart")
                        if sf:
                            simkl_fanart = sf if sf.startswith("http") else f"https://simkl.in/fanart/{sf}_medium.jpg"

                al_banner = None
                if item.get("anilist_item"):
                    al_media = (item["anilist_item"].get("media") or {}) if isinstance(item["anilist_item"], dict) else {}
                    al_banner = al_media.get("bannerImage")
                elif item.get("mal_id") and bulk_details:
                    al_banner = (bulk_details.get(item["mal_id"]) or {}).get("bannerImage")
                elif item.get("anilist_id") and bulk_details:
                    al_banner = (bulk_details.get(item["anilist_id"]) or {}).get("bannerImage")

                meta_fields = extract_item_metadata_fields(item, "combined", bulk_details=bulk_details)
                metas.append(
                    {
                        "id": stremio_id,
                        "type": stremio_type,
                        "name": name,
                        "poster": poster,
                        "background": simkl_fanart or al_banner,
                        "kitsu_id": kitsu_id,
                        "mal_id": mal_id,
                        "anilist_id": anilist_id,
                        "simkl_id": simkl_id,
                        "description": (
                            f"Watchlist - {comb_status.replace('_', ' ').title()}.\n"
                            f"Progress: {progress} / {total_eps}."
                        ),
                        "score": meta_fields["score"],
                        "episodes": meta_fields["episodes"] or total_eps,
                        "year": meta_fields["year"],
                        "airing_at": meta_fields["airing_at"],
                        "updated_at": meta_fields["updated_at"],
                    }
                )
            except Exception as item_err:
                logging.warning("Skipping malformed combined item in status %s: %s", comb_status, item_err)
                continue
    except Exception:
        logging.exception("Combined watchlist catalog load failed for status %s", comb_status)

    return await respond_with(
        {"metas": format_catalog_metas(metas, user, catalog_type, catalog_id)},
        max_age=300,
        stale_while_revalidate=600,
    )
