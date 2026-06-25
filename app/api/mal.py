import secrets
from urllib.parse import urlencode

from app.services.http import get_client
from config import Config

AUTH_URL = "https://myanimelist.net/v1"
BASE_URL = Config.MAL_API_URL
N_BYTES = 96
TIMEOUT = 10
CODE_CHALLENGE_METHOD = "plain"


import httpx


class MalTokenInvalidError(Exception):
    pass


def _raise_for_status(resp):
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        if resp.status_code in [400, 401, 403]:
            raise MalTokenInvalidError("MAL token is invalid, expired, or revoked") from e
        raise e


def _redirect_uri() -> str:
    return f"{Config.PROTOCOL}://{Config.REDIRECT_URL}/callback"


def _client_id() -> str:
    return Config.MAL_CLIENT_ID


def _client_secret() -> str:
    return Config.MAL_CLIENT_SECRET


# ── PKCE helpers ──────────────────────────────────────────────────────────────


def generate_pkce() -> tuple[str, str]:
    # Generate high-entropy verifier (43-128 characters)
    verifier = secrets.token_urlsafe(N_BYTES)[:128]
    # For plain method, challenge is same as verifier
    return verifier, verifier


def get_auth_url(code_challenge: str, state: str) -> str:
    params = urlencode(
        {
            "response_type": "code",
            "client_id": _client_id(),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": CODE_CHALLENGE_METHOD,
            "redirect_uri": _redirect_uri(),
        }
    )
    return f"{AUTH_URL}/oauth2/authorize?{params}"


# ── Token management ──────────────────────────────────────────────────────────


async def get_access_token(code: str, code_verifier: str) -> dict:
    client = get_client()
    resp = await client.post(
        f"{AUTH_URL}/oauth2/token",
        data={
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": _redirect_uri(),
        },
        timeout=TIMEOUT,
    )
    _raise_for_status(resp)
    return resp.json()


async def refresh_token(refresh_tok: str) -> dict:
    client = get_client()
    resp = await client.post(
        f"{AUTH_URL}/oauth2/token",
        data={
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "grant_type": "refresh_token",
            "refresh_token": refresh_tok,
        },
        timeout=TIMEOUT,
    )
    _raise_for_status(resp)
    return resp.json()


async def get_user_details(token: str) -> dict:
    client = get_client()
    resp = await client.get(
        f"{BASE_URL}/users/@me",
        headers={"Authorization": f"Bearer {token}"},
        params={"fields": "id,name,picture"},
        timeout=TIMEOUT,
    )
    _raise_for_status(resp)
    return resp.json()


# ── Anime data ────────────────────────────────────────────────────────────────


async def get_anime_details(token: str, anime_id: str) -> dict:
    fields = "id,title,num_episodes,my_list_status{status,num_episodes_watched,start_date,finish_date,is_rewatching,num_times_rewatched}"
    client = get_client()
    resp = await client.get(
        f"{BASE_URL}/anime/{anime_id}",
        headers={"Authorization": f"Bearer {token}"},
        params={"fields": fields},
        timeout=TIMEOUT,
    )
    _raise_for_status(resp)
    return resp.json()


async def get_user_anime_list(token: str, status: str = "", limit: int = 100, offset: int = 0) -> dict:
    fields = "id,title,main_picture,num_episodes,status,mean,my_list_status{status,score,num_episodes_watched,updated_at},genres,media_type,end_date"
    params = {"fields": fields, "limit": limit, "offset": offset}
    if status:
        params["status"] = status
    client = get_client()
    resp = await client.get(
        f"{BASE_URL}/users/@me/animelist",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=TIMEOUT,
    )
    _raise_for_status(resp)
    return resp.json()


async def search_anime(token: str, query: str, limit: int = 100, offset: int = 0) -> dict:
    fields = (
        "id,title,main_picture,num_episodes,status,mean,my_list_status{status,num_episodes_watched,updated_at},media_type"
    )

    params = {"q": query, "fields": fields, "limit": limit, "offset": offset}
    client = get_client()
    resp = await client.get(
        f"{BASE_URL}/anime",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=TIMEOUT,
    )
    _raise_for_status(resp)
    return resp.json()


async def update_watch_status(
    token: str,
    anime_id: str,
    episode: int,
    status: str,
    start_date: str = "",
    finish_date: str = "",
    is_rewatching: bool | None = None,
    num_times_rewatched: int | None = None,
) -> dict:
    body: dict = {"status": status, "num_watched_episodes": episode}
    if start_date:
        body["start_date"] = start_date
    if finish_date:
        body["finish_date"] = finish_date
    if is_rewatching is not None:
        body["is_rewatching"] = "true" if is_rewatching else "false"
    if num_times_rewatched is not None:
        body["num_times_rewatched"] = num_times_rewatched

    client = get_client()
    resp = await client.put(
        f"{BASE_URL}/anime/{anime_id}/my_list_status",
        headers={"Authorization": f"Bearer {token}"},
        data=body,
        timeout=TIMEOUT,
    )
    _raise_for_status(resp)
    return resp.json()
