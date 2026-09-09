import logging

from app.api import anilist as anilist_api
from app.api import mal as mal_api
from app.api import simkl as simkl_api
from app.services.db import db, handle_invalid_anilist_token, store_user
from app.services.recommendations.utils import normalize_user_status

logger = logging.getLogger(__name__)


async def fetch_user_watchlist_history(user: dict, user_id: str) -> tuple[dict, set, set, set, set]:
    """Fetches and normalizes watchlist history across all connected trackers (MAL, AniList, Simkl).

    Returns:
        tuple of (merged_shows, watched_mal_ids, watched_anilist_ids, watched_kitsu_ids, watched_titles)
    """
    # 1. Fetch watched history from track managers
    mal_items = []
    if user.get("mal_access_token") and user.get("mal_enabled", True):
        try:
            res = await mal_api.get_user_anime_list(user["mal_access_token"], limit=100)
            mal_items = res.get("data", [])
        except Exception as e:
            logger.warning("Failed to fetch MAL user list: %s", e)

    anilist_items = []
    if user.get("anilist_token") and user.get("anilist_enabled", True):
        try:
            anilist_uid = user.get("anilist_id")
            if anilist_uid:
                anilist_uid = int(anilist_uid)
            else:
                viewer = await anilist_api.get_viewer(user["anilist_token"])
                anilist_uid = int(viewer["id"])
                user["anilist_id"] = str(anilist_uid)
                store_user(user)
            collection = await anilist_api.get_user_anime_list(user["anilist_token"], user_id=anilist_uid)
            for user_list in collection.get("lists", []):
                anilist_items.extend(user_list.get("entries", []))
        except anilist_api.AnilistTokenInvalidError as e:
            logger.warning("AniList token invalid during recommendations update for user %s: %s", user_id, e)
            handle_invalid_anilist_token(user_id)
        except Exception as e:
            logger.warning("Failed to fetch AniList user list: %s", e)

    simkl_items = []
    if user.get("simkl_access_token") and user.get("simkl_enabled", True):
        try:
            simkl_items = await simkl_api.get_user_anime_list(user["simkl_access_token"])
        except Exception as e:
            logger.warning("Failed to fetch Simkl user list for recommendations: %s", e)

    # 2. Extract unique shows and filter watched lists
    merged_shows = {}
    title_lang = (user.get("title_language", "english") or "english").lower() if user else "english"

    for item in mal_items:
        node = item.get("node", {})
        alt_titles = node.get("alternative_titles") or {}
        if title_lang == "english":
            title = alt_titles.get("en") or node.get("title") or "Unknown Title"
        elif title_lang == "japanese":
            title = alt_titles.get("ja") or node.get("title") or "Unknown Title"
        else:
            title = node.get("title") or alt_titles.get("en") or "Unknown Title"

        mal_id = str(node.get("id"))
        list_status = node.get("my_list_status", {})
        status = normalize_user_status(list_status.get("status"))
        rating = list_status.get("score", 0) or 0
        genres = [g.get("name") for g in node.get("genres", []) if g.get("name")]

        merged_shows[mal_id] = {
            "title": title,
            "mal_id": mal_id,
            "anilist_id": None,
            "kitsu_id": None,
            "simkl_id": None,
            "status": status,
            "rating": rating,
            "genres": genres,
        }

    for entry in anilist_items:
        media = entry.get("media", {})
        t_obj = media.get("title") or {}
        if title_lang == "english":
            title = t_obj.get("english") or t_obj.get("userPreferred") or t_obj.get("romaji") or "Unknown Title"
        elif title_lang == "japanese":
            title = t_obj.get("native") or t_obj.get("userPreferred") or t_obj.get("romaji") or "Unknown Title"
        else:
            title = t_obj.get("romaji") or t_obj.get("userPreferred") or t_obj.get("english") or "Unknown Title"

        anilist_id = str(media.get("id"))
        mal_id = str(media.get("idMal")) if media.get("idMal") else None
        status = normalize_user_status(entry.get("status"))
        rating = entry.get("score", 0) or 0
        if rating > 10:
            rating = int(rating / 10)
        genres = media.get("genres", []) or []

        key = mal_id if mal_id else f"al_{anilist_id}"
        if key not in merged_shows:
            merged_shows[key] = {
                "title": title,
                "mal_id": mal_id,
                "anilist_id": anilist_id,
                "kitsu_id": None,
                "simkl_id": None,
                "status": status,
                "rating": rating,
                "genres": genres,
            }
        else:
            merged_shows[key]["anilist_id"] = anilist_id
            merged_shows[key]["rating"] = max(merged_shows[key].get("rating") or 0, rating)

            old_status = merged_shows[key]["status"]
            if old_status == "planning" and status != "planning":
                merged_shows[key]["status"] = status
            elif old_status != "completed" and status == "completed":
                merged_shows[key]["status"] = "completed"

            old_genres = merged_shows[key].get("genres", [])
            for g in genres:
                if g not in old_genres:
                    old_genres.append(g)
            merged_shows[key]["genres"] = old_genres

    for item in simkl_items:
        if "show" in item and isinstance(item["show"], dict):
            show_obj = item["show"]
        elif "anime" in item and isinstance(item["anime"], dict):
            show_obj = item["anime"]
        else:
            show_obj = item

        show_ids = show_obj.get("ids") or {}
        simkl_id = str(show_ids.get("simkl") or "")
        mal_id = str(show_ids.get("mal") or "") or None
        anilist_id = str(show_ids.get("anilist") or "") or None
        kitsu_id = str(show_ids.get("kitsu") or "") or None

        title = show_obj.get("title") or ""
        status = normalize_user_status(item.get("list"))
        rating = item.get("user_rating", 0) or 0
        genres = show_obj.get("genres", []) or []

        matched_key = None
        if mal_id and mal_id in merged_shows:
            matched_key = mal_id
        elif anilist_id and f"al_{anilist_id}" in merged_shows:
            matched_key = f"al_{anilist_id}"

        if matched_key:
            merged_shows[matched_key]["simkl_id"] = simkl_id
            if kitsu_id:
                merged_shows[matched_key]["kitsu_id"] = kitsu_id
            merged_shows[matched_key]["rating"] = max(merged_shows[matched_key].get("rating") or 0, rating)

            old_status = merged_shows[matched_key]["status"]
            if old_status == "planning" and status != "planning":
                merged_shows[matched_key]["status"] = status
            elif old_status != "completed" and status == "completed":
                merged_shows[matched_key]["status"] = "completed"

            old_genres = merged_shows[matched_key].get("genres", [])
            for g in genres:
                if g not in old_genres:
                    old_genres.append(g)
            merged_shows[matched_key]["genres"] = old_genres
        else:
            key = (
                mal_id
                if mal_id
                else (f"al_{anilist_id}" if anilist_id else (f"kitsu_{kitsu_id}" if kitsu_id else f"simkl_{simkl_id}"))
            )
            merged_shows[key] = {
                "title": title,
                "mal_id": mal_id,
                "anilist_id": anilist_id,
                "kitsu_id": kitsu_id,
                "simkl_id": simkl_id,
                "status": status,
                "rating": rating,
                "genres": genres,
            }

    # Watched sets for filtering
    watched_mal_ids = set()
    watched_anilist_ids = set()
    watched_kitsu_ids = set()
    watched_titles = set()

    for show in merged_shows.values():
        if show["status"] == "planning":
            continue

        if show.get("mal_id"):
            watched_mal_ids.add(str(show["mal_id"]))
        if show.get("anilist_id"):
            watched_anilist_ids.add(str(show["anilist_id"]))
        if show.get("kitsu_id"):
            watched_kitsu_ids.add(str(show["kitsu_id"]))
        if show.get("title"):
            watched_titles.add(show["title"].lower())

    # Bulk-resolve IDs from fribb_mappings and id_cache
    raw_mal_ids = list(watched_mal_ids)
    raw_al_ids = list(watched_anilist_ids)
    raw_kitsu_ids = list(watched_kitsu_ids)
    if raw_mal_ids or raw_al_ids or raw_kitsu_ids:
        fribb_query = []
        if raw_mal_ids:
            fribb_query.append({"mal_id": {"$in": raw_mal_ids}})
        if raw_al_ids:
            fribb_query.append({"anilist_id": {"$in": raw_al_ids}})
        if raw_kitsu_ids:
            kitsu_int_ids = []
            for k in raw_kitsu_ids:
                try:
                    kitsu_int_ids.append(int(k))
                except Exception:
                    pass
            fribb_query.append({"kitsu_id": {"$in": raw_kitsu_ids + kitsu_int_ids}})
        if fribb_query:
            try:
                for doc in db.fribb_mappings.find({"$or": fribb_query}):
                    m_id = doc.get("mal_id")
                    a_id = doc.get("anilist_id")
                    k_id = doc.get("kitsu_id")
                    if m_id:
                        watched_mal_ids.add(str(m_id))
                    if a_id:
                        watched_anilist_ids.add(str(a_id))
                    if k_id:
                        watched_kitsu_ids.add(str(k_id))
            except Exception as e:
                logger.warning("Failed to bulk query fribb_mappings for ID resolving: %s", e)

        cache_query = []
        if raw_mal_ids:
            cache_query.append({"mal_id": {"$in": raw_mal_ids}})
        if raw_al_ids:
            cache_query.append({"anilist_id": {"$in": raw_al_ids}})
        if raw_kitsu_ids:
            cache_query.append({"kitsu_id": {"$in": raw_kitsu_ids}})
        if cache_query:
            try:
                for doc in db.get_collection("id_cache").find({"$or": cache_query}):
                    m_id = doc.get("mal_id")
                    a_id = doc.get("anilist_id")
                    k_id = doc.get("kitsu_id")
                    if m_id:
                        watched_mal_ids.add(str(m_id))
                    if a_id:
                        watched_anilist_ids.add(str(a_id))
                    if k_id:
                        watched_kitsu_ids.add(str(k_id))
            except Exception as e:
                logger.warning("Failed to bulk query id_cache for ID resolving: %s", e)

    return merged_shows, watched_mal_ids, watched_anilist_ids, watched_kitsu_ids, watched_titles
