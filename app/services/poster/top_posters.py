import logging

from app.services.http import get_client


async def validate_top_poster_api_key(api_key: str) -> bool:
    """
    Validate the TOP Posters API key by querying the /isValid endpoint.
    """
    if not api_key:
        return False
    url = f"https://top-posters.com/{api_key}/isValid"
    try:
        client = get_client()
        resp = await client.get(url, timeout=8)
        if resp.status_code in [200, 302, 307]:
            return True
        url_alt = f"https://top-posters.com/{api_key}/imdb/poster-default/tt0111161.jpg"
        resp_alt = await client.get(url_alt, timeout=8, follow_redirects=True)
        return resp_alt.status_code in [200, 301, 302, 307, 308]
    except Exception as e:
        logging.error("Failed to validate TOP Posters API key: %s", e)
        return False


def build_top_poster_url(
    top_key: str,
    media_type: str,
    shape: str = "poster",
    imdb_id: str | None = None,
    tmdb_id: str | None = None,
    tvdb_id: str | None = None,
) -> str | None:
    """
    Construct the TOP Posters poster or backdrop URL.
    """
    if not top_key:
        return None

    id_type = None
    media_id = None

    if imdb_id:
        id_type = "imdb"
        media_id = imdb_id
    elif tmdb_id:
        id_type = "tmdb"
        prefix = "movie" if media_type == "movie" else "series"
        media_id = f"{prefix}-{tmdb_id}"
    elif tvdb_id:
        id_type = "tvdb"
        prefix = "movie" if media_type == "movie" else "series"
        media_id = f"{prefix}-{tvdb_id}"

    if not id_type or not media_id:
        return None

    endpoint = "backdrop-default" if shape == "landscape" else "poster-default"
    return f"https://top-posters.com/{top_key}/{id_type}/{endpoint}/{media_id}.jpg"
