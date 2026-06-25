import logging
from datetime import date
from enum import Enum

from app.api import mal as mal_api


class UpdateStatus(Enum):
    OK = "MAL=OK"
    NULL = "MAL=NO_UPDATE"
    NOT_LIST = "MAL=NOT_LISTED"
    FAIL = "MAL=FAILED"


def _resolve_new_status(
    current_status: str,
    current_episode: int,
    watched_episodes: int,
    total_episodes: int,
    is_rewatching: bool = False,
) -> str | None:
    # Allow any listed status including "completed" re-watch edge cases
    if not current_status:
        return None
    
    # Start/restart rewatch (ignore movies/single-episodes to prevent infinite loops)
    if total_episodes != 1 and (current_status == "completed" or (is_rewatching and watched_episodes == total_episodes)) and current_episode == 1:
        return "watching"

    # Already at this episode or further — no regression
    if current_episode <= watched_episodes:
        return None
    # Last episode — mark completed
    if total_episodes > 0 and current_episode >= total_episodes:
        return "completed"
    # Otherwise keep/set watching
    return "watching"


def _watch_dates(
    list_status: dict | None,
    current_episode: int,
    total_episodes: int,
) -> tuple[str, str]:
    today = date.today().strftime("%Y-%m-%d")
    start_date, finish_date = "", ""

    if list_status:
        if list_status.get("is_rewatching"):
            return "", ""
        start_date = list_status.get("start_date") or ""
        finish_date = list_status.get("finish_date") or ""

    if not start_date and current_episode == 1:
        start_date = today
    if not finish_date and total_episodes > 0 and current_episode >= total_episodes:
        finish_date = today

    return start_date, finish_date


async def sync_mal(user: dict, mal_id: str, episode: int, sync_unlisted: bool) -> UpdateStatus:
    user_id = user.get("uid")
    from app.services.db import get_or_refresh_mal_token, handle_invalid_mal_token, reset_mal_error_counter
    from app.api.mal import MalTokenInvalidError

    token = await get_or_refresh_mal_token(user_id)
    if not token:
        logging.warning("MAL sync skipped: No valid token available for user %s", user_id)
        return UpdateStatus.FAIL

    try:
        anime = await mal_api.get_anime_details(token, mal_id)
        reset_mal_error_counter(user_id)
    except Exception as e:
        if isinstance(e, MalTokenInvalidError):
            logging.warning("MAL token invalid during get_anime_details for user %s: %s", user_id, e)
            handle_invalid_mal_token(user_id)
        logging.error("MAL get_anime_details failed: %s", e)
        return UpdateStatus.FAIL

    total_episodes = anime.get("num_episodes") or 0
    list_status = anime.get("my_list_status")
    current_status = list_status.get("status", "") if list_status else ""
    watched_episodes = list_status.get("num_episodes_watched", 0) if list_status else 0
    is_rewatching = list_status.get("is_rewatching", False) if list_status else False
    num_times_rewatched = list_status.get("num_times_rewatched", 0) if list_status else 0

    logging.info(
        "MAL sync: id=%s ep=%d watched=%d status=%s total=%d is_rewatching=%s rewatch_count=%d",
        mal_id,
        episode,
        watched_episodes,
        current_status,
        total_episodes,
        is_rewatching,
        num_times_rewatched,
    )

    if not current_status and not sync_unlisted:
        return UpdateStatus.NOT_LIST

    if not current_status and sync_unlisted:
        current_status = "watching"

    new_status = _resolve_new_status(current_status, episode, watched_episodes, total_episodes, is_rewatching=is_rewatching)
    if not new_status:
        logging.info("MAL no update needed: ep=%d already watched=%d", episode, watched_episodes)
        return UpdateStatus.NULL

    start_date, finish_date = _watch_dates(list_status, episode, total_episodes)

    # Rewatching logic
    send_is_rewatching = None
    send_num_times_rewatched = None
    if (current_status == "completed" and episode == 1) or is_rewatching:
        if new_status == "completed":
            send_is_rewatching = False
            send_num_times_rewatched = num_times_rewatched + 1
        else:
            send_is_rewatching = True

    try:
        await mal_api.update_watch_status(
            token,
            mal_id,
            episode,
            new_status,
            start_date,
            finish_date,
            is_rewatching=send_is_rewatching,
            num_times_rewatched=send_num_times_rewatched,
        )
        reset_mal_error_counter(user_id)
        logging.info("MAL updated: id=%s ep=%d status=%s", mal_id, episode, new_status)
        return UpdateStatus.OK
    except Exception as e:
        if isinstance(e, MalTokenInvalidError):
            logging.warning("MAL token invalid during update_watch_status for user %s: %s", user_id, e)
            handle_invalid_mal_token(user_id)
        logging.error("MAL update_watch_status failed: %s", e)
        return UpdateStatus.FAIL

