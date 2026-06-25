import logging
from datetime import datetime, timedelta

from pymongo import MongoClient
from pymongo.synchronous.collection import Collection
from pymongo.synchronous.database import Database

from config import Config

client: MongoClient = MongoClient(
    Config.MONGO_URI,
    maxPoolSize=100,
    minPoolSize=10,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,
    socketTimeoutMS=5000,
    retryWrites=True,
    retryReads=True,
)
db: Database = client.get_database(Config.MONGO_DB)

# Ensure database indexes on initialization
try:
    db.get_collection("users").create_index("uid", unique=True)
    db.get_collection("users").create_index("mal_id")
    db.get_collection("users").create_index("anilist_id")
    db.get_collection("users").create_index("simkl_id")

    db.get_collection("rate_limits").create_index([("ip", 1), ("route", 1), ("timestamp", -1)])
    db.get_collection("rate_limits").create_index("timestamp", expireAfterSeconds=60)
    db.get_collection("sessions").create_index("expiry", expireAfterSeconds=0)
    db.get_collection("fribb_mappings").create_index("kitsu_id")
    db.get_collection("fribb_mappings").create_index("mal_id")
    db.get_collection("fribb_mappings").create_index("anilist_id")
    db.get_collection("jikan_cache").create_index([("mal_id", 1), ("episode", 1)])

    # id_cache indexes
    db.get_collection("id_cache").create_index("kitsu_id")
    db.get_collection("id_cache").create_index("mal_id")
    db.get_collection("id_cache").create_index("anilist_id")
    db.get_collection("id_cache").create_index("simkl_id")

    # Caching collections indexes
    db.get_collection("user_watchlist_cache").create_index([("uid", 1), ("tracker", 1), ("status", 1)])
    db.get_collection("user_watchlist_cache").create_index("expires_at", expireAfterSeconds=0)

    db.get_collection("anilist_airing_cache").create_index("anilist_id")
    db.get_collection("anilist_airing_cache").create_index("expires_at", expireAfterSeconds=0)

    db.get_collection("kitsu_search_cache").create_index([("query", 1), ("offset", 1)])
    db.get_collection("kitsu_search_cache").create_index("expires_at", expireAfterSeconds=0)
except Exception as e:
    logging.error("Failed to initialize database indexes: %s", e)

users_collection: Collection = db.get_collection("users")
id_cache_collection: Collection = db.get_collection("id_cache")
jikan_cache_collection: Collection = db.get_collection("jikan_cache")


# ── Jikan episode filler cache ────────────────────────────────────────────────

JIKAN_CACHE_TTL_HOURS = 168


def get_jikan_filler_cache(mal_id: str, episode: int) -> bool | None:
    """Return cached filler status for an episode, or None if not cached / expired."""
    try:
        doc = jikan_cache_collection.find_one({"mal_id": str(mal_id), "episode": int(episode)})
        if doc:
            age = datetime.utcnow() - doc.get("cached_at", datetime.min)
            if age < timedelta(hours=JIKAN_CACHE_TTL_HOURS):
                return bool(doc["filler"])
    except Exception as e:
        logging.error("Jikan cache read error: %s", e)
    return None


def set_jikan_filler_cache(mal_id: str, episode: int, filler: bool):
    """Cache the Jikan filler result for an episode."""
    try:
        jikan_cache_collection.update_one(
            {"mal_id": str(mal_id), "episode": int(episode)},
            {
                "$set": {
                    "mal_id": str(mal_id),
                    "episode": int(episode),
                    "filler": filler,
                    "cached_at": datetime.utcnow(),
                }
            },
            upsert=True,
        )
    except Exception as e:
        logging.error("Jikan cache write error: %s", e)


# ── User helpers ──────────────────────────────────────────────────────────────


def get_user(user_id: str) -> dict | None:
    if not user_id:
        return None
    # 1. Try exact match first
    user = users_collection.find_one({"uid": user_id})
    if user:
        return user

    # 2. Support stripping prefixes (e.g., al_6613976 -> 6613976)
    if user_id.startswith("al_"):
        stripped = user_id[3:]
        user = users_collection.find_one({"uid": stripped})
        if user:
            return user
    elif user_id.startswith("simkl_"):
        stripped = user_id[6:]
        user = users_collection.find_one({"uid": stripped})
        if user:
            return user

    # 3. Support adding prefixes (e.g., 6613976 -> al_6613976)
    if user_id.isdigit():
        for prefix in ["al_", "simkl_"]:
            user = users_collection.find_one({"uid": f"{prefix}{user_id}"})
            if user:
                return user

    return None


def find_user_by_mal_id(mal_id: str) -> dict | None:
    return users_collection.find_one({"$or": [{"uid": str(mal_id)}, {"mal_id": str(mal_id)}]})


def find_user_by_anilist_id(anilist_id: str) -> dict | None:
    return users_collection.find_one(
        {"$or": [{"uid": f"al_{anilist_id}"}, {"uid": str(anilist_id)}, {"anilist_id": str(anilist_id)}]}
    )


def find_user_by_simkl_id(simkl_id: str) -> dict | None:
    return users_collection.find_one(
        {"$or": [{"uid": f"simkl_{simkl_id}"}, {"uid": str(simkl_id)}, {"simkl_id": str(simkl_id)}]}
    )


def store_user(user_details: dict) -> bool:
    uid = user_details.get("uid") or user_details.get("id")
    if not uid:
        return False
    user_details["uid"] = str(uid)
    existing = users_collection.find_one({"uid": str(uid)})
    if existing:
        return users_collection.replace_one({"uid": str(uid)}, user_details).acknowledged
    return users_collection.insert_one(user_details).acknowledged


def get_valid_mal_user(user_id: str) -> tuple[dict, str | None]:
    user = get_user(user_id)
    if not user:
        return {}, "User not found."
    if not user.get("mal_enabled"):
        return user, None  # not an error — MAL just disabled
    if not user.get("mal_access_token") or not user.get("mal_refresh_token"):
        return {}, "MAL not connected. Please log in."
    expiry = user.get("mal_expires_at")
    if expiry and datetime.utcnow() > expiry:
        return {}, "MAL session expired. Please refresh."
    return user, None


# ── ARM ID cache ──────────────────────────────────────────────────────────────


def get_cached_ids(kitsu_id: str) -> dict | None:
    try:
        return id_cache_collection.find_one({"kitsu_id": int(kitsu_id)})
    except (ValueError, TypeError):
        return None


def get_cached_ids_by_mal(mal_id: str) -> dict | None:
    try:
        return id_cache_collection.find_one({"mal_id": str(mal_id)})
    except (ValueError, TypeError):
        return None


def get_cached_ids_by_anilist(anilist_id: str) -> dict | None:
    try:
        return id_cache_collection.find_one({"anilist_id": str(anilist_id)})
    except (ValueError, TypeError):
        return None


def get_cached_ids_by_simkl(simkl_id: str) -> dict | None:
    try:
        query = {"$or": [{"simkl_id": str(simkl_id)}]}
        if str(simkl_id).isdigit():
            query["$or"].append({"simkl_id": int(simkl_id)})
            query["$or"].append({"simkl": int(simkl_id)})
        return id_cache_collection.find_one(query)
    except Exception:
        return None


def cache_ids(
    kitsu_id: str,
    mal_id: str | None,
    anilist_id: str | None,
    simkl_id: str | None = None,
    imdb_id: str | None = None,
    tmdb_id: str | None = None,
    tvdb_id: str | None = None,
):
    try:
        doc = {
            "kitsu_id": int(kitsu_id) if kitsu_id else None,
            "mal_id": str(mal_id) if mal_id else None,
            "anilist_id": str(anilist_id) if anilist_id else None,
            "simkl_id": str(simkl_id) if simkl_id else None,
            "imdb_id": str(imdb_id) if imdb_id else None,
            "tmdb_id": str(tmdb_id) if tmdb_id else None,
            "tvdb_id": str(tvdb_id) if tvdb_id else None,
        }
        # Filter out None kitsu_id
        if doc["kitsu_id"] is None:
            return
        existing = id_cache_collection.find_one({"kitsu_id": doc["kitsu_id"]})
        if existing:
            update_doc = {}
            for k, v in doc.items():
                if v is not None:
                    update_doc[k] = v
            if update_doc:
                id_cache_collection.update_one({"kitsu_id": doc["kitsu_id"]}, {"$set": update_doc})
        else:
            id_cache_collection.insert_one(doc)
    except Exception as e:
        logging.error("Cache write error: %s", e)


def invalidate_user_watchlist_cache(user_id: str):
    """Delete all cached watchlist documents for a specific user."""
    try:
        db.get_collection("user_watchlist_cache").delete_many({"uid": str(user_id)})
        logging.info("Invalidated watchlist cache for user %s", user_id)
    except Exception as e:
        logging.error("Failed to invalidate watchlist cache for user %s: %s", user_id, e)


def is_anilist_in_cooldown(user: dict) -> bool:
    """Check if AniList calls are in a temporary cooldown due to a recent authentication error."""
    last_error_at = user.get("anilist_last_auth_error_at")
    if last_error_at:
        import time
        # Cooldown duration: 2 hours (7200 seconds)
        if time.time() - last_error_at < 7200:
            return True
    return False


def reset_anilist_error_counter(user_id: str):
    """Reset the AniList consecutive auth error counter to 0 on success."""
    try:
        user = get_user(user_id)
        if user and (user.get("anilist_consecutive_auth_errors", 0) > 0 or "anilist_last_auth_error_at" in user):
            user["anilist_consecutive_auth_errors"] = 0
            user.pop("anilist_last_auth_error_at", None)
            store_user(user)
            logging.info("Reset AniList auth error counter for user %s", user_id)
    except Exception as e:
        logging.error("Failed to reset AniList error counter for user %s: %s", user_id, e)


def handle_invalid_anilist_token(user_id: str):
    """Handles an invalid AniList token. Instead of disconnecting immediately,
    it tracks consecutive failures and puts the account in a temporary cooldown.
    After 3 consecutive failures, it permanently disconnects the account.
    """
    try:
        user = get_user(user_id)
        if not user or not user.get("anilist_token"):
            return

        import time
        consecutive_errors = user.get("anilist_consecutive_auth_errors", 0) + 1
        user["anilist_consecutive_auth_errors"] = consecutive_errors
        user["anilist_last_auth_error_at"] = time.time()

        if consecutive_errors >= 3:
            logging.warning("AniList token failed 3 consecutive times. Automatically disconnecting user %s", user_id)
            user.pop("anilist_token", None)
            user.pop("anilist_username", None)
            user.pop("anilist_picture", None)
            user["anilist_enabled"] = False
            user["anilist_consecutive_auth_errors"] = 0
            user.pop("anilist_last_auth_error_at", None)
            user["anilist_session_expired"] = True
            # Retain user picture if MAL or Simkl is connected
            if not user.get("mal_access_token") and not user.get("simkl_access_token"):
                user.pop("picture", None)
            store_user(user)
            invalidate_user_watchlist_cache(user_id)
        else:
            logging.warning(
                "AniList token invalid error (attempt %d/3) for user %s. Entering 2-hour cooldown.",
                consecutive_errors,
                user_id,
            )
            store_user(user)
    except Exception as e:
        logging.error("Failed to handle invalid AniList token for user %s: %s", user_id, e)


async def get_or_refresh_mal_token(user_id: str) -> str | None:
    """Gets the MAL access token for a user. If it's expired or close to expiring,
    attempts to refresh it in the background. If refreshing fails, disconnects MAL
    and sets the expired flag.
    """
    try:
        user = get_user(user_id)
        if not user or not user.get("mal_access_token") or not user.get("mal_refresh_token"):
            return None

        expiry = user.get("mal_expires_at")
        now = datetime.utcnow()
        if expiry and now >= expiry - timedelta(hours=2):
            from app.api import mal as mal_api
            try:
                logging.info("Auto-refreshing MAL token for user %s", user_id)
                token_data = await mal_api.refresh_token(user["mal_refresh_token"])
                user["mal_access_token"] = token_data["access_token"]
                user["mal_refresh_token"] = token_data["refresh_token"]
                user["mal_expires_at"] = now + timedelta(seconds=token_data["expires_in"])
                user["mal_consecutive_auth_errors"] = 0
                store_user(user)
            except Exception as e:
                logging.error("Failed to auto-refresh MAL token for user %s: %s", user_id, e)
                # Disconnect on refresh failure
                user.pop("mal_access_token", None)
                user.pop("mal_refresh_token", None)
                user.pop("mal_picture", None)
                user["mal_enabled"] = False
                user["mal_consecutive_auth_errors"] = 0
                user.pop("mal_last_auth_error_at", None)
                user["mal_session_expired"] = True

                if not user.get("anilist_token") and not user.get("simkl_access_token"):
                    user.pop("picture", None)

                store_user(user)
                invalidate_user_watchlist_cache(user_id)
                return None

        return user.get("mal_access_token")
    except Exception as e:
        logging.error("Error in get_or_refresh_mal_token: %s", e)
        return None


def reset_mal_error_counter(user_id: str):
    """Reset the MAL consecutive auth error counter to 0 on success."""
    try:
        user = get_user(user_id)
        if user and (user.get("mal_consecutive_auth_errors", 0) > 0 or "mal_last_auth_error_at" in user):
            user["mal_consecutive_auth_errors"] = 0
            user.pop("mal_last_auth_error_at", None)
            store_user(user)
            logging.info("Reset MAL auth error counter for user %s", user_id)
    except Exception as e:
        logging.error("Failed to reset MAL error counter for user %s: %s", user_id, e)


def handle_invalid_mal_token(user_id: str):
    """Handles an invalid MAL token (401/403). Tracks consecutive errors and
    disconnects after 3 failures. Expures the token to force refresh.
    """
    try:
        user = get_user(user_id)
        if not user or not user.get("mal_access_token"):
            return

        import time
        consecutive_errors = user.get("mal_consecutive_auth_errors", 0) + 1
        user["mal_consecutive_auth_errors"] = consecutive_errors
        user["mal_last_auth_error_at"] = time.time()
        # Force a refresh attempt on the next call by expiring the token
        user["mal_expires_at"] = datetime.utcnow() - timedelta(seconds=1)

        if consecutive_errors >= 3:
            logging.warning("MAL token failed 3 consecutive times. Automatically disconnecting user %s", user_id)
            user.pop("mal_access_token", None)
            user.pop("mal_refresh_token", None)
            user.pop("mal_picture", None)
            user["mal_enabled"] = False
            user["mal_consecutive_auth_errors"] = 0
            user.pop("mal_last_auth_error_at", None)
            user["mal_session_expired"] = True

            if not user.get("anilist_token") and not user.get("simkl_access_token"):
                user.pop("picture", None)

            store_user(user)
            invalidate_user_watchlist_cache(user_id)
        else:
            logging.warning("MAL token invalid error (attempt %d/3) for user %s.", consecutive_errors, user_id)
            store_user(user)
    except Exception as e:
        logging.error("Failed to handle invalid MAL token: %s", e)


def reset_simkl_error_counter(user_id: str):
    """Reset the Simkl consecutive auth error counter to 0 on success."""
    try:
        user = get_user(user_id)
        if user and (user.get("simkl_consecutive_auth_errors", 0) > 0 or "simkl_last_auth_error_at" in user):
            user["simkl_consecutive_auth_errors"] = 0
            user.pop("simkl_last_auth_error_at", None)
            store_user(user)
            logging.info("Reset Simkl auth error counter for user %s", user_id)
    except Exception as e:
        logging.error("Failed to reset Simkl error counter for user %s: %s", user_id, e)


def handle_invalid_simkl_token(user_id: str):
    """Handles an invalid Simkl token (401/403). Tracks consecutive errors and
    disconnects after 3 failures.
    """
    try:
        user = get_user(user_id)
        if not user or not user.get("simkl_access_token"):
            return

        import time
        consecutive_errors = user.get("simkl_consecutive_auth_errors", 0) + 1
        user["simkl_consecutive_auth_errors"] = consecutive_errors
        user["simkl_last_auth_error_at"] = time.time()

        if consecutive_errors >= 3:
            logging.warning("Simkl token failed 3 consecutive times. Automatically disconnecting user %s", user_id)
            user.pop("simkl_access_token", None)
            user.pop("simkl_username", None)
            user.pop("simkl_avatar", None)
            user["simkl_enabled"] = False
            user["simkl_consecutive_auth_errors"] = 0
            user.pop("simkl_last_auth_error_at", None)
            user["simkl_session_expired"] = True

            if not user.get("mal_access_token") and not user.get("anilist_token"):
                user.pop("picture", None)

            store_user(user)
            invalidate_user_watchlist_cache(user_id)
        else:
            logging.warning("Simkl token invalid error (attempt %d/3) for user %s.", consecutive_errors, user_id)
            store_user(user)
    except Exception as e:
        logging.error("Failed to handle invalid Simkl token: %s", e)



def get_user_watch_progress(
    user_id: str, mal_id: str | None = None, anilist_id: str | None = None, simkl_id: str | None = None
) -> int:
    """Find the user's maximum watch progress (watched episode count) across cached watchlists."""
    if not user_id:
        return 0

    max_progress = 0
    try:
        cache_col = db.get_collection("user_watchlist_cache")
        docs = list(cache_col.find({"uid": str(user_id)}))

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


def update_user_watchlist_cache_progress(
    user_id: str,
    episode: int,
    mal_id: str | None = None,
    anilist_id: str | None = None,
    simkl_id: str | None = None,
):
    """Update watch progress in-place in the cache database to keep it accurate after a scrobble."""
    try:
        cache_col = db.get_collection("user_watchlist_cache")
        docs = list(cache_col.find({"uid": str(user_id)}))
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

            if updated:
                cache_col.update_one({"_id": doc["_id"]}, {"$set": {"data": data}})
                logging.info(
                    "Updated watchlist cache in-place for user %s, tracker %s, show %s to ep %s",
                    user_id,
                    tracker,
                    mal_str or anilist_str or simkl_str,
                    episode,
                )
            elif not item_found and status == "watching":
                cache_col.delete_one({"_id": doc["_id"]})
                logging.info(
                    "Show not found in cache for user %s, tracker %s. Invalidating cache document.",
                    user_id,
                    tracker,
                )

    except Exception as e:
        logging.error("Failed to update watchlist cache progress for user %s: %s", user_id, e)

