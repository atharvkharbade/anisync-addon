import contextvars
import logging

import httpx

from config import Config

_client: httpx.AsyncClient | None = None


class PersistentAsyncClient(httpx.AsyncClient):
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def aclose(self):
        pass

    async def _real_close(self):
        try:
            await super().aclose()
            logging.info("Released shared persistent connection pool.")
        except Exception as e:
            logging.error("Failed to cleanly close shared connection pool: %s", e)


def init_client():
    global _client
    if _client is None:
        proxy = Config.PROXY_URL if Config.PROXY_URL else None
        _client = PersistentAsyncClient(
            timeout=8, limits=httpx.Limits(max_connections=200, max_keepalive_connections=50), proxy=proxy
        )
        if proxy:
            logging.info("Initialized shared persistent connection pool with proxy: %s", proxy)
        else:
            logging.info("Initialized shared persistent connection pool (Direct Connection).")


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        init_client()
    return _client


async def close_client():
    global _client
    if _client is not None:
        await _client._real_close()
        _client = None


correlation_id_var = contextvars.ContextVar("correlation_id", default=None)
