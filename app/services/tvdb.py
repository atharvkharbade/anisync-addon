import asyncio
import base64
import json
import logging
from datetime import UTC, datetime, timedelta

from config import Config
from app.services.http import get_client

# In-memory token cache and login failure backoff state
_mem_token: str | None = None
_mem_exp: datetime | None = None
_login_blocked_until: datetime | None = None
_token_lock = asyncio.Lock()


def invalidate_token():
    """Flushes the active token from memory and MongoDB."""
    global _mem_token, _mem_exp
    _mem_token = None
    _mem_exp = None
    try:
        from app.services.db import db
        db.get_collection("tvdb_auth").delete_one({"_id": "auth_token"})
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
                from app.services.db import db
                doc = db.get_collection("tvdb_auth").find_one({"_id": "auth_token"})
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
                        from app.services.db import db
                        db.get_collection("tvdb_auth").update_one(
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


async def resolve_tvdb_remote_id(imdb_id: str, client, token: str) -> tuple[str | None, str | None]:
    """
    Resolves IMDb ID to a TheTVDB entity (movie or series) with 30-day hit and 24-hour miss caching.
    Returns (entity_type, entity_id) where entity_type is 'movie' or 'series'.
    """
    clean_id = (imdb_id or "").strip()
    if not clean_id:
        return None, None

    from app.services.db import db
    col = db.get_collection("tvdb_remote_ids")
    now = datetime.now(UTC).replace(tzinfo=None)

    try:
        cached = col.find_one({"imdb_id": clean_id})
        if cached:
            exp = cached.get("expires_at")
            if exp:
                if exp.tzinfo is not None:
                    exp = exp.astimezone(UTC).replace(tzinfo=None)
                if exp > now:
                    e_type = cached.get("entity_type")
                    e_id = cached.get("entity_id")
                    return (e_type, e_id) if e_type and e_id else (None, None)
    except Exception as e:
        logging.warning("Error reading tvdb_remote_ids for %s: %s", clean_id, e)

    url = f"https://api4.thetvdb.com/v4/search/remoteid/{clean_id}"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        resp = await client.get(url, headers=headers, timeout=5.0)
        if resp.status_code == 200:
            res_json = resp.json()
            data = res_json.get("data")
            if isinstance(data, list):
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    if "movie" in item and isinstance(item["movie"], dict):
                        m_id = item["movie"].get("id")
                        if m_id:
                            str_mid = str(m_id)
                            try:
                                col.update_one(
                                    {"imdb_id": clean_id},
                                    {
                                        "$set": {
                                            "imdb_id": clean_id,
                                            "entity_type": "movie",
                                            "entity_id": str_mid,
                                            "expires_at": now + timedelta(days=30),
                                            "updated_at": now,
                                        }
                                    },
                                    upsert=True,
                                )
                            except Exception:
                                pass
                            return "movie", str_mid
                    if "series" in item and isinstance(item["series"], dict):
                        s_id = item["series"].get("id")
                        if s_id:
                            str_sid = str(s_id)
                            try:
                                col.update_one(
                                    {"imdb_id": clean_id},
                                    {
                                        "$set": {
                                            "imdb_id": clean_id,
                                            "entity_type": "series",
                                            "entity_id": str_sid,
                                            "expires_at": now + timedelta(days=30),
                                            "updated_at": now,
                                        }
                                    },
                                    upsert=True,
                                )
                            except Exception:
                                pass
                            return "series", str_sid

            # No match found in list: cache 24h negative miss
            try:
                col.update_one(
                    {"imdb_id": clean_id},
                    {
                        "$set": {
                            "imdb_id": clean_id,
                            "entity_type": None,
                            "entity_id": None,
                            "expires_at": now + timedelta(days=1),
                            "updated_at": now,
                        }
                    },
                    upsert=True,
                )
            except Exception:
                pass
            return None, None

        elif resp.status_code == 404:
            try:
                col.update_one(
                    {"imdb_id": clean_id},
                    {
                        "$set": {
                            "imdb_id": clean_id,
                            "entity_type": None,
                            "entity_id": None,
                            "expires_at": now + timedelta(days=1),
                            "updated_at": now,
                        }
                    },
                    upsert=True,
                )
            except Exception:
                pass
            return None, None
    except Exception as e:
        logging.warning("Failed to resolve TheTVDB remote ID for %s: %s", clean_id, e)

    return None, None


def _trim_artworks(raw_arts: list[dict], allowed_types: set[int]) -> list[dict]:
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
        trimmed.append({
            "image": img,
            "score": float(art.get("score") or 0),
            "includesText": bool(art.get("includesText")),
            "language": art.get("language") if isinstance(art.get("language"), str) else None,
            # Unknown width stays None so pick_tvdb_background keeps it instead of treating it as too small
            "width": int(art["width"]) if art.get("width") else None,
            "type": art_type,
        })
    return trimmed


async def fetch_tvdb_artworks(kind: str, ext_id: str | int | None) -> list[dict]:
    """
    Fetches and caches TheTVDB artworks for a series (tvdb_id) or movie (imdb_id).
    - Series artworks: GET /v4/series/{id}/artworks?type=3
    - Movie artworks: GET /v4/search/remoteid/{imdb_id} -> GET /v4/movies/{id}/extended
    - Cached in tvdb_meta_cache under (kind, ext_id): 7 days for hits, 24 hours for misses
    - Returns stale cache on upstream 429 or 5xx errors
    - Retries once on 401 with refreshed token
    """
    if not ext_id or str(ext_id).strip() == "":
        return []

    normalized_kind = "movies" if kind in ("movie", "movies") else "tv"
    str_id = str(ext_id).strip()

    from app.services.db import db
    col = db.get_collection("tvdb_meta_cache")
    now = datetime.now(UTC).replace(tzinfo=None)

    stale_data = None
    try:
        cached = col.find_one({"kind": normalized_kind, "ext_id": str_id})
        if cached:
            exp = cached.get("expires_at")
            if exp:
                if exp.tzinfo is not None:
                    exp = exp.astimezone(UTC).replace(tzinfo=None)
                if exp > now:
                    return cached.get("data", [])
            stale_data = cached.get("data", [])
    except Exception as e:
        logging.warning("Error reading tvdb_meta_cache for %s/%s: %s", normalized_kind, str_id, e)

    if not Config.TVDB_API_KEY:
        return stale_data or []

    token = await get_valid_token()
    if not token:
        return stale_data or []

    client = get_client()

    endpoint_url = None
    allowed_types = {3}

    if normalized_kind == "tv":
        endpoint_url = f"https://api4.thetvdb.com/v4/series/{str_id}/artworks?type=3"
        allowed_types = {3}
    else:
        # Movies: ext_id is imdb_id
        entity_type, entity_id = await resolve_tvdb_remote_id(str_id, client, token)
        if not entity_type or not entity_id:
            try:
                col.update_one(
                    {"kind": normalized_kind, "ext_id": str_id},
                    {
                        "$set": {
                            "kind": normalized_kind,
                            "ext_id": str_id,
                            "data": [],
                            "expires_at": now + timedelta(days=1),
                            "updated_at": now,
                        }
                    },
                    upsert=True,
                )
            except Exception:
                pass
            return []

        if entity_type == "movie":
            endpoint_url = f"https://api4.thetvdb.com/v4/movies/{entity_id}/extended"
            allowed_types = {15}
        else:
            # Series-classified movie
            endpoint_url = f"https://api4.thetvdb.com/v4/series/{entity_id}/artworks?type=3"
            allowed_types = {3}

    try:
        resp = await client.get(endpoint_url, headers={"Authorization": f"Bearer {token}"}, timeout=5.0)

        # On 401: invalidate token, refresh once, and retry
        if resp.status_code == 401:
            logging.warning("TheTVDB returned 401 Unauthorized: refreshing token and retrying once")
            invalidate_token()
            token = await get_valid_token(force_refresh=True)
            if not token:
                return stale_data or []
            resp = await client.get(endpoint_url, headers={"Authorization": f"Bearer {token}"}, timeout=5.0)

        if resp.status_code == 200:
            payload = resp.json()
            raw_arts = payload.get("data", {}).get("artworks", []) if isinstance(payload.get("data"), dict) else []
            trimmed = _trim_artworks(raw_arts, allowed_types)
            ttl = timedelta(days=7) if trimmed else timedelta(days=1)
            try:
                col.update_one(
                    {"kind": normalized_kind, "ext_id": str_id},
                    {
                        "$set": {
                            "kind": normalized_kind,
                            "ext_id": str_id,
                            "data": trimmed,
                            "expires_at": now + ttl,
                            "updated_at": now,
                        }
                    },
                    upsert=True,
                )
            except Exception as ex:
                logging.warning("Failed to update tvdb_meta_cache for %s/%s: %s", normalized_kind, str_id, ex)
            return trimmed

        elif resp.status_code == 404:
            try:
                col.update_one(
                    {"kind": normalized_kind, "ext_id": str_id},
                    {
                        "$set": {
                            "kind": normalized_kind,
                            "ext_id": str_id,
                            "data": [],
                            "expires_at": now + timedelta(days=1),
                            "updated_at": now,
                        }
                    },
                    upsert=True,
                )
            except Exception as ex:
                logging.warning("Failed to update 404 tvdb_meta_cache for %s/%s: %s", normalized_kind, str_id, ex)
            return []

        elif resp.status_code in (429, 500, 502, 503, 504):
            logging.warning("TheTVDB returned %s for %s/%s, falling back to stale cache if present", resp.status_code, normalized_kind, str_id)
            if stale_data is not None:
                return stale_data
            return []

    except Exception as e:
        logging.warning("Failed to fetch artworks from TheTVDB (%s/%s): %s", normalized_kind, str_id, e)
        if stale_data is not None:
            return stale_data

    return []


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
