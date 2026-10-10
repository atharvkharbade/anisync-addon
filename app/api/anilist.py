import asyncio
import logging
import httpx

from app.services.http import get_client
from config import Config

ANILIST_URL = Config.ANILIST_API_URL
DEFAULT_TIMEOUT = 4.0
AUTH_TIMEOUT = 15.0
SCROBBLE_TIMEOUT = 8.0
TIMEOUT = DEFAULT_TIMEOUT  # Backward compatibility for any external callers

VIEWER_QUERY = """
query {
  Viewer {
    id
    name
    avatar {
      large
    }
  }
}
"""

MEDIA_QUERY = """
query ($mediaId: Int) {
  Media(id: $mediaId, type: ANIME) {
    id
    title {
      english
      romaji
      native
    }
    episodes
    status
    description
    averageScore
    coverImage {
      large
      extraLarge
    }
    bannerImage
    nextAiringEpisode {
      episode
      airingAt
      timeUntilAiring
    }
    mediaListEntry {
      progress
      status
      score
      repeat
    }
  }
}
"""

SAVE_MUTATION = """
mutation ($mediaId: Int, $progress: Int, $status: MediaListStatus, $repeat: Int) {
  SaveMediaListEntry(mediaId: $mediaId, progress: $progress, status: $status, repeat: $repeat) {
    id
    status
    progress
    repeat
  }
}
"""



class AnilistTokenInvalidError(Exception):
    """Exception raised when AniList API returns an invalid token error."""
    pass


class AnilistAPIError(Exception):
    """Exception raised when AniList API returns GraphQL errors or null data."""
    pass


async def _gql(
    token: str | None,
    query: str,
    variables: dict | None = None,
    timeout: float | None = None,
) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://anilist.co/",
        "Origin": "https://anilist.co",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    payload: dict = {"query": query}
    if variables:
        payload["variables"] = variables

    client = get_client()
    req_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
    
    retries = 4
    backoffs = [0.2, 0.5, 1.0, 1.5]
    for attempt in range(retries):
        try:
            resp = await client.post(ANILIST_URL, json=payload, headers=headers, timeout=req_timeout)
            
            # Handle rate limiting (HTTP 429 Too Many Requests)
            if resp.status_code == 429:
                if attempt == retries - 1:
                    resp.raise_for_status()
                retry_after = resp.headers.get("Retry-After")
                wait_time = int(retry_after) if (retry_after and retry_after.isdigit()) else 1.0
                logging.warning(
                    "AniList 429 rate limit hit. Retrying in %s seconds (attempt %s/%s)...",
                    wait_time,
                    attempt + 1,
                    retries,
                )
                await asyncio.sleep(wait_time)
                continue
                
            # AniList returns 401 for revoked/expired tokens, or 400/200 with error messages
            if resp.status_code == 401:
                raise AnilistTokenInvalidError("AniList token is invalid or expired.")

            if resp.status_code in (400, 200):
                try:
                    data = resp.json()
                    errors = data.get("errors", [])
                    for err in errors:
                        if err.get("message") == "Invalid token":
                            raise AnilistTokenInvalidError("AniList token is invalid or expired.")
                except AnilistTokenInvalidError:
                    raise
                except (ValueError, KeyError, TypeError):
                    pass
                    
            resp.raise_for_status()
            return resp.json()
            
        except (httpx.HTTPStatusError, httpx.RequestError, asyncio.TimeoutError) as e:
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
                if attempt == retries - 1:
                    raise e
                continue
            if attempt == retries - 1:
                raise e
            sleep_time = backoffs[attempt] if attempt < len(backoffs) else 1.0
            logging.warning("AniList query request error (attempt %s/%s): %s (retrying in %ss)", attempt + 1, retries, e, sleep_time)
            await asyncio.sleep(sleep_time)
            
    raise httpx.RequestError("AniList query failed after retries")


async def get_viewer(token: str, timeout: float = AUTH_TIMEOUT) -> dict:
    data = await _gql(token, VIEWER_QUERY, timeout=timeout)
    return (data.get("data") or {}).get("Viewer") or {}


async def get_media_status(token: str, anilist_id: int, use_cache: bool = True) -> dict:
    from app.services.db import cache_anilist_media, get_cached_anilist_media

    # Check local MongoDB cache first if use_cache is True
    if use_cache:
        cached = get_cached_anilist_media(anilist_id)
        if cached:
            return cached

    data = await _gql(token, MEDIA_QUERY, {"mediaId": anilist_id})
    media = (data.get("data") or {}).get("Media")
    if media:
        cache_anilist_media(anilist_id, media)
    return media


async def save_entry(
    token: str,
    anilist_id: int,
    progress: int,
    status: str,
    repeat: int | None = None,
    timeout: float = SCROBBLE_TIMEOUT,
) -> dict:
    variables = {"mediaId": anilist_id, "progress": progress, "status": status}
    if repeat is not None:
        variables["repeat"] = repeat
    data = await _gql(
        token,
        SAVE_MUTATION,
        variables,
        timeout=timeout,
    )
    if not data or not isinstance(data, dict) or not data.get("data"):
        errors = (data or {}).get("errors", [])
        raise AnilistAPIError(f"AniList mutation failed: {errors}")
    save_media = data["data"].get("SaveMediaListEntry")
    if not save_media:
        raise AnilistAPIError(f"AniList response missing SaveMediaListEntry: {data}")
    return save_media


USER_LIST_QUERY = """
query ($userId: Int, $status: MediaListStatus) {
  MediaListCollection(userId: $userId, type: ANIME, status: $status) {
    lists {
      name
      isCustomList
      status
      entries {
        id
        status
        score
        progress
        updatedAt
        media {
          id
          idMal
          episodes
          format
          description
          genres
          status
          averageScore
          startDate {
            year
            month
            day
          }
          endDate {
            year
            month
            day
          }
          seasonYear
          nextAiringEpisode {
            episode
            airingAt
          }
          title {
            userPreferred
            english
            romaji
            native
          }
          coverImage {
            large
            medium
          }
          bannerImage
        }
      }
    }
  }
}
"""

SEARCH_ANIME_QUERY = """
query ($search: String, $limit: Int) {
  Page(page: 1, perPage: $limit) {
    media(search: $search, type: ANIME) {
      id
      episodes
      format
      description
      title {
        userPreferred
        english
      }
      coverImage {
        large
        medium
      }
      bannerImage
    }
  }
}
"""


async def get_user_anime_list(token: str, user_id: int, status: str = None) -> dict:
    variables = {"userId": user_id}
    if status:
        variables["status"] = status
    data = await _gql(token, USER_LIST_QUERY, variables)
    return (data.get("data") or {}).get("MediaListCollection") or {}


async def search_anime(token: str, query: str, limit: int = 20) -> list:
    data = await _gql(token, SEARCH_ANIME_QUERY, {"search": query, "limit": limit})
    return ((data.get("data") or {}).get("Page") or {}).get("media") or []
