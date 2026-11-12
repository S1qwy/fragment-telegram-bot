import httpx as _httpx

from .provider_http import proxy_url


def AsyncClient(*args, **kwargs):
    if proxy_url():
        kwargs.setdefault("proxy", proxy_url())
    return _httpx.AsyncClient(*args, **kwargs)


def __getattr__(name):
    return getattr(_httpx, name)
