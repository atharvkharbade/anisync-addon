import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.routes.catalog.search import handle_search_catalog


class TestSearchKitsuResolution(unittest.IsolatedAsyncioTestCase):
    @patch("app.routes.catalog.search.respond_with", new_callable=AsyncMock)
    @patch("app.routes.catalog.search.get_client")
    @patch("app.services.db.db")
    @patch("app.routes.catalog.search.kitsu_id_from_kitsu_mappings", new_callable=AsyncMock)
    @patch("app.routes.catalog.search.format_catalog_metas_async", new_callable=AsyncMock)
    async def test_search_fallback_resolves_via_kitsu_mappings(
        self,
        mock_format_metas,
        mock_kitsu_mappings,
        mock_db,
        mock_get_client,
        mock_respond_with,
    ):
        mock_respond_with.side_effect = lambda data, **kw: data
        mock_format_metas.side_effect = lambda metas, *args, **kwargs: metas
        mock_col = MagicMock()
        mock_col.find_one.return_value = None
        mock_col.find.return_value = []
        mock_db.get_collection.return_value = mock_col

        mock_client = MagicMock()
        # Call 1: Kitsu search returns empty
        resp_kitsu = MagicMock(status_code=200)
        resp_kitsu.json.return_value = {"data": []}

        # Call 2: AniList GQL fallback returns media with id 208025
        resp_anilist = MagicMock(status_code=200)
        resp_anilist.json.return_value = {
            "data": {
                "Page": {
                    "media": [
                        {
                            "id": 208025,
                            "idMal": None,
                            "title": {"english": "Dark Summoner", "userPreferred": "Dark Summoner"},
                            "format": "TV",
                        }
                    ]
                }
            }
        }
        mock_client.get = AsyncMock(return_value=resp_kitsu)
        mock_client.post = AsyncMock(return_value=resp_anilist)
        mock_get_client.return_value = mock_client

        mock_kitsu_mappings.return_value = "50990"

        res = await handle_search_catalog(
            user={"title_language": "english"},
            user_id="test_user",
            catalog_type="anime",
            catalog_id="search",
            filters={"search": "Dark Summoner"},
        )
        metas = res.get("metas", [])
        self.assertTrue(any(m["id"] == "kitsu:50990" for m in metas))
        mock_kitsu_mappings.assert_called_once_with("anilist", "208025", mal_id=None)

    @patch("app.routes.catalog.search.respond_with", new_callable=AsyncMock)
    @patch("app.routes.catalog.search.get_client")
    @patch("app.services.db.db")
    @patch("app.routes.catalog.search.kitsu_id_from_kitsu_mappings", new_callable=AsyncMock)
    @patch("app.routes.catalog.search.format_catalog_metas_async", new_callable=AsyncMock)
    async def test_search_fallback_caps_at_8_lookups(
        self,
        mock_format_metas,
        mock_kitsu_mappings,
        mock_db,
        mock_get_client,
        mock_respond_with,
    ):
        mock_respond_with.side_effect = lambda data, **kw: data
        mock_format_metas.side_effect = lambda metas, *args, **kwargs: metas
        mock_col = MagicMock()
        mock_col.find_one.return_value = None
        mock_col.find.return_value = []
        mock_db.get_collection.return_value = mock_col

        mock_client = MagicMock()
        resp_kitsu = MagicMock(status_code=200)
        resp_kitsu.json.return_value = {"data": []}

        # 15 media items from AniList fallback
        media_items = [
            {
                "id": i,
                "idMal": None,
                "title": {"english": f"Anime {i}"},
                "format": "TV",
            }
            for i in range(100, 115)
        ]
        resp_anilist = MagicMock(status_code=200)
        resp_anilist.json.return_value = {"data": {"Page": {"media": media_items}}}
        mock_client.get = AsyncMock(return_value=resp_kitsu)
        mock_client.post = AsyncMock(return_value=resp_anilist)
        mock_get_client.return_value = mock_client

        mock_kitsu_mappings.return_value = None

        await handle_search_catalog(
            user={"title_language": "english"},
            user_id="test_user",
            catalog_type="anime",
            catalog_id="search",
            filters={"search": "Batch Anime"},
        )
        self.assertLessEqual(mock_kitsu_mappings.call_count, 8)

    @patch("app.routes.catalog.search.respond_with", new_callable=AsyncMock)
    @patch("app.routes.catalog.search.get_client")
    @patch("app.services.db.db")
    @patch("app.routes.catalog.search.kitsu_id_from_kitsu_mappings", new_callable=AsyncMock)
    @patch("app.routes.catalog.search.format_catalog_metas_async", new_callable=AsyncMock)
    async def test_search_fallback_respects_2_5s_budget(
        self,
        mock_format_metas,
        mock_kitsu_mappings,
        mock_db,
        mock_get_client,
        mock_respond_with,
    ):
        mock_respond_with.side_effect = lambda data, **kw: data
        mock_format_metas.side_effect = lambda metas, *args, **kwargs: metas
        mock_col = MagicMock()
        mock_col.find_one.return_value = None
        mock_col.find.return_value = []
        mock_db.get_collection.return_value = mock_col

        mock_client = MagicMock()
        resp_kitsu = MagicMock(status_code=200)
        resp_kitsu.json.return_value = {"data": []}

        resp_anilist = MagicMock(status_code=200)
        resp_anilist.json.return_value = {
            "data": {
                "Page": {
                    "media": [
                        {
                            "id": 99999,
                            "idMal": None,
                            "title": {"english": "Hanging Search"},
                            "format": "TV",
                        }
                    ]
                }
            }
        }
        mock_client.get = AsyncMock(return_value=resp_kitsu)
        mock_client.post = AsyncMock(return_value=resp_anilist)
        mock_get_client.return_value = mock_client

        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            res = await handle_search_catalog(
                user={"title_language": "english"},
                user_id="test_user",
                catalog_type="anime",
                catalog_id="search",
                filters={"search": "Hanging Search"},
            )
            self.assertIn("metas", res)


if __name__ == "__main__":
    unittest.main()
