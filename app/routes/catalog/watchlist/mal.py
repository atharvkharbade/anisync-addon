import datetime
import logging
import time
import urllib.parse

from quart import request

from app.routes.catalog.formatting import (
    format_catalog_metas,
    get_mal_title,
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
    get_cached_mal_user_anime_list,
)


async def handle_mal_catalog(user, user_id, catalog_type, catalog_id, filters, extras=""):
    if not user.get("mal_access_token") or not user.get("mal_enabled") or user.get("mal_token_expired"):
        return await respond_with({"metas": []})

    mal_status = catalog_id.split("mal_")[1]
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
        from app.services.db import get_or_refresh_mal_token

        mal_token = await get_or_refresh_mal_token(user_id)
        if not mal_token:
            return await respond_with({"metas": []})
        data_items = await get_cached_mal_user_anime_list(user_id, mal_token, mal_status)

        current_time = int(time.time())

        # Map MAL status to watchlist category key for sorting settings
        mal_map = {
            "watching": "watching",
            "plan_to_watch": "planning",
            "completed": "completed",
            "on_hold": "on_hold",
            "dropped": "dropped",
        }
        category_key = mal_map.get(mal_status, "watching")

        custom_sort_enabled, sort_by, sort_order = get_catalog_sorting(
            user, catalog_id, category_key, url_filters=filters
        )

        # Fetch AniList next-airing-episode data in bulk ONLY for airing shows
        bulk_details = {}
        enable_new_ep_badge = user.get("enable_new_episodes_badge", user.get("sort_by_new_episodes", True))
        sort_by_new_ep = user.get("sort_by_new_episodes", False)
        needs_bulk = (mal_status in ["watching", "plan_to_watch"] and data_items) and (
            (enable_new_ep_badge or sort_by_new_ep) or (custom_sort_enabled and sort_by in ["airing_date", "score"])
        )
        if needs_bulk:
            airing_mal_ids = []
            for item in data_items:
                node = (item.get("node") or {}) if isinstance(item, dict) else {}
                nid = node.get("id")
                if not nid:
                    continue
                nstatus = node.get("status") or ""
                if nstatus in ["currently_airing", "not_yet_aired"] or not nstatus:
                    airing_mal_ids.append(str(nid))
            if airing_mal_ids:
                bulk_details = await fetch_anilist_details_in_bulk(airing_mal_ids)

        # Compute per-item: is_new_ep (with time-gating)
        def compute_mal_flags(item, mal_id):
            node = (item.get("node") or {}) if isinstance(item, dict) else {}
            status_obj = node.get("my_list_status") or {}
            progress = status_obj.get("num_episodes_watched", 0) or 0
            total = node.get("num_episodes", 0) or 0
            al_media = bulk_details.get(mal_id) or {}
            next_ep = al_media.get("nextAiringEpisode")
            next_ep_num = next_ep.get("episode") if next_ep else None
            next_ep_airing_at = next_ep.get("airingAt") if next_ep else None

            latest_aired_at = 0
            latest_aired_num = 0

            if next_ep_num and next_ep_airing_at:
                latest_aired_num = next_ep_num - 1
                latest_aired_at = next_ep_airing_at - 604800

            recently_finished = False
            al_status = al_media.get("status") if al_media else ""

            # Check AniList status
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

            # Fallback to MAL's own end_date if we couldn't determine from AniList
            if not recently_finished and node.get("status") == "finished_airing":
                mal_end_date = node.get("end_date")
                if mal_end_date and total > 0:
                    try:
                        parts = [int(p) for p in mal_end_date.split("-")]
                        if len(parts) == 3:
                            dt = datetime.datetime(parts[0], parts[1], parts[2], tzinfo=datetime.timezone.utc)
                            end_ts = int(dt.timestamp())
                            if (current_time - end_ts) <= (604800 + 86400) and progress < total:
                                recently_finished = True
                                latest_aired_num = total
                                latest_aired_at = end_ts
                    except Exception:
                        pass

            # Detect newly released movie (release date in startDate / start_date)
            al_format = al_media.get("format") if al_media else ""
            mal_media_type = (node.get("media_type") or "").lower()
            is_movie = (al_format == "MOVIE") or (mal_media_type == "movie") or (total == 1)

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

                # Check MAL start_date or end_date
                if not recently_finished:
                    mal_date_str = node.get("start_date") or node.get("end_date")
                    if mal_date_str:
                        try:
                            parts = [int(p) for p in mal_date_str.split("-")]
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

            has_unwatched = False
            if latest_aired_num > 0:
                has_unwatched = progress < latest_aired_num
            elif total > 0:
                has_unwatched = progress < total
            else:
                has_unwatched = True

            status = node.get("status", "")
            is_airing = status == "currently_airing" or status == "not_yet_aired"

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

            return is_new_ep, has_unwatched, latest_aired_at, latest_aired_num, recently_finished

        # Sorting & Shuffle
        if is_catalog_shuffle_enabled(user, catalog_id):
            import random

            data_items = list(data_items)
            random.shuffle(data_items)
            paged_data_items = data_items[offset : offset + page_limit]
        elif custom_sort_enabled and sort_by != "default":
            sorted_data_items = sort_watchlist_items(
                data_items, sort_by, sort_order, "mal", bulk_details=bulk_details
            )
            paged_data_items = sorted_data_items[offset : offset + page_limit]
        elif sort_by_new_ep and mal_status in ["watching", "plan_to_watch"]:

            def get_mal_priority(item):
                node = (item.get("node") or {}) if isinstance(item, dict) else {}
                mal_id = str(node.get("id") or "")
                if not mal_id:
                    return (3, 0, 0)
                is_new_ep, has_unwatched, latest_aired_at, _, recently_finished = compute_mal_flags(item, mal_id)

                status = node.get("status", "")
                is_airing = status == "currently_airing" or status == "not_yet_aired"
                status_obj = node.get("my_list_status") or {}
                updated_ts = parse_iso_timestamp(status_obj.get("updated_at", ""))

                # Fetch airing time for Group 2 sorting
                al_media = bulk_details.get(mal_id) or {}
                next_ep = al_media.get("nextAiringEpisode")
                airing_at = next_ep.get("airingAt") if next_ep else None
                if not airing_at:
                    if recently_finished:
                        airing_at = latest_aired_at
                    else:
                        airing_at = 2**31 - 1

                if (is_airing or recently_finished) and is_new_ep:
                    group_idx = 0
                    secondary_sort = (-airing_at, -updated_ts)
                elif not is_airing and not recently_finished:
                    group_idx = 1
                    secondary_sort = (-updated_ts, 0)
                else:
                    group_idx = 2
                    secondary_sort = (airing_at, -updated_ts)

                return (group_idx, *secondary_sort)

            sorted_data_items = sorted(data_items, key=get_mal_priority)
            paged_data_items = sorted_data_items[offset : offset + page_limit]
        else:

            def get_mal_updated_ts(item):
                node = (item.get("node") or {}) if isinstance(item, dict) else {}
                status = node.get("my_list_status") or {}
                return parse_iso_timestamp(status.get("updated_at", ""))

            sorted_data_items = sorted(data_items, key=get_mal_updated_ts, reverse=True)
            paged_data_items = sorted_data_items[offset : offset + page_limit]

        # Resolve Kitsu IDs in bulk
        from app.lib.id_resolver import bulk_resolve_to_kitsu

        mal_ids = [
            str(item["node"]["id"])
            for item in paged_data_items
            if isinstance(item, dict)
            and item.get("node")
            and isinstance(item["node"], dict)
            and item["node"].get("id")
        ]
        kitsu_mappings = await bulk_resolve_to_kitsu(mal_ids=mal_ids, skip_external=True)

        # Build meta items
        for item in paged_data_items:
            try:
                node = (item.get("node") or {}) if isinstance(item, dict) else {}
                mal_id = str(node.get("id") or "")
                if not mal_id:
                    continue
                status_obj = node.get("my_list_status") or {}
                progress = status_obj.get("num_episodes_watched", 0) or 0

                is_new_ep, _, _, _, _ = (
                    compute_mal_flags(item, mal_id)
                    if mal_status in ["watching", "plan_to_watch"]
                    else (False, False, 0, 0, False)
                )

                name = get_mal_title(node, title_lang)

                main_pic = node.get("main_picture") or {}
                poster = main_pic.get("large") or main_pic.get("medium") or ""

                al_media = bulk_details.get(mal_id) or {}
                mal_media_type = (node.get("media_type") or "tv").lower()
                is_movie = (mal_media_type == "movie") or ((al_media.get("format") if al_media else "") == "MOVIE") or ((node.get("num_episodes") or 0) == 1)

                if is_new_ep and enable_new_ep_badge and poster:
                    encoded_url = urllib.parse.quote_plus(poster)
                    badge_style = user.get("badge_style", "modern")
                    badge_type = "movie" if is_movie else "episode"
                    poster = f"{Config.PROTOCOL}://{Config.REDIRECT_URL}/{user_id}/poster/{mal_id}_m_22.jpg?url={encoded_url}&badge=new&badge_type={badge_type}&tracker=mal&style={badge_style}&v=hd_poster_v1"

                kitsu_id = kitsu_mappings.get(f"mal:{mal_id}")
                stremio_id = f"kitsu:{kitsu_id}" if kitsu_id else f"mal:{mal_id}"

                stremio_type = "movie" if is_movie else "series"

                meta_fields = extract_item_metadata_fields(item, "mal", bulk_details=bulk_details)
                metas.append(
                    {
                        "id": stremio_id,
                        "type": stremio_type,
                        "name": name,
                        "poster": poster,
                        "mal_id": mal_id,
                        "kitsu_id": kitsu_id,
                        "description": (
                            f"MAL Watchlist - {mal_status.replace('_', ' ').title()}.\n"
                            f"Progress: {progress} / {node.get('num_episodes') or '?' }."
                        ),
                        "score": meta_fields["score"],
                        "episodes": meta_fields["episodes"] or (node.get("num_episodes") or 0),
                        "year": meta_fields["year"],
                        "airing_at": meta_fields["airing_at"],
                        "updated_at": meta_fields["updated_at"],
                    }
                )
            except Exception as item_err:
                logging.warning("Skipping malformed MAL item in status %s: %s", mal_status, item_err)
                continue
    except Exception as e:
        logging.error("MAL catalog load failed for status %s: %s", mal_status, e)

    metas = await apply_catalog_dub_filter(metas, user, catalog_id)

    return await respond_with(
        {"metas": format_catalog_metas(metas, user, catalog_type, catalog_id)},
        max_age=300,
        stale_while_revalidate=600,
    )
