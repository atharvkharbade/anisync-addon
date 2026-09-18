import asyncio
from collections import defaultdict
import logging
import time

from app.services import db as db_service

# In-memory per-user rate limiting for on-demand tracker queries
_meta_fallback_rate_limits: dict[str, list[float]] = defaultdict(list)
_FALLBACK_RATE_LIMIT = 10  # Max 10 on-demand tracker queries per 60 seconds per user


def _is_rate_limited(user_key: str) -> bool:
    now = time.time()
    ts_list = [t for t in _meta_fallback_rate_limits[user_key] if now - t < 60]
    if len(ts_list) >= _FALLBACK_RATE_LIMIT:
        return True
    ts_list.append(now)
    _meta_fallback_rate_limits[user_key] = ts_list
    return False


def _format_status_header(status_info: dict) -> str | None:
    status = (status_info.get("status") or "").lower()
    progress = status_info.get("progress") or 0
    total_eps = status_info.get("total_episodes") or 0
    score = status_info.get("score") or 0
    if score > 10:
        score = round(score / 10.0, 1)

    status_display_map = {
        "watching": "Watching",
        "current": "Watching",
        "completed": "Completed",
        "planning": "Plan to Watch",
        "plan_to_watch": "Plan to Watch",
        "plantowatch": "Plan to Watch",
        "on_hold": "On Hold",
        "paused": "On Hold",
        "hold": "On Hold",
        "dropped": "Dropped",
    }
    status_title = status_display_map.get(status, status.capitalize() if status else "Tracked")

    parts = [f"Status: {status_title}"]
    if status in ["watching", "current", "on_hold", "paused", "hold", "dropped", "completed"] and progress > 0:
        if total_eps > 0:
            parts.append(f"Progress: {progress}/{total_eps} Ep")
        else:
            parts.append(f"Progress: {progress} Ep")

    if score > 0:
        score_str = int(score) if float(score).is_integer() else f"{score:.1f}"
        parts.append(f"Your Rating: {score_str}/10")

    return f"[{' • '.join(parts)}]"


async def build_user_status_header(
    user: dict | None,
    user_id: str,
    mal_id: str | None = None,
    anilist_id: str | None = None,
    simkl_id: str | None = None,
) -> str | None:
    # 1. Check local caches (user_anime_status_cache + user_watchlist_cache)
    status_info = db_service.get_user_anime_meta_status(user_id, mal_id=mal_id, anilist_id=anilist_id, simkl_id=simkl_id)
    if status_info:
        return _format_status_header(status_info)

    # 2. If not in cache, perform on-demand tracker lookup (MAL > AniList)
    if not user and user_id:
        from app.services.db.users import get_user
        user = get_user(user_id)

    if not user:
        return None

    canonical_uid = str(user.get("uid") or user_id)
    if _is_rate_limited(canonical_uid):
        return None

    # --- MAL Check ---
    if mal_id and user.get("mal_enabled", True) and user.get("mal_access_token"):
        try:
            from app.api import mal as mal_api
            from app.api.mal import MalTokenInvalidError
            from app.services.db import get_or_refresh_mal_token, handle_invalid_mal_token, reset_mal_error_counter

            mal_token = await get_or_refresh_mal_token(canonical_uid)
            if mal_token:
                mal_details = await asyncio.wait_for(mal_api.get_anime_details(mal_token, str(mal_id)), timeout=2.5)
                reset_mal_error_counter(canonical_uid)
                my_status = mal_details.get("my_list_status")
                if my_status and my_status.get("status"):
                    st = my_status.get("status")
                    prog = my_status.get("num_episodes_watched") or 0
                    sc = my_status.get("score") or 0
                    total = mal_details.get("num_episodes") or 0
                    db_service.save_user_anime_meta_status(
                        canonical_uid,
                        status=st,
                        progress=prog,
                        total_episodes=total,
                        score=sc,
                        mal_id=mal_id,
                        anilist_id=anilist_id,
                        simkl_id=simkl_id,
                        tracker="mal",
                    )
                    return _format_status_header({"status": st, "progress": prog, "total_episodes": total, "score": sc})
        except MalTokenInvalidError as e:
            logging.warning("MAL token invalid during on-demand lookup for user %s: %s", canonical_uid, e)
            handle_invalid_mal_token(canonical_uid)
        except Exception as e:
            logging.debug("On-demand MAL status lookup skipped for user %s, anime %s: %s", user_id, mal_id, e)

    # --- AniList Check ---
    if anilist_id and user.get("anilist_enabled", True) and user.get("anilist_token"):
        try:
            from app.api import anilist as anilist_api
            from app.api.anilist import AnilistTokenInvalidError
            from app.services.db import handle_invalid_anilist_token, is_anilist_in_cooldown, reset_anilist_error_counter

            if not is_anilist_in_cooldown(user):
                al_uid = user.get("anilist_id")
                if al_uid:
                    query = """
                    query ($userId: Int, $mediaId: Int) {
                      MediaList(userId: $userId, mediaId: $mediaId) {
                        status
                        progress
                        score
                        media {
                          episodes
                        }
                      }
                    }
                    """
                    data = await asyncio.wait_for(
                        anilist_api._gql(user["anilist_token"], query, {"userId": int(al_uid), "mediaId": int(anilist_id)}),
                        timeout=2.5,
                    )
                    reset_anilist_error_counter(canonical_uid)
                    entry = (data.get("data") or {}).get("MediaList")
                    if entry and entry.get("status"):
                        st = entry.get("status")
                        prog = entry.get("progress") or 0
                        raw_sc = entry.get("score") or 0
                        sc = round(raw_sc / 10.0, 1) if raw_sc > 10 else raw_sc
                        total = (entry.get("media") or {}).get("episodes") or 0
                        db_service.save_user_anime_meta_status(
                            canonical_uid,
                            status=st,
                            progress=prog,
                            total_episodes=total,
                            score=sc,
                            mal_id=mal_id,
                            anilist_id=anilist_id,
                            simkl_id=simkl_id,
                            tracker="anilist",
                        )
                        return _format_status_header({"status": st, "progress": prog, "total_episodes": total, "score": sc})
        except AnilistTokenInvalidError as e:
            logging.warning("AniList token invalid during on-demand lookup for user %s: %s", canonical_uid, e)
            handle_invalid_anilist_token(canonical_uid)
        except Exception as e:
            logging.debug("On-demand AniList status lookup skipped for user %s, anime %s: %s", user_id, anilist_id, e)

    return None


def build_next_airing_header(anilist_data: dict | None = None, mal_data: dict | None = None) -> str | None:
    if anilist_data:
        al_status = anilist_data.get("status")
        # Only check nextAiringEpisode if show is actively releasing or unreleased
        if al_status in ["RELEASING", "NOT_YET_RELEASED"] or not al_status:
            next_ep = anilist_data.get("nextAiringEpisode")
            if next_ep:
                ep_num = next_ep.get("episode")
                time_until = next_ep.get("timeUntilAiring")
                if time_until is not None and ep_num is not None and time_until > 0:
                    if time_until < 3600:
                        mins = max(1, time_until // 60)
                        time_str = f"in {mins}m"
                    elif time_until < 86400:
                        hours = max(1, time_until // 3600)
                        time_str = f"in {hours}h"
                    else:
                        days = max(1, time_until // 86400)
                        time_str = f"in {days}d"
                    return f"[Next Airing: Episode {ep_num} releases {time_str}]"

    if mal_data:
        mal_status = (mal_data.get("status") or "").lower().replace(" ", "_")
        # Strictly ignore historical broadcast slots for finished anime
        if mal_status in ["currently_airing", "not_yet_aired"]:
            broadcast = mal_data.get("broadcast") or {}
            day = broadcast.get("day") or broadcast.get("day_of_the_week")
            time_str = broadcast.get("time") or broadcast.get("start_time")
            if day and time_str:
                return f"[Next Airing: Broadcasts {day} at {time_str} JST]"

    return None


def build_filler_arc_header(
    anizp_data: dict | None,
    watched_progress: int = 0,
    mal_id: str | None = None,
) -> str | None:
    filler_eps_set = set()

    # 1. Check Jikan MongoDB cache first (primary authority for episode filler tags)
    if mal_id:
        try:
            from app.services.db import jikan_cache_collection

            docs = jikan_cache_collection.find({"mal_id": str(mal_id), "filler": True}, {"episode": 1})
            for doc in docs:
                ep = doc.get("episode")
                if isinstance(ep, int):
                    filler_eps_set.add(ep)
        except Exception as e:
            logging.debug("Could not query jikan_cache for filler arc header: %s", e)

    # 2. Check AniZip episodes if any provided
    if anizp_data:
        episodes = anizp_data.get("episodes") or {}
        for ep_key, ep_info in episodes.items():
            if isinstance(ep_info, dict) and (ep_info.get("isFiller") is True or ep_info.get("filler") is True):
                try:
                    filler_eps_set.add(int(ep_key))
                except ValueError:
                    pass

    if not filler_eps_set:
        return None

    filler_eps = sorted(filler_eps_set)

    ranges = []
    start = filler_eps[0]
    end = filler_eps[0]

    for ep in filler_eps[1:]:
        if ep == end + 1:
            end = ep
        else:
            ranges.append((start, end))
            start = ep
            end = ep
    ranges.append((start, end))

    range_strs = []
    for r_start, r_end in ranges:
        if r_start == r_end:
            range_strs.append(f"Ep {r_start}")
        else:
            range_strs.append(f"Episodes {r_start}–{r_end}")

    if not range_strs:
        return None

    target_ep = (watched_progress + 1) if (watched_progress is not None and watched_progress >= 0) else 1

    for r_start, r_end in ranges:
        if r_start <= target_ep <= r_end and (watched_progress > 0 or target_ep == 1):
            if r_start == r_end:
                return f"[Current Filler Episode: Ep {r_start}]"
            return f"[Current Filler Arc: Episodes {r_start}–{r_end}]"

    if watched_progress == 0:
        if len(range_strs) <= 3:
            if len(filler_eps) == 1:
                return f"[Filler Guide: {range_strs[0]} is non-canon filler]"
            return f"[Filler Guide: {', '.join(range_strs)} are non-canon fillers]"
        return None

    for r_start, r_end in ranges:
        if target_ep < r_start and (r_start - target_ep) <= 10:
            if r_start == r_end:
                return f"[Upcoming Filler Episode: Ep {r_start}]"
            return f"[Upcoming Filler Arc: Episodes {r_start}–{r_end}]"

    return None


def build_expired_trackers_notice(user: dict | None) -> str | None:
    if not user:
        return None

    now = time.time()
    grace_period = 7 * 86400  # 7 days in seconds
    expired_names = []

    trackers = [
        ("AniList", "anilist_token_expired", "anilist_expired_at"),
        ("MyAnimeList", "mal_token_expired", "mal_expired_at"),
        ("Simkl", "simkl_token_expired", "simkl_expired_at"),
    ]

    for name, flag_key, timestamp_key in trackers:
        if user.get(flag_key):
            expired_at = user.get(timestamp_key)
            if expired_at is None or (now - float(expired_at)) <= grace_period:
                expired_names.append(name)

    if not expired_names:
        return None

    if len(expired_names) == 1:
        trackers_str = expired_names[0]
        session_word = "session has"
    elif len(expired_names) == 2:
        trackers_str = f"{expired_names[0]} & {expired_names[1]}"
        session_word = "sessions have"
    else:
        trackers_str = f"{', '.join(expired_names[:-1])} & {expired_names[-1]}"
        session_word = "sessions have"

    return f"Your {trackers_str} {session_word} expired. You can re-login via the website."
