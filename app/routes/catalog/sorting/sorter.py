from functools import cmp_to_key

from app.routes.catalog.formatting import parse_iso_timestamp


def sort_watchlist_items(items, sort_by, sort_order, tracker_type, bulk_details=None):
    """
    Sorts watchlist items according to the specified field and direction.
    Supports score, last_updated, airing_date, year/release_date, episodes,
    progress, popularity, added_date/created_date, and title.
    Missing/zero values always sink to the end, with title as a secondary tie-breaker.
    """
    if not items or sort_by == "default":
        return items

    reverse = (sort_order == "desc")

    def extract_title(item):
        title = ""
        if not isinstance(item, dict):
            return ""
        if tracker_type == "mal":
            node = item.get("node") or {}
            title = node.get("title", "")
        elif tracker_type == "anilist":
            media = item.get("media") or {}
            title_obj = media.get("title") or {}
            title = (
                title_obj.get("userPreferred")
                or title_obj.get("english")
                or title_obj.get("romaji")
                or ""
            )
        elif tracker_type == "simkl":
            show_obj = (item.get("show") or item.get("anime") or item) if isinstance(item, dict) else {}
            if not isinstance(show_obj, dict):
                show_obj = {}
            title = show_obj.get("en_title") or show_obj.get("title", "")
        elif tracker_type == "combined":
            if item.get("anilist_item"):
                media = (item["anilist_item"].get("media") or {}) if isinstance(item["anilist_item"], dict) else {}
                title_obj = media.get("title") or {}
                title = (
                    title_obj.get("userPreferred")
                    or title_obj.get("english")
                    or title_obj.get("romaji")
                    or ""
                )
            if not title and item.get("mal_item"):
                node = (item["mal_item"].get("node") or {}) if isinstance(item["mal_item"], dict) else {}
                title = node.get("title", "")
            if not title and item.get("simkl_item"):
                s_item = item["simkl_item"]
                show_obj = (s_item.get("show") or s_item.get("anime") or s_item) if isinstance(s_item, dict) else {}
                if not isinstance(show_obj, dict):
                    show_obj = {}
                title = show_obj.get("en_title") or show_obj.get("title", "")
        if not title and isinstance(item, dict):
            title = str(item.get("name") or item.get("title") or "")
        return str(title or "").strip()

    def extract_numeric(item):
        if not isinstance(item, dict):
            return 0.0
        if sort_by == "score":
            score = 0
            if tracker_type == "mal":
                node = item.get("node") or {}
                status = node.get("my_list_status") or {}
                user_score = status.get("score", 0) or 0
                if user_score > 0:
                    score = user_score
                else:
                    global_score = node.get("mean", 0) or 0
                    if global_score > 0:
                        score = global_score
                    elif bulk_details:
                        mal_id = str(node.get("id") or "")
                        al_media = (bulk_details.get(mal_id) or {}) if mal_id else {}
                        score = (al_media.get("averageScore") or 0) / 10
            elif tracker_type == "anilist":
                user_score = item.get("score", 0) or 0
                if user_score > 10:
                    user_score = user_score / 10
                if user_score > 0:
                    score = user_score
                else:
                    media = item.get("media") or {}
                    score = (media.get("averageScore") or 0) / 10
            elif tracker_type == "simkl":
                user_score = item.get("user_rating") or item.get("rating") or 0
                if user_score > 0:
                    score = user_score
                elif bulk_details:
                    show_obj = item.get("show") or item.get("anime") or item
                    if not isinstance(show_obj, dict):
                        show_obj = {}
                    ids = show_obj.get("ids") or {}
                    mal_id = str(ids.get("mal") or "")
                    al_media = (bulk_details.get(mal_id) or {}) if mal_id else {}
                    score = (al_media.get("averageScore") or 0) / 10
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
                    al_bulk = (bulk_details.get(mal_id) or {}) if (bulk_details and mal_id) else {}
                    bulk_global = (al_bulk.get("averageScore") or 0) / 10
                    simkl_global = bulk_global
                    if mal_global == 0 and bulk_global > 0:
                        mal_global = bulk_global
                    global_scores = [mal_global, al_global, simkl_global]
                    rated_global_scores = [s for s in global_scores if s > 0]
                    score = sum(rated_global_scores) / len(rated_global_scores) if rated_global_scores else 0.0
            if not score and isinstance(item, dict):
                try:
                    score = float(item.get("score") or item.get("imdbRating") or 0)
                except Exception:
                    score = 0.0
            return float(score)

        elif sort_by == "last_updated":
            ts = 0
            if tracker_type == "mal":
                node = item.get("node") or {}
                status = node.get("my_list_status") or {}
                ts = parse_iso_timestamp(status.get("updated_at", ""))
            elif tracker_type == "anilist":
                ts = item.get("updatedAt") or 0
            elif tracker_type == "simkl":
                ts = parse_iso_timestamp(item.get("last_watched_at"))
            elif tracker_type == "combined":
                mal_item = item.get("mal_item") or {}
                mal_node = mal_item.get("node") or {} if isinstance(mal_item, dict) else {}
                mal_status = mal_node.get("my_list_status") or {}
                mal_ts = parse_iso_timestamp(mal_status.get("updated_at", ""))
                anilist_item = item.get("anilist_item") or {}
                al_ts = anilist_item.get("updatedAt") or 0
                simkl_item = item.get("simkl_item") or {}
                simkl_ts = parse_iso_timestamp(simkl_item.get("last_watched_at"))
                ts = max(mal_ts, al_ts, simkl_ts)
            if not ts and isinstance(item, dict):
                ts = int(item.get("updated_at") or 0)
            return ts

        elif sort_by == "airing_date":
            airing_at = None
            if tracker_type == "mal":
                node = item.get("node") or {}
                mal_id = str(node.get("id", ""))
                al_media = (bulk_details.get(mal_id) or {}) if (bulk_details and mal_id) else {}
                next_ep = al_media.get("nextAiringEpisode") if isinstance(al_media, dict) else None
                airing_at = next_ep.get("airingAt") if isinstance(next_ep, dict) else None
            elif tracker_type == "anilist":
                media = item.get("media") or {}
                next_ep = media.get("nextAiringEpisode") if isinstance(media, dict) else None
                airing_at = next_ep.get("airingAt") if isinstance(next_ep, dict) else None
            elif tracker_type == "simkl":
                show_obj = item.get("show") or item.get("anime") or item
                if not isinstance(show_obj, dict):
                    show_obj = {}
                ids = show_obj.get("ids") or {}
                mal_id = str(ids.get("mal") or "")
                al_media = (bulk_details.get(mal_id) or {}) if (bulk_details and mal_id) else {}
                next_ep = al_media.get("nextAiringEpisode") if isinstance(al_media, dict) else None
                airing_at = next_ep.get("airingAt") if isinstance(next_ep, dict) else None
            elif tracker_type == "combined":
                if item.get("anilist_item"):
                    al_m = (item["anilist_item"].get("media") or {}) if isinstance(item["anilist_item"], dict) else {}
                    next_ep = al_m.get("nextAiringEpisode") if isinstance(al_m, dict) else None
                    airing_at = next_ep.get("airingAt") if isinstance(next_ep, dict) else None
                if not airing_at and item.get("mal_id") and bulk_details:
                    al_media = bulk_details.get(item["mal_id"]) or {}
                    next_ep = al_media.get("nextAiringEpisode") if isinstance(al_media, dict) else None
                    airing_at = next_ep.get("airingAt") if isinstance(next_ep, dict) else None
            if airing_at is None and isinstance(item, dict):
                airing_at = item.get("airing_at")
            return int(airing_at or 0)

        elif sort_by in ["year", "release_date"]:
            yr = 0
            if tracker_type == "mal":
                node = item.get("node") or {}
                start_season = node.get("start_season") or {}
                yr = int(start_season.get("year", 0) or 0)
                if not yr:
                    d_str = str(node.get("start_date") or "")
                    if len(d_str) >= 4 and d_str[:4].isdigit():
                        yr = int(d_str[:4])
            elif tracker_type == "anilist":
                media = item.get("media") or {}
                start_date = media.get("startDate") or {}
                yr = int(start_date.get("year", 0) or media.get("seasonYear", 0) or 0)
            elif tracker_type == "simkl":
                show_obj = item.get("show") or item.get("anime") or item
                if not isinstance(show_obj, dict):
                    show_obj = {}
                yr = int(show_obj.get("year", 0) or 0)
            elif tracker_type == "combined":
                if item.get("anilist_item"):
                    al_m = (item["anilist_item"].get("media") or {}) if isinstance(item["anilist_item"], dict) else {}
                    start_date = al_m.get("startDate") or {}
                    yr = int(start_date.get("year", 0) or al_m.get("seasonYear", 0) or 0)
                if not yr and item.get("mal_item"):
                    mal_node = (item["mal_item"].get("node") or {}) if isinstance(item["mal_item"], dict) else {}
                    start_season = mal_node.get("start_season") or {}
                    yr = int(start_season.get("year", 0) or 0)
                    if not yr:
                        d_str = str(mal_node.get("start_date") or "")
                        if len(d_str) >= 4 and d_str[:4].isdigit():
                            yr = int(d_str[:4])
                if not yr and item.get("simkl_item"):
                    s_item = item["simkl_item"]
                    show_obj = (s_item.get("show") or s_item.get("anime") or s_item) if isinstance(s_item, dict) else {}
                    if not isinstance(show_obj, dict):
                        show_obj = {}
                    yr = int(show_obj.get("year", 0) or 0)
            if isinstance(item, dict):
                d_val = item.get("date_val")
                if d_val:
                    try:
                        return int(d_val)
                    except Exception:
                        pass
                rel_d = str(item.get("release_date") or item.get("start_date") or "")
                if rel_d and len(rel_d) >= 4 and rel_d[:4].isdigit():
                    parts = rel_d.split("-")
                    y = int(parts[0])
                    mo = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
                    da = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 0
                    return y * 10000 + mo * 100 + da
            if not yr and isinstance(item, dict):
                try:
                    yr = int(item.get("year") or item.get("releaseInfo") or 0)
                except Exception:
                    yr = 0
            if yr > 0:
                return yr * 10000
            return yr

        elif sort_by in ["episodes", "total_episodes"]:
            eps = 0
            if tracker_type == "mal":
                node = item.get("node") or {}
                eps = int(node.get("num_episodes", 0) or 0)
            elif tracker_type == "anilist":
                media = item.get("media") or {}
                eps = int(media.get("episodes", 0) or 0)
                if not eps:
                    next_ep = media.get("nextAiringEpisode") or {}
                    if next_ep.get("episode"):
                        eps = max(0, int(next_ep["episode"]) - 1)
            elif tracker_type == "simkl":
                show_obj = item.get("show") or item.get("anime") or item
                if not isinstance(show_obj, dict):
                    show_obj = {}
                eps = int(show_obj.get("total_episodes", 0) or show_obj.get("episodes_count", 0) or show_obj.get("num_episodes", 0) or item.get("total_episodes_count", 0) or 0)
            elif tracker_type == "combined":
                if item.get("anilist_item"):
                    al_m = (item["anilist_item"].get("media") or {}) if isinstance(item["anilist_item"], dict) else {}
                    eps = int(al_m.get("episodes", 0) or 0)
                    if not eps:
                        next_ep = al_m.get("nextAiringEpisode") or {}
                        if next_ep.get("episode"):
                            eps = max(0, int(next_ep["episode"]) - 1)
                if not eps and item.get("mal_item"):
                    mal_node = (item["mal_item"].get("node") or {}) if isinstance(item["mal_item"], dict) else {}
                    eps = int(mal_node.get("num_episodes", 0) or 0)
                if not eps and item.get("simkl_item"):
                    s_item = item["simkl_item"]
                    show_obj = (s_item.get("show") or s_item.get("anime") or s_item) if isinstance(s_item, dict) else {}
                    if not isinstance(show_obj, dict):
                        show_obj = {}
                    eps = int(s_item.get("total_episodes_count") or show_obj.get("episodes_count") or show_obj.get("num_episodes") or show_obj.get("total_episodes", 0) or 0)
            if not eps and isinstance(item, dict):
                try:
                    eps = int(item.get("episodes") or item.get("totalEpisodes") or 0)
                except Exception:
                    eps = 0
                if not eps and item.get("next_episode"):
                    try:
                        eps = max(0, int(item.get("next_episode")) - 1)
                    except Exception:
                        pass
            return eps

        elif sort_by == "progress":
            prog = 0
            if tracker_type == "mal":
                node = item.get("node") or {}
                status = node.get("my_list_status") or {}
                prog = int(status.get("num_episodes_watched", 0) or 0)
            elif tracker_type == "anilist":
                prog = int(item.get("progress", 0) or 0)
            elif tracker_type == "simkl":
                prog = int(item.get("user_episodes_watched", 0) or item.get("watched_episodes_count", 0) or item.get("episodes_watched", 0) or item.get("progress", 0) or 0)
            elif tracker_type == "combined":
                al_p = int((item.get("anilist_item") or {}).get("progress", 0) or 0)
                mal_node = (item.get("mal_item", {}).get("node") or {}) if isinstance(item.get("mal_item"), dict) else {}
                mal_status = mal_node.get("my_list_status") or {}
                mal_p = int(mal_status.get("num_episodes_watched", 0) or 0)
                simkl_item = item.get("simkl_item") or {}
                simkl_p = int(
                    simkl_item.get("watched_episodes_count")
                    or simkl_item.get("episodes_watched")
                    or simkl_item.get("progress", 0)
                    or 0
                )
                prog = max(al_p, mal_p, simkl_p)
            if not prog and isinstance(item, dict):
                try:
                    prog = float(item.get("progress") or 0)
                except Exception:
                    prog = 0.0
            return prog

        elif sort_by == "popularity":
            pop = 0
            if tracker_type == "mal":
                node = item.get("node") or {}
                pop = int(node.get("num_list_users", 0) or node.get("popularity", 0) or 0)
            elif tracker_type == "anilist":
                media = item.get("media") or {}
                pop = int(media.get("popularity", 0) or 0)
            elif tracker_type == "simkl":
                show_obj = item.get("show") or item.get("anime") or item
                if not isinstance(show_obj, dict):
                    show_obj = {}
                pop = int(show_obj.get("users_count") or show_obj.get("user_count") or show_obj.get("watchers") or 0)
            elif tracker_type == "combined":
                al_media = (item.get("anilist_item", {}).get("media") or {}) if isinstance(item.get("anilist_item"), dict) else {}
                al_p = int(al_media.get("popularity", 0) or 0)
                mal_node = (item.get("mal_item", {}).get("node") or {}) if isinstance(item.get("mal_item"), dict) else {}
                mal_p = int(mal_node.get("num_list_users", 0) or 0)
                simkl_item = item.get("simkl_item") or {}
                show_obj = (simkl_item.get("show") or simkl_item.get("anime") or simkl_item) if isinstance(simkl_item, dict) else {}
                if not isinstance(show_obj, dict):
                    show_obj = {}
                simkl_pop = int(show_obj.get("users_count") or show_obj.get("user_count") or show_obj.get("watchers") or 0)
                pop = max(al_p, mal_p, simkl_pop)
            if not pop and isinstance(item, dict):
                try:
                    pop = float(item.get("popularity") or 0)
                except Exception:
                    pop = 0.0
            return pop

        elif sort_by in ["added_date", "created_date"]:
            ts = 0
            if tracker_type == "mal":
                node = item.get("node") or {}
                status = node.get("my_list_status") or {}
                ts = parse_iso_timestamp(status.get("updated_at", ""))
            elif tracker_type == "anilist":
                ts = item.get("createdAt") or item.get("updatedAt") or 0
            elif tracker_type == "simkl":
                ts = parse_iso_timestamp(item.get("added_to_watchlist_at") or item.get("last_watched_at"))
            elif tracker_type == "combined":
                mal_node = (item.get("mal_item", {}).get("node") or {}) if isinstance(item.get("mal_item"), dict) else {}
                mal_status = mal_node.get("my_list_status") or {}
                mal_ts = parse_iso_timestamp(mal_status.get("updated_at", ""))
                anilist_item = item.get("anilist_item") or {}
                al_ts = anilist_item.get("createdAt") or anilist_item.get("updatedAt") or 0
                simkl_item = item.get("simkl_item") or {}
                simkl_ts = parse_iso_timestamp(
                    simkl_item.get("added_to_watchlist_at")
                    or simkl_item.get("last_watched_at")
                    or ""
                )
                ts = max(mal_ts, al_ts, simkl_ts)
            return ts

        return 0

    def compare_items(a, b):
        if sort_by == "title":
            title_a = extract_title(a).lower()
            title_b = extract_title(b).lower()
            diff = (title_a > title_b) - (title_a < title_b)
            return -diff if reverse else diff

        val_a = extract_numeric(a)
        val_b = extract_numeric(b)

        a_missing = (val_a is None or val_a <= 0)
        b_missing = (val_b is None or val_b <= 0)

        # Missing/zero values ALWAYS go to the very end regardless of Asc or Desc
        if a_missing and b_missing:
            title_a = extract_title(a).lower()
            title_b = extract_title(b).lower()
            return (title_a > title_b) - (title_a < title_b)
        if a_missing:
            return 1
        if b_missing:
            return -1

        diff = (val_a > val_b) - (val_a < val_b)
        if diff != 0:
            return -diff if reverse else diff

        title_a = extract_title(a).lower()
        title_b = extract_title(b).lower()
        return (title_a > title_b) - (title_a < title_b)

    return sorted(items, key=cmp_to_key(compare_items))
