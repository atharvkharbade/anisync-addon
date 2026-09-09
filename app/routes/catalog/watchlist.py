import asyncio
import datetime
import logging
import time
import urllib.parse

from quart import request

from app.api import anilist as anilist_api
from app.api import mal as mal_api
from app.api import simkl as simkl_api
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


async def get_cached_mal_user_anime_list(user_id: str, token: str, status: str) -> list:
    from app.services.db import db

    now = datetime.datetime.utcnow()
    cache_col = db.get_collection("user_watchlist_cache")
    try:
        cached = cache_col.find_one({"uid": user_id, "tracker": "mal", "status": status})
        if cached and cached.get("fetched_at") and (now - cached.get("fetched_at")) < datetime.timedelta(minutes=15):
            return cached["data"]
    except Exception as e:
        logging.error("Failed to query user_watchlist_cache (MAL): %s", e)
        cached = None

    try:
        res = await mal_api.get_user_anime_list(token, status=status, limit=500, offset=0)
        from app.services.db import reset_mal_error_counter
        reset_mal_error_counter(user_id)
        data_items = res.get("data", [])
        try:
            cache_col.update_one(
                {"uid": user_id, "tracker": "mal", "status": status},
                {
                    "$set": {
                        "uid": user_id,
                        "tracker": "mal",
                        "status": status,
                        "data": data_items,
                        "fetched_at": now,
                        "expires_at": now + datetime.timedelta(days=7),
                    }
                },
                upsert=True,
            )
        except Exception as e:
            logging.error("Failed to write user_watchlist_cache (MAL): %s", e)
        return data_items
    except Exception as e:
        from app.api.mal import MalTokenInvalidError
        if isinstance(e, MalTokenInvalidError):
            logging.warning("MAL token invalid during get_cached_mal_user_anime_list for user %s: %s", user_id, e)
            from app.services.db import handle_invalid_mal_token
            handle_invalid_mal_token(user_id)
        if cached:
            logging.warning("MAL API failed, returning expired cache for user %s: %s", user_id, e)
            return cached["data"]
        raise e


async def get_cached_anilist_user_anime_list(user_id: str, token: str, anilist_uid: int, status: str) -> dict:
    from app.services.db import db

    now = datetime.datetime.utcnow()
    cache_col = db.get_collection("user_watchlist_cache")
    try:
        cached = cache_col.find_one({"uid": user_id, "tracker": "anilist", "status": status})
        if cached and cached.get("fetched_at") and (now - cached.get("fetched_at")) < datetime.timedelta(minutes=15):
            return cached["data"]
    except Exception as e:
        logging.error("Failed to query user_watchlist_cache (AniList): %s", e)
        cached = None

    try:
        collection = await anilist_api.get_user_anime_list(token, user_id=anilist_uid, status=status)
        try:
            cache_col.update_one(
                {"uid": user_id, "tracker": "anilist", "status": status},
                {
                    "$set": {
                        "uid": user_id,
                        "tracker": "anilist",
                        "status": status,
                        "data": collection,
                        "fetched_at": now,
                        "expires_at": now + datetime.timedelta(days=7),
                    }
                },
                upsert=True,
            )
        except Exception as e:
            logging.error("Failed to write user_watchlist_cache (AniList): %s", e)
        return collection
    except anilist_api.AnilistTokenInvalidError as e:
        from app.services.db import handle_invalid_anilist_token
        handle_invalid_anilist_token(user_id)
        raise e
    except Exception as e:
        if cached:
            logging.warning("AniList API failed, returning expired cache for user %s: %s", user_id, e)
            return cached["data"]
        raise e


async def get_cached_simkl_user_anime_list(user_id: str, token: str, status: str) -> list:
    from app.services.db import db

    now = datetime.datetime.utcnow()
    cache_col = db.get_collection("user_watchlist_cache")
    try:
        cached = cache_col.find_one({"uid": user_id, "tracker": "simkl", "status": status})
        if cached and cached.get("fetched_at") and (now - cached.get("fetched_at")) < datetime.timedelta(minutes=15):
            return cached["data"]
    except Exception as e:
        logging.error("Failed to query user_watchlist_cache (Simkl): %s", e)
        cached = None

    try:
        collection = await simkl_api.get_user_anime_list(token, status=status)
        from app.services.db import reset_simkl_error_counter
        reset_simkl_error_counter(user_id)
        try:
            cache_col.update_one(
                {"uid": user_id, "tracker": "simkl", "status": status},
                {
                    "$set": {
                        "uid": user_id,
                        "tracker": "simkl",
                        "status": status,
                        "data": collection,
                        "fetched_at": now,
                        "expires_at": now + datetime.timedelta(days=7),
                    }
                },
                upsert=True,
            )
        except Exception as e:
            logging.error("Failed to write user_watchlist_cache (Simkl): %s", e)
        return collection
    except Exception as e:
        from app.api.simkl import SimklTokenInvalidError
        if isinstance(e, SimklTokenInvalidError):
            logging.warning("Simkl token invalid during get_cached_simkl_user_anime_list for user %s: %s", user_id, e)
            from app.services.db import handle_invalid_simkl_token
            handle_invalid_simkl_token(user_id)
        if cached:
            logging.warning("Simkl API failed, returning expired cache for user %s: %s", user_id, e)
            return cached["data"]
        raise e


async def fetch_anilist_details_in_bulk(mal_ids: list[str] | None = None, anilist_ids: list[str] | None = None) -> dict:
    mal_ids = [str(m) for m in (mal_ids or []) if m]
    direct_al_ids = [str(a) for a in (anilist_ids or []) if a]
    if not mal_ids and not direct_al_ids:
        return {}
    from app.services.db import db, id_cache_collection

    mal_to_anilist = {}
    if mal_ids:
        try:
            cache_docs = list(id_cache_collection.find({"mal_id": {"$in": mal_ids}}))
            mal_to_anilist = {str(doc["mal_id"]): str(doc["anilist_id"]) for doc in cache_docs if doc.get("anilist_id")}
        except Exception as e:
            logging.error("Failed to fetch id_cache in bulk: %s", e)
            mal_to_anilist = {}

        # Resolve any uncached MAL IDs via local MongoDB fribb_mappings database (zero external network latency)
        uncached_mal_ids = [mid for mid in mal_ids if mid not in mal_to_anilist]
        if uncached_mal_ids:
            try:
                fribb_docs = list(db.fribb_mappings.find({"mal_id": {"$in": uncached_mal_ids}}))
                for fdoc in fribb_docs:
                    m_id = str(fdoc.get("mal_id") or "")
                    al_id = str(fdoc.get("anilist_id") or "")
                    if m_id and al_id:
                        mal_to_anilist[m_id] = al_id
            except Exception as e:
                logging.error("Failed to query fribb_mappings for bulk AniList details: %s", e)

            # 3. HTTP Fallback for any remaining unmapped IDs (e.g. brand new / niche anime not yet in Fribb)
            remaining_mal_ids = [mid for mid in uncached_mal_ids if mid not in mal_to_anilist]
            if remaining_mal_ids:
                from app.lib.id_resolver import resolve_mal_to_kitsu
                sem = asyncio.Semaphore(10)

                async def resolve_with_sem(mid):
                    async with sem:
                        try:
                            await resolve_mal_to_kitsu(mid)
                        except Exception as ex:
                            logging.warning("Tier 3 fallback failed for MAL ID %s: %s", mid, ex)

                await asyncio.gather(*[resolve_with_sem(mid) for mid in remaining_mal_ids])

                # Check id_cache for newly resolved items
                try:
                    fresh_docs = list(id_cache_collection.find({"mal_id": {"$in": remaining_mal_ids}}))
                    for doc in fresh_docs:
                        if doc.get("mal_id") and doc.get("anilist_id"):
                            mal_to_anilist[str(doc["mal_id"])] = str(doc["anilist_id"])
                except Exception as e:
                    logging.error("Failed to re-query id_cache after Tier 3 resolution: %s", e)

    target_al_ids = set(direct_al_ids)
    target_al_ids.update(mal_to_anilist.values())
    target_al_ids = [x for x in target_al_ids if x]
    if not target_al_ids:
        return {}

    # 1. Query local airing cache
    now = datetime.datetime.utcnow()
    airing_col = db.get_collection("anilist_airing_cache")
    cached_details = {}
    try:
        cached_docs = list(
            airing_col.find({"anilist_id": {"$in": [int(x) for x in target_al_ids if x.isdigit()]}, "expires_at": {"$gt": now}})
        )
        for doc in cached_docs:
            cached_details[str(doc["anilist_id"])] = {
                "id": doc["anilist_id"],
                "status": doc.get("status"),
                "nextAiringEpisode": doc.get("nextAiringEpisode"),
                "averageScore": doc.get("averageScore"),
                "episodes": doc.get("episodes"),
                "endDate": doc.get("endDate"),
                "title": doc.get("title"),
                "coverImage": doc.get("coverImage") or "",
                "bannerImage": doc.get("bannerImage") or "",
            }
    except Exception as e:
        logging.error("Failed to read from anilist_airing_cache: %s", e)

    # 2. Determine which IDs need to be fetched
    uncached_anilist_ids = [aid for aid in target_al_ids if aid not in cached_details]

    if uncached_anilist_ids:
        query = """
        query ($ids: [Int]) {
          Page(page: 1, perPage: 50) {
            media(id_in: $ids, type: ANIME) {
              id
              status
              averageScore
              episodes
              endDate {
                year
                month
                day
              }
              nextAiringEpisode {
                episode
                airingAt
              }
              title {
                english
                romaji
                native
                userPreferred
              }
              coverImage {
                large
              }
              bannerImage
            }
          }
        }
        """
        try:
            chunks = [uncached_anilist_ids[i : i + 50] for i in range(0, len(uncached_anilist_ids), 50)]

            async def fetch_chunk(chunk_ids):
                try:
                    res = await anilist_api._gql(None, query, {"ids": [int(x) for x in chunk_ids if x.isdigit()]})
                    return res.get("data", {}).get("Page", {}).get("media", [])
                except Exception as e:
                    logging.error("AniList chunk query failed: %s", e)
                return []

            tasks = [fetch_chunk(c) for c in chunks]
            results = await asyncio.gather(*tasks)

            media_list = []
            for r in results:
                media_list.extend(r)

            for media in media_list:
                aid = media.get("id")
                if not aid:
                    continue
                status = media.get("status", "")
                next_ep = media.get("nextAiringEpisode")
                avg_score = media.get("averageScore")
                m_title = media.get("title")
                cover_img = (media.get("coverImage") or {}).get("large") or ""
                banner_img = media.get("bannerImage") or ""
                media["coverImage"] = cover_img
                media["bannerImage"] = banner_img

                # Expiry calculations:
                if status == "FINISHED":
                    expires_at = now + datetime.timedelta(days=30)
                elif next_ep and next_ep.get("airingAt"):
                    target_time = datetime.datetime.fromtimestamp(next_ep["airingAt"]) + datetime.timedelta(minutes=15)
                    max_cap = now + datetime.timedelta(hours=12)
                    expires_at = min(target_time, max_cap)
                elif status == "NOT_YET_RELEASED":
                    expires_at = now + datetime.timedelta(days=2)
                else:
                    expires_at = now + datetime.timedelta(hours=12)

                try:
                    airing_col.update_one(
                        {"anilist_id": int(aid)},
                        {
                            "$set": {
                                "anilist_id": int(aid),
                                "status": status,
                                "nextAiringEpisode": next_ep,
                                "averageScore": avg_score,
                                "episodes": media.get("episodes"),
                                "endDate": media.get("endDate"),
                                "title": m_title,
                                "coverImage": cover_img,
                                "bannerImage": banner_img,
                                "expires_at": expires_at,
                            }
                        },
                        upsert=True,
                    )
                except Exception as e:
                    logging.error("Failed to update anilist_airing_cache: %s", e)

                cached_details[str(aid)] = media
        except Exception as e:
            logging.error("Failed bulk AniList query: %s", e)

    # 3. Map back to MAL IDs and AniList IDs
    result_details = {}
    for mid, aid in mal_to_anilist.items():
        if aid in cached_details:
            result_details[str(mid)] = cached_details[aid]
    for aid in target_al_ids:
        if aid in cached_details:
            result_details[str(aid)] = cached_details[aid]
    return result_details


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
                        mal_entries = await get_cached_mal_user_anime_list(
                            user_id, mal_token, mal_status
                        )
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
                    simkl_entries = await get_cached_simkl_user_anime_list(
                        user_id, user["simkl_access_token"], simkl_status
                    )
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
        kitsu_mappings = await bulk_resolve_to_kitsu(mal_ids=mal_ids, anilist_ids=anilist_ids, simkl_ids=simkl_ids, skip_external=True)

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
                page_titles_bulk = await fetch_anilist_details_in_bulk(missing_simkl_mal_ids, anilist_ids=missing_simkl_al_ids)
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
                    is_movie = (al_media.get("format") == "MOVIE")
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
                        total_eps = show_obj.get("episodes_count") or show_obj.get("num_episodes") or s_item.get("total_episodes_count") or total_eps
                    if progress == 0:
                        progress = s_item.get("watched_episodes_count") or s_item.get("episodes_watched") or s_item.get("progress") or 0

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

        custom_sort_enabled, sort_by, sort_order = get_catalog_sorting(user, catalog_id, category_key, url_filters=filters)

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
                if simkl_status_str not in ["ended", "completed", "canceled", "cancelled"] or item.get("not_aired_episodes_count", 0) > 0 or not simkl_status_str:
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
            progress = (
                item.get("watched_episodes_count") or item.get("episodes_watched") or item.get("progress") or 0
            )
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

                al_media = ((bulk_details.get(mal_id) if mal_id else None) or (bulk_details.get(al_id) if al_id else None) or {})
                is_airing = False
                if al_media:
                    al_status = al_media.get("status", "")
                    is_airing = al_status in ["RELEASING", "NOT_YET_RELEASED"]
                else:
                    is_airing = item.get("not_aired_episodes_count", 0) > 0

                progress = (
                    item.get("watched_episodes_count") or item.get("episodes_watched") or item.get("progress") or 0
                )
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
                page_titles_bulk = await fetch_anilist_details_in_bulk(missing_simkl_mal_ids, anilist_ids=missing_simkl_al_ids)
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
                total_eps = show_obj.get("episodes_count") or show_obj.get("num_episodes") or item.get("total_episodes_count") or "?"
                name = get_simkl_display_title(show_obj, title_lang, bulk_details=bulk_details)
                poster = show_obj.get("poster") or show_obj.get("poster_image") or ""
                if poster and not poster.startswith("http"):
                    poster = f"https://simkl.in/posters/{poster}_m.jpg"

                is_new_ep = False
                if simkl_status in ["watching", "plantowatch"]:
                    is_new_ep, _, _, _ = compute_simkl_flags(item, mal_id, al_id=al_id)

                if is_new_ep and enable_new_ep_badge and poster:
                    encoded_url = urllib.parse.quote_plus(poster)
                    badge_style = user.get("badge_style", "modern")
                    poster = f"{Config.PROTOCOL}://{Config.REDIRECT_URL}/{user_id}/poster/simkl_{simkl_id}_s_22.jpg?url={encoded_url}&badge=new&tracker=simkl&style={badge_style}&v=hd_poster_v1"

                kitsu_id = kitsu_mappings.get(f"simkl:{simkl_id}")
                stremio_id = f"kitsu:{kitsu_id}" if kitsu_id else f"simkl:{simkl_id}"

                simkl_media_type = show_obj.get("anime_type") or show_obj.get("type") or "series"
                stremio_type = "movie" if simkl_media_type == "movie" else "series"

                simkl_status_titles = {
                    "watching": "Watching",
                    "plantowatch": "Plan to Watch",
                    "completed": "Completed",
                    "hold": "On Hold",
                    "dropped": "Dropped",
                }
                disp_simkl_status = simkl_status_titles.get(simkl_status, simkl_status.replace('_', ' ').title())

                sf = show_obj.get("fanart")
                simkl_fanart = (sf if sf.startswith("http") else f"https://simkl.in/fanart/{sf}_medium.jpg") if sf else None

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
                            f"Simkl Watchlist - {disp_simkl_status}.\n"
                            f"Progress: {progress} / {total_eps}."
                        ),
                        "score": meta_fields["score"],
                        "episodes": meta_fields["episodes"] or (int(total_eps) if isinstance(total_eps, int) or (isinstance(total_eps, str) and total_eps.isdigit()) else 0),
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

    return await respond_with(
        {"metas": format_catalog_metas(metas, user, catalog_type, catalog_id)},
        max_age=300,
        stale_while_revalidate=600,
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

        custom_sort_enabled, sort_by, sort_order = get_catalog_sorting(user, catalog_id, category_key, url_filters=filters)

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

        mal_ids = [str(item["node"]["id"]) for item in paged_data_items if isinstance(item, dict) and item.get("node") and isinstance(item["node"], dict) and item["node"].get("id")]
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

                if is_new_ep and enable_new_ep_badge and poster:
                    encoded_url = urllib.parse.quote_plus(poster)
                    badge_style = user.get("badge_style", "modern")
                    poster = f"{Config.PROTOCOL}://{Config.REDIRECT_URL}/{user_id}/poster/{mal_id}_m_22.jpg?url={encoded_url}&badge=new&tracker=mal&style={badge_style}&v=hd_poster_v1"

                kitsu_id = kitsu_mappings.get(f"mal:{mal_id}")
                stremio_id = f"kitsu:{kitsu_id}" if kitsu_id else f"mal:{mal_id}"

                mal_media_type = (node.get("media_type") or "tv").lower()
                stremio_type = "movie" if mal_media_type == "movie" else "series"

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
                            f"Progress: {progress} / {node.get('num_episodes') or '?'}."
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

    return await respond_with(
        {"metas": format_catalog_metas(metas, user, catalog_type, catalog_id)},
        max_age=300,
        stale_while_revalidate=600,
    )


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
            from app.services.db import handle_invalid_anilist_token
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
            entries = sort_watchlist_items(entries, sort_by, sort_order, "anilist", bulk_details=None)
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
        from app.lib.id_resolver import bulk_resolve_to_kitsu

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

                if is_new_ep and enable_new_ep_badge and poster:
                    encoded_url = urllib.parse.quote_plus(poster)
                    badge_style = user.get("badge_style", "modern")
                    poster = f"{Config.PROTOCOL}://{Config.REDIRECT_URL}/{user_id}/poster/{al_id}_a_22.jpg?url={encoded_url}&badge=new&tracker=anilist&style={badge_style}&v=hd_poster_v1"

                kitsu_id = kitsu_mappings.get(f"anilist:{al_id}")
                stremio_id = f"kitsu:{kitsu_id}" if kitsu_id else f"anilist:{al_id}"

                al_media_format = (media.get("format") or "tv").lower()
                stremio_type = "movie" if al_media_format == "movie" else "series"

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

    return await respond_with(
        {"metas": format_catalog_metas(metas, user, catalog_type, catalog_id)},
        max_age=300,
        stale_while_revalidate=600,
    )
