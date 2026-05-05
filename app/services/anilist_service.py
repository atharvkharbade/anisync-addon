import logging
from enum import Enum

from app.api import anilist as al_api


class UpdateStatus(Enum):
    OK = "ANILIST=OK"
    NULL = "ANILIST=NO_UPDATE"
    NOT_LIST = "ANILIST=NOT_LISTED"
    FAIL = "ANILIST=FAILED"


async def sync_anilist(user: dict, anilist_id: str, episode: int, sync_unlisted: bool) -> UpdateStatus:
    token = user.get("anilist_token", "")
    try:
        media = await al_api.get_media_status(token, int(anilist_id))
    except Exception as e:
        logging.error("AniList get_media_status failed: %s", e)
        return UpdateStatus.FAIL

    total_episodes = media.get("episodes") or 0
    list_entry = media.get("mediaListEntry")
    current_al_status = list_entry.get("status", "") if list_entry else ""
    progress = list_entry.get("progress", 0) if list_entry else 0

    logging.info(
        "AniList sync: id=%s ep=%d progress=%d status=%s total=%d",
        anilist_id, episode, progress, current_al_status, total_episodes
    )

    if not list_entry and not sync_unlisted:
        return UpdateStatus.NOT_LIST

    # No regression
    if episode <= progress:
        logging.info("AniList no update needed: ep=%d already at progress=%d", episode, progress)
        return UpdateStatus.NULL

    # Determine new status
    if total_episodes and episode >= total_episodes:
        new_status = "COMPLETED"
    else:
        new_status = "CURRENT"

    try:
        await al_api.save_entry(token, int(anilist_id), episode, new_status)
        logging.info("AniList updated: id=%s ep=%d status=%s", anilist_id, episode, new_status)
        return UpdateStatus.OK
    except Exception as e:
        logging.error("AniList save_entry failed: %s", e)
        return UpdateStatus.FAIL
