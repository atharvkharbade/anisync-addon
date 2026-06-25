import asyncio
import logging
import httpx

from app.services.http import get_client

ANILIST_URL = "https://graphql.anilist.co"
TIMEOUT = 10

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
    episodes
    mediaListEntry {
      progress
      status
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


async def _gql(token: str | None, query: str, variables: dict | None = None) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    payload: dict = {"query": query}
    if variables:
        payload["variables"] = variables

    client = get_client()
    
    retries = 3
    for attempt in range(retries):
        try:
            resp = await client.post(ANILIST_URL, json=payload, headers=headers, timeout=TIMEOUT)
            
            # Handle rate limiting (HTTP 429 Too Many Requests)
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                wait_time = int(retry_after) if (retry_after and retry_after.isdigit()) else (2 ** attempt + 1)
                logging.warning(
                    "AniList 429 rate limit hit. Retrying in %s seconds (attempt %s/%s)...",
                    wait_time,
                    attempt + 1,
                    retries,
                )
                await asyncio.sleep(wait_time)
                continue
                
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
                # Handled above, but if it falls through (e.g. raised by raise_for_status somehow), continue
                continue
            if attempt == retries - 1:
                raise e
            logging.warning("AniList query request error (attempt %s/%s): %s", attempt + 1, retries, e)
            await asyncio.sleep(2 ** attempt + 1)
            
    raise httpx.RequestError("AniList query failed after retries")


async def get_viewer(token: str) -> dict:
    data = await _gql(token, VIEWER_QUERY)
    return data["data"]["Viewer"]


async def get_media_status(token: str, anilist_id: int) -> dict:
    data = await _gql(token, MEDIA_QUERY, {"mediaId": anilist_id})
    return data["data"]["Media"]


async def save_entry(token: str, anilist_id: int, progress: int, status: str, repeat: int | None = None) -> dict:
    variables = {"mediaId": anilist_id, "progress": progress, "status": status}
    if repeat is not None:
        variables["repeat"] = repeat
    data = await _gql(
        token,
        SAVE_MUTATION,
        variables,
    )
    return data["data"]["SaveMediaListEntry"]


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
          endDate {
            year
            month
            day
          }
          nextAiringEpisode {
            episode
            airingAt
          }
          title {
            userPreferred
            english
          }
          coverImage {
            large
            medium
          }
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
    }
  }
}
"""


async def get_user_anime_list(token: str, user_id: int, status: str = None) -> dict:
    variables = {"userId": user_id}
    if status:
        variables["status"] = status
    data = await _gql(token, USER_LIST_QUERY, variables)
    return data.get("data", {}).get("MediaListCollection", {})


async def search_anime(token: str, query: str, limit: int = 20) -> list:
    data = await _gql(token, SEARCH_ANIME_QUERY, {"search": query, "limit": limit})
    return data.get("data", {}).get("Page", {}).get("media", [])
