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
        mounts = {}
        limits = httpx.Limits(max_connections=200, max_keepalive_connections=50)
        
        # Determine global proxy
        global_proxy = Config.PROXY_URL if Config.PROXY_URL else None
        
        # Specific proxy overrides
        services = {
            "graphql.anilist.co": Config.PROXY_ANILIST,
            "api.myanimelist.net": Config.PROXY_MAL,
            "myanimelist.net": Config.PROXY_MAL,
            "api.simkl.com": Config.PROXY_SIMKL,
            "api.jikan.moe": Config.PROXY_JIKAN,
            "kitsu.io": Config.PROXY_KITSU,
            "api.ani.zip": Config.PROXY_ANIZP,
            "v3-cinemeta.strem.io": Config.PROXY_CINEMETA,
            "arm.haglund.dev": Config.PROXY_ARM,
            "raw.githubusercontent.com": Config.PROXY_GITHUB,
            "images.metahub.space": Config.PROXY_METAHUB,
            "api.rpdb.co": Config.PROXY_RPDB,
        }
        
        for domain, proxy_val in services.items():
            if proxy_val:
                if proxy_val.lower() in ["direct", "none"]:
                    # Map to None/direct transport to force direct connection
                    mounts[f"all://{domain}"] = httpx.AsyncHTTPTransport(limits=limits)
                else:
                    mounts[f"all://{domain}"] = httpx.AsyncHTTPTransport(proxy=proxy_val, limits=limits)
            elif global_proxy:
                # Fallback to global proxy explicitly
                mounts[f"all://{domain}"] = httpx.AsyncHTTPTransport(proxy=global_proxy, limits=limits)
                
        # General default fallback for all other domains
        if global_proxy:
            mounts["all://"] = httpx.AsyncHTTPTransport(proxy=global_proxy, limits=limits)
            
        _client = PersistentAsyncClient(
            timeout=8,
            limits=limits,
            mounts=mounts
        )
        
        if mounts:
            # Format mounts for readable logging
            readable_mounts = {}
            for k, v in mounts.items():
                pool = getattr(v, "_pool", None)
                if pool and getattr(pool, "_proxy_url", None) is not None:
                    readable_mounts[str(k)] = bytes(pool._proxy_url).decode()
                else:
                    readable_mounts[str(k)] = "Direct"
            logging.info("Initialized shared persistent connection pool with proxy routing: %s", readable_mounts)
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
