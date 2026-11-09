"""Metadata localization language definitions and normalization helpers."""

SUPPORTED_METADATA_LANGUAGES: dict[str, dict[str, str | None]] = {
    "eng": {
        "label": "English",
        "tvdb": "eng",
        "tvdb_fallback": None,
        "anizp": "en",
    },
    "fra": {
        "label": "French (Français)",
        "tvdb": "fra",
        "tvdb_fallback": None,
        "anizp": "fr",
    },
    "spa": {
        "label": "Spanish (Español)",
        "tvdb": "spa",
        "tvdb_fallback": None,
        "anizp": "es",
    },
    "deu": {
        "label": "German (Deutsch)",
        "tvdb": "deu",
        "tvdb_fallback": None,
        "anizp": "de",
    },
    "ita": {
        "label": "Italian (Italiano)",
        "tvdb": "ita",
        "tvdb_fallback": None,
        "anizp": "it",
    },
    "pt": {
        "label": "Portuguese - Portugal (Português)",
        "tvdb": "pt",
        "tvdb_fallback": "por",
        "anizp": "pt-BR",
    },
    "por": {
        "label": "Portuguese - Brazil (Português BR)",
        "tvdb": "por",
        "tvdb_fallback": "pt",
        "anizp": "pt-BR",
    },
    "jpn": {
        "label": "Japanese (日本語)",
        "tvdb": "jpn",
        "tvdb_fallback": None,
        "anizp": "ja",
    },
}

_LANGUAGE_ALIASES: dict[str, str] = {
    "en": "eng",
    "eng": "eng",
    "english": "eng",
    "fr": "fra",
    "fra": "fra",
    "fre": "fra",
    "french": "fra",
    "français": "fra",
    "es": "spa",
    "esp": "spa",
    "spa": "spa",
    "spanish": "spa",
    "español": "spa",
    "de": "deu",
    "deu": "deu",
    "ger": "deu",
    "german": "deu",
    "deutsch": "deu",
    "it": "ita",
    "ita": "ita",
    "italian": "ita",
    "italiano": "ita",
    "pt": "pt",
    "pt-pt": "pt",
    "pt_pt": "pt",
    "por": "por",
    "pt-br": "por",
    "pt_br": "por",
    "portuguese": "por",
    "português": "por",
    "ja": "jpn",
    "jp": "jpn",
    "jpn": "jpn",
    "japanese": "jpn",
}


def normalize_metadata_language(val: str | None) -> str:
    """Normalize a metadata language setting to a supported canonical key (defaults to 'eng')."""
    if not val:
        return "eng"
    cleaned = str(val).strip().lower()
    if cleaned in SUPPORTED_METADATA_LANGUAGES:
        return cleaned
    return _LANGUAGE_ALIASES.get(cleaned, "eng")


def get_tvdb_language_codes(val: str | None) -> tuple[str, str | None]:
    """Return (primary_tvdb_code, fallback_tvdb_code) for the normalized metadata language."""
    lang = normalize_metadata_language(val)
    cfg = SUPPORTED_METADATA_LANGUAGES[lang]
    return str(cfg["tvdb"]), cfg["tvdb_fallback"]


def get_anizp_language_key(val: str | None) -> str:
    """Return the AniZip episode title language key for the normalized metadata language."""
    lang = normalize_metadata_language(val)
    return str(SUPPORTED_METADATA_LANGUAGES[lang]["anizp"])
