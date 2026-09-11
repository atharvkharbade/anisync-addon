import asyncio
import datetime
import logging
import urllib.parse


def _parse_stremio_filters(extras: str) -> dict:
    if not extras:
        return {}
    filters = {}
    for part in extras.split("&"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        filters[k] = urllib.parse.unquote(v)
    return filters


def parse_iso_timestamp(ts_str: str) -> int:
    if not ts_str:
        return 0
    try:
        s = ts_str.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(s)
        return int(dt.timestamp())
    except Exception:
        return 0


def get_anilist_title(title_obj: dict, title_lang: str) -> str:
    if not title_obj:
        return "Unknown"
    if title_lang == "english":
        return title_obj.get("english") or title_obj.get("userPreferred") or title_obj.get("romaji") or "Unknown"
    elif title_lang == "romaji":
        return title_obj.get("romaji") or title_obj.get("userPreferred") or title_obj.get("english") or "Unknown"
    elif title_lang == "japanese":
        return title_obj.get("native") or title_obj.get("romaji") or title_obj.get("userPreferred") or "Unknown"
    else:  # default / canonical
        return title_obj.get("userPreferred") or title_obj.get("english") or title_obj.get("romaji") or "Unknown"


def get_kitsu_title(attrs: dict, title_lang: str) -> str:
    if not attrs:
        return "Unknown"
    titles = attrs.get("titles", {})
    if title_lang == "english":
        return titles.get("en") or titles.get("en_us") or attrs.get("canonicalTitle") or titles.get("en_jp") or "Unknown"
    elif title_lang == "romaji":
        return titles.get("en_jp") or attrs.get("canonicalTitle") or titles.get("en") or "Unknown"
    elif title_lang == "japanese":
        return titles.get("ja_jp") or titles.get("en_jp") or attrs.get("canonicalTitle") or "Unknown"
    else:  # default / canonical
        return attrs.get("canonicalTitle") or titles.get("en") or titles.get("en_jp") or "Unknown"


def get_mal_title(node: dict, title_lang: str) -> str:
    if not node:
        return ""
    alt_titles = node.get("alternative_titles", {})
    if title_lang == "english":
        return alt_titles.get("en") or node.get("title") or ""
    elif title_lang == "japanese":
        return alt_titles.get("ja") or node.get("title") or ""
    else:  # romaji or default
        return node.get("title") or ""


def get_simkl_display_title(show_obj: dict, title_lang: str, bulk_details: dict | None = None) -> str:
    if not show_obj or not isinstance(show_obj, dict):
        return ""

    raw_title = show_obj.get("title") or (show_obj.get("show") or {}).get("title") or ""
    if title_lang == "romaji":
        return raw_title

    ids = show_obj.get("ids") or {}
    if not ids and ("show" in show_obj or "anime" in show_obj):
        inner = show_obj.get("show") or show_obj.get("anime") or {}
        ids = inner.get("ids") or {}

    al_id = str(ids.get("anilist") or "")
    mal_id = str(ids.get("mal") or "")
    kitsu_id = str(ids.get("kitsu") or "")

    # 1. Check in-memory bulk_details (AniList media objects)
    if bulk_details:
        al_media = None
        if al_id and al_id in bulk_details:
            al_media = bulk_details[al_id]
        elif mal_id and mal_id in bulk_details:
            al_media = bulk_details[mal_id]
        if al_media and al_media.get("title"):
            t = get_anilist_title(al_media["title"], title_lang)
            if t and t != "Unknown":
                return t

    # 2. Check explicit title in Simkl payload
    if title_lang == "english" and show_obj.get("en_title"):
        return show_obj["en_title"]

    # 3. Check local database caches (zero external network latency)
    try:
        from app.services.db import db

        if not al_id and mal_id:
            try:
                id_doc = db.get_collection("id_cache").find_one({"mal_id": str(mal_id)})
                if id_doc and id_doc.get("anilist_id"):
                    al_id = str(id_doc["anilist_id"])
            except Exception:
                pass

        if al_id and al_id.isdigit():
            # Check anilist_airing_cache
            airing_doc = db.get_collection("anilist_airing_cache").find_one({"anilist_id": int(al_id)})
            if airing_doc and airing_doc.get("title"):
                t = get_anilist_title(airing_doc["title"], title_lang)
                if t and t != "Unknown":
                    return t

            # Check anilist_meta_cache
            meta_doc = db.get_collection("anilist_meta_cache").find_one({"anilist_id": int(al_id)})
            if meta_doc and meta_doc.get("data", {}).get("title"):
                t = get_anilist_title(meta_doc["data"]["title"], title_lang)
                if t and t != "Unknown":
                    return t

        if kitsu_id and (kitsu_id.isdigit() or isinstance(kitsu_id, str)):
            k_id = int(kitsu_id) if kitsu_id.isdigit() else kitsu_id
            kdoc = db.get_collection("kitsu_meta_cache").find_one({"kitsu_id": k_id})
            if kdoc and kdoc.get("data", {}).get("attributes"):
                t = get_kitsu_title(kdoc["data"]["attributes"], title_lang)
                if t and t != "Unknown":
                    return t
    except Exception as e:
        logging.error("Failed to query local cache for Simkl title: %s", e)

    return raw_title


currently_fetching_pairs = set()
currently_fetching_pages = set()
jikan_semaphore = None


def get_jikan_semaphore():
    global jikan_semaphore
    if jikan_semaphore is None:
        jikan_semaphore = asyncio.Semaphore(1)
    return jikan_semaphore


async def background_fetch_and_cache_filler(mal_id: str, episode: int):
    episode = int(episode)
    page = (episode - 1) // 100 + 1
    page_pair = (str(mal_id), page)

    if page_pair in currently_fetching_pages:
        currently_fetching_pairs.discard((str(mal_id), episode))
        return
    currently_fetching_pages.add(page_pair)

    try:
        async with get_jikan_semaphore():
            await asyncio.sleep(1.0)  # Rate limiting safety sleep

            # Check cache again inside the lock
            from app.services.db import get_jikan_filler_cache, set_jikan_filler_cache

            cached = get_jikan_filler_cache(mal_id, episode)
            if cached is not None:
                return

            from app.api.jikan import get_anime_episodes

            success = False
            episodes_data = await get_anime_episodes(mal_id, page=page)
            if episodes_data is not None:
                for item in episodes_data:
                    ep_num = item.get("mal_id")
                    if ep_num:
                        filler = bool(item.get("filler", False))
                        set_jikan_filler_cache(mal_id, ep_num, filler)
                logging.info(
                    "Cached Jikan filler page %s for mal_id=%s (found %s episodes)", page, mal_id, len(episodes_data)
                )
                success = True

            # If we fetched successfully but this specific episode wasn't in the returned list,
            # mark as False to avoid infinite loops.
            if success and get_jikan_filler_cache(mal_id, episode) is None:
                set_jikan_filler_cache(mal_id, episode, False)
    except Exception as e:
        logging.warning("Background Jikan filler fetch encountered error for mal_id=%s ep=%s: %s", mal_id, episode, e)
    finally:
        currently_fetching_pages.discard(page_pair)
        currently_fetching_pairs.discard((str(mal_id), episode))


def is_nsfw_meta(m: dict) -> bool:
    if not isinstance(m, dict):
        return False
    if m.get("is_adult") is True or m.get("isAdult") is True or m.get("nsfw") is True:
        return True
    rating = str(m.get("rating") or m.get("age_rating") or m.get("ageRating") or "").lower().strip()
    if rating in ["rx", "r18", "hentai"] or "hentai" in rating or rating.startswith("rx"):
        return True
    genres = m.get("genres") or []
    if isinstance(genres, list):
        if any(str(g).lower().strip() == "hentai" for g in genres):
            return True
    return False


def format_catalog_metas(metas_list: list, user: dict, catalog_type: str, catalog_id: str | None = None) -> list:
    custom_types_map = {
        "Watching": "anime",
        "Plan to Watch": "anime",
        "Completed": "anime",
        "On Hold": "anime",
        "Dropped": "anime",
        "Planning": "anime",
        "Paused": "anime",
        "Repeating": "anime",
    }

    import urllib.parse

    from app.services.poster import get_poster_url

    title_lang = user.get("title_language", "english") if user else "english"
    is_watchlist_catalog = bool(
        catalog_id
        and any(
            catalog_id.startswith(p)
            for p in ["mal_", "anilist_", "simkl_", "comb_"]
        )
    )
    hide_nsfw = user.get("hide_nsfw", True) if user else True
    formatted_metas = []
    for m in metas_list:
        if hide_nsfw and not is_watchlist_catalog and is_nsfw_meta(m):
            continue
        m_copy = m.copy()
        if "title_obj" in m_copy:
            t_obj = m_copy["title_obj"]
            if t_obj:
                if "canonicalTitle" in t_obj or "titles" in t_obj:
                    m_copy["name"] = get_kitsu_title(t_obj, title_lang)
                else:
                    m_copy["name"] = get_anilist_title(t_obj, title_lang)

        item_type = m_copy.get("type")
        if item_type not in ["series", "movie"]:
            if item_type in custom_types_map:
                item_type = custom_types_map[item_type]
            elif catalog_type in custom_types_map:
                item_type = custom_types_map[catalog_type]
            else:
                item_type = "series"
        m_copy["type"] = item_type

        # Apply metadata provider poster preference (unless this is an individual tracker watchlist)
        from app.lib.meta_providers import get_al_cover, get_effective_meta_providers

        effective = get_effective_meta_providers(user)
        poster_pref = effective.get("poster", "kitsu")

        is_individual_tracker_catalog = bool(
            catalog_id and any(catalog_id.startswith(p) for p in ["mal_", "anilist_", "simkl_"])
        )
        if not is_individual_tracker_catalog:
            new_poster = None
            if poster_pref == "anilist":
                al_poster = m_copy.get("poster_al")
                poster_val = m_copy.get("poster") or ""
                if not al_poster and (
                    poster_val.startswith("https://s4.anilist.co")
                    or "/anilist/" in poster_val
                ):
                    al_poster = poster_val
                if al_poster:
                    new_poster = al_poster
                elif m_copy.get("anilist_id"):
                    al_cov = get_al_cover(m_copy.get("anilist_id"))
                    if al_cov:
                        new_poster = al_cov
                        m_copy["poster_al"] = al_cov
            elif poster_pref == "mal":
                if m_copy.get("poster_mal"):
                    new_poster = m_copy["poster_mal"]
            elif poster_pref == "kitsu":
                if m_copy.get("poster_kitsu"):
                    new_poster = m_copy["poster_kitsu"]

            if new_poster:
                curr = m_copy.get("poster") or ""
                if "/poster/" in curr and "url=" in curr:
                    try:
                        parsed = urllib.parse.urlparse(curr)
                        q_params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
                        q_params["url"] = new_poster
                        base_u = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                        m_copy["poster"] = f"{base_u}?{urllib.parse.urlencode(q_params)}"
                    except Exception:
                        m_copy["poster"] = new_poster
                else:
                    m_copy["poster"] = new_poster

        current_poster = m_copy.get("poster") or ""
        clean_poster = current_poster
        is_badge = bool(m_copy.get("is_badge"))
        badge_query_params = dict(m_copy.get("badge_query_params") or {})
        badge_base_url = m_copy.get("badge_base_url") or ""

        # Check if this is a badge redirect poster URL (from serve_modified_poster)
        if "/poster/" in current_poster and "url=" in current_poster:
            is_badge = True
            try:
                parsed = urllib.parse.urlparse(current_poster)
                badge_base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                badge_query_params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
                clean_poster = badge_query_params.get("url", current_poster)
            except Exception:
                pass

        m_copy["clean_poster"] = clean_poster

        stremio_id = m_copy.get("id", "")
        kitsu_id = m_copy.get("kitsu_id")
        mal_id = m_copy.get("mal_id")
        anilist_id = m_copy.get("anilist_id")
        simkl_id = m_copy.get("simkl_id")
        imdb_id = m_copy.get("imdb_id")
        if not imdb_id and stremio_id.startswith("tt"):
            imdb_id = stremio_id

        if not kitsu_id and stremio_id.startswith("kitsu:"):
            kitsu_id = stremio_id.split(":")[1]
        elif not mal_id and stremio_id.startswith("mal:"):
            mal_id = stremio_id.split(":")[1]
        elif not anilist_id and stremio_id.startswith("anilist:"):
            anilist_id = stremio_id.split(":")[1]
        elif not simkl_id and stremio_id.startswith("simkl:"):
            simkl_id = stremio_id.split(":")[1]

        # Handle card shape (landscape vs poster)
        catalog_shapes = (user.get("catalog_shapes") or {}) if user else {}
        catalog_configs = (user.get("catalog_configs") or {}) if user else {}
        cat_cfg = catalog_configs.get(catalog_id, {}) if isinstance(catalog_configs, dict) else {}
        is_landscape = bool(catalog_id and (cat_cfg.get("shape") == "landscape" or catalog_shapes.get(catalog_id) == "landscape"))

        # Apply RPDB poster overlay if configured (supports per-catalog poster art on/off toggle)
        catalog_poster_arts = (user.get("catalog_poster_arts") or {}) if user else {}
        cat_art_override = catalog_poster_arts.get(catalog_id)
        if cat_art_override is None and user:
            cat_art_override = cat_cfg.get("poster_art")
        if cat_art_override is None and catalog_id == "anisync_search" and user:
            if not user.get("rpdb_in_search", True):
                cat_art_override = False

        is_art_disabled = (cat_art_override is False or cat_art_override in ("false", "off", "clean", "none"))

        ids_dict = {"imdb_id": imdb_id} if imdb_id else {}
        resolved_art = get_poster_url(
            user=user,
            media_type=item_type,
            kitsu_id=kitsu_id,
            mal_id=mal_id,
            anilist_id=anilist_id,
            simkl_id=simkl_id,
            fallback_poster=clean_poster,
            provider_override=None,
            resolved_ids=ids_dict,
            shape="landscape" if is_landscape else "poster",
            imdb_id=imdb_id,
        )

        if ids_dict.get("imdb_id"):
            m_copy["imdb_id"] = ids_dict["imdb_id"]

        if resolved_art and resolved_art != clean_poster:
            if is_badge:
                # Keep clean poster as the base for new episode badges to avoid overlapping badge clutter
                art_poster = current_poster
            else:
                art_poster = resolved_art
        else:
            art_poster = current_poster if is_badge else clean_poster

        m_copy["art_poster"] = art_poster
        m_copy["is_badge"] = is_badge
        m_copy["badge_base_url"] = badge_base_url
        m_copy["badge_query_params"] = badge_query_params

        # If poster art is not disabled and art_poster is available, use it; otherwise preserve badge or clean_poster
        if (not is_art_disabled) and art_poster:
            m_copy["poster"] = art_poster
        else:
            m_copy["poster"] = current_poster if is_badge else clean_poster

        formatted_metas.append(m_copy)

    from app.lib.meta_providers import enrich_catalog_metas_artwork

    enrich_catalog_metas_artwork(formatted_metas, user)

    # Handle card shape (landscape vs poster) for Stremio and modern clients
    catalog_shapes = (user.get("catalog_shapes") or {}) if user else {}
    catalog_configs = (user.get("catalog_configs") or {}) if user else {}
    cat_cfg = catalog_configs.get(catalog_id, {}) if isinstance(catalog_configs, dict) else {}
    is_landscape = bool(catalog_id and (cat_cfg.get("shape") == "landscape" or catalog_shapes.get(catalog_id) == "landscape"))
    for m in formatted_metas:
        bg = m.get("background")
        current_poster = str(m.get("poster") or "")
        has_badge = bool(m.get("is_badge") or ("/poster/" in current_poster and "badge=new" in current_poster))

        landscape_badge_url = None
        clean_bg = None
        if bg and isinstance(bg, str) and bg.strip():
            clean_bg = bg.strip()
            if has_badge:
                try:
                    base_u = m.get("badge_base_url")
                    b_params = dict(m.get("badge_query_params") or {})
                    if not base_u:
                        parsed = urllib.parse.urlparse(current_poster)
                        base_u = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                        b_params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}

                    b_params["url"] = clean_bg
                    b_params["shape"] = "landscape"
                    b_params["v"] = "hd_land_v2"
                    if "_land" not in base_u and base_u.endswith(".jpg"):
                        base_u = base_u[:-4] + "_land.jpg"
                    landscape_badge_url = f"{base_u}?{urllib.parse.urlencode(b_params)}"
                except Exception as e:
                    logging.warning("Failed to construct landscape badge URL: %s", e)

        # Update background with landscape badge so clients (such as Nuvio) that force
        # landscape row views will display the new episode badge on the 16:9 backdrop card
        if landscape_badge_url:
            m["background"] = landscape_badge_url

        if is_landscape:
            m["posterShape"] = "landscape"
            if clean_bg:
                m["poster"] = landscape_badge_url if landscape_badge_url else clean_bg
        else:
            m["posterShape"] = "poster"

    return formatted_metas
