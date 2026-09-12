import datetime
import logging
import time
import urllib.parse
from quart import request

from config import Config
from app.api import anilist as anilist_api
from app.services.db import store_user, handle_invalid_anilist_token
from app.routes.catalog.formatting import format_catalog_metas, get_anilist_title
from app.routes.utils import respond_with
from app.routes.catalog.sorting import (
    apply_catalog_dub_filter,
    get_catalog_sorting,
    is_catalog_shuffle_enabled,
    resolve_title_lang,
    sort_watchlist_items,
    extract_item_metadata_fields,
)
from app.lib.id_resolver import bulk_resolve_to_kitsu
from app.routes.catalog.watchlist.common import get_cached_anilist_user_anime_list


async def handle_anilist_catalog(user, user_id, catalog_type, catalog_id, filters, extras=""):
    if not user.get("anilist_token") or not user.get("anilist_enabled") or user.get("anilist_token_expired"):
        return await respond_with({"metas": []})

    # Retrieve user's AniList numerical ID first
    anilist_uid = user.get("anilist_id")
    if anilist_uid:
        anilist_uid = int(anilist_uid)
    else:
        try:
            viewer = await anilist_api.get_viewer(user["anilist_token"])
            anilist_uid = int(viewer["id"])
            user["anilist_id"] = str(anilist_uid)
            store_user(user)
        except anilist_api.AnilistTokenInvalidError as e:
            logging.warning("AniList token invalid during viewer retrieval for user %s: %s", user_id, e)
            handle_invalid_anilist_token(user_id)
            return await respond_with({"metas": []})
        except Exception as e:
            logging.error("Failed to retrieve AniList viewer ID: %s", e)
            return await respond_with({"metas": []})

    anilist_status = catalog_id.split("anilist_")[1].upper()
    if anilist_status == "WATCHING":
        anilist_status = "CURRENT"

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
        # Map AniList status to watchlist category key for sorting settings
        if anilist_status in ["CURRENT", "REPEATING"]:
            category_key = "watching"
        elif anilist_status == "PLANNING":
            category_key = "planning"
        elif anilist_status == "COMPLETED":
            category_key = "completed"
        elif anilist_status == "PAUSED":
            category_key = "on_hold"
        elif anilist_status == "DROPPED":
            category_key = "dropped"
        else:
            category_key = "watching"

        custom_sort_enabled, sort_by, sort_order = get_catalog_sorting(user, catalog_id, category_key, url_filters=filters)

        collection = await get_cached_anilist_user_anime_list(
            user_id, user["anilist_token"], anilist_uid=anilist_uid, status=anilist_status
        )
        lists = collection.get("lists", [])
        entries = []
        for user_list in lists:
            entries.extend(user_list.get("entries", []))

        current_time = int(time.time())
        enable_new_ep_badge = user.get("enable_new_episodes_badge", user.get("sort_by_new_episodes", True))
        sort_by_new_ep = user.get("sort_by_new_episodes", False)

        # Compute per-entry flags (with time-gating)
        def compute_al_flags(entry):
            media = (entry.get("media") or {}) if isinstance(entry, dict) else {}
            progress = entry.get("progress", 0) or 0
            total = media.get("episodes") or 0
            next_ep = media.get("nextAiringEpisode")
            next_ep_num = next_ep.get("episode") if next_ep else None
            next_ep_airing_at = next_ep.get("airingAt") if next_ep else None

            latest_aired_at = 0
            latest_aired_num = 0

            if next_ep_num and next_ep_airing_at:
                latest_aired_num = next_ep_num - 1
                latest_aired_at = next_ep_airing_at - 604800

            status = media.get("status", "")
            recently_finished = False
            if status == "FINISHED":
                end_date = media.get("endDate")
                if total > 0 and end_date:
                    y = end_date.get("year")
                    m = end_date.get("month") or 1
                    d = end_date.get("day") or 1
                    if y:
                        try:
                            dt = datetime.datetime(y, m, d, tzinfo=datetime.timezone.utc)
                            end_ts = int(dt.timestamp())
                            if (current_time - end_ts) <= (604800 + 86400) and progress < total:
                                recently_finished = True
                                latest_aired_num = total
                                latest_aired_at = end_ts
                        except Exception:
                            pass

            # Detect newly released movie (release date in startDate / endDate)
            al_format = (media.get("format") or "").upper()
            is_movie = (al_format == "MOVIE") or (total == 1)

            if is_movie and not recently_finished and progress == 0:
                start_date = media.get("startDate") or media.get("endDate")
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

            has_unwatched = False
            if latest_aired_num > 0:
                has_unwatched = progress < latest_aired_num
            elif total > 0:
                has_unwatched = progress < total
            else:
                has_unwatched = True

            is_airing = status in ["RELEASING", "NOT_YET_RELEASED"]

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

            return is_new_ep, has_unwatched, latest_aired_at, recently_finished

        # Sorting & Shuffle
        if is_catalog_shuffle_enabled(user, catalog_id):
            import random
            entries = list(entries)
            random.shuffle(entries)
        elif custom_sort_enabled and sort_by != "default":
            entries = sort_watchlist_items(entries, sort_by, sort_order, "anilist", bulk_details=None, title_lang=resolve_title_lang(user))
        elif sort_by_new_ep and anilist_status in ["CURRENT", "PLANNING"]:

            def get_al_priority(entry):
                is_new_ep, has_unwatched, latest_aired_at, recently_finished = compute_al_flags(entry)
                media = (entry.get("media") or {}) if isinstance(entry, dict) else {}
                status = media.get("status", "")
                is_airing = status in ["RELEASING", "NOT_YET_RELEASED"]
                updated_ts = entry.get("updatedAt") or 0 if isinstance(entry, dict) else 0

                next_ep = media.get("nextAiringEpisode")
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

            entries = sorted(entries, key=get_al_priority)
        else:

            def get_al_updated_ts(entry):
                return (entry.get("updatedAt") or 0) if isinstance(entry, dict) else 0

            entries = sorted(entries, key=get_al_updated_ts, reverse=True)

        # Paginate the full sorted list
        paged_entries = entries[offset : offset + page_limit]

        # Resolve Kitsu IDs in bulk
        anilist_ids = [str(entry["media"]["id"]) for entry in paged_entries if isinstance(entry, dict) and entry.get("media") and isinstance(entry["media"], dict) and entry["media"].get("id")]
        kitsu_mappings = await bulk_resolve_to_kitsu(anilist_ids=anilist_ids, skip_external=True)

        # Build meta items
        for entry in paged_entries:
            try:
                media = (entry.get("media") or {}) if isinstance(entry, dict) else {}
                al_id = str(media.get("id") or "")
                if not al_id:
                    continue
                progress = entry.get("progress", 0) or 0

                is_new_ep = False
                if anilist_status in ["CURRENT", "PLANNING"]:
                    is_new_ep, _, _, _ = compute_al_flags(entry)

                name = get_anilist_title(media.get("title"), title_lang)

                cover_img = media.get("coverImage") or {}
                poster = (
                    cover_img.get("extraLarge")
                    or cover_img.get("large")
                    or cover_img.get("medium")
                    or ""
                )

                al_media_format = (media.get("format") or "tv").lower()
                is_movie = (al_media_format == "movie") or ((media.get("episodes") or 0) == 1)

                if is_new_ep and enable_new_ep_badge and poster:
                    encoded_url = urllib.parse.quote_plus(poster)
                    badge_style = user.get("badge_style", "modern")
                    badge_type = "movie" if is_movie else "episode"
                    poster = f"{Config.PROTOCOL}://{Config.REDIRECT_URL}/{user_id}/poster/{al_id}_a_22.jpg?url={encoded_url}&badge=new&badge_type={badge_type}&tracker=anilist&style={badge_style}&v=hd_poster_v1"

                kitsu_id = kitsu_mappings.get(f"anilist:{al_id}")
                stremio_id = f"kitsu:{kitsu_id}" if kitsu_id else f"anilist:{al_id}"

                stremio_type = "movie" if is_movie else "series"

                meta_fields = extract_item_metadata_fields(entry, "anilist", bulk_details=None)
                metas.append(
                    {
                        "id": stremio_id,
                        "type": stremio_type,
                        "name": name,
                        "poster": poster,
                        "background": media.get("bannerImage"),
                        "anilist_id": al_id,
                        "kitsu_id": kitsu_id,
                        "description": (
                            f"AniList Watchlist - {anilist_status.title()}.\n"
                            f"Progress: {progress} / {media.get('episodes') or '?'}."
                        ),
                        "score": meta_fields["score"],
                        "episodes": meta_fields["episodes"] or (media.get("episodes") or 0),
                        "year": meta_fields["year"],
                        "airing_at": meta_fields["airing_at"],
                        "updated_at": meta_fields["updated_at"],
                    }
                )
            except Exception as item_err:
                logging.warning("Skipping malformed AniList item in status %s: %s", anilist_status, item_err)
                continue
    except Exception as e:
        logging.error("AniList catalog load failed for status %s: %s", anilist_status, e)

    metas = await apply_catalog_dub_filter(metas, user, catalog_id)

    return await respond_with(
        {"metas": format_catalog_metas(metas, user, catalog_type, catalog_id)},
        max_age=300,
        stale_while_revalidate=600,
    )
