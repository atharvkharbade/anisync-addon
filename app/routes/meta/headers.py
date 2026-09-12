import logging
import time


def build_user_status_header(user_id: str, mal_id: str | None, anilist_id: str | None, simkl_id: str | None) -> str | None:
    from app.services.db import get_user_anime_meta_status

    status_info = get_user_anime_meta_status(user_id, mal_id=mal_id, anilist_id=anilist_id, simkl_id=simkl_id)
    if not status_info:
        return None

    status = (status_info.get("status") or "").lower()
    progress = status_info.get("progress") or 0
    total_eps = status_info.get("total_episodes") or 0
    score = status_info.get("score") or 0

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
        parts.append(f"Your Rating: {score}/10")

    return f"[{' • '.join(parts)}]"


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
