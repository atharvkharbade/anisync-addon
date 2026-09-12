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


_in_flight_scrobbles: set[tuple[str, str, int]] = set()


async def _release_scrobble_key(key: tuple[str, str, int]):
    await asyncio.sleep(5.0)
    _in_flight_scrobbles.discard(key)


@subtitles_bp.route("/<user_id>/subtitles/<string:content_type>/<path:content_id>.json")
@rate_limit(limit=60, period_seconds=60)
async def handle_subtitles(user_id: str, content_type: str, content_id: str):
    if not is_valid_user_id(user_id):
        return await respond_with({"subtitles": []})

    content_id = urllib.parse.unquote(content_id)

    # Strip anything after "/" (video hash / filename junk)
    clean_id = content_id.split("/")[0]

    kitsu_id = None
    mal_id = None
    anilist_id = None
    simkl_id = None
    episode = 1

    if clean_id.startswith("kitsu:"):
        remainder = clean_id[len("kitsu:") :]
        parts = remainder.split(":")
        kitsu_id = parts[0].strip()
        if len(parts) > 1 and parts[1].isdigit():
            episode = int(parts[1])
    elif clean_id.startswith("mal:"):
        remainder = clean_id[len("mal:") :]
        parts = remainder.split(":")
        mal_id = parts[0].strip()
        if len(parts) > 1 and parts[1].isdigit():
            episode = int(parts[1])
        from app.lib.id_resolver import resolve_mal_to_kitsu

        kitsu_id = await resolve_mal_to_kitsu(mal_id)
    elif clean_id.startswith("anilist:"):
        remainder = clean_id[len("anilist:") :]
        parts = remainder.split(":")
        anilist_id = parts[0].strip()
        if len(parts) > 1 and parts[1].isdigit():
            episode = int(parts[1])
        from app.lib.id_resolver import resolve_anilist_to_kitsu

        kitsu_id = await resolve_anilist_to_kitsu(anilist_id)
    elif clean_id.startswith("simkl:"):
        remainder = clean_id[len("simkl:") :]
        parts = remainder.split(":")
        simkl_id = parts[0].strip()
        if len(parts) > 1 and parts[1].isdigit():
            episode = int(parts[1])
        from app.lib.id_resolver import resolve_simkl_to_kitsu

        kitsu_id = await resolve_simkl_to_kitsu(simkl_id)
    else:
        return await respond_with({"subtitles": []})

    if not kitsu_id and not mal_id and not anilist_id and not simkl_id:
        logging.warning("Could not resolve anime IDs from content_id=%s", content_id)
        return await respond_with({"subtitles": []})

    # Debounce duplicate in-flight requests (Stremio fires multiple subtitle queries on play)
    scrobble_key = (str(user_id), str(clean_id), episode)
    if scrobble_key in _in_flight_scrobbles:
        logging.info("Debouncing duplicate in-flight subtitle scrobble for %s", scrobble_key)
        return await respond_with({"subtitles": []})
    _in_flight_scrobbles.add(scrobble_key)

    user = get_user(user_id, for_manifest=True)
    if not user:
        logging.warning("Unknown user_id=%s", user_id)
        asyncio.create_task(_release_scrobble_key(scrobble_key))
        return await respond_with({"subtitles": []})

    mal_enabled = user.get("mal_enabled", True) if user.get("mal_access_token") else False
    anilist_enabled = user.get("anilist_enabled", True) if user.get("anilist_token") else False
    simkl_enabled = user.get("simkl_enabled", True) and bool(user.get("simkl_access_token"))
    sync_unlisted = user.get("sync_unlisted", True)

    if not mal_enabled and not anilist_enabled and not simkl_enabled:
        asyncio.create_task(_release_scrobble_key(scrobble_key))
        return await respond_with({"subtitles": []})

    try:
        if kitsu_id:
            resolved_mal, resolved_al = await resolve(kitsu_id)
            mal_id = mal_id or resolved_mal
            anilist_id = anilist_id or resolved_al
            logging.info("Resolved: kitsu=%s → mal=%s anilist=%s", kitsu_id, mal_id, anilist_id)

        from app.services.db import get_cached_ids, cache_ids, db, update_user_watchlist_cache_progress
        cached_ids = get_cached_ids(kitsu_id) if kitsu_id else None
        if cached_ids:
            simkl_id = cached_ids.get("simkl_id")
        if not simkl_id:
            try:
                fribb_doc = db.fribb_mappings.find_one({"kitsu_id": int(kitsu_id)})
                if fribb_doc and fribb_doc.get("simkl_id"):
                    simkl_id = str(fribb_doc["simkl_id"])
                    try:
                        cache_ids(kitsu_id, mal_id, anilist_id, simkl_id=simkl_id)
                    except Exception:
                        pass
            except Exception:
                pass

        tasks = []
        if mal_enabled and mal_id and user.get("mal_access_token"):
            tasks.append(sync_mal(user, mal_id, episode, sync_unlisted))
        if anilist_enabled and anilist_id and user.get("anilist_token"):
            tasks.append(sync_anilist(user, anilist_id, episode, sync_unlisted))
        if simkl_enabled and user.get("simkl_access_token"):
            tasks.append(sync_simkl(user, kitsu_id, mal_id, anilist_id, episode, content_type, sync_unlisted, simkl_id=simkl_id))

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
                update_user_watchlist_cache_progress(
                    user_id=user_id,
                    episode=episode,
                    mal_id=mal_id,
                    anilist_id=anilist_id,
                    simkl_id=simkl_id,
                )
    finally:
        asyncio.create_task(_release_scrobble_key(scrobble_key))

    return await respond_with({"subtitles": []})
