import asyncio
import logging
import urllib.parse

from quart import Blueprint

from app.lib.id_resolver import resolve
from app.routes.utils import respond_with, is_valid_user_id, rate_limit
from app.services.db import get_user
from app.services.anilist_service import sync_anilist
from app.services.mal_service import sync_mal

subtitles_bp = Blueprint("subtitles", __name__)


@subtitles_bp.route("/<user_id>/subtitles/<string:content_type>/<path:content_id>.json")
@rate_limit(limit=60, period_seconds=60)
async def handle_subtitles(user_id: str, content_type: str, content_id: str):
    if not is_valid_user_id(user_id):
        return await respond_with({"subtitles": []})

    content_id = urllib.parse.unquote(content_id)

    # content_id format: "kitsu:KITSU_ID:EPISODE" or
    # "kitsu:KITSU_ID/filename=...&videoSize=..." (torrent stream)
    # We must extract only the numeric kitsu_id and episode number

    if not content_id.startswith("kitsu:"):
        return await respond_with({"subtitles": []})

    # Strip the "kitsu:" prefix
    remainder = content_id[len("kitsu:"):]

    # Strip anything after "/" (video hash / filename junk)
    remainder = remainder.split("/")[0]

    # Now split by ":" to get kitsu_id and optional episode
    parts = remainder.split(":")
    kitsu_id = parts[0].strip()
    episode = 1
    if len(parts) > 1 and parts[1].isdigit():
        episode = int(parts[1])

    if not kitsu_id.isdigit():
        logging.warning("Could not parse kitsu_id from content_id=%s", content_id)
        return await respond_with({"subtitles": []})

    logging.info("Subtitles hook: kitsu_id=%s episode=%d user=%s", kitsu_id, episode, user_id)

    user = get_user(user_id)
    if not user:
        logging.warning("Unknown user_id=%s", user_id)
        return await respond_with({"subtitles": []})

    mal_enabled = user.get("mal_enabled", False)
    anilist_enabled = user.get("anilist_enabled", False)
    sync_unlisted = user.get("sync_unlisted", False)

    if not mal_enabled and not anilist_enabled:
        return await respond_with({"subtitles": []})

    mal_id, anilist_id = await resolve(kitsu_id)
    logging.info("Resolved: kitsu=%s → mal=%s anilist=%s", kitsu_id, mal_id, anilist_id)

    tasks = []
    if mal_enabled and mal_id and user.get("mal_access_token"):
        tasks.append(sync_mal(user, mal_id, episode, sync_unlisted))
    if anilist_enabled and anilist_id and user.get("anilist_token"):
        tasks.append(sync_anilist(user, anilist_id, episode, sync_unlisted))

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        any_updated = False
        for r in results:
            if isinstance(r, Exception):
                logging.error("Sync task error: %s", r)
            else:
                logging.info("Sync result: %s", r)
                if getattr(r, "name", None) == "OK":
                    any_updated = True
        
        if any_updated:
            from app.services.db import invalidate_user_watchlist_cache
            invalidate_user_watchlist_cache(user_id)

    return await respond_with({"subtitles": []})
