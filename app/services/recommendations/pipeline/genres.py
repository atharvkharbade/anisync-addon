from app.services.recommendations.genres import generate_genre_recommendations


async def generate_favorite_genre_recommendations(
    merged_shows: dict,
    user: dict,
    fallbacks: list[dict],
    watched_mal_ids: set[str],
    watched_anilist_ids: set[str],
    watched_kitsu_ids: set[str],
    watched_titles: set[str],
) -> tuple[list[dict], str, list[dict], str]:
    """
    Identifies the user's top 2 favorite genres based on their watch history,
    and queries top-rated anime within those genres.
    Returns: (genre_1_items, genre_1_name, genre_2_items, genre_2_name)
    """
    genre_counts = {}
    for show in merged_shows.values():
        if show.get("status") == "planning":
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
        genre_1_name,
        user,
        watched_mal_ids,
        watched_anilist_ids,
        watched_titles,
        watched_kitsu_ids=watched_kitsu_ids,
    )
    genre_2_items = await generate_genre_recommendations(
        genre_2_name,
        user,
        watched_mal_ids,
        watched_anilist_ids,
        watched_titles,
        watched_kitsu_ids=watched_kitsu_ids,
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

    return genre_1_items, genre_1_name, genre_2_items, genre_2_name
