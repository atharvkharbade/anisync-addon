import asyncio
import datetime
import json
import logging
import random

from app.api import anilist as anilist_api
from app.api import mal as mal_api
from app.api import simkl as simkl_api
from app.lib.id_resolver import resolve, resolve_anilist_to_kitsu, resolve_mal_to_kitsu
from app.services.db import db, get_user, store_user
from app.services.http import get_client
from config import Config

logger = logging.getLogger(__name__)
recommendations_cache_collection = db.get_collection("recommendations_cache")

currently_updating_users = set()

# Popular anime fallback collection
POPULAR_FALLBACKS = []


def clean_html(text: str) -> str:
    if not text:
        return ""
    import re

    # Strip HTML tags
    clean = re.sub(r"<[^<]+?>", "", text)
    # Decode common HTML entities if any
    clean = (
        clean.replace("&quot;", '"')
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&apos;", "'")
    )
    return clean.strip()


def is_proper_anime(title: str, synopsis: str | None = None) -> bool:
    if not title:
        return True
    t_lower = title.lower()

    # Exclude obvious shorts, chibi series, recaps, side stories, and specials by keywords
    excl_keywords = [
        "break time",
        "kyuukei jikan",
        "chibi",
        "petit",
        "mini-anime",
        "mini anime",
        "character theater",
        "chara gekijou",
        "picture drama",
        "recap",
        "summary",
        "special episode",
        "pv",
        "trailer",
        "commercial",
        "short anime",
        "web short",
        "spin-off",
        "spinoff",
        "bonus",
        "audio commentary",
        "side story",
        "side stories",
        "junior high",
        "ple ple pleiades",
        "chara-gekijou",
        "chara gekijou",
        "oitsukeru",
        "de oitsukeru",
        "soushuuhen",
        "sou-shuuhen",
        "soushuhen",
        "digest",
        "daijesuto",
        "compilation",
        "catch-up",
        "catch up",
        "re-cap",
        "re-edit",
        "omnibus",
        "theatrical short",
        "drama cd",
        "audio drama",
        "special edition",
    ]

    for kw in excl_keywords:
        if kw == "ona":
            import re

            if re.search(r"\bona\b", t_lower):
                return False
        elif kw == "ova":
            import re

            if re.search(r"\bova\b", t_lower):
                return False
        elif kw in t_lower:
            return False

    if synopsis:
        s_lower = synopsis.lower().strip()
        recap_prefixes = (
            "recap of",
            "a recap of",
            "summary of",
            "a summary of",
            "digest of",
            "a digest of",
            "compilation of",
            "a compilation of",
            "special episode summarizing",
            "recap episode",
        )
        if any(s_lower.startswith(prefix) for prefix in recap_prefixes):
            return False

    return True


async def get_mal_recommendations_for_id(token: str | None, mal_id: str) -> list[dict]:
    items = []
    if token:
        client = get_client()
        url = f"{Config.MAL_API_URL}/anime/{mal_id}"
        params = {
            "fields": "recommendations{node{id,title,main_picture,genres,start_season,media_type,popularity,mean,synopsis,average_episode_duration,status}}"
        }
        headers = {"Authorization": f"Bearer {token}"}
        try:
            resp = await client.get(url, params=params, headers=headers, timeout=10)
            if resp.status_code == 200:
                items = resp.json().get("recommendations", [])
        except Exception as e:
            logger.warning("Failed to fetch MAL recommendations for MAL ID %s: %s", mal_id, e)

    if not items and mal_id:
        try:
            from app.api.jikan import get_anime_recommendations
            jikan_recs = await get_anime_recommendations(mal_id)
            if jikan_recs:
                for rec in jikan_recs:
                    entry = rec.get("entry", {})
                    mid = entry.get("mal_id")
                    title = entry.get("title") or entry.get("name")
                    url = entry.get("url") or ""
                    if not title and "/anime/" in url:
                        parts = url.split("/anime/")[1].split("/")
                        if len(parts) > 1:
                            title = parts[1].replace("_", " ")
                    if mid:
                        items.append({
                            "node": {
                                "id": mid,
                                "title": title or f"Anime #{mid}"
                            }
                        })
        except Exception as ex:
            logger.warning("Jikan recommendations fallback failed for MAL ID %s: %s", mal_id, ex)

    return items


async def get_anilist_recommendations_bulk(token: str, anilist_ids: list[int]) -> list[dict]:
    if not anilist_ids:
        return []
    
    # AniList Page recommendations doesn't support bulk mediaId_in, so we query using aliases.
    # Limit to top 15 seeds to keep query size reasonable and avoid complexity limits.
    anilist_ids = [int(aid) for aid in anilist_ids[:15]]
    
    rec_fields = """
          rating
          media {
            id
          }
          mediaRecommendation {
            id
            idMal
            status
            title {
              english
              romaji
              userPreferred
            }
            coverImage {
              large
              medium
            }
            startDate {
              year
            }
            genres
            format
            duration
            popularity
            averageScore
            description
          }
    """
    
    # Construct the query variables definition
    var_defs = ", ".join([f"$mediaId{i}: Int" for i in range(len(anilist_ids))])
    
    # Construct the query fields (aliases)
    alias_queries = []
    for i in range(len(anilist_ids)):
        alias_queries.append(f"""
      page_{i}: Page(page: 1, perPage: 15) {{
        recommendations(mediaId: $mediaId{i}, sort: RATING_DESC) {{
          {rec_fields}
        }}
      }}
        """)
        
    query = f"""
    query ({var_defs}) {{
      {"".join(alias_queries)}
    }}
    """
    
    variables = {f"mediaId{i}": aid for i, aid in enumerate(anilist_ids)}
    
    try:
        res = await anilist_api._gql(token, query, variables)
        all_recs = []
        for key, page_data in res.get("data", {}).items():
            if key.startswith("page_") and page_data:
                recs = page_data.get("recommendations", [])
                if recs:
                    all_recs.extend(recs)
        return all_recs
    except Exception as e:
        logger.warning("Failed bulk AniList recommendations query: %s", e)
    return []


async def resolve_title_via_kitsu(
    title: str, rec_year_min: int = 1970, rec_year_max: int = 2026, rec_excluded_genres: list = None
) -> dict | None:
    url = "https://kitsu.io/api/edge/anime"
    params = {"filter[text]": title, "page[limit]": 1}
    headers = {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }
    client = get_client()
    try:
        resp = await client.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if data:
                item = data[0]
                kitsu_id = str(item["id"])
                attrs = item.get("attributes", {})

                # Check year bounds
                start_date = attrs.get("startDate")
                if start_date:
                    try:
                        k_year = int(start_date[:4])
                        if k_year < rec_year_min or k_year > rec_year_max:
                            return None
                    except ValueError:
                        pass

                subtype = (attrs.get("subtype") or "tv").lower()
                item_type = "movie" if subtype == "movie" else "series"
                titles = attrs.get("titles", {})
                canonical_title = attrs.get("canonicalTitle") or titles.get("en") or titles.get("en_jp") or title
                poster = (
                    attrs.get("posterImage", {}).get("large")
                    or attrs.get("posterImage", {}).get("medium")
                    or attrs.get("posterImage", {}).get("original")
                    or ""
                )
                if poster:
                    poster = poster.split("?")[0]
                synopsis = attrs.get("synopsis") or ""

                return {
                    "id": f"kitsu:{kitsu_id}",
                    "type": item_type,
                    "name": canonical_title,
                    "poster": poster,
                    "description": synopsis[:200] + "..." if len(synopsis) > 200 else synopsis,
                }
    except Exception as e:
        logger.warning("Failed to resolve Gemini title '%s' via Kitsu: %s", title, e)
    return None


async def update_recommendations_cache(user_id: str, force: bool = False):
    if user_id in currently_updating_users:
        return
    currently_updating_users.add(user_id)
    try:
        await _update_recommendations_cache_impl(user_id, force)
    except Exception as e:
        logger.exception("Error updating recommendations for user %s: %s", user_id, e)
    finally:
        currently_updating_users.discard(user_id)


def normalize_user_status(status: str | None) -> str:
    if not status:
        return "watching"
    s = status.lower()
    if s in ["watching", "current"]:
        return "watching"
    if s in ["completed"]:
        return "completed"
    if s in ["on_hold", "paused", "hold"]:
        return "on_hold"
    if s in ["dropped"]:
        return "dropped"
    if s in ["plan_to_watch", "planning", "plantowatch"]:
        return "planning"
    return s


async def get_recommendations_for_seeds(
    seeds: list[dict],
    user: dict,
    watched_mal_ids: set,
    watched_anilist_ids: set,
    watched_titles: set,
    max_seeds: int = 15,
) -> list[dict]:
    if not seeds:
        return []

    rec_language = user.get("rec_language", "en")
    rec_popularity = user.get("rec_popularity", "balanced")
    rec_year_min = user.get("rec_year_min", 1980)
    rec_year_max = user.get("rec_year_max", 2026)
    rec_excluded_movie_genres = user.get("rec_excluded_movie_genres", [])
    rec_excluded_series_genres = user.get("rec_excluded_series_genres", [])
    filter_watched = user.get("recommendations_filter_watched", True)

    title_lang = (user.get("title_language", "english") or "english").lower() if user else "english"
    rec_candidates = {}

    # 1. Fetch from AniList in bulk
    al_ids = [int(s["anilist_id"]) for s in seeds[:max_seeds] if s["anilist_id"]]
    al_recs = []
    anilist_id_to_title = {str(s["anilist_id"]): s["title"] for s in seeds if s.get("anilist_id")}
    if al_ids and user.get("anilist_token") and user.get("anilist_enabled", True):
        al_recs = await get_anilist_recommendations_bulk(user["anilist_token"], al_ids)

    for rec in al_recs:
        media = rec.get("mediaRecommendation")
        if not media:
            continue

        if media.get("status") == "NOT_YET_RELEASED":
            continue

        # Exclude OVA, SPECIAL, MUSIC, TV_SHORT and short durations (<= 5 minutes)
        m_format = media.get("format")
        duration = media.get("duration")
        if m_format in ["OVA", "SPECIAL", "MUSIC", "TV_SHORT"]:
            continue
        if duration is not None and duration <= 5:
            continue

        seed_media = rec.get("media") or {}
        seed_aid = str(seed_media.get("id")) if seed_media.get("id") else None
        seed_title = anilist_id_to_title.get(seed_aid) if seed_aid else None

        aid = str(media.get("id"))
        mid = str(media.get("idMal")) if media.get("idMal") else None

        # Watched filters
        if filter_watched:
            if aid in watched_anilist_ids or (mid and mid in watched_mal_ids):
                continue

        # Year filter
        year = media.get("startDate", {}).get("year")
        if year and (year < rec_year_min or year > rec_year_max):
            continue

        item_type = "movie" if m_format == "MOVIE" else "series"

        # Excluded genres filter
        genres = media.get("genres", []) or []
        excluded_genres = rec_excluded_movie_genres if item_type == "movie" else rec_excluded_series_genres
        if any(g in excluded_genres for g in genres):
            continue

        # Popularity filters
        pop_score = media.get("popularity") or 0
        avg_score = media.get("averageScore") or 0
        if rec_popularity == "mainstream":
            if pop_score < 25000:
                continue
        elif rec_popularity == "gems":
            if pop_score >= 25000 or avg_score < 73:
                continue

        # Choose title based on user language preference
        title_pref = media.get("title", {})
        if title_lang == "japanese":
            title = title_pref.get("native") or title_pref.get("userPreferred") or title_pref.get("english") or "Unknown Title"
        elif title_lang == "romaji":
            title = title_pref.get("romaji") or title_pref.get("userPreferred") or title_pref.get("english") or "Unknown Title"
        else:
            title = title_pref.get("english") or title_pref.get("userPreferred") or title_pref.get("romaji") or "Unknown Title"

        if filter_watched and title.lower() in watched_titles:
            continue

        desc = media.get("description") or ""
        if not is_proper_anime(title, desc):
            continue

        poster = (media.get("coverImage") or {}).get("large") or (media.get("coverImage") or {}).get("medium") or ""
        syn = clean_html(media.get("description") or "")

        key = f"mal:{mid}" if mid else f"anilist:{aid}"
        if key not in rec_candidates:
            rec_candidates[key] = {
                "id": key,
                "type": item_type,
                "name": title,
                "poster": poster,
                "score": rec.get("rating", 1),
                "description": "Recommended based on your history.",
                "synopsis": syn,
                "inspired_by_titles": [seed_title] if seed_title else [],
            }
        else:
            rec_candidates[key]["score"] += rec.get("rating", 1)
            if syn and not rec_candidates[key].get("synopsis"):
                rec_candidates[key]["synopsis"] = syn
            if seed_title and seed_title not in rec_candidates[key]["inspired_by_titles"]:
                rec_candidates[key]["inspired_by_titles"].append(seed_title)

    # 2. Fetch from MAL (limit to top 5 seeds for rate limits)
    mal_seed_shows = [s for s in seeds[:5] if s["mal_id"]]
    if mal_seed_shows and user.get("mal_access_token") and user.get("mal_enabled", True):
        tasks = [get_mal_recommendations_for_id(user["mal_access_token"], s["mal_id"]) for s in mal_seed_shows]
        mal_recs_lists = await asyncio.gather(*tasks)

        for s, rec_list in zip(mal_seed_shows, mal_recs_lists):
            seed_title = s["title"]
            for rec in rec_list:
                node = rec.get("node", {})
                mid = str(node.get("id"))
                alt_titles = node.get("alternative_titles") or {}
                if title_lang == "english":
                    title = alt_titles.get("en") or node.get("title") or "Unknown Title"
                elif title_lang == "japanese":
                    title = alt_titles.get("ja") or node.get("title") or "Unknown Title"
                else:
                    title = node.get("title") or alt_titles.get("en") or "Unknown Title"

                if node.get("status") == "not_yet_aired":
                    continue

                syn = clean_html(node.get("synopsis") or "")
                # Exclude OVA, SPECIAL, MUSIC and short duration (<= 5 minutes / 300 seconds)
                m_type = node.get("media_type")
                duration = node.get("average_episode_duration")
                if m_type in ["ova", "special", "music"] or not is_proper_anime(title, syn):
                    continue
                if duration is not None and duration <= 300:
                    continue

                # Watched filters
                if filter_watched:
                    if mid in watched_mal_ids:
                        continue
                    if title.lower() in watched_titles:
                        continue

                # Year filter
                year = node.get("start_season", {}).get("year")
                if year and (year < rec_year_min or year > rec_year_max):
                    continue

                item_type = "movie" if m_type == "movie" else "series"

                # Excluded genres filter
                genres = [g.get("name") for g in node.get("genres", []) if g.get("name")]
                excluded_genres = rec_excluded_movie_genres if item_type == "movie" else rec_excluded_series_genres
                if any(g in excluded_genres for g in genres):
                    continue

                # Popularity filters
                pop_rank = node.get("popularity")
                mean_score = node.get("mean")
                if rec_popularity == "mainstream":
                    if pop_rank and pop_rank > 1200:
                        continue
                elif rec_popularity == "gems":
                    if (pop_rank and pop_rank <= 1200) or (mean_score and mean_score < 7.3):
                        continue

                poster = node.get("main_picture", {}).get("large") or node.get("main_picture", {}).get("medium") or ""
                syn = clean_html(node.get("synopsis") or "")

                key = f"mal:{mid}"
                if key not in rec_candidates:
                    rec_candidates[key] = {
                        "id": key,
                        "type": item_type,
                        "name": title,
                        "poster": poster,
                        "score": rec.get("num_recommendations", 1),
                        "description": "Recommended based on your history.",
                        "synopsis": syn,
                        "inspired_by_titles": [seed_title],
                    }
                else:
                    rec_candidates[key]["score"] += rec.get("num_recommendations", 1)
                    if syn and not rec_candidates[key].get("synopsis"):
                        rec_candidates[key]["synopsis"] = syn
                    if seed_title not in rec_candidates[key]["inspired_by_titles"]:
                        rec_candidates[key]["inspired_by_titles"].append(seed_title)

    # 3. Kitsu Media Relationships (fetch sequels, prequels, spin-offs for up to 15 seeds)
    kitsu_seed_shows = [s for s in seeds[:15]]
    if kitsu_seed_shows:

        async def fetch_kitsu_relationships_for_seed(s):
            try:
                # 1. Resolve kitsu_id
                kitsu_id = None
                if s.get("mal_id"):
                    kitsu_id = await resolve_mal_to_kitsu(s["mal_id"])
                elif s.get("anilist_id"):
                    kitsu_id = await resolve_anilist_to_kitsu(s["anilist_id"])
                if not kitsu_id:
                    return s, []

                # 2. Fetch media-relationships from Kitsu
                url = f"https://kitsu.io/api/edge/anime/{kitsu_id}/media-relationships?include=destination"
                headers = {
                    "Accept": "application/vnd.api+json",
                    "Content-Type": "application/vnd.api+json",
                    "User-Agent": "Mozilla/5.0",
                }
                client = get_client()
                resp = await client.get(url, headers=headers, timeout=10)
                if resp.status_code != 200:
                    return s, []

                data = resp.json()
                included = data.get("included") or []
                related_items = []
                for item in included:
                    if item.get("type") == "anime":
                        kid = item.get("id")
                        attrs = item.get("attributes", {})
                        if not kid or not attrs:
                            continue

                        # Exclude OVA, SPECIAL, MUSIC, and short duration (<= 5 minutes)
                        subtype = (attrs.get("subtype") or "tv").lower()
                        episode_length = attrs.get("episodeLength")
                        if subtype in ["ova", "special", "music"]:
                            continue
                        if episode_length is not None and episode_length <= 5:
                            continue

                        if attrs.get("status") in ["upcoming", "unreleased", "tba"]:
                            continue

                        start_date = attrs.get("startDate")
                        k_year = None
                        if start_date:
                            try:
                                k_year = int(start_date[:4])
                            except ValueError:
                                pass

                        item_type = "movie" if subtype == "movie" else "series"
                        titles = attrs.get("titles", {})
                        title = attrs.get("canonicalTitle") or titles.get("en") or titles.get("en_jp")
                        if not is_proper_anime(title):
                            continue
                        poster = (
                            attrs.get("posterImage", {}).get("large")
                            or attrs.get("posterImage", {}).get("medium")
                            or attrs.get("posterImage", {}).get("original")
                            or ""
                        )
                        if poster:
                            poster = poster.split("?")[0]
                        synopsis = attrs.get("synopsis") or ""

                        related_items.append(
                            {
                                "kitsu_id": kid,
                                "name": title,
                                "poster": poster,
                                "type": item_type,
                                "year": k_year,
                                "description": synopsis[:200] + "..." if len(synopsis) > 200 else synopsis,
                            }
                        )
                return s, related_items
            except Exception as e:
                logger.warning("Kitsu relationship lookup failed for seed %s: %s", s.get("title"), e)
                return s, []

        kitsu_tasks = [fetch_kitsu_relationships_for_seed(s) for s in kitsu_seed_shows]
        kitsu_results = await asyncio.gather(*kitsu_tasks)

        for s, related_items in kitsu_results:
            seed_title = s["title"]
            for r_item in related_items:
                # Watched title filter first
                if filter_watched and r_item["name"].lower() in watched_titles:
                    continue

                # Resolve Kitsu ID to MAL/AniList IDs to do ID-based watched filtering
                mid, aid = await resolve(r_item["kitsu_id"])

                if filter_watched:
                    if (mid and mid in watched_mal_ids) or (aid and aid in watched_anilist_ids):
                        continue

                # Year filter
                year = r_item.get("year")
                if year and (year < rec_year_min or year > rec_year_max):
                    continue

                key = f"mal:{mid}" if mid else f"anilist:{aid}" if aid else f"kitsu:{r_item['kitsu_id']}"
                syn = clean_html(r_item.get("description") or "")

                # Add to candidates
                if key not in rec_candidates:
                    rec_candidates[key] = {
                        "id": key,
                        "type": r_item["type"],
                        "name": r_item["name"],
                        "poster": r_item["poster"],
                        "score": 10,  # Score boost for franchise expansions
                        "description": r_item["description"] or "Franchise sequel, prequel, or spin-off.",
                        "synopsis": syn,
                        "inspired_by_titles": [seed_title],
                    }
                else:
                    rec_candidates[key]["score"] += 10
                    if syn and not rec_candidates[key].get("synopsis"):
                        rec_candidates[key]["synopsis"] = syn
                    if seed_title not in rec_candidates[key]["inspired_by_titles"]:
                        rec_candidates[key]["inspired_by_titles"].append(seed_title)

    sorted_recs = sorted(rec_candidates.values(), key=lambda x: x["score"], reverse=True)
    for r in sorted_recs:
        r.pop("score", None)
    return sorted_recs


async def get_top_anime_by_genre(token: str, genre: str, sort: str = "POPULARITY_DESC") -> list[dict]:
    query = """
    query ($genre: String, $sort: [MediaSort]) {
      Page(page: 1, perPage: 50) {
        media(genre: $genre, type: ANIME, sort: $sort) {
          id
          idMal
          status
          title {
            english
            romaji
            userPreferred
          }
          coverImage {
            large
            medium
          }
          startDate {
            year
          }
          genres
          format
          duration
          popularity
          averageScore
          description
        }
      }
    }
    """
    try:
        res = await anilist_api._gql(token, query, {"genre": genre, "sort": [sort]})
        return res.get("data", {}).get("Page", {}).get("media", [])
    except Exception as e:
        logger.warning("Failed to fetch top anime for genre %s: %s", genre, e)
    return []


async def generate_genre_recommendations(
    genre: str, user: dict, watched_mal_ids: set, watched_anilist_ids: set, watched_titles: set
) -> list[dict]:
    rec_language = user.get("rec_language", "en")
    rec_popularity = user.get("rec_popularity", "balanced")
    rec_year_min = user.get("rec_year_min", 1980)
    rec_year_max = user.get("rec_year_max", 2026)
    rec_excluded_movie_genres = user.get("rec_excluded_movie_genres", [])
    rec_excluded_series_genres = user.get("rec_excluded_series_genres", [])
    filter_watched = user.get("recommendations_filter_watched", True)

    sort_order = "POPULARITY_DESC"
    if rec_popularity == "gems":
        sort_order = "SCORE_DESC"

    token = user.get("anilist_token")
    media_list = await get_top_anime_by_genre(token, genre, sort_order)

    recs = []
    for media in media_list:
        if not media:
            continue

        if media.get("status") == "NOT_YET_RELEASED":
            continue

        # Exclude OVA, SPECIAL, MUSIC, TV_SHORT and short durations (<= 5 minutes)
        m_format = media.get("format")
        duration = media.get("duration")
        if m_format in ["OVA", "SPECIAL", "MUSIC", "TV_SHORT"]:
            continue
        if duration is not None and duration <= 5:
            continue

        aid = str(media.get("id"))
        mid = str(media.get("idMal")) if media.get("idMal") else None

        # Watched filters
        if filter_watched:
            if aid in watched_anilist_ids or (mid and mid in watched_mal_ids):
                continue

        # Year filter
        year = media.get("startDate", {}).get("year")
        if year and (year < rec_year_min or year > rec_year_max):
            continue

        item_type = "movie" if m_format == "MOVIE" else "series"

        # Excluded genres filter
        genres = media.get("genres", []) or []
        excluded_genres = rec_excluded_movie_genres if item_type == "movie" else rec_excluded_series_genres
        if any(g in excluded_genres for g in genres):
            continue

        # Popularity filters
        pop_score = media.get("popularity") or 0
        avg_score = media.get("averageScore") or 0
        if rec_popularity == "mainstream":
            if pop_score < 25000:
                continue
        elif rec_popularity == "gems":
            if pop_score >= 25000 or avg_score < 73:
                continue

        # Choose title based on language
        title_pref = media.get("title", {})
        title_lang = (user.get("title_language", "english") or "english").lower() if user else "english"
        if title_lang == "japanese":
            title = title_pref.get("native") or title_pref.get("userPreferred") or title_pref.get("english") or "Unknown Title"
        elif title_lang == "romaji":
            title = title_pref.get("romaji") or title_pref.get("userPreferred") or title_pref.get("english") or "Unknown Title"
        else:
            title = title_pref.get("english") or title_pref.get("userPreferred") or title_pref.get("romaji") or "Unknown Title"

        if filter_watched and title.lower() in watched_titles:
            continue

        if not is_proper_anime(title):
            continue

        poster = (media.get("coverImage") or {}).get("large") or (media.get("coverImage") or {}).get("medium") or ""

        key = f"mal:{mid}" if mid else f"anilist:{aid}"
        syn = clean_html(media.get("description") or "")
        desc_header = f"Popular {genre} anime based on your taste."
        full_desc = f"{desc_header}  \n\n{syn}" if syn else desc_header

        recs.append({
            "id": key,
            "type": item_type,
            "name": title,
            "poster": poster,
            "description": full_desc,
            "synopsis": syn,
        })
    return recs


def select_weighted_seeds(pool, count):
    import random

    if len(pool) <= count:
        return pool
    selected = []
    pool_copy = list(pool)
    while len(selected) < count and pool_copy:
        weights = []
        for x in pool_copy:
            rating = x.get("rating") or 0
            if rating >= 9:
                w = 10
            elif 7 <= rating <= 8:
                w = 7
            elif 1 <= rating <= 6:
                w = 4
            else:  # unrated
                w = 5
            weights.append(w)
        choice = random.choices(pool_copy, weights=weights, k=1)[0]
        selected.append(choice)
        pool_copy.remove(choice)
    return selected


async def _update_recommendations_cache_impl(user_id: str, force: bool = False):
    user = get_user(user_id)
    if not user or not user.get("enable_recommendations", True):
        return
    fallbacks = get_popular_fallbacks()

    # Check if cache is fresh enough
    existing = recommendations_cache_collection.find_one({"uid": user_id})
    if existing and not force:
        last_updated = existing.get("last_updated")
        if last_updated and (datetime.datetime.utcnow() - last_updated) < datetime.timedelta(hours=24):
            return

    # Retrieve user preference filters
    rec_language = user.get("rec_language", "en")
    rec_popularity = user.get("rec_popularity", "balanced")
    rec_sorting_order = user.get("rec_sorting_order", "default")
    rec_year_min = user.get("rec_year_min", 1980)
    rec_year_max = user.get("rec_year_max", 2026)
    rec_excluded_movie_genres = user.get("rec_excluded_movie_genres", [])
    rec_excluded_series_genres = user.get("rec_excluded_series_genres", [])

    logger.info(
        "Recalculating recommendations for user %s (Lang: %s, Pop: %s, Years: %s-%s, Excl Movies: %s, Excl Series: %s)...",
        user_id,
        rec_language,
        rec_popularity,
        rec_year_min,
        rec_year_max,
        rec_excluded_movie_genres,
        rec_excluded_series_genres,
    )

    # 1. Fetch watched history from both track managers
    mal_items = []
    if user.get("mal_access_token") and user.get("mal_enabled", True):
        try:
            res = await mal_api.get_user_anime_list(user["mal_access_token"], limit=100)
            mal_items = res.get("data", [])
        except Exception as e:
            logger.warning("Failed to fetch MAL user list: %s", e)

    anilist_items = []
    if user.get("anilist_token") and user.get("anilist_enabled", True):
        try:
            anilist_uid = user.get("anilist_id")
            if anilist_uid:
                anilist_uid = int(anilist_uid)
            else:
                viewer = await anilist_api.get_viewer(user["anilist_token"])
                anilist_uid = int(viewer["id"])
                user["anilist_id"] = str(anilist_uid)
                store_user(user)
            collection = await anilist_api.get_user_anime_list(user["anilist_token"], user_id=anilist_uid)
            for user_list in collection.get("lists", []):
                anilist_items.extend(user_list.get("entries", []))
        except anilist_api.AnilistTokenInvalidError as e:
            logger.warning("AniList token invalid during recommendations update for user %s: %s", user_id, e)
            from app.services.db import handle_invalid_anilist_token
            handle_invalid_anilist_token(user_id)
        except Exception as e:
            logger.warning("Failed to fetch AniList user list: %s", e)

    simkl_items = []
    if user.get("simkl_access_token") and user.get("simkl_enabled", True):
        try:
            simkl_items = await simkl_api.get_user_anime_list(user["simkl_access_token"])
        except Exception as e:
            logger.warning("Failed to fetch Simkl user list for recommendations: %s", e)

    # 2. Extract unique shows and filter watched lists
    merged_shows = {}
    title_lang = (user.get("title_language", "english") or "english").lower() if user else "english"

    for item in mal_items:
        node = item.get("node", {})
        alt_titles = node.get("alternative_titles") or {}
        if title_lang == "english":
            title = alt_titles.get("en") or node.get("title") or "Unknown Title"
        elif title_lang == "japanese":
            title = alt_titles.get("ja") or node.get("title") or "Unknown Title"
        else:
            title = node.get("title") or alt_titles.get("en") or "Unknown Title"

        mal_id = str(node.get("id"))
        list_status = node.get("my_list_status", {})
        status = normalize_user_status(list_status.get("status"))
        rating = list_status.get("score", 0) or 0
        genres = [g.get("name") for g in node.get("genres", []) if g.get("name")]

        merged_shows[mal_id] = {
            "title": title,
            "mal_id": mal_id,
            "anilist_id": None,
            "simkl_id": None,
            "status": status,
            "rating": rating,
            "genres": genres,
        }

    for entry in anilist_items:
        media = entry.get("media", {})
        t_obj = media.get("title") or {}
        if title_lang == "english":
            title = t_obj.get("english") or t_obj.get("userPreferred") or t_obj.get("romaji") or "Unknown Title"
        elif title_lang == "japanese":
            title = t_obj.get("native") or t_obj.get("userPreferred") or t_obj.get("romaji") or "Unknown Title"
        else:
            title = t_obj.get("romaji") or t_obj.get("userPreferred") or t_obj.get("english") or "Unknown Title"

        anilist_id = str(media.get("id"))
        mal_id = str(media.get("idMal")) if media.get("idMal") else None
        status = normalize_user_status(entry.get("status"))
        rating = entry.get("score", 0) or 0
        if rating > 10:
            rating = int(rating / 10)
        genres = media.get("genres", []) or []

        key = mal_id if mal_id else f"al_{anilist_id}"
        if key not in merged_shows:
            merged_shows[key] = {
                "title": title,
                "mal_id": mal_id,
                "anilist_id": anilist_id,
                "simkl_id": None,
                "status": status,
                "rating": rating,
                "genres": genres,
            }
        else:
            merged_shows[key]["anilist_id"] = anilist_id
            merged_shows[key]["rating"] = max(merged_shows[key].get("rating") or 0, rating)

            # Status merging: completed/watching/dropped/on_hold override planning
            old_status = merged_shows[key]["status"]
            if old_status == "planning" and status != "planning":
                merged_shows[key]["status"] = status
            elif old_status != "completed" and status == "completed":
                merged_shows[key]["status"] = "completed"

            # Merge genres
            old_genres = merged_shows[key].get("genres", [])
            for g in genres:
                if g not in old_genres:
                    old_genres.append(g)
            merged_shows[key]["genres"] = old_genres

    for item in simkl_items:
        if "show" in item and isinstance(item["show"], dict):
            show_obj = item["show"]
        elif "anime" in item and isinstance(item["anime"], dict):
            show_obj = item["anime"]
        else:
            show_obj = item

        show_ids = show_obj.get("ids") or {}
        simkl_id = str(show_ids.get("simkl") or "")
        mal_id = str(show_ids.get("mal") or "") or None
        anilist_id = str(show_ids.get("anilist") or "") or None
        kitsu_id = str(show_ids.get("kitsu") or "") or None

        title = show_obj.get("title") or ""
        status = normalize_user_status(item.get("list"))
        rating = item.get("user_rating", 0) or 0
        genres = show_obj.get("genres", []) or []

        matched_key = None
        if mal_id and mal_id in merged_shows:
            matched_key = mal_id
        elif anilist_id and f"al_{anilist_id}" in merged_shows:
            matched_key = f"al_{anilist_id}"

        if matched_key:
            merged_shows[matched_key]["simkl_id"] = simkl_id
            merged_shows[matched_key]["rating"] = max(merged_shows[matched_key].get("rating") or 0, rating)

            old_status = merged_shows[matched_key]["status"]
            if old_status == "planning" and status != "planning":
                merged_shows[matched_key]["status"] = status
            elif old_status != "completed" and status == "completed":
                merged_shows[matched_key]["status"] = "completed"

            old_genres = merged_shows[matched_key].get("genres", [])
            for g in genres:
                if g not in old_genres:
                    old_genres.append(g)
            merged_shows[matched_key]["genres"] = old_genres
        else:
            key = (
                mal_id
                if mal_id
                else (f"al_{anilist_id}" if anilist_id else (f"kitsu_{kitsu_id}" if kitsu_id else f"simkl_{simkl_id}"))
            )
            merged_shows[key] = {
                "title": title,
                "mal_id": mal_id,
                "anilist_id": anilist_id,
                "simkl_id": simkl_id,
                "status": status,
                "rating": rating,
                "genres": genres,
            }

    # Watched sets for filtering
    watched_mal_ids = set()
    watched_anilist_ids = set()
    watched_titles = set()

    for show in merged_shows.values():
        if show["status"] == "planning":
            continue

        if show["mal_id"]:
            watched_mal_ids.add(str(show["mal_id"]))
        if show["anilist_id"]:
            watched_anilist_ids.add(str(show["anilist_id"]))
        if show["title"]:
            watched_titles.add(show["title"].lower())

    # Bulk-resolve IDs from fribb_mappings and id_cache to ensure complete cross-tracker filtering
    raw_mal_ids = list(watched_mal_ids)
    raw_al_ids = list(watched_anilist_ids)
    if raw_mal_ids or raw_al_ids:
        # Query fribb_mappings
        fribb_query = []
        if raw_mal_ids:
            fribb_query.append({"mal_id": {"$in": raw_mal_ids}})
        if raw_al_ids:
            fribb_query.append({"anilist_id": {"$in": raw_al_ids}})
        if fribb_query:
            try:
                for doc in db.fribb_mappings.find({"$or": fribb_query}):
                    m_id = doc.get("mal_id")
                    a_id = doc.get("anilist_id")
                    if m_id:
                        watched_mal_ids.add(str(m_id))
                    if a_id:
                        watched_anilist_ids.add(str(a_id))
            except Exception as e:
                logger.warning("Failed to bulk query fribb_mappings for ID resolving: %s", e)

        # Query id_cache
        cache_query = []
        if raw_mal_ids:
            cache_query.append({"mal_id": {"$in": raw_mal_ids}})
        if raw_al_ids:
            cache_query.append({"anilist_id": {"$in": raw_al_ids}})
        if cache_query:
            try:
                for doc in db.get_collection("id_cache").find({"$or": cache_query}):
                    m_id = doc.get("mal_id")
                    a_id = doc.get("anilist_id")
                    if m_id:
                        watched_mal_ids.add(str(m_id))
                    if a_id:
                        watched_anilist_ids.add(str(a_id))
            except Exception as e:
                logger.warning("Failed to bulk query id_cache for ID resolving: %s", e)

    filter_watched = user.get("recommendations_filter_watched", True)

    # Sort history to select seed shows
    sorted_user_history = sorted(
        merged_shows.values(),
        key=lambda x: (1 if x["status"] in ["completed", "watching"] else 0, x["rating"] or 0),
        reverse=True,
    )

    # 3. Generate "Top Picks" (Community Recs)
    top_picks = []
    rec_candidates = {}

    seed_pool = [s for s in merged_shows.values() if s["status"] in ["completed", "watching", "on_hold"]]
    if not seed_pool:
        seed_pool = [s for s in merged_shows.values() if s["status"] == "planning"]
    if len(seed_pool) > 50:
        seed_pool = random.sample(seed_pool, 50)

    sorted_seed_pool = sorted(
        seed_pool, key=lambda x: (1 if x["status"] in ["completed", "watching"] else 0, x["rating"] or 0), reverse=True
    )
    recent_seeds = sorted_seed_pool[:5]
    remaining_pool = [s for s in seed_pool if s not in recent_seeds]
    random_seeds = select_weighted_seeds(remaining_pool, 10)
    top_picks_seeds = recent_seeds + random_seeds

    # AniList Bulk query for top picks seeds
    al_ids = [int(s["anilist_id"]) for s in top_picks_seeds if s["anilist_id"]]
    al_recs = []
    anilist_id_to_title = {str(s["anilist_id"]): s["title"] for s in top_picks_seeds if s.get("anilist_id")}
    if al_ids and user.get("anilist_token") and user.get("anilist_enabled", True):
        al_recs = await get_anilist_recommendations_bulk(user["anilist_token"], al_ids)

    for rec in al_recs:
        media = rec.get("mediaRecommendation")
        if not media:
            continue

        if media.get("status") == "NOT_YET_RELEASED":
            continue

        # Exclude OVA, SPECIAL, MUSIC, TV_SHORT and short durations (<= 5 minutes)
        m_format = media.get("format")
        duration = media.get("duration")
        if m_format in ["OVA", "SPECIAL", "MUSIC", "TV_SHORT"]:
            continue
        if duration is not None and duration <= 5:
            continue

        seed_media = rec.get("media") or {}
        seed_aid = str(seed_media.get("id")) if seed_media.get("id") else None
        seed_title = anilist_id_to_title.get(seed_aid) if seed_aid else None

        aid = str(media.get("id"))
        mid = str(media.get("idMal")) if media.get("idMal") else None

        # Watched filters
        if filter_watched:
            if aid in watched_anilist_ids or (mid and mid in watched_mal_ids):
                continue

        # Year filter
        year = media.get("startDate", {}).get("year")
        if year and (year < rec_year_min or year > rec_year_max):
            continue

        item_type = "movie" if m_format == "MOVIE" else "series"

        # Excluded genres filter
        genres = media.get("genres", []) or []
        excluded_genres = rec_excluded_movie_genres if item_type == "movie" else rec_excluded_series_genres
        if any(g in excluded_genres for g in genres):
            continue

        # Popularity filters
        pop_score = media.get("popularity") or 0
        avg_score = media.get("averageScore") or 0
        if rec_popularity == "mainstream":
            if pop_score < 25000:
                continue
        elif rec_popularity == "gems":
            if pop_score >= 25000 or avg_score < 73:
                continue

        # Choose title based on language
        title_pref = media.get("title", {})
        if rec_language == "ja":
            title = title_pref.get("romaji") or title_pref.get("userPreferred") or title_pref.get("english")
        else:
            title = title_pref.get("english") or title_pref.get("userPreferred") or title_pref.get("romaji")

        if filter_watched and title.lower() in watched_titles:
            continue

        if not is_proper_anime(title):
            continue

        poster = (media.get("coverImage") or {}).get("large") or (media.get("coverImage") or {}).get("medium") or ""
        syn = clean_html(media.get("description") or "")

        key = f"mal:{mid}" if mid else f"anilist:{aid}"
        if key not in rec_candidates:
            rec_candidates[key] = {
                "id": key,
                "type": item_type,
                "name": title,
                "poster": poster,
                "score": rec.get("rating", 1),
                "description": "AniList Community Recommendation.",
                "synopsis": syn,
                "inspired_by_titles": [seed_title] if seed_title else [],
            }
        else:
            rec_candidates[key]["score"] += rec.get("rating", 1)
            if syn and not rec_candidates[key].get("synopsis"):
                rec_candidates[key]["synopsis"] = syn
            if seed_title and seed_title not in rec_candidates[key]["inspired_by_titles"]:
                rec_candidates[key]["inspired_by_titles"].append(seed_title)

    # MAL recommendations query for top 5 MAL shows
    mal_seed_shows = [s for s in top_picks_seeds if s["mal_id"]][:5]
    if mal_seed_shows and user.get("mal_access_token") and user.get("mal_enabled", True):
        tasks = [get_mal_recommendations_for_id(user["mal_access_token"], s["mal_id"]) for s in mal_seed_shows]
        mal_recs_lists = await asyncio.gather(*tasks)

        for s, rec_list in zip(mal_seed_shows, mal_recs_lists):
            seed_title = s["title"]
            for rec in rec_list:
                node = rec.get("node", {})
                mid = str(node.get("id"))
                alt_titles = node.get("alternative_titles") or {}
                if title_lang == "english":
                    title = alt_titles.get("en") or node.get("title") or "Unknown Title"
                elif title_lang == "japanese":
                    title = alt_titles.get("ja") or node.get("title") or "Unknown Title"
                else:
                    title = node.get("title") or alt_titles.get("en") or "Unknown Title"

                if node.get("status") == "not_yet_aired":
                    continue

                # Exclude OVA, SPECIAL, MUSIC and short duration (<= 5 minutes / 300 seconds)
                syn = clean_html(node.get("synopsis") or "")
                m_type = node.get("media_type")
                duration = node.get("average_episode_duration")
                if m_type in ["ova", "special", "music"] or not is_proper_anime(title, syn):
                    continue
                if duration is not None and duration <= 300:
                    continue

                # Watched filters
                if filter_watched:
                    if mid in watched_mal_ids:
                        continue
                    if title.lower() in watched_titles:
                        continue

                # Year filter
                year = node.get("start_season", {}).get("year")
                if year and (year < rec_year_min or year > rec_year_max):
                    continue

                item_type = "movie" if m_type == "movie" else "series"

                # Excluded genres filter
                genres = [g.get("name") for g in node.get("genres", []) if g.get("name")]
                excluded_genres = rec_excluded_movie_genres if item_type == "movie" else rec_excluded_series_genres
                if any(g in excluded_genres for g in genres):
                    continue

                # Popularity filters
                pop_rank = node.get("popularity")
                mean_score = node.get("mean")
                if rec_popularity == "mainstream":
                    if pop_rank and pop_rank > 1200:
                        continue
                elif rec_popularity == "gems":
                    if (pop_rank and pop_rank <= 1200) or (mean_score and mean_score < 7.3):
                        continue

                poster = node.get("main_picture", {}).get("large") or node.get("main_picture", {}).get("medium") or ""
                syn = clean_html(node.get("synopsis") or "")

                key = f"mal:{mid}"
                if key not in rec_candidates:
                    rec_candidates[key] = {
                        "id": key,
                        "type": item_type,
                        "name": title,
                        "poster": poster,
                        "score": rec.get("num_recommendations", 1),
                        "description": "MAL Community Recommendation.",
                        "synopsis": syn,
                        "inspired_by_titles": [seed_title],
                    }
                else:
                    rec_candidates[key]["score"] += rec.get("num_recommendations", 1)
                    if syn and not rec_candidates[key].get("synopsis"):
                        rec_candidates[key]["synopsis"] = syn
                    if seed_title not in rec_candidates[key]["inspired_by_titles"]:
                        rec_candidates[key]["inspired_by_titles"].append(seed_title)

    top_picks = sorted(rec_candidates.values(), key=lambda x: x["score"], reverse=True)
    for tp in top_picks:
        tp.pop("score", None)
        syn = tp.get("synopsis") or ""
        inspired_by = tp.get("inspired_by_titles", [])
        if inspired_by:
            desc = f"Inspired by your history: {', '.join(inspired_by)}."
        else:
            desc = tp.get("description") or "Community Recommendation."
        tp["description"] = f"{desc}  \n\n{syn}" if syn else desc

    # 4. Generate "Because you Watched"
    item_recs = []
    seed_show = None
    seed_candidates = [s for s in seed_pool if (s["rating"] or 0) >= 7 or s["status"] in ["completed", "watching"]]
    if not seed_candidates and seed_pool:
        seed_candidates = seed_pool
    if seed_candidates:
        seed_show = random.choice(seed_candidates)

    if seed_show:
        item_recs = await get_recommendations_for_seeds(
            [seed_show], user, watched_mal_ids, watched_anilist_ids, watched_titles
        )
        for ir in item_recs:
            desc = f"Recommended because you watched {seed_show['title']}."
            syn = ir.get("synopsis") or ""
            ir["description"] = f"{desc}  \n\n{syn}" if syn else desc

    # Fallback default seeds if empty
    if not item_recs:
        item_recs = []
        for fb in fallbacks:
            if len(item_recs) >= 5:
                break

            # Check if watched
            if filter_watched:
                title = fb.get("name", "")
                if title and title.lower() in watched_titles:
                    continue
                fb_id = fb["id"]
                if ":" in fb_id:
                    tracker, ext_id = fb_id.split(":", 1)
                    if tracker == "mal" and ext_id in watched_mal_ids:
                        continue
                    if tracker == "anilist" and ext_id in watched_anilist_ids:
                        continue

            item_copy = fb.copy()
            desc = "Popular trending anime you might enjoy."
            fb_desc = item_copy.get("description") or ""
            item_copy["description"] = f"{desc}  \n\n{fb_desc}" if fb_desc else desc
            item_recs.append(item_copy)
        seed_show = {"title": "Fullmetal Alchemist: Brotherhood"}

    # Filter item_recs for watched shows if filter_watched is True
    if filter_watched and item_recs:
        filtered_item_recs = []
        for ir in item_recs:
            title = ir.get("name", "")
            if title and title.lower() in watched_titles:
                continue
            ir_id = ir.get("id")
            if ir_id and ":" in ir_id:
                tracker, ext_id = ir_id.split(":", 1)
                if tracker == "mal" and ext_id in watched_mal_ids:
                    continue
                if tracker == "anilist" and ext_id in watched_anilist_ids:
                    continue
            filtered_item_recs.append(ir)
        item_recs = filtered_item_recs

    # 5. Generate "Inspired by your Favorites"
    loved_count = 8
    if len(seed_pool) < 16:
        loved_count = max(1, len(seed_pool) // 2)
    loved_seeds = select_weighted_seeds(seed_pool, loved_count)
    loved_items = await get_recommendations_for_seeds(
        loved_seeds, user, watched_mal_ids, watched_anilist_ids, watched_titles
    )
    for lr in loved_items:
        inspired_by = lr.get("inspired_by_titles", [])
        if inspired_by:
            desc = f"Inspired by your favorites: {', '.join(inspired_by)}."
        else:
            desc = "Inspired by your favorites."
        syn = lr.get("synopsis") or ""
        lr["description"] = f"{desc}  \n\n{syn}" if syn else desc
    if not loved_items:
        loved_items = []
        for fb in fallbacks[:5]:
            item_copy = fb.copy()
            desc = "Popular trending anime you might enjoy."
            fb_desc = item_copy.get("description") or ""
            item_copy["description"] = f"{desc}  \n\n{fb_desc}" if fb_desc else desc
            loved_items.append(item_copy)

    # 6. Generate "More from your Watchlist"
    remaining_liked_pool = [s for s in seed_pool if s not in loved_seeds]
    liked_count = 8
    if len(seed_pool) < 16:
        liked_count = len(seed_pool) - len(loved_seeds)
    liked_seeds = select_weighted_seeds(remaining_liked_pool, liked_count)
    liked_items = await get_recommendations_for_seeds(
        liked_seeds, user, watched_mal_ids, watched_anilist_ids, watched_titles
    )
    for lr in liked_items:
        inspired_by = lr.get("inspired_by_titles", [])
        if inspired_by:
            desc = f"Inspired by your watchlist: {', '.join(inspired_by)}."
        else:
            desc = "More from your watchlist."
        syn = lr.get("synopsis") or ""
        lr["description"] = f"{desc}  \n\n{syn}" if syn else desc
    if not liked_items:
        liked_items = []
        for fb in fallbacks[3:8]:
            item_copy = fb.copy()
            desc = "Popular trending anime you might enjoy."
            fb_desc = item_copy.get("description") or ""
            item_copy["description"] = f"{desc}  \n\n{fb_desc}" if fb_desc else desc
            liked_items.append(item_copy)

    # 7. Genre Collections
    genre_counts = {}
    for show in merged_shows.values():
        if show["status"] == "planning":
            continue
        for g in show.get("genres", []):
            genre_counts[g] = genre_counts.get(g, 0) + 1
    sorted_genres = sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)
    fav_genres = [g[0] for g in sorted_genres[:2]]
    while len(fav_genres) < 2:
        for fallback_genre in ["Action", "Adventure", "Comedy", "Fantasy", "Drama"]:
            if fallback_genre not in fav_genres:
                fav_genres.append(fallback_genre)
                if len(fav_genres) >= 2:
                    break

    genre_1_name = fav_genres[0]
    genre_2_name = fav_genres[1]

    genre_1_items = await generate_genre_recommendations(
        genre_1_name, user, watched_mal_ids, watched_anilist_ids, watched_titles
    )
    genre_2_items = await generate_genre_recommendations(
        genre_2_name, user, watched_mal_ids, watched_anilist_ids, watched_titles
    )
    if not genre_1_items:
        genre_1_items = []
        for fb in fallbacks[1:6]:
            item_copy = fb.copy()
            desc = "Popular genre collection."
            fb_desc = item_copy.get("description") or ""
            item_copy["description"] = f"{desc}  \n\n{fb_desc}" if fb_desc else desc
            genre_1_items.append(item_copy)
    if not genre_2_items:
        genre_2_items = []
        for fb in fallbacks[2:7]:
            item_copy = fb.copy()
            desc = "Popular genre collection."
            fb_desc = item_copy.get("description") or ""
            item_copy["description"] = f"{desc}  \n\n{fb_desc}" if fb_desc else desc
            genre_2_items.append(item_copy)

    # 8. Enhance recommendations using Gemini API if key is provided
    gemini_api_key = user.get("gemini_api_key", "").strip()
    if gemini_api_key:
        candidates_by_name = {}
        for item in (
            top_picks[:8] + item_recs[:8] + loved_items[:8] + liked_items[:8] + genre_1_items[:8] + genre_2_items[:8]
        ):
            if item.get("name") and item["name"] not in candidates_by_name:
                candidates_by_name[item["name"]] = item

        if candidates_by_name:
            history_lines = []
            for show in sorted_user_history[:15]:
                status = show["status"].lower() if show["status"] else "watched"
                rating_str = f"rated {show['rating']}/10" if show["rating"] else "no rating"
                history_lines.append(f"- {show['title']} ({status}, {rating_str})")
            history_text = "\n".join(history_lines)
            candidates_text = "\n".join([f"- {name}" for name in candidates_by_name.keys()])

            prompt = f"""
            You are an advanced anime recommendation assistant.
            Based on the user's anime watch history:
            {history_text}

            And this list of candidate anime recommendations:
            {candidates_text}

            For each candidate that is relevant, write a personalized, engaging 1-sentence description explaining why the user would like it based on their history (referencing specific anime they watched when appropriate). Keep descriptions concise (under 150 characters).

            Return your response as a JSON object mapping the exact candidate title to its personalized description:
            {{
              "Anime Title 1": "Description...",
              "Anime Title 2": "Description...",
              ...
            }}
            Return only the raw JSON.
            """
            try:
                models = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-2.5-flash"]
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json"},
                }
                client = get_client()
                resp = None
                for model in models:
                    try:
                        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_api_key}"
                        r = await client.post(url, json=payload, timeout=15)
                        if r.status_code == 200:
                            resp = r
                            logger.info("Successfully generated AI recommendations via %s", model)
                            break
                        else:
                            logger.warning("Gemini model %s returned status %s, attempting next fallback model...", model, r.status_code)
                    except Exception as model_err:
                        logger.warning("Gemini model %s failed (%s), attempting next fallback model...", model, model_err)

                if resp and resp.status_code == 200:
                    res_json = resp.json()
                    text = res_json["candidates"][0]["content"]["parts"][0]["text"]
                    ai_explanations = json.loads(text)

                    def enhance_list(items):
                        enhanced = []
                        others = []
                        for item in items:
                            name = item.get("name")
                            if name in ai_explanations:
                                item_copy = item.copy()
                                ai_desc = ai_explanations[name]
                                syn = item_copy.get("synopsis") or ""
                                item_copy["description"] = f"{ai_desc}  \n\n{syn}" if syn else ai_desc
                                enhanced.append(item_copy)
                            else:
                                others.append(item)
                        return enhanced + others

                    top_picks = enhance_list(top_picks)
                    item_recs = enhance_list(item_recs)
                    loved_items = enhance_list(loved_items)
                    liked_items = enhance_list(liked_items)
                    genre_1_items = enhance_list(genre_1_items)
                    genre_2_items = enhance_list(genre_2_items)
                else:
                    logger.warning("Gemini API call failed with status %s: %s", resp.status_code, resp.text)
            except Exception as e:
                logger.warning("Failed to enhance recommendations with Gemini: %s", e)

    # Deduplicate and pad lists to prevent identical listings across rows
    shown_ids = set()
    watched_titles_filter = watched_titles if filter_watched else set()

    # Helper function to pad a list with popular unique/unwatched anime up to a minimum count
    def pad_catalog(items, fallback_list, shown_ids_set, watched_titles_set, min_count=15, default_desc=None):
        padded_items = []
        for item in items:
            if watched_titles_set:
                title = item.get("name", "")
                if title and title.lower() in watched_titles_set:
                    continue
                item_id = item.get("id")
                if item_id and ":" in item_id:
                    tracker, ext_id = item_id.split(":", 1)
                    if tracker == "mal" and ext_id in watched_mal_ids:
                        continue
                    if tracker == "anilist" and ext_id in watched_anilist_ids:
                        continue
            if item["id"] not in shown_ids_set:
                shown_ids_set.add(item["id"])
                item_copy = item.copy()
                curr_desc = item_copy.get("description", "")
                if default_desc and "\n\n" not in curr_desc:
                    syn = item_copy.get("synopsis") or curr_desc
                    item_copy["description"] = f"{default_desc}  \n\n{syn}" if syn else default_desc
                padded_items.append(item_copy)

        for fb_item in fallback_list:
            if len(padded_items) >= min_count:
                break
            if fb_item["id"] in shown_ids_set:
                continue
            title = fb_item.get("name", "")
            if title and title.lower() in watched_titles_set:
                continue
            fb_id = fb_item["id"]
            if ":" in fb_id:
                tracker, ext_id = fb_id.split(":", 1)
                if tracker == "mal" and ext_id in watched_mal_ids:
                    continue
                if tracker == "anilist" and ext_id in watched_anilist_ids:
                    continue
            shown_ids_set.add(fb_item["id"])
            item_copy = fb_item.copy()
            fb_desc = item_copy.get("synopsis") or item_copy.get("description") or ""
            if default_desc:
                item_copy["description"] = f"{default_desc}  \n\n{fb_desc}" if fb_desc else default_desc
            padded_items.append(item_copy)

        # Second pass safety fallback (allow reuse of shown_ids across catalogs if we could not satisfy min_count)
        if len(padded_items) < min_count:
            for fb_item in fallback_list:
                if len(padded_items) >= min_count:
                    break
                # Avoid duplicate within the same row
                if any(x["id"] == fb_item["id"] for x in padded_items):
                    continue
                title = fb_item.get("name", "")
                if title and title.lower() in watched_titles_set:
                    continue
                fb_id = fb_item["id"]
                if ":" in fb_id:
                    tracker, ext_id = fb_id.split(":", 1)
                    if tracker == "mal" and ext_id in watched_mal_ids:
                        continue
                    if tracker == "anilist" and ext_id in watched_anilist_ids:
                        continue
                item_copy = fb_item.copy()
                fb_desc = item_copy.get("synopsis") or item_copy.get("description") or ""
                if default_desc:
                    item_copy["description"] = f"{default_desc}  \n\n{fb_desc}" if fb_desc else default_desc
                padded_items.append(item_copy)
        return padded_items

    # 1. Deduplicate & pad Top Picks
    top_picks = pad_catalog(
        top_picks,
        fallbacks,
        shown_ids,
        watched_titles_filter,
        min_count=15,
        default_desc="Popular community recommendation.",
    )
    # 2. Deduplicate & pad Loved Items
    loved_items = pad_catalog(
        loved_items,
        fallbacks,
        shown_ids,
        watched_titles_filter,
        min_count=15,
        default_desc="Popular trending anime you might enjoy.",
    )
    # 3. Deduplicate & pad Liked Items
    liked_items = pad_catalog(
        liked_items,
        fallbacks,
        shown_ids,
        watched_titles_filter,
        min_count=15,
        default_desc="Popular trending anime you might enjoy.",
    )
    # 4. Deduplicate & pad Genre Items
    genre_1_items = pad_catalog(
        genre_1_items,
        fallbacks,
        shown_ids,
        watched_titles_filter,
        min_count=15,
        default_desc=f"Popular {genre_1_name} collection.",
    )
    genre_2_items = pad_catalog(
        genre_2_items,
        fallbacks,
        shown_ids,
        watched_titles_filter,
        min_count=15,
        default_desc=f"Popular {genre_2_name} collection.",
    )

    # Enforce sorting order preference (Default, Series First, Movies First)
    def apply_sorting_order(metas):
        if rec_sorting_order == "series_first":
            return sorted(metas, key=lambda x: 0 if x.get("type") == "series" else 1)
        elif rec_sorting_order == "movies_first":
            return sorted(metas, key=lambda x: 0 if x.get("type") == "movie" else 1)
        return metas

    top_picks = apply_sorting_order(top_picks)[:30]
    item_recs = apply_sorting_order(item_recs)[:30]
    loved_items = apply_sorting_order(loved_items)[:30]
    liked_items = apply_sorting_order(liked_items)[:30]
    genre_1_items = apply_sorting_order(genre_1_items)[:30]
    genre_2_items = apply_sorting_order(genre_2_items)[:30]

    # Save to database
    recommendations_cache_collection.update_one(
        {"uid": user_id},
        {
            "$set": {
                "uid": user_id,
                "rec_items": top_picks,
                "item_items": item_recs,
                "item_seed_title": seed_show["title"] if seed_show else "Steins;Gate",
                "loved_items": loved_items,
                "liked_items": liked_items,
                "genre_1_items": genre_1_items,
                "genre_1_name": genre_1_name,
                "genre_2_items": genre_2_items,
                "genre_2_name": genre_2_name,
                "last_updated": datetime.datetime.utcnow(),
            }
        },
        upsert=True,
    )
    logger.info("Successfully updated recommendations cache for user %s", user_id)


def get_cached_recommendations(user_id: str) -> dict | None:
    return recommendations_cache_collection.find_one({"uid": user_id})


def trigger_recommendation_update_background(user_id: str, force: bool = False):
    user = get_user(user_id)
    if not user or not user.get("enable_recommendations", True):
        return
    asyncio.create_task(update_recommendations_cache(user_id, force=force))


popular_fallbacks_collection = db.get_collection("popular_fallbacks")


def get_popular_fallbacks() -> list[dict]:
    """Retrieve fallback list from database cache, or fallback to the static list if empty."""
    try:
        cached = list(popular_fallbacks_collection.find({}, {"_id": 0}))
        if cached and len(cached) >= 15:
            return cached
    except Exception as e:
        logger.error("Failed to read popular fallbacks from MongoDB: %s", e)
    return POPULAR_FALLBACKS


async def update_popular_fallbacks_cache():
    """Fetch the top 80 most popular anime from AniList and cache them in MongoDB."""
    query = """
    query {
      Page(page: 1, perPage: 80) {
        media(type: ANIME, sort: POPULARITY_DESC, isAdult: false) {
          id
          idMal
          status
          format
          duration
          title {
            english
            romaji
            userPreferred
          }
          coverImage {
            large
          }
          description
        }
      }
    }
    """
    try:
        logger.info("Updating popular fallbacks cache from AniList...")
        res = await anilist_api._gql(None, query)
        data = res.get("data", {}).get("Page", {}).get("media", [])
        if data:
                import re

                new_items = []
                for media in data:
                    if media.get("status") == "NOT_YET_RELEASED":
                        continue
                    # Exclude OVA, SPECIAL, MUSIC, TV_SHORT from popular fallbacks and short durations (<= 5 minutes)
                    m_format = media.get("format")
                    duration = media.get("duration")
                    if m_format in ["OVA", "SPECIAL", "MUSIC", "TV_SHORT"]:
                        continue
                    if duration is not None and duration <= 5:
                        continue
                    mal_id = media.get("idMal")
                    item_id = f"mal:{mal_id}" if mal_id else f"anilist:{media.get('id')}"
                    item_type = "movie" if m_format == "MOVIE" else "series"
                    title_pref = media.get("title", {})
                    name = title_pref.get("english") or title_pref.get("userPreferred") or title_pref.get("romaji")
                    if not is_proper_anime(name):
                        continue
                    poster = (media.get("coverImage") or {}).get("large") or ""
                    desc = media.get("description") or ""
                    desc = re.sub("<[^<]+?>", "", desc)
                    desc = desc[:150] + "..." if len(desc) > 150 else desc
                    desc = desc.replace("\n", " ").replace("  ", " ").strip()
                    new_items.append(
                        {"id": item_id, "type": item_type, "name": name, "poster": poster, "description": desc}
                    )
                if new_items:
                    # Wipe and insert
                    popular_fallbacks_collection.delete_many({})
                    popular_fallbacks_collection.insert_many(new_items)
                    logger.info("Successfully cached %d popular fallbacks from AniList.", len(new_items))
                    return
        logger.warning("AniList returned empty data for popular fallbacks, attempting Jikan fallback...")
    except Exception as e:
        logger.error("Failed to update popular fallbacks cache from AniList: %s, trying Jikan...", e)

    try:
        from app.api.jikan import get_top_anime
        jikan_top = await get_top_anime(type_filter="tv", page=1)
        if jikan_top:
            import re
            new_items = []
            for item in jikan_top[:40]:
                mal_id = item.get("mal_id")
                name = item.get("title_english") or item.get("title") or "Unknown Anime"
                desc = item.get("synopsis") or ""
                desc = re.sub("<[^<]+?>", "", desc)
                desc = desc[:150] + "..." if len(desc) > 150 else desc
                desc = desc.replace("\n", " ").replace("  ", " ").strip()
                images = item.get("images", {}).get("jpg", {})
                poster = images.get("large_image_url") or images.get("image_url") or ""
                if mal_id:
                    new_items.append({
                        "id": f"mal:{mal_id}",
                        "type": "series",
                        "name": name,
                        "poster": poster,
                        "description": desc
                    })
            if new_items:
                popular_fallbacks_collection.delete_many({})
                popular_fallbacks_collection.insert_many(new_items)
                logger.info("Successfully cached %d popular fallbacks from Jikan API.", len(new_items))
    except Exception as ex:
        logger.error("Failed to update popular fallbacks from Jikan fallback: %s", ex)


async def popular_fallbacks_loop():
    """Background loop to update popular fallbacks once every 24 hours."""
    await asyncio.sleep(5)  # Wait for app startup
    while True:
        try:
            await update_popular_fallbacks_cache()
        except Exception as e:
            logger.error("Error in popular fallbacks loop: %s", e)
        await asyncio.sleep(24 * 3600)


def trigger_popular_fallbacks_update_background():
    """Start the background popular fallbacks updater task."""
    asyncio.create_task(popular_fallbacks_loop())
