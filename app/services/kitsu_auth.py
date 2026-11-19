import asyncio
from datetime import UTC, datetime, timedelta
import logging
import time

import httpx
from pymongo.errors import DuplicateKeyError

from config import Config, kitsu_login_enabled
from app.services.http import get_client

logger = logging.getLogger(__name__)

# Note on Step 6 (what stays anonymous):

# Recommendations (recommendations/seeding.py), poster lookups (services/poster/id_resolver.py),
# discovery Kitsu fetchers and title fallback (id_resolver/remote.py) intentionally stay anonymous.

KITSU_OAUTH_TOKEN_URL = "https://kitsu.io/api/oauth/token"
_REFRESH_LEASE_SECONDS = 15.0
_LEASE_WAIT_TIMEOUT = 8.0
_FAILURE_BACKOFF_SECONDS = 600.0  # 10 minutes

# In-memory token cache and login failure backoff state per worker
_mem_token: str | None = None
_mem_exp: datetime | None = None
_login_blocked_until: datetime | None = None
_token_lock = asyncio.Lock()


def _get_auth_col():
    from app.services.db import db

    return db.get_collection("kitsu_auth")


def _to_naive_utc(dt: datetime | None) -> datetime | None:
    if not dt or not isinstance(dt, datetime):
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def invalidate_kitsu_token() -> None:
    """Flushes the active token from memory and MongoDB."""
    global _mem_token, _mem_exp
    _mem_token = None
    _mem_exp = None
    try:
        _get_auth_col().delete_one({"_id": "auth_token"})
    except Exception as e:
        logger.warning("Failed to delete kitsu_auth from Mongo: %s", e)


async def get_kitsu_token(force_refresh: bool = False) -> str | None:
    """
    Retrieves a valid Kitsu Bearer token.
    Checks in-memory fast path, MongoDB (kitsu_auth), and renews/authenticates via OAuth.
    Valid token: returns if > 3 days remain.
    Renewing: tries grant_type=refresh_token first, then grant_type=password.
    Applies a 10-minute backoff after a failed login to avoid endpoint spam.
    Lease lock in Mongo coordinates multi-worker refreshes.
    """
    global _mem_token, _mem_exp, _login_blocked_until

    if not kitsu_login_enabled():
        return None

    now = datetime.now(UTC).replace(tzinfo=None)

    # 1. In-memory fast path
    if not force_refresh and _mem_token and _mem_exp and (_mem_exp - now) > timedelta(days=3):
        return _mem_token

    # 2. In-memory failure backoff
    if _login_blocked_until and _login_blocked_until > now:
        return None

    async with _token_lock:
        now = datetime.now(UTC).replace(tzinfo=None)
        if not force_refresh and _mem_token and _mem_exp and (_mem_exp - now) > timedelta(days=3):
            return _mem_token

        if _login_blocked_until and _login_blocked_until > now:
            return None

        col = _get_auth_col()

        # 3. Check MongoDB doc for backoff and valid token
        try:
            doc = col.find_one({"_id": "auth_token"})
        except Exception as e:
            logger.warning("Error reading kitsu_auth from Mongo: %s", e)
            doc = None

        if doc:
            last_fail = _to_naive_utc(doc.get("last_failure_at"))
            if last_fail and (now - last_fail).total_seconds() < _FAILURE_BACKOFF_SECONDS:
                _login_blocked_until = last_fail + timedelta(seconds=_FAILURE_BACKOFF_SECONDS)
                return None

            if not force_refresh and doc.get("access_token") and doc.get("expires_at"):
                exp = _to_naive_utc(doc["expires_at"])
                if exp and (exp - now) > timedelta(days=3):
                    _mem_token = str(doc["access_token"])
                    _mem_exp = exp
                    return _mem_token

        # 4. Multi-worker lease coordination
        now_ts = time.time()
        lease_acquired = False
        try:
            res = col.update_one(
                {
                    "_id": "auth_token",
                    "$or": [
                        {"refresh_lock_until": {"$exists": False}},
                        {"refresh_lock_until": {"$lt": now_ts}},
                    ],
                },
                {"$set": {"refresh_lock_until": now_ts + _REFRESH_LEASE_SECONDS}},
                upsert=True,
            )
            lease_acquired = bool(res.matched_count or res.upserted_id)
        except DuplicateKeyError:
            # The doc exists and another worker's lease is still active: the filter didn't match, so the upsert
            # tried to insert a second auth_token doc.
            lease_acquired = False
        except Exception as e:
            logger.debug("Could not acquire kitsu_auth lease: %s", e)
            lease_acquired = True  # Proceed anyway on a real Mongo glitch

        if not lease_acquired:
            # Another worker holds the lease; wait up to 8s and re-check
            start_wait = time.monotonic()
            while time.monotonic() - start_wait < _LEASE_WAIT_TIMEOUT:
                await asyncio.sleep(0.4)
                try:
                    doc = col.find_one({"_id": "auth_token"})
                    if doc and doc.get("access_token") and doc.get("expires_at"):
                        exp = _to_naive_utc(doc["expires_at"])
                        now = datetime.now(UTC).replace(tzinfo=None)
                        if exp and (exp - now) > timedelta(days=3):
                            _mem_token = str(doc["access_token"])
                            _mem_exp = exp
                            return _mem_token
                    # If lock has expired or released, try taking it
                    if not doc or not doc.get("refresh_lock_until") or doc["refresh_lock_until"] < time.time():
                        break
                except Exception:
                    break

        # 5. Perform renewal or password login (re-read: another worker may have rotated the refresh token meanwhile)
        if not lease_acquired:
            try:
                doc = col.find_one({"_id": "auth_token"}) or doc
            except Exception:
                pass
        client = get_client()
        refresh_token = doc.get("refresh_token") if doc else None
        token_data = None
        renew_ok = False

        # Try refresh token grant first if available
        if refresh_token:
            try:
                resp = await client.post(
                    KITSU_OAUTH_TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": Config.KITSU_CLIENT_ID,
                        "client_secret": Config.KITSU_CLIENT_SECRET,
                    },
                    headers={"Accept": "application/json"},
                    timeout=8.0,
                )
                if resp.status_code == 200:
                    token_data = resp.json()
                    renew_ok = True
                else:
                    logger.debug("Kitsu token refresh failed (HTTP %s), falling back to password login", resp.status_code)
            except Exception as e:
                logger.debug("Kitsu token refresh request error: %s, falling back to password login", e)

        # Fallback to password login
        if not renew_ok:
            try:
                resp = await client.post(
                    KITSU_OAUTH_TOKEN_URL,
                    data={
                        "grant_type": "password",
                        "username": Config.KITSU_USERNAME,
                        "password": Config.KITSU_PASSWORD,
                        "client_id": Config.KITSU_CLIENT_ID,
                        "client_secret": Config.KITSU_CLIENT_SECRET,
                    },
                    headers={"Accept": "application/json"},
                    timeout=8.0,
                )
                if resp.status_code == 200:
                    token_data = resp.json()
                else:
                    err_msg = ""
                    try:
                        err_payload = resp.json()
                        err_msg = err_payload.get("error_description") or err_payload.get("error") or str(resp.status_code)
                    except Exception:
                        err_msg = f"HTTP {resp.status_code}"
                    logger.warning("Kitsu login failed: %s", err_msg)
            except Exception as e:
                logger.warning("Kitsu login failed: %s", type(e).__name__)

        # 6. Handle success or failure
        now_dt = datetime.now(UTC).replace(tzinfo=None)
        if token_data and token_data.get("access_token"):
            new_access_token = str(token_data["access_token"])
            new_refresh_token = str(token_data.get("refresh_token") or "")
            expires_in = int(token_data.get("expires_in") or 2592000)
            exp_dt = now_dt + timedelta(seconds=expires_in)

            days = max(1, expires_in // 86400)
            logger.info("Kitsu login ok, expires in %d days", days)

            _mem_token = new_access_token
            _mem_exp = exp_dt
            _login_blocked_until = None

            try:
                col.update_one(
                    {"_id": "auth_token"},
                    {
                        "$set": {
                            "access_token": new_access_token,
                            "refresh_token": new_refresh_token,
                            "expires_at": exp_dt,
                            "updated_at": now_dt,
                        },
                        "$unset": {
                            "refresh_lock_until": "",
                            "last_failure_at": "",
                        },
                    },
                    upsert=True,
                )
            except Exception as ex:
                logger.warning("Failed to save kitsu_auth to Mongo: %s", ex)

            return _mem_token
        else:
            # Login failed: record failure backoff
            _login_blocked_until = now_dt + timedelta(seconds=_FAILURE_BACKOFF_SECONDS)
            try:
                col.update_one(
                    {"_id": "auth_token"},
                    {
                        "$set": {"last_failure_at": now_dt},
                        "$unset": {"refresh_lock_until": ""},
                    },
                    upsert=True,
                )
            except Exception as ex:
                logger.warning("Failed to record kitsu_auth failure to Mongo: %s", ex)
            return None


async def kitsu_authed_get(url: str, params: dict | None = None, timeout: float = 8.0) -> httpx.Response | None:
    """
    Sends an authenticated GET request to Kitsu with Bearer token.
    On 401 Unauthorized, forces a token refresh and retries once.
    Returns the httpx.Response, or None when login is unavailable.
    """
    if not kitsu_login_enabled():
        return None

    token = await get_kitsu_token(force_refresh=False)
    if not token:
        return None

    client = get_client()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.api+json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }
    try:
        resp = await client.get(url, params=params, headers=headers, timeout=timeout)
        if resp.status_code == 401:
            logger.info("Kitsu returned 401 Unauthorized: refreshing token and retrying once")
            new_token = await get_kitsu_token(force_refresh=True)
            if not new_token:
                return None
            headers["Authorization"] = f"Bearer {new_token}"
            resp = await client.get(url, params=params, headers=headers, timeout=timeout)
        return resp
    except Exception as e:
        logger.debug("kitsu_authed_get failed for %s: %s", url, e)
        return None

