import asyncio
import logging
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from .connection import users_collection
from .users import get_user, store_user, update_user_fields
from .watchlist import invalidate_user_watchlist_cache

_mal_refresh_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def is_anilist_in_cooldown(user: dict) -> bool:
    """Check if AniList calls are in a temporary cooldown due to a recent authentication error."""
    last_error_at = user.get("anilist_last_auth_error_at")
    if last_error_at:
        # Cooldown duration: 2 hours (7200 seconds)
        if time.time() - last_error_at < 7200:
            return True
    return False


def reset_anilist_error_counter(user_id: str):
    """Reset the AniList consecutive auth error counter to 0 on success."""
    try:
        user = get_user(user_id)
        if user and (user.get("anilist_consecutive_auth_errors", 0) > 0 or "anilist_last_auth_error_at" in user):
            canonical_uid = str(user.get("uid") or user_id)
            update_user_fields(
                canonical_uid,
                set_fields={"anilist_consecutive_auth_errors": 0},
                unset_fields=["anilist_last_auth_error_at"],
            )
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

        canonical_uid = str(user.get("uid") or user_id)
        consecutive_errors = user.get("anilist_consecutive_auth_errors", 0) + 1

        if consecutive_errors >= 3:
            logging.warning("AniList token failed 3 consecutive times. Automatically marking expired for user %s", user_id)
            update_user_fields(
                canonical_uid,
                set_fields={
                    "anilist_enabled": False,
                    "anilist_consecutive_auth_errors": 0,
                    "anilist_token_expired": True,
                    "anilist_expired_at": time.time(),
                },
                unset_fields=["anilist_last_auth_error_at"],
            )
            invalidate_user_watchlist_cache(canonical_uid)
        else:
            logging.warning(
                "AniList token invalid error (attempt %d/3) for user %s. Entering 2-hour cooldown.",
                consecutive_errors,
                user_id,
            )
            update_user_fields(
                canonical_uid,
                set_fields={
                    "anilist_consecutive_auth_errors": consecutive_errors,
                    "anilist_last_auth_error_at": time.time(),
                },
            )
    except Exception as e:
        logging.error("Failed to handle invalid AniList token for user %s: %s", user_id, e)


_MAL_REFRESH_LEASE_SECONDS = 30
_MAL_REFRESH_WAIT_SECONDS = 8.0


def _acquire_mal_refresh_lease(uid: str) -> bool:
    """Cross-process refresh lease stored on the user document.

    The app runs several uvicorn workers, so the in-process asyncio lock alone cannot stop two
    workers from spending the same single-use MAL refresh token. Returns False only when another
    worker currently holds an unexpired lease; any other outcome (missing doc, Mongo error) lets the
    caller go ahead, which is the old behaviour."""
    now_ts = time.time()
    try:
        res = users_collection.update_one(
            {
                "uid": uid,
                "$or": [
                    {"mal_refresh_lock_until": {"$exists": False}},
                    {"mal_refresh_lock_until": {"$lt": now_ts}},
                ],
            },
            {"$set": {"mal_refresh_lock_until": now_ts + _MAL_REFRESH_LEASE_SECONDS}},
        )
        if res.matched_count:
            return True
        held = users_collection.find_one({"uid": uid, "mal_refresh_lock_until": {"$gte": now_ts}}, {"_id": 1})
        return held is None
    except Exception as e:
        logging.warning("MAL refresh lease unavailable for %s, refreshing without it: %s", uid, e)
        return True


def _release_mal_refresh_lease(uid: str) -> None:
    try:
        users_collection.update_one({"uid": uid}, {"$unset": {"mal_refresh_lock_until": ""}})
    except Exception:
        pass


def _usable_mal_token(user: dict | None) -> str | None:
    """The stored access token if it has not actually expired yet (inside the 2-hour pre-refresh window)."""
    if not user or user.get("mal_token_expired") or not user.get("mal_access_token"):
        return None
    expiry = user.get("mal_expires_at")
    if expiry and datetime.now(UTC).replace(tzinfo=None) >= expiry:
        return None
    return user.get("mal_access_token")


async def _wait_for_other_mal_refresh(uid: str, used_refresh_token: str) -> str | None:
    """Another worker is refreshing: wait for its result instead of spending the same refresh token."""
    deadline = time.monotonic() + _MAL_REFRESH_WAIT_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(0.25)
        user = get_user(uid)
        if not user or user.get("mal_token_expired"):
            return None
        if user.get("mal_refresh_token") != used_refresh_token:
            return user.get("mal_access_token")
        if not user.get("mal_refresh_lock_until"):
            break
    return _usable_mal_token(get_user(uid))


async def get_or_refresh_mal_token(user_id: str) -> str | None:
    """Gets the MAL access token for a user. If it's expired or close to expiring,
    attempts to refresh it. If refreshing fails, marks MAL as expired.
    Serialised per canonical uid: an asyncio lock inside this worker plus a Mongo lease across workers.
    """
    try:
        user = get_user(user_id)
        if not user or not user.get("mal_access_token") or not user.get("mal_refresh_token"):
            return None

        # If already marked expired, do not attempt to refresh or use
        if user.get("mal_token_expired"):
            return None

        canonical_uid = str(user.get("uid") or user_id)

        async with _mal_refresh_locks[canonical_uid]:
            # Double-checked locking: re-read the user inside the lock before deciding to refresh
            user = get_user(canonical_uid)
            if not user or not user.get("mal_access_token") or not user.get("mal_refresh_token"):
                return None
            if user.get("mal_token_expired"):
                return None

            expiry = user.get("mal_expires_at")
            now = datetime.now(UTC).replace(tzinfo=None)
            if not (expiry and now >= expiry - timedelta(hours=2)):
                return user.get("mal_access_token")

            used_refresh_token = user["mal_refresh_token"]
            if not _acquire_mal_refresh_lease(canonical_uid):
                return await _wait_for_other_mal_refresh(canonical_uid, used_refresh_token)

            from app.api import mal as mal_api

            try:
                # Re-read after taking the lease: another worker may have just finished
                fresh = get_user(canonical_uid)
                if fresh and fresh.get("mal_refresh_token") != used_refresh_token:
                    return fresh.get("mal_access_token")

                logging.info("Auto-refreshing MAL token for user %s (canonical %s)", user_id, canonical_uid)
                token_data = await mal_api.refresh_token(used_refresh_token)
                update_user_fields(
                    canonical_uid,
                    set_fields={
                        "mal_access_token": token_data["access_token"],
                        "mal_refresh_token": token_data["refresh_token"],
                        "mal_expires_at": now + timedelta(seconds=token_data["expires_in"]),
                        "mal_consecutive_auth_errors": 0,
                    },
                    unset_fields=["mal_token_expired", "mal_expired_at", "mal_last_auth_error_at"],
                )
                return token_data["access_token"]
            except Exception as e:
                # If someone else rotated the token meanwhile, theirs is valid: don't disconnect the user
                latest = get_user(canonical_uid)
                if latest and latest.get("mal_refresh_token") and latest.get("mal_refresh_token") != used_refresh_token:
                    logging.info("MAL refresh for %s lost a race but another refresh succeeded; using it", canonical_uid)
                    return latest.get("mal_access_token")
                logging.error("Failed to auto-refresh MAL token for user %s: %s", user_id, e)
                # Mark as expired on refresh failure
                update_user_fields(
                    canonical_uid,
                    set_fields={
                        "mal_enabled": False,
                        "mal_consecutive_auth_errors": 0,
                        "mal_token_expired": True,
                        "mal_expired_at": time.time(),
                    },
                    unset_fields=["mal_last_auth_error_at"],
                )
                invalidate_user_watchlist_cache(canonical_uid)
                return None
            finally:
                _release_mal_refresh_lease(canonical_uid)
    except Exception as e:
        logging.error("Error in get_or_refresh_mal_token: %s", e)
        return None


def reset_mal_error_counter(user_id: str):
    """Reset the MAL consecutive auth error counter to 0 on success."""
    try:
        user = get_user(user_id)
        if user and (user.get("mal_consecutive_auth_errors", 0) > 0 or "mal_last_auth_error_at" in user):
            canonical_uid = str(user.get("uid") or user_id)
            update_user_fields(
                canonical_uid,
                set_fields={"mal_consecutive_auth_errors": 0},
                unset_fields=["mal_last_auth_error_at"],
            )
            logging.info("Reset MAL auth error counter for user %s", user_id)
    except Exception as e:
        logging.error("Failed to reset MAL error counter for user %s: %s", user_id, e)


def handle_invalid_mal_token(user_id: str):
    """Handles an invalid MAL token (401/403). Tracks consecutive errors and
    marks expired after 3 failures.
    """
    try:
        user = get_user(user_id)
        if not user or not user.get("mal_access_token"):
            return

        canonical_uid = str(user.get("uid") or user_id)
        consecutive_errors = user.get("mal_consecutive_auth_errors", 0) + 1
        forced_expiry = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)

        if consecutive_errors >= 3:
            logging.warning("MAL token failed 3 consecutive times. Automatically marking expired for user %s", user_id)
            update_user_fields(
                canonical_uid,
                set_fields={
                    "mal_enabled": False,
                    "mal_consecutive_auth_errors": 0,
                    "mal_token_expired": True,
                    "mal_expired_at": time.time(),
                    "mal_expires_at": forced_expiry,
                },
                unset_fields=["mal_last_auth_error_at"],
            )
            invalidate_user_watchlist_cache(canonical_uid)
        else:
            logging.warning("MAL token invalid error (attempt %d/3) for user %s.", consecutive_errors, user_id)
            update_user_fields(
                canonical_uid,
                set_fields={
                    "mal_consecutive_auth_errors": consecutive_errors,
                    "mal_last_auth_error_at": time.time(),
                    "mal_expires_at": forced_expiry,
                },
            )
    except Exception as e:
        logging.error("Failed to handle invalid MAL token: %s", e)


def reset_simkl_error_counter(user_id: str):
    """Reset the Simkl consecutive auth error counter to 0 on success."""
    try:
        user = get_user(user_id)
        if user and (user.get("simkl_consecutive_auth_errors", 0) > 0 or "simkl_last_auth_error_at" in user):
            canonical_uid = str(user.get("uid") or user_id)
            update_user_fields(
                canonical_uid,
                set_fields={"simkl_consecutive_auth_errors": 0},
                unset_fields=["simkl_last_auth_error_at"],
            )
            logging.info("Reset Simkl auth error counter for user %s", user_id)
    except Exception as e:
        logging.error("Failed to reset Simkl error counter for user %s: %s", user_id, e)


def handle_invalid_simkl_token(user_id: str):
    """Handles an invalid Simkl token (401/403). Tracks consecutive errors and
    marks expired after 3 failures.
    """
    try:
        user = get_user(user_id)
        if not user or not user.get("simkl_access_token"):
            return

        canonical_uid = str(user.get("uid") or user_id)
        consecutive_errors = user.get("simkl_consecutive_auth_errors", 0) + 1

        if consecutive_errors >= 3:
            logging.warning("Simkl token failed 3 consecutive times. Automatically marking expired for user %s", user_id)
            update_user_fields(
                canonical_uid,
                set_fields={
                    "simkl_enabled": False,
                    "simkl_consecutive_auth_errors": 0,
                    "simkl_token_expired": True,
                    "simkl_expired_at": time.time(),
                },
                unset_fields=["simkl_last_auth_error_at"],
            )
            invalidate_user_watchlist_cache(canonical_uid)
        else:
            logging.warning("Simkl token invalid error (attempt %d/3) for user %s.", consecutive_errors, user_id)
            update_user_fields(
                canonical_uid,
                set_fields={
                    "simkl_consecutive_auth_errors": consecutive_errors,
                    "simkl_last_auth_error_at": time.time(),
                },
            )
    except Exception as e:
        logging.error("Failed to handle invalid Simkl token: %s", e)
