import difflib
import logging
import re

import httpx

from app.services.db import cache_ids, db
from app.services.http import get_client

from .constants import ANIZP_API, ARM_API, TIMEOUT
from .fribb import ensure_fribb_mappings


async def _try_arm(client: httpx.AsyncClient, kitsu_id: str) -> tuple[str | None, str | None]:
    resp = await client.get(
        ARM_API,
        params={"source": "kitsu", "id": kitsu_id, "include": "anilist,myanimelist"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return None, None
    mal_id = str(data["myanimelist"]) if data.get("myanimelist") else None
    anilist_id = str(data["anilist"]) if data.get("anilist") else None
    if mal_id or anilist_id:
        return mal_id, anilist_id
    return None, None


async def _try_anizp(client: httpx.AsyncClient, kitsu_id: str) -> tuple[str | None, str | None]:
    resp = await client.get(ANIZP_API, params={"kitsu_id": kitsu_id}, timeout=TIMEOUT)
    resp.raise_for_status()
    mappings = resp.json().get("mappings", {})
    mal_id = str(mappings["mal_id"]) if mappings.get("mal_id") else None
    anilist_id = str(mappings["anilist_id"]) if mappings.get("anilist_id") else None
    imdb_id = str(mappings.get("imdb_id")) if mappings.get("imdb_id") else None
    tmdb_id = str(mappings.get("themoviedb_id")) if mappings.get("themoviedb_id") else None
    tvdb_id = str(mappings.get("thetvdb_id")) if mappings.get("thetvdb_id") else None
    if mal_id or anilist_id or imdb_id or tmdb_id or tvdb_id:
        cache_ids(
            kitsu_id=kitsu_id, mal_id=mal_id, anilist_id=anilist_id, imdb_id=imdb_id, tmdb_id=tmdb_id, tvdb_id=tvdb_id
        )
        return mal_id, anilist_id
    return None, None


async def _try_fribb(client: httpx.AsyncClient, kitsu_id: str) -> tuple[str | None, str | None]:
    await ensure_fribb_mappings(client)
    try:
        kid = int(kitsu_id)
        doc = db.fribb_mappings.find_one({"kitsu_id": kid})
        if doc:
            mal_id = doc.get("mal_id")
            anilist_id = doc.get("anilist_id")
            return mal_id, anilist_id
    except Exception as e:
        logging.error("Error looking up kitsu_id in Fribb mappings: %s", e)
    return None, None


# Single-letter numerals (i, v, x) are deliberately omitted: they collide with ordinary title
# tokens such as "Spy x Family", "Hunter x Hunter" or "I Want to Eat Your Pancreas".
ROMAN_NUMERALS: dict[str, str] = {
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
}
SEQUEL_KEYWORDS: set[str] = {
    "movie",
    "film",
    "special",
    "recap",
    "ova",
    "ona",
}


def normalize_title(t: str) -> str:
    if not t:
        return ""
    t = t.casefold()
    t = re.sub(r"^(?:the|a|an)\s+", "", t)
    t = re.sub(r"[^\w\s]", " ", t)
    return " ".join(t.split())


def extract_markers(t: str) -> set[str]:
    markers = set()
    cleaned = re.sub(r"[^\w\s]", " ", t.casefold())
    for token in cleaned.split():
        # Digits anywhere in a token: "2", "2nd", "s2", and CJK forms like "第3期" (no word break)
        digits = re.findall(r"\d+", token)
        if digits:
            markers.update(str(int(d)) for d in digits)
            continue
        if token in ROMAN_NUMERALS:
            markers.add(ROMAN_NUMERALS[token])
            continue
        if token in SEQUEL_KEYWORDS:
            markers.add(token)
    return markers


def _check_single_title_pair(q: str, c: str) -> tuple[bool, bool, str]:
    q_markers = extract_markers(q)
    c_markers = extract_markers(c)
    if q_markers != c_markers:
        return False, False, f"Marker mismatch: {q_markers} != {c_markers}"

    nq = normalize_title(q)
    nc = normalize_title(c)
    if not nq or not nc:
        return False, False, "Empty normalized title"

    if nq == nc:
        return True, True, "Exact match"

    q_toks = nq.split()
    c_toks = nc.split()
    if not q_toks or not c_toks:
        return False, False, "Empty tokens"

    if q_toks[0] != c_toks[0]:
        return False, False, f"First token mismatch: '{q_toks[0]}' != '{c_toks[0]}'"

    ratio = difflib.SequenceMatcher(None, nq, nc).ratio()
    if ratio >= 0.85:
        return True, False, f"Ratio {ratio:.2f} >= 0.85"

    return False, False, f"Ratio {ratio:.2f} < 0.85"


def check_title_gate(query_titles: list[str], cand_titles: list[str]) -> tuple[bool, bool, str]:
    best_passed = False
    best_exact = False
    best_reason = "Passed"
    last_failure_reason = "No matching title pair"

    for q in query_titles:
        if not q:
            continue
        for c in cand_titles:
            if not c:
                continue
            passed, is_exact, reason = _check_single_title_pair(q, c)
            if is_exact:
                return True, True, reason
            if passed:
                best_passed = True
                best_reason = reason
            else:
                last_failure_reason = reason

    return best_passed, best_exact, (best_reason if best_passed else last_failure_reason)


def normalize_canonical_format(fmt: str | None) -> str | None:
    if not fmt:
        return None
    cleaned = str(fmt).strip().upper().replace(" ", "_").replace("-", "_")
    if cleaned in ("TV", "TV_SERIES"):
        return "TV"
    if cleaned in ("TV_SHORT", "TV_SHORTS"):
        return "TV_SHORT"
    if cleaned in ("MOVIE", "FILM"):
        return "MOVIE"
    if cleaned in ("OVA", "OVAS"):
        return "OVA"
    if cleaned in ("ONA", "ONAS"):
        return "ONA"
    if cleaned in ("SPECIAL", "SPECIALS", "TV_SPECIAL"):
        return "SPECIAL"
    if cleaned in ("MUSIC",):
        return "MUSIC"
    return cleaned


def check_format_gate(query_fmt: str | None, cand_fmt: str | None) -> bool:
    q_norm = normalize_canonical_format(query_fmt)
    c_norm = normalize_canonical_format(cand_fmt)
    if not q_norm or not c_norm:
        return True
    if q_norm == c_norm:
        return True
    if {q_norm, c_norm} == {"TV", "TV_SHORT"}:
        return True
    return False


def check_year_gate(query_year: int | None, cand_year: int | None) -> bool:
    if not query_year or not cand_year:
        return True
    return abs(query_year - cand_year) <= 1


def check_episode_gate(query_eps: int | None, cand_eps: int | None) -> bool:
    if not query_eps or not cand_eps:
        return True
    if query_eps <= 12 or cand_eps <= 12:
        return query_eps == cand_eps
    return abs(query_eps - cand_eps) <= 1


async def search_kitsu_by_title(info_or_title: dict | str) -> str | None:
    """
    Searches Kitsu by title using hard metadata gates:
      1. Format Gate (TV vs Movie vs ONA etc.)
      2. Year Gate (|query_year - kitsu_year| <= 1)
      3. Episode Count Gate
      4. Sequel-Marker Gate (digits, ordinals, roman numerals, keywords)
      5. Title Gate (Exact or ratio >= 0.85 with first-token match)
      6. Uniqueness Gate (ambiguity rejects)
    """
    if not info_or_title:
        return None

    if isinstance(info_or_title, str):
        info = {"titles": [info_or_title]}
    else:
        info = info_or_title

    query_titles = [t for t in info.get("titles", []) if t]
    if not query_titles:
        return None

    query_format = info.get("format")
    query_year = info.get("year")
    query_episodes = info.get("episodes")

    # Search distinct titles (e.g. English, Romaji) up to 2 distinct queries
    search_queries = []
    for t in query_titles:
        norm_t = normalize_title(t)
        if norm_t and norm_t not in [normalize_title(x) for x in search_queries]:
            search_queries.append(t)
        if len(search_queries) >= 2:
            break

    client = get_client()
    url = "https://kitsu.io/api/edge/anime"
    headers = {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }

    candidates_by_id = {}
    for sq in search_queries:
        try:
            resp = await client.get(url, params={"filter[text]": sq, "page[limit]": 5}, headers=headers, timeout=8)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                for item in data:
                    item_id = str(item.get("id"))
                    if item_id and item_id not in candidates_by_id:
                        candidates_by_id[item_id] = item
        except Exception as e:
            logging.warning("Kitsu title search failed for '%s': %s", sq, e)

    if not candidates_by_id:
        return None

    passing_candidates: list[tuple[str, bool, str]] = []  # (kitsu_id, is_exact, canonical_title)

    for item_id, item in candidates_by_id.items():
        attr = item.get("attributes", {})
        cand_title = attr.get("canonicalTitle") or ""

        # Gate 1: Format
        kitsu_format = attr.get("subtype") or attr.get("showType")
        if not check_format_gate(query_format, kitsu_format):
            logging.debug(
                "Kitsu candidate %s ('%s') rejected by Format Gate (%s != %s)",
                item_id,
                cand_title,
                query_format,
                kitsu_format,
            )
            continue

        # Gate 2: Year
        cand_year = None
        start_date = attr.get("startDate")
        if start_date and len(start_date) >= 4 and start_date[:4].isdigit():
            cand_year = int(start_date[:4])
        if not check_year_gate(query_year, cand_year):
            logging.debug(
                "Kitsu candidate %s ('%s') rejected by Year Gate (%s vs %s)",
                item_id,
                cand_title,
                query_year,
                cand_year,
            )
            continue

        # Gate 3: Episode count
        cand_eps = attr.get("episodeCount")
        if not check_episode_gate(query_episodes, cand_eps):
            logging.debug(
                "Kitsu candidate %s ('%s') rejected by Episode Gate (%s vs %s)",
                item_id,
                cand_title,
                query_episodes,
                cand_eps,
            )
            continue

        # Gate 4 & 5: Title & Sequel-Marker
        cand_titles = [cand_title]
        titles_obj = attr.get("titles") or {}
        if isinstance(titles_obj, dict):
            cand_titles.extend(titles_obj.values())
        abbrev = attr.get("abbreviatedTitles") or []
        if isinstance(abbrev, list):
            cand_titles.extend(abbrev)

        passed_title, is_exact, reason = check_title_gate(query_titles, [t for t in cand_titles if t])
        if not passed_title:
            logging.debug("Kitsu candidate %s ('%s') rejected by Title Gate: %s", item_id, cand_title, reason)
            continue

        passing_candidates.append((item_id, is_exact, cand_title))

    # Gate 6: Uniqueness
    if not passing_candidates:
        return None

    if len(passing_candidates) == 1:
        kid, is_ex, ctitle = passing_candidates[0]
        logging.info("Kitsu title search verified candidate kitsu_id=%s ('%s', exact=%s)", kid, ctitle, is_ex)
        return kid

    exact_candidates = [c for c in passing_candidates if c[1]]
    if len(exact_candidates) == 1:
        kid, is_ex, ctitle = exact_candidates[0]
        logging.info("Kitsu title search resolved single exact candidate kitsu_id=%s ('%s')", kid, ctitle)
        return kid

    logging.warning(
        "Kitsu title search ambiguous: multiple candidates passed without single exact match: %s",
        [c[0] for c in passing_candidates],
    )
    return None


async def fetch_anime_info_by_mal_id(mal_id: str) -> dict | None:
    """
    Queries AniList GraphQL using idMal to retrieve titles, format, year, episodes, and AniList ID.
    Also falls back to public MAL API V2 if AniList GraphQL query fails.
    Returns info dict or None.
    """
    query = """
    query ($idMal: Int) {
      Media(idMal: $idMal, type: ANIME) {
        id
        format
        seasonYear
        startDate {
          year
        }
        episodes
        synonyms
        title {
          english
          userPreferred
          romaji
          native
        }
      }
    }
    """
    try:
        from app.api.anilist import _gql

        data = await _gql(None, query, {"idMal": int(mal_id)})
        media = data.get("data", {}).get("Media", {})
        if media:
            anilist_id = str(media["id"])
            titles_dict = media.get("title", {}) or {}
            raw_titles = [
                titles_dict.get("english"),
                titles_dict.get("userPreferred"),
                titles_dict.get("romaji"),
                titles_dict.get("native"),
            ]
            synonyms = media.get("synonyms") or []
            if isinstance(synonyms, list):
                raw_titles.extend(synonyms)
            seen = set()
            unique_titles = []
            for t in raw_titles:
                if t and t not in seen:
                    seen.add(t)
                    unique_titles.append(t)

            year = media.get("seasonYear") or (media.get("startDate") or {}).get("year")
            return {
                "titles": unique_titles,
                "mal_id": str(mal_id),
                "anilist_id": anilist_id,
                "format": normalize_canonical_format(media.get("format")),
                "year": year,
                "episodes": media.get("episodes"),
            }
    except Exception as e:
        logging.warning("fetch_anime_info_by_mal_id: AniList query failed for mal_id=%s: %s", mal_id, e)

    from config import Config

    if Config.MAL_CLIENT_ID:
        try:
            client = get_client()
            resp = await client.get(
                f"{Config.MAL_API_URL}/anime/{mal_id}",
                params={"fields": "alternative_titles,media_type,start_date,num_episodes"},
                headers={"X-MAL-CLIENT-ID": Config.MAL_CLIENT_ID},
                timeout=TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                raw_titles = [data.get("title")]
                alt = data.get("alternative_titles") or {}
                if alt.get("en"):
                    raw_titles.append(alt.get("en"))
                if alt.get("ja"):
                    raw_titles.append(alt.get("ja"))
                for s in alt.get("synonyms") or []:
                    raw_titles.append(s)

                seen = set()
                unique_titles = []
                for t in raw_titles:
                    if t and t not in seen:
                        seen.add(t)
                        unique_titles.append(t)

                start_date = data.get("start_date")
                year = (
                    int(start_date[:4])
                    if (start_date and len(start_date) >= 4 and start_date[:4].isdigit())
                    else None
                )
                return {
                    "titles": unique_titles,
                    "mal_id": str(mal_id),
                    "anilist_id": None,
                    "format": normalize_canonical_format(data.get("media_type")),
                    "year": year,
                    "episodes": data.get("num_episodes"),
                }
        except Exception as e:
            logging.warning("fetch_anime_info_by_mal_id: public MAL fetch failed for mal_id=%s: %s", mal_id, e)

    return None


async def fetch_anime_info_by_anilist_id(anilist_id: str) -> dict | None:
    """
    Queries AniList GraphQL using id to retrieve titles, format, year, episodes, and MAL ID.
    Returns info dict or None.
    """
    query = """
    query ($id: Int) {
      Media(id: $id, type: ANIME) {
        idMal
        format
        seasonYear
        startDate {
          year
        }
        episodes
        synonyms
        title {
          english
          userPreferred
          romaji
          native
        }
      }
    }
    """
    try:
        from app.api.anilist import _gql

        data = await _gql(None, query, {"id": int(anilist_id)})
        media = data.get("data", {}).get("Media", {})
        if media:
            mal_id = str(media["idMal"]) if media.get("idMal") else None
            titles_dict = media.get("title", {}) or {}
            raw_titles = [
                titles_dict.get("english"),
                titles_dict.get("userPreferred"),
                titles_dict.get("romaji"),
                titles_dict.get("native"),
            ]
            synonyms = media.get("synonyms") or []
            if isinstance(synonyms, list):
                raw_titles.extend(synonyms)
            seen = set()
            unique_titles = []
            for t in raw_titles:
                if t and t not in seen:
                    seen.add(t)
                    unique_titles.append(t)

            year = media.get("seasonYear") or (media.get("startDate") or {}).get("year")
            return {
                "titles": unique_titles,
                "mal_id": mal_id,
                "anilist_id": str(anilist_id),
                "format": normalize_canonical_format(media.get("format")),
                "year": year,
                "episodes": media.get("episodes"),
            }
    except Exception as e:
        logging.warning("fetch_anime_info_by_anilist_id: AniList query failed for anilist_id=%s: %s", anilist_id, e)

    return None
