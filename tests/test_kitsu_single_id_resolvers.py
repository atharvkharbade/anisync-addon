import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.lib.id_resolver.resolvers import (
    resolve_anilist_to_kitsu,
    resolve_mal_to_kitsu,
)


class TestKitsuSingleIDResolvers(unittest.IsolatedAsyncioTestCase):
    @patch("app.lib.id_resolver.resolvers.get_cached_ids_by_anilist", return_value=None)
    @patch("app.lib.id_resolver.resolvers.get_client")
    @patch("app.lib.id_resolver.resolvers.ensure_fribb_mappings", new_callable=AsyncMock)
    @patch("app.lib.id_resolver.resolvers.db")
    @patch("app.lib.id_resolver.resolvers.kitsu_id_from_kitsu_mappings")
    @patch("app.lib.id_resolver.resolvers._verified_title_fallback", new_callable=AsyncMock)
    async def test_resolve_anilist_to_kitsu_hits_kitsu_mapping_skips_title_fallback(
        self,
        mock_title_fallback,
        mock_kitsu_mappings,
        mock_db,
        mock_ensure_fribb,
        mock_get_client,
        mock_get_cached,
    ):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_get_client.return_value = mock_client
        mock_db.fribb_mappings.find_one.return_value = None

        mock_kitsu_mappings.return_value = "50990"

        res = await resolve_anilist_to_kitsu("208025")
        self.assertEqual(res, "50990")
        mock_kitsu_mappings.assert_called_once_with("anilist", "208025", mal_id=None)
        mock_title_fallback.assert_not_called()

    @patch("app.lib.id_resolver.resolvers.get_cached_ids_by_anilist", return_value=None)
    @patch("app.lib.id_resolver.resolvers.get_client")
    @patch("app.lib.id_resolver.resolvers.ensure_fribb_mappings", new_callable=AsyncMock)
    @patch("app.lib.id_resolver.resolvers.db")
    @patch("app.lib.id_resolver.resolvers.kitsu_id_from_kitsu_mappings")
    @patch("app.lib.id_resolver.resolvers._verified_title_fallback", new_callable=AsyncMock)
    async def test_resolve_anilist_to_kitsu_tries_discovered_mal_if_anilist_misses(
        self,
        mock_title_fallback,
        mock_kitsu_mappings,
        mock_db,
        mock_ensure_fribb,
        mock_get_client,
        mock_get_cached,
    ):
        # ani.zip returns mal_id="12345" but no kitsu_id
        mock_client = MagicMock()
        mock_resp_arm = MagicMock(status_code=404)
        mock_resp_anizp = MagicMock(status_code=200)
        mock_resp_anizp.json.return_value = {"mappings": {"mal_id": "12345"}}
        mock_client.get = AsyncMock(side_effect=[mock_resp_arm, mock_resp_anizp])
        mock_get_client.return_value = mock_client
        mock_db.fribb_mappings.find_one.return_value = None

        # First call ("anilist", "208025") -> None; second call ("mal", "12345") -> "50990"
        mock_kitsu_mappings.side_effect = [None, "50990"]

        res = await resolve_anilist_to_kitsu("208025")
        self.assertEqual(res, "50990")
        self.assertEqual(mock_kitsu_mappings.call_count, 2)
        mock_title_fallback.assert_not_called()

    @patch("app.lib.id_resolver.resolvers.get_cached_ids_by_mal", return_value=None)
    @patch("app.lib.id_resolver.resolvers.get_client")
    @patch("app.lib.id_resolver.resolvers.ensure_fribb_mappings", new_callable=AsyncMock)
    @patch("app.lib.id_resolver.resolvers.db")
    @patch("app.lib.id_resolver.resolvers.kitsu_id_from_kitsu_mappings")
    @patch("app.lib.id_resolver.resolvers._verified_title_fallback", new_callable=AsyncMock)
    async def test_resolve_mal_to_kitsu_hits_kitsu_mapping_skips_title_fallback(
        self,
        mock_title_fallback,
        mock_kitsu_mappings,
        mock_db,
        mock_ensure_fribb,
        mock_get_client,
        mock_get_cached,
    ):
        mock_client = MagicMock()
        mock_resp = MagicMock(status_code=404)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_get_client.return_value = mock_client
        mock_db.fribb_mappings.find_one.return_value = None

        mock_kitsu_mappings.return_value = "40001"

        res = await resolve_mal_to_kitsu("54321")
        self.assertEqual(res, "40001")
        mock_kitsu_mappings.assert_called_once_with("mal", "54321", anilist_id=None)
        mock_title_fallback.assert_not_called()

    @patch("app.lib.id_resolver.resolvers.get_cached_ids_by_mal", return_value=None)
    @patch("app.lib.id_resolver.resolvers.get_client")
    @patch("app.lib.id_resolver.resolvers.ensure_fribb_mappings", new_callable=AsyncMock)
    @patch("app.lib.id_resolver.resolvers.db")
    @patch("app.lib.id_resolver.resolvers.kitsu_id_from_kitsu_mappings")
    @patch("app.lib.id_resolver.resolvers._verified_title_fallback", new_callable=AsyncMock)
    async def test_resolve_mal_to_kitsu_tries_discovered_anilist_if_mal_misses(
        self,
        mock_title_fallback,
        mock_kitsu_mappings,
        mock_db,
        mock_ensure_fribb,
        mock_get_client,
        mock_get_cached,
    ):
        # ARM discovers anilist_id="67890" but no kitsu
        mock_client = MagicMock()
        mock_resp_arm = MagicMock(status_code=200)
        mock_resp_arm.json.return_value = {"anilist": "67890"}
        mock_resp_malsync = MagicMock(status_code=404)
        mock_client.get = AsyncMock(side_effect=[mock_resp_arm, mock_resp_malsync])
        mock_get_client.return_value = mock_client
        mock_db.fribb_mappings.find_one.return_value = None

        # First call ("mal", "54321") -> None; second call ("anilist", "67890") -> "40001"
        mock_kitsu_mappings.side_effect = [None, "40001"]

        res = await resolve_mal_to_kitsu("54321")
        self.assertEqual(res, "40001")
        self.assertEqual(mock_kitsu_mappings.call_count, 2)
        mock_title_fallback.assert_not_called()

    @patch("app.lib.id_resolver.resolvers.get_cached_ids_by_anilist", return_value=None)
    @patch("app.lib.id_resolver.resolvers.get_client")
    @patch("app.lib.id_resolver.resolvers.ensure_fribb_mappings", new_callable=AsyncMock)
    @patch("app.lib.id_resolver.resolvers.db")
    @patch("app.lib.id_resolver.resolvers.kitsu_id_from_kitsu_mappings", return_value=None)
    @patch("app.lib.id_resolver.resolvers.is_fallback_miss", return_value=False)
    @patch("app.lib.id_resolver.resolvers.fetch_anime_info_by_anilist_id")
    @patch("app.lib.id_resolver.resolvers._verified_title_fallback", new_callable=AsyncMock)
    async def test_resolve_falls_back_to_title_when_kitsu_mapping_misses(
        self,
        mock_title_fallback,
        mock_fetch_info,
        mock_miss,
        mock_kitsu_mappings,
        mock_db,
        mock_ensure_fribb,
        mock_get_client,
        mock_get_cached,
    ):
        mock_client = MagicMock()
        mock_resp = MagicMock(status_code=404)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_get_client.return_value = mock_client
        mock_db.fribb_mappings.find_one.return_value = None

        mock_fetch_info.return_value = {"title": "Test Title", "mal_id": "111"}
        mock_title_fallback.return_value = "99999"

        res = await resolve_anilist_to_kitsu("208025")
        self.assertEqual(res, "99999")
        mock_title_fallback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
