"""Shared HTTP helper with retry/backoff."""
from __future__ import annotations

import asyncio

import httpx

from .. import config


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=config.HTTP_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": config.USER_AGENT, "Accept": "application/json"},
    )


async def get_json(client: httpx.AsyncClient, url: str, params: dict | None = None, retries: int = 3,
                   headers: dict | None = None):
    """GET -> parsed JSON. Retries on 429/5xx/network errors. 404 raises immediately (bad board token)."""
    return await _request_json(client, "GET", url, retries, params=params, headers=headers)


async def post_json(client: httpx.AsyncClient, url: str, body: dict, retries: int = 3, headers: dict | None = None):
    """POST JSON -> parsed JSON, same retry policy as get_json."""
    return await _request_json(client, "POST", url, retries, json=body, headers=headers)


async def _request_json(client: httpx.AsyncClient, method: str, url: str, retries: int, **kw):
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = await client.request(method, url, **kw)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            last = e
            if e.response.status_code not in (429, 500, 502, 503, 504):
                raise
        except (httpx.TransportError, ValueError) as e:
            last = e
        await asyncio.sleep(min(2 ** attempt, 8) * 0.5)
    assert last is not None
    raise last
