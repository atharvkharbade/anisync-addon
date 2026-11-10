import asyncio
import re
from datetime import UTC, date, datetime

from app.lib.languages import get_anizp_language_key, normalize_metadata_language
from app.lib.meta_providers import pick_backdrop

from .fetchers import clean_imdb_id

# Roman numerals used as sequel markers. Single letters (i, v, x) are omitted on purpose:
# they collide with ordinary title words such as "SPY x FAMILY" or "Hunter x Hunter".
_ROMAN_SEASONS = {"ii": 2, "iii": 3, "iv": 4, "vi": 6, "vii": 7, "viii": 8, "ix": 9}

# Max distance between the anime premiere and a Cinemeta season's Episode 1 air date
# (absorbs JP vs US release-day differences and simulcast delays).
_SEASON_PREMIERE_TOLERANCE_DAYS = 60


_RE_YEAR = re.compile(r"^(\d{4})$")
_RE_YEAR_MONTH = re.compile(r"^(\d{4})[-/](\d{1,2})$")
_RE_YMD = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$")
_RE_ISO_MS_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z$")


def _normalize_iso_released(val) -> str | None:
    """Normalize a date/timestamp string into strict ISO-8601 UTC format for Stremio."""
    if not val or not isinstance(val, str):
        return None
    s = val.strip()
    if not s:
        return None
    if _RE_ISO_MS_Z.match(s):
        try:
            datetime.fromisoformat(s[:-1] + "+00:00")
            return s
        except ValueError:
            return None
    if m := _RE_YEAR.match(s):
        year = int(m.group(1))
        return f"{year:04d}-01-01T00:00:00Z" if 1 <= year <= 9999 else None
    if m := _RE_YEAR_MONTH.match(s):
        try:
            return datetime(int(m.group(1)), int(m.group(2)), 1).strftime("%Y-%m-%dT00:00:00Z")
        except ValueError:
            return None
    if m := _RE_YMD.match(s):
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime("%Y-%m-%dT00:00:00Z")
        except ValueError:
            return None
    normalized = re.sub(r"^(\d{4})/(\d{2})/(\d{2})(?=[T\s])", r"\1-\2-\3", s)
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


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


def _index_tvdb_episodes(
    tvdb_episodes: list[dict] | None,
) -> tuple[dict[int, dict], dict[tuple[int, int], dict], dict[int, dict]]:
    """Build O(1) TVDB episode lookups: by_id, by_(season, ep) (S>0 only), and by_abs (S>0 only)."""
    by_id: dict[int, dict] = {}
    by_se: dict[tuple[int, int], dict] = {}
    by_abs: dict[int, dict] = {}
    for tep in tvdb_episodes or []:
        if not isinstance(tep, dict):
            continue
        tid = tep.get("id")
        if tid is not None:
            try:
                by_id[int(tid)] = tep
            except (ValueError, TypeError):
                pass
        try:
            s_num = int(tep.get("seasonNumber") or 0)
        except (ValueError, TypeError):
            s_num = 0
        if s_num <= 0:
            continue
        try:
            e_num = int(tep.get("number") or 0)
        except (ValueError, TypeError):
            e_num = 0
        if e_num > 0:
            by_se.setdefault((s_num, e_num), tep)
        raw_abs = tep.get("absoluteNumber")
        if raw_abs is None:
            raw_abs = tep.get("absoluteEpisodeNumber")
        try:
            abs_num = int(raw_abs or 0)
        except (ValueError, TypeError):
            abs_num = 0
        if abs_num > 0:
            by_abs.setdefault(abs_num, tep)
    return by_id, by_se, by_abs


def _match_tvdb_episode(
    anizp_ep: dict,
    ep_num: int,
    ep_season: int | None,
    is_likely_sequel: bool,
    by_id: dict[int, dict],
    by_se: dict[tuple[int, int], dict],
    by_abs: dict[int, dict],
) -> dict | None:
    """Match a Kitsu/AniZip episode to its TVDB episode via tvdbId -> (season, ep) -> absoluteNumber."""
    if not by_id and not by_se and not by_abs:
        return None
    tvdb_ep_id = anizp_ep.get("tvdbId")
    if tvdb_ep_id is not None:
        try:
            matched = by_id.get(int(tvdb_ep_id))
            if matched is not None:
                return matched
        except (ValueError, TypeError):
            pass

    if ep_season is not None and ep_season > 0:
        anizp_ep_num = anizp_ep.get("episodeNumber")
        if anizp_ep_num is not None:
            try:
                anizp_ep_int = int(anizp_ep_num)
                if anizp_ep_int > 0:
                    matched = by_se.get((ep_season, anizp_ep_int))
                    if matched is not None:
                        return matched
            except (ValueError, TypeError):
                pass
        matched = by_se.get((ep_season, ep_num))
        if matched is not None:
            return matched

    abs_ep_num = anizp_ep.get("absoluteEpisodeNumber")
    if abs_ep_num is not None:
        try:
            abs_int = int(abs_ep_num)
            if abs_int > 0:
                matched = by_abs.get(abs_int)
                if matched is not None:
                    return matched
        except (ValueError, TypeError):
            pass
    elif not is_likely_sequel and ep_season in (None, 1):
        return by_abs.get(ep_num)

    return None


def _resolve_single_tvdb_season(
    subtype: str,
    anizp_episodes: dict,
    attributes: dict,
    cinemeta_data: dict | None,
) -> int | None:
    """Return a single TVDB season (> 0) for split-cour/single-season entries, or None for multi-season/movies."""
    if subtype == "movie":
        return None
    anizp_seasons: set[int] = set()
    if isinstance(anizp_episodes, dict):
        for ep in anizp_episodes.values():
            if isinstance(ep, dict) and ep.get("seasonNumber") is not None:
                try:
                    s_val = int(ep["seasonNumber"])
                    if s_val > 0:
                        anizp_seasons.add(s_val)
                except (ValueError, TypeError):
                    pass
    if len(anizp_seasons) == 1:
        return next(iter(anizp_seasons))
    if len(anizp_seasons) > 1:
        return None
    cin_videos = cinemeta_data.get("videos") if cinemeta_data and isinstance(cinemeta_data.get("videos"), list) else []
    detected = detect_anime_season(
        attributes.get("canonicalTitle") or "",
        attributes.get("titles") or {},
        anizp_episodes,
        cin_videos,
        attributes.get("startDate"),
    )
    return detected if (detected and detected > 0) else None


def is_root_tvdb_entry(
    subtype: str,
    anizp_episodes: dict | None,
    attributes: dict,
    cinemeta_data: dict | None = None,
) -> bool:
    """
    True when a Kitsu entry starts its TVDB series (S1E1), so TVDB's show-level title and synopsis describe it.
    Sequels and split cours (e.g. Bleach TYBW Part 2 = TVDB S17E14+, AoT S3) share one TVDB series, and its
    translation would rename them to the parent show ("Bleach") and show the parent premise as synopsis.
    """
    if (subtype or "").lower() == "movie":
        return True
    ep1 = anizp_episodes.get("1") if isinstance(anizp_episodes, dict) else None
    if isinstance(ep1, dict) and ep1.get("seasonNumber") is not None:
        try:
            season = int(ep1["seasonNumber"])
            ep_in_season = int(ep1.get("episodeNumber") or 1)
        except (ValueError, TypeError):
            season, ep_in_season = 1, 1
        return season == 1 and ep_in_season <= 1
    single = _resolve_single_tvdb_season(subtype, anizp_episodes or {}, attributes, cinemeta_data)
    return single in (None, 1)


def _resolve_localized_episode_text(
    norm_lang: str,
    anizp_code: str,
    base_ep_title: str,
    base_overview: str,
    anizp_ep: dict,
    tvdb_ep: dict | None,
    episodes_provider: str,
    title_language: str,
) -> tuple[str, str]:
    """Apply TVDB localized/provider episode title and overview with English fallbacks."""
    tvdb_name = (tvdb_ep.get("name") or "").strip() if tvdb_ep else ""
    tvdb_ov = (tvdb_ep.get("overview") or "").strip() if tvdb_ep else ""

    if norm_lang != "eng":
        anizp_titles = anizp_ep.get("title") if isinstance(anizp_ep.get("title"), dict) else {}
        anizp_en = (anizp_titles.get("en") or "").strip()
        anizp_loc = (anizp_titles.get(anizp_code) or "").strip() if anizp_code else ""
        tvdb_matches_en = bool(tvdb_name and anizp_en and tvdb_name.casefold() == anizp_en.casefold())

        if tvdb_name and not tvdb_matches_en:
            ep_title = tvdb_name
        elif anizp_loc:
            ep_title = anizp_loc
        elif tvdb_name:
            ep_title = tvdb_name
        else:
            ep_title = base_ep_title

        overview = tvdb_ov if tvdb_ov else base_overview
        return ep_title, overview

    if episodes_provider == "tvdb" and tvdb_ep:
        ep_title = tvdb_name if (tvdb_name and title_language == "english") else base_ep_title
        overview = tvdb_ov if tvdb_ov else base_overview
        return ep_title, overview

    return base_ep_title, base_overview


def map_kitsu_to_stremio(
    kitsu_data: dict,
    meta_id: str,
    anizp_data: dict = None,
    mal_id: str = None,
    show_filler_tags: bool = False,
    loop=None,
    cinemeta_data: dict = None,
    fanart_data: dict = None,
    tvdb_data: list[dict] | None = None,
    show_watched_tags: bool = False,
    watched_progress: int = 0,
    canonical_total_episodes: int | None = None,
    title_language: str = "english",
    episodes_provider: str = "anizp",
    backdrop_provider: str = "cinemeta",
    poster_provider: str = "anilist",
    metadata_language: str = "eng",
    tvdb_translation: dict | None = None,
    tvdb_posters: dict | None = None,
    tvdb_episodes: list[dict] | None = None,
) -> dict:
    data = kitsu_data.get("data", {})
    if not data:
        return {}

    norm_lang = normalize_metadata_language(metadata_language)
    anizp_code = get_anizp_language_key(norm_lang)

    video_base = f"kitsu:{data.get('id')}"

    attributes = data.get("attributes", {})
    titles = attributes.get("titles", {})
    subtype = (attributes.get("subtype") or "tv").lower()
    anizp_episodes = anizp_data.get("episodes", {}) if anizp_data else {}

    if title_language == "english":
        title = titles.get("en") or titles.get("en_us") or attributes.get("canonicalTitle") or titles.get("en_jp") or "Unknown Title"
    elif title_language == "romaji":
        title = titles.get("en_jp") or attributes.get("canonicalTitle") or titles.get("en") or "Unknown Title"
    elif title_language == "japanese":
        title = titles.get("ja_jp") or titles.get("en_jp") or attributes.get("canonicalTitle") or "Unknown Title"
    else:  # default / canonical
        title = attributes.get("canonicalTitle") or titles.get("en") or titles.get("en_jp") or "Unknown Title"
    synopsis = attributes.get("synopsis", "")

    if isinstance(tvdb_translation, dict) and tvdb_translation:
        tvdb_title = (tvdb_translation.get("name") or "").strip()
        tvdb_overview = (tvdb_translation.get("overview") or "").strip()
        if norm_lang != "eng" and tvdb_title:
            title = tvdb_title
        if tvdb_overview:
            synopsis = tvdb_overview

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
    tvdb_poster = None
    if poster_provider == "tvdb" and tvdb_posters:
        from app.services.tvdb import pick_tvdb_poster

        single_season = _resolve_single_tvdb_season(subtype, anizp_episodes, attributes, cinemeta_data)
        tvdb_poster = pick_tvdb_poster(tvdb_posters, lang=norm_lang, season_number=single_season)
    poster = tvdb_poster or kitsu_poster or anizp_poster or ""
    cover_data = attributes.get("coverImage") or {}
    kitsu_cover = cover_data.get("original") or cover_data.get("large") or cover_data.get("medium")

    cin_bg = cinemeta_data.get("background") if cinemeta_data else None
    fanarttv_bg = None
    if fanart_data:
        from app.services.fanart import pick_fanart_background
        fanarttv_bg = pick_fanart_background(fanart_data)

    tvdb_api_bg = None
    if tvdb_data:
        from app.services.tvdb import pick_tvdb_background
        tvdb_api_bg = pick_tvdb_background(tvdb_data)

    chosen_bg = pick_backdrop(
        backdrop_provider,
        sources={
            "fanarttv": fanarttv_bg,
            "cinemeta": cin_bg,
            "tvdb": tvdb_api_bg or tvdb_fanart,
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

    if subtype == "movie":
        videos.append(
            {
                "id": video_base,
                "title": title,
                "episode": 1,
                "season": 1,
                "released": _normalize_iso_released(start_date),
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

        tvdb_by_id, tvdb_by_se, tvdb_by_abs = _index_tvdb_episodes(tvdb_episodes)

        last_valid_ep_date = None
        for i in range(1, total_ep + 1):
            ep_num = i
            kitsu_ep = kitsu_ep_map.get(i)
            anizp_ep = anizp_episodes.get(str(i)) or {}

            # Extract attributes from Kitsu episode if available
            attrs = kitsu_ep.get("attributes", {}) if kitsu_ep else {}
            anizp_ep_titles = anizp_ep.get("title") if isinstance(anizp_ep.get("title"), dict) else {}

            if title_language == "english":
                base_ep_title = (
                    anizp_ep_titles.get("en")
                    or attrs.get("canonicalTitle")
                    or anizp_ep_titles.get("x-jat")
                    or f"Episode {ep_num}"
                )
            elif title_language == "romaji":
                base_ep_title = (
                    anizp_ep_titles.get("x-jat")
                    or attrs.get("canonicalTitle")
                    or anizp_ep_titles.get("en")
                    or f"Episode {ep_num}"
                )
            elif title_language == "japanese":
                base_ep_title = (
                    anizp_ep_titles.get("ja")
                    or anizp_ep_titles.get("x-jat")
                    or attrs.get("canonicalTitle")
                    or f"Episode {ep_num}"
                )
            else:  # default / canonical
                base_ep_title = (
                    attrs.get("canonicalTitle")
                    or anizp_ep_titles.get("en")
                    or anizp_ep_titles.get("x-jat")
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

            tvdb_ep = _match_tvdb_episode(
                anizp_ep,
                ep_num,
                ep_season,
                is_likely_sequel,
                tvdb_by_id,
                tvdb_by_se,
                tvdb_by_abs,
            )

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
            normalized_released = _normalize_iso_released(candidate_released)
            if normalized_released:
                ep_date = datetime.strptime(normalized_released[:10], "%Y-%m-%d").date()
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
                    released = normalized_released
                    last_valid_ep_date = ep_date

            # If the anime or episode is unreleased (air date in future), Cinemeta's Metahub
            # thumbnail link is pre-computed and guaranteed to 404 on Metahub/TMDB.
            # Suppress cinemeta_thumb so it falls back to backdrop (background) or real preview stills.
            if cinemeta_thumb:
                if anime_status in ["unreleased", "not_yet_released"]:
                    cinemeta_thumb = None
                elif released:
                    ep_date = datetime.strptime(released[:10], "%Y-%m-%d").date()
                    now_date = datetime.now(UTC).date()
                    if ep_date > now_date:
                        cinemeta_thumb = None
                elif not released and is_likely_sequel:
                    cinemeta_thumb = None

            if episodes_provider == "kitsu":
                base_overview = attrs.get("synopsis") or anizp_ep.get("overview") or anizp_ep.get("summary") or ""
                thumbnail = (
                    (attrs.get("thumbnail") or {}).get("original")
                    or (attrs.get("thumbnail") or {}).get("large")
                    or anizp_ep.get("image")
                    or cinemeta_thumb
                    or background
                )
            else:  # anizp, mal, or default
                base_overview = anizp_ep.get("overview") or anizp_ep.get("summary") or attrs.get("synopsis") or ""
                thumbnail = (
                    anizp_ep.get("image")
                    or (attrs.get("thumbnail") or {}).get("original")
                    or (attrs.get("thumbnail") or {}).get("large")
                    or cinemeta_thumb
                    or background
                )

            ep_title, overview = _resolve_localized_episode_text(
                norm_lang,
                anizp_code,
                base_ep_title,
                base_overview,
                anizp_ep,
                tvdb_ep,
                episodes_provider,
                title_language,
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

            videos.append(
                {
                    "id": f"{video_base}:{ep_num}",
                    "title": ep_title,
                    "episode": ep_num,
                    "season": 1,
                    "released": released,
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
