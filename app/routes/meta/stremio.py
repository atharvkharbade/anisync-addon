import asyncio
import re
from datetime import UTC, date, datetime

from app.lib.meta_providers import pick_backdrop

from .fetchers import clean_imdb_id

# Roman numerals used as sequel markers. Single letters (i, v, x) are omitted on purpose:
# they collide with ordinary title words such as "SPY x FAMILY" or "Hunter x Hunter".
_ROMAN_SEASONS = {"ii": 2, "iii": 3, "iv": 4, "vi": 6, "vii": 7, "viii": 8, "ix": 9}

# Max distance between the anime premiere and a Cinemeta season's Episode 1 air date
# (absorbs JP vs US release-day differences and simulcast delays).
_SEASON_PREMIERE_TOLERANCE_DAYS = 60


def _parse_date(value) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip()[:10].replace("/", "-"), "%Y-%m-%d").date()
    except ValueError:
        return None


def _title_season_candidates(titles: list[str]) -> list[int]:
    """Season numbers suggested by titles. "Part N" / "Cour N" are split cours of one season, not seasons."""
    found = []
    for t in titles:
        if not t:
            continue
        for pattern in (
            r"\bseason\s*(\d+)\b",
            r"\b(\d+)(?:st|nd|rd|th)\s+season\b",
            r"(?:\s|^|\()(\d+)\s*(?:\)|$)",  # trailing number, e.g. "Bungou Stray Dogs 5"
        ):
            m = re.search(pattern, t, re.IGNORECASE)
            if m and 1 <= int(m.group(1)) < 50:  # exclude release years like (2011)
                found.append(int(m.group(1)))
        for word in re.sub(r"[^\w\s]", " ", t.lower()).split():
            if word in _ROMAN_SEASONS:
                found.append(_ROMAN_SEASONS[word])
    return list(dict.fromkeys(found))


def detect_anime_season(
    canonical_title: str,
    titles_dict: dict | None,
    anizp_episodes: dict | None,
    cinemeta_videos: list | None,
    start_date_str: str | None,
) -> int | None:
    """
    Deterministically detects which season number an anime corresponds to in multi-season
    databases (Cinemeta / TheTVDB / IMDb).

    1. Checks AniZip episode metadata for any non-null seasonNumber.
    2. When Cinemeta has per-season Episode 1 air dates, a season is only accepted if its
       premiere lies within _SEASON_PREMIERE_TOLERANCE_DAYS of the anime's start date:
       first among the season numbers suggested by the titles, then the nearest premiere.
       Titles alone are not trusted here ("Kaiju No. 8" is not season 8, and "Part 2"
       is usually the second cour of a season).
    3. Without usable Cinemeta dates, falls back to the first title-suggested season.
    """
    # 1. From AniZip episode metadata
    if anizp_episodes and isinstance(anizp_episodes, dict):
        for ep in anizp_episodes.values():
            if isinstance(ep, dict) and ep.get("seasonNumber") is not None:
                try:
                    s_val = int(ep["seasonNumber"])
                    if s_val > 0:
                        return s_val
                except (ValueError, TypeError):
                    pass

    all_titles = [canonical_title]
    if titles_dict and isinstance(titles_dict, dict):
        all_titles.extend(str(v) for v in titles_dict.values() if v)
    title_seasons = _title_season_candidates(all_titles)

    # Episode 1 air date of each Cinemeta season
    season_starts = {}
    for v in cinemeta_videos or []:
        if not isinstance(v, dict) or v.get("episode") != 1:
            continue
        try:
            s_int = int(v.get("season"))
        except (ValueError, TypeError):
            continue
        rel = _parse_date(v.get("released"))
        if s_int > 0 and rel:
            season_starts[s_int] = rel

    start = _parse_date(start_date_str)
    if season_starts and start:
        def gap(s_int: int) -> int:
            return abs((season_starts[s_int] - start).days)

        # 2a. Title-suggested season confirmed by its premiere date
        for s_int in title_seasons:
            if s_int in season_starts and gap(s_int) <= _SEASON_PREMIERE_TOLERANCE_DAYS:
                return s_int
        # 2b. Nearest season premiere
        nearest = min(season_starts, key=gap)
        if gap(nearest) <= _SEASON_PREMIERE_TOLERANCE_DAYS:
            return nearest
        return None

    # 3. No dates to verify against
    return title_seasons[0] if title_seasons else None


def map_kitsu_to_stremio(
    kitsu_data: dict,
    meta_id: str,
    anizp_data: dict = None,
    mal_id: str = None,
    show_filler_tags: bool = False,
    loop=None,
    cinemeta_data: dict = None,
    show_watched_tags: bool = False,
    watched_progress: int = 0,
    canonical_total_episodes: int | None = None,
    title_language: str = "english",
    episodes_provider: str = "anizp",
    backdrop_provider: str = "cinemeta",
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
    tvdb_fanart = None
    anizp_poster = None
    anizp_logo = None
    for img in anizp_images:
        if img.get("coverType") == "Fanart" and not tvdb_fanart:
            tvdb_fanart = img.get("url")
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

    cin_bg = cinemeta_data.get("background") if cinemeta_data else None
    chosen_bg = pick_backdrop(
        backdrop_provider,
        sources={
            "cinemeta": cin_bg,
            "tvdb": tvdb_fanart,
            "kitsu": kitsu_cover,
        },
        disallow=poster,
    )
    background = chosen_bg or poster

    imdb_id = clean_imdb_id(anizp_data.get("mappings", {}).get("imdb_id") if anizp_data else None)

    logo = anizp_logo
    if not logo and cinemeta_data:
        logo = cinemeta_data.get("logo")
    if not logo and imdb_id:
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
    anime_status = (attributes.get("status") or "").lower()

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
            ep_nums = []
            for x in episodes_data:
                raw_n = x.get("attributes", {}).get("number")
                if raw_n is not None:
                    try:
                        ep_nums.append(int(raw_n))
                    except (ValueError, TypeError):
                        pass
            if ep_nums:
                max_kitsu_ep = max(ep_nums)

        max_anizp_ep = 0
        if anizp_episodes:
            try:
                max_anizp_ep = max([int(k) for k in anizp_episodes.keys() if k.isdigit()])
            except ValueError:
                pass

        total_ep = max(kitsu_ep_count, max_kitsu_ep, max_anizp_ep)
        if total_ep == 0:
            total_ep = canonical_total_episodes if (canonical_total_episodes and canonical_total_episodes > 0) else 12
        elif canonical_total_episodes and canonical_total_episodes > 0:
            total_ep = min(total_ep, canonical_total_episodes)

        # Create a mapping of episode number to Kitsu episode data for O(1) lookup
        kitsu_ep_map = {}
        for ep in episodes_data:
            num = ep.get("attributes", {}).get("number")
            if num:
                try:
                    kitsu_ep_map[int(num)] = ep
                except (ValueError, TypeError):
                    pass

        # Build Cinemeta episode thumbnail and release date maps: (season, episode) -> value
        cinemeta_ep_thumbs = {}
        cinemeta_ep_released = {}
        cin_seasons = set()
        cin_videos = cinemeta_data.get("videos") if cinemeta_data and isinstance(cinemeta_data.get("videos"), list) else []
        for v in cin_videos:
            s = v.get("season")
            e = v.get("episode")
            thumb = v.get("thumbnail")
            rel = v.get("released")
            if s is not None and e is not None:
                try:
                    s_int = int(s)
                    e_int = int(e)
                    cin_seasons.add(s_int)
                    if thumb:
                        cinemeta_ep_thumbs[(s_int, e_int)] = thumb
                    if rel:
                        cinemeta_ep_released[(s_int, e_int)] = rel
                except (ValueError, TypeError):
                    pass

        detected_season = detect_anime_season(
            attributes.get("canonicalTitle") or "",
            attributes.get("titles") or {},
            anizp_episodes,
            cin_videos,
            start_date,
        )

        canonical_title = (attributes.get("canonicalTitle") or "").lower()
        cin_s1_start = cinemeta_ep_released.get((1, 1))
        cin_s1_year = int(cin_s1_start[:4]) if cin_s1_start and cin_s1_start[:4].isdigit() else None
        anime_start_year = int(start_date[:4]) if start_date and len(start_date) >= 4 and start_date[:4].isdigit() else None

        # Determine if this anime is a sequel or spinoff to prevent wrongly defaulting to Season 1
        is_likely_sequel = False
        if detected_season is not None and detected_season > 1:
            is_likely_sequel = True
        elif any(
            re.search(rf"\b{re.escape(marker)}\b", canonical_title)
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
                "arc",
                "hen",
                "act 2",
                "kessen",
                "sensou",
            ]
        ):
            is_likely_sequel = True
        elif anime_start_year and cin_s1_year and (anime_start_year - cin_s1_year >= 2):
            is_likely_sequel = True

        last_valid_ep_date = None
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

            # Determine season for this specific episode (falling back to show-level detected_season)
            ep_season = None
            if anizp_ep.get("seasonNumber") is not None:
                try:
                    s_candidate = int(anizp_ep["seasonNumber"])
                    if s_candidate > 0:
                        ep_season = s_candidate
                except (ValueError, TypeError):
                    pass
            if ep_season is None:
                ep_season = detected_season

            # Match Cinemeta episode thumbnail and release date safely
            cinemeta_thumb = None
            cin_released = None
            if cinemeta_ep_thumbs or cinemeta_ep_released:
                if ep_season is not None:
                    cinemeta_thumb = cinemeta_ep_thumbs.get((ep_season, ep_num))
                    cin_released = cinemeta_ep_released.get((ep_season, ep_num))
                elif not is_likely_sequel and cin_seasons == {1}:
                    # Only fall back to Season 1 if Cinemeta has ONLY 1 season and start years align
                    if anime_start_year is None or (cin_s1_year and abs(anime_start_year - cin_s1_year) <= 1):
                        cinemeta_thumb = cinemeta_ep_thumbs.get((1, ep_num))
                        cin_released = cinemeta_ep_released.get((1, ep_num))

            candidate_released = attrs.get("airdate") or anizp_ep.get("airdate") or cin_released

            # Validate episode release date against anime start date and chronological ordering
            released = None
            if candidate_released:
                try:
                    date_str = str(candidate_released).strip()[:10].replace("/", "-")
                    ep_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                    is_date_valid = True

                    # 1. Episode air date cannot precede the anime premiere year
                    if anime_start_year and ep_date.year < anime_start_year:
                        is_date_valid = False

                    # 2. Monotonicity: episode N cannot air prior to episode N-1
                    if last_valid_ep_date and ep_date < last_valid_ep_date:
                        is_date_valid = False

                    # 3. Unreleased anime status check: cannot have episodes in the past
                    if anime_status in ["unreleased", "not_yet_released"]:
                        now_date = datetime.now(UTC).date()
                        if ep_date < now_date:
                            is_date_valid = False

                    if is_date_valid:
                        # Keep the original value: Cinemeta timestamps carry the actual air time
                        released = str(candidate_released).strip()
                        last_valid_ep_date = ep_date
                except (ValueError, TypeError):
                    pass

            # If the anime or episode is unreleased (air date in future), Cinemeta's Metahub
            # thumbnail link is pre-computed and guaranteed to 404 on Metahub/TMDB.
            # Suppress cinemeta_thumb so it falls back to backdrop (background) or real preview stills.
            if cinemeta_thumb:
                if anime_status in ["unreleased", "not_yet_released"]:
                    cinemeta_thumb = None
                elif released:
                    try:
                        date_str = str(released).strip()[:10].replace("/", "-")
                        ep_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                        now_date = datetime.now(UTC).date()
                        if ep_date > now_date:
                            cinemeta_thumb = None
                    except (ValueError, TypeError):
                        pass
                elif not released and is_likely_sequel:
                    cinemeta_thumb = None

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
