from datetime import datetime, timedelta
import secrets
from urllib.parse import urlencode

from quart import Blueprint, flash, redirect, render_template, request, session, url_for

from app.api import anilist as al_api
from app.api import mal as mal_api
from app.routes.utils import log_error, rate_limit
from app.services.db import get_user, store_user, find_user_by_mal_id, find_user_by_anilist_id
from config import Config

auth_bp = Blueprint("auth", __name__)


# ── MAL OAuth (PKCE / authorization code) ────────────────────────────────────

@auth_bp.route("/authorization")
@rate_limit(limit=10, period_seconds=60)
async def authorize_mal():
    code_verifier, code_challenge = mal_api.generate_pkce()
    state = secrets.token_urlsafe(16)
    
    session["code_verifier"] = code_verifier
    session["oauth_state"] = state
    
    auth_url = mal_api.get_auth_url(code_challenge, state)
    return await render_template("mal_connecting.html", redirect_url=auth_url)


@auth_bp.route("/callback")
@rate_limit(limit=10, period_seconds=60)
async def mal_callback():
    if request.args.get("error"):
        await flash(request.args.get("message", "MAL authorization failed."), "danger")
        return redirect(url_for("ui.index"))

    # Verify state to prevent CSRF
    req_state = request.args.get("state")
    saved_state = session.pop("oauth_state", None)
    if not saved_state or req_state != saved_state:
        await flash("Authorization failed: Invalid OAuth state (CSRF check failed).", "danger")
        return redirect(url_for("ui.index"))

    if not (code := request.args.get("code")):
        await flash("Invalid callback. Please try again.", "warning")
        return redirect(url_for("ui.index"))

    code_verifier = session.pop("code_verifier", None)
    if not code_verifier:
        await flash("Session error. Please try again.", "warning")
        return redirect(url_for("ui.index"))

    try:
        token_data = await mal_api.get_access_token(code, code_verifier)
        user_info = await mal_api.get_user_details(token_data["access_token"])

        mal_id = str(user_info["id"])
        
        user_session = session.get("user")
        if user_session:
            uid = user_session["uid"]
            existing = get_user(uid) or {}
        else:
            existing = find_user_by_mal_id(mal_id) or {}
            uid = existing.get("uid") or mal_id
            session["user"] = {"uid": uid}
            session.permanent = True

        existing.update({
            "uid": uid,
            "mal_id": mal_id,
            "name": user_info.get("name", ""),
            "mal_picture": user_info.get("picture", ""),
            "picture": user_info.get("picture", "") or existing.get("anilist_picture", ""),
            "mal_access_token": token_data["access_token"],
            "mal_refresh_token": token_data["refresh_token"],
            "mal_expires_at": datetime.utcnow() + timedelta(seconds=token_data["expires_in"]),
            "mal_enabled": existing.get("mal_enabled", True),
        })
        store_user(existing)

        await flash("Connected to MyAnimeList!", "success")
        return redirect(url_for("ui.configure"))

    except Exception as e:
        log_error("MAL_CALLBACK", str(e))
        await flash("Failed to connect to MyAnimeList.", "danger")
        return redirect(url_for("ui.index"))


@auth_bp.route("/refresh-mal")
@rate_limit(limit=10, period_seconds=60)
async def refresh_mal():
    user_session = session.get("user")
    if not user_session:
        await flash("Not logged in.", "warning")
        return redirect(url_for("ui.index"))

    user = get_user(user_session["uid"])
    if not user or not user.get("mal_refresh_token"):
        await flash("No MAL session to refresh.", "danger")
        return redirect(url_for("ui.index"))

    try:
        token_data = await mal_api.refresh_token(user["mal_refresh_token"])
        user.update({
            "mal_access_token": token_data["access_token"],
            "mal_refresh_token": token_data["refresh_token"],
            "mal_expires_at": datetime.utcnow() + timedelta(seconds=token_data["expires_in"]),
        })
        store_user(user)
        await flash("MAL session refreshed.", "success")
    except Exception as e:
        log_error("MAL_REFRESH", str(e))
        await flash("Failed to refresh MAL session.", "danger")

    return redirect(url_for("ui.configure"))


@auth_bp.route("/logout")
@rate_limit(limit=10, period_seconds=60)
async def logout():
    session.pop("user", None)
    await flash("Logged out.", "info")
    return redirect(url_for("ui.index"))


# ── AniList OAuth (Authorization Code flow with PKCE backend validation) ──────

@auth_bp.route("/authorize-anilist")
@rate_limit(limit=10, period_seconds=60)
async def authorize_anilist():
    anilist_url = (
        f"https://anilist.co/api/v2/oauth/authorize"
        f"?client_id={Config.ANILIST_CLIENT_ID}"
        f"&response_type=token"
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
    if not token:
        return {"ok": False, "error": "No token provided"}, 400

    try:
        viewer = await al_api.get_viewer(token)
        anilist_uid = str(viewer["id"])
        anilist_username = viewer.get("name", "")
        anilist_picture = viewer.get("avatar", {}).get("large", "")

        user_session = session.get("user")
        if user_session:
            uid = user_session["uid"]
            user = get_user(uid) or {}
        else:
            user = find_user_by_anilist_id(anilist_uid) or {}
            uid = user.get("uid") or f"al_{anilist_uid}"
            session["user"] = {"uid": uid}
            session.permanent = True

        user.update({
            "uid": uid,
            "anilist_id": anilist_uid,
            "anilist_token": token,
            "anilist_username": anilist_username,
            "anilist_enabled": user.get("anilist_enabled", True),
            "anilist_picture": anilist_picture,
            "picture": anilist_picture or user.get("mal_picture") or user.get("picture") or "",
        })
        store_user(user)
        return {"ok": True, "username": anilist_username}

    except Exception as e:
        log_error("ANILIST_SAVE", str(e))
        return {"ok": False, "error": "Invalid token"}, 400


@auth_bp.route("/disconnect-mal")
@rate_limit(limit=10, period_seconds=60)
async def disconnect_mal():
    user_session = session.get("user")
    if not user_session:
        return redirect(url_for("ui.index"))

    user = get_user(user_session["uid"])
    if user:
        user.pop("mal_access_token", None)
        user.pop("mal_refresh_token", None)
        user.pop("mal_expires_at", None)
        user.pop("name", None)
        user.pop("mal_picture", None)

        from app.services.db import invalidate_user_watchlist_cache
        if not user.get("anilist_token"):
            user.pop("picture", None)
            session.pop("user", None)
            store_user(user)
            invalidate_user_watchlist_cache(user_session["uid"])
            await flash("Disconnected from MyAnimeList and logged out.", "info")
            return redirect(url_for("ui.index"))
        else:
            user["picture"] = user.get("anilist_picture", "")
            store_user(user)
            invalidate_user_watchlist_cache(user_session["uid"])
            await flash("Disconnected from MyAnimeList.", "info")
            return redirect(url_for("ui.configure"))

    return redirect(url_for("ui.index"))


@auth_bp.route("/disconnect-anilist")
@rate_limit(limit=10, period_seconds=60)
async def disconnect_anilist():
    user_session = session.get("user")
    if not user_session:
        return redirect(url_for("ui.index"))

    user = get_user(user_session["uid"])
    if user:
        user.pop("anilist_token", None)
        user.pop("anilist_username", None)
        user.pop("anilist_picture", None)

        from app.services.db import invalidate_user_watchlist_cache
        if not user.get("mal_access_token"):
            user.pop("picture", None)
            session.pop("user", None)
            store_user(user)
            invalidate_user_watchlist_cache(user_session["uid"])
            await flash("Disconnected from AniList and logged out.", "info")
            return redirect(url_for("ui.index"))
        else:
            user["picture"] = user.get("mal_picture", "")
            store_user(user)
            invalidate_user_watchlist_cache(user_session["uid"])
            await flash("Disconnected from AniList.", "info")
            return redirect(url_for("ui.configure"))

    return redirect(url_for("ui.index"))
