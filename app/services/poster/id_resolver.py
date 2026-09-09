import asyncio
import logging
from datetime import datetime, timedelta

from app.services.db import cache_ids, db, id_cache_collection
from app.services.http import get_client


def resolve_media_ids(
    kitsu_id: str | None = None,
    mal_id: str | None = None,
    anilist_id: str | None = None,
    simkl_id: str | None = None,
    resolved_ids: dict | None = None,
    imdb_id: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """
    Look up IMDb, TMDB, and TVDB IDs from local caches (id_cache and fribb_mappings).
    Mutates resolved_ids in-place if provided and returns (imdb_id, tmdb_id, tvdb_id).
    """
    tmdb_id = resolved_ids.get("tmdb_id") if resolved_ids else None
    tvdb_id = resolved_ids.get("tvdb_id") if resolved_ids else None
    if not imdb_id and resolved_ids:
        imdb_id = resolved_ids.get("imdb_id")

    query = []
    if kitsu_id:
        try:
            query.append({"kitsu_id": int(kitsu_id)})
        except (ValueError, TypeError):
            pass
    if mal_id:
        query.append({"mal_id": str(mal_id)})
        if str(mal_id).isdigit():
            query.append({"mal_id": int(mal_id)})
    if anilist_id:
        query.append({"anilist_id": str(anilist_id)})
        if str(anilist_id).isdigit():
            query.append({"anilist_id": int(anilist_id)})
    if simkl_id:
        query.append({"simkl_id": str(simkl_id)})
        if str(simkl_id).isdigit():
            query.append({"simkl_id": int(simkl_id)})
            query.append({"simkl": int(simkl_id)})

    # 1. Check primary id_cache
    if query and not (imdb_id and tmdb_id and tvdb_id):
        try:
            doc = id_cache_collection.find_one({"$or": query})
            if doc:
                imdb_id = imdb_id or doc.get("imdb_id")
                tmdb_id = tmdb_id or doc.get("tmdb_id")
                tvdb_id = tvdb_id or doc.get("tvdb_id")

                if isinstance(imdb_id, list):
                    imdb_id = imdb_id[0] if imdb_id else None
                if isinstance(tmdb_id, dict):
                    tmdb_id = tmdb_id.get("tv") or tmdb_id.get("movie")
                if isinstance(tvdb_id, list):
                    tvdb_id = tvdb_id[0] if tvdb_id else None
        except Exception as e:
            logging.error("Failed to query id_cache for poster resolution: %s", e)

    # 2. Check fribb_mappings next (offline database with 15k+ entries)
    if not (imdb_id or tmdb_id or tvdb_id):
        fribb_query = []
        if kitsu_id:
            try:
                fribb_query.append({"kitsu_id": int(kitsu_id)})
            except (ValueError, TypeError):
                pass
        if mal_id:
            fribb_query.append({"mal_id": str(mal_id)})
            if str(mal_id).isdigit():
                fribb_query.append({"mal_id": int(mal_id)})
        if anilist_id:
            fribb_query.append({"anilist_id": str(anilist_id)})
            if str(anilist_id).isdigit():
                fribb_query.append({"anilist_id": int(anilist_id)})
        if simkl_id:
            fribb_query.append({"simkl_id": str(simkl_id)})
            if str(simkl_id).isdigit():
                fribb_query.append({"simkl_id": int(simkl_id)})

        if fribb_query:
            try:
                doc = db.fribb_mappings.find_one({"$or": fribb_query})
                if doc:
                    imdb_id = doc.get("imdb_id")
                    tmdb_id = doc.get("tmdb_id")
                    tvdb_id = doc.get("tvdb_id")

                    if isinstance(imdb_id, list):
                        imdb_id = imdb_id[0] if imdb_id else None
                    if isinstance(tmdb_id, dict):
                        tmdb_id = tmdb_id.get("tv") or tmdb_id.get("movie")
                    if isinstance(tvdb_id, list):
                        tvdb_id = tvdb_id[0] if tvdb_id else None

                    if imdb_id or tmdb_id or tvdb_id:
                        cache_ids(
                            kitsu_id=kitsu_id or doc.get("kitsu_id"),
                            mal_id=mal_id or doc.get("mal_id"),
                            anilist_id=anilist_id or doc.get("anilist_id"),
                            simkl_id=simkl_id or doc.get("simkl_id"),
                            imdb_id=imdb_id,
                            tmdb_id=tmdb_id,
                            tvdb_id=tvdb_id,
                        )
            except Exception as e:
                logging.error("Failed to query fribb_mappings for poster resolution: %s", e)

    if resolved_ids is not None:
        if imdb_id:
            resolved_ids["imdb_id"] = imdb_id
        if tmdb_id:
            resolved_ids["tmdb_id"] = tmdb_id
        if tvdb_id:
            resolved_ids["tvdb_id"] = tvdb_id

    return imdb_id, tmdb_id, tvdb_id


async def background_resolve_external_ids(
    kitsu_id: str | None = None, mal_id: str | None = None, anilist_id: str | None = None
):
    """
    Query api.ani.zip in the background and cache external IDs (IMDb, TMDB, TVDB).
    """
    query = {}
    if kitsu_id:
        query["kitsu_id"] = int(kitsu_id)
    elif mal_id:
        query["mal_id"] = str(mal_id)
    elif anilist_id:
        query["anilist_id"] = str(anilist_id)

    if not query:
        return

    try:
        doc = id_cache_collection.find_one(query)
        if doc:
            if doc.get("imdb_id") or doc.get("tmdb_id") or doc.get("tvdb_id"):
                return  # Already resolved

            last_attempt = doc.get("last_attempt")
            if last_attempt and (datetime.utcnow() - last_attempt) < timedelta(days=1):
                return  # Throttling repeated requests for unmappable IDs
    except Exception as e:
        logging.error("Failed to query id_cache in background: %s", e)

    k_id = str(kitsu_id) if kitsu_id else ""
    m_id = str(mal_id) if mal_id else ""
    a_id = str(anilist_id) if anilist_id else ""

    # 1. Resolve kitsu_id to mal_id/anilist_id first if we only have kitsu_id
    if k_id and not (m_id or a_id):
        try:
            doc = id_cache_collection.find_one({"kitsu_id": int(k_id)})
            if doc:
                m_id = doc.get("mal_id") or ""
                a_id = doc.get("anilist_id") or ""
        except Exception:
            pass

        if not (m_id or a_id):
            from app.lib.id_resolver import resolve

            try:
                m_id_res, a_id_res = await resolve(k_id)
                m_id = str(m_id_res) if m_id_res else ""
                a_id = str(a_id_res) if a_id_res else ""
            except Exception as e:
                logging.warning("Failed to resolve kitsu_id=%s to mal/anilist in background: %s", k_id, e)

    # 2. Conversely, try resolving mal_id/anilist_id to kitsu_id to facilitate relationships tracing if needed
    if m_id and not k_id:
        from app.lib.id_resolver import resolve_mal_to_kitsu

        try:
            k_id_res = await resolve_mal_to_kitsu(m_id)
            k_id = str(k_id_res) if k_id_res else ""
        except Exception:
            pass
    elif a_id and not k_id:
        from app.lib.id_resolver import resolve_anilist_to_kitsu

        try:
            k_id_res = await resolve_anilist_to_kitsu(a_id)
            k_id = str(k_id_res) if k_id_res else ""
        except Exception:
            pass

    imdb_id = ""
    tmdb_id = ""
    tvdb_id = ""

    # 3. Query api.ani.zip mappings endpoint using mal_id or anilist_id (since kitsu_id is not supported)
    url = "https://api.ani.zip/mappings"
    params = {}
    if a_id:
        params["anilist_id"] = a_id
    elif m_id:
        params["mal_id"] = m_id

    if params:
        try:
            client = get_client()
            resp = await client.get(url, params=params, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                mappings = data.get("mappings", {})
                k_id = str(mappings.get("kitsu_id") or k_id or "")
                m_id = str(mappings.get("mal_id") or m_id or "")
                a_id = str(mappings.get("anilist_id") or a_id or "")
                imdb_id = str(mappings.get("imdb_id") or "")
                tmdb_id = str(mappings.get("themoviedb_id") or "")
                tvdb_id = str(mappings.get("thetvdb_id") or "")
        except Exception as e:
            logging.warning("Failed to background resolve external IDs from ani.zip: %s", e)

    # 4. Trace relationships on Kitsu if we still lack external ID mappings
    if not (imdb_id or tmdb_id or tvdb_id) and k_id:
        try:
            client = get_client()
            kitsu_url = f"https://kitsu.io/api/edge/anime/{k_id}/media-relationships?include=destination"
            kitsu_resp = await client.get(kitsu_url, timeout=8)
            if kitsu_resp.status_code == 200:
                rel_data = kitsu_resp.json()
                dest_ids = []
                # Prefer prequel, parent, full_story, etc.
                for rel in rel_data.get("data", []):
                    role = rel.get("attributes", {}).get("role")
                    if role in ["prequel", "parent", "full_story", "alternative_setting", "main_story", "parent_story"]:
                        rel_link = rel.get("relationships", {}).get("destination", {}).get("data", {})
                        if rel_link and rel_link.get("type") == "anime":
                            dest_ids.append(str(rel_link.get("id")))
                # Try alternative roles
                if not dest_ids:
                    for rel in rel_data.get("data", []):
                        rel_link = rel.get("relationships", {}).get("destination", {}).get("data", {})
                        if rel_link and rel_link.get("type") == "anime":
                            dest_ids.append(str(rel_link.get("id")))

                for dest_id in dest_ids:
                    doc = id_cache_collection.find_one({"kitsu_id": int(dest_id)})
                    if not doc:
                        doc = db.fribb_mappings.find_one({"kitsu_id": int(dest_id)})

                    if doc and (doc.get("imdb_id") or doc.get("tmdb_id") or doc.get("tvdb_id")):
                        imdb_id = doc.get("imdb_id") or ""
                        tmdb_id = doc.get("tmdb_id") or ""
                        tvdb_id = doc.get("tvdb_id") or ""
                        logging.info(
                            "Resolved external IDs for kitsu=%s via related kitsu=%s: imdb=%s tmdb=%s tvdb=%s",
                            k_id,
                            dest_id,
                            imdb_id,
                            tmdb_id,
                            tvdb_id,
                        )
                        break
        except Exception as ex:
            logging.warning("Failed to resolve via Kitsu relationships for kitsu=%s: %s", k_id, ex)

    # 5. Cache the resolved/mapped IDs
    if k_id:
        cache_ids(
            kitsu_id=k_id,
            mal_id=m_id or None,
            anilist_id=a_id or None,
            imdb_id=imdb_id or None,
            tmdb_id=tmdb_id or None,
            tvdb_id=tvdb_id or None,
        )
        try:
            id_cache_collection.update_one({"kitsu_id": int(k_id)}, {"$set": {"last_attempt": datetime.utcnow()}})
        except Exception:
            pass
        logging.info("Cached external IDs for kitsu=%s: imdb=%s tmdb=%s tvdb=%s", k_id, imdb_id, tmdb_id, tvdb_id)


def trigger_background_resolution(
    kitsu_id: str | None = None, mal_id: str | None = None, anilist_id: str | None = None
):
    """
    Safely schedule background_resolve_external_ids if an event loop is running.
    """
    if not (kitsu_id or mal_id or anilist_id):
        return
    try:
        loop = asyncio.get_event_loop()
        if loop and loop.is_running():
            asyncio.create_task(
                background_resolve_external_ids(kitsu_id=kitsu_id, mal_id=mal_id, anilist_id=anilist_id)
            )
    except Exception:
        pass
