from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Update
from test_bot import BUYER, chat_message

from fragment_bot.catalog import Product, setting_value
from fragment_bot.config import Config, ShopError
from fragment_bot.fulfillment import Fulfillment
from fragment_bot.payments import Checkout, DemoGateway
from fragment_bot.shop import shop_router
from fragment_bot.store import Store


def rate_store(tmp_path):
    store = Store(tmp_path, "demo")
    store.configure("pricing_mode", "rates")
    return store


def test_rates_presets_custom_and_rounding(tmp_path):
    store = rate_store(tmp_path)
    assert [p.quantity for p in store.sale_products() if p.kind == "stars"] == [
        50,
        100,
        250,
        500,
        1000,
    ]
    assert [p.quantity for p in store.sale_products() if p.kind == "premium"] == [3, 6, 12]
    store.configure("stars_rate", "1.234567")
    assert store.quote("stars", 51) == Product("stars", 51, 63)
    store.configure("ton_rate", "3.000001")
    assert store.quote("ton", 7).price == 22
    store.configure("premium_price_12", "777")
    assert store.quote("premium", 12).price == 777
    with pytest.raises(ShopError):
        store.quote("stars", 1)
    with pytest.raises(ShopError):
        store.quote("stars", 100000)


def test_reprice_disable_and_old_invoice(tmp_path):
    store = rate_store(tmp_path)
    checkout = Checkout(store, DemoGateway())
    product = store.quote("stars", 51)
    order = checkout.create(42, product, "sample", "")
    store.configure("stars_rate", "3")
    with pytest.raises(ShopError, match="изменились"):
        checkout.create(42, product, "sample", "")
    assert store.get(order.id).price == 102
    store.configure("enabled_stars", "0")
    assert not any(p.kind == "stars" for p in store.sale_products())
    assert not checkout.authorize(order.id, 42, 102, "XTR", "q")
    with pytest.raises(ShopError, match="отключён"):
        checkout.create(42, product, "sample", "")
    store.configure("enabled_stars", "1")
    store.configure("stars_custom", "0")
    with pytest.raises(ShopError, match="пакет"):
        store.quote("stars", 51)
    assert store.quote("stars", 50).price == 150


@pytest.mark.parametrize(
    "key,value",
    [
        ("stars_rate", "NaN"),
        ("ton_rate", "-1"),
        ("stars_rate", "0"),
        ("stars_rate", "1e2"),
        ("ton_rate", "0.1234567"),
        ("premium_price_6", "0"),
        ("premium_price_3", "10001"),
        ("stars_packages", "50,100"),
        ("stars_packages", "50,100,250,500,500"),
        ("stars_packages", "1,50,100,250,500"),
        ("enabled_ton", "yes"),
    ],
)
def test_invalid_pricing_settings(key, value):
    with pytest.raises(ShopError):
        setting_value(key, value)


@pytest.mark.parametrize("kind,quantity", [("stars", 57), ("ton", 7)])
async def test_custom_quantity_to_real_order(tmp_path, kind, quantity):
    store = rate_store(tmp_path)
    config = Config("demo", tmp_path, token="123:dummy")
    async with Bot(config.token) as bot:
        bot.session.make_request = AsyncMock(return_value=True)
        checkout = Checkout(store, DemoGateway(bot))
        dispatcher = Dispatcher()
        dispatcher.include_router(shop_router(config, store, checkout, Fulfillment(config, store)))

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
            await callback(f"custom:{kind}")
            await dispatcher.feed_update(
                bot, Update(update_id=2, message=chat_message(str(quantity)))
            )
            await dispatcher.feed_update(bot, Update(update_id=3, message=chat_message("@sample")))
            preview = next(
                c.args[1]
                for c in reversed(bot.session.make_request.await_args_list)
                if isinstance(c.args[1], SendMessage)
            )
            await callback(preview.reply_markup.inline_keyboard[0][0].callback_data)
            order = store.orders(buyer=BUYER.id)[0]
            assert order.quantity == quantity
            assert order.price == store.quote(kind, quantity).price
        finally:
            await dispatcher.fsm.close()
