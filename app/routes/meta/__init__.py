import asyncio
import logging
import urllib.parse

from quart import Blueprint

from app.lib.id_resolver import resolve, resolve_anilist_to_kitsu, resolve_mal_to_kitsu, resolve_simkl_to_kitsu
from app.lib.meta_providers import get_effective_meta_providers
from app.routes.utils import is_valid_user_id, rate_limit, respond_with
from app.services.db import get_user

from .fetchers import (
    apply_metadata_provider_override,
    clean_imdb_id,
    fetch_anizp_metadata,
    fetch_cinemeta_metadata,
    fetch_kitsu_meta,
    get_banner_aspect_ratio,
)
from .headers import (
    build_expired_trackers_notice,
    build_filler_arc_header,
    build_next_airing_header,
    build_user_status_header,
)
from .stremio import map_kitsu_to_stremio

meta_bp = Blueprint("meta", __name__)


@meta_bp.route("/meta/<string:meta_type>/<string:meta_id>.json")
@rate_limit(limit=60, period_seconds=60)
async def handle_root_meta(meta_type: str, meta_id: str):
    return await respond_with({"meta": {}})


@meta_bp.route("/<user_id>/meta/<string:meta_type>/<string:meta_id>.json")
@rate_limit(limit=60, period_seconds=60)
async def handle_meta(user_id: str, meta_type: str, meta_id: str):
    meta_id = urllib.parse.unquote(meta_id)
    if meta_type not in ["anime", "series", "movie"]:
        return await respond_with({"meta": {}})

    if not is_valid_user_id(user_id):
        return await respond_with({"meta": {}})

    user = get_user(user_id, for_manifest=True)
    if not user:
        logging.warning("Meta request: Unknown user_id=%s", user_id)
        return await respond_with({"meta": {}})

    # Strip prefixes and get kitsu id
    kitsu_id = None
    anilist_id = None
    mal_id = None
    simkl_id = None

    if meta_id.startswith(("mal:", "mal-", "mal_")):
        mal_id = meta_id[4:]
        kitsu_id = await resolve_mal_to_kitsu(mal_id)
    elif meta_id.startswith(("anilist:", "anilist-", "anilist_")):
        anilist_id = meta_id[8:]
        kitsu_id = await resolve_anilist_to_kitsu(anilist_id)
    elif meta_id.startswith(("simkl:", "simkl-", "simkl_")):
        simkl_id = meta_id[6:]
        kitsu_id = await resolve_simkl_to_kitsu(simkl_id)
    elif meta_id.startswith(("kitsu:", "kitsu-", "kitsu_")):
        kitsu_id = meta_id[6:]

    if not kitsu_id:
        logging.warning("Could not map meta_id=%s to Kitsu ID", meta_id)
        return await respond_with({"meta": {}})

    # Resolve mapped IDs using db cache or resolvers robustly
    resolved_mal, resolved_anilist = await resolve(kitsu_id)
    if not mal_id:
        mal_id = resolved_mal
    if not anilist_id:
        anilist_id = resolved_anilist

    try:
        tasks = [fetch_kitsu_meta(kitsu_id)]
        if anilist_id or mal_id:
            tasks.append(fetch_anizp_metadata(anilist_id=anilist_id, mal_id=mal_id))
        else:
            tasks.append(asyncio.sleep(0, {}))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        kitsu_data = results[0] if not isinstance(results[0], Exception) else {}
        anizp_data = results[1] if (len(results) > 1 and not isinstance(results[1], Exception)) else {}

        if not kitsu_data:
            return await respond_with({"meta": {}})

        imdb_id = clean_imdb_id(anizp_data.get("mappings", {}).get("imdb_id") if anizp_data else None)
        if not imdb_id and kitsu_id:
            from app.services.db import db, get_cached_ids

            cached_ids = get_cached_ids(kitsu_id)
            if cached_ids:
                imdb_id = clean_imdb_id(cached_ids.get("imdb_id"))
            if not imdb_id:
                try:
                    fribb_doc = db.fribb_mappings.find_one({"kitsu_id": int(kitsu_id)})
                    if fribb_doc:
                        imdb_id = clean_imdb_id(fribb_doc.get("imdb_id"))
                except Exception as e:
                    logging.warning("Failed to query fribb_mappings for imdb_id: %s", e)
            if imdb_id:
                if not isinstance(anizp_data, dict):
                    anizp_data = {}
                if "mappings" not in anizp_data:
                    anizp_data["mappings"] = {}
                anizp_data["mappings"]["imdb_id"] = imdb_id

        cinemeta_data = {}
        if imdb_id:
            k_data = kitsu_data.get("data") or {} if isinstance(kitsu_data, dict) else {}
            k_attrs = k_data.get("attributes") or {} if isinstance(k_data, dict) else {}
            k_status = (k_attrs.get("status") or "").lower()
            is_releasing = k_status in ["current", "releasing", "unreleased", "not_yet_released"]
            subtype = (k_attrs.get("subtype") or "tv").lower()
            media_type = "movie" if subtype == "movie" else "series"
            cinemeta_data = await fetch_cinemeta_metadata(imdb_id, media_type, is_releasing=is_releasing)

        # Resolve simkl_id if not present but we have kitsu_id
        if not simkl_id and kitsu_id:
            from app.services.db import cache_ids, db, get_cached_ids

            cached_ids = get_cached_ids(kitsu_id)
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
                except Exception as e:
                    logging.warning("Failed to query fribb_mappings for simkl_id: %s", e)
        show_filler = user.get("show_filler_tags", True) if user else True
        show_watched = user.get("show_watched_tags", False) if user else False
        watched_progress = 0
        if show_watched:
            from app.services.db import get_user_watch_progress

            watched_progress = get_user_watch_progress(user_id, mal_id=mal_id, anilist_id=anilist_id, simkl_id=simkl_id)

        title_lang = user.get("title_language", "english") if user else "english"
        effective_provs = get_effective_meta_providers(user)

        # Offload CPU-bound mapping to worker threads
        run_loop = asyncio.get_running_loop()
        meta = await asyncio.to_thread(
            map_kitsu_to_stremio,
            kitsu_data,
            meta_id,
            anizp_data=anizp_data,
            mal_id=mal_id,
            show_filler_tags=show_filler,
            loop=run_loop,
            cinemeta_data=cinemeta_data,
            show_watched_tags=show_watched,
            watched_progress=watched_progress,
            title_language=title_lang,
            episodes_provider=effective_provs.get("episodes", "anizp"),
            backdrop_provider=effective_provs.get("backdrop", "fanart"),
            poster_provider=effective_provs.get("poster", "anilist"),
        )

        # Apply user preferred Metadata Provider override (MAL / AniList with Kitsu fallback)
        mal_data_override, al_data_override = await apply_metadata_provider_override(meta, user, mal_id, anilist_id)

        # Apply custom poster provider if configured
        if (user.get("poster_provider") and user.get("poster_provider") != "none") or user.get("rpdb_api_key"):
            from app.services.poster import get_poster_url

            meta["poster"] = get_poster_url(
                user=user,
                media_type=meta.get("type", "series"),
                kitsu_id=kitsu_id,
                mal_id=mal_id,
                anilist_id=anilist_id,
                fallback_poster=meta.get("poster"),
            )

        notice = build_expired_trackers_notice(user)
        if notice:
            curr_desc = meta.get("description", "")
            meta["description"] = f"{notice}\n\n\n{curr_desc}" if curr_desc else notice

        # Collect dynamic metadata headers
        dynamic_headers = []

        show_tracking = user.get("show_tracking_in_synopsis", True) if user else True
        show_airing = user.get("show_airing_in_synopsis", True) if user else True

        if show_tracking:
            user_status_hdr = build_user_status_header(user_id, mal_id=mal_id, anilist_id=anilist_id, simkl_id=simkl_id)
            if user_status_hdr:
                dynamic_headers.append(user_status_hdr)

        if show_airing:
            airing_prov = effective_provs.get("airing", "anilist")
            al_data = al_data_override if (airing_prov == "anilist" and al_data_override) else None
            if airing_prov == "anilist" and not al_data and anilist_id:
                try:
                    from app.api.anilist import get_media_status

                    token = user.get("anilist_token", "") if user else ""
                    al_data = await asyncio.wait_for(get_media_status(token, int(anilist_id)), timeout=3.0)
                except Exception as e:
                    logging.debug("Could not fetch AniList media status for airing countdown: %s", e)

            mal_data_airing = mal_data_override if (airing_prov == "mal" and mal_data_override) else None
            if airing_prov == "mal" and not mal_data_airing and mal_id:
                try:
                    from app.api.jikan import get_anime_by_id

                    mal_data_airing = await asyncio.wait_for(get_anime_by_id(mal_id), timeout=3.0)
                except Exception:
                    pass

            if airing_prov != "kitsu":
                next_airing_hdr = build_next_airing_header(al_data, mal_data=mal_data_airing)
                if next_airing_hdr:
                    dynamic_headers.append(next_airing_hdr)

        if show_filler:
            filler_arc_hdr = build_filler_arc_header(anizp_data, watched_progress=watched_progress, mal_id=mal_id)
            if filler_arc_hdr:
                dynamic_headers.append(filler_arc_hdr)

        if dynamic_headers:
            header_text = "\n".join(dynamic_headers)
            curr_desc = meta.get("description", "")
            meta["description"] = f"{header_text}\n\n{curr_desc}" if curr_desc else header_text

        return await respond_with({"meta": meta}, max_age=86400, stale_while_revalidate=604800)
    except Exception as e:
        logging.error("Failed to handle meta for %s: %s", meta_id, e)
        return await respond_with({"meta": {}})


__all__ = [
    "meta_bp",
    "handle_root_meta",
    "handle_meta",
    "clean_imdb_id",
    "fetch_anizp_metadata",
    "fetch_cinemeta_metadata",
    "fetch_kitsu_meta",
    "get_banner_aspect_ratio",
    "apply_metadata_provider_override",
    "build_user_status_header",
    "build_next_airing_header",
    "build_filler_arc_header",
    "build_expired_trackers_notice",
    "map_kitsu_to_stremio",
]
