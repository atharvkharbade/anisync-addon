import secrets
from app.services.db import db, get_user, invalidate_user_watchlist_cache


def resolve_or_create_user(session_obj, tracker_id: str, find_by_tracker_func) -> tuple[str, dict]:
    """Resolves an existing user or creates a new UID for an authenticated tracker account.

    Handles:
    - Preserving existing authenticated user session if present (account linking)
    - Deleting temporary guest user record if user was in guest mode
    - Finding user by tracker ID in the database
    - Legacy UID prefix migration (stripping 'al_', 'simkl_', 'guest_')
    - Generating a unique 9-digit UID if no clean UID exists
    - Setting the session user cookie
    """
    user_session = session_obj.get("user")
    old_guest_uid = None

    if user_session and user_session.get("uid") and not user_session["uid"].startswith("guest_"):
        uid = user_session["uid"]
        user = get_user(uid) or {}
    else:
        if user_session and user_session.get("uid", "").startswith("guest_"):
            old_guest_uid = user_session["uid"]

        user = find_by_tracker_func(tracker_id) or {}
        uid = user.get("uid")

        # Migration check: if existing user has prefix, try to strip it
        if uid and (uid.startswith("al_") or uid.startswith("simkl_") or uid.startswith("guest_")):
            stripped_uid = uid.split("_", 1)[1]
            if stripped_uid.isdigit() and not db.get_collection("users").find_one({"uid": stripped_uid}):
                db.get_collection("users").delete_one({"uid": uid})
                invalidate_user_watchlist_cache(uid)
                uid = stripped_uid
                user["uid"] = uid

        is_new_account = not bool(uid)
        if not uid:
            if tracker_id.isdigit() and not db.get_collection("users").find_one({"uid": tracker_id}):
                uid = tracker_id
            else:
                while True:
                    candidate = str(100000000 + secrets.randbelow(900000000))
                    if not db.get_collection("users").find_one({"uid": candidate}):
                        uid = candidate
                        break

        if old_guest_uid:
            db.get_collection("users").delete_one({"_id": old_guest_uid})
            db.get_collection("users").delete_one({"uid": old_guest_uid})

        if hasattr(session_obj, "rotate"):
            session_obj.rotate()

        session_obj["user"] = {"uid": uid}
        if is_new_account:
            session_obj["is_new_user"] = True
            user.setdefault("sync_unlisted", True)
            user.setdefault("show_filler_tags", False)
            user.setdefault("show_airing_in_synopsis", False)
            user.setdefault("show_tracking_in_synopsis", False)
            user.setdefault("enable_discovery_catalogs", False)
            user.setdefault("enable_recommendations", False)
        if hasattr(session_obj, "permanent"):
            session_obj.permanent = True

    return uid, user
