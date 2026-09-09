from app.routes.catalog.formatting import parse_iso_timestamp


def extract_item_metadata_fields(item, tracker_type, bulk_details=None):
    """
    Extracts common sortable metadata (score, episodes, year, airing_at, updated_at) from a watchlist item.
    Returns: dict with episodes (int), score (float), year (int), airing_at (int/None), updated_at (int)
    """
    score = 0.0
    episodes = 0
    year = 0
    airing_at = None
    updated_at = 0

    if not isinstance(item, dict):
        return {
            "score": 0.0,
            "episodes": 0,
            "year": 0,
            "airing_at": None,
            "updated_at": 0,
        }

    if tracker_type == "mal":
        node = item.get("node") or {}
        status = node.get("my_list_status") or {}
        score = float(status.get("score") or node.get("mean") or 0)
        episodes = int(node.get("num_episodes") or 0)
        start_season = node.get("start_season") or {}
        year = int(start_season.get("year") or 0)
        if not year:
            d_str = str(node.get("start_date") or "")
            if len(d_str) >= 4 and d_str[:4].isdigit():
                year = int(d_str[:4])
        updated_at = parse_iso_timestamp(status.get("updated_at", ""))
        mal_id = str(node.get("id") or "")
        al_media = (bulk_details.get(mal_id) or {}) if (bulk_details and mal_id) else {}
        next_ep = al_media.get("nextAiringEpisode") if isinstance(al_media, dict) else None
        airing_at = next_ep.get("airingAt") if isinstance(next_ep, dict) else None

    elif tracker_type == "anilist":
        media = item.get("media") or {}
        score = float(item.get("score") or 0)
        if score > 10:
            score = score / 10
        if score == 0:
            score = float(media.get("averageScore") or 0) / 10
        episodes = int(media.get("episodes") or 0)
        start_date = media.get("startDate") or {}
        year = int(start_date.get("year") or media.get("seasonYear") or 0)
        updated_at = item.get("updatedAt") or 0
        next_ep = media.get("nextAiringEpisode") if isinstance(media, dict) else None
        airing_at = next_ep.get("airingAt") if isinstance(next_ep, dict) else None

    elif tracker_type == "simkl":
        show_obj = (item.get("show") or item.get("anime") or item) if isinstance(item, dict) else {}
        if not isinstance(show_obj, dict):
            show_obj = {}
        score = float(item.get("user_rating") or item.get("rating") or 0)
        episodes = int(item.get("total_episodes_count") or show_obj.get("episodes_count") or show_obj.get("num_episodes") or show_obj.get("total_episodes") or 0)
        year = int(show_obj.get("year") or 0)
        updated_at = parse_iso_timestamp(item.get("last_watched_at"))
        ids = show_obj.get("ids") or {}
        mal_id = str(ids.get("mal") or "")
        al_id = str(ids.get("anilist") or "")
        al_media = (((bulk_details.get(mal_id) if mal_id else None) or (bulk_details.get(al_id) if al_id else None)) or {}) if bulk_details else {}
        if not isinstance(al_media, dict):
            al_media = {}
        next_ep = al_media.get("nextAiringEpisode") if isinstance(al_media, dict) else None
        airing_at = next_ep.get("airingAt") if isinstance(next_ep, dict) else None

    elif tracker_type == "combined":
        mal_item = item.get("mal_item") or {}
        mal_node = mal_item.get("node") or {} if isinstance(mal_item, dict) else {}
        mal_status = mal_node.get("my_list_status") or {}
        mal_user = mal_status.get("score", 0) or 0

        anilist_item = item.get("anilist_item") or {}
        al_media = anilist_item.get("media") or {} if isinstance(anilist_item, dict) else {}
        al_user = anilist_item.get("score", 0) or 0
        if al_user > 10:
            al_user = al_user / 10

        simkl_item = item.get("simkl_item") or {}
        simkl_user = simkl_item.get("user_rating") or simkl_item.get("rating") or 0

        user_scores = [mal_user, al_user, simkl_user]
        rated_user_scores = [s for s in user_scores if s > 0]
        if rated_user_scores:
            score = sum(rated_user_scores) / len(rated_user_scores)
        else:
            mal_global = mal_node.get("mean", 0) or 0
            al_global = (al_media.get("averageScore", 0) or 0) / 10
            mal_id = item.get("mal_id")
            bulk_m = (bulk_details.get(mal_id) or {}) if (bulk_details and mal_id) else {}
            bulk_global = (bulk_m.get("averageScore") or 0) / 10
            global_scores = [mal_global, al_global, bulk_global]
            rated_globals = [s for s in global_scores if s > 0]
            score = sum(rated_globals) / len(rated_globals) if rated_globals else 0.0

        if anilist_item:
            episodes = int(al_media.get("episodes", 0) or 0)
        if not episodes and mal_item:
            episodes = int(mal_node.get("num_episodes", 0) or 0)
        if not episodes and simkl_item:
            show_obj = (simkl_item.get("show") or simkl_item.get("anime") or simkl_item) if isinstance(simkl_item, dict) else {}
            if not isinstance(show_obj, dict):
                show_obj = {}
            episodes = int(simkl_item.get("total_episodes_count") or show_obj.get("episodes_count") or show_obj.get("num_episodes") or show_obj.get("total_episodes", 0) or 0)

        if anilist_item:
            start_date = al_media.get("startDate") or {}
            year = int(start_date.get("year", 0) or al_media.get("seasonYear", 0) or 0)
        if not year and mal_item:
            start_season = mal_node.get("start_season") or {}
            year = int(start_season.get("year", 0) or 0)
            if not year:
                d_str = str(mal_node.get("start_date") or "")
                if len(d_str) >= 4 and d_str[:4].isdigit():
                    year = int(d_str[:4])
        if not year and simkl_item:
            show_obj = (simkl_item.get("show") or simkl_item.get("anime") or simkl_item) if isinstance(simkl_item, dict) else {}
            if not isinstance(show_obj, dict):
                show_obj = {}
            year = int(show_obj.get("year", 0) or 0)

        mal_ts = parse_iso_timestamp(mal_status.get("updated_at", "")) if mal_item else 0
        al_ts = anilist_item.get("updatedAt") or 0 if anilist_item else 0
        simkl_ts = parse_iso_timestamp(simkl_item.get("last_watched_at")) if simkl_item else 0
        updated_at = max(mal_ts, al_ts, simkl_ts)

        if anilist_item:
            next_ep = al_media.get("nextAiringEpisode") if isinstance(al_media, dict) else None
            airing_at = next_ep.get("airingAt") if isinstance(next_ep, dict) else None
        if not airing_at and item.get("mal_id") and bulk_details:
            bulk_m = bulk_details.get(item["mal_id"]) or {}
            next_ep = bulk_m.get("nextAiringEpisode") if isinstance(bulk_m, dict) else None
            airing_at = next_ep.get("airingAt") if isinstance(next_ep, dict) else None

    return {
        "score": round(float(score), 1),
        "episodes": int(episodes),
        "year": int(year),
        "airing_at": airing_at,
        "updated_at": updated_at,
    }
