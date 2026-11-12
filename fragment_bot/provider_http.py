"""Per-request proxy configuration for adapted aiohttp clients."""

import os

import aiohttp as _aiohttp
from aiohttp_socks import ProxyConnector


def proxy_url():
    return (
        os.getenv("PAYMENT_PROXY")
        or os.getenv("FRAGMENT_PROXY")
        or os.getenv("TELEGRAM_PROXY")
        or None
    )


def ClientSession(*args, **kwargs):
    kwargs.setdefault("timeout", _aiohttp.ClientTimeout(total=30))
    proxy = proxy_url()
    if proxy:
        if proxy.startswith(("socks4://", "socks5://")):
            kwargs.setdefault("connector", ProxyConnector.from_url(proxy))
        else:
            kwargs.setdefault("proxy", proxy)
    return _aiohttp.ClientSession(*args, **kwargs)


def __getattr__(name):
    return getattr(_aiohttp, name)
