#!/usr/bin/env python3
"""
scripts/audit_id_cache.py

Audits MongoDB id_cache for poisoned or duplicate mappings against local authoritative Fribb mappings,
with optional online ARM verification and healing (deletion of corrupted documents).

Usage:
    python3 scripts/audit_id_cache.py              # Offline dry run (default)
    python3 scripts/audit_id_cache.py --heal       # Heal (delete) conflicting records
    python3 scripts/audit_id_cache.py --online     # Include rate-limited ARM checks
"""

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.db import db, id_cache_collection
from app.services.http import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def audit_offline(heal: bool = False) -> tuple[list[dict], list[dict]]:
    logging.info("Starting offline audit of id_cache against local fribb_mappings...")
    fribb = db.get_collection("fribb_mappings")

    conflicts = []
    duplicates = []

    # 1. Check for duplicate kitsu_id entries in id_cache
    pipeline = [
        {"$group": {"_id": "$kitsu_id", "count": {"$sum": 1}, "docs": {"$push": "$_id"}}},
        {"$match": {"count": {"$gt": 1}}},
    ]
    dupe_groups = list(id_cache_collection.aggregate(pipeline))
    for dg in dupe_groups:
        kid = dg["_id"]
        count = dg["count"]
        doc_ids = dg["docs"]
        logging.warning("Duplicate documents found for kitsu_id=%s (count=%d)", kid, count)
        duplicates.append({"kitsu_id": kid, "count": count, "doc_ids": doc_ids})
        if heal:
            # Keep the newest/first, delete extra duplicates
            for extra_id in doc_ids[1:]:
                id_cache_collection.delete_one({"_id": extra_id})
            logging.info("Healed duplicates for kitsu_id=%s (removed %d extra docs)", kid, count - 1)

    # 1b. Report MAL/AniList IDs claimed by more than one kitsu_id. Lookups by mal_id/anilist_id use
    # find_one, so these silently return an arbitrary doc. Report-only: the conflict pass below
    # (or --online) decides which doc is wrong.
    for field in ("mal_id", "anilist_id"):
        shared = id_cache_collection.aggregate(
            [
                {"$match": {field: {"$nin": [None, ""]}}},
                {"$group": {"_id": f"${field}", "kitsu_ids": {"$addToSet": "$kitsu_id"}}},
                {"$match": {"kitsu_ids.1": {"$exists": True}}},
            ]
        )
        for grp in shared:
            logging.warning("%s=%s is cached on multiple kitsu_ids: %s", field, grp["_id"], grp["kitsu_ids"])

    # 2. Check for conflicts against Fribb mappings
    cursor = id_cache_collection.find({"kitsu_id": {"$ne": None}})
    total_checked = 0
    fribb_matched = 0

    for doc in cursor:
        total_checked += 1
        kid = doc.get("kitsu_id")
        cached_mal = str(doc.get("mal_id") or "") or None
        cached_al = str(doc.get("anilist_id") or "") or None

        fdoc = fribb.find_one({"kitsu_id": int(kid)}) if kid else None
        if not fdoc:
            continue

        fribb_matched += 1
        f_mal = str(fdoc.get("mal_id") or "") or None
        f_al = str(fdoc.get("anilist_id") or "") or None

        mal_conflict = cached_mal and f_mal and cached_mal != f_mal
        al_conflict = cached_al and f_al and cached_al != f_al

        if mal_conflict or al_conflict:
            conflict_entry = {
                "kitsu_id": kid,
                "_id": doc["_id"],
                "cached": {"mal_id": cached_mal, "anilist_id": cached_al},
                "fribb": {"mal_id": f_mal, "anilist_id": f_al},
                "is_fallback": doc.get("is_fallback", False),
            }
            conflicts.append(conflict_entry)
            logging.warning(
                "CONFLICT kitsu_id=%s: cached(mal=%s, al=%s) vs authoritative fribb(mal=%s, al=%s)",
                kid,
                cached_mal,
                cached_al,
                f_mal,
                f_al,
            )
            if heal:
                id_cache_collection.delete_one({"_id": doc["_id"]})
                logging.info("Healed: deleted poisoned document for kitsu_id=%s", kid)

    logging.info(
        "Offline audit complete. Checked %d documents (%d covered by Fribb). Found %d conflicts, %d duplicate groups.",
        total_checked,
        fribb_matched,
        len(conflicts),
        len(duplicates),
    )
    return conflicts, duplicates


async def audit_online(heal: bool = False):
    from app.lib.id_resolver.constants import ARM_API, TIMEOUT

    client = get_client()
    fribb = db.get_collection("fribb_mappings")

    cursor = id_cache_collection.find({"kitsu_id": {"$ne": None}})
    arm_conflicts = []

    for doc in cursor:
        kid = doc.get("kitsu_id")
        # Only check ARM for documents not verified by Fribb
        if fribb.find_one({"kitsu_id": int(kid)}):
            continue

        cached_mal = str(doc.get("mal_id") or "") or None
        cached_al = str(doc.get("anilist_id") or "") or None

        try:
            resp = await client.get(
                ARM_API,
                params={"source": "kitsu", "id": str(kid), "include": "anilist,myanimelist"},
                timeout=TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data:
                    arm_mal = str(data["myanimelist"]) if data.get("myanimelist") else None
                    arm_al = str(data["anilist"]) if data.get("anilist") else None

                    mal_conflict = cached_mal and arm_mal and cached_mal != arm_mal
                    al_conflict = cached_al and arm_al and cached_al != arm_al

                    if mal_conflict or al_conflict:
                        logging.warning(
                            "ARM CONFLICT kitsu_id=%s: cached(mal=%s, al=%s) vs arm(mal=%s, al=%s)",
                            kid,
                            cached_mal,
                            cached_al,
                            arm_mal,
                            arm_al,
                        )
                        arm_conflicts.append({"kitsu_id": kid, "cached": (cached_mal, cached_al), "arm": (arm_mal, arm_al)})
                        if heal:
                            id_cache_collection.delete_one({"_id": doc["_id"]})
                            logging.info("Healed: deleted poisoned document for kitsu_id=%s", kid)
            # Rate limit politeness
            await asyncio.sleep(0.5)
        except Exception as e:
            logging.debug("ARM check failed for kitsu_id=%s: %s", kid, e)

    logging.info("Online ARM audit complete. Found %d conflicts.", len(arm_conflicts))
    return arm_conflicts


def main():
    parser = argparse.ArgumentParser(description="Audit and heal id_cache against authoritative mappings.")
    parser.add_argument("--heal", action="store_true", help="Delete conflicting records so they re-resolve authoritatively.")
    parser.add_argument("--online", action="store_true", help="Include online rate-limited ARM check for unmapped records.")
    args = parser.parse_args()

    mode = "HEAL (DELETE CORRUPTED)" if args.heal else "DRY-RUN (REPORT ONLY)"
    print("\n=======================================================")
    print(f"  ANISYNC ID_CACHE AUDIT & HEALING SCRIPT - {mode}")
    print("=======================================================\n")

    conflicts, duplicates = audit_offline(heal=args.heal)

    if args.online:
        asyncio.run(audit_online(heal=args.heal))

    print("\n---------------- SUMMARY ----------------")
    print(f"Conflicts detected: {len(conflicts)}")
    print(f"Duplicate groups:   {len(duplicates)}")
    if not args.heal and (conflicts or duplicates):
        print("\n[ACTION REQUIRED] Run with --heal to delete corrupted records.")
    elif args.heal:
        print("\n[SUCCESS] Corrupted records successfully removed.")
    print("-----------------------------------------\n")


if __name__ == "__main__":
    main()
