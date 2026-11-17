import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.routes.catalog.watchlist.common import (
    _watchlist_kitsu_bg_tasks,
    schedule_watchlist_kitsu_resolution,
)


class TestWatchlistKitsuResolution(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _watchlist_kitsu_bg_tasks.clear()

    async def asyncTearDown(self):
        # Wait for any lingering tasks
        if _watchlist_kitsu_bg_tasks:
            await asyncio.gather(*list(_watchlist_kitsu_bg_tasks), return_exceptions=True)
        _watchlist_kitsu_bg_tasks.clear()

    @patch("app.routes.catalog.watchlist.common.kitsu_id_from_kitsu_mappings", new_callable=AsyncMock)
    async def test_schedule_watchlist_kitsu_resolution_runs_background_task(
        self,
        mock_kitsu_mappings,
    ):
        mock_kitsu_mappings.return_value = "50990"
        items = [("anilist", "208025", "12345"), ("mal", "54321", None)]

        schedule_watchlist_kitsu_resolution(items)
        self.assertEqual(len(_watchlist_kitsu_bg_tasks), 1)

        # Await the background task
        await asyncio.gather(*list(_watchlist_kitsu_bg_tasks))

        self.assertEqual(len(_watchlist_kitsu_bg_tasks), 0)
        self.assertEqual(mock_kitsu_mappings.call_count, 2)
        mock_kitsu_mappings.assert_any_call("anilist", "208025", mal_id="12345")
        mock_kitsu_mappings.assert_any_call("mal", "54321", mal_id=None)

    @patch("app.routes.catalog.watchlist.common.kitsu_id_from_kitsu_mappings", new_callable=AsyncMock)
    async def test_schedule_watchlist_kitsu_resolution_caps_at_10_items(
        self,
        mock_kitsu_mappings,
    ):
        mock_kitsu_mappings.return_value = "50990"
        items = [("anilist", str(i), None) for i in range(100, 120)]  # 20 items

        schedule_watchlist_kitsu_resolution(items)
        self.assertEqual(len(_watchlist_kitsu_bg_tasks), 1)

        await asyncio.gather(*list(_watchlist_kitsu_bg_tasks))
        self.assertEqual(mock_kitsu_mappings.call_count, 10)

    def test_empty_unmapped_items_no_task_created(self):
        schedule_watchlist_kitsu_resolution([])
        self.assertEqual(len(_watchlist_kitsu_bg_tasks), 0)


if __name__ == "__main__":
    unittest.main()
