import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import app.lib.id_resolver as idr_pkg
from app.lib.id_resolver.constants import ANIZP_API, ARM_API, FRIBB_API, MALSYNC_API, TIMEOUT
from app.lib.id_resolver.fribb import clean_id, ensure_fribb_mappings
from app.lib.id_resolver.remote import (
    _try_anizp,
    _try_arm,
    _try_fribb,
    fetch_anime_info_by_anilist_id,
    fetch_anime_info_by_mal_id,
    search_kitsu_by_title,
)
from app.lib.id_resolver.resolvers import (
    bulk_resolve_to_kitsu,
    resolve,
    resolve_anilist_to_kitsu,
    resolve_mal_to_kitsu,
    resolve_simkl_to_kitsu,
)


class TestIDResolverSubpackage(unittest.IsolatedAsyncioTestCase):
    def test_reexports(self):
        """Verify all required symbols exist on the root package."""
        symbols = [
            "ARM_API",
            "ANIZP_API",
            "KITSU_MAPPINGS_API",
            "MALSYNC_API",
            "FRIBB_API",
            "TIMEOUT",
            "ensure_fribb_mappings",
            "resolve",
            "resolve_mal_to_kitsu",
            "resolve_anilist_to_kitsu",
            "resolve_simkl_to_kitsu",
            "bulk_resolve_to_kitsu",
            "kitsu_id_from_kitsu_mappings",
            "search_kitsu_by_title",
            "fetch_anime_info_by_mal_id",
            "fetch_anime_info_by_anilist_id",
        ]
        for sym in symbols:
            self.assertTrue(hasattr(idr_pkg, sym), f"Missing {sym} on app.lib.id_resolver")

    def test_clean_id(self):
        self.assertIsNone(clean_id(None))
        self.assertIsNone(clean_id(""))
        self.assertEqual(clean_id("tt1234567"), "tt1234567")
        self.assertEqual(clean_id("['tt1234567']"), "tt1234567")
        self.assertEqual(clean_id(["tt1234567"]), "tt1234567")

    @patch("app.lib.id_resolver.resolvers.get_cached_ids")
    async def test_resolve_cached(self, mock_cached):
        mock_cached.return_value = {"mal_id": "100", "anilist_id": "200"}
        mal_id, anilist_id = await resolve("kitsu:5000")
        self.assertEqual(mal_id, "100")
        self.assertEqual(anilist_id, "200")

    @patch("app.lib.id_resolver.resolvers.get_cached_ids_by_mal")
    async def test_resolve_mal_to_kitsu_cached(self, mock_cached):
        mock_cached.return_value = {"kitsu_id": 5000}
        kid = await resolve_mal_to_kitsu("mal:100")
        self.assertEqual(kid, "5000")

    @patch("app.lib.id_resolver.resolvers.get_cached_ids_by_anilist")
    async def test_resolve_anilist_to_kitsu_cached(self, mock_cached):
        mock_cached.return_value = {"kitsu_id": 6000}
        kid = await resolve_anilist_to_kitsu("anilist:200")
        self.assertEqual(kid, "6000")

    @patch("app.lib.id_resolver.resolvers.get_cached_ids_by_simkl")
    async def test_resolve_simkl_to_kitsu_cached(self, mock_cached):
        mock_cached.return_value = {"kitsu_id": 7000}
        kid = await resolve_simkl_to_kitsu("simkl:300")
        self.assertEqual(kid, "7000")

    @patch("app.lib.id_resolver.resolvers.id_cache_collection")
    async def test_bulk_resolve_to_kitsu(self, mock_col):
        mock_col.find.return_value = [
            {"kitsu_id": 101, "mal_id": "11", "anilist_id": "22"},
            {"kitsu_id": 102, "simkl_id": "33"},
        ]
        res = await bulk_resolve_to_kitsu(
            mal_ids=["11"], anilist_ids=["22"], simkl_ids=["33"], skip_external=True
        )
        self.assertEqual(res.get("mal:11"), "101")
        self.assertEqual(res.get("anilist:22"), "101")
        self.assertEqual(res.get("simkl:33"), "102")


if __name__ == "__main__":
    unittest.main()
