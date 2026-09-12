import logging

from app.api import anilist as anilist_api
from app.services.http import get_client
from config import Config

logger = logging.getLogger(__name__)


async def get_mal_recommendations_for_id(token: str | None, mal_id: str) -> list[dict]:
    items = []
    if token:
        client = get_client()
        url = f"{Config.MAL_API_URL}/anime/{mal_id}"
        params = {
            "fields": "recommendations{node{id,title,main_picture,genres,start_season,media_type,popularity,mean,synopsis,average_episode_duration,status,num_episodes}}"
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
                                "title": title or f"Anime #{mid}",
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
            bannerImage
            startDate {
              year
            }
            genres
            format
            duration
            episodes
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
          bannerImage
          startDate {
            year
          }
          genres
          format
          duration
          episodes
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
