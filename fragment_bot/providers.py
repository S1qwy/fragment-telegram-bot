import asyncio
import importlib
import inspect
from contextlib import asynccontextmanager

from .config import ShopError
from .provider_config import PROVIDERS, ProviderConfig
from .provider_identity import PayerIdentity

_locks = {}


@asynccontextmanager
async def provider_client(config: ProviderConfig, provider: str):
    spec = PROVIDERS.get(provider)
    if not spec or not spec["module"]:
        raise ShopError("Для XTR используется TelegramStarsGateway.")
    # Some vendor SDKs keep process-global credentials (YooKassa).
    lock = _locks.setdefault(provider, asyncio.Lock())
    async with lock:
        with config.bind():
            try:
                module = importlib.import_module(f"fragment_bot.vendor.bedolaga.{spec['module']}")
            except ImportError:
                raise ShopError("Установите зависимости: uv sync --extra providers.") from None
            client = getattr(module, spec["class"])()
            try:
                yield client
            finally:
                close = getattr(client, "close", None)
                if close:
                    result = close()
                    if inspect.isawaitable(result):
                        await result


async def provider_call(config, provider, method, params):
    spec = PROVIDERS.get(provider)
    if not spec or method not in spec["methods"]:
        raise ShopError("Метод отсутствует в публичном API провайдера.")
    if method.startswith(("create", "generate_payment", "build_payment", "charge", "subscribe")):
        if not config.values().get(provider.upper() + "_ENABLED", False):
            raise ShopError("Провайдер отключён. Сначала настройте и включите его.")
    if provider == "tribute" and method in {"refund_payment", "get_payment_status"}:
        raise ShopError(
            "Upstream Tribute не реализует реальный запрос статуса/возврата. Используйте кабинет провайдера и подписанные события."
        )
    async with provider_client(config, provider) as client:
        if method.startswith(("create", "generate_payment", "charge", "subscribe")) and hasattr(
            client, "_max_retries"
        ):
            client._max_retries = 1
        arguments = dict(params)
        if provider == "platega" and isinstance(arguments.get("payer"), dict):
            arguments["payer"] = PayerIdentity(**arguments["payer"])
        function = getattr(client, method)
        try:
            inspect.signature(function).bind(**arguments)
        except TypeError:
            raise ShopError("Проверьте параметры метода по provider-info.") from None
        result = function(**arguments)
        return await result if inspect.isawaitable(result) else result
