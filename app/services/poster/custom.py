import logging


def build_custom_poster_url(
    custom_pattern: str,
    shape: str = "poster",
    fallback_poster: str | None = None,
    imdb_id: str | None = None,
    tmdb_id: str | None = None,
    tvdb_id: str | None = None,
    mal_id: str | None = None,
    kitsu_id: str | None = None,
    anilist_id: str | None = None,
    rpdb_key: str | None = None,
    top_key: str | None = None,
) -> str | None:
    """
    Construct poster URL from a custom URL pattern with placeholder substitutions.
    Handles landscape fallbacks for providers like Btttr.cc or patterns missing landscape placeholders.
    """
    if not custom_pattern:
        return fallback_poster

    try:
        is_landscape = shape == "landscape"
        is_btttr = "btttr.cc" in custom_pattern
        has_landscape_placeholder = any(p in custom_pattern for p in ("{shape}", "{endpoint}", "backdrop"))

        # Custom providers (like btttr.cc) or patterns without explicit landscape placeholders
        # fall back to original cover/backdrop in landscape mode to prevent distorted cropping.
        if is_landscape and (is_btttr or not has_landscape_placeholder):
            return fallback_poster

        # Substitute placeholders
        url = custom_pattern
        replacements = {
            "{shape}": "landscape" if is_landscape else "poster",
            "{endpoint}": "backdrop-default" if is_landscape else "poster-default",
            "{imdb_id}": imdb_id or "",
            "{mal_id}": str(mal_id) if mal_id else "",
            "{kitsu_id}": str(kitsu_id) if kitsu_id else "",
            "{anilist_id}": str(anilist_id) if anilist_id else "",
            "{tmdb_id}": str(tmdb_id) if tmdb_id else "",
            "{tvdb_id}": str(tvdb_id) if tvdb_id else "",
            "{rpdb_key}": rpdb_key or "",
            "{top_key}": top_key or "",
        }

        # If pattern requires a placeholder that is empty, fallback
        for placeholder, val in replacements.items():
            if placeholder in url:
                if not val:
                    return fallback_poster
                url = url.replace(placeholder, val)

        return url
    except Exception as e:
        logging.error("Failed to evaluate custom poster pattern: %s", e)
        return fallback_poster
