import logging
import httpx
from typing import Optional

_client: Optional[httpx.AsyncClient] = None

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
        _client = PersistentAsyncClient(
            timeout=8,
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50)
        )
        logging.info("Initialized shared persistent connection pool with limits (max_connections=200).")
    # Globally patch httpx.AsyncClient
    httpx.AsyncClient = lambda *args, **kwargs: _client

async def close_client():
    global _client
    if _client is not None:
        await _client._real_close()
        _client = None
