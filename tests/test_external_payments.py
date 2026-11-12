import importlib
import inspect
from unittest.mock import AsyncMock

import pytest

from fragment_bot.catalog import Product
from fragment_bot.config import ShopError
from fragment_bot.external_payments import HOSTED, PAID, ExternalGateway, available
from fragment_bot.payments import Checkout
from fragment_bot.provider_config import FIELDS, PROVIDERS, ProviderConfig
from fragment_bot.store import Store


def gateway(tmp_path, name="cryptobot"):
    store = Store(tmp_path, "demo")
    config = ProviderConfig(store)
    config.set(name.upper() + "_CHECKOUT_RATE", "0.01")
    config.set(name.upper() + "_ENABLED", True)
    minimum = name.upper() + "_MIN_AMOUNT_KOPEKS"
    if minimum in FIELDS:
        config.set(minimum, 1)
    return store, config, ExternalGateway(store, name, email="buyer@example.com")


@pytest.mark.parametrize("name", sorted(HOSTED))
def test_every_hosted_builder_matches_real_client_signature(tmp_path, name):
    store, config, service = gateway(tmp_path, name)
    order = Checkout(store, service).create(42, Product("stars", 50, 100), "sample", "")
    method, params = service.create_args(order)
    spec = PROVIDERS[name]
    with config.bind():
        cls = getattr(
            importlib.import_module("fragment_bot.vendor.bedolaga." + spec["module"]), spec["class"]
        )
        inspect.signature(getattr(cls, method)).bind(None, **params)
    assert name in PAID
    assert order.product.price == 100
    assert order.price == 100


async def test_external_invoice_is_single_and_capture_checks_actual_money(tmp_path):
    store, config, service = gateway(tmp_path)
    order = Checkout(store, service).create(42, Product("stars", 50, 100), "sample", "")
    service.invoke = AsyncMock(
        return_value={"invoice_id": 123, "bot_invoice_url": "https://t.me/CryptoBot?start=123"}
    )
    await service.checkout(order)
    await service.checkout(order)
    assert service.invoke.await_count == 1
    service.remote = AsyncMock(
        return_value=[
            {"invoice_id": 123, "status": "paid", "amount": "1.00", "asset": order.currency}
        ]
    )
    assert await service.check(order)
    assert not await service.check(order)
    assert store.get(order.id).state == "paid"


@pytest.mark.parametrize(
    "changed", [{"invoice_id": 999}, {"amount": "99.00"}, {"asset": "BAD"}, {"amount": "NaN"}]
)
async def test_mismatched_provider_payment_never_captures(tmp_path, changed):
    store, config, service = gateway(tmp_path)
    order = Checkout(store, service).create(42, Product("stars", 50, 100), "sample", "")
    service.invoke = AsyncMock(
        return_value={"invoice_id": 123, "bot_invoice_url": "https://t.me/CryptoBot?start=123"}
    )
    await service.checkout(order)
    remote = {
        "invoice_id": 123,
        "status": "paid",
        "amount": "1.00",
        "asset": order.currency,
        **changed,
    }
    service.remote = AsyncMock(return_value=[remote])
    with pytest.raises(ShopError):
        await service.check(order)
    assert store.get(order.id).state == "unpaid"


async def test_unknown_create_outcome_cannot_send_second_request(tmp_path):
    store, config, service = gateway(tmp_path)
    order = Checkout(store, service).create(42, Product("stars", 50, 100), "sample", "")
    service.invoke = AsyncMock(side_effect=TimeoutError())
    with pytest.raises(ShopError):
        await service.checkout(order)
    with pytest.raises(ShopError):
        await service.checkout(order)
    assert service.invoke.await_count == 1


def test_incomplete_credentials_hide_gateway_and_rates_validate(tmp_path):
    store, config, service = gateway(tmp_path)
    assert "cryptobot" not in available(config)
    config.set("CRYPTOBOT_API_TOKEN", "test-token")
    assert "cryptobot" in available(config)
    for value in ("0", "NaN", "Infinity", "-1", "0.1234567"):
        with pytest.raises(ShopError):
            config.set("CRYPTOBOT_CHECKOUT_RATE", value)


async def test_telegram_checkout_stays_xtr_with_external_provider_configured(tmp_path):
    from aiogram import Bot, Dispatcher
    from aiogram.methods import SendInvoice, SendMessage
    from aiogram.types import CallbackQuery, Update
    from test_bot import BUYER, chat_message

    from fragment_bot.config import Config
    from fragment_bot.fulfillment import Fulfillment
    from fragment_bot.payments import TelegramStarsGateway
    from fragment_bot.shop import shop_router

    store = Store(tmp_path, "live")
    store.configure("terms", "Live terms")
    store.configure("support", "Support")
    store.configure("open", "1")
    settings = ProviderConfig(store)
    settings.set("CRYPTOBOT_ENABLED", True)
    settings.set("CRYPTOBOT_API_TOKEN", "test-token")
    settings.set("CRYPTOBOT_CHECKOUT_RATE", "2.5")
    config = Config("live", tmp_path, token="123:dummy")
    fulfillment = Fulfillment(config, store)
    fulfillment.verify_recipient = AsyncMock(return_value=None)
    async with Bot(config.token) as bot:
        bot.session.make_request = AsyncMock(return_value=True)
        dispatcher = Dispatcher()
        dispatcher.include_router(
            shop_router(config, store, Checkout(store, TelegramStarsGateway(bot)), fulfillment)
        )

        async def callback(data):
            await dispatcher.feed_update(
                bot,
                Update(
                    update_id=1,
                    callback_query=CallbackQuery(
                        id="q",
                        from_user=BUYER,
                        chat_instance="c",
                        data=data,
                        message=chat_message(),
                    ),
                ),
            )

        try:
            await callback("buy:stars:50")
            await dispatcher.feed_update(bot, Update(update_id=2, message=chat_message("@sample")))
            preview = next(
                c.args[1]
                for c in reversed(bot.session.make_request.await_args_list)
                if isinstance(c.args[1], SendMessage)
            )
            await callback(preview.reply_markup.inline_keyboard[0][0].callback_data)
            order = store.orders(buyer=BUYER.id)[0]
            assert (order.provider, order.currency, order.price) == ("telegram_stars", "XTR", 100)
            assert any(
                isinstance(c.args[1], SendInvoice) for c in bot.session.make_request.await_args_list
            )
        finally:
            await dispatcher.fsm.close()


async def test_provider_http_uses_local_proxy_without_network(monkeypatch):
    from fragment_bot import provider_http

    monkeypatch.setenv("PAYMENT_PROXY", "http://127.0.0.1:10809")
    async with provider_http.ClientSession() as session:
        assert str(session._default_proxy) == "http://127.0.0.1:10809"
