import logging
from enum import Enum

from app.api import simkl as simkl_api

logger = logging.getLogger("anisync")


class UpdateStatus(Enum):
    OK = "SIMKL=OK"
    NULL = "SIMKL=NO_UPDATE"
    NOT_LIST = "SIMKL=NOT_LISTED"
    FAIL = "SIMKL=FAILED"


async def sync_simkl(
    user: dict,
    kitsu_id: str,
    mal_id: str | None,
    anilist_id: str | None,
    episode: int,
    content_type: str,
    sync_unlisted: bool,
) -> UpdateStatus:
    """Sync watch progress for a movie or show episode to Simkl."""
    user_id = user.get("uid")
    if user.get("simkl_token_expired"):
        logger.info("Skipping Simkl sync for user %s: token is expired", user_id)
        return UpdateStatus.FAIL

    from app.services.db import handle_invalid_simkl_token, reset_simkl_error_counter
    from app.api.simkl import SimklTokenInvalidError

    token = user.get("simkl_access_token", "")
    if not token:
        logger.warning("Simkl token not found for user %s", user_id)
        return UpdateStatus.FAIL

    # If sync_unlisted is False, we check if the show exists in the user's Simkl watchlist.
    if not sync_unlisted:
        try:
            watchlist = await simkl_api.get_user_anime_list(token)
            reset_simkl_error_counter(user_id)
            found = False
            for item in watchlist:
                # Resolve ids dictionary
                if "show" in item and isinstance(item["show"], dict):
                    show_ids = item["show"].get("ids") or {}
                elif "anime" in item and isinstance(item["anime"], dict):
                    show_ids = item["anime"].get("ids") or {}
                else:
                    show_ids = item.get("ids") or {}

                # Match against any of the provided IDs
                if mal_id and str(show_ids.get("mal")) == str(mal_id):
                    found = True
                    break
                if anilist_id and str(show_ids.get("anilist")) == str(anilist_id):
                    found = True
                    break
                if kitsu_id and str(show_ids.get("kitsu")) == str(kitsu_id):
                    found = True
                    break

            if not found:
                logger.info(
                    "Simkl sync skipped: item kitsu:%s / mal:%s / al:%s not in user watchlist.",
                    kitsu_id,
                    mal_id,
                    anilist_id,
                )
                return UpdateStatus.NOT_LIST
        except Exception as e:
            if isinstance(e, SimklTokenInvalidError):
                logger.warning("Simkl token invalid during watchlist check for user %s: %s", user_id, e)
                handle_invalid_simkl_token(user_id)
            logger.error("Failed to check Simkl watchlist: %s", e)
            return UpdateStatus.FAIL

    try:
        # Perform the history update to Simkl
        success = await simkl_api.sync_history(
            token=token,
            kitsu_id=kitsu_id,
            mal_id=mal_id,
            anilist_id=anilist_id,
            episode=episode,
            content_type=content_type,
        )
        if success:
            reset_simkl_error_counter(user_id)
            logger.info(
                "Simkl updated: kitsu=%s mal=%s al=%s ep=%d type=%s", kitsu_id, mal_id, anilist_id, episode, content_type
            )
            return UpdateStatus.OK
        else:
            return UpdateStatus.FAIL
    except Exception as e:
        if isinstance(e, SimklTokenInvalidError):
            logger.warning("Simkl token invalid during sync_history for user %s: %s", user_id, e)
            handle_invalid_simkl_token(user_id)
        logger.error("Failed to sync Simkl: %s", e)
        return UpdateStatus.FAIL

