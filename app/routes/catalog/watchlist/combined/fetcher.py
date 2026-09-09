import asyncio
import logging

from app.api import anilist as anilist_api
from app.services.db import store_user

from ..common import (
    get_cached_anilist_user_anime_list,
    get_cached_mal_user_anime_list,
    get_cached_simkl_user_anime_list,
)


async def fetch_combined_tracker_lists(
    user, user_id, mal_enabled, anilist_enabled, simkl_enabled, mal_status, al_status, simkl_status
) -> tuple[list, list, list]:
    """
    Fetches user anime lists from MAL, AniList, and Simkl in parallel.
    Handles credential verification, viewer ID retrieval, and token expiration.
    Returns: (mal_entries, anilist_entries, simkl_entries)
    """
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
    return mal_entries, anilist_entries, simkl_entries
