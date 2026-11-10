import asyncio
import base64
import json
import logging
import math
from datetime import UTC, datetime, timedelta

from app.lib.languages import get_tvdb_language_codes, normalize_metadata_language
from app.services.http import get_client
from config import Config

# In-memory token cache and login failure backoff state
_mem_token: str | None = None
_mem_exp: datetime | None = None
_login_blocked_until: datetime | None = None
_token_lock = asyncio.Lock()

# Single-flight request coalescing & strong references for background SWR refreshes
_inflight: dict[tuple, asyncio.Task] = {}
_background_tasks: set[asyncio.Task] = set()

_PURGE_RETENTION = timedelta(days=30)
_DEFAULT_CALLER_TIMEOUT = 8.0
_MAX_EPISODE_PAGES = 50


def _get_col(name: str):
    from app.services.db import db

    return db.get_collection(name)


def _to_naive_utc(dt: datetime | None) -> datetime | None:
    if not dt or not isinstance(dt, datetime):
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _read_cache(col_name: str, query: dict, default):
    """
    Reads a cache document and returns (is_fresh_hit, data, stale_fallback, should_swr).
    - Fresh hit: expires_at > now and (purge_at is None or purge_at > now)
    - Stale-while-revalidate: expires_at <= now < purge_at
    - Stale fallback without purge_at (legacy/test doc): stale_fallback populated, should_swr=False
    - Past purge_at: treated as a full miss (stale_fallback=None)
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        cached = _get_col(col_name).find_one(query)
        if not cached:
            return False, default, None, False

        data = cached.get("data")
        if data is None:
            data = default

        exp = _to_naive_utc(cached.get("expires_at"))
        purge = _to_naive_utc(cached.get("purge_at"))

        if purge is not None and purge <= now:
            return False, default, None, False

        if exp is not None and exp > now:
            return True, data, None, False

        if purge is not None and purge > now:
            return False, default, data, True

        return False, default, data, False
    except Exception as e:
        logging.warning("Error reading %s for %s: %s", col_name, query, e)
        return False, default, None, False


def _write_cache(col_name: str, query: dict, doc_fields: dict, expires_at: datetime, now: datetime) -> None:
    purge_at = expires_at + _PURGE_RETENTION
    payload = {
        **doc_fields,
        "expires_at": expires_at,
        "purge_at": purge_at,
        "updated_at": now,
    }
    try:
        _get_col(col_name).update_one(query, {"$set": payload}, upsert=True)
    except Exception as ex:
        logging.warning("Failed to update %s for %s: %s", col_name, query, ex)


def _register_inflight(key: tuple, coro, *, background: bool = False) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _inflight[key] = task
    if background:
        _background_tasks.add(task)

    def _on_done(done_task: asyncio.Task) -> None:
        if _inflight.get(key) is done_task:
            _inflight.pop(key, None)
        _background_tasks.discard(done_task)
        if not done_task.cancelled():
            try:
                exc = done_task.exception()
                if exc is not None:
                    logging.debug("TVDB task %s failed: %s", key, exc)
            except Exception:
                pass

    task.add_done_callback(_on_done)
    return task


def _schedule_background_refresh(key: tuple, coro_factory) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    existing = _inflight.get(key)
    if existing is not None and not existing.done() and existing.get_loop() is loop:
        return
    _register_inflight(key, coro_factory(), background=True)


async def _await_inflight(
    key: tuple,
    coro_factory,
    *,
    stale_fallback,
    default,
    timeout: float = _DEFAULT_CALLER_TIMEOUT,
):
    task = _inflight.get(key)
    if task is None or task.done() or task.get_loop() is not asyncio.get_running_loop():
        task = _register_inflight(key, coro_factory(), background=False)
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except TimeoutError:
        logging.warning("Timed out waiting for TVDB inflight task %s", key)
        return stale_fallback if stale_fallback is not None else default
    except Exception as e:
        logging.warning("TVDB inflight task %s failed: %s", key, e)
        return stale_fallback if stale_fallback is not None else default


async def _tvdb_get(url: str, client, token: str, timeout: float = 5.0):
    resp = await client.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
    if resp.status_code == 401:
        logging.warning("TheTVDB returned 401 Unauthorized: refreshing token and retrying once")
        if _mem_token == token:
            invalidate_token()
            new_token = await get_valid_token(force_refresh=True)
        else:
            new_token = await get_valid_token(force_refresh=False)
            if not new_token or new_token == token:
                invalidate_token()
                new_token = await get_valid_token(force_refresh=True)
        if not new_token:
            return resp
        resp = await client.get(url, headers={"Authorization": f"Bearer {new_token}"}, timeout=timeout)
    return resp


def invalidate_token():
    """Flushes the active token from memory and MongoDB."""
    global _mem_token, _mem_exp
    _mem_token = None
    _mem_exp = None
    try:
        _get_col("tvdb_auth").delete_one({"_id": "auth_token"})
    except Exception as e:
        logging.warning("Failed to delete tvdb_auth from Mongo: %s", e)


async def get_valid_token(force_refresh: bool = False) -> str | None:
    """
    Retrieves a valid TheTVDB v4 Bearer token.
    Checks in-memory fast path, MongoDB (tvdb_auth), and authenticates via POST /v4/login.
    Shares a single 30-day session across all workers.
    Applies a 10-minute backoff after a failed login to avoid endpoint spam.
    """
    global _mem_token, _mem_exp, _login_blocked_until

    now = datetime.now(UTC)

    # In-memory fast path
    if not force_refresh and _mem_token and _mem_exp and _mem_exp > now:
        return _mem_token

    # Check 10-minute failure backoff
    if _login_blocked_until and _login_blocked_until > now:
        return None

    async with _token_lock:
        now = datetime.now(UTC)
        if not force_refresh and _mem_token and _mem_exp and _mem_exp > now:
            return _mem_token

        if _login_blocked_until and _login_blocked_until > now:
            return None

        # Check MongoDB tvdb_auth
        if not force_refresh:
            try:
                doc = _get_col("tvdb_auth").find_one({"_id": "auth_token"})
                if doc and doc.get("token") and doc.get("expires_at"):
                    exp = doc["expires_at"]
                    if not exp.tzinfo:
                        exp = exp.replace(tzinfo=UTC)
                    if exp > now:
                        _mem_token = doc["token"]
                        _mem_exp = exp
                        return _mem_token
            except Exception as e:
                logging.warning("Error reading tvdb_auth from Mongo: %s", e)

        # Authenticate with TheTVDB API
        api_key = (Config.TVDB_API_KEY or "").strip()
        if not api_key:
            return None

        try:
            client = get_client()
            resp = await client.post(
                "https://api4.thetvdb.com/v4/login",
                json={"apikey": api_key},
                timeout=5.0,
            )
            if resp.status_code == 200:
                res_data = resp.json().get("data", {})
                token = res_data.get("token")
                if token and isinstance(token, str):
                    exp_dt = None
                    try:
                        part = token.split(".")[1]
                        padded = part + "=" * ((4 - len(part) % 4) % 4)
                        payload = json.loads(base64.urlsafe_b64decode(padded))
                        if "exp" in payload:
                            exp_dt = datetime.fromtimestamp(payload["exp"], tz=UTC) - timedelta(days=1)
                    except Exception:
                        pass
                    if not exp_dt:
                        exp_dt = now + timedelta(days=28)

                    _mem_token = token
                    _mem_exp = exp_dt
                    _login_blocked_until = None

                    try:
                        _get_col("tvdb_auth").update_one(
                            {"_id": "auth_token"},
                            {
                                "$set": {
                                    "token": token,
                                    "expires_at": exp_dt.replace(tzinfo=None),
                                    "updated_at": now.replace(tzinfo=None),
                                }
                            },
                            upsert=True,
                        )
                    except Exception as ex:
                        logging.warning("Failed to save auth token to tvdb_auth: %s", ex)

                    return token

            logging.warning("TheTVDB login failed with HTTP %s: blocking login attempts for 10 minutes", resp.status_code)
            _login_blocked_until = now + timedelta(minutes=10)
            return None

        except Exception as e:
            logging.warning("Failed to connect to TheTVDB login endpoint: %s. Blocking login for 10 minutes.", e)
            _login_blocked_until = now + timedelta(minutes=10)
            return None


def _cache_remote_id(
    col,
    clean_id: str,
    entity_type: str | None,
    entity_id: str | None,
    ttl: timedelta,
    now: datetime,
) -> None:
    try:
        col.update_one(
            {"imdb_id": clean_id},
            {
                "$set": {
                    "imdb_id": clean_id,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "expires_at": now + ttl,
                    "updated_at": now,
                }
            },
            upsert=True,
        )
    except Exception:
        pass


async def _resolve_tvdb_remote_id_with_status(
    imdb_id: str, client, token: str
) -> tuple[str | None, str | None, bool]:
    """
    Resolves IMDb ID to a TheTVDB entity (movie or series) with 30-day hit and 24-hour miss caching.
    Returns (entity_type, entity_id, is_transient_error).
    """
    clean_id = (imdb_id or "").strip()
    if not clean_id:
        return None, None, False

    col = _get_col("tvdb_remote_ids")
    now = datetime.now(UTC).replace(tzinfo=None)

    try:
        cached = col.find_one({"imdb_id": clean_id})
        if cached:
            exp = _to_naive_utc(cached.get("expires_at"))
            if exp and exp > now:
                e_type = cached.get("entity_type")
                e_id = cached.get("entity_id")
                return (e_type, e_id, False) if e_type and e_id else (None, None, False)
    except Exception as e:
        logging.warning("Error reading tvdb_remote_ids for %s: %s", clean_id, e)

    async def _resolve_impl() -> tuple[str | None, str | None, bool]:
        url = f"https://api4.thetvdb.com/v4/search/remoteid/{clean_id}"
        try:
            resp = await _tvdb_get(url, client, token, timeout=5.0)
            if resp.status_code == 200:
                res_json = resp.json()
                data = res_json.get("data") if isinstance(res_json, dict) else None
                if isinstance(data, list):
                    for item in data:
                        if not isinstance(item, dict):
                            continue
                        for entity_type in ("movie", "series"):
                            entity = item.get(entity_type)
                            if isinstance(entity, dict) and entity.get("id"):
                                str_eid = str(entity["id"])
                                _cache_remote_id(col, clean_id, entity_type, str_eid, timedelta(days=30), now)
                                return entity_type, str_eid, False

                # No match found in list: cache 24h negative miss
                _cache_remote_id(col, clean_id, None, None, timedelta(days=1), now)
                return None, None, False

            if resp.status_code == 404:
                _cache_remote_id(col, clean_id, None, None, timedelta(days=1), now)
                return None, None, False

            logging.warning("TheTVDB remoteid returned HTTP %s for %s", resp.status_code, clean_id)
            return None, None, True
        except Exception as e:
            logging.warning("Failed to resolve TheTVDB remote ID for %s: %s", clean_id, e)
            return None, None, True

    return await _await_inflight(
        ("remote_id", clean_id),
        _resolve_impl,
        stale_fallback=(None, None, True),
        default=(None, None, True),
    )


async def resolve_tvdb_remote_id(imdb_id: str, client, token: str) -> tuple[str | None, str | None]:
    """
    Resolves IMDb ID to a TheTVDB entity (movie or series) with 30-day hit and 24-hour miss caching.
    Returns (entity_type, entity_id) where entity_type is 'movie' or 'series'.
    """
    entity_type, entity_id, _ = await _resolve_tvdb_remote_id_with_status(imdb_id, client, token)
    return entity_type, entity_id


def _trim_artworks(
    raw_arts: list[dict],
    allowed_types: set[int],
    season_id_to_number: dict[int, int] | None = None,
) -> list[dict]:
    trimmed = []
    for art in raw_arts:
        if not isinstance(art, dict):
            continue
        art_type = art.get("type")
        if allowed_types and art_type not in allowed_types:
            continue
        img = art.get("image")
        if not img or not isinstance(img, str):
            continue
        entry = {
            "image": img,
            "score": float(art.get("score") or 0),
            "includesText": bool(art.get("includesText")),
            "language": art.get("language") if isinstance(art.get("language"), str) else None,
            # Unknown width stays None so pick_tvdb_background keeps it instead of treating it as too small
            "width": int(art["width"]) if art.get("width") else None,
            "type": art_type,
        }
        if season_id_to_number is not None:
            season_num = art.get("seasonNumber")
            if season_num is None and art.get("seasonId") is not None:
                try:
                    season_num = season_id_to_number.get(int(art["seasonId"]))
                except (ValueError, TypeError):
                    season_num = None
            if season_num is not None:
                try:
                    entry["seasonNumber"] = int(season_num)
                except (ValueError, TypeError):
                    pass
        trimmed.append(entry)
    return trimmed


async def _fetch_tvdb_artworks_impl(normalized_kind: str, str_id: str, stale_data: list[dict] | None) -> list[dict]:
    if not Config.TVDB_API_KEY:
        return stale_data if stale_data is not None else []

    token = await get_valid_token()
    if not token:
        return stale_data if stale_data is not None else []

    client = get_client()
    now = datetime.now(UTC).replace(tzinfo=None)
    query = {"kind": normalized_kind, "ext_id": str_id}

    if normalized_kind == "tv":
        endpoint_url = f"https://api4.thetvdb.com/v4/series/{str_id}/artworks?type=3"
        allowed_types = {3}
    else:
        # Movies: ext_id is imdb_id
        entity_type, entity_id, is_transient = await _resolve_tvdb_remote_id_with_status(str_id, client, token)
        if is_transient:
            return stale_data if stale_data is not None else []
        if not entity_type or not entity_id:
            _write_cache(
                "tvdb_meta_cache",
                query,
                {"kind": normalized_kind, "ext_id": str_id, "data": []},
                now + timedelta(days=1),
                now,
            )
            return []

        token = await get_valid_token() or token
        if entity_type == "movie":
            endpoint_url = f"https://api4.thetvdb.com/v4/movies/{entity_id}/extended"
            allowed_types = {15}
        else:
            endpoint_url = f"https://api4.thetvdb.com/v4/series/{entity_id}/artworks?type=3"
            allowed_types = {3}

    try:
        resp = await _tvdb_get(endpoint_url, client, token, timeout=5.0)
        if resp.status_code == 200:
            payload = resp.json()
            data_obj = payload.get("data") if isinstance(payload, dict) else None
            raw_arts = (data_obj.get("artworks") or []) if isinstance(data_obj, dict) else []
            trimmed = _trim_artworks(raw_arts, allowed_types)
            ttl = timedelta(days=7) if trimmed else timedelta(days=1)
            _write_cache(
                "tvdb_meta_cache",
                query,
                {"kind": normalized_kind, "ext_id": str_id, "data": trimmed},
                now + ttl,
                now,
            )
            return trimmed

        if resp.status_code == 404:
            _write_cache(
                "tvdb_meta_cache",
                query,
                {"kind": normalized_kind, "ext_id": str_id, "data": []},
                now + timedelta(days=1),
                now,
            )
            return []

        logging.warning(
            "TheTVDB returned %s for %s/%s, falling back to stale cache if present",
            resp.status_code,
            normalized_kind,
            str_id,
        )
        return stale_data if stale_data is not None else []

    except Exception as e:
        logging.warning("Failed to fetch artworks from TheTVDB (%s/%s): %s", normalized_kind, str_id, e)
        if stale_data is not None:
            return stale_data

    return []


async def fetch_tvdb_artworks(kind: str, ext_id: str | int | None) -> list[dict]:
    """
    Fetches and caches TheTVDB artworks for a series (tvdb_id) or movie (imdb_id).
    - Series artworks: GET /v4/series/{id}/artworks?type=3
    - Movie artworks: GET /v4/search/remoteid/{imdb_id} -> GET /v4/movies/{id}/extended
    - Cached in tvdb_meta_cache under (kind, ext_id): 7 days for hits, 24 hours for misses, +30d purge_at
    - Serves stale cache immediately when expires_at <= now < purge_at and triggers a single background refresh
    - Falls back to stale cache on upstream 429 or 5xx errors
    """
    if not ext_id or str(ext_id).strip() == "":
        return []

    normalized_kind = "movies" if kind in ("movie", "movies") else "tv"
    str_id = str(ext_id).strip()
    query = {"kind": normalized_kind, "ext_id": str_id}
    key = ("artworks", normalized_kind, str_id)

    is_hit, data, stale_data, should_swr = _read_cache("tvdb_meta_cache", query, [])
    if is_hit:
        return data

    if should_swr:
        if Config.TVDB_API_KEY:
            _schedule_background_refresh(
                key, lambda: _fetch_tvdb_artworks_impl(normalized_kind, str_id, stale_data)
            )
        return stale_data if stale_data is not None else []

    if not Config.TVDB_API_KEY:
        return stale_data if stale_data is not None else []

    return await _await_inflight(
        key,
        lambda: _fetch_tvdb_artworks_impl(normalized_kind, str_id, stale_data),
        stale_fallback=stale_data,
        default=[],
    )


def _extract_translation(payload: dict, expected_lang: str) -> dict:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return {}
    name = data.get("name").strip() if isinstance(data.get("name"), str) else ""
    overview = data.get("overview").strip() if isinstance(data.get("overview"), str) else ""
    if not name and not overview:
        return {}
    lang_val = data.get("language")
    return {
        "name": name,
        "overview": overview,
        "language": lang_val.strip() if isinstance(lang_val, str) and lang_val.strip() else expected_lang,
    }


async def _fetch_tvdb_translation_impl(
    normalized_kind: str,
    str_id: str,
    norm_lang: str,
    primary_tvdb: str,
    fallback_tvdb: str | None,
    is_releasing: bool,
    stale_data: dict | None,
) -> dict:
    if not Config.TVDB_API_KEY:
        return stale_data if stale_data is not None else {}

    token = await get_valid_token()
    if not token:
        return stale_data if stale_data is not None else {}

    client = get_client()
    now = datetime.now(UTC).replace(tzinfo=None)
    query = {"kind": normalized_kind, "ext_id": str_id, "lang": norm_lang}

    if normalized_kind == "tv":
        entity_prefix = f"series/{str_id}"
    else:
        entity_type, entity_id, is_transient = await _resolve_tvdb_remote_id_with_status(str_id, client, token)
        if is_transient:
            return stale_data if stale_data is not None else {}
        if not entity_type or not entity_id:
            _write_cache(
                "tvdb_translation_cache",
                query,
                {"kind": normalized_kind, "ext_id": str_id, "lang": norm_lang, "data": {}},
                now + timedelta(days=1),
                now,
            )
            return {}
        entity_prefix = f"movies/{entity_id}" if entity_type == "movie" else f"series/{entity_id}"
        token = await get_valid_token() or token

    url = f"https://api4.thetvdb.com/v4/{entity_prefix}/translations/{primary_tvdb}"
    result: dict = {}
    fallback_transient_error = False
    try:
        resp = await _tvdb_get(url, client, token, timeout=5.0)
        if resp.status_code == 200:
            result = _extract_translation(resp.json(), primary_tvdb)
        elif resp.status_code == 404:
            result = {}
        else:
            logging.warning(
                "TheTVDB translation returned %s for %s/%s (%s), falling back to stale cache",
                resp.status_code,
                normalized_kind,
                str_id,
                norm_lang,
            )
            return stale_data if stale_data is not None else {}

        # Portuguese (pt <-> por) fallback if primary is empty or missing overview or name
        if fallback_tvdb and (not result or not result.get("overview") or not result.get("name")):
            token = await get_valid_token() or token
            fb_url = f"https://api4.thetvdb.com/v4/{entity_prefix}/translations/{fallback_tvdb}"
            try:
                fb_resp = await _tvdb_get(fb_url, client, token, timeout=5.0)
                if fb_resp.status_code == 200:
                    fb_res = _extract_translation(fb_resp.json(), fallback_tvdb)
                    if fb_res:
                        if not result:
                            result = fb_res
                        else:
                            if not result.get("overview") and fb_res.get("overview"):
                                result["overview"] = fb_res["overview"]
                            if not result.get("name") and fb_res.get("name"):
                                result["name"] = fb_res["name"]
                elif fb_resp.status_code != 404:
                    fallback_transient_error = True
            except Exception as fb_err:
                logging.warning(
                    "Failed to fetch fallback TheTVDB translation (%s/%s/%s): %s",
                    normalized_kind,
                    str_id,
                    fallback_tvdb,
                    fb_err,
                )
                fallback_transient_error = True
    except Exception as e:
        logging.warning("Failed to fetch TheTVDB translation (%s/%s/%s): %s", normalized_kind, str_id, norm_lang, e)
        return stale_data if stale_data is not None else {}

    if fallback_transient_error:
        if not result:
            return stale_data if stale_data is not None else {}
        if stale_data and isinstance(stale_data, dict) and stale_data.get("name") and stale_data.get("overview"):
            return stale_data
        return result

    if result:
        ttl = timedelta(hours=12) if is_releasing else timedelta(days=7)
    else:
        ttl = timedelta(days=1)

    _write_cache(
        "tvdb_translation_cache",
        query,
        {"kind": normalized_kind, "ext_id": str_id, "lang": norm_lang, "data": result},
        now + ttl,
        now,
    )
    return result


async def fetch_tvdb_translation(
    kind: str,
    ext_id: str | int | None,
    lang: str,
    is_releasing: bool = False,
) -> dict:
    """
    Fetches localized title and synopsis via lightweight TheTVDB v4 translation endpoint (~1.4 KB).
    - Series: GET /v4/series/{id}/translations/{tvdb_lang}
    - Movies: resolve_tvdb_remote_id(imdb_id) -> GET /v4/movies/{id}/translations/{tvdb_lang}
    - Returns {'name': str, 'overview': str, 'language': str} on hit, or {} on miss.
    """
    if not ext_id or str(ext_id).strip() == "":
        return {}

    normalized_kind = "movies" if kind in ("movie", "movies") else "tv"
    str_id = str(ext_id).strip()
    norm_lang = normalize_metadata_language(lang)
    primary_tvdb, fallback_tvdb = get_tvdb_language_codes(norm_lang)
    query = {"kind": normalized_kind, "ext_id": str_id, "lang": norm_lang}
    key = ("translation", normalized_kind, str_id, norm_lang)

    is_hit, data, stale_data, should_swr = _read_cache("tvdb_translation_cache", query, {})
    if is_hit:
        return data if isinstance(data, dict) else {}

    if should_swr:
        if Config.TVDB_API_KEY:
            _schedule_background_refresh(
                key,
                lambda: _fetch_tvdb_translation_impl(
                    normalized_kind, str_id, norm_lang, primary_tvdb, fallback_tvdb, is_releasing, stale_data
                ),
            )
        return stale_data if isinstance(stale_data, dict) else {}

    if not Config.TVDB_API_KEY:
        return stale_data if isinstance(stale_data, dict) else {}

    return await _await_inflight(
        key,
        lambda: _fetch_tvdb_translation_impl(
            normalized_kind, str_id, norm_lang, primary_tvdb, fallback_tvdb, is_releasing, stale_data
        ),
        stale_fallback=stale_data if isinstance(stale_data, dict) else {},
        default={},
    )


def _extract_season_mappings(extended_data: dict) -> tuple[dict[int, int], dict[str, str]]:
    """
    Builds season_id -> season_number and str(season_number) -> image_url from /series/{id}/extended?short=true.
    Prefers Official ('Aired Order') seasons before alternative orders (e.g. 'Absolute Order').
    """
    seasons = extended_data.get("seasons") if isinstance(extended_data, dict) else None
    if not isinstance(seasons, list):
        return {}, {}

    id_to_num: dict[int, int] = {}
    season_images: dict[str, str] = {}

    official_first = sorted(
        (s for s in seasons if isinstance(s, dict)),
        key=lambda s: 0
        if (
            not s.get("type")
            or (isinstance(s.get("type"), dict) and (s["type"].get("type") == "official" or s["type"].get("id") == 1))
        )
        else 1,
    )

    for s in official_first:
        sid = s.get("id")
        snum = s.get("number")
        if sid is None or snum is None:
            continue
        try:
            sid_int = int(sid)
            snum_int = int(snum)
        except (ValueError, TypeError):
            continue
        if sid_int not in id_to_num:
            id_to_num[sid_int] = snum_int

        is_official = not s.get("type") or (
            isinstance(s.get("type"), dict) and (s["type"].get("type") == "official" or s["type"].get("id") == 1)
        )
        img = s.get("image")
        if is_official and snum_int > 0 and isinstance(img, str) and img.strip():
            season_images.setdefault(str(snum_int), img.strip())

    return id_to_num, season_images


async def _fetch_series_posters(series_id: str, client, token: str, stale_data: dict | None) -> dict | None:
    """Fetches type=2, type=7 artworks and extended?short=true for a TVDB series. Returns None on transient 429/5xx."""
    urls = [
        f"https://api4.thetvdb.com/v4/series/{series_id}/artworks?type=2",
        f"https://api4.thetvdb.com/v4/series/{series_id}/artworks?type=7",
        f"https://api4.thetvdb.com/v4/series/{series_id}/extended?short=true",
    ]
    resps = await asyncio.gather(
        *[_tvdb_get(u, client, token, timeout=5.0) for u in urls],
        return_exceptions=True,
    )

    for r in resps:
        if isinstance(r, Exception):
            raise r
        if r.status_code not in (200, 404):
            return None

    r_series, r_seasons, r_ext = resps
    if r_series.status_code == 404 and r_ext.status_code == 404:
        return {}

    ext_data = (r_ext.json().get("data") or {}) if r_ext.status_code == 200 and isinstance(r_ext.json(), dict) else {}
    if not isinstance(ext_data, dict):
        ext_data = {}

    season_id_map, season_images = _extract_season_mappings(ext_data)
    default_img = ext_data.get("image")
    default_image = default_img.strip() if isinstance(default_img, str) and default_img.strip() else None

    raw_series_arts = (
        ((r_series.json().get("data") or {}).get("artworks") or [])
        if r_series.status_code == 200 and isinstance(r_series.json(), dict)
        else []
    )
    raw_season_arts = (
        ((r_seasons.json().get("data") or {}).get("artworks") or [])
        if r_seasons.status_code == 200 and isinstance(r_seasons.json(), dict)
        else []
    )

    series_posters = _trim_artworks(raw_series_arts, {2})
    season_posters = _trim_artworks(raw_season_arts, {7}, season_id_to_number=season_id_map)

    if not series_posters and not season_posters and not season_images and not default_image:
        return {}

    return {
        "series_posters": series_posters,
        "season_posters": season_posters,
        "season_images": season_images,
        "default_image": default_image,
    }


async def _fetch_tvdb_posters_impl(
    normalized_kind: str,
    str_id: str,
    is_releasing: bool,
    stale_data: dict | None,
) -> dict:
    if not Config.TVDB_API_KEY:
        return stale_data if stale_data is not None else {}

    token = await get_valid_token()
    if not token:
        return stale_data if stale_data is not None else {}

    client = get_client()
    now = datetime.now(UTC).replace(tzinfo=None)
    query = {"kind": normalized_kind, "ext_id": str_id}

    try:
        result: dict | None = None
        if normalized_kind == "tv":
            result = await _fetch_series_posters(str_id, client, token, stale_data)
        else:
            entity_type, entity_id, is_transient = await _resolve_tvdb_remote_id_with_status(str_id, client, token)
            if is_transient:
                return stale_data if stale_data is not None else {}
            if not entity_type or not entity_id:
                _write_cache(
                    "tvdb_posters_cache",
                    query,
                    {"kind": normalized_kind, "ext_id": str_id, "data": {}},
                    now + timedelta(days=1),
                    now,
                )
                return {}
            token = await get_valid_token() or token
            if entity_type == "series":
                result = await _fetch_series_posters(entity_id, client, token, stale_data)
            else:
                # TVDB v4 returns 400 for /movies/{id}/artworks?type=14; movie posters (type=14) are served on /movies/{id}/extended
                resp = await _tvdb_get(f"https://api4.thetvdb.com/v4/movies/{entity_id}/extended", client, token, timeout=5.0)
                if resp.status_code == 404:
                    result = {}
                elif resp.status_code == 200:
                    data_obj = resp.json().get("data") if isinstance(resp.json(), dict) else {}
                    if not isinstance(data_obj, dict):
                        data_obj = {}
                    raw_arts = data_obj.get("artworks") or []
                    movie_posters = _trim_artworks(raw_arts, {14})
                    default_img = data_obj.get("image")
                    default_image = default_img.strip() if isinstance(default_img, str) and default_img.strip() else None
                    if movie_posters or default_image:
                        result = {
                            "series_posters": movie_posters,
                            "season_posters": [],
                            "season_images": {},
                            "default_image": default_image,
                        }
                    else:
                        result = {}
                else:
                    result = None

        if result is None:
            logging.warning("TheTVDB posters hit transient error for %s/%s, falling back to stale cache", normalized_kind, str_id)
            return stale_data if stale_data is not None else {}

        ttl = (timedelta(hours=12) if is_releasing else timedelta(days=7)) if result else timedelta(days=1)
        _write_cache(
            "tvdb_posters_cache",
            query,
            {"kind": normalized_kind, "ext_id": str_id, "data": result},
            now + ttl,
            now,
        )
        return result
    except Exception as e:
        logging.warning("Failed to fetch TheTVDB posters (%s/%s): %s", normalized_kind, str_id, e)
        return stale_data if stale_data is not None else {}


async def fetch_tvdb_posters(kind: str, ext_id: str | int | None, is_releasing: bool = False) -> dict:
    """
    Fetches and caches TheTVDB cover posters ONLY when the caller requests TVDB cover posters.
    - Series: /v4/series/{id}/artworks?type=2, ?type=7, and /extended?short=true
    - Movies: resolve_tvdb_remote_id(imdb_id) -> /v4/movies/{id}/extended (type=14 + default image)
    - Cached in tvdb_posters_cache under (kind, ext_id): 12h airing / 7d finished / 24h miss, +30d purge_at
    """
    if not ext_id or str(ext_id).strip() == "":
        return {}

    normalized_kind = "movies" if kind in ("movie", "movies") else "tv"
    str_id = str(ext_id).strip()
    query = {"kind": normalized_kind, "ext_id": str_id}
    key = ("posters", normalized_kind, str_id)

    is_hit, data, stale_data, should_swr = _read_cache("tvdb_posters_cache", query, {})
    if is_hit:
        return data if isinstance(data, dict) else {}

    if should_swr:
        if Config.TVDB_API_KEY:
            _schedule_background_refresh(
                key, lambda: _fetch_tvdb_posters_impl(normalized_kind, str_id, is_releasing, stale_data)
            )
        return stale_data if isinstance(stale_data, dict) else {}

    if not Config.TVDB_API_KEY:
        return stale_data if isinstance(stale_data, dict) else {}

    return await _await_inflight(
        key,
        lambda: _fetch_tvdb_posters_impl(normalized_kind, str_id, is_releasing, stale_data),
        stale_fallback=stale_data if isinstance(stale_data, dict) else {},
        default={},
    )


def _rank_posters(candidates: list[dict], primary_tvdb: str, fallback_tvdb: str | None) -> str | None:
    valid = [
        art
        for art in candidates
        if isinstance(art, dict) and isinstance(art.get("image"), str) and art["image"].strip()
    ]
    if not valid:
        return None

    def _rank_key(item: dict):
        lang = item.get("language")
        lang_str = str(lang).strip().lower() if lang else ""
        if lang_str == primary_tvdb:
            lang_rank = 5
        elif fallback_tvdb and lang_str == fallback_tvdb:
            lang_rank = 4
        elif not lang_str:
            lang_rank = 3
        elif lang_str == "eng":
            lang_rank = 2
        elif lang_str == "jpn":
            lang_rank = 1
        else:
            lang_rank = 0

        try:
            score = float(item.get("score") or 0)
        except (ValueError, TypeError):
            score = 0.0

        return (lang_rank, score)

    best = max(valid, key=_rank_key)
    return best["image"].strip()


def pick_tvdb_poster(
    posters_data: dict | None,
    lang: str = "eng",
    season_number: int | None = None,
) -> str | None:
    """
    Selects the best TheTVDB cover poster:
    - If season_number (> 0) is provided (single-season / split-cour Kitsu entry), prefers type=7 season posters
      for that season (or extended season image), falling back to type=2 series posters.
    - Multi-season or unmapped entries pass season_number=None and receive type=2 series posters (or type=14 for movies).
    - Language preference: selected lang -> fallback lang (pt<->por) -> neutral -> eng -> jpn -> score descending.
    """
    if not posters_data or not isinstance(posters_data, dict):
        return None

    primary_tvdb, fallback_tvdb = get_tvdb_language_codes(lang)

    if season_number is not None and season_number > 0:
        season_posters = [
            p
            for p in (posters_data.get("season_posters") or [])
            if isinstance(p, dict) and p.get("seasonNumber") == season_number
        ]
        picked_season = _rank_posters(season_posters, primary_tvdb, fallback_tvdb)
        if picked_season:
            return picked_season

        season_images = posters_data.get("season_images") or {}
        if isinstance(season_images, dict):
            s_img = season_images.get(str(season_number))
            if isinstance(s_img, str) and s_img.strip():
                return s_img.strip()

    series_posters = posters_data.get("series_posters") or []
    picked_series = _rank_posters(series_posters, primary_tvdb, fallback_tvdb)
    if picked_series:
        return picked_series

    default_image = posters_data.get("default_image")
    if isinstance(default_image, str) and default_image.strip():
        return default_image.strip()
    return None


def _trim_episodes(raw_eps: list[dict]) -> list[dict]:
    cleaned: list[dict] = []
    for ep in raw_eps:
        if not isinstance(ep, dict):
            continue
        ep_id = ep.get("id")
        if ep_id is None:
            continue
        try:
            season_num = int(ep["seasonNumber"]) if ep.get("seasonNumber") is not None else 0
        except (ValueError, TypeError):
            season_num = 0
        try:
            ep_num = int(ep["number"]) if ep.get("number") is not None else 0
        except (ValueError, TypeError):
            ep_num = 0
        try:
            abs_num = int(ep["absoluteNumber"]) if ep.get("absoluteNumber") is not None else 0
        except (ValueError, TypeError):
            abs_num = 0

        name = ep.get("name").strip() if isinstance(ep.get("name"), str) else ""
        overview = ep.get("overview").strip() if isinstance(ep.get("overview"), str) else ""

        cleaned.append({
            "id": int(ep_id) if str(ep_id).isdigit() else ep_id,
            "seasonNumber": season_num,
            "number": ep_num,
            "absoluteNumber": abs_num,
            "name": name,
            "overview": overview,
        })
    return cleaned


def _overview_coverage(episodes: list[dict]) -> tuple[int, int, float]:
    """Counts non-empty overviews strictly on regular (seasonNumber > 0) episodes."""
    regular = [ep for ep in episodes if (ep.get("seasonNumber") or 0) > 0]
    if not regular:
        return 0, 0, 0.0
    with_overview = sum(1 for ep in regular if (ep.get("overview") or "").strip())
    return with_overview, len(regular), (with_overview / len(regular))


async def _fetch_episodes_for_lang(series_id: str, tvdb_lang: str, client, token: str) -> list[dict] | None:
    """
    Fetches all pages of /v4/series/{id}/episodes/default/{tvdb_lang}.
    Returns cleaned episode list (including seasonNumber <= 0 for exact tvdbId matches),
    [] on 404/empty, or None on transient 400/401/403/429/5xx errors.
    """
    base_url = f"https://api4.thetvdb.com/v4/series/{series_id}/episodes/default/{tvdb_lang}"
    resp0 = await _tvdb_get(f"{base_url}?page=0", client, token, timeout=5.0)
    if resp0.status_code == 404:
        return []
    if resp0.status_code != 200:
        return None

    payload0 = resp0.json()
    if not isinstance(payload0, dict):
        return []

    raw_eps = list(((payload0.get("data") or {}).get("episodes")) or [])
    links = payload0.get("links") or {}
    next_url = links.get("next") if isinstance(links, dict) else None

    if next_url:
        token = await get_valid_token() or token
        total_items = links.get("total_items")
        page_size = links.get("page_size")
        if isinstance(total_items, int) and isinstance(page_size, int) and page_size > 0 and total_items > page_size:
            total_pages = min(math.ceil(total_items / page_size), _MAX_EPISODE_PAGES)
            if total_pages > 1:
                page_resps = await asyncio.gather(
                    *[_tvdb_get(f"{base_url}?page={p}", client, token, timeout=5.0) for p in range(1, total_pages)],
                    return_exceptions=True,
                )
                last_links = {}
                for pr in page_resps:
                    if isinstance(pr, Exception):
                        raise pr
                    if pr.status_code not in (200, 404):
                        return None
                    if pr.status_code == 200:
                        pj = pr.json()
                        if isinstance(pj, dict):
                            raw_eps.extend(((pj.get("data") or {}).get("episodes")) or [])
                            last_links = pj.get("links") or {}
                next_url = last_links.get("next") if isinstance(last_links, dict) else None
                page_counter = total_pages
            else:
                next_url = None
                page_counter = 1
        else:
            page_counter = 1

        while next_url and page_counter < _MAX_EPISODE_PAGES:
            full_next = next_url if next_url.startswith("http") else f"https://api4.thetvdb.com{next_url}"
            pr = await _tvdb_get(full_next, client, token, timeout=5.0)
            if pr.status_code == 404:
                break
            if pr.status_code != 200:
                return None
            pj = pr.json()
            if not isinstance(pj, dict):
                break
            raw_eps.extend(((pj.get("data") or {}).get("episodes")) or [])
            next_links = pj.get("links") or {}
            next_url = next_links.get("next") if isinstance(next_links, dict) else None
            page_counter += 1

    return _trim_episodes(raw_eps)


def _merge_portuguese_episodes(
    primary_eps: list[dict],
    fb_eps: list[dict],
    prefer_fb_overviews: bool,
) -> list[dict]:
    """
    Merges primary and fallback Portuguese episode lists while always preserving primary names first,
    backfilling missing overviews, and retaining primary-only regular (seasonNumber > 0) episodes.
    """
    if not primary_eps:
        return fb_eps
    if not fb_eps:
        return primary_eps

    if prefer_fb_overviews:
        primary_by_id = {ep["id"]: ep for ep in primary_eps if ep.get("id") is not None}
        seen_ids: set = set()
        merged: list[dict] = []
        for ep in fb_eps:
            ep_id = ep.get("id")
            alt = primary_by_id.get(ep_id) if ep_id is not None else None
            if ep_id is not None:
                seen_ids.add(ep_id)
            if alt:
                # TheTVDB fills untranslated names with English, so only trust the primary name
                # when the primary variant actually translated that episode (it has an overview)
                primary_translated = bool(alt.get("overview"))
                merged.append({
                    **ep,
                    "name": (alt.get("name") if primary_translated else ep.get("name")) or ep.get("name") or alt.get("name", ""),
                    "overview": ep.get("overview") or alt.get("overview", ""),
                })
            else:
                merged.append(ep)
        for ep in primary_eps:
            ep_id = ep.get("id")
            if ep_id is not None and ep_id not in seen_ids and (ep.get("seasonNumber") or 0) > 0:
                merged.append(ep)
        return merged

    fb_by_id = {ep["id"]: ep for ep in fb_eps if ep.get("id") is not None}
    merged = []
    for ep in primary_eps:
        alt = fb_by_id.get(ep.get("id")) if ep.get("id") is not None else None
        if alt:
            merged.append({
                **ep,
                "name": ep.get("name") or alt.get("name", ""),
                "overview": ep.get("overview") or alt.get("overview", ""),
            })
        else:
            merged.append(ep)
    return merged


async def _fetch_tvdb_episodes_impl(
    str_id: str,
    norm_lang: str,
    primary_tvdb: str,
    fallback_tvdb: str | None,
    is_releasing: bool,
    stale_data: list[dict] | None,
) -> list[dict]:
    if not Config.TVDB_API_KEY:
        return stale_data if stale_data is not None else []

    token = await get_valid_token()
    if not token:
        return stale_data if stale_data is not None else []

    client = get_client()
    now = datetime.now(UTC).replace(tzinfo=None)
    query = {"tvdb_id": str_id, "lang": norm_lang}

    try:
        primary_eps = await _fetch_episodes_for_lang(str_id, primary_tvdb, client, token)
        if primary_eps is None:
            logging.warning("TheTVDB episodes hit transient error for %s (%s), falling back to stale cache", str_id, norm_lang)
            return stale_data if stale_data is not None else []

        final_eps = primary_eps

        if fallback_tvdb:
            primary_count, primary_total, primary_ratio = _overview_coverage(primary_eps)
            if primary_total == 0 or primary_ratio < 0.5:
                token = await get_valid_token() or token
                fb_eps = await _fetch_episodes_for_lang(str_id, fallback_tvdb, client, token)
                if fb_eps is None:
                    logging.warning(
                        "TheTVDB fallback episodes hit transient error for %s (%s), skipping incomplete cache",
                        str_id,
                        norm_lang,
                    )
                    if stale_data:
                        return stale_data
                    if stale_data is not None and not primary_eps:
                        return stale_data
                    return primary_eps
                fb_count, _, _ = _overview_coverage(fb_eps)
                final_eps = _merge_portuguese_episodes(
                    primary_eps,
                    fb_eps,
                    prefer_fb_overviews=(fb_count > primary_count or not primary_eps),
                )

        ttl = (timedelta(hours=4) if is_releasing else timedelta(days=7)) if final_eps else timedelta(days=1)
        _write_cache(
            "tvdb_episodes_cache",
            query,
            {"tvdb_id": str_id, "lang": norm_lang, "data": final_eps},
            now + ttl,
            now,
        )
        return final_eps
    except Exception as e:
        logging.warning("Failed to fetch TheTVDB episodes (%s/%s): %s", str_id, norm_lang, e)
        return stale_data if stale_data is not None else []


async def fetch_tvdb_episodes(
    tvdb_id: str | int | None,
    lang: str,
    is_releasing: bool = False,
) -> list[dict]:
    """
    Fetches paginated TheTVDB episodes in the requested language (/v4/series/{id}/episodes/default/{lang}).
    - Preserves seasonNumber <= 0 specials so callers can match exact tvdbId while excluding them from season/abs maps.
    - Portuguese (pt/por): checks alternate variant only when < 50% of seasonNumber > 0 episodes have a non-empty overview.
    - Cached in tvdb_episodes_cache under (tvdb_id, lang): 4h airing / 7d finished / 24h miss, +30d purge_at.
    """
    if not tvdb_id or str(tvdb_id).strip() == "":
        return []

    str_id = str(tvdb_id).strip()
    norm_lang = normalize_metadata_language(lang)
    primary_tvdb, fallback_tvdb = get_tvdb_language_codes(norm_lang)
    query = {"tvdb_id": str_id, "lang": norm_lang}
    key = ("episodes", str_id, norm_lang)

    is_hit, data, stale_data, should_swr = _read_cache("tvdb_episodes_cache", query, [])
    if is_hit:
        return data if isinstance(data, list) else []

    if should_swr:
        if Config.TVDB_API_KEY:
            _schedule_background_refresh(
                key,
                lambda: _fetch_tvdb_episodes_impl(
                    str_id, norm_lang, primary_tvdb, fallback_tvdb, is_releasing, stale_data
                ),
            )
        return stale_data if isinstance(stale_data, list) else []

    if not Config.TVDB_API_KEY:
        return stale_data if isinstance(stale_data, list) else []

    return await _await_inflight(
        key,
        lambda: _fetch_tvdb_episodes_impl(
            str_id, norm_lang, primary_tvdb, fallback_tvdb, is_releasing, stale_data
        ),
        stale_fallback=stale_data if isinstance(stale_data, list) else [],
        default=[],
    )


def pick_tvdb_background(artworks: list[dict] | None) -> str | None:
    """
    Selects the highest-ranked 1080p/4K background from TheTVDB artworks.
    Filters:
      - width >= 1280 (if width is provided)
      - valid image URL
    Ranking order:
      1. Textless first (includesText is False > True)
      2. Language preference: Neutral (None or empty) > 'eng' > 'jpn' > others
      3. Artwork score descending
    """
    if not artworks or not isinstance(artworks, list):
        return None

    candidates = []
    for art in artworks:
        if not isinstance(art, dict):
            continue
        img = art.get("image")
        if not img or not isinstance(img, str) or not img.strip():
            continue
        width = art.get("width")
        if width is not None and width < 1280:
            continue
        candidates.append(art)

    if not candidates:
        return None

    def _rank_key(item: dict):
        # 1. Textless first
        is_textless = 1 if item.get("includesText") is False else 0

        # 2. Language preference: Neutral > eng > jpn > other
        lang = item.get("language")
        lang_str = str(lang).strip().lower() if lang else ""
        if not lang_str:
            lang_score = 3
        elif lang_str == "eng":
            lang_score = 2
        elif lang_str == "jpn":
            lang_score = 1
        else:
            lang_score = 0

        # 3. Upstream score
        try:
            score = float(item.get("score") or 0)
        except (ValueError, TypeError):
            score = 0.0

        return (is_textless, lang_score, score)

    sorted_candidates = sorted(candidates, key=_rank_key, reverse=True)
    return sorted_candidates[0].get("image") if sorted_candidates else None


async def bg_warm_tvdb(items: list[tuple[str, str]]):
    """
    Background non-blocking task to fetch and cache TheTVDB artworks for items missing from tvdb_meta_cache.
    Accepts list of tuples: (kind, ext_id).
    """
    sem = asyncio.Semaphore(5)

    async def fetch_one(kind: str, ext_id: str):
        if not ext_id:
            return
        async with sem:
            try:
                await fetch_tvdb_artworks(kind, ext_id)
            except Exception:
                pass

    tasks = [fetch_one(k, eid) for (k, eid) in items]
    await asyncio.gather(*tasks, return_exceptions=True)
