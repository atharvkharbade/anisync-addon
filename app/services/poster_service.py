"""
Backward-compatibility facade for poster art services.
All poster functionality has been modularized under `app.services.poster`.
This module re-exports all public functions to prevent breaking existing imports.
"""

from app.services.poster import (
    background_resolve_external_ids,
    build_custom_poster_url,
    build_rpdb_poster_url,
    build_top_poster_url,
    check_rpdb_key_validity_background,
    get_poster_url,
    get_rpdb_poster_url,
    resolve_media_ids,
    validate_rpdb_api_key,
    validate_top_poster_api_key,
)

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
