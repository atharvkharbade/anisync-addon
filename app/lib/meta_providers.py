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

