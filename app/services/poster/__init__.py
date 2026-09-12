"""
Poster Art Subpackage for AniSync.
Provides modular poster generation across multiple providers (RPDB, TOP Posters, Custom/Btttr, Clean)
with automatic external ID resolution and caching.
"""

from app.services.poster.custom import build_custom_poster_url
from app.services.poster.id_resolver import (
    background_resolve_external_ids,
    resolve_media_ids,
    trigger_background_resolution,
)
from app.services.poster.rpdb import (
    build_rpdb_poster_url,
    check_rpdb_key_validity_background,
    validate_rpdb_api_key,
)
from app.services.poster.top_posters import (
    build_top_poster_url,
    validate_top_poster_api_key,
)


def get_poster_url(
    user: dict,
    media_type: str,
    kitsu_id: str | None = None,
    mal_id: str | None = None,
    anilist_id: str | None = None,
    simkl_id: str | None = None,
    fallback_poster: str | None = None,
    provider_override: str | None = None,
    resolved_ids: dict | None = None,
    shape: str = "poster",
    imdb_id: str | None = None,
) -> str | None:
    """
    Resolve and construct the poster URL for an item based on user's poster_provider setting:
    - 'none' / 'clean': Returns fallback_poster (original cover)
    - 'btttr': Uses Btttr.cc rating poster endpoint
    - 'rpdb': Uses RPDB (Rating Poster DB) with API key
    - 'top_poster': Uses Top Poster API with API key
    - 'custom': Formats custom URL pattern using available IDs ({imdb_id}, {mal_id}, {kitsu_id}, etc.)
    """
    if not user:
        return fallback_poster

    # Determine provider (with per-catalog override support and backward compatibility)
    if provider_override:
        if provider_override in ["clean", "none"]:
            return fallback_poster
        elif provider_override in ["topposters", "top_poster"]:
            provider = "top_poster"
        elif provider_override in ["rpdb", "btttr", "custom"]:
            provider = provider_override
        else:
            provider = user.get("poster_provider")
    else:
        provider = user.get("poster_provider")
        if not provider:
            provider = "rpdb" if user.get("rpdb_api_key") else "none"

    if provider in ["topposters", "top_poster"]:
        provider = "top_poster"

    if provider in ["none", "clean"] and resolved_ids is None:
        return fallback_poster

    # Provider specific key validation
    rpdb_key = user.get("rpdb_api_key")
    top_key = user.get("top_poster_key")
    custom_pattern = user.get("custom_poster_pattern", "").strip()

    if provider == "rpdb" and rpdb_key:
        from datetime import datetime, timedelta

        last_checked = user.get("rpdb_key_last_checked")
        if (not last_checked or (datetime.utcnow() - last_checked) > timedelta(days=1)) and user.get("uid"):
            check_rpdb_key_validity_background(user["uid"], rpdb_key)

    imdb_id, tmdb_id, tvdb_id = resolve_media_ids(
        kitsu_id=kitsu_id,
        mal_id=mal_id,
        anilist_id=anilist_id,
        simkl_id=simkl_id,
        resolved_ids=resolved_ids,
        imdb_id=imdb_id,
    )

    # If provider is none or clean, return fallback_poster
    if provider in ["none", "clean"]:
        return fallback_poster

    # Provider specific key validation
    if provider == "rpdb" and (not rpdb_key or user.get("rpdb_key_valid") is False):
        return fallback_poster
    if provider == "top_poster" and (not top_key or user.get("top_key_valid") is False):
        return fallback_poster
    if provider == "custom" and not custom_pattern:
        return fallback_poster

    # Provider specific URL generation
    if provider == "custom":
        # Check if custom pattern requires Western/external IDs
        needs_external_id = any(p in custom_pattern for p in ("{imdb_id}", "{tmdb_id}", "{tvdb_id}"))
        if needs_external_id and not (imdb_id or tmdb_id or tvdb_id):
            trigger_background_resolution(kitsu_id=kitsu_id, mal_id=mal_id, anilist_id=anilist_id)
            return fallback_poster

        return build_custom_poster_url(
            custom_pattern=custom_pattern,
            shape=shape,
            fallback_poster=fallback_poster,
            imdb_id=imdb_id,
            tmdb_id=tmdb_id,
            tvdb_id=tvdb_id,
            mal_id=mal_id,
            kitsu_id=kitsu_id,
            anilist_id=anilist_id,
            simkl_id=simkl_id,
            rpdb_key=rpdb_key,
            top_key=top_key,
        )

    # Trigger background mappings resolution if we still lack external IDs (for RPDB / TopPosters)
    if not (imdb_id or tmdb_id or tvdb_id):
        trigger_background_resolution(kitsu_id=kitsu_id, mal_id=mal_id, anilist_id=anilist_id)
        return fallback_poster

    if provider == "top_poster":
        res = build_top_poster_url(
            top_key=top_key,
            media_type=media_type,
            shape=shape,
            imdb_id=imdb_id,
            tmdb_id=tmdb_id,
            tvdb_id=tvdb_id,
        )
        return res if res else fallback_poster

    # Default to RPDB
    res = build_rpdb_poster_url(
        rpdb_key=rpdb_key,
        media_type=media_type,
        shape=shape,
        rec_language=user.get("rec_language", "en"),
        imdb_id=imdb_id,
        tmdb_id=tmdb_id,
        tvdb_id=tvdb_id,
    )
    return res if res else fallback_poster


# Backward-compatible alias for existing callers
get_rpdb_poster_url = get_poster_url

__all__ = [
    "get_poster_url",
    "get_rpdb_poster_url",
    "validate_rpdb_api_key",
    "validate_top_poster_api_key",
    "check_rpdb_key_validity_background",
    "background_resolve_external_ids",
    "resolve_media_ids",
    "build_rpdb_poster_url",
    "build_top_poster_url",
    "build_custom_poster_url",
]
