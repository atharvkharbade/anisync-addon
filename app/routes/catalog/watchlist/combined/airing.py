import datetime
import time


def filter_airing_candidates(combined_items: list[dict]) -> tuple[list[str], list[str]]:
    """
    Identifies combined items that may be currently releasing or airing,
    returning lists of (airing_mal_ids, airing_al_ids) for targeted bulk metadata fetching.
    """
    airing_mal_ids = []
    airing_al_ids = []

    for item in combined_items:
        mal_id = item.get("mal_id")
        al_id = item.get("anilist_id")
        if not mal_id and not al_id:
            continue

        is_candidate = False
        if item.get("anilist_item"):
            al_media = (item["anilist_item"].get("media") or {}) if isinstance(item["anilist_item"], dict) else {}
            al_status_str = al_media.get("status", "")
            is_candidate = al_status_str in ["RELEASING", "NOT_YET_RELEASED"] or not al_status_str
        elif item.get("mal_item"):
            mal_node = (item["mal_item"].get("node") or {}) if isinstance(item["mal_item"], dict) else {}
            mal_status_str = mal_node.get("status", "")
            is_candidate = mal_status_str in ["currently_airing", "not_yet_aired"] or not mal_status_str
        elif item.get("simkl_item"):
            s_item = item["simkl_item"] if isinstance(item.get("simkl_item"), dict) else {}
            show_obj = s_item.get("show") or s_item.get("anime") or s_item
            simkl_status_str = (show_obj.get("status") or "").lower()
            is_candidate = simkl_status_str not in ["ended", "completed", "canceled", "cancelled"]
        else:
            is_candidate = True

        if is_candidate:
            if mal_id:
                airing_mal_ids.append(mal_id)
            if al_id:
                airing_al_ids.append(al_id)

    return airing_mal_ids, airing_al_ids


def compute_comb_flags(
    item: dict,
    bulk_details: dict = None,
    current_time: int = None,
    enable_new_ep_badge: bool = True,
    sort_by_new_ep: bool = False,
) -> tuple[bool, int, int]:
    """
    Computes airing status, latest aired episode timestamp, and next airing timestamp for a combined item.
    Returns: (is_new_ep, latest_aired_at, next_airing_at)
    """
    if current_time is None:
        current_time = int(time.time())

    is_new_ep = False
    latest_aired_at = 0
    next_airing_at = 2**31 - 1

    if not isinstance(item, dict):
        return is_new_ep, latest_aired_at, next_airing_at

    # Check status/airing state first
    is_airing = False
    al_media = {}
    if item.get("anilist_item"):
        al_media = (item["anilist_item"].get("media") or {}) if isinstance(item["anilist_item"], dict) else {}
        al_status_str = al_media.get("status", "")
        is_airing = al_status_str in ["RELEASING", "NOT_YET_RELEASED"]
    elif item.get("mal_item"):
        mal_node = (item["mal_item"].get("node") or {}) if isinstance(item["mal_item"], dict) else {}
        mal_status_str = mal_node.get("status", "")
        is_airing = mal_status_str in ["currently_airing", "not_yet_aired"]
        if item.get("mal_id") and bulk_details:
            al_media = bulk_details.get(item["mal_id"]) or {}
    elif item.get("simkl_item"):
        s_item = item["simkl_item"]
        show_obj = (s_item.get("show") or s_item.get("anime") or s_item) if isinstance(s_item, dict) else {}
        if not isinstance(show_obj, dict):
            show_obj = {}
        simkl_status_str = show_obj.get("status", "")
        if item.get("mal_id") and bulk_details:
            al_media = bulk_details.get(item["mal_id"]) or {}
        if al_media:
            al_status_str = al_media.get("status", "")
            is_airing = al_status_str in ["RELEASING", "NOT_YET_RELEASED"]
        else:
            is_airing = simkl_status_str in ["airing", "currently airing"]

    # Extract progress
    progress = 0
    if item.get("mal_item"):
        mal_node = (item["mal_item"].get("node") or {}) if isinstance(item["mal_item"], dict) else {}
        progress = max(progress, (mal_node.get("my_list_status") or {}).get("num_episodes_watched", 0) or 0)
    if item.get("anilist_item"):
        progress = max(progress, item["anilist_item"].get("progress", 0) or 0)
    if item.get("simkl_item"):
        s_item = item["simkl_item"]
        simkl_progress = (
            s_item.get("watched_episodes_count")
            or s_item.get("episodes_watched")
            or s_item.get("progress")
            or 0
        )
        progress = max(progress, simkl_progress)

    # Extract total episodes
    total = 0
    if item.get("mal_item"):
        mal_node = (item["mal_item"].get("node") or {}) if isinstance(item["mal_item"], dict) else {}
        total = max(total, mal_node.get("num_episodes", 0) or 0)
    if item.get("anilist_item"):
        al_m = (item["anilist_item"].get("media") or {}) if isinstance(item["anilist_item"], dict) else {}
        total = max(total, al_m.get("episodes") or 0)
    if item.get("simkl_item"):
        s_item = item["simkl_item"]
        show_obj = (s_item.get("show") or s_item.get("anime") or s_item) if isinstance(s_item, dict) else {}
        if not isinstance(show_obj, dict):
            show_obj = {}
        simkl_total = show_obj.get("episodes_count") or show_obj.get("num_episodes") or 0
        total = max(total, simkl_total)

    # Airing calculations using AniList data
    next_ep_num = None
    next_ep_airing_at = None

    if item.get("anilist_item"):
        al_m = (item["anilist_item"].get("media") or {}) if isinstance(item["anilist_item"], dict) else {}
        next_ep = al_m.get("nextAiringEpisode")
        if next_ep and isinstance(next_ep, dict):
            next_ep_num = next_ep.get("episode")
            next_ep_airing_at = next_ep.get("airingAt")
    elif item.get("mal_id"):
        if not al_media and bulk_details:
            al_media = bulk_details.get(item["mal_id"]) or {}
        next_ep = al_media.get("nextAiringEpisode") if isinstance(al_media, dict) else None
        if next_ep and isinstance(next_ep, dict):
            next_ep_num = next_ep.get("episode")
            next_ep_airing_at = next_ep.get("airingAt")

    latest_aired_num = 0
    if next_ep_num and next_ep_airing_at:
        latest_aired_num = next_ep_num - 1
        latest_aired_at = next_ep_airing_at - 604800
        next_airing_at = next_ep_airing_at

    # Check for recently finished show
    recently_finished = False
    al_status_cur = al_media.get("status") if isinstance(al_media, dict) else ""
    if al_status_cur == "FINISHED":
        end_date = al_media.get("endDate") if isinstance(al_media, dict) else None
        total_eps = (al_media.get("episodes") if isinstance(al_media, dict) else None) or total
        if total_eps and isinstance(end_date, dict):
            y = end_date.get("year")
            m = end_date.get("month") or 1
            d = end_date.get("day") or 1
            if y:
                try:
                    dt = datetime.datetime(y, m, d, tzinfo=datetime.timezone.utc)
                    end_ts = int(dt.timestamp())
                    if (current_time - end_ts) <= (604800 + 86400) and progress < total_eps:
                        recently_finished = True
                        latest_aired_num = total_eps
                        latest_aired_at = end_ts
                except Exception:
                    pass

    # Fallback to MAL's own end_date if we couldn't determine from AniList
    if not recently_finished and item.get("mal_item"):
        mal_node = (item["mal_item"].get("node") or {}) if isinstance(item["mal_item"], dict) else {}
        if mal_node.get("status") == "finished_airing":
            mal_end_date = mal_node.get("end_date")
            mal_total = mal_node.get("num_episodes", 0) or total
            if mal_end_date and mal_total > 0:
                try:
                    parts = [int(p) for p in mal_end_date.split("-")]
                    if len(parts) == 3:
                        dt = datetime.datetime(parts[0], parts[1], parts[2], tzinfo=datetime.timezone.utc)
                        end_ts = int(dt.timestamp())
                        if (current_time - end_ts) <= (604800 + 86400) and progress < mal_total:
                            recently_finished = True
                            latest_aired_num = mal_total
                            latest_aired_at = end_ts
                except Exception:
                    pass

    # Detect newly released movie (release date in startDate / start_date)
    al_format = al_media.get("format") if isinstance(al_media, dict) else ""
    is_movie = (al_format == "MOVIE") or (total == 1)
    if not is_movie and item.get("mal_item"):
        mal_node = (item["mal_item"].get("node") or {}) if isinstance(item["mal_item"], dict) else {}
        if (mal_node.get("media_type") or "").lower() == "movie" or mal_node.get("num_episodes") == 1:
            is_movie = True
    if not is_movie and item.get("simkl_item"):
        s_item = item["simkl_item"]
        show_obj = (s_item.get("show") or s_item.get("anime") or s_item) if isinstance(s_item, dict) else {}
        if (show_obj.get("anime_type") or show_obj.get("type") or "").lower() == "movie" or show_obj.get("episodes_count") == 1:
            is_movie = True

    if is_movie and not recently_finished and progress == 0:
        # Check AniList startDate or endDate
        start_date = al_media.get("startDate") if isinstance(al_media, dict) else None
        if not start_date and item.get("anilist_item"):
            al_m = (item["anilist_item"].get("media") or {}) if isinstance(item["anilist_item"], dict) else {}
            start_date = al_m.get("startDate") or al_m.get("endDate")
        if isinstance(start_date, dict):
            y = start_date.get("year")
            m = start_date.get("month") or 1
            d = start_date.get("day") or 1
            if y:
                try:
                    dt = datetime.datetime(y, m, d, tzinfo=datetime.timezone.utc)
                    rel_ts = int(dt.timestamp())
                    if 0 <= (current_time - rel_ts) <= (604800 + 86400):
                        recently_finished = True
                        latest_aired_num = 1
                        latest_aired_at = rel_ts
                except Exception:
                    pass

        # Check MAL start_date or end_date
        if not recently_finished and item.get("mal_item"):
            mal_node = (item["mal_item"].get("node") or {}) if isinstance(item["mal_item"], dict) else {}
            mal_date_str = mal_node.get("start_date") or mal_node.get("end_date")
            if mal_date_str:
                try:
                    parts = [int(p) for p in mal_date_str.split("-")]
                    if len(parts) >= 1:
                        y = parts[0]
                        m = parts[1] if len(parts) > 1 else 1
                        d = parts[2] if len(parts) > 2 else 1
                        dt = datetime.datetime(y, m, d, tzinfo=datetime.timezone.utc)
                        rel_ts = int(dt.timestamp())
                        if 0 <= (current_time - rel_ts) <= (604800 + 86400):
                            recently_finished = True
                            latest_aired_num = 1
                            latest_aired_at = rel_ts
                except Exception:
                    pass

        # Check Simkl release_date
        if not recently_finished and item.get("simkl_item"):
            s_item = item["simkl_item"]
            show_obj = (s_item.get("show") or s_item.get("anime") or s_item) if isinstance(s_item, dict) else {}
            simkl_rel = show_obj.get("release_date")
            if simkl_rel:
                try:
                    parts = [int(p) for p in simkl_rel.split("-")]
                    if len(parts) >= 1:
                        y = parts[0]
                        m = parts[1] if len(parts) > 1 else 1
                        d = parts[2] if len(parts) > 2 else 1
                        dt = datetime.datetime(y, m, d, tzinfo=datetime.timezone.utc)
                        rel_ts = int(dt.timestamp())
                        if 0 <= (current_time - rel_ts) <= (604800 + 86400):
                            recently_finished = True
                            latest_aired_num = 1
                            latest_aired_at = rel_ts
                except Exception:
                    pass

    if (
        (is_airing or recently_finished)
        and (enable_new_ep_badge or sort_by_new_ep)
        and latest_aired_num > 0
        and progress < latest_aired_num
    ):
        time_since_air = current_time - latest_aired_at
        if time_since_air <= 604800 or recently_finished:
            is_new_ep = True
            if recently_finished and next_airing_at == 2**31 - 1:
                next_airing_at = latest_aired_at

    return is_new_ep, latest_aired_at, next_airing_at
