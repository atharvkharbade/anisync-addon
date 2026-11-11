import asyncio
import logging
import time
import urllib.parse

from quart import Blueprint

from app.lib.id_resolver import resolve, resolve_anilist_to_kitsu, resolve_mal_to_kitsu, resolve_simkl_to_kitsu
from app.lib.meta_providers import get_effective_meta_providers
from app.routes.utils import is_valid_user_id, rate_limit, respond_with
from app.services.db import get_user

from .fetchers import (
    apply_metadata_provider_override,
    apply_provider_overrides,
    clean_imdb_id,
    fetch_anizp_metadata,
    fetch_cinemeta_metadata,
    fetch_kitsu_meta,
    fetch_provider_overrides,
    resolve_external_ids,
)
from .headers import (
    build_expired_trackers_notice,
    build_filler_arc_header,
    build_next_airing_header,
    build_user_status_header,
)
from .stremio import is_root_tvdb_entry, map_kitsu_to_stremio

meta_bp = Blueprint("meta", __name__)

# Strong references for shielded upstream fetches that outlive a timed-out request
_keep_alive: set[asyncio.Future] = set()


def _finish_keep_alive(task: asyncio.Future) -> None:
    """Release a background task reference and consume any late exception so asyncio stays quiet."""
    _keep_alive.discard(task)
    if not task.cancelled():
        try:
            task.exception()
        except Exception:
            pass


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

    deadline = time.monotonic() + 2.8

    def _budget(cap: float) -> float:
        return max(0.2, min(cap, deadline - time.monotonic()))

    # Set when any upstream timed out: the response is then partial, so Stremio must not keep it for a day
    degraded = False

    async def _shielded(coro, timeout: float):
        """Wait up to `timeout` without cancelling the fetch, so a slow upstream still fills its cache for the next request."""
        task = asyncio.ensure_future(coro)
        _keep_alive.add(task)
        task.add_done_callback(_finish_keep_alive)
        return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)

    # Strip prefixes and get kitsu id
    kitsu_id = None
    anilist_id = None
    mal_id = None
    simkl_id = None

    try:
        if meta_id.startswith(("mal:", "mal-", "mal_")):
            mal_id = meta_id[4:]
            kitsu_id = await _shielded(resolve_mal_to_kitsu(mal_id), _budget(2.0))
        elif meta_id.startswith(("anilist:", "anilist-", "anilist_")):
            anilist_id = meta_id[8:]
            kitsu_id = await _shielded(resolve_anilist_to_kitsu(anilist_id), _budget(2.0))
        elif meta_id.startswith(("simkl:", "simkl-", "simkl_")):
            simkl_id = meta_id[6:]
            kitsu_id = await _shielded(resolve_simkl_to_kitsu(simkl_id), _budget(2.0))
        elif meta_id.startswith(("kitsu:", "kitsu-", "kitsu_")):
            kitsu_id = meta_id[6:]
    except TimeoutError:
        logging.warning("Timed out resolving Kitsu ID for meta_id=%s", meta_id)
        kitsu_id = None

    if not kitsu_id:
        logging.warning("Could not map meta_id=%s to Kitsu ID", meta_id)
        return await respond_with({"meta": {}})

    # Resolve mapped IDs using db cache or resolvers robustly
    try:
        resolved_mal, resolved_anilist = await _shielded(resolve(kitsu_id), _budget(1.5))
    except TimeoutError:
        logging.warning("Timed out resolving mapped IDs for kitsu_id=%s", kitsu_id)
        resolved_mal, resolved_anilist = None, None
        degraded = True
    if not mal_id:
        mal_id = resolved_mal
    if not anilist_id:
        anilist_id = resolved_anilist

    try:
        tasks = [asyncio.wait_for(fetch_kitsu_meta(kitsu_id), timeout=_budget(2.5))]
        if anilist_id or mal_id:
            tasks.append(
                _shielded(fetch_anizp_metadata(anilist_id=anilist_id, mal_id=mal_id), _budget(1.4))
            )
        else:
            tasks.append(asyncio.sleep(0, {}))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        if any(isinstance(r, TimeoutError) for r in results):
            degraded = True
        kitsu_data = results[0] if not isinstance(results[0], Exception) else {}
        anizp_data = results[1] if (len(results) > 1 and not isinstance(results[1], Exception)) else {}

        if not kitsu_data:
            return await respond_with({"meta": {}})

        k_data = kitsu_data.get("data") or {} if isinstance(kitsu_data, dict) else {}
        k_attrs = k_data.get("attributes") or {} if isinstance(k_data, dict) else {}
        k_status = (k_attrs.get("status") or "").lower()
        is_releasing = k_status in ["current", "releasing", "unreleased", "not_yet_released"]
        subtype = (k_attrs.get("subtype") or "tv").lower()
        media_type = "movie" if subtype == "movie" else "series"

        imdb_id, tvdb_id, simkl_id, is_movie = resolve_external_ids(
            kitsu_id,
            anizp_data,
            subtype,
            simkl_id=simkl_id,
            mal_id=mal_id,
            anilist_id=anilist_id,
        )
        if imdb_id and not (isinstance(anizp_data, dict) and clean_imdb_id((anizp_data.get("mappings") or {}).get("imdb_id"))):
            if not isinstance(anizp_data, dict):
                anizp_data = {}
            if "mappings" not in anizp_data or not isinstance(anizp_data["mappings"], dict):
                anizp_data["mappings"] = {}
            anizp_data["mappings"]["imdb_id"] = imdb_id

        show_filler = user.get("show_filler_tags", False) if user else False
        show_watched = user.get("show_watched_tags", False) if user else False
        show_tracking = user.get("show_tracking_in_synopsis", False) if user else False
        show_airing = user.get("show_airing_in_synopsis", False) if user else False
        watched_progress = 0
        canonical_total_episodes = None
        if user_id:
            from app.services.db import get_user_anime_meta_status, get_user_watch_progress

            status_info = get_user_anime_meta_status(user_id, mal_id=mal_id, anilist_id=anilist_id, simkl_id=simkl_id)
            if status_info:
                if show_watched or show_filler:
                    watched_progress = status_info.get("progress") or 0
                canonical_total_episodes = status_info.get("total_episodes")
            elif show_watched or show_filler:
                watched_progress = get_user_watch_progress(user_id, mal_id=mal_id, anilist_id=anilist_id, simkl_id=simkl_id)

        title_lang = user.get("title_language", "english") if user else "english"
        effective_provs = get_effective_meta_providers(user)
        metadata_language = effective_provs.get("language", "eng")
        synopsis_prov = effective_provs.get("synopsis", "kitsu")
        episodes_prov = effective_provs.get("episodes", "anizp")
        poster_prov = effective_provs.get("poster", "anilist")
        backdrop_prov = effective_provs.get("backdrop", "cinemeta")

        ext_kind = "movies" if is_movie and imdb_id else ("tv" if tvdb_id else ("movies" if imdb_id else None))
        ext_id = imdb_id if ext_kind == "movies" else tvdb_id

        # Movies only resolve through IMDb: AniZip's thetvdb_id on a movie is its parent series (e.g. Mugen Train)
        if is_movie:
            tvdb_entity_kind, tvdb_entity_id = ("movies", imdb_id) if imdb_id else (None, None)
        else:
            tvdb_entity_kind, tvdb_entity_id = ext_kind, ext_id

        # TVDB translations are show-level: only apply them to the entry that starts the show, never to sequels/split cours
        need_tvdb_translation = (
            (metadata_language != "eng" or synopsis_prov == "tvdb")
            and bool(tvdb_entity_id)
            and is_root_tvdb_entry(subtype, (anizp_data or {}).get("episodes") or {}, k_attrs)
        )
        need_tvdb_posters = (poster_prov == "tvdb") and bool(tvdb_entity_id)
        need_tvdb_episodes = (not is_movie) and (metadata_language != "eng" or episodes_prov == "tvdb") and bool(tvdb_id)

        async def _noop(default):
            return default

        if imdb_id:
            cinemeta_coro = asyncio.wait_for(fetch_cinemeta_metadata(imdb_id, media_type, is_releasing=is_releasing), timeout=_budget(3.0))
        else:
            cinemeta_coro = _noop({})

        if backdrop_prov == "fanarttv" and ext_kind and ext_id:
            from app.services.fanart import fetch_fanart

            client_key = user.get("fanart_personal_key") if user else None
            fanart_coro = asyncio.wait_for(fetch_fanart(ext_kind, ext_id, client_key=client_key), timeout=_budget(2.5))
        else:
            fanart_coro = _noop({})

        if backdrop_prov == "tvdb" and ext_kind and ext_id:
            from app.services.tvdb import fetch_tvdb_artworks

            tvdb_art_coro = asyncio.wait_for(fetch_tvdb_artworks(ext_kind, ext_id), timeout=_budget(2.5))
        else:
            tvdb_art_coro = _noop([])

        if need_tvdb_translation:
            from app.services.tvdb import fetch_tvdb_translation

            tvdb_trans_coro = asyncio.wait_for(
                fetch_tvdb_translation(tvdb_entity_kind, tvdb_entity_id, metadata_language, is_releasing=is_releasing),
                timeout=_budget(2.5),
            )
        else:
            tvdb_trans_coro = _noop({})

        if need_tvdb_posters:
            from app.services.tvdb import fetch_tvdb_posters

            tvdb_posters_coro = asyncio.wait_for(
                fetch_tvdb_posters(tvdb_entity_kind, tvdb_entity_id, is_releasing=is_releasing),
                timeout=_budget(2.5),
            )
        else:
            tvdb_posters_coro = _noop({})

        if need_tvdb_episodes:
            from app.services.tvdb import fetch_tvdb_episodes

            tvdb_eps_coro = asyncio.wait_for(
                fetch_tvdb_episodes(tvdb_id, metadata_language, is_releasing=is_releasing),
                timeout=_budget(3.0),
            )
        else:
            tvdb_eps_coro = _noop([])

        overrides_coro = asyncio.wait_for(
            fetch_provider_overrides(user, mal_id, anilist_id, show_airing=show_airing),
            timeout=_budget(3.0),
        )

        (
            cinemeta_res,
            fanart_res,
            tvdb_art_res,
            overrides_res,
            tvdb_trans_res,
            tvdb_posters_res,
            tvdb_eps_res,
        ) = await asyncio.gather(
            cinemeta_coro,
            fanart_coro,
            tvdb_art_coro,
            overrides_coro,
            tvdb_trans_coro,
            tvdb_posters_coro,
            tvdb_eps_coro,
            return_exceptions=True,
        )
        if any(
            isinstance(r, TimeoutError)
            for r in (cinemeta_res, fanart_res, tvdb_art_res, overrides_res, tvdb_trans_res, tvdb_posters_res, tvdb_eps_res)
        ):
            degraded = True

        if isinstance(cinemeta_res, Exception):
            logging.warning("Failed to fetch Cinemeta metadata in handle_meta: %s", cinemeta_res)
            cinemeta_data = {}
        else:
            cinemeta_data = cinemeta_res or {}

        if isinstance(fanart_res, Exception):
            logging.warning("Failed to fetch Fanart.tv metadata in handle_meta: %s", fanart_res)
            fanart_data = {}
        else:
            fanart_data = fanart_res or {}

        if isinstance(tvdb_art_res, Exception):
            logging.warning("Failed to fetch TheTVDB metadata in handle_meta: %s", tvdb_art_res)
            tvdb_data = []
        else:
            tvdb_data = tvdb_art_res or []

        if isinstance(overrides_res, Exception):
            logging.warning("Failed to fetch provider overrides in handle_meta: %s", overrides_res)
            mal_data_override, al_data_override = None, None
        else:
            mal_data_override, al_data_override = overrides_res

        if isinstance(tvdb_trans_res, Exception):
            logging.warning("Failed to fetch TheTVDB translation in handle_meta: %s", tvdb_trans_res)
            tvdb_translation = {}
        else:
            tvdb_translation = tvdb_trans_res if isinstance(tvdb_trans_res, dict) else {}

        if tvdb_translation and not is_root_tvdb_entry(
            subtype,
            (anizp_data or {}).get("episodes") or {},
            k_attrs,
            cinemeta_data=cinemeta_data,
        ):
            tvdb_translation = {}

        if isinstance(tvdb_posters_res, Exception):
            logging.warning("Failed to fetch TheTVDB posters in handle_meta: %s", tvdb_posters_res)
            tvdb_posters = {}
        else:
            tvdb_posters = tvdb_posters_res if isinstance(tvdb_posters_res, dict) else {}

        if isinstance(tvdb_eps_res, Exception):
            logging.warning("Failed to fetch TheTVDB episodes in handle_meta: %s", tvdb_eps_res)
            tvdb_episodes = []
        else:
            tvdb_episodes = tvdb_eps_res if isinstance(tvdb_eps_res, list) else []

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
            fanart_data=fanart_data,
            tvdb_data=tvdb_data,
            show_watched_tags=show_watched,
            watched_progress=watched_progress,
            canonical_total_episodes=canonical_total_episodes,
            title_language=title_lang,
            episodes_provider=episodes_prov,
            backdrop_provider=backdrop_prov,
            poster_provider=poster_prov,
            metadata_language=metadata_language,
            tvdb_translation=tvdb_translation,
            tvdb_posters=tvdb_posters,
            tvdb_episodes=tvdb_episodes,
        )

        # Apply user preferred Metadata Provider override (MAL / AniList with Kitsu fallback)
        preserve_localized_synopsis = bool(
            metadata_language != "eng" and tvdb_translation and (tvdb_translation.get("overview") or "").strip()
        )
        mal_data_override, al_data_override = apply_provider_overrides(
            meta,
            user,
            mal_data_override,
            al_data_override,
            preserve_localized_synopsis=preserve_localized_synopsis,
        )

        # Sync single-video movie entry with final localized/provider title & overview
        if meta.get("type") == "movie" and meta.get("videos"):
            meta["videos"][0]["title"] = meta.get("name", meta["videos"][0].get("title"))
            meta["videos"][0]["overview"] = meta.get("description", meta["videos"][0].get("overview"))

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

        if show_tracking:
            user_status_hdr = await build_user_status_header(user, user_id, mal_id=mal_id, anilist_id=anilist_id, simkl_id=simkl_id)
            if user_status_hdr:
                dynamic_headers.append(user_status_hdr)

        if show_airing:
            airing_prov = effective_provs.get("airing", "anilist")
            al_data = al_data_override if (airing_prov == "anilist" and al_data_override) else None
            mal_data_airing = mal_data_override if (airing_prov == "mal" and mal_data_override) else None

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

        has_personalized = bool(notice or dynamic_headers or (show_watched and watched_progress > 0))
        if has_personalized:
            cache_max_age, cache_stale = 60, 120
        elif degraded:
            cache_max_age, cache_stale = 300, 600
        else:
            cache_max_age, cache_stale = 86400, 604800
        return await respond_with({"meta": meta}, max_age=cache_max_age, stale_while_revalidate=cache_stale)
    except Exception as e:
        logging.error("Failed to handle meta for %s: %s", meta_id, e)
        return await respond_with({"meta": {}})


__all__ = [
    "meta_bp",
    "handle_root_meta",
    "handle_meta",
    "clean_imdb_id",
    "resolve_external_ids",
    "fetch_anizp_metadata",
    "fetch_cinemeta_metadata",
    "fetch_kitsu_meta",
    "fetch_provider_overrides",
    "apply_provider_overrides",
    "apply_metadata_provider_override",
    "build_user_status_header",
    "build_next_airing_header",
    "build_filler_arc_header",
    "build_expired_trackers_notice",
    "map_kitsu_to_stremio",
]
