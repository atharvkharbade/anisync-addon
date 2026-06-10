import os
import secrets
import hashlib
import base64
from typing import Optional
from urllib.parse import urlencode

import httpx

from config import Config
from app.services.http import get_client

AUTH_URL = "https://myanimelist.net/v1"
BASE_URL = "https://api.myanimelist.net/v2"
N_BYTES = 96
TIMEOUT = 10
CODE_CHALLENGE_METHOD = "plain"


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
    params = urlencode({
        "response_type": "code",
        "client_id": _client_id(),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": CODE_CHALLENGE_METHOD,
        "redirect_uri": _redirect_uri(),
    })
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
    resp.raise_for_status()
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
    resp.raise_for_status()
    return resp.json()


async def get_user_details(token: str) -> dict:
    client = get_client()
    resp = await client.get(
        f"{BASE_URL}/users/@me",
        headers={"Authorization": f"Bearer {token}"},
        params={"fields": "id,name,picture"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


# ── Anime data ────────────────────────────────────────────────────────────────

async def get_anime_details(token: str, anime_id: str) -> dict:
    fields = "id,title,num_episodes,my_list_status{status,num_episodes_watched,start_date,finish_date,is_rewatching}"
    client = get_client()
    resp = await client.get(
        f"{BASE_URL}/anime/{anime_id}",
        headers={"Authorization": f"Bearer {token}"},
        params={"fields": fields},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


async def get_user_anime_list(token: str, status: str = "", limit: int = 100, offset: int = 0) -> dict:
    fields = "id,title,main_picture,num_episodes,status,my_list_status{status,score,num_episodes_watched,updated_at},genres,media_type"
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
    resp.raise_for_status()
    return resp.json()


async def search_anime(token: str, query: str, limit: int = 100, offset: int = 0) -> dict:
    fields = "id,title,main_picture,num_episodes,status,my_list_status{status,num_episodes_watched,updated_at},media_type"
    params = {"q": query, "fields": fields, "limit": limit, "offset": offset}
    client = get_client()
    resp = await client.get(
        f"{BASE_URL}/anime",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


async def update_watch_status(
    token: str,
    anime_id: str,
    episode: int,
    status: str,
    start_date: str = "",
    finish_date: str = "",
) -> dict:
    body: dict = {"status": status, "num_watched_episodes": episode}
    if start_date:
        body["start_date"] = start_date
    if finish_date:
        body["finish_date"] = finish_date

    client = get_client()
    resp = await client.put(
        f"{BASE_URL}/anime/{anime_id}/my_list_status",
        headers={"Authorization": f"Bearer {token}"},
        data=body,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()
