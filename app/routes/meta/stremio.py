import asyncio
from .fetchers import clean_imdb_id


def map_kitsu_to_stremio(
    kitsu_data: dict,
    meta_id: str,
    anizp_data: dict = None,
    mal_id: str = None,
    show_filler_tags: bool = True,
    loop=None,
    cinemeta_data: dict = None,
    show_watched_tags: bool = False,
    watched_progress: int = 0,
    title_language: str = "english",
    episodes_provider: str = "anizp",
    backdrop_provider: str = "fanart",
    poster_provider: str = "anilist",
) -> dict:
    data = kitsu_data.get("data", {})
    if not data:
        return {}

    video_base = f"kitsu:{data.get('id')}"

    attributes = data.get("attributes", {})
    titles = attributes.get("titles", {})

    if title_language == "english":
        title = titles.get("en") or titles.get("en_us") or attributes.get("canonicalTitle") or titles.get("en_jp") or "Unknown Title"
    elif title_language == "romaji":
        title = titles.get("en_jp") or attributes.get("canonicalTitle") or titles.get("en") or "Unknown Title"
    elif title_language == "japanese":
        title = titles.get("ja_jp") or titles.get("en_jp") or attributes.get("canonicalTitle") or "Unknown Title"
    else:  # default / canonical
        title = attributes.get("canonicalTitle") or titles.get("en") or titles.get("en_jp") or "Unknown Title"
    synopsis = attributes.get("synopsis", "")
    anizp_images = anizp_data.get("images", []) if anizp_data else []
    anizp_fanart = None
    anizp_poster = None
    anizp_logo = None
    for img in anizp_images:
        if img.get("coverType") == "Fanart" and not anizp_fanart:
            anizp_fanart = img.get("url")
        elif img.get("coverType") == "Poster" and not anizp_poster:
            anizp_poster = img.get("url")
        elif img.get("coverType") in ["Clearlogo", "Logo"] and not anizp_logo:
            img_url = img.get("url") or ""
            # Filter out TheTVDB square icons/avatars (which AniZip mistakenly tags as Clearlogo)
            if "/icons/" not in img_url and "/icon/" not in img_url:
                anizp_logo = img_url

    poster_data = attributes.get("posterImage") or {}
    kitsu_poster = poster_data.get("original") or poster_data.get("large") or poster_data.get("medium") or ""
    if poster_provider == "kitsu":
        poster = kitsu_poster or anizp_poster or ""
    else:
        poster = anizp_poster or kitsu_poster or ""
    cover_data = attributes.get("coverImage") or {}
    kitsu_cover = cover_data.get("original") or cover_data.get("large") or cover_data.get("medium")

    if backdrop_provider == "kitsu":
        background = kitsu_cover or anizp_fanart or (cinemeta_data.get("background") if cinemeta_data else "") or poster
    else:  # fanart (default)
        background = (
            (cinemeta_data.get("background") if cinemeta_data else None)
            or anizp_fanart
            or kitsu_cover
            or poster
        )

    imdb_id = clean_imdb_id(anizp_data.get("mappings", {}).get("imdb_id") if anizp_data else None)

    logo = anizp_logo
    if not logo and cinemeta_data:
        logo = cinemeta_data.get("logo")
    if not logo and not cinemeta_data and imdb_id:
        logo = f"https://images.metahub.space/logo/medium/{imdb_id}/img"

    average_rating = attributes.get("averageRating")
    rating = str(round(float(average_rating) / 10.0, 1)) if average_rating else None

    # Release info (Year)
    start_date = attributes.get("startDate")
    end_date = attributes.get("endDate")
    release_info = ""
    if start_date:
        release_info = start_date[:4]
        if end_date:
            release_info += f"-{end_date[:4]}"
        else:
            release_info += "-"

    # Media Type
    subtype = (attributes.get("subtype") or "tv").lower()
    media_type = "movie" if subtype == "movie" else "series"

    # Videos / Episodes List
    videos = []
    included = kitsu_data.get("included") or []

    # Filter and sort episodes by number
    episodes_data = []
    for item in included:
        if item.get("type") == "episodes":
            episodes_data.append(item)

    anizp_episodes = anizp_data.get("episodes", {}) if anizp_data else {}

    if subtype == "movie":
        videos.append(
            {
                "id": video_base,
                "title": title,
                "episode": 1,
                "season": 1,
                "released": start_date + "T00:00:00Z" if start_date else None,
                "overview": synopsis,
                "thumbnail": background or poster,
            }
        )
    else:
        # Determine the maximum episode number we should display to prevent truncation
        kitsu_ep_count = attributes.get("episodeCount") or 0

        max_kitsu_ep = 0
        if episodes_data:
            max_kitsu_ep = max([x.get("attributes", {}).get("number") or 0 for x in episodes_data])

        max_anizp_ep = 0
        if anizp_episodes:
            try:
                max_anizp_ep = max([int(k) for k in anizp_episodes.keys() if k.isdigit()])
            except ValueError:
                pass

        total_ep = max(kitsu_ep_count, max_kitsu_ep, max_anizp_ep)
        if total_ep == 0:
            total_ep = 12  # Default fallback

        # Create a mapping of episode number to Kitsu episode data for O(1) lookup
        kitsu_ep_map = {}
        for ep in episodes_data:
            num = ep.get("attributes", {}).get("number")
            if num:
                try:
                    kitsu_ep_map[int(num)] = ep
                except (ValueError, TypeError):
                    pass

        # Build Cinemeta episode thumbnail map: (season, episode) -> thumbnail
        cinemeta_ep_thumbs = {}
        if cinemeta_data and isinstance(cinemeta_data.get("videos"), list):
            for v in cinemeta_data["videos"]:
                s = v.get("season")
                e = v.get("episode")
                thumb = v.get("thumbnail")
                if s is not None and e is not None and thumb:
                    try:
                        cinemeta_ep_thumbs[(int(s), int(e))] = thumb
                    except (ValueError, TypeError):
                        pass

        # Detect if this anime is likely a sequel to avoid assuming Season 1 when seasonNumber is missing
        canonical_title = (attributes.get("canonicalTitle") or "").lower()
        is_likely_sequel = any(
            marker in canonical_title
            for marker in [
                "season 2",
                "season 3",
                "season 4",
                "season 5",
                "2nd season",
                "3rd season",
                "4th season",
                "part 2",
                "part 3",
                "part 4",
                "cour 2",
            ]
        )

        for i in range(1, total_ep + 1):
            ep_num = i
            kitsu_ep = kitsu_ep_map.get(i)
            anizp_ep = anizp_episodes.get(str(i)) or {}

            # Extract attributes from Kitsu episode if available
            attrs = kitsu_ep.get("attributes", {}) if kitsu_ep else {}

            if title_language == "english":
                ep_title = (
                    anizp_ep.get("title", {}).get("en")
                    or attrs.get("canonicalTitle")
                    or anizp_ep.get("title", {}).get("x-jat")
                    or f"Episode {ep_num}"
                )
            elif title_language == "romaji":
                ep_title = (
                    anizp_ep.get("title", {}).get("x-jat")
                    or attrs.get("canonicalTitle")
                    or anizp_ep.get("title", {}).get("en")
                    or f"Episode {ep_num}"
                )
            elif title_language == "japanese":
                ep_title = (
                    anizp_ep.get("title", {}).get("ja")
                    or anizp_ep.get("title", {}).get("x-jat")
                    or attrs.get("canonicalTitle")
                    or f"Episode {ep_num}"
                )
            else:  # default / canonical
                ep_title = (
                    attrs.get("canonicalTitle")
                    or anizp_ep.get("title", {}).get("en")
                    or anizp_ep.get("title", {}).get("x-jat")
                    or f"Episode {ep_num}"
                )
            released = attrs.get("airdate") or anizp_ep.get("airdate")

            # Match Cinemeta episode thumbnail safely
            cinemeta_thumb = None
            if cinemeta_ep_thumbs:
                s_num = anizp_ep.get("seasonNumber")
                if s_num is not None:
                    try:
                        cinemeta_thumb = cinemeta_ep_thumbs.get((int(s_num), ep_num))
                    except (ValueError, TypeError):
                        pass
                elif not is_likely_sequel:
                    cinemeta_thumb = cinemeta_ep_thumbs.get((1, ep_num))

            if episodes_provider == "kitsu":
                overview = attrs.get("synopsis") or anizp_ep.get("overview") or anizp_ep.get("summary") or ""
                thumbnail = (
                    (attrs.get("thumbnail") or {}).get("original")
                    or (attrs.get("thumbnail") or {}).get("large")
                    or anizp_ep.get("image")
                    or cinemeta_thumb
                    or background
                )
            else:  # anizp, mal, or default
                overview = anizp_ep.get("overview") or anizp_ep.get("summary") or attrs.get("synopsis") or ""
                thumbnail = (
                    anizp_ep.get("image")
                    or (attrs.get("thumbnail") or {}).get("original")
                    or (attrs.get("thumbnail") or {}).get("large")
                    or cinemeta_thumb
                    or background
                )

            # Check filler status
            is_filler = False
            if mal_id and show_filler_tags:
                from app.services.db import get_jikan_filler_cache

                cached = get_jikan_filler_cache(mal_id, ep_num)
                if cached is not None:
                    is_filler = cached
                else:
                    from app.routes.catalog import background_fetch_and_cache_filler, currently_fetching_pairs

                    pair = (str(mal_id), ep_num)
                    if pair not in currently_fetching_pairs:
                        currently_fetching_pairs.add(pair)
                        if loop and loop.is_running():
                            try:
                                asyncio.run_coroutine_threadsafe(
                                    background_fetch_and_cache_filler(mal_id, ep_num), loop
                                )
                            except Exception:
                                pass

            if is_filler:
                ep_title = f"[Filler] {ep_title}"
            if show_watched_tags and ep_num <= watched_progress:
                ep_title = f"[Watched] {ep_title}"

            released_str = None
            if released and isinstance(released, str) and released.strip():
                released = released.strip()
                if "T" not in released:
                    released_str = released + "T00:00:00Z"
                else:
                    released_str = released

            videos.append(
                {
                    "id": f"{video_base}:{ep_num}",
                    "title": ep_title,
                    "episode": ep_num,
                    "season": 1,
                    "released": released_str,
                    "overview": overview,
                    "thumbnail": thumbnail,
                }
            )

    genres = ["Anime"]
    if cinemeta_data and cinemeta_data.get("genres"):
        for g in cinemeta_data["genres"]:
            if g not in genres:
                genres.append(g)

    links = []
    if cinemeta_data and "links" in cinemeta_data:
        for link in cinemeta_data["links"]:
            if link.get("category") == "Cast":
                links.append(link)

    meta_obj = {
        "id": meta_id,
        "name": title,
        "type": media_type,
        "poster": poster,
        "background": background,
        "imdbRating": rating,
        "releaseInfo": release_info,
        "description": synopsis,
        "videos": videos,
        "genres": genres,
        "links": links,
    }
    if logo:
        meta_obj["logo"] = logo

    return meta_obj
