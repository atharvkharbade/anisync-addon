def pad_catalog(
    items: list[dict],
    fallback_list: list[dict],
    shown_ids_set: set[str],
    watched_titles_set: set[str],
    watched_mal_ids: set[str] = None,
    watched_anilist_ids: set[str] = None,
    watched_kitsu_ids: set[str] = None,
    min_count: int = 15,
    default_desc: str = None,
    fallback_offset: int = 0,
) -> list[dict]:
    """
    Deduplicates items across catalog rows, excludes watched items,
    and pads each catalog with fallbacks up to min_count.
    Rotates fallback items by fallback_offset to diversify recommendations across rows.
    """
    watched_mal_ids = watched_mal_ids or set()
    watched_anilist_ids = watched_anilist_ids or set()
    watched_kitsu_ids = watched_kitsu_ids or set()

    if fallback_offset and fallback_list and len(fallback_list) > 1:
        offset = fallback_offset % len(fallback_list)
        effective_fallbacks = fallback_list[offset:] + fallback_list[:offset]
    else:
        effective_fallbacks = fallback_list

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
                if tracker == "kitsu" and ext_id in watched_kitsu_ids:
                    continue
        if item["id"] not in shown_ids_set:
            shown_ids_set.add(item["id"])
            item_copy = item.copy()
            curr_desc = item_copy.get("description", "")
            if default_desc and "\n\n" not in curr_desc:
                syn = item_copy.get("synopsis") or curr_desc
                item_copy["description"] = f"{default_desc}  \n\n{syn}" if syn else default_desc
            padded_items.append(item_copy)

    for fb_item in effective_fallbacks:
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
            if tracker == "kitsu" and ext_id in watched_kitsu_ids:
                continue
        shown_ids_set.add(fb_item["id"])
        item_copy = fb_item.copy()
        fb_desc = item_copy.get("synopsis") or item_copy.get("description") or ""
        if default_desc:
            item_copy["description"] = f"{default_desc}  \n\n{fb_desc}" if fb_desc else default_desc
        padded_items.append(item_copy)

    if len(padded_items) < min_count:
        for fb_item in effective_fallbacks:
            if len(padded_items) >= min_count:
                break
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
                if tracker == "kitsu" and ext_id in watched_kitsu_ids:
                    continue
            item_copy = fb_item.copy()
            fb_desc = item_copy.get("synopsis") or item_copy.get("description") or ""
            if default_desc:
                item_copy["description"] = f"{default_desc}  \n\n{fb_desc}" if fb_desc else default_desc
            padded_items.append(item_copy)

    return padded_items


def apply_sorting_order(metas: list[dict], rec_sorting_order: str = "default") -> list[dict]:
    """
    Applies series_first or movies_first ordering to recommendation rows.
    """
    if rec_sorting_order == "series_first":
        return sorted(metas, key=lambda x: 0 if x.get("type") == "series" else 1)
    elif rec_sorting_order == "movies_first":
        return sorted(metas, key=lambda x: 0 if x.get("type") == "movie" else 1)
    return metas
