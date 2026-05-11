import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()


class Config:
    JSON_SORT_KEYS = False
    FLASK_HOST = os.getenv("FLASK_RUN_HOST", "localhost")
    FLASK_PORT = os.getenv("FLASK_RUN_PORT", "5000")
    SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
    SESSION_TYPE = os.getenv("SESSION_TYPE", "filesystem")
    SEND_FILE_MAX_AGE_DEFAULT = timedelta(days=7)
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)
    DEBUG = os.getenv("FLASK_DEBUG", False)

    # MongoDB
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    MONGO_DB = os.getenv("MONGO_DB", "anisync")

    # MAL OAuth
    MAL_CLIENT_ID = os.getenv("MAL_CLIENT_ID", "")
    MAL_CLIENT_SECRET = os.getenv("MAL_CLIENT_SECRET", "")

    # AniList OAuth (implicit grant — client ID only, no secret)
    ANILIST_CLIENT_ID = os.getenv("ANILIST_CLIENT_ID", "")

    if DEBUG in ["1", True, "True"]:
        PROTOCOL = "http"
        REDIRECT_URL = f"{FLASK_HOST}:{FLASK_PORT}"
    else:
        PROTOCOL = "https"
        REDIRECT_URL = f"{FLASK_HOST}"


KITSU_ID_PREFIX = "kitsu:"
