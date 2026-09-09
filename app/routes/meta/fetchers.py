import asyncio
import datetime
import logging
import re
from app.services.http import get_client

KITSU_API_BASE = "https://kitsu.io/api/edge"
TIMEOUT = 10


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


async def fetch_anizp_metadata(anilist_id: str = None, mal_id: str = None) -> dict:
    if not anilist_id and not mal_id:
        return {}

    from app.services.db import db

    cache_key = f"al_{anilist_id}" if anilist_id else f"mal_{mal_id}"
    col = db.get_collection("anizp_meta_cache")
    now = datetime.datetime.utcnow()
    try:
        cached = col.find_one({"key": cache_key})
        if cached and cached.get("expires_at") > now:
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
            try:
                col.update_one(
                    {"key": cache_key},
                    {
                        "$set": {
                            "key": cache_key,
                            "data": data,
                            "expires_at": now + ttl,
                            "updated_at": now,
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
                        {"$set": {"key": f"al_{other_al}", "data": data, "expires_at": now + ttl, "updated_at": now}},
                        upsert=True,
                    )
                if other_mal and f"mal_{other_mal}" != cache_key:
                    col.update_one(
                        {"key": f"mal_{other_mal}"},
                        {"$set": {"key": f"mal_{other_mal}", "data": data, "expires_at": now + ttl, "updated_at": now}},
                        upsert=True,
                    )
            except Exception as ex:
                logging.error("Failed to write anizp_meta_cache for %s: %s", cache_key, ex)
            return data
    except Exception as e:
        logging.warning("Failed to fetch rich metadata from ani.zip: %s", e)
    return {}


async def fetch_cinemeta_metadata(imdb_id: str, media_type: str, is_releasing: bool = False) -> dict:
    from app.services.db import db

    col = db.get_collection("cinemeta_meta_cache")
    now = datetime.datetime.utcnow()
    try:
        cached = col.find_one({"imdb_id": str(imdb_id), "media_type": str(media_type)})
        if cached and cached.get("expires_at") > now:
            return cached.get("data", {})
    except Exception as e:
        logging.error("Failed to read cinemeta_meta_cache for %s: %s", imdb_id, e)

    url = f"https://v3-cinemeta.strem.io/meta/{media_type}/{imdb_id}.json"
    try:
        client = get_client()
        resp = await client.get(url, timeout=8)
        if resp.status_code == 200:
            data = resp.json().get("meta", {})
            ttl = datetime.timedelta(hours=4) if is_releasing else datetime.timedelta(days=7)
            try:
                col.update_one(
                    {"imdb_id": str(imdb_id), "media_type": str(media_type)},
                    {
                        "$set": {
                            "imdb_id": str(imdb_id),
                            "media_type": str(media_type),
                            "data": data,
                            "is_releasing": is_releasing,
                            "expires_at": now + ttl,
                            "updated_at": now,
                        }
                    },
                    upsert=True,
                )
            except Exception as ex:
                logging.error("Failed to write cinemeta_meta_cache for %s: %s", imdb_id, ex)
            return data
    except Exception as e:
        logging.warning("Failed to fetch metadata from Cinemeta: %s", e)
    return {}


async def fetch_kitsu_meta(kitsu_id: str) -> dict:
    from app.services.db import db

    col = db.get_collection("kitsu_meta_cache")
    now = datetime.datetime.utcnow()
    try:
        cached = col.find_one({"kitsu_id": str(kitsu_id)})
        if cached and cached.get("expires_at") > now:
            return cached.get("data", {})
    except Exception as e:
        logging.error("Failed to read kitsu_meta_cache for %s: %s", kitsu_id, e)

    url = f"{KITSU_API_BASE}/anime/{kitsu_id}"
    params = {"include": "episodes"}
    headers = {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }
    client = get_client()
    try:
        resp = await client.get(url, params=params, headers=headers, timeout=TIMEOUT)
        if resp.status_code != 200:
            logging.error("Kitsu API returned status %s for id %s", resp.status_code, kitsu_id)
            return {}
        data = resp.json()

        k_data = data.get("data") or {} if isinstance(data, dict) else {}
        k_attrs = k_data.get("attributes") or {} if isinstance(k_data, dict) else {}
        status = (k_attrs.get("status") or "").lower()
        if status in ["current", "releasing", "unreleased", "not_yet_released"]:
            ttl = datetime.timedelta(hours=2)
        else:
            ttl = datetime.timedelta(days=7)

        try:
            col.update_one(
                {"kitsu_id": str(kitsu_id)},
                {
                    "$set": {
                        "kitsu_id": str(kitsu_id),
                        "data": data,
                        "status": status,
                        "expires_at": now + ttl,
                        "updated_at": now,
                    }
                },
                upsert=True,
            )
        except Exception as ex:
            logging.error("Failed to write kitsu_meta_cache for %s: %s", kitsu_id, ex)
        return data
    except Exception as e:
        logging.error("Failed to fetch Kitsu meta for %s: %s", kitsu_id, e)
        return {}


async def get_banner_aspect_ratio(banner_url: str) -> float:
    """Check banner aspect ratio with MongoDB caching. Returns 2.5 on error to trigger safe fallback."""
    if not banner_url:
        return 2.5
    try:
        from app.services.db import db

        col = db.get_collection("banner_ratios")
        doc = col.find_one({"url": banner_url})
        if doc and "ratio" in doc:
            return float(doc["ratio"])

        import httpx
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = 25_000_000

        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(banner_url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200 and len(resp.content) <= 10 * 1024 * 1024:
                img = Image.open(io.BytesIO(resp.content))
                bw, bh = img.size
                ratio = round(bw / float(bh), 2) if bh > 0 else 2.5
                try:
                    col.update_one(
                        {"url": banner_url},
                        {"$set": {"url": banner_url, "ratio": ratio}},
                        upsert=True,
                    )
                except Exception as ex:
                    logging.warning("Failed to cache banner ratio for %s: %s", banner_url, ex)
                return ratio
    except Exception as e:
        logging.warning("Failed to resolve banner aspect ratio for %s: %s", banner_url, e)
    return 2.5


async def apply_metadata_provider_override(
    meta: dict,
    user: dict,
    mal_id: str | None,
    anilist_id: str | None,
) -> tuple[dict | None, dict | None]:
    from app.lib.meta_providers import get_effective_meta_providers

    effective = get_effective_meta_providers(user)
    synopsis_prov = effective["synopsis"]
    poster_prov = effective["poster"]
    backdrop_prov = effective["backdrop"]

    mal_data_ret = None
    al_data_ret = None

    # 1. Fetch MAL data if needed for synopsis or poster
    mal_data = None
    if (synopsis_prov == "mal" or poster_prov == "mal") and mal_id:
        try:
            from app.api.jikan import get_anime_by_id

            mal_data = await asyncio.wait_for(get_anime_by_id(mal_id), timeout=3.0)
            if mal_data:
                mal_data_ret = mal_data
        except Exception as e:
            logging.warning("Failed to fetch MAL metadata for mal_id=%s: %s", mal_id, e)

    # 2. Fetch AniList data if needed for synopsis, poster, or backdrop
    al_data = None
    if (synopsis_prov == "anilist" or poster_prov == "anilist" or backdrop_prov == "anilist") and anilist_id:
        try:
            from app.api.anilist import get_media_status

            token = user.get("anilist_token", "") if user else ""
            al_data = await asyncio.wait_for(get_media_status(token, int(anilist_id)), timeout=3.0)
            if al_data:
                al_data_ret = al_data
        except Exception as e:
            logging.warning("Failed to fetch AniList metadata for anilist_id=%s: %s", anilist_id, e)

    # 3. Apply Synopsis & Rating
    if synopsis_prov == "mal" and mal_data:
        synopsis = mal_data.get("synopsis")
        if synopsis:
            meta["description"] = synopsis
        score = mal_data.get("score")
        if score:
            meta["imdbRating"] = str(score)
    elif synopsis_prov == "anilist" and al_data:
        desc = al_data.get("description")
        if desc:
            clean_desc = re.sub(r"<[^>]+>", "", desc)
            meta["description"] = clean_desc
        avg_score = al_data.get("averageScore")
        if avg_score:
            meta["imdbRating"] = f"{avg_score / 10:.1f}"

    # 4. Apply Poster Override
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

    # 5. Apply Backdrop Override
    if backdrop_prov == "anilist" and al_data:
        banner_url = al_data.get("bannerImage")
        if banner_url:
            ratio = await get_banner_aspect_ratio(banner_url)
            if ratio <= 2.0:
                meta["background"] = banner_url
            # Else (ratio > 2.0 ultra-wide banner): skip and retain Fanart/Kitsu/Cinemeta fallback

    return mal_data_ret, al_data_ret
