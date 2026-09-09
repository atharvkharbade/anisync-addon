import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

import httpx

from app.services.db import db
from app.services.http import get_client

logger = logging.getLogger(__name__)

SUPPORTED_DUB_LANGUAGES = {
    "english": "English",
    "spanish": "Spanish",
    "german": "German",
    "french": "French",
    "italian": "Italian",
    "portuguese": "Portuguese",
    "hindi": "Hindi",
}

# MyDubList GitHub endpoint for normal confidence tier (>= 2 sources or curated)
MYDUBLIST_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/Joelis57/MyDubList/main/dubs/confidence/normal/dubbed_{language}.json"
)

# In-memory TTL: 6 hours
MEMORY_TTL_SECONDS = 3600 * 6
# MongoDB TTL: 7 days
DB_TTL_DAYS = 7

# Memory cache: language -> (cached_at_timestamp, set_of_mal_ids)
_MEMORY_DUB_CACHE: dict[str, tuple[float, set[int]]] = {}

# Ensure MongoDB indexes
try:
    db.get_collection("dubbed_cache").create_index("language", unique=True)
    db.get_collection("dubbed_cache").create_index("expires_at", expireAfterSeconds=0)
except Exception as exc:
    logger.warning("Could not ensure index on dubbed_cache: %s", exc)


def normalize_dub_language(language: str | None) -> str:
    """Validate and normalize dub language key."""
    if not language:
        return "english"
    lang = str(language).strip().lower()
    if lang in SUPPORTED_DUB_LANGUAGES:
        return lang
    aliases = {
        "en": "english",
        "eng": "english",
        "es": "spanish",
        "esp": "spanish",
        "spa": "spanish",
        "de": "german",
        "deu": "german",
        "ger": "german",
        "fr": "french",
        "fra": "french",
        "fre": "french",
        "it": "italian",
        "ita": "italian",
        "pt": "portuguese",
        "por": "portuguese",
        "hi": "hindi",
        "hin": "hindi",
    }
    return aliases.get(lang, "english")


async def get_dubbed_mal_ids(language: str = "english") -> set[int]:
    """
    Retrieve the set of MyAnimeList IDs that have an available dub in the given language.
    Checks in-memory cache first, then MongoDB, then fetches from MyDubList GitHub dataset.
    """
    lang = normalize_dub_language(language)
    now_ts = time.time()

    # 1. In-memory check
    if lang in _MEMORY_DUB_CACHE:
        cached_ts, id_set = _MEMORY_DUB_CACHE[lang]
        if now_ts - cached_ts < MEMORY_TTL_SECONDS:
            return id_set

    col = db.get_collection("dubbed_cache")
    now_dt = datetime.now(UTC)

    # 2. MongoDB check
    doc = None
    try:
        doc = col.find_one({"language": lang})
        if doc and doc.get("mal_ids") and doc.get("expires_at"):
            # Ensure expires_at comparison is timezone-aware
            expires_at = doc["expires_at"]
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at > now_dt:
                id_set = set(doc["mal_ids"])
                _MEMORY_DUB_CACHE[lang] = (now_ts, id_set)
                return id_set
    except Exception as exc:
        logger.warning("Error reading dubbed_cache for %s: %s", lang, exc)

    # 3. Fetch from MyDubList
    url = MYDUBLIST_URL_TEMPLATE.format(language=lang)
    try:
        resp = None
        try:
            client = get_client()
            resp = await client.get(url, timeout=12.0)
        except Exception:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as direct_client:
                resp = await direct_client.get(url)

        if resp and resp.status_code == 200:
            data = resp.json()
            raw_ids = data.get("dubbed", [])
            mal_ids = [int(x) for x in raw_ids if str(x).isdigit()]
            id_set = set(mal_ids)

            col.update_one(
                {"language": lang},
                {
                    "$set": {
                        "language": lang,
                        "mal_ids": mal_ids,
                        "count": len(mal_ids),
                        "updated_at": now_dt,
                        "expires_at": now_dt + timedelta(days=DB_TTL_DAYS),
                    }
                },
                upsert=True,
            )
            _MEMORY_DUB_CACHE[lang] = (now_ts, id_set)
            logger.info("Fetched and cached %d %s dubbed anime from MyDubList", len(mal_ids), lang)
            return id_set
        else:
            logger.warning("MyDubList returned status %s for language %s", resp.status_code, lang)
    except Exception as exc:
        logger.warning("Failed to fetch MyDubList for %s: %s", lang, exc)

    # 4. Fallback to existing stale MongoDB record if available
    if doc and doc.get("mal_ids"):
        logger.info("Using stale MongoDB dubbed cache for %s (%d items)", lang, len(doc["mal_ids"]))
        id_set = set(doc["mal_ids"])
        _MEMORY_DUB_CACHE[lang] = (now_ts, id_set)
        return id_set

    # 5. Fallback: empty set
    return set()


async def is_mal_id_dubbed(mal_id: int | str, language: str = "english") -> bool:
    """Check if a specific MAL ID has a dub in the specified language."""
    dubbed_ids = await get_dubbed_mal_ids(language)
    try:
        return int(mal_id) in dubbed_ids
    except (ValueError, TypeError):
        return False


async def dub_sync_loop():
    """Background loop to ensure dub database is loaded and refreshed daily."""
    while True:
        try:
            # Warm English cache first
            await get_dubbed_mal_ids("english")
        except Exception as e:
            logger.warning("Error in background dub sync loop: %s", e)
        await asyncio.sleep(86400)


def trigger_dub_sync_background():
    """Start background task for periodic dub database sync."""
    try:
        asyncio.create_task(dub_sync_loop())
    except Exception as e:
        logger.warning("Could not launch background dub sync task: %s", e)


async def is_anime_dubbed(mal_id: int | str | None, language: str = "english") -> bool:
    """Check if a specific MyAnimeList ID is dubbed in the target language."""
    if not mal_id:
        return False
    try:
        mid = int(mal_id)
        dubbed_set = await get_dubbed_mal_ids(language)
        return mid in dubbed_set
    except (ValueError, TypeError):
        return False


def _extract_mal_id(meta: dict) -> int | None:
    """Extract MAL ID as an integer from a Stremio meta dictionary."""
    # 1. Direct mal_id / idMal field
    mid = meta.get("mal_id") or meta.get("idMal")
    if mid is not None and str(mid).isdigit():
        return int(mid)

    # 2. Meta ID prefix check (e.g. 'mal:123')
    stremio_id = str(meta.get("id", ""))
    if stremio_id.startswith("mal:"):
        part = stremio_id.split(":", 1)[1]
        if part.isdigit():
            return int(part)

    # 3. Kitsu / AniList ID resolution fallback from MongoDB id_cache
    if stremio_id.startswith("kitsu:"):
        k_id = stremio_id.split(":", 1)[1]
        if k_id.isdigit():
            cached = db.get_collection("id_cache").find_one({"kitsu_id": int(k_id)})
            if cached and cached.get("mal_id"):
                return int(cached["mal_id"])
    elif stremio_id.startswith("anilist:"):
        a_id = stremio_id.split(":", 1)[1]
        if a_id.isdigit():
            cached = db.get_collection("id_cache").find_one({"anilist_id": int(a_id)})
            if cached and cached.get("mal_id"):
                return int(cached["mal_id"])

    return None


async def filter_dubbed(metas: list[dict], language: str = "english") -> list[dict]:
    """
    Filter a list of Stremio meta dictionaries to only keep titles
    that have a verified dub in the specified language.
    """
    if not metas:
        return []

    dubbed_ids = await get_dubbed_mal_ids(language)
    if not dubbed_ids:
        return []

    filtered = []
    for m in metas:
        mid = _extract_mal_id(m)
        if mid is not None and mid in dubbed_ids:
            filtered.append(m)

    return filtered


filter_anime_list_by_dub = filter_dubbed
