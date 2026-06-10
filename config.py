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

    # MAL OAuth
    MAL_CLIENT_ID = os.getenv("MAL_CLIENT_ID", "")
    MAL_CLIENT_SECRET = os.getenv("MAL_CLIENT_SECRET", "")

    # AniList OAuth (authorization code flow)
    ANILIST_CLIENT_ID = os.getenv("ANILIST_CLIENT_ID", "")
    ANILIST_CLIENT_SECRET = os.getenv("ANILIST_CLIENT_SECRET", "")

    # Simkl OAuth
    SIMKL_CLIENT_ID = os.getenv("SIMKL_CLIENT_ID", "749cbca8c56af4464eb3504e8c3cdb8c6f1ab3e8546a02a792dba421a77fbd2d")
    SIMKL_CLIENT_SECRET = os.getenv("SIMKL_CLIENT_SECRET", "7bab200ffcb2ef2c0963d5886544c2fd4c68ffeb0b6813c7e1ee446b9ec202f2")

    if DEBUG:
        PROTOCOL = "http"
        REDIRECT_URL = f"{FLASK_HOST}:{FLASK_PORT}"
    else:
        PROTOCOL = "https"
        REDIRECT_URL = f"{FLASK_HOST}"


KITSU_ID_PREFIX = "kitsu:"
