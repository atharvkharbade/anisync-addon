import logging
from enum import Enum

from app.api import anilist as al_api


class UpdateStatus(Enum):
    OK = "ANILIST=OK"
    NULL = "ANILIST=NO_UPDATE"
    NOT_LIST = "ANILIST=NOT_LISTED"
    FAIL = "ANILIST=FAILED"


async def sync_anilist(user: dict, anilist_id: str, episode: int, sync_unlisted: bool) -> UpdateStatus:
    from app.services.db import is_anilist_in_cooldown, reset_anilist_error_counter
    if is_anilist_in_cooldown(user):
        logging.info("Skipping AniList sync for user %s due to temporary auth error cooldown", user.get("uid"))
        return UpdateStatus.FAIL

    token = user.get("anilist_token", "")
    try:
        media = await al_api.get_media_status(token, int(anilist_id))
    except al_api.AnilistTokenInvalidError as e:
        logging.warning("AniList token invalid during get_media_status for user %s: %s", user.get("uid"), e)
        from app.services.db import handle_invalid_anilist_token
        handle_invalid_anilist_token(user.get("uid"))
        return UpdateStatus.FAIL
    except Exception as e:
        logging.error("AniList get_media_status failed: %s", e)
        return UpdateStatus.FAIL

    if user.get("anilist_consecutive_auth_errors", 0) > 0:
        reset_anilist_error_counter(user.get("uid"))

    total_episodes = media.get("episodes") or 0
    list_entry = media.get("mediaListEntry")
    current_al_status = list_entry.get("status", "") if list_entry else ""
    progress = list_entry.get("progress", 0) if list_entry else 0
    repeat = list_entry.get("repeat", 0) if list_entry else 0

    logging.info(
        "AniList sync: id=%s ep=%d progress=%d status=%s total=%d repeat=%d",
        anilist_id,
        episode,
        progress,
        current_al_status,
        total_episodes,
        repeat,
    )

    if not list_entry and not sync_unlisted:
        return UpdateStatus.NOT_LIST

    # Determine if we are starting a new rewatch or in the middle of one
    is_rewatching = (current_al_status == "REPEATING") or (current_al_status == "COMPLETED" and episode == 1)

    # Bypass the regression check if we are explicitly resetting/starting a rewatch
    is_starting_rewatch = (current_al_status == "COMPLETED" and episode == 1) or \
                          (current_al_status == "REPEATING" and episode == 1 and progress == total_episodes)

    if not is_starting_rewatch:
        # No regression
        if episode <= progress:
            logging.info("AniList no update needed: ep=%d already at progress=%d", episode, progress)
            return UpdateStatus.NULL

    # Determine new status & repeat count
    send_repeat = None
    if is_rewatching:
        if total_episodes and episode >= total_episodes:
            new_status = "COMPLETED"
            send_repeat = repeat + 1
        else:
            new_status = "REPEATING"
    else:
        if total_episodes and episode >= total_episodes:
            new_status = "COMPLETED"
        else:
            new_status = "CURRENT"

    try:
        await al_api.save_entry(token, int(anilist_id), episode, new_status, repeat=send_repeat)
        logging.info("AniList updated: id=%s ep=%d status=%s repeat=%s", anilist_id, episode, new_status, send_repeat)
        if user.get("anilist_consecutive_auth_errors", 0) > 0:
            reset_anilist_error_counter(user.get("uid"))
        return UpdateStatus.OK
    except al_api.AnilistTokenInvalidError as e:
        logging.warning("AniList token invalid during save_entry for user %s: %s", user.get("uid"), e)
        from app.services.db import handle_invalid_anilist_token
        handle_invalid_anilist_token(user.get("uid"))
        return UpdateStatus.FAIL
    except Exception as e:
        logging.error("AniList save_entry failed: %s", e)
        return UpdateStatus.FAIL
