def get_effective_meta_providers(user: dict | None) -> dict:
    pref = (user.get("metadata_provider", "kitsu") or "kitsu").lower() if user else "kitsu"
    if pref == "custom":
        legacy_artwork = (user.get("meta_artwork_provider") or "anilist").lower()
        return {
            "synopsis": (user.get("meta_synopsis_provider") or "kitsu").lower(),
            "episodes": (user.get("meta_episodes_provider") or "anizp").lower(),
            "poster": (user.get("meta_poster_provider") or legacy_artwork).lower(),
            "backdrop": (user.get("meta_backdrop_provider") or "fanart").lower(),
            "airing": (user.get("meta_airing_provider") or "anilist").lower(),
        }
    elif pref == "mal":
        return {
            "synopsis": "mal",
            "episodes": "anizp",
            "poster": "mal",
            "backdrop": "fanart",
            "airing": "mal",
        }
    elif pref == "anilist":
        return {
            "synopsis": "anilist",
            "episodes": "anizp",
            "poster": "anilist",
            "backdrop": "fanart",
            "airing": "anilist",
        }
    else:  # kitsu
        return {
            "synopsis": "kitsu",
            "episodes": "anizp",
            "poster": "kitsu",
            "backdrop": "fanart",
            "airing": "anilist",
        }


def get_al_cover(aid: str | int | None) -> str:
    """Convenience accessor to retrieve cached AniList cover image URL."""
    from app.services.db import get_al_cover as _get_al_cover

    return _get_al_cover(aid)


def clean_imdb_id(val) -> str | None:
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
    if val and not val.startswith("tt") and val.isdigit():
        val = f"tt{val}"
    return val if (val and val.startswith("tt")) else None


def enrich_catalog_metas_artwork(metas: list[dict], user: dict | None = None) -> None:
    """Enriches catalog preview items with native 16:9 widescreen `background` and transparent `logo`
    for optimal rendering in modern clients like Nuvio (Hero catalog & Landscape poster mode).

    Uses zero-external-latency cached sources:
    - Background: AniList bannerImage, Kitsu coverImage, Simkl fanart, AniZip Fanart, Cinemeta background, Metahub background.
    - Logo: AniZip Clearlogo/Logo, Cinemeta logo, Metahub logo.

    Never falls back to stretching portrait posters (which causes pixelation/distortion).
    """
    if not metas:
        return

    import logging
    from app.services.db import db

    effective = get_effective_meta_providers(user)
    backdrop_pref = effective.get("backdrop", "fanart")

    # 1. Gather all items and their known IDs
    extracted_items = []
    known_kitsu = set()
    known_mal = set()
    known_al = set()
    known_simkl = set()
    known_imdb = set()

    for m in metas:
        stremio_id = str(m.get("id") or "")
        kitsu_id = m.get("kitsu_id")
        mal_id = m.get("mal_id")
        anilist_id = m.get("anilist_id")
        simkl_id = m.get("simkl_id")
        imdb_id = clean_imdb_id(m.get("imdb_id"))

        if not kitsu_id and stremio_id.startswith("kitsu:"):
            kitsu_id = stremio_id.split(":")[1]
        elif not mal_id and stremio_id.startswith("mal:"):
            mal_id = stremio_id.split(":")[1]
        elif not anilist_id and stremio_id.startswith("anilist:"):
            anilist_id = stremio_id.split(":")[1]
        elif not simkl_id and stremio_id.startswith("simkl:"):
            simkl_id = stremio_id.split(":")[1]
        elif not imdb_id and stremio_id.startswith("tt"):
            imdb_id = clean_imdb_id(stremio_id)

        if kitsu_id:
            known_kitsu.add(str(kitsu_id))
        if mal_id:
            known_mal.add(str(mal_id))
        if anilist_id:
            known_al.add(str(anilist_id))
        if simkl_id:
            known_simkl.add(str(simkl_id))
        if imdb_id:
            known_imdb.add(imdb_id)

        extracted_items.append({
            "meta": m,
            "kitsu_id": str(kitsu_id) if kitsu_id else None,
            "mal_id": str(mal_id) if mal_id else None,
            "anilist_id": str(anilist_id) if anilist_id else None,
            "simkl_id": str(simkl_id) if simkl_id else None,
            "imdb_id": imdb_id,
        })

    # 2. Bulk resolve missing cross-tracker and IMDB IDs from local MongoDB (id_cache & fribb_mappings)
    id_map = {}

    or_clauses = []
    k_ints = [int(k) for k in known_kitsu if k.isdigit()]
    if k_ints:
        or_clauses.append({"kitsu_id": {"$in": k_ints}})
    m_strs = [m for m in known_mal if m]
    if m_strs:
        or_clauses.append({"mal_id": {"$in": m_strs}})
    al_strs = [a for a in known_al if a]
    if al_strs:
        or_clauses.append({"anilist_id": {"$in": al_strs}})
    s_strs = [s for s in known_simkl if s]
    s_ints = [int(s) for s in known_simkl if s.isdigit()]
    if s_strs:
        or_clauses.append({"simkl_id": {"$in": list(set(s_strs + s_ints))}})

    if or_clauses:
        try:
            id_docs = list(db.id_cache.find({"$or": or_clauses}))
            fribb_docs = list(db.fribb_mappings.find({"$or": or_clauses}))

            for doc in fribb_docs + id_docs:
                k = str(doc["kitsu_id"]) if doc.get("kitsu_id") else None
                m_id = str(doc["mal_id"]) if doc.get("mal_id") else None
                a_id = str(doc["anilist_id"]) if doc.get("anilist_id") else None
                s_id = str(doc.get("simkl_id") or doc.get("simkl") or "") or None
                im_id = clean_imdb_id(doc.get("imdb_id"))

                keys_to_update = []
                for id_type, id_val in [("kitsu", k), ("mal", m_id), ("anilist", a_id), ("simkl", s_id)]:
                    if id_val:
                        keys_to_update.append((id_type, id_val))

                # Find any existing entry among these keys
                existing_entry = None
                for key_tuple in keys_to_update:
                    if key_tuple in id_map:
                        existing_entry = id_map[key_tuple]
                        break

                if not existing_entry:
                    existing_entry = {
                        "kitsu_id": k,
                        "mal_id": m_id,
                        "anilist_id": a_id,
                        "simkl_id": s_id,
                        "imdb_id": im_id,
                    }
                else:
                    if k and not existing_entry.get("kitsu_id"):
                        existing_entry["kitsu_id"] = k
                    if m_id and not existing_entry.get("mal_id"):
                        existing_entry["mal_id"] = m_id
                    if a_id and not existing_entry.get("anilist_id"):
                        existing_entry["anilist_id"] = a_id
                    if s_id and not existing_entry.get("simkl_id"):
                        existing_entry["simkl_id"] = s_id
                    if im_id and not existing_entry.get("imdb_id"):
                        existing_entry["imdb_id"] = im_id

                for key_tuple in keys_to_update:
                    id_map[key_tuple] = existing_entry
        except Exception as e:
            logging.error("enrich_catalog_metas_artwork: ID lookup failed: %s", e)

    # Propagate resolved IDs
    for item_info in extracted_items:
        resolved = None
        if item_info["kitsu_id"] and ("kitsu", item_info["kitsu_id"]) in id_map:
            resolved = id_map[("kitsu", item_info["kitsu_id"])]
        elif item_info["mal_id"] and ("mal", item_info["mal_id"]) in id_map:
            resolved = id_map[("mal", item_info["mal_id"])]
        elif item_info["anilist_id"] and ("anilist", item_info["anilist_id"]) in id_map:
            resolved = id_map[("anilist", item_info["anilist_id"])]
        elif item_info["simkl_id"] and ("simkl", item_info["simkl_id"]) in id_map:
            resolved = id_map[("simkl", item_info["simkl_id"])]

        if resolved:
            if not item_info["imdb_id"] and resolved.get("imdb_id"):
                item_info["imdb_id"] = resolved["imdb_id"]
                known_imdb.add(resolved["imdb_id"])
            if not item_info["anilist_id"] and resolved.get("anilist_id"):
                item_info["anilist_id"] = resolved["anilist_id"]
                known_al.add(resolved["anilist_id"])
            if not item_info["mal_id"] and resolved.get("mal_id"):
                item_info["mal_id"] = resolved["mal_id"]
                known_mal.add(resolved["mal_id"])
            if not item_info["kitsu_id"] and resolved.get("kitsu_id"):
                item_info["kitsu_id"] = resolved["kitsu_id"]
                known_kitsu.add(resolved["kitsu_id"])
            if not item_info["simkl_id"] and resolved.get("simkl_id"):
                item_info["simkl_id"] = resolved["simkl_id"]
                known_simkl.add(resolved["simkl_id"])

    # 3. Bulk fetch cached rich metadata (AniZip, Cinemeta, Kitsu)
    anizp_by_key = {}
    anizp_keys = [f"al_{a}" for a in known_al if a] + [f"mal_{m}" for m in known_mal if m]
    if anizp_keys:
        try:
            for doc in db.anizp_meta_cache.find({"key": {"$in": anizp_keys}}):
                if doc.get("key") and doc.get("data"):
                    anizp_by_key[doc["key"]] = doc["data"]
        except Exception as e:
            logging.error("enrich_catalog_metas_artwork: anizp_meta_cache query failed: %s", e)

    cinemeta_by_imdb = {}
    if known_imdb:
        try:
            for doc in db.cinemeta_meta_cache.find({"imdb_id": {"$in": list(known_imdb)}}):
                if doc.get("imdb_id") and doc.get("data"):
                    cinemeta_by_imdb[doc["imdb_id"]] = doc["data"]
        except Exception as e:
            logging.error("enrich_catalog_metas_artwork: cinemeta_meta_cache query failed: %s", e)

    kitsu_by_id = {}
    if known_kitsu:
        try:
            for doc in db.kitsu_meta_cache.find({"kitsu_id": {"$in": [str(k) for k in known_kitsu]}}):
                if doc.get("kitsu_id") and doc.get("data"):
                    kitsu_by_id[doc["kitsu_id"]] = doc["data"]
        except Exception as e:
            logging.error("enrich_catalog_metas_artwork: kitsu_meta_cache query failed: %s", e)

    # 4. Resolve Background and Logo for each item
    for item_info in extracted_items:
        m = item_info["meta"]
        poster_url = m.get("poster") or ""
        aid = item_info["anilist_id"]
        mid = item_info["mal_id"]
        kid = item_info["kitsu_id"]
        imdb_id = item_info["imdb_id"]

        # AniZip artwork
        anizp_data = (anizp_by_key.get(f"al_{aid}") if aid else None) or (anizp_by_key.get(f"mal_{mid}") if mid else None) or {}
        anizp_fanart = None
        anizp_logo = None
        if anizp_data:
            for img in anizp_data.get("images", []):
                ctype = img.get("coverType")
                url = img.get("url") or ""
                if ctype == "Fanart" and not anizp_fanart:
                    anizp_fanart = url
                elif ctype in ["Clearlogo", "Logo"] and not anizp_logo:
                    if "/icons/" not in url and "/icon/" not in url:
                        anizp_logo = url
            if not imdb_id:
                imdb_id = clean_imdb_id(anizp_data.get("mappings", {}).get("imdb_id"))

        # Cinemeta artwork
        cinemeta_data = cinemeta_by_imdb.get(imdb_id) if imdb_id else {}
        cinemeta_bg = cinemeta_data.get("background") if cinemeta_data else None
        cinemeta_logo = cinemeta_data.get("logo") if cinemeta_data else None

        # Kitsu cover (16:9 banner)
        kitsu_cover = None
        item_cover = m.get("coverImage")
        if isinstance(item_cover, dict):
            kitsu_cover = item_cover.get("original") or item_cover.get("large") or item_cover.get("medium")
        elif isinstance(item_cover, str) and item_cover:
            kitsu_cover = item_cover
        if not kitsu_cover and m.get("kitsu_cover"):
            kitsu_cover = m.get("kitsu_cover")
        if not kitsu_cover and kid and kid in kitsu_by_id:
            k_attrs = kitsu_by_id[kid].get("data", {}).get("attributes", {})
            c_img = k_attrs.get("coverImage") or {}
            kitsu_cover = c_img.get("original") or c_img.get("large") or c_img.get("medium")

        # Simkl fanart (16:9 widescreen)
        simkl_fanart = m.get("fanart") or m.get("simkl_fanart")
        if simkl_fanart and not simkl_fanart.startswith("http"):
            simkl_fanart = f"https://simkl.in/fanart/{simkl_fanart}_medium.jpg"

        # Metahub artwork (fallback using IMDB ID)
        metahub_bg = f"https://images.metahub.space/background/medium/{imdb_id}/img" if imdb_id else None
        metahub_logo = f"https://images.metahub.space/logo/medium/{imdb_id}/img" if imdb_id else None

        # Existing background / bannerImage on item
        item_bg = m.get("background") or m.get("bannerImage")
        if item_bg and item_bg.strip() == poster_url.strip():
            item_bg = None
            m.pop("background", None)

        # --- Resolve Background ---
        # Prioritize based on user backdrop preference, cascading to providers with native 16:9 artwork
        if backdrop_pref == "kitsu":
            bg_candidates = [kitsu_cover, anizp_fanart, cinemeta_bg, item_bg, simkl_fanart, metahub_bg]
        elif backdrop_pref == "anilist":
            bg_candidates = [item_bg, anizp_fanart, cinemeta_bg, kitsu_cover, simkl_fanart, metahub_bg]
        else:  # fanart (default) / mal / simkl
            bg_candidates = [anizp_fanart, cinemeta_bg, item_bg, kitsu_cover, simkl_fanart, metahub_bg]

        chosen_bg = None
        for cand in bg_candidates:
            if cand and isinstance(cand, str) and cand.strip():
                # Never use portrait poster as background (prevents distorted/pixelated hero banners in Nuvio)
                if cand.strip() != poster_url.strip():
                    chosen_bg = cand.strip()
                    break

        if chosen_bg:
            m["background"] = chosen_bg

        # --- Resolve Logo ---
        logo_candidates = [m.get("logo"), anizp_logo, cinemeta_logo, metahub_logo]
        chosen_logo = None
        for cand in logo_candidates:
            if cand and isinstance(cand, str) and cand.strip():
                chosen_logo = cand.strip()
                break

        if chosen_logo:
            m["logo"] = chosen_logo

    # 5. Background-warm missing AniZip metadata so subsequent requests have Clearlogo & Fanart
    missing_for_anizp = []
    for item_info in extracted_items:
        aid = item_info["anilist_id"]
        mid = item_info["mal_id"]
        if (aid and f"al_{aid}" not in anizp_by_key) or (mid and f"mal_{mid}" not in anizp_by_key):
            missing_for_anizp.append(item_info)

    if missing_for_anizp:
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            loop.create_task(bg_warm_anizip(missing_for_anizp[:25]))
        except (RuntimeError, Exception):
            pass


async def bg_warm_anizip(item_infos: list[dict]):
    """Background task to fetch and cache AniZip metadata for items missing from anizp_meta_cache."""
    import asyncio
    from app.routes.meta import fetch_anizp_metadata

    sem = asyncio.Semaphore(5)

    async def fetch_one(info):
        aid = info.get("anilist_id")
        mid = info.get("mal_id")
        if not aid and not mid:
            return
        async with sem:
            try:
                await fetch_anizp_metadata(anilist_id=aid, mal_id=mid)
            except Exception:
                pass

    tasks = [fetch_one(info) for info in item_infos]
    await asyncio.gather(*tasks, return_exceptions=True)


