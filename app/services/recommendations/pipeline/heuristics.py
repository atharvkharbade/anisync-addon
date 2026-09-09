import random

from app.services.recommendations.seeding import get_recommendations_for_seeds, select_weighted_seeds


async def generate_seed_based_recommendations(
    user: dict,
    seed_pool: list[dict],
    fallbacks: list[dict],
    watched_mal_ids: set[str],
    watched_anilist_ids: set[str],
    watched_kitsu_ids: set[str],
    watched_titles: set[str],
    filter_watched: bool,
) -> tuple[list[dict], dict, list[dict], list[dict]]:
    """
    Generates heuristic recommendations based on user history seeds:
    1. 'Because you Watched' (item_recs)
    2. 'Inspired by your Favorites' (loved_items)
    3. 'More from your Watchlist' (liked_items)
    Returns: (item_recs, seed_show, loved_items, liked_items)
    """
    # 1. Generate "Because you Watched"
    item_recs = []
    seed_show = None
    seed_candidates = [s for s in seed_pool if (s.get("rating") or 0) >= 7 or s.get("status") in ["completed", "watching"]]
    if not seed_candidates and seed_pool:
        seed_candidates = seed_pool
    if seed_candidates:
        seed_show = random.choice(seed_candidates)

    if seed_show:
        item_recs = await get_recommendations_for_seeds(
            [seed_show],
            user,
            watched_mal_ids,
            watched_anilist_ids,
            watched_titles,
            watched_kitsu_ids=watched_kitsu_ids,
        )
        for ir in item_recs:
            desc = f"Recommended because you watched {seed_show['title']}."
            syn = ir.get("synopsis") or ""
            ir["description"] = f"{desc}  \n\n{syn}" if syn else desc

    if not item_recs:
        item_recs = []
        for fb in fallbacks:
            if len(item_recs) >= 5:
                break

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
                    if tracker == "kitsu" and ext_id in watched_kitsu_ids:
                        continue

            item_copy = fb.copy()
            desc = "Popular trending anime you might enjoy."
            fb_desc = item_copy.get("description") or ""
            item_copy["description"] = f"{desc}  \n\n{fb_desc}" if fb_desc else desc
            item_recs.append(item_copy)
        seed_show = {"title": "Fullmetal Alchemist: Brotherhood"}

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
                if tracker == "kitsu" and ext_id in watched_kitsu_ids:
                    continue
            filtered_item_recs.append(ir)
        item_recs = filtered_item_recs

    # 2. Generate "Inspired by your Favorites"
    loved_count = 8
    if len(seed_pool) < 16:
        loved_count = max(1, len(seed_pool) // 2)
    loved_seeds = select_weighted_seeds(seed_pool, loved_count)
    loved_items = await get_recommendations_for_seeds(
        loved_seeds, user, watched_mal_ids, watched_anilist_ids, watched_titles, watched_kitsu_ids=watched_kitsu_ids
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

    # 3. Generate "More from your Watchlist"
    remaining_liked_pool = [s for s in seed_pool if s not in loved_seeds]
    liked_count = 8
    if len(seed_pool) < 16:
        liked_count = len(seed_pool) - len(loved_seeds)
    liked_seeds = select_weighted_seeds(remaining_liked_pool, liked_count)
    liked_items = await get_recommendations_for_seeds(
        liked_seeds, user, watched_mal_ids, watched_anilist_ids, watched_titles, watched_kitsu_ids=watched_kitsu_ids
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

    return item_recs, seed_show, loved_items, liked_items
