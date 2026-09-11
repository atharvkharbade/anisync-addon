import asyncio
import datetime
import logging

from app.api import anilist as anilist_api
from app.api import mal as mal_api
from app.api import simkl as simkl_api


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
                "format": doc.get("format"),
                "nextAiringEpisode": doc.get("nextAiringEpisode"),
                "averageScore": doc.get("averageScore"),
                "episodes": doc.get("episodes"),
                "startDate": doc.get("startDate"),
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
              format
              averageScore
              episodes
              startDate {
                year
                month
                day
              }
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
                m_format = media.get("format")
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
                                "format": m_format,
                                "nextAiringEpisode": next_ep,
                                "averageScore": avg_score,
                                "episodes": media.get("episodes"),
                                "startDate": media.get("startDate"),
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
