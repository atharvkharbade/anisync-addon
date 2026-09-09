import re


def clean_html(text: str) -> str:
    if not text:
        return ""

    # Strip HTML tags
    clean = re.sub(r"<[^<]+?>", "", text)
    # Decode common HTML entities if any
    clean = (
        clean.replace("&quot;", '"')
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&apos;", "'")
    )
    return clean.strip()


def is_proper_anime(title: str, synopsis: str | None = None) -> bool:
    if not title:
        return True
    t_lower = title.lower()

    # Exclude obvious shorts, chibi series, recaps, side stories, and specials by keywords
    excl_keywords = [
        "break time",
        "kyuukei jikan",
        "chibi",
        "petit",
        "mini-anime",
        "mini anime",
        "character theater",
        "chara gekijou",
        "picture drama",
        "recap",
        "summary",
        "special episode",
        "pv",
        "trailer",
        "commercial",
        "short anime",
        "web short",
        "spin-off",
        "spinoff",
        "bonus",
        "audio commentary",
        "side story",
        "side stories",
        "junior high",
        "ple ple pleiades",
        "chara-gekijou",
        "chara gekijou",
        "oitsukeru",
        "de oitsukeru",
        "soushuuhen",
        "sou-shuuhen",
        "soushuhen",
        "digest",
        "daijesuto",
        "compilation",
        "catch-up",
        "catch up",
        "re-cap",
        "re-edit",
        "omnibus",
        "theatrical short",
        "drama cd",
        "audio drama",
        "special edition",
    ]

    for kw in excl_keywords:
        if kw == "ona":
            if re.search(r"\bona\b", t_lower):
                return False
        elif kw == "ova":
            if re.search(r"\bova\b", t_lower):
                return False
        elif kw in t_lower:
            return False

    if synopsis:
        s_lower = synopsis.lower().strip()
        recap_prefixes = (
            "recap of",
            "a recap of",
            "summary of",
            "a summary of",
            "digest of",
            "a digest of",
            "compilation of",
            "a compilation of",
            "special episode summarizing",
            "recap episode",
        )
        if any(s_lower.startswith(prefix) for prefix in recap_prefixes):
            return False

    return True


def normalize_user_status(status: str | None) -> str:
    if not status:
        return "watching"
    s = status.lower()
    if s in ["watching", "current"]:
        return "watching"
    if s in ["completed"]:
        return "completed"
    if s in ["on_hold", "paused", "hold"]:
        return "on_hold"
    if s in ["dropped"]:
        return "dropped"
    if s in ["plan_to_watch", "planning", "plantowatch"]:
        return "planning"
    return s
