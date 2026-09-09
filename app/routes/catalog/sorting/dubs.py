from .preferences import is_catalog_dubbed_enabled


async def apply_catalog_dub_filter(metas, user, catalog_id: str):
    """
    Filters catalog metas to only dubbed anime if dubbed filtering is enabled for this catalog.
    """
    if not metas or not user:
        return metas

    if is_catalog_dubbed_enabled(user, catalog_id):
        from app.services.dub_service import filter_dubbed
        cat_cfg = (user.get("catalog_configs", {}) or {}).get(catalog_id, {})
        user_dub_lang = (
            (cat_cfg.get("dub_language") or cat_cfg.get("dubbed_language"))
            if isinstance(cat_cfg, dict) else None
        ) or user.get("dub_language") or user.get("dubbed_language", "english")
        metas = await filter_dubbed(metas, language=user_dub_lang)

    return metas
