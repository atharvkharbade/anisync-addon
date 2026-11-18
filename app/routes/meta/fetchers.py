import asyncio
import datetime
import logging
import re

from app.services.http import get_client
from config import kitsu_login_enabled


KITSU_API_BASE = "https://kitsu.io/api/edge"
TIMEOUT = 10

_inflight: dict[tuple, asyncio.Task] = {}
_background_tasks: set[asyncio.Task] = set()


def _now_naive_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _to_naive_utc(dt) -> datetime.datetime | None:
    if not isinstance(dt, datetime.datetime):
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(datetime.UTC).replace(tzinfo=None)
    return dt


def _get_or_start_task(key: tuple, coro_factory) -> asyncio.Task:
    loop = asyncio.get_running_loop()
    existing = _inflight.get(key)
    if existing is not None and not existing.done() and existing.get_loop() is loop:
        return existing

    task = asyncio.create_task(coro_factory())
    _inflight[key] = task
    _background_tasks.add(task)

    def _on_done(t: asyncio.Task) -> None:
        if _inflight.get(key) is t:
            _inflight.pop(key, None)
        _background_tasks.discard(t)
        if not t.cancelled():
            try:
                t.exception()
            except Exception:
                pass

    task.add_done_callback(_on_done)
    return task


def clean_imdb_id(val) -> str | None:
    if not val:
        return None
    if isinstance(val, list):
        val = val[0] if val else None
    if not val:
        return None
    val = str(val).strip()
    if val.startswith("[") and val.endswith("]"):
        import ast
        try:
            lst = ast.literal_eval(val)
            if isinstance(lst, list) and len(lst) > 0:
                val = str(lst[0]).strip()
        except Exception:
            val = val.strip("[]'\" ")
    return val.strip("'\" ")


def resolve_external_ids(
    kitsu_id: str | int | None,
    anizp_data: dict | None,
    subtype: str | None = "tv",
    *,
    simkl_id: str | None = None,
    mal_id: str | None = None,
    anilist_id: str | None = None,
) -> tuple[str | None, str | None, str | None, bool]:
    """
    Resolves (imdb_id, tvdb_id, simkl_id, is_movie) using AniZip mappings first,
    then at most ONE get_cached_ids lookup and ONE fribb_mappings lookup.
    """
    is_movie = (subtype or "tv").strip().lower() == "movie"
    mappings = anizp_data.get("mappings", {}) if isinstance(anizp_data, dict) else {}
    if not isinstance(mappings, dict):
        mappings = {}

    imdb_id = clean_imdb_id(mappings.get("imdb_id"))
    raw_tvdb = mappings.get("thetvdb_id") or mappings.get("tvdb_id")
    tvdb_id = str(raw_tvdb).strip() if raw_tvdb not in (None, "") else None
    resolved_simkl = str(simkl_id).strip() if simkl_id not in (None, "") else None
    if not resolved_simkl and mappings.get("simkl_id") not in (None, ""):
        resolved_simkl = str(mappings.get("simkl_id")).strip()

    clean_kitsu = str(kitsu_id).replace("kitsu:", "").strip() if kitsu_id is not None else ""
    if clean_kitsu and (not imdb_id or not tvdb_id or not resolved_simkl):
        from app.services.db import cache_ids, db, get_cached_ids

        cached_ids = get_cached_ids(clean_kitsu)
        if isinstance(cached_ids, dict):
            if not imdb_id:
                imdb_id = clean_imdb_id(cached_ids.get("imdb_id"))
            if not tvdb_id:
                c_tvdb = cached_ids.get("tvdb_id") or cached_ids.get("thetvdb_id")
                if c_tvdb not in (None, ""):
                    tvdb_id = str(c_tvdb).strip()
            if not resolved_simkl and cached_ids.get("simkl_id") not in (None, ""):
                resolved_simkl = str(cached_ids.get("simkl_id")).strip()

        if not imdb_id or not tvdb_id or not resolved_simkl:
            try:
                if clean_kitsu.isdigit():
                    fribb_doc = db.fribb_mappings.find_one({"kitsu_id": int(clean_kitsu)})
                else:
                    fribb_doc = None
                if isinstance(fribb_doc, dict):
                    found_new = False
                    if not imdb_id:
                        f_imdb = clean_imdb_id(fribb_doc.get("imdb_id"))
                        if f_imdb:
                            imdb_id = f_imdb
                            found_new = True
                    if not tvdb_id:
                        f_tvdb = fribb_doc.get("tvdb_id") or fribb_doc.get("thetvdb_id")
                        if f_tvdb not in (None, ""):
                            tvdb_id = str(f_tvdb).strip()
                            found_new = True
                    if not resolved_simkl and fribb_doc.get("simkl_id") not in (None, ""):
                        resolved_simkl = str(fribb_doc.get("simkl_id")).strip()
                        found_new = True
                    if found_new:
                        try:
                            cache_ids(
                                clean_kitsu,
                                mal_id,
                                anilist_id,
                                simkl_id=resolved_simkl,
                                imdb_id=imdb_id,
                                tvdb_id=tvdb_id,
                            )
                        except Exception:
                            pass
            except Exception as e:
                logging.warning("Failed to query fribb_mappings for kitsu_id=%s: %s", clean_kitsu, e)

    return imdb_id, tvdb_id, resolved_simkl, is_movie


async def fetch_anizp_metadata(anilist_id: str = None, mal_id: str = None) -> dict:
    if not anilist_id and not mal_id:
        return {}

    from app.services.db import db

    cache_key = f"al_{anilist_id}" if anilist_id else f"mal_{mal_id}"
    col = db.get_collection("anizp_meta_cache")
    now = _now_naive_utc()
    try:
        cached = col.find_one({"key": cache_key})
        exp = _to_naive_utc(cached.get("expires_at")) if cached else None
        if cached and exp and exp > now:
            return cached.get("data", {})
    except Exception as e:
        logging.error("Failed to read anizp_meta_cache for %s: %s", cache_key, e)

    url = "https://api.ani.zip/mappings"
    params = {}
    if anilist_id:
        params["anilist_id"] = anilist_id
    elif mal_id:
        params["mal_id"] = mal_id

    try:
        client = get_client()
        resp = await client.get(url, params=params, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            ttl = datetime.timedelta(days=30)
            write_now = _now_naive_utc()
            try:
                col.update_one(
                    {"key": cache_key},
                    {
                        "$set": {
                            "key": cache_key,
                            "data": data,
                            "expires_at": write_now + ttl,
                            "updated_at": write_now,
                        }
                    },
                    upsert=True,
                )
                mappings = data.get("mappings", {}) or {}
                other_al = str(mappings.get("anilist_id") or "")
                other_mal = str(mappings.get("mal_id") or "")
                if other_al and f"al_{other_al}" != cache_key:
                    col.update_one(
                        {"key": f"al_{other_al}"},
                        {"$set": {"key": f"al_{other_al}", "data": data, "expires_at": write_now + ttl, "updated_at": write_now}},
                        upsert=True,
                    )
                if other_mal and f"mal_{other_mal}" != cache_key:
                    col.update_one(
                        {"key": f"mal_{other_mal}"},
                        {"$set": {"key": f"mal_{other_mal}", "data": data, "expires_at": write_now + ttl, "updated_at": write_now}},
                        upsert=True,
                    )
            except Exception as ex:
                logging.error("Failed to write anizp_meta_cache for %s: %s", cache_key, ex)
            return data
        elif resp.status_code == 404:
            # Cache miss for 24 hours so we don't spam ani.zip on every catalog load
            write_now = _now_naive_utc()
            ttl = datetime.timedelta(hours=24)
            try:
                col.update_one(
                    {"key": cache_key},
                    {
                        "$set": {
                            "key": cache_key,
                            "data": {},
                            "expires_at": write_now + ttl,
                            "updated_at": write_now,
                            "is_miss": True,
                        }
                    },
                    upsert=True,
                )
            except Exception as ex:
                logging.error("Failed to write negative anizp_meta_cache for %s: %s", cache_key, ex)
            return {}
    except Exception as e:
        logging.warning("Failed to fetch rich metadata from ani.zip: %s", e)
    return {}


async def fetch_cinemeta_metadata(imdb_id: str, media_type: str, is_releasing: bool = False) -> dict:
    from app.services.db import db

    col = db.get_collection("cinemeta_meta_cache")
    now = _now_naive_utc()
    cached = None
    exp = None
    purge = None
    stale_data = None
    try:
        cached = col.find_one({"imdb_id": str(imdb_id), "media_type": str(media_type)})
        if cached:
            exp = _to_naive_utc(cached.get("expires_at"))
            purge = _to_naive_utc(cached.get("purge_at"))
            if purge is None and exp is not None:
                purge = exp + datetime.timedelta(days=30)
            if exp and exp > now:
                return cached.get("data", {})
            if purge is None or now < purge:
                stale_data = cached.get("data")
    except Exception as e:
        logging.error("Failed to read cinemeta_meta_cache for %s: %s", imdb_id, e)

    key = ("cinemeta", str(media_type), str(imdb_id))
    url = f"https://v3-cinemeta.strem.io/meta/{media_type}/{imdb_id}.json"

    async def _do_fetch() -> dict:
        try:
            client = get_client()
            resp = await client.get(url, timeout=8)
            if resp.status_code == 200:
                data = resp.json().get("meta", {})
                write_now = _now_naive_utc()
                ttl = datetime.timedelta(hours=4) if is_releasing else datetime.timedelta(days=7)
                expires_at = write_now + ttl
                purge_at = expires_at + datetime.timedelta(days=30)
                try:
                    col.update_one(
                        {"imdb_id": str(imdb_id), "media_type": str(media_type)},
                        {
                            "$set": {
                                "imdb_id": str(imdb_id),
                                "media_type": str(media_type),
                                "data": data,
                                "is_releasing": is_releasing,
                                "expires_at": expires_at,
                                "purge_at": purge_at,
                                "updated_at": write_now,
                            }
                        },
                        upsert=True,
                    )
                except Exception as ex:
                    logging.error("Failed to write cinemeta_meta_cache for %s: %s", imdb_id, ex)
                return data
            if resp.status_code == 429 or resp.status_code >= 500:
                logging.warning("Cinemeta returned %s for %s/%s, falling back to stale cache if present", resp.status_code, media_type, imdb_id)
                if stale_data is not None:
                    return stale_data
        except Exception as e:
            logging.warning("Failed to fetch metadata from Cinemeta: %s", e)
            if stale_data is not None:
                return stale_data
        return {}

    if cached and stale_data is not None and exp is not None and exp <= now and (purge is None or now < purge):
        _get_or_start_task(key, _do_fetch)
        return stale_data

    task = _get_or_start_task(key, _do_fetch)
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=9.0)
    except TimeoutError:
        return stale_data if stale_data is not None else {}


async def fetch_kitsu_meta(kitsu_id: str, allow_nsfw: bool = False) -> dict:
    from app.services.db import db

    col = db.get_collection("kitsu_meta_cache")
    now = _now_naive_utc()
    cached = None
    exp = None
    purge = None
    stale_data = None
    cached_is_nsfw = False
    try:
        cached = col.find_one({"kitsu_id": str(kitsu_id)})
        if cached:
            cached_is_nsfw = cached.get("nsfw") is True
            if not cached_is_nsfw and isinstance(cached.get("data"), dict):
                k_data = cached["data"].get("data") or {}
                if isinstance(k_data, dict) and (k_data.get("attributes") or {}).get("nsfw") is True:
                    cached_is_nsfw = True

            if cached_is_nsfw and not allow_nsfw:
                return {}

            exp = _to_naive_utc(cached.get("expires_at"))
            purge = _to_naive_utc(cached.get("purge_at"))
            if purge is None and exp is not None:
                purge = exp + datetime.timedelta(days=30)
            if exp and exp > now:
                return cached.get("data", {})
            if purge is None or now < purge:
                stale_data = cached.get("data")
    except Exception as e:
        logging.error("Failed to read kitsu_meta_cache for %s: %s", kitsu_id, e)

    key = ("kitsu", str(kitsu_id), "nsfw" if allow_nsfw else "anon")
    url = f"{KITSU_API_BASE}/anime/{kitsu_id}"
    params = {"include": "episodes"}
    headers = {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }

    async def _do_fetch() -> dict:
        client = get_client()
        try:
            resp = await client.get(url, params=params, headers=headers, timeout=TIMEOUT)
            data = None
            is_nsfw_entry = False

            if resp.status_code == 200:
                data = resp.json()
            elif resp.status_code == 404 and allow_nsfw and kitsu_login_enabled():
                from app.services.kitsu_auth import kitsu_authed_get

                authed_resp = await kitsu_authed_get(url, params=params, timeout=TIMEOUT)
                if authed_resp is not None and authed_resp.status_code == 200:
                    data = authed_resp.json()
                    is_nsfw_entry = True

            if data:
                k_data = data.get("data") or {} if isinstance(data, dict) else {}
                k_attrs = k_data.get("attributes") or {} if isinstance(k_data, dict) else {}
                status = (k_attrs.get("status") or "").lower()
                if status in ["current", "releasing", "unreleased", "not_yet_released"]:
                    ttl = datetime.timedelta(hours=2)
                else:
                    ttl = datetime.timedelta(days=7)

                if k_attrs.get("nsfw") is True or is_nsfw_entry:
                    is_nsfw_entry = True

                write_now = _now_naive_utc()
                expires_at = write_now + ttl
                purge_at = expires_at + datetime.timedelta(days=30)
                try:
                    payload = {
                        "kitsu_id": str(kitsu_id),
                        "data": data,
                        "status": status,
                        "expires_at": expires_at,
                        "purge_at": purge_at,
                        "updated_at": write_now,
                    }
                    if is_nsfw_entry:
                        payload["nsfw"] = True
                    col.update_one(
                        {"kitsu_id": str(kitsu_id)},
                        {"$set": payload},
                        upsert=True,
                    )
                except Exception as ex:
                    logging.error("Failed to write kitsu_meta_cache for %s: %s", kitsu_id, ex)

                if is_nsfw_entry and not allow_nsfw:
                    return {}
                return data

            if resp.status_code == 429 or resp.status_code >= 500:
                logging.warning("Kitsu API returned %s for id %s, falling back to stale cache if present", resp.status_code, kitsu_id)
                if stale_data is not None:
                    return stale_data
                return {}
            logging.error("Kitsu API returned status %s for id %s", resp.status_code, kitsu_id)
            return {}
        except Exception as e:
            logging.error("Failed to fetch Kitsu meta for %s: %s", kitsu_id, e)
            if stale_data is not None:
                return stale_data
            return {}

    if cached and stale_data is not None and exp is not None and exp <= now and (purge is None or now < purge):
        refresh_allow_nsfw = allow_nsfw or cached_is_nsfw
        refresh_key = ("kitsu", str(kitsu_id), "nsfw" if refresh_allow_nsfw else "anon")
        if refresh_allow_nsfw == allow_nsfw:
            _get_or_start_task(key, _do_fetch)
        else:
            _get_or_start_task(refresh_key, lambda: fetch_kitsu_meta(kitsu_id, allow_nsfw=refresh_allow_nsfw))
        return stale_data

    task = _get_or_start_task(key, _do_fetch)
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=TIMEOUT + 1.0)
    except TimeoutError:
        return stale_data if stale_data is not None else {}



async def fetch_provider_overrides(
    user: dict | None,
    mal_id: str | None,
    anilist_id: str | None,
    *,
    show_airing: bool = False,
) -> tuple[dict | None, dict | None]:
    from app.lib.meta_providers import get_effective_meta_providers

    effective = get_effective_meta_providers(user)
    synopsis_prov = effective["synopsis"]
    poster_prov = effective["poster"]
    airing_prov = effective["airing"]

    need_mal = bool(mal_id and (synopsis_prov == "mal" or poster_prov == "mal" or (show_airing and airing_prov == "mal")))
    need_al = bool(anilist_id and (synopsis_prov == "anilist" or poster_prov == "anilist" or (show_airing and airing_prov == "anilist")))

    async def _fetch_mal() -> dict | None:
        try:
            from app.api.jikan import get_anime_by_id

            return await asyncio.wait_for(get_anime_by_id(mal_id), timeout=3.0)
        except Exception as e:
            logging.warning("Failed to fetch MAL metadata for mal_id=%s: %s", mal_id, e)
            return None

    async def _fetch_al() -> dict | None:
        try:
            from app.api.anilist import get_media_status

            token = user.get("anilist_token", "") if user else ""
            return await asyncio.wait_for(get_media_status(token, int(anilist_id)), timeout=3.0)
        except Exception as e:
            logging.warning("Failed to fetch AniList metadata for anilist_id=%s: %s", anilist_id, e)
            return None

    if need_mal and need_al:
        mal_res, al_res = await asyncio.gather(_fetch_mal(), _fetch_al(), return_exceptions=True)
        mal_data = None if isinstance(mal_res, Exception) else mal_res
        al_data = None if isinstance(al_res, Exception) else al_res
        return mal_data, al_data
    elif need_mal:
        return await _fetch_mal(), None
    elif need_al:
        return None, await _fetch_al()
    return None, None


def apply_provider_overrides(
    meta: dict,
    user: dict | None,
    mal_data: dict | None,
    al_data: dict | None,
    *,
    preserve_localized_synopsis: bool = False,
) -> tuple[dict | None, dict | None]:
    from app.lib.meta_providers import get_effective_meta_providers

    effective = get_effective_meta_providers(user)
    synopsis_prov = effective["synopsis"]
    poster_prov = effective["poster"]

    # Apply Synopsis & Rating
    if synopsis_prov == "mal" and mal_data:
        if not preserve_localized_synopsis:
            synopsis = mal_data.get("synopsis")
            if synopsis:
                meta["description"] = synopsis
        score = mal_data.get("score")
        if score:
            meta["imdbRating"] = str(score)
    elif synopsis_prov == "anilist" and al_data:
        if not preserve_localized_synopsis:
            desc = al_data.get("description")
            if desc:
                clean_desc = re.sub(r"<[^>]+>", "", desc)
                meta["description"] = clean_desc
        avg_score = al_data.get("averageScore")
        if avg_score:
            meta["imdbRating"] = f"{avg_score / 10:.1f}"

    # Apply Poster Override (skip if TVDB poster was selected)
    if poster_prov == "mal" and mal_data:
        images = mal_data.get("images", {})
        jpg_img = images.get("jpg", {}) or images.get("webp", {})
        poster_url = jpg_img.get("large_image_url") or jpg_img.get("image_url")
        if poster_url:
            meta["poster"] = poster_url
    elif poster_prov == "anilist" and al_data:
        cover = al_data.get("coverImage", {})
        poster_url = cover.get("extraLarge") or cover.get("large")
        if poster_url:
            meta["poster"] = poster_url

    return mal_data, al_data


async def apply_metadata_provider_override(
    meta: dict,
    user: dict,
    mal_id: str | None,
    anilist_id: str | None,
) -> tuple[dict | None, dict | None]:
    show_airing = bool(user.get("show_airing_in_synopsis", False)) if user else False
    mal_data, al_data = await fetch_provider_overrides(user, mal_id, anilist_id, show_airing=show_airing)
    return apply_provider_overrides(meta, user, mal_data, al_data)
