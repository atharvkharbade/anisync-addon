import logging
from datetime import UTC, datetime, timedelta

import httpx

from app.services.db import db

from .constants import FRIBB_API


def clean_id(val) -> str | None:
    if not val:
        return None
    if isinstance(val, list):
        val = val[0] if val else None
    if not val:
        return None
    val = str(val).strip()
    if val.startswith("[") and val.endswith("]"):
        import ast

        try:
            lst = ast.literal_eval(val)
            if isinstance(lst, list) and len(lst) > 0:
                val = str(lst[0]).strip()
        except Exception:
            val = val.strip("[]'\" ")
    return val.strip("'\" ")


async def ensure_fribb_mappings(client: httpx.AsyncClient):
    """
    Ensures that the fribb_mappings collection has been populated and updated
    within the last 24 hours. Does an atomic swap to avoid query disruption.
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    force_rebuild = False
    try:
        # Check if we need to force rebuild to upgrade old schema lacking external IDs
        if db.fribb_mappings.count_documents({}) > 0:
            if db.fribb_mappings.count_documents({"imdb_id": {"$exists": True}}) == 0:
                logging.info("Forcing rebuild of Fribb mappings to upgrade schema (adding imdb_id, tmdb_id, tvdb_id)")
                force_rebuild = True
    except Exception as e:
        logging.error("Failed to check Fribb mappings schema status: %s", e)

    try:
        if not force_rebuild:
            meta = db.fribb_meta.find_one({"key": "last_updated"})
            if meta and (now - meta["timestamp"]) < timedelta(hours=24):
                return
    except Exception as e:
        logging.error("Failed to query Fribb metadata: %s", e)

    # Acquire distributed lock in MongoDB to prevent multi-worker races
    try:
        # Check for active rebuild lock (not stale)
        lock = db.fribb_meta.find_one({"key": "rebuild_lock"})
        if lock:
            locked_at = lock.get("locked_at")
            if locked_at and (now - locked_at) < timedelta(minutes=10):
                logging.info("Another worker is currently rebuilding Fribb mappings. Skipping.")
                return
            else:
                logging.warning("Stale Fribb rebuild lock detected. Removing it.")
                db.fribb_meta.delete_one({"key": "rebuild_lock"})

        # Atomically acquire rebuild lock
        res = db.fribb_meta.find_one_and_update(
            {"key": "rebuild_lock"},
            {"$setOnInsert": {"key": "rebuild_lock", "locked_at": now}},
            upsert=True,
            return_document=False,
        )
        if res is not None:
            logging.info("Another worker acquired the Fribb rebuild lock. Skipping.")
            return
    except Exception as e:
        logging.error("Failed to acquire Fribb rebuild lock: %s", e)
        return

    try:
        # Recheck last_updated after acquiring lock
        try:
            if not force_rebuild:
                meta = db.fribb_meta.find_one({"key": "last_updated"})
                if meta and (datetime.now(UTC).replace(tzinfo=None) - meta["timestamp"]) < timedelta(hours=24):
                    return
        except Exception:
            pass

        logging.info("Updating local Fribb mappings database cache...")
        try:
            resp = await client.get(FRIBB_API, timeout=15)
            resp.raise_for_status()
            entries = resp.json()

            docs = []

            for entry in entries:
                kitsu_id = entry.get("kitsu_id")
                if kitsu_id is not None:
                    mal_id = entry.get("mal_id")
                    anilist_id = entry.get("anilist_id")
                    simkl_id = entry.get("simkl_id")
                    imdb_id = entry.get("imdb_id")
                    tvdb_id = entry.get("tvdb_id")

                    tmdb_id = None
                    tmdb_obj = entry.get("themoviedb_id")
                    if tmdb_obj:
                        if isinstance(tmdb_obj, dict):
                            tmdb_id = tmdb_obj.get("tv") or tmdb_obj.get("movie")
                        else:
                            tmdb_id = tmdb_obj

                    docs.append(
                        {
                            "kitsu_id": int(kitsu_id),
                            "mal_id": str(mal_id) if mal_id is not None else None,
                            "anilist_id": str(anilist_id) if anilist_id is not None else None,
                            "simkl_id": str(simkl_id) if simkl_id is not None else None,
                            "imdb_id": clean_id(imdb_id),
                            "tmdb_id": clean_id(tmdb_id),
                            "tvdb_id": clean_id(tvdb_id),
                        }
                    )

            if docs:
                temp_coll = db.get_collection("fribb_mappings_temp")
                temp_coll.drop()
                temp_coll.insert_many(docs)
                temp_coll.create_index("kitsu_id")
                temp_coll.create_index("mal_id")
                temp_coll.create_index("anilist_id")
                temp_coll.create_index("simkl_id")
                temp_coll.create_index("imdb_id")

                # Swap collections atomically
                temp_coll.rename("fribb_mappings", dropTarget=True)

                db.fribb_meta.update_one(
                    {"key": "last_updated"}, {"$set": {"timestamp": datetime.now(UTC).replace(tzinfo=None)}}, upsert=True
                )
                logging.info("Successfully updated Fribb mappings with %d entries.", len(docs))
        except Exception as e:
            logging.error("Failed to update Fribb mappings cache: %s", e)
    finally:
        try:
            db.fribb_meta.delete_one({"key": "rebuild_lock"})
        except Exception as e:
            logging.error("Failed to release Fribb rebuild lock: %s", e)
