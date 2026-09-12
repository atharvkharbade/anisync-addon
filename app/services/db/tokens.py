import asyncio
import logging
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from .users import get_user, store_user
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

        consecutive_errors = user.get("anilist_consecutive_auth_errors", 0) + 1
        user["anilist_consecutive_auth_errors"] = consecutive_errors
        user["anilist_last_auth_error_at"] = time.time()

        if consecutive_errors >= 3:
            logging.warning("AniList token failed 3 consecutive times. Automatically marking expired for user %s", user_id)
            user["anilist_enabled"] = False
            user["anilist_consecutive_auth_errors"] = 0
            user.pop("anilist_last_auth_error_at", None)
            user["anilist_token_expired"] = True
            user["anilist_expired_at"] = time.time()
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
    attempts to refresh it in the background. If refreshing fails, marks MAL as expired.
    """
    async with _mal_refresh_locks[str(user_id)]:
        try:
            user = get_user(user_id)
            if not user or not user.get("mal_access_token") or not user.get("mal_refresh_token"):
                return None

            # If already marked expired, do not attempt to refresh or use
            if user.get("mal_token_expired"):
                return None

            expiry = user.get("mal_expires_at")
            now = datetime.now(UTC).replace(tzinfo=None)
            if expiry and now >= expiry - timedelta(hours=2):
                from app.api import mal as mal_api

                try:
                    logging.info("Auto-refreshing MAL token for user %s", user_id)
                    token_data = await mal_api.refresh_token(user["mal_refresh_token"])
                    user["mal_access_token"] = token_data["access_token"]
                    user["mal_refresh_token"] = token_data["refresh_token"]
                    user["mal_expires_at"] = now + timedelta(seconds=token_data["expires_in"])
                    user["mal_consecutive_auth_errors"] = 0
                    user.pop("mal_token_expired", None)
                    store_user(user)
                except Exception as e:
                    logging.error("Failed to auto-refresh MAL token for user %s: %s", user_id, e)
                    # Mark as expired on refresh failure
                    user["mal_enabled"] = False
                    user["mal_consecutive_auth_errors"] = 0
                    user.pop("mal_last_auth_error_at", None)
                    user["mal_token_expired"] = True
                    user["mal_expired_at"] = time.time()
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
    marks expired after 3 failures.
    """
    try:
        user = get_user(user_id)
        if not user or not user.get("mal_access_token"):
            return

        consecutive_errors = user.get("mal_consecutive_auth_errors", 0) + 1
        user["mal_consecutive_auth_errors"] = consecutive_errors
        user["mal_last_auth_error_at"] = time.time()
        # Force a refresh attempt on the next call by expiring the token
        user["mal_expires_at"] = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)

        if consecutive_errors >= 3:
            logging.warning("MAL token failed 3 consecutive times. Automatically marking expired for user %s", user_id)
            user["mal_enabled"] = False
            user["mal_consecutive_auth_errors"] = 0
            user.pop("mal_last_auth_error_at", None)
            user["mal_token_expired"] = True
            user["mal_expired_at"] = time.time()
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
    marks expired after 3 failures.
    """
    try:
        user = get_user(user_id)
        if not user or not user.get("simkl_access_token"):
            return

        consecutive_errors = user.get("simkl_consecutive_auth_errors", 0) + 1
        user["simkl_consecutive_auth_errors"] = consecutive_errors
        user["simkl_last_auth_error_at"] = time.time()

        if consecutive_errors >= 3:
            logging.warning("Simkl token failed 3 consecutive times. Automatically marking expired for user %s", user_id)
            user["simkl_enabled"] = False
            user["simkl_consecutive_auth_errors"] = 0
            user.pop("simkl_last_auth_error_at", None)
            user["simkl_token_expired"] = True
            user["simkl_expired_at"] = time.time()
            store_user(user)
            invalidate_user_watchlist_cache(user_id)
        else:
            logging.warning("Simkl token invalid error (attempt %d/3) for user %s.", consecutive_errors, user_id)
            store_user(user)
    except Exception as e:
        logging.error("Failed to handle invalid Simkl token: %s", e)
