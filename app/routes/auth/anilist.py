import asyncio
import secrets
from datetime import datetime

import httpx
from quart import flash, redirect, render_template, request, session, url_for

from app.api import anilist as al_api
from app.routes.auth.blueprint import auth_bp
from app.routes.auth.helpers import resolve_or_create_user
from app.routes.utils import log_error, rate_limit
from app.services.db import find_user_by_anilist_id, get_user, invalidate_user_watchlist_cache, store_user
from config import Config


@auth_bp.route("/authorize-anilist")
@rate_limit(limit=10, period_seconds=60)
async def authorize_anilist():
    state = secrets.token_urlsafe(32)
    session["anilist_oauth_state"] = state
    anilist_url = (
        f"https://anilist.co/api/v2/oauth/authorize"
        f"?client_id={Config.ANILIST_CLIENT_ID}"
        f"&response_type=token"
        f"&state={state}"
    )
    return redirect(anilist_url)


@auth_bp.route("/anilist-callback")
@rate_limit(limit=10, period_seconds=60)
async def anilist_callback():
    return await render_template("anilist_callback.html")


@auth_bp.route("/anilist-save", methods=["POST"])
@rate_limit(limit=10, period_seconds=60)
async def anilist_save():
    form = await request.form
    token = form.get("token", "").strip()
    req_state = form.get("state", "").strip()
    saved_state = session.pop("anilist_oauth_state", None)

    if not saved_state or not req_state or req_state != saved_state:
        return {"ok": False, "error": "Invalid OAuth state (CSRF check failed)"}, 403

    if not token:
        return {"ok": False, "error": "No token provided"}, 400

    try:
        viewer = await al_api.get_viewer(token)
        anilist_uid = str(viewer["id"])
        anilist_username = viewer.get("name", "")
        anilist_picture = viewer.get("avatar", {}).get("large", "")

        uid, user = resolve_or_create_user(session, anilist_uid, find_user_by_anilist_id)

        user.pop("is_guest", None)
        user["enable_catalogs"] = True
        user.setdefault("enable_recommendations", False)
        user.setdefault("enable_discovery_catalogs", False)
        user.setdefault("sync_unlisted", True)
        user.setdefault("show_filler_tags", False)
        user.setdefault("show_airing_in_synopsis", False)
        user.setdefault("show_tracking_in_synopsis", False)

        user.update(
            {
                "uid": uid,
                "anilist_id": anilist_uid,
                "anilist_token": token,
                "anilist_username": anilist_username,
                "anilist_enabled": True if user.get("anilist_token_expired") else user.get("anilist_enabled", True),
                "anilist_picture": anilist_picture,
                "picture": anilist_picture or user.get("mal_picture") or user.get("picture") or "",
                "last_profile_sync": datetime.utcnow(),
                "anilist_consecutive_auth_errors": 0,
            }
        )
        user.pop("anilist_last_auth_error_at", None)
        user.pop("anilist_token_expired", None)
        user.pop("anilist_expired_at", None)
        store_user(user)
        return {"ok": True, "username": anilist_username}

    # Order matters: AnilistTokenInvalidError is a custom Exception, caught first before httpx errors
    except al_api.AnilistTokenInvalidError as e:
        log_error("ANILIST_SAVE", f"AnilistTokenInvalidError: {str(e) or 'Invalid token'}")
        return {"ok": False, "error": "Your AniList session token is invalid or expired. Please authorize again."}, 401

    # TimeoutException is a subclass of RequestError; must be caught before generic RequestError
    except (httpx.TimeoutException, asyncio.TimeoutError) as e:
        err_msg = f"{type(e).__name__}: {str(e) or 'Request timed out'}"
        log_error("ANILIST_SAVE", err_msg)
        return {"ok": False, "error": "Connection to AniList timed out. Please try again."}, 504

    # ConnectError is a subclass of RequestError; must be caught before generic RequestError
    except httpx.ConnectError as e:
        err_msg = f"ConnectError: {str(e) or 'Connection or DNS lookup failed'}"
        log_error("ANILIST_SAVE", err_msg)
        return {"ok": False, "error": "Unable to connect to AniList. Connection or DNS lookup failed."}, 502

    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        err_msg = f"HTTPStatusError: {status} - {str(e) or 'HTTP error'}"
        log_error("ANILIST_SAVE", err_msg)
        if status == 429:
            return {"ok": False, "error": "AniList rate limit reached. Please wait a moment and try again."}, 429
        if status in (401, 403):
            return {"ok": False, "error": "AniList rejected the token (it may have expired or been revoked). Please authorize again."}, 401
        if 500 <= status <= 599:
            return {"ok": False, "error": f"AniList service is temporarily unavailable (HTTP {status}). Please try again later."}, 502
        return {"ok": False, "error": f"AniList error: HTTP {status}. Please try again."}, 502

    # Catches all remaining transport / protocol RequestErrors
    except httpx.RequestError as e:
        err_msg = f"{type(e).__name__}: {str(e) or 'Request error'}"
        log_error("ANILIST_SAVE", err_msg)
        return {"ok": False, "error": "Unable to communicate with AniList. Network or proxy connection error."}, 502

    # Fallback for unexpected internal errors
    except Exception as e:
        err_msg = f"{type(e).__name__}: {str(e) or 'Unknown error'}"
        log_error("ANILIST_SAVE", err_msg)
        return {"ok": False, "error": "Failed to complete AniList authorization. Please try again."}, 500


@auth_bp.route("/disconnect-anilist", methods=["GET", "POST"])
@rate_limit(limit=10, period_seconds=60)
async def disconnect_anilist():
    user_session = session.get("user")
    if not user_session:
        return redirect(url_for("ui.index"))

    if request.method == "GET" and request.args.get("confirm") != "1":
        await flash("Confirmation required to disconnect tracker.", "warning")
        return redirect(url_for("ui.configure"))

    user = get_user(user_session["uid"])
    if user:
        user.pop("anilist_token", None)
        user.pop("anilist_username", None)
        user.pop("anilist_picture", None)
        user.pop("anilist_token_expired", None)
        user.pop("anilist_expired_at", None)

        if not user.get("mal_access_token") and not user.get("simkl_access_token"):
            user.pop("picture", None)
            session.pop("user", None)
            store_user(user)
            invalidate_user_watchlist_cache(user_session["uid"])
            await flash("Disconnected from AniList and logged out.", "info")
            return redirect(url_for("ui.index"))
        else:
            user["picture"] = user.get("mal_picture") or user.get("simkl_avatar") or ""
            store_user(user)
            invalidate_user_watchlist_cache(user_session["uid"])
            await flash("Disconnected from AniList.", "info")
            return redirect(url_for("ui.configure"))

    return redirect(url_for("ui.index"))
