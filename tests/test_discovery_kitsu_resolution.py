import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.routes.catalog.discovery.builder import update_discovery_catalogs_cache


class TestDiscoveryKitsuResolution(unittest.IsolatedAsyncioTestCase):
    DEFAULT_SUPP = {
        "jikan_pop": [],
        "jikan_airing": [],
        "jikan_top": [],
        "jikan_movies": [],
        "jikan_season_now": [],
        "jikan_schedule": [],
        "jikan_fav": [],
        "kitsu_pop": [],
        "kitsu_rating": [],
        "kitsu_airing": [],
        "kitsu_season": [],
        "kitsu_movie": [],
    }

    @patch("app.routes.catalog.discovery.builder.fetch_supplemental_discovery_data", new_callable=AsyncMock)
    @patch("app.routes.catalog.discovery.builder.anilist_api._gql", new_callable=AsyncMock)
    @patch("app.routes.catalog.discovery.builder.bulk_resolve_to_kitsu", new_callable=AsyncMock)
    @patch("app.routes.catalog.discovery.builder.kitsu_id_from_kitsu_mappings", new_callable=AsyncMock)
    @patch("app.routes.catalog.discovery.builder.db")
    async def test_discovery_resolves_unmapped_item_via_helper(
        self,
        mock_db,
        mock_kitsu_mappings,
        mock_bulk_resolve,
        mock_anilist_gql,
        mock_fetch_supp,
    ):
        mock_col = MagicMock()
        mock_col.find_one.return_value = None
        mock_col.update_one.return_value.matched_count = 0
        mock_db.get_collection.return_value = mock_col
        mock_fetch_supp.return_value = self.DEFAULT_SUPP
        # GQL returns an unmapped media item 208025
        mock_anilist_gql.return_value = {
            "data": {
                "trending": {
                    "media": [
                        {
                            "id": 208025,
                            "idMal": None,
                            "title": {"english": "Dark Summoner"},
                            "format": "TV",
                        }
                    ]
                }
            }
        }
        mock_bulk_resolve.return_value = {}  # Misses in cache/ARM/ani.zip
        mock_kitsu_mappings.return_value = "50990"  # Resolved by Kitsu endpoint

        res = await update_discovery_catalogs_cache()
        trending_cards = res.get("anisync_trending") or []
        card_ids = [c["id"] for c in trending_cards]
        self.assertIn("kitsu:50990", card_ids)
        mock_kitsu_mappings.assert_called_once_with("anilist", "208025", mal_id=None)

    @patch("app.routes.catalog.discovery.builder.fetch_supplemental_discovery_data", new_callable=AsyncMock)
    @patch("app.routes.catalog.discovery.builder.anilist_api._gql", new_callable=AsyncMock)
    @patch("app.routes.catalog.discovery.builder.bulk_resolve_to_kitsu", new_callable=AsyncMock)
    @patch("app.routes.catalog.discovery.builder.kitsu_id_from_kitsu_mappings", new_callable=AsyncMock)
    @patch("app.routes.catalog.discovery.builder.db")
    async def test_discovery_caps_at_60_lookups(
        self,
        mock_db,
        mock_kitsu_mappings,
        mock_bulk_resolve,
        mock_anilist_gql,
        mock_fetch_supp,
    ):
        mock_col = MagicMock()
        mock_col.find_one.return_value = None
        mock_col.update_one.return_value.matched_count = 0
        mock_db.get_collection.return_value = mock_col
        mock_fetch_supp.return_value = self.DEFAULT_SUPP
        # Create 75 unmapped items
        media_items = [
            {"id": i, "idMal": None, "title": {"english": f"Anime {i}"}, "format": "TV"}
            for i in range(1000, 1075)
        ]
        mock_anilist_gql.return_value = {"data": {"trending": {"media": media_items}}}
        mock_bulk_resolve.return_value = {}
        mock_kitsu_mappings.return_value = None

        await update_discovery_catalogs_cache()
        self.assertLessEqual(mock_kitsu_mappings.call_count, 60)

    @patch("app.routes.catalog.discovery.builder.fetch_supplemental_discovery_data", new_callable=AsyncMock)
    @patch("app.routes.catalog.discovery.builder.anilist_api._gql", new_callable=AsyncMock)
    @patch("app.routes.catalog.discovery.builder.bulk_resolve_to_kitsu", new_callable=AsyncMock)
    @patch("app.routes.catalog.discovery.builder.kitsu_id_from_kitsu_mappings", new_callable=AsyncMock)
    @patch("app.routes.catalog.discovery.builder.db")
    async def test_discovery_respects_25s_timeout(
        self,
        mock_db,
        mock_kitsu_mappings,
        mock_bulk_resolve,
        mock_anilist_gql,
        mock_fetch_supp,
    ):
        mock_col = MagicMock()
        mock_col.find_one.return_value = None
        mock_col.update_one.return_value.matched_count = 0
        mock_db.get_collection.return_value = mock_col
        mock_fetch_supp.return_value = self.DEFAULT_SUPP
        mock_anilist_gql.return_value = {
            "data": {
                "trending": {
                    "media": [
                        {"id": 99999, "idMal": None, "title": {"english": "Hanging Anime"}, "format": "TV"}
                    ]
                }
            }
        }
        mock_bulk_resolve.return_value = {}

        # Simulate hanging helper that exceeds 25s timeout
        async def _hanging_helper(*args, **kwargs):
            await asyncio.sleep(30.0)
            return "55555"

        mock_kitsu_mappings.side_effect = _hanging_helper

        # Patch asyncio.wait_for inside builder to use a very small timeout for fast testing
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            res = await update_discovery_catalogs_cache()
            self.assertIsInstance(res, dict)


if __name__ == "__main__":
    unittest.main()
