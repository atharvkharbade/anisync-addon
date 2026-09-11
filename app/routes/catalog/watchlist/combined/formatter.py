import logging
import urllib.parse

from app.routes.catalog.formatting import (
    get_anilist_title,
    get_mal_title,
    get_simkl_display_title,
)
from app.routes.catalog.sorting import extract_item_metadata_fields
from config import Config

from .airing import compute_comb_flags


def format_combined_page_metas(
    paged_items: list[dict],
    user: dict,
    user_id: str,
    comb_status: str,
    bulk_details: dict,
    kitsu_mappings: dict,
) -> list[dict]:
    """
    Transforms paged combined watchlist items into Stremio meta objects,
    resolving localized titles, poster artwork, episode badges, and metadata fields.
    """
    metas = []
    title_lang = user.get("title_language", "english")
    sort_by_new_ep = user.get("sort_by_new_episodes", False)
    enable_new_ep_badge = user.get("enable_new_episodes_badge", user.get("sort_by_new_episodes", True))

    for item in paged_items:
        try:
            name = "Unknown"
            poster = ""
            is_movie = False
            total_eps = "?"
            progress = 0

            if item.get("anilist_item"):
                al_media = item["anilist_item"]["media"]
                name = get_anilist_title(al_media.get("title"), title_lang)
                poster = (al_media.get("coverImage") or {}).get("large") or ""
                is_movie = al_media.get("format") == "MOVIE"
                total_eps = al_media.get("episodes") or total_eps
                progress = item["anilist_item"].get("progress", 0)

            if (name == "Unknown" or not poster) and item.get("mal_item"):
                mal_node = item["mal_item"]["node"]
                if name == "Unknown":
                    name = get_mal_title(mal_node, title_lang)
                if not poster:
                    main_pic = mal_node.get("main_picture") or {}
                    poster = main_pic.get("large") or main_pic.get("medium") or ""
                mal_type = (mal_node.get("media_type") or "").lower()
                if mal_type == "movie":
                    is_movie = True
                if total_eps == "?":
                    total_eps = mal_node.get("num_episodes") or total_eps
                if progress == 0:
                    progress = (mal_node.get("my_list_status") or {}).get("num_episodes_watched", 0)

            if (name == "Unknown" or not poster) and item.get("simkl_item"):
                s_item = item["simkl_item"]
                show_obj = (s_item.get("show") or s_item.get("anime") or s_item) if isinstance(s_item, dict) else {}
                if not isinstance(show_obj, dict):
                    show_obj = {}
                if name == "Unknown":
                    name = get_simkl_display_title(show_obj, title_lang, bulk_details=bulk_details)
                if not poster:
                    p = show_obj.get("poster") or show_obj.get("poster_image") or ""
                    if p and not p.startswith("http"):
                        p = f"https://simkl.in/posters/{p}_m.jpg"
                    poster = p
                simkl_type = (show_obj.get("anime_type") or show_obj.get("type") or "").lower()
                if simkl_type == "movie":
                    is_movie = True
                if total_eps == "?":
                    total_eps = (
                        show_obj.get("episodes_count")
                        or show_obj.get("num_episodes")
                        or s_item.get("total_episodes_count")
                        or total_eps
                    )
                if progress == 0:
                    progress = (
                        s_item.get("watched_episodes_count")
                        or s_item.get("episodes_watched")
                        or s_item.get("progress")
                        or 0
                    )

            is_new_ep = False
            if comb_status in ["watching", "plan_to_watch"]:
                is_new_ep, _, _ = compute_comb_flags(
                    item,
                    bulk_details=bulk_details,
                    enable_new_ep_badge=enable_new_ep_badge,
                    sort_by_new_ep=sort_by_new_ep,
                )

            if not is_movie:
                al_media_bulk = bulk_details.get(item.get("mal_id")) or bulk_details.get(item.get("anilist_id")) or {}
                if isinstance(al_media_bulk, dict) and al_media_bulk.get("format") == "MOVIE":
                    is_movie = True
                elif total_eps == 1:
                    is_movie = True

            if is_new_ep and enable_new_ep_badge and poster:
                badge_id = item.get("mal_id") or item.get("anilist_id") or item.get("simkl_id") or "new"
                badge_tracker = "mal" if item.get("mal_id") else ("anilist" if item.get("anilist_id") else "simkl")
                encoded_url = urllib.parse.quote_plus(poster)
                badge_style = user.get("badge_style", "modern")
                badge_type = "movie" if is_movie else "episode"
                poster = f"{Config.PROTOCOL}://{Config.REDIRECT_URL}/{user_id}/poster/comb_{badge_id}_c_22.jpg?url={encoded_url}&badge=new&badge_type={badge_type}&tracker={badge_tracker}&style={badge_style}&v=hd_poster_v1"

            mal_id = item.get("mal_id")
            anilist_id = item.get("anilist_id")
            simkl_id = item.get("simkl_id")
            kitsu_id = None
            if mal_id:
                kitsu_id = kitsu_mappings.get(f"mal:{mal_id}")
            if not kitsu_id and anilist_id:
                kitsu_id = kitsu_mappings.get(f"anilist:{anilist_id}")
            if not kitsu_id and simkl_id:
                kitsu_id = kitsu_mappings.get(f"simkl:{simkl_id}")

            stremio_id = (
                f"kitsu:{kitsu_id}"
                if kitsu_id
                else (
                    f"mal:{mal_id}" if mal_id else (f"anilist:{anilist_id}" if anilist_id else f"simkl:{simkl_id}")
                )
            )
            stremio_type = "movie" if is_movie else "series"

            simkl_fanart = None
            if item.get("simkl_item"):
                s_item = item["simkl_item"]
                show_obj = (s_item.get("show") or s_item.get("anime") or s_item) if isinstance(s_item, dict) else {}
                if isinstance(show_obj, dict):
                    sf = show_obj.get("fanart")
                    if sf:
                        simkl_fanart = sf if sf.startswith("http") else f"https://simkl.in/fanart/{sf}_medium.jpg"

            al_banner = None
            if item.get("anilist_item"):
                al_media = (item["anilist_item"].get("media") or {}) if isinstance(item["anilist_item"], dict) else {}
                al_banner = al_media.get("bannerImage")
            elif item.get("mal_id") and bulk_details:
                al_banner = (bulk_details.get(item["mal_id"]) or {}).get("bannerImage")
            elif item.get("anilist_id") and bulk_details:
                al_banner = (bulk_details.get(item["anilist_id"]) or {}).get("bannerImage")

            meta_fields = extract_item_metadata_fields(item, "combined", bulk_details=bulk_details)
            metas.append(
                {
                    "id": stremio_id,
                    "type": stremio_type,
                    "name": name,
                    "poster": poster,
                    "background": simkl_fanart or al_banner,
                    "kitsu_id": kitsu_id,
                    "mal_id": mal_id,
                    "anilist_id": anilist_id,
                    "simkl_id": simkl_id,
                    "description": (
                        f"Watchlist - {comb_status.replace('_', ' ').title()}.\n"
                        f"Progress: {progress} / {total_eps}."
                    ),
                    "score": meta_fields["score"],
                    "episodes": meta_fields["episodes"] or total_eps,
                    "year": meta_fields["year"],
                    "airing_at": meta_fields["airing_at"],
                    "updated_at": meta_fields["updated_at"],
                }
            )
        except Exception as item_err:
            logging.warning("Skipping malformed combined item in status %s: %s", comb_status, item_err)
            continue

    return metas
