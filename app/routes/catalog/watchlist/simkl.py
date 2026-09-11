import datetime
import logging
import time
import urllib.parse

from quart import request

from app.routes.catalog.formatting import (
    format_catalog_metas,
    get_simkl_display_title,
    parse_iso_timestamp,
)
from app.routes.catalog.sorting import (
    apply_catalog_dub_filter,
    extract_item_metadata_fields,
    get_catalog_sorting,
    is_catalog_shuffle_enabled,
    sort_watchlist_items,
)
from app.routes.utils import respond_with
from config import Config

from .common import (
    fetch_anilist_details_in_bulk,
    get_cached_simkl_user_anime_list,
)


async def handle_simkl_catalog(user, user_id, catalog_type, catalog_id, filters, extras=""):
    if not user.get("simkl_access_token") or not user.get("simkl_enabled", True) or user.get("simkl_token_expired"):
        return await respond_with({"metas": []})

    simkl_status = catalog_id.split("simkl_")[1]
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
        data_items = await get_cached_simkl_user_anime_list(user_id, user["simkl_access_token"], simkl_status)

        current_time = int(time.time())

        # Map Simkl status to watchlist category key for sorting settings
        simkl_map = {
            "watching": "watching",
            "plantowatch": "planning",
            "completed": "completed",
            "hold": "on_hold",
            "dropped": "dropped",
        }
        category_key = simkl_map.get(simkl_status, "watching")

        custom_sort_enabled, sort_by, sort_order = get_catalog_sorting(
            user, catalog_id, category_key, url_filters=filters
        )

        # Fetch AniList next-airing-episode data in bulk ONLY for airing shows
        bulk_details = {}
        enable_new_ep_badge = user.get("enable_new_episodes_badge", user.get("sort_by_new_episodes", True))
        sort_by_new_ep = user.get("sort_by_new_episodes", False)
        needs_bulk = (simkl_status in ["watching", "plantowatch"] and data_items) and (
            (enable_new_ep_badge or sort_by_new_ep) or (custom_sort_enabled and sort_by in ["airing_date", "score"])
        )
        if needs_bulk:
            airing_mal_ids = []
            airing_al_ids = []
            for item in data_items:
                show_obj = item.get("show") or item.get("anime") or item
                simkl_status_str = (show_obj.get("status") or "").lower()
                if (
                    simkl_status_str not in ["ended", "completed", "canceled", "cancelled"]
                    or item.get("not_aired_episodes_count", 0) > 0
                    or not simkl_status_str
                ):
                    ids = show_obj.get("ids") or {}
                    mal_id = str(ids.get("mal") or "")
                    al_id = str(ids.get("anilist") or "")
                    if mal_id:
                        airing_mal_ids.append(mal_id)
                    if al_id:
                        airing_al_ids.append(al_id)
            if airing_mal_ids or airing_al_ids:
                bulk_details = await fetch_anilist_details_in_bulk(airing_mal_ids, anilist_ids=airing_al_ids)

        def compute_simkl_flags(item, mal_id, al_id=None):
            show_obj = item.get("show") or item.get("anime") or item
            progress = item.get("watched_episodes_count") or item.get("episodes_watched") or item.get("progress") or 0
            total = (
                show_obj.get("episodes_count")
                or show_obj.get("num_episodes")
                or item.get("total_episodes_count")
                or 0
            )

            al_media = {}
            if mal_id and mal_id in bulk_details:
                al_media = bulk_details[mal_id]
            elif al_id and al_id in bulk_details:
                al_media = bulk_details[al_id]
            next_ep = al_media.get("nextAiringEpisode")
            next_ep_num = next_ep.get("episode") if next_ep else None
            next_ep_airing_at = next_ep.get("airingAt") if next_ep else None

            latest_aired_at = 0
            latest_aired_num = 0
            if next_ep_num and next_ep_airing_at:
                latest_aired_num = next_ep_num - 1
                latest_aired_at = next_ep_airing_at - 604800
            elif total > 0:
                latest_aired_num = total

            has_unwatched = False
            if latest_aired_num > 0:
                has_unwatched = progress < latest_aired_num
            else:
                has_unwatched = True

            is_airing = False
            if al_media:
                al_status = al_media.get("status", "")
                is_airing = al_status in ["RELEASING", "NOT_YET_RELEASED"]
            else:
                is_airing = item.get("not_aired_episodes_count", 0) > 0

            # Check for recently finished show
            recently_finished = False
            al_status = al_media.get("status") if al_media else ""
            if al_status == "FINISHED":
                end_date = al_media.get("endDate")
                total_eps = al_media.get("episodes") or total
                if total_eps and end_date:
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

            # Detect newly released movie (release date in startDate / release_date)
            al_format = al_media.get("format") if al_media else ""
            simkl_type = (show_obj.get("anime_type") or show_obj.get("type") or "").lower()
            is_movie = (al_format == "MOVIE") or (simkl_type == "movie") or (total == 1)

            if is_movie and not recently_finished and progress == 0:
                # Check AniList startDate or endDate
                start_date = al_media.get("startDate") if al_media else None
                if isinstance(start_date, dict):
                    y = start_date.get("year")
                    m = start_date.get("month") or 1
                    d = start_date.get("day") or 1
                    if y:
                        try:
                            dt = datetime.datetime(y, m, d, tzinfo=datetime.timezone.utc)
                            rel_ts = int(dt.timestamp())
                            if 0 <= (current_time - rel_ts) <= (604800 + 86400):
                                recently_finished = True
                                latest_aired_num = 1
                                latest_aired_at = rel_ts
                        except Exception:
                            pass

                # Check Simkl release_date
                if not recently_finished:
                    simkl_rel = show_obj.get("release_date")
                    if simkl_rel:
                        try:
                            parts = [int(p) for p in simkl_rel.split("-")]
                            if len(parts) >= 1:
                                y = parts[0]
                                m = parts[1] if len(parts) > 1 else 1
                                d = parts[2] if len(parts) > 2 else 1
                                dt = datetime.datetime(y, m, d, tzinfo=datetime.timezone.utc)
                                rel_ts = int(dt.timestamp())
                                if 0 <= (current_time - rel_ts) <= (604800 + 86400):
                                    recently_finished = True
                                    latest_aired_num = 1
                                    latest_aired_at = rel_ts
                        except Exception:
                            pass

            is_new_ep = False
            if (
                (is_airing or recently_finished)
                and (enable_new_ep_badge or sort_by_new_ep)
                and latest_aired_num > 0
                and progress < latest_aired_num
            ):
                time_since_air = current_time - latest_aired_at
                if time_since_air <= 604800 or recently_finished:
                    is_new_ep = True

            return is_new_ep, has_unwatched, latest_aired_at, latest_aired_num

        if is_catalog_shuffle_enabled(user, catalog_id):
            import random

            data_items = list(data_items)
            random.shuffle(data_items)
            paged_data_items = data_items[offset : offset + page_limit]
        elif custom_sort_enabled and sort_by != "default":
            sorted_data_items = sort_watchlist_items(
                data_items, sort_by, sort_order, "simkl", bulk_details=bulk_details
            )
            paged_data_items = sorted_data_items[offset : offset + page_limit]
        elif sort_by_new_ep and simkl_status in ["watching", "plantowatch"]:

            def get_simkl_priority(item):
                show_obj = item.get("show") or item.get("anime") or item
                ids = show_obj.get("ids") or {}
                mal_id = str(ids.get("mal") or "") or None
                al_id = str(ids.get("anilist") or "") or None

                is_new_ep, has_unwatched, latest_aired_at, _ = compute_simkl_flags(item, mal_id, al_id=al_id)

                al_media = (
                    (bulk_details.get(mal_id) if mal_id else None)
                    or (bulk_details.get(al_id) if al_id else None)
                    or {}
                )
                is_airing = False
                if al_media:
                    al_status = al_media.get("status", "")
                    is_airing = al_status in ["RELEASING", "NOT_YET_RELEASED"]
                else:
                    is_airing = item.get("not_aired_episodes_count", 0) > 0

                progress = item.get("watched_episodes_count") or item.get("episodes_watched") or item.get("progress") or 0
                total_eps = (
                    show_obj.get("episodes_count")
                    or show_obj.get("num_episodes")
                    or item.get("total_episodes_count")
                    or 0
                )

                recently_finished = False
                al_status = al_media.get("status") if al_media else ""
                if al_status == "FINISHED":
                    end_date = al_media.get("endDate")
                    if end_date and total_eps:
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

                next_ep = al_media.get("nextAiringEpisode")
                airing_at = next_ep.get("airingAt") if next_ep else None
                if not airing_at:
                    if recently_finished or latest_aired_at:
                        airing_at = latest_aired_at
                    else:
                        airing_at = 2**31 - 1

                updated_ts = parse_iso_timestamp(item.get("last_watched_at"))

                if is_new_ep:
                    group_idx = 0
                    secondary_sort = (-airing_at, -updated_ts)
                elif not is_airing and not recently_finished:
                    group_idx = 1
                    secondary_sort = (-updated_ts, 0)
                else:
                    group_idx = 2
                    secondary_sort = (airing_at, -updated_ts)

                return (group_idx, *secondary_sort)

            sorted_data_items = sorted(data_items, key=get_simkl_priority)
            paged_data_items = sorted_data_items[offset : offset + page_limit]
        else:

            def get_simkl_updated_ts(item):
                return parse_iso_timestamp(item.get("last_watched_at"))

            sorted_data_items = sorted(data_items, key=get_simkl_updated_ts, reverse=True)
            paged_data_items = sorted_data_items[offset : offset + page_limit]

        # Resolve Kitsu IDs in bulk
        from app.lib.id_resolver import bulk_resolve_to_kitsu

        simkl_ids = []
        for item in paged_data_items:
            show_obj = item.get("show") or item.get("anime") or item
            show_ids = show_obj.get("ids") or {}
            simkl_id = str(show_ids.get("simkl") or "")
            if simkl_id:
                simkl_ids.append(simkl_id)
        kitsu_mappings = await bulk_resolve_to_kitsu(simkl_ids=simkl_ids, skip_external=True)

        # If title language is not romaji, resolve missing titles for items on the current page in bulk
        if title_lang != "romaji":
            missing_simkl_al_ids = []
            missing_simkl_mal_ids = []
            for item in paged_data_items:
                show_obj = item.get("show") or item.get("anime") or item
                if title_lang == "english" and show_obj.get("en_title"):
                    continue
                ids = show_obj.get("ids") or {}
                al_id = str(ids.get("anilist") or "")
                mal_id = str(ids.get("mal") or "")
                if al_id and al_id not in bulk_details:
                    missing_simkl_al_ids.append(al_id)
                elif mal_id and mal_id not in bulk_details:
                    missing_simkl_mal_ids.append(mal_id)
            if missing_simkl_al_ids or missing_simkl_mal_ids:
                page_titles_bulk = await fetch_anilist_details_in_bulk(
                    missing_simkl_mal_ids, anilist_ids=missing_simkl_al_ids
                )
                bulk_details.update(page_titles_bulk)

        # Build meta items
        for item in paged_data_items:
            try:
                if "show" in item and isinstance(item["show"], dict):
                    show_obj = item["show"]
                elif "anime" in item and isinstance(item["anime"], dict):
                    show_obj = item["anime"]
                else:
                    show_obj = item

                show_ids = show_obj.get("ids") or {}
                simkl_id = str(show_ids.get("simkl") or "")
                mal_id = str(show_ids.get("mal") or "") or None
                al_id = str(show_ids.get("anilist") or "") or None

                progress = (
                    item.get("watched_episodes_count") or item.get("episodes_watched") or item.get("progress") or 0
                )
                total_eps = (
                    show_obj.get("episodes_count")
                    or show_obj.get("num_episodes")
                    or item.get("total_episodes_count")
                    or "?"
                )
                name = get_simkl_display_title(show_obj, title_lang, bulk_details=bulk_details)
                poster = show_obj.get("poster") or show_obj.get("poster_image") or ""
                if poster and not poster.startswith("http"):
                    poster = f"https://simkl.in/posters/{poster}_m.jpg"

                is_new_ep = False
                if simkl_status in ["watching", "plantowatch"]:
                    is_new_ep, _, _, _ = compute_simkl_flags(item, mal_id, al_id=al_id)

                al_media = (bulk_details.get(mal_id) if mal_id else None) or (bulk_details.get(al_id) if al_id else None) or {}
                simkl_media_type = (show_obj.get("anime_type") or show_obj.get("type") or "series").lower()
                al_format = al_media.get("format") if al_media else ""
                is_movie = (simkl_media_type == "movie") or (al_format == "MOVIE") or (total_eps == 1)

                if is_new_ep and enable_new_ep_badge and poster:
                    encoded_url = urllib.parse.quote_plus(poster)
                    badge_style = user.get("badge_style", "modern")
                    badge_type = "movie" if is_movie else "episode"
                    poster = f"{Config.PROTOCOL}://{Config.REDIRECT_URL}/{user_id}/poster/simkl_{simkl_id}_s_22.jpg?url={encoded_url}&badge=new&badge_type={badge_type}&tracker=simkl&style={badge_style}&v=hd_poster_v1"

                kitsu_id = kitsu_mappings.get(f"simkl:{simkl_id}")
                stremio_id = f"kitsu:{kitsu_id}" if kitsu_id else f"simkl:{simkl_id}"

                stremio_type = "movie" if is_movie else "series"

                simkl_status_titles = {
                    "watching": "Watching",
                    "plantowatch": "Plan to Watch",
                    "completed": "Completed",
                    "hold": "On Hold",
                    "dropped": "Dropped",
                }
                disp_simkl_status = simkl_status_titles.get(simkl_status, simkl_status.replace("_", " ").title())

                sf = show_obj.get("fanart")
                simkl_fanart = (
                    (sf if sf.startswith("http") else f"https://simkl.in/fanart/{sf}_medium.jpg") if sf else None
                )

                meta_fields = extract_item_metadata_fields(item, "simkl", bulk_details=bulk_details)
                metas.append(
                    {
                        "id": stremio_id,
                        "type": stremio_type,
                        "name": name,
                        "poster": poster,
                        "background": simkl_fanart,
                        "simkl_id": simkl_id,
                        "mal_id": mal_id,
                        "anilist_id": al_id,
                        "kitsu_id": kitsu_id,
                        "description": (
                            f"Simkl Watchlist - {disp_simkl_status}.\nProgress: {progress} / {total_eps}."
                        ),
                        "score": meta_fields["score"],
                        "episodes": meta_fields["episodes"]
                        or (
                            int(total_eps)
                            if isinstance(total_eps, int)
                            or (isinstance(total_eps, str) and total_eps.isdigit())
                            else 0
                        ),
                        "year": meta_fields["year"],
                        "airing_at": meta_fields["airing_at"],
                        "updated_at": meta_fields["updated_at"],
                    }
                )
            except Exception as item_err:
                logging.warning("Skipping malformed simkl item in status %s: %s", simkl_status, item_err)
                continue
    except Exception as e:
        logging.error("Simkl catalog load failed for status %s: %s", simkl_status, e)

    metas = await apply_catalog_dub_filter(metas, user, catalog_id)

    return await respond_with(
        {"metas": format_catalog_metas(metas, user, catalog_type, catalog_id)},
        max_age=300,
        stale_while_revalidate=600,
    )
