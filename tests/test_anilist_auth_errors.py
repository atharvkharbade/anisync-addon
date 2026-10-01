import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from app.api import anilist as al_api
from app.factory import create_app
from app.routes.utils import _rate_limit_buckets, _rate_limit_lock


class TestAnilistAuthErrors(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()

    def setUp(self):
        self.client = self.app.test_client()
        with _rate_limit_lock:
            _rate_limit_buckets.clear()

    def test_timeout_constants(self):
        self.assertEqual(al_api.DEFAULT_TIMEOUT, 4.0)
        self.assertEqual(al_api.AUTH_TIMEOUT, 15.0)
        self.assertEqual(al_api.SCROBBLE_TIMEOUT, 8.0)
        self.assertEqual(al_api.TIMEOUT, 4.0)

    async def _post_save(self, token="test_token", state="valid_state", saved_state="valid_state"):
        fake_session = {}
        if saved_state is not None:
            fake_session["anilist_oauth_state"] = saved_state

        form_data = {}
        if token is not None:
            form_data["token"] = token
        if state is not None:
            form_data["state"] = state

        with patch("app.routes.auth.anilist.session", fake_session):
            return await self.client.post("/anilist-save", form=form_data)

    async def test_csrf_failure(self):
        resp = await self._post_save(state="wrong_state", saved_state="valid_state")
        self.assertEqual(resp.status_code, 403)
        data = await resp.get_json()
        self.assertFalse(data["ok"])
        self.assertIn("CSRF", data["error"])

    async def test_no_token_failure(self):
        resp = await self._post_save(token="", state="valid_state", saved_state="valid_state")
        self.assertEqual(resp.status_code, 400)
        data = await resp.get_json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "No token provided")

    async def test_timeout_returns_504(self):
        with patch("app.routes.auth.anilist.al_api.get_viewer", new_callable=AsyncMock) as mock_viewer:
            mock_viewer.side_effect = httpx.ReadTimeout("Read timed out")
            resp = await self._post_save()
            self.assertEqual(resp.status_code, 504)
            data = await resp.get_json()
            self.assertFalse(data["ok"])
            self.assertEqual(data["error"], "Connection to AniList timed out. Please try again.")

        with patch("app.routes.auth.anilist.al_api.get_viewer", new_callable=AsyncMock) as mock_viewer:
            mock_viewer.side_effect = asyncio.TimeoutError()
            resp = await self._post_save()
            self.assertEqual(resp.status_code, 504)
            data = await resp.get_json()
            self.assertFalse(data["ok"])
            self.assertEqual(data["error"], "Connection to AniList timed out. Please try again.")

    async def test_connect_error_returns_502(self):
        with patch("app.routes.auth.anilist.al_api.get_viewer", new_callable=AsyncMock) as mock_viewer:
            mock_viewer.side_effect = httpx.ConnectError("Failed to resolve host")
            resp = await self._post_save()
            self.assertEqual(resp.status_code, 502)
            data = await resp.get_json()
            self.assertFalse(data["ok"])
            self.assertEqual(data["error"], "Unable to connect to AniList. Connection or DNS lookup failed.")

    async def test_token_invalid_exception_returns_401(self):
        with patch("app.routes.auth.anilist.al_api.get_viewer", new_callable=AsyncMock) as mock_viewer:
            mock_viewer.side_effect = al_api.AnilistTokenInvalidError("Token expired")
            resp = await self._post_save()
            self.assertEqual(resp.status_code, 401)
            data = await resp.get_json()
            self.assertFalse(data["ok"])
            self.assertEqual(data["error"], "Your AniList session token is invalid or expired. Please authorize again.")

    async def test_http_401_status_returns_401(self):
        with patch("app.routes.auth.anilist.al_api.get_viewer", new_callable=AsyncMock) as mock_viewer:
            mock_resp = MagicMock(status_code=401)
            mock_viewer.side_effect = httpx.HTTPStatusError("Unauthorized", request=MagicMock(), response=mock_resp)
            resp = await self._post_save()
            self.assertEqual(resp.status_code, 401)
            data = await resp.get_json()
            self.assertFalse(data["ok"])
            self.assertIn("rejected the token", data["error"])

    async def test_rate_limit_429_returns_429(self):
        with patch("app.routes.auth.anilist.al_api.get_viewer", new_callable=AsyncMock) as mock_viewer:
            mock_resp = MagicMock(status_code=429)
            mock_viewer.side_effect = httpx.HTTPStatusError("Rate Limit", request=MagicMock(), response=mock_resp)
            resp = await self._post_save()
            self.assertEqual(resp.status_code, 429)
            data = await resp.get_json()
            self.assertFalse(data["ok"])
            self.assertEqual(data["error"], "AniList rate limit reached. Please wait a moment and try again.")

    async def test_5xx_service_unavailable(self):
        with patch("app.routes.auth.anilist.al_api.get_viewer", new_callable=AsyncMock) as mock_viewer:
            mock_resp = MagicMock(status_code=503)
            mock_viewer.side_effect = httpx.HTTPStatusError("Service Unavailable", request=MagicMock(), response=mock_resp)
            resp = await self._post_save()
            self.assertEqual(resp.status_code, 502)
            data = await resp.get_json()
            self.assertFalse(data["ok"])
            self.assertEqual(data["error"], "AniList service is temporarily unavailable (HTTP 503). Please try again later.")

    async def test_generic_request_error_returns_502(self):
        with patch("app.routes.auth.anilist.al_api.get_viewer", new_callable=AsyncMock) as mock_viewer:
            mock_viewer.side_effect = httpx.RequestError("Protocol error")
            resp = await self._post_save()
            self.assertEqual(resp.status_code, 502)
            data = await resp.get_json()
            self.assertFalse(data["ok"])
            self.assertEqual(data["error"], "Unable to communicate with AniList. Network or proxy connection error.")

    async def test_generic_exception_returns_500(self):
        with patch("app.routes.auth.anilist.al_api.get_viewer", new_callable=AsyncMock) as mock_viewer:
            mock_viewer.side_effect = RuntimeError("Database down")
            resp = await self._post_save()
            self.assertEqual(resp.status_code, 500)
            data = await resp.get_json()
            self.assertFalse(data["ok"])
            self.assertEqual(data["error"], "Failed to complete AniList authorization. Please try again.")

    async def test_successful_auth(self):
        viewer_data = {
            "id": 998877,
            "name": "TestOtaku",
            "avatar": {"large": "https://example.com/avatar.png"},
        }
        with patch("app.routes.auth.anilist.al_api.get_viewer", new_callable=AsyncMock, return_value=viewer_data), \
             patch("app.routes.auth.anilist.resolve_or_create_user", return_value=("user_123", {"uid": "user_123"})), \
             patch("app.routes.auth.anilist.store_user") as mock_store:
            resp = await self._post_save()
            self.assertEqual(resp.status_code, 200)
            data = await resp.get_json()
            self.assertTrue(data["ok"])
            self.assertEqual(data["username"], "TestOtaku")
            mock_store.assert_called_once()

    async def test_gql_401_raises_token_invalid_error(self):
        mock_resp = MagicMock(status_code=401)
        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("app.api.anilist.get_client", return_value=mock_client):
            with self.assertRaises(al_api.AnilistTokenInvalidError):
                await al_api._gql("fake_token", "query { Viewer { id } }")

    async def test_gql_429_propagates_on_final_attempt(self):
        mock_resp = MagicMock(status_code=429)
        mock_resp.headers = {"Retry-After": "0"}
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "429 Too Many Requests",
            request=MagicMock(),
            response=mock_resp,
        )
        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("app.api.anilist.get_client", return_value=mock_client), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            with self.assertRaises(httpx.HTTPStatusError) as ctx:
                await al_api._gql("fake_token", "query { Viewer { id } }")
            self.assertEqual(ctx.exception.response.status_code, 429)


if __name__ == "__main__":
    unittest.main()
