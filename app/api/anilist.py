import logging
from typing import Optional

import httpx

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
    }
  }
}
"""

SAVE_MUTATION = """
mutation ($mediaId: Int, $progress: Int, $status: MediaListStatus) {
  SaveMediaListEntry(mediaId: $mediaId, progress: $progress, status: $status) {
    id
    status
    progress
  }
}
"""


async def _gql(token: str, query: str, variables: Optional[dict] = None) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload: dict = {"query": query}
    if variables:
        payload["variables"] = variables

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(ANILIST_URL, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


async def get_viewer(token: str) -> dict:
    data = await _gql(token, VIEWER_QUERY)
    return data["data"]["Viewer"]


async def get_media_status(token: str, anilist_id: int) -> dict:
    data = await _gql(token, MEDIA_QUERY, {"mediaId": anilist_id})
    return data["data"]["Media"]


async def save_entry(token: str, anilist_id: int, progress: int, status: str) -> dict:
    data = await _gql(
        token,
        SAVE_MUTATION,
        {"mediaId": anilist_id, "progress": progress, "status": status},
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
        progress
        updatedAt
        media {
          id
          idMal
          episodes
          format
          description
          status
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

