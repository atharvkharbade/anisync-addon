def get_simkl_ids(item) -> dict:
    """
    Extracts IDs dictionary from a Simkl item regardless of structure (show/anime/direct).
    """
    if not isinstance(item, dict):
        return {}
    if "show" in item and isinstance(item["show"], dict):
        return item["show"].get("ids") or {}
    elif "anime" in item and isinstance(item["anime"], dict):
        return item["anime"].get("ids") or {}
    return item.get("ids") or {}


def merge_tracker_entries(mal_entries, anilist_entries, simkl_entries) -> list[dict]:
    """
    Merges watchlist entries from MAL, AniList, and Simkl into a deduplicated list
    of unified combined items, cross-referencing entries via MAL ID and AniList ID.
    Returns: list of dicts with keys (mal_item, anilist_item, simkl_item, mal_id, anilist_id, simkl_id)
    """
    # 1. Match AniList entries and MAL entries
    al_by_mal_id = {}
    al_by_al_id = {}
    for entry in anilist_entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("media"), dict):
            continue
        al_id = str(entry["media"]["id"])
        al_by_al_id[al_id] = entry

        id_mal = entry["media"].get("idMal")
        if id_mal:
            al_by_mal_id[str(id_mal)] = entry

    # 2. Match Simkl entries
    simkl_by_mal_id = {}
    simkl_by_al_id = {}

    for entry in simkl_entries:
        ids = get_simkl_ids(entry)
        s_mal = ids.get("mal")
        if s_mal:
            simkl_by_mal_id[str(s_mal)] = entry
        s_al = ids.get("anilist")
        if s_al:
            simkl_by_al_id[str(s_al)] = entry

    combined_items = []
    processed_mal_ids = set()
    processed_al_ids = set()
    processed_simkl_ids = set()

    for mal_item in mal_entries:
        if not isinstance(mal_item, dict) or not isinstance(mal_item.get("node"), dict):
            continue
        mal_id = str(mal_item["node"]["id"])
        processed_mal_ids.add(mal_id)

        al_entry = al_by_mal_id.get(mal_id)
        al_id = None
        if al_entry:
            al_id = str(al_entry["media"]["id"])
            processed_al_ids.add(al_id)

        simkl_entry = simkl_by_mal_id.get(mal_id)
        if not simkl_entry and al_id:
            simkl_entry = simkl_by_al_id.get(al_id)

        simkl_id = None
        if simkl_entry:
            simkl_id = str(get_simkl_ids(simkl_entry).get("simkl") or "")
            if simkl_id:
                processed_simkl_ids.add(simkl_id)

        combined_items.append(
            {
                "mal_item": mal_item,
                "anilist_item": al_entry,
                "simkl_item": simkl_entry,
                "mal_id": mal_id,
                "anilist_id": al_id,
                "simkl_id": simkl_id,
            }
        )

    for al_entry in anilist_entries:
        if not isinstance(al_entry, dict) or not isinstance(al_entry.get("media"), dict):
            continue
        al_id = str(al_entry["media"]["id"])
        if al_id in processed_al_ids:
            continue

        processed_al_ids.add(al_id)
        mal_id = str(al_entry["media"].get("idMal") or "") or None
        if mal_id:
            processed_mal_ids.add(mal_id)

        simkl_entry = simkl_by_al_id.get(al_id)
        if not simkl_entry and mal_id:
            simkl_entry = simkl_by_mal_id.get(mal_id)

        simkl_id = None
        if simkl_entry:
            simkl_id = str(get_simkl_ids(simkl_entry).get("simkl") or "")
            if simkl_id:
                processed_simkl_ids.add(simkl_id)

        combined_items.append(
            {
                "mal_item": None,
                "anilist_item": al_entry,
                "simkl_item": simkl_entry,
                "mal_id": mal_id,
                "anilist_id": al_id,
                "simkl_id": simkl_id,
            }
        )

    for simkl_item in simkl_entries:
        ids = get_simkl_ids(simkl_item)
        simkl_id = str(ids.get("simkl") or "")
        if not simkl_id or simkl_id in processed_simkl_ids:
            continue

        processed_simkl_ids.add(simkl_id)
        mal_id = str(ids.get("mal") or "") or None
        al_id = str(ids.get("anilist") or "") or None

        combined_items.append(
            {
                "mal_item": None,
                "anilist_item": None,
                "simkl_item": simkl_item,
                "mal_id": mal_id,
                "anilist_id": al_id,
                "simkl_id": simkl_id,
            }
        )

    return combined_items
