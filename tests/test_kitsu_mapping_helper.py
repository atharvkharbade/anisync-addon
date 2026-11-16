import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.lib.id_resolver.resolvers import (
    _KITSU_MAPPINGS_SEMAPHORE,
    kitsu_id_from_kitsu_mappings,
)


class TestKitsuMappingHelper(unittest.IsolatedAsyncioTestCase):
    @patch("app.lib.id_resolver.resolvers.cache_ids")
    @patch("app.lib.id_resolver.resolvers.is_fallback_miss", return_value=False)
    @patch("app.lib.id_resolver.resolvers.get_client")
    async def test_anilist_hit_caches_and_returns_id(self, mock_get_client, mock_miss, mock_cache_ids):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {
                    "id": "354380",
                    "type": "mappings",
                    "attributes": {
                        "externalSite": "anilist/anime",
                        "externalId": "208025",
                    },
                    "relationships": {
                        "item": {
                            "data": {"type": "anime", "id": "50990"}
                        }
                    },
                }
            ],
            "included": [
                {
                    "id": "50990",
                    "type": "anime",
                    "attributes": {"canonicalTitle": "Dark Summoner"},
                }
            ],
        }
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_get_client.return_value = mock_client

        res = await kitsu_id_from_kitsu_mappings("anilist", "208025")
        self.assertEqual(res, "50990")
        mock_cache_ids.assert_called_once_with("50990", None, "208025")

    @patch("app.lib.id_resolver.resolvers.cache_ids")
    @patch("app.lib.id_resolver.resolvers.is_fallback_miss", return_value=False)
    @patch("app.lib.id_resolver.resolvers.get_client")
    async def test_mal_hit_caches_and_returns_id(self, mock_get_client, mock_miss, mock_cache_ids):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {
                    "id": "12345",
                    "type": "mappings",
                    "attributes": {
                        "externalSite": "myanimelist/anime",
                        "externalId": "54321",
                    },
                    "relationships": {
                        "item": {
                            "data": {"type": "anime", "id": "40001"}
                        }
                    },
                }
            ],
            "included": [
                {
                    "id": "40001",
                    "type": "anime",
                }
            ],
        }
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_get_client.return_value = mock_client

        res = await kitsu_id_from_kitsu_mappings("mal", "54321")
        self.assertEqual(res, "40001")
        mock_cache_ids.assert_called_once_with("40001", "54321", None)

    @patch("app.lib.id_resolver.resolvers.record_fallback_miss")
    @patch("app.lib.id_resolver.resolvers.is_fallback_miss", return_value=False)
    @patch("app.lib.id_resolver.resolvers.get_client")
    async def test_mismatched_external_id_ignored(self, mock_get_client, mock_miss, mock_record_miss):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {
                    "id": "12345",
                    "type": "mappings",
                    "attributes": {
                        "externalSite": "anilist/anime",
                        "externalId": "999999",  # Different ID!
                    },
                    "relationships": {
                        "item": {
                            "data": {"type": "anime", "id": "40001"}
                        }
                    },
                }
            ],
            "included": [{"id": "40001", "type": "anime"}],
        }
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_get_client.return_value = mock_client

        res = await kitsu_id_from_kitsu_mappings("anilist", "12345")
        self.assertIsNone(res)
        mock_record_miss.assert_called_once_with("kitsu_map_anilist", "12345", "No Kitsu mapping", ttl_hours=12)

    @patch("app.lib.id_resolver.resolvers.record_fallback_miss")
    @patch("app.lib.id_resolver.resolvers.is_fallback_miss", return_value=False)
    @patch("app.lib.id_resolver.resolvers.get_client")
    async def test_non_anime_included_ignored(self, mock_get_client, mock_miss, mock_record_miss):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {
                    "id": "12345",
                    "type": "mappings",
                    "attributes": {
                        "externalSite": "anilist/anime",
                        "externalId": "12345",
                    },
                    "relationships": {
                        "item": {
                            "data": {"type": "manga", "id": "40001"}  # Non-anime!
                        }
                    },
                }
            ],
            "included": [{"id": "40001", "type": "manga"}],
        }
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_get_client.return_value = mock_client

        res = await kitsu_id_from_kitsu_mappings("anilist", "12345")
        self.assertIsNone(res)
        mock_record_miss.assert_called_once_with("kitsu_map_anilist", "12345", "No Kitsu mapping", ttl_hours=12)

    @patch("app.lib.id_resolver.resolvers.record_fallback_miss")
    @patch("app.lib.id_resolver.resolvers.is_fallback_miss")
    @patch("app.lib.id_resolver.resolvers.get_client")
    async def test_http_200_empty_records_negative_and_skips_second_call(
        self, mock_get_client, mock_is_miss, mock_record_miss
    ):
        mock_is_miss.return_value = False
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": []}
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_get_client.return_value = mock_client

        # Call 1: misses and records 12h negative entry
        res1 = await kitsu_id_from_kitsu_mappings("mal", "63468")
        self.assertIsNone(res1)
        mock_record_miss.assert_called_once_with("kitsu_map_mal", "63468", "No Kitsu mapping", ttl_hours=12)
        self.assertEqual(mock_client.get.call_count, 1)

        # Call 2: active negative cache skips HTTP call
        mock_is_miss.return_value = True
        res2 = await kitsu_id_from_kitsu_mappings("mal", "63468")
        self.assertIsNone(res2)
        self.assertEqual(mock_client.get.call_count, 1)

    @patch("app.lib.id_resolver.resolvers.record_fallback_miss")
    @patch("app.lib.id_resolver.resolvers.is_fallback_miss", return_value=False)
    @patch("app.lib.id_resolver.resolvers.get_client")
    async def test_transient_errors_no_negative_cache(self, mock_get_client, mock_miss, mock_record_miss):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # 429 Rate Limit
        mock_resp_429 = MagicMock()
        mock_resp_429.status_code = 429
        mock_client.get = AsyncMock(return_value=mock_resp_429)
        res_429 = await kitsu_id_from_kitsu_mappings("anilist", "12345")
        self.assertIsNone(res_429)
        mock_record_miss.assert_not_called()

        # 500 Server Error
        mock_resp_500 = MagicMock()
        mock_resp_500.status_code = 500
        mock_client.get = AsyncMock(return_value=mock_resp_500)
        res_500 = await kitsu_id_from_kitsu_mappings("anilist", "12345")
        self.assertIsNone(res_500)
        mock_record_miss.assert_not_called()

        # Timeout / Network Error
        mock_client.get = AsyncMock(side_effect=asyncio.TimeoutError("Timeout"))
        res_to = await kitsu_id_from_kitsu_mappings("anilist", "12345")
        self.assertIsNone(res_to)
        mock_record_miss.assert_not_called()

    @patch("app.lib.id_resolver.resolvers.get_client")
    async def test_invalid_inputs_skip_http(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.get = AsyncMock()
        mock_get_client.return_value = mock_client

        # Non-digit
        res1 = await kitsu_id_from_kitsu_mappings("anilist", "abc12")
        self.assertIsNone(res1)

        # Length > 12
        res2 = await kitsu_id_from_kitsu_mappings("anilist", "1234567890123")
        self.assertIsNone(res2)

        # Invalid site
        res3 = await kitsu_id_from_kitsu_mappings("invalid_site", "12345")
        self.assertIsNone(res3)

        mock_client.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
