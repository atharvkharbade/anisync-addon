from datetime import UTC, datetime, timedelta
import logging

from .connection import db


def _resolve_user_cache_uids(user_id: str) -> list[str]:
    """Resolve all known user ID aliases (uid, manifest_token) for cache lookups."""
    if not user_id:
        return []
    uids = {str(user_id)}
    try:
        from .users import get_user

        user = get_user(str(user_id))
        if user:
            if user.get("uid"):
                uids.add(str(user["uid"]))
            if user.get("manifest_token"):
                uids.add(str(user["manifest_token"]))
    except Exception as e:
        logging.warning("Error resolving user cache uids for %s: %s", user_id, e)
    return list(uids)


def invalidate_user_watchlist_cache(user_id: str):
    """Delete all cached watchlist documents for a specific user, including manifest_token aliases."""
    try:
        uids = _resolve_user_cache_uids(user_id)
        if not uids:
            return
        db.get_collection("user_watchlist_cache").delete_many({"uid": {"$in": uids}})
        db.get_collection("user_anime_status_cache").delete_many({"uid": {"$in": uids}})
        logging.info("Invalidated watchlist cache for uids %s", uids)
    except Exception as e:
        logging.error("Failed to invalidate watchlist cache for user %s: %s", user_id, e)


def get_user_watch_progress(
    user_id: str, mal_id: str | None = None, anilist_id: str | None = None, simkl_id: str | None = None
) -> int:
    """Find the user's maximum watch progress (watched episode count) across cached watchlists."""
    uids = _resolve_user_cache_uids(user_id)
    if not uids:
        return 0

    max_progress = 0
    try:
        cache_col = db.get_collection("user_watchlist_cache")
        docs = list(cache_col.find({"uid": {"$in": uids}}))

        mal_str = str(mal_id) if mal_id else None
        anilist_str = str(anilist_id) if anilist_id else None
        simkl_str = str(simkl_id) if simkl_id else None

        for doc in docs:
            tracker = doc.get("tracker")
            data = doc.get("data")
            if not data:
                continue

            if tracker == "mal":
                for item in data:
                    node = item.get("node") or {}
                    nid = str(node.get("id") or "")
                    if nid and mal_str and nid == mal_str:
                        status_obj = node.get("my_list_status") or {}
                        max_progress = max(max_progress, status_obj.get("num_episodes_watched") or 0)

            elif tracker == "anilist":
                lists = data.get("lists") or []
                for lst in lists:
                    entries = lst.get("entries") or []
                    for entry in entries:
                        media = entry.get("media") or {}
                        al_id = str(media.get("id") or "")
                        al_mal_id = str(media.get("idMal") or "")
                        if (anilist_str and al_id == anilist_str) or (mal_str and al_mal_id == mal_str):
                            max_progress = max(max_progress, entry.get("progress") or 0)

            elif tracker == "simkl":
                for item in data:
                    show_obj = item.get("show") or item.get("anime") or item
                    ids = show_obj.get("ids") or {}
                    s_mal = str(ids.get("mal") or "")
                    s_al = str(ids.get("anilist") or "")
                    s_simkl = str(ids.get("simkl") or "")

                    match = False
                    if mal_str and s_mal and s_mal == mal_str:
                        match = True
                    elif anilist_str and s_al and s_al == anilist_str:
                        match = True
                    elif simkl_str and s_simkl and s_simkl == simkl_str:
                        match = True

                    if match:
                        prog = (
                            item.get("watched_episodes_count")
                            or item.get("episodes_watched")
                            or item.get("progress")
                            or 0
                        )
                        max_progress = max(max_progress, prog)

    except Exception as e:
        logging.error("Failed to query watch progress for user %s: %s", user_id, e)

    return max_progress


def get_user_anime_meta_status(
    user_id: str, mal_id: str | None = None, anilist_id: str | None = None, simkl_id: str | None = None
) -> dict | None:
    """Find the user's watch status, progress, total_episodes, and personal score across cached watchlists."""
    if not user_id:
        return None

    try:
        uids = _resolve_user_cache_uids(user_id)
        if not uids:
            return None

        mal_str = str(mal_id) if mal_id else None
        anilist_str = str(anilist_id) if anilist_id else None
        simkl_str = str(simkl_id) if simkl_id else None

        # 1. First check dedicated on-demand single-item cache
        meta_col = db.get_collection("user_anime_status_cache")
        query_or = []
        if mal_str:
            query_or.append({"mal_id": mal_str})
        if anilist_str:
            query_or.append({"anilist_id": anilist_str})
        if simkl_str:
            query_or.append({"simkl_id": simkl_str})

        if query_or:
            cached_meta = meta_col.find_one({"uid": {"$in": uids}, "$or": query_or})
            if cached_meta and isinstance(cached_meta, dict) and cached_meta.get("status"):
                score = cached_meta.get("score") or 0
                if isinstance(score, (int, float)) and score > 10:
                    score = round(score / 10.0, 1)
                return {
                    "status": cached_meta.get("status"),
                    "progress": cached_meta.get("progress") or 0,
                    "total_episodes": cached_meta.get("total_episodes") or 0,
                    "score": score,
                }

        # 2. Check full-list catalog watchlist cache
        cache_col = db.get_collection("user_watchlist_cache")
        docs = list(cache_col.find({"uid": {"$in": uids}}))

        # Enforce deterministic tracker priority: MAL > AniList > Simkl
        tracker_order = {"mal": 0, "anilist": 1, "simkl": 2}
        docs.sort(key=lambda d: tracker_order.get(d.get("tracker"), 99))

        for doc in docs:
            tracker = doc.get("tracker")
            data = doc.get("data")
            if not data:
                continue

            if tracker == "mal":
                for item in data:
                    node = item.get("node") or {}
                    nid = str(node.get("id") or "")
                    if nid and mal_str and nid == mal_str:
                        status_obj = node.get("my_list_status") or {}
                        status = (status_obj.get("status") or "").lower()
                        progress = status_obj.get("num_episodes_watched") or 0
                        total_eps = node.get("num_episodes") or 0
                        score = status_obj.get("score") or 0
                        return {"status": status, "progress": progress, "total_episodes": total_eps, "score": score}

            elif tracker == "anilist":
                lists = data.get("lists") or []
                for lst in lists:
                    entries = lst.get("entries") or []
                    for entry in entries:
                        media = entry.get("media") or {}
                        al_id = str(media.get("id") or "")
                        al_mal_id = str(media.get("idMal") or "")
                        if (anilist_str and al_id == anilist_str) or (mal_str and al_mal_id == mal_str):
                            status = (entry.get("status") or "").lower()
                            progress = entry.get("progress") or 0
                            total_eps = media.get("episodes") or 0
                            raw_score = entry.get("score") or 0
                            score = round(raw_score / 10.0, 1) if raw_score > 10 else raw_score
                            return {"status": status, "progress": progress, "total_episodes": total_eps, "score": score}

            elif tracker == "simkl":
                for item in data:
                    show_obj = item.get("show") or item.get("anime") or item
                    ids = show_obj.get("ids") or {}
                    s_mal = str(ids.get("mal") or "")
                    s_al = str(ids.get("anilist") or "")
                    s_simkl = str(ids.get("simkl") or "")

                    match = False
                    if mal_str and s_mal and s_mal == mal_str:
                        match = True
                    elif anilist_str and s_al and s_al == anilist_str:
                        match = True
                    elif simkl_str and s_simkl and s_simkl == simkl_str:
                        match = True

                    if match:
                        status = (item.get("status") or "").lower()
                        progress = item.get("watched_episodes_count") or item.get("episodes_watched") or item.get("progress") or 0
                        total_eps = (
                            item.get("total_episodes_count")
                            or show_obj.get("total_episodes_count")
                            or show_obj.get("episodes_count")
                            or show_obj.get("num_episodes")
                            or show_obj.get("episodes")
                            or 0
                        )
                        score = item.get("user_rating") or item.get("score") or 0
                        return {"status": status, "progress": progress, "total_episodes": total_eps, "score": score}

    except Exception as e:
        logging.error("Failed to query anime meta status for user %s: %s", user_id, e)

    return None


def save_user_anime_meta_status(
    user_id: str,
    status: str,
    progress: int,
    total_episodes: int,
    score: float | int,
    mal_id: str | None = None,
    anilist_id: str | None = None,
    simkl_id: str | None = None,
    tracker: str | None = None,
):
    """Save an on-demand tracker lookup result to the dedicated user_anime_status_cache collection."""
    if not user_id:
        return
    uids = _resolve_user_cache_uids(user_id)
    canonical_uid = uids[0] if uids else str(user_id)

    now = datetime.now(UTC).replace(tzinfo=None)
    meta_col = db.get_collection("user_anime_status_cache")

    norm_score = float(score) if score else 0.0
    if norm_score > 10:
        norm_score = round(norm_score / 10.0, 1)

    doc = {
        "uid": canonical_uid,
        "status": (status or "").lower(),
        "progress": int(progress or 0),
        "total_episodes": int(total_episodes or 0),
        "score": norm_score,
        "mal_id": str(mal_id) if mal_id else None,
        "anilist_id": str(anilist_id) if anilist_id else None,
        "simkl_id": str(simkl_id) if simkl_id else None,
        "tracker": tracker,
        "fetched_at": now,
        "expires_at": now + timedelta(hours=24),
    }
    if mal_id:
        doc["mal_id"] = str(mal_id)
    if anilist_id:
        doc["anilist_id"] = str(anilist_id)
    if simkl_id:
        doc["simkl_id"] = str(simkl_id)

    or_clauses = []
    if mal_id:
        or_clauses.append({"mal_id": str(mal_id)})
    if anilist_id:
        or_clauses.append({"anilist_id": str(anilist_id)})
    if simkl_id:
        or_clauses.append({"simkl_id": str(simkl_id)})

    if not or_clauses:
        return

    query_filter = {"uid": canonical_uid, "$or": or_clauses}

    try:
        meta_col.update_one(query_filter, {"$set": doc}, upsert=True)
    except Exception as e:
        logging.error("Failed to save user_anime_status_cache for %s: %s", user_id, e)


def update_user_watchlist_cache_progress(
    user_id: str,
    episode: int,
    mal_id: str | None = None,
    anilist_id: str | None = None,
    simkl_id: str | None = None,
):
    """Update watch progress in-place in the cache database to keep it accurate after a scrobble."""
    uids = _resolve_user_cache_uids(user_id)
    if not uids:
        return
    try:
        cache_col = db.get_collection("user_watchlist_cache")
        docs = list(cache_col.find({"uid": {"$in": uids}}))
        if not docs:
            return

        mal_str = str(mal_id) if mal_id else None
        anilist_str = str(anilist_id) if anilist_id else None
        simkl_str = str(simkl_id) if simkl_id else None

        for doc in docs:
            tracker = doc.get("tracker")
            data = doc.get("data")
            status = doc.get("status")
            if not data:
                continue

            updated = False
            item_found = False

            if tracker == "mal":
                for item in data:
                    node = item.get("node") or {}
                    nid = str(node.get("id") or "")
                    if nid and mal_str and nid == mal_str:
                        item_found = True
                        status_obj = node.get("my_list_status") or {}
                        current_prog = status_obj.get("num_episodes_watched") or 0
                        if current_prog != episode:
                            status_obj["num_episodes_watched"] = episode
                            node["my_list_status"] = status_obj
                            item["node"] = node
                            updated = True
                        break

            elif tracker == "anilist":
                lists = data.get("lists") or []
                for lst in lists:
                    entries = lst.get("entries") or []
                    for entry in entries:
                        media = entry.get("media") or {}
                        al_id = str(media.get("id") or "")
                        al_mal_id = str(media.get("idMal") or "")
                        if (anilist_str and al_id == anilist_str) or (mal_str and al_mal_id == mal_str):
                            item_found = True
                            current_prog = entry.get("progress") or 0
                            if current_prog != episode:
                                entry["progress"] = episode
                                updated = True
                            break
                    if item_found:
                        break

            elif tracker == "simkl":
                for item in data:
                    show_obj = item.get("show") or item.get("anime") or item
                    ids = show_obj.get("ids") or {}
                    s_mal = str(ids.get("mal") or "")
                    s_al = str(ids.get("anilist") or "")
                    s_simkl = str(ids.get("simkl") or "")

                    match = False
                    if mal_str and s_mal and s_mal == mal_str:
                        match = True
                    elif anilist_str and s_al and s_al == anilist_str:
                        match = True
                    elif simkl_str and s_simkl and s_simkl == simkl_str:
                        match = True

                    if match:
                        item_found = True
                        for key in ["watched_episodes_count", "episodes_watched", "progress"]:
                            if key in item:
                                if item[key] != episode:
                                    item[key] = episode
                                    updated = True
                        if not updated:
                            item["watched_episodes_count"] = episode
                            updated = True
                        break

            if status == "watching":
                if updated:
                    cache_col.update_one({"_id": doc["_id"]}, {"$set": {"data": data}})
                    logging.info(
                        "Updated watching watchlist cache in-place for user %s, tracker %s, show %s to ep %s",
                        user_id,
                        tracker,
                        mal_str or anilist_str or simkl_str,
                        episode,
                    )
            else:
                # If an item was found in a non-watching list (e.g. plan_to_watch, on_hold, dropped),
                # it has transitioned to watching. Invalidate this cache document so it refetches clean.
                if item_found:
                    cache_col.delete_one({"_id": doc["_id"]})
                    logging.info(
                        "Show %s transitioned to watching; invalidated stale %s cache for user %s, tracker %s.",
                        mal_str or anilist_str or simkl_str,
                        status,
                        user_id,
                        tracker,
                    )

        # Also update single-item on-demand cache if present
        meta_subquery = []
        if mal_str:
            meta_subquery.append({"mal_id": mal_str})
        if anilist_str:
            meta_subquery.append({"anilist_id": anilist_str})
        if simkl_str:
            meta_subquery.append({"simkl_id": simkl_str})
        if meta_subquery:
            db.get_collection("user_anime_status_cache").update_many(
                {
                    "uid": {"$in": uids},
                    "$or": meta_subquery,
                    "status": {"$nin": ["completed", "dropped"]},
                },
                {"$set": {"progress": episode}},
            )

    except Exception as e:
        logging.error("Failed to update watchlist cache progress for user %s: %s", user_id, e)
