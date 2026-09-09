import logging
from datetime import UTC, datetime

from app.services.db import get_user, store_user


async def sync_user_profiles_task(user_id: str):
    from app.api import anilist as al_api
    from app.api import mal as mal_api

    user = get_user(user_id)
    if not user:
        return

    updated = False
    now = datetime.now(UTC).replace(tzinfo=None)

    # 1. Sync MAL profile details if connected and enabled
    if user.get("mal_access_token") and user.get("mal_enabled", True):
        expiry = user.get("mal_expires_at")
        if expiry and now > expiry:
            logging.warning("Skipping MAL profile sync - token expired for user %s", user_id)
        else:
            try:
                user_info = await mal_api.get_user_details(user["mal_access_token"])
                if user_info.get("name"):
                    user["name"] = user_info["name"]
                if user_info.get("picture"):
                    user["mal_picture"] = user_info["picture"]
                    user["picture"] = user_info["picture"]
                updated = True
            except Exception as e:
                logging.error("Failed to sync MAL profile in background: %s", e)

    # 2. Sync AniList profile details if connected and enabled
    if user.get("anilist_token") and user.get("anilist_enabled", True):
        try:
            viewer = await al_api.get_viewer(user["anilist_token"])
            if viewer.get("name"):
                user["anilist_username"] = viewer["name"]
            if viewer.get("avatar", {}).get("large"):
                user["anilist_picture"] = viewer["avatar"]["large"]
                if not user.get("mal_picture"):
                    user["picture"] = viewer["avatar"]["large"]
            updated = True
        except Exception as e:
            logging.error("Failed to sync AniList profile in background: %s", e)

    # 3. Sync Simkl profile details if connected and enabled
    if user.get("simkl_access_token") and user.get("simkl_enabled", True):
        from app.api import simkl as simkl_api

        try:
            user_info = await simkl_api.get_user_details(user["simkl_access_token"])
            simkl_username = user_info.get("user", {}).get("name")
            simkl_avatar = user_info.get("user", {}).get("avatar")
            if simkl_username:
                user["simkl_username"] = simkl_username
            if simkl_avatar:
                user["simkl_avatar"] = simkl_avatar
                if not user.get("mal_picture") and not user.get("anilist_picture"):
                    user["picture"] = simkl_avatar
            updated = True
        except Exception as e:
            logging.error("Failed to sync Simkl profile in background: %s", e)

    if updated:
        user["last_profile_sync"] = now
        store_user(user)
