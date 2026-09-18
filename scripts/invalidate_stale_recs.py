"""Script to invalidate stale recommendations cache entries on deployment."""

import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.recommendations.cache import recommendations_cache_collection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("invalidate_stale_recs")


def main():
    result = recommendations_cache_collection.delete_many({})
    logger.info(f"Invalidated recommendations cache: {result.deleted_count} documents removed.")


if __name__ == "__main__":
    main()
