from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods.base import TelegramMethod, TelegramType
from aiohttp_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError


class ProxySession(AiohttpSession):
    """Expose proxy outages through aiogram's normal network error handling."""

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,
    ) -> TelegramType:
        try:
            return await super().make_request(bot, method, timeout)
        except (ProxyConnectionError, ProxyError, ProxyTimeoutError) as exc:
            # Proxy exceptions may contain credentials or a private endpoint.
            raise TelegramNetworkError(
                method=method, message=f"Proxy connection failed ({type(exc).__name__})"
            ) from None
