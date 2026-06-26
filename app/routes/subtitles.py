import asyncio
import logging
import urllib.parse

from quart import Blueprint

from app.lib.id_resolver import resolve
from app.routes.utils import is_valid_user_id, rate_limit, respond_with
from app.services.anilist_service import sync_anilist
from app.services.db import get_user
from app.services.mal_service import sync_mal
from app.services.simkl_service import sync_simkl

subtitles_bp = Blueprint("subtitles", __name__)


@subtitles_bp.route("/<user_id>/subtitles/<string:content_type>/<path:content_id>.json")
@rate_limit(limit=60, period_seconds=60)
async def handle_subtitles(user_id: str, content_type: str, content_id: str):
    if not is_valid_user_id(user_id):
        return await respond_with({"subtitles": []})

    content_id = urllib.parse.unquote(content_id)

    # content_id format: "kitsu:KITSU_ID:EPISODE" or "mal:MAL_ID:EPISODE" or
    # "kitsu:KITSU_ID/filename=..." (torrent stream)
    # We must extract only the prefix, numeric ID and episode number

    prefix = None
    if content_id.startswith("kitsu:"):
        prefix = "kitsu"
        remainder = content_id[len("kitsu:") :]
    elif content_id.startswith("mal:"):
        prefix = "mal"
        remainder = content_id[len("mal:") :]
    else:
        return await respond_with({"subtitles": []})

    # Strip anything after "/" (video hash / filename junk)
    remainder = remainder.split("/")[0]

    # Now split by ":" to get kitsu_id/mal_id and optional episode
    parts = remainder.split(":")
    extracted_id = parts[0].strip()
    episode = 1
    if len(parts) > 1 and parts[1].isdigit():
        episode = int(parts[1])

    if not extracted_id.isdigit():
        logging.warning("Could not parse %s ID from content_id=%s", prefix, content_id)
        return await respond_with({"subtitles": []})

    logging.info("Subtitles hook: prefix=%s extracted_id=%s episode=%d user=%s", prefix, extracted_id, episode, user_id)

    user = get_user(user_id)
    if not user:
        logging.warning("Unknown user_id=%s", user_id)
        return await respond_with({"subtitles": []})

    mal_enabled = user.get("mal_enabled", False)
    anilist_enabled = user.get("anilist_enabled", False)
    simkl_enabled = user.get("simkl_enabled", True) and bool(user.get("simkl_access_token"))
    sync_unlisted = user.get("sync_unlisted", False)

    if not mal_enabled and not anilist_enabled and not simkl_enabled:
        return await respond_with({"subtitles": []})

    kitsu_id = None
    mal_id = None
    anilist_id = None

    if prefix == "kitsu":
        kitsu_id = extracted_id
        mal_id, anilist_id = await resolve(kitsu_id)
    else:  # prefix == "mal"
        mal_id = extracted_id
        from app.lib.id_resolver import resolve_mal_to_kitsu, fetch_anime_info_by_mal_id
        kitsu_id = await resolve_mal_to_kitsu(mal_id)
        if kitsu_id:
            _, anilist_id = await resolve(kitsu_id)
        else:
            _, anilist_id = await fetch_anime_info_by_mal_id(mal_id)

    logging.info("Resolved: kitsu=%s → mal=%s anilist=%s", kitsu_id, mal_id, anilist_id)

    tasks = []
    if mal_enabled and mal_id and user.get("mal_access_token"):
        tasks.append(sync_mal(user, mal_id, episode, sync_unlisted))
    if anilist_enabled and anilist_id and user.get("anilist_token"):
        tasks.append(sync_anilist(user, anilist_id, episode, sync_unlisted))
    if simkl_enabled and user.get("simkl_access_token"):
        tasks.append(sync_simkl(user, kitsu_id, mal_id, anilist_id, episode, content_type, sync_unlisted))

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
            from app.services.db import get_cached_ids, get_cached_ids_by_mal, update_user_watchlist_cache_progress

            simkl_id = None
            cached_ids = None
            if kitsu_id:
                cached_ids = get_cached_ids(kitsu_id)
            elif mal_id:
                cached_ids = get_cached_ids_by_mal(mal_id)

            if cached_ids:
                simkl_id = cached_ids.get("simkl_id")

            update_user_watchlist_cache_progress(
                user_id=user_id,
                episode=episode,
                mal_id=mal_id,
                anilist_id=anilist_id,
                simkl_id=simkl_id,
            )

    return await respond_with({"subtitles": []})
