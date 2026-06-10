import logging
from typing import Optional

from config import Config
from app.services.http import get_client

BASE_URL = "https://api.simkl.com"
TIMEOUT = 10

logger = logging.getLogger("anisync")


def _redirect_uri() -> str:
    return f"{Config.PROTOCOL}://{Config.REDIRECT_URL}/simkl-callback"


async def get_access_token(code: str) -> str:
    """Exchange authorization code for a Simkl access token."""
    client = get_client()
    payload = {
        "code": code,
        "client_id": Config.SIMKL_CLIENT_ID,
        "client_secret": Config.SIMKL_CLIENT_SECRET,
        "redirect_uri": _redirect_uri(),
        "grant_type": "authorization_code",
    }
    resp = await client.post(
        f"{BASE_URL}/oauth/token",
        json=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "AniSync/1.0",
        },
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        logger.error("SIMKL_TOKEN_ERROR status=%d body=%s", resp.status_code, resp.text)
    resp.raise_for_status()
    data = resp.json()
    return data.get("access_token") or ""



async def get_user_details(token: str) -> dict:
    """Get the user's settings / details from Simkl."""
    client = get_client()
    headers = {
        "Authorization": f"Bearer {token}",
        "simkl-api-key": Config.SIMKL_CLIENT_ID,
        "User-Agent": "AniSync/1.0",
    }
    resp = await client.get(
        f"{BASE_URL}/users/settings",
        headers=headers,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


async def get_user_anime_list(token: str, status: Optional[str] = None) -> list:
    """Get the user's anime watchlist items (all or by status)."""
    client = get_client()
    headers = {
        "Authorization": f"Bearer {token}",
        "simkl-api-key": Config.SIMKL_CLIENT_ID,
        "User-Agent": "AniSync/1.0",
    }
    if status:
        status_map = {
            "watching": "watching",
            "plantowatch": "plantowatch",
            "completed": "completed",
            "hold": "hold",
            "dropped": "dropped",
        }
        simkl_status = status_map.get(status, "watching")
        url = f"{BASE_URL}/sync/all-items/anime/{simkl_status}?extended=full"
    else:
        url = f"{BASE_URL}/sync/all-items/anime?extended=full"

    # Fetch watchlist items
    resp = await client.get(
        url,
        headers=headers,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()

    # Defensive parsing for direct list vs dictionary wrapped format
    if isinstance(data, dict):
        return data.get("anime", [])
    elif isinstance(data, list):
        return data
    return []


async def sync_history(
    token: str,
    kitsu_id: str,
    mal_id: Optional[str],
    anilist_id: Optional[str],
    episode: int,
    content_type: str,
) -> bool:
    """Post watch history update to Simkl."""
    client = get_client()
    headers = {
        "Authorization": f"Bearer {token}",
        "simkl-api-key": Config.SIMKL_CLIENT_ID,
        "Content-Type": "application/json",
        "User-Agent": "AniSync/1.0",
    }

    # Construct cross-platform IDs dictionary
    ids = {}
    if kitsu_id:
        ids["kitsu"] = str(kitsu_id)
    if mal_id:
        ids["mal"] = int(mal_id) if isinstance(mal_id, (int, str)) and str(mal_id).isdigit() else mal_id
    if anilist_id:
        ids["anilist"] = int(anilist_id) if isinstance(anilist_id, (int, str)) and str(anilist_id).isdigit() else anilist_id

    # Format the payload based on movie vs show content type
    if content_type == "movie":
        payload = {
            "movies": [
                {
                    "ids": ids
                }
            ]
        }
    else:
        payload = {
            "shows": [
                {
                    "ids": ids,
                    "seasons": [
                        {
                            "number": 1,
                            "episodes": [
                                {
                                    "number": i
                                } for i in range(1, int(episode) + 1)
                            ]
                        }
                    ]
                }
            ]
        }

    try:
        resp = await client.post(
            f"{BASE_URL}/sync/history",
            json=payload,
            headers=headers,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error("Failed to sync watch history to Simkl: %s", e)
        return False
