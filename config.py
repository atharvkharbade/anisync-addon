import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()


class Config:
    JSON_SORT_KEYS = False
    FLASK_HOST = os.getenv("FLASK_RUN_HOST", "localhost")
    FLASK_PORT = os.getenv("FLASK_RUN_PORT", "5000")
    DEBUG = os.getenv("FLASK_DEBUG", "False").lower() in ["1", "true"]

    SECRET_KEY = os.getenv("SECRET_KEY")
    if not SECRET_KEY:
        if not DEBUG:
            raise ValueError("SECRET_KEY environment variable is required in production!")
        SECRET_KEY = "change-me-in-production"

    SESSION_TYPE = os.getenv("SESSION_TYPE", "filesystem")
    SEND_FILE_MAX_AGE_DEFAULT = timedelta(days=7)
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)

    # MongoDB
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    MONGO_DB = os.getenv("MONGO_DB", "anisync")

    # Proxy Support
    PROXY_URL = os.getenv("PROXY_URL", "")
    PROXY_ANILIST = os.getenv("PROXY_ANILIST", "")
    PROXY_MAL = os.getenv("PROXY_MAL", "")
    PROXY_SIMKL = os.getenv("PROXY_SIMKL", "")
    PROXY_JIKAN = os.getenv("PROXY_JIKAN", "")
    PROXY_KITSU = os.getenv("PROXY_KITSU", "")
    PROXY_ANIZP = os.getenv("PROXY_ANIZP", "")
    PROXY_CINEMETA = os.getenv("PROXY_CINEMETA", "")
    PROXY_ARM = os.getenv("PROXY_ARM", "")
    PROXY_GITHUB = os.getenv("PROXY_GITHUB", "")
    PROXY_METAHUB = os.getenv("PROXY_METAHUB", "")
    PROXY_RPDB = os.getenv("PROXY_RPDB", "")

    # MAL OAuth
    MAL_CLIENT_ID = os.getenv("MAL_CLIENT_ID", "")
    MAL_CLIENT_SECRET = os.getenv("MAL_CLIENT_SECRET", "")

    # AniList OAuth (authorization code flow)
    ANILIST_CLIENT_ID = os.getenv("ANILIST_CLIENT_ID", "")
    ANILIST_CLIENT_SECRET = os.getenv("ANILIST_CLIENT_SECRET", "")

    # Simkl OAuth
    SIMKL_CLIENT_ID = os.getenv("SIMKL_CLIENT_ID", "")
    SIMKL_CLIENT_SECRET = os.getenv("SIMKL_CLIENT_SECRET", "")

    # API endpoints (Allows routing through Cloudflare Workers or proxies)
    ANILIST_API_URL = os.getenv("ANILIST_API_URL", "https://graphql.anilist.co")
    MAL_API_URL = os.getenv("MAL_API_URL", "https://api.myanimelist.net/v2")
    SIMKL_API_URL = os.getenv("SIMKL_API_URL", "https://api.simkl.com")

    if DEBUG:
        PROTOCOL = "http"
        REDIRECT_URL = f"{FLASK_HOST}:{FLASK_PORT}"
    else:
        PROTOCOL = "https"
        REDIRECT_URL = f"{FLASK_HOST}"


KITSU_ID_PREFIX = "kitsu:"
