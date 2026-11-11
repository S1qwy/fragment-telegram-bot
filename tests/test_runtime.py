import asyncio
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetMe, GetUpdates
from aiogram.types import Update, User
from aiohttp_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError

from fragment_bot.bot import initialize_bot, poll, worker
from fragment_bot.catalog import Product
from fragment_bot.config import Config
from fragment_bot.fulfillment import Fulfillment
from fragment_bot.payments import Checkout, DemoGateway, TelegramStarsGateway
from fragment_bot.store import Store
from fragment_bot.transport import ProxySession


@pytest.mark.parametrize("error_type", [ProxyConnectionError, ProxyError, ProxyTimeoutError])
async def test_proxy_failure_is_recoverable_and_does_not_expose_endpoint(monkeypatch, error_type):
    monkeypatch.setattr(
        AiohttpSession,
        "make_request",
        AsyncMock(side_effect=error_type("socks5://user:secret@private-host:1080")),
    )
    async with Bot("123:dummy", session=ProxySession()) as bot:
        with pytest.raises(TelegramNetworkError) as failure:
            await bot.get_me()
    assert "secret" not in str(failure.value)
    assert "private-host" not in str(failure.value)


async def test_startup_waits_for_network_and_preserves_bot_binding(tmp_path, monkeypatch):
    config = Config("demo", tmp_path, token="123:dummy", admin_ids=frozenset({99}))
    store = Store(tmp_path, "demo")
    sleep = AsyncMock()
    monkeypatch.setattr("fragment_bot.bot.asyncio.sleep", sleep)
    me = User(id=123, is_bot=True, first_name="Shop", username="testshopbot")
    async with Bot(config.token) as bot:
        bot.get_me = AsyncMock(
            side_effect=[TelegramNetworkError(method=GetMe(), message="offline"), me]
        )
        bot.delete_webhook = AsyncMock()
        bot.set_my_commands = AsyncMock()
        assert await initialize_bot(bot, config, store) == me
        assert bot.get_me.await_count == 2
        bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=False)
        assert bot.set_my_commands.await_count == 2
    sleep.assert_awaited_once_with(1)
    store.bind_bot(123)


async def test_poll_keeps_offset_when_network_fails(tmp_path, monkeypatch):
    store = Store(tmp_path, "demo")
    async with Bot("123:dummy") as bot:
        dispatcher = Dispatcher()
        dispatcher.feed_update = AsyncMock()
        bot.get_updates = AsyncMock(
            side_effect=[
                [Update(update_id=40)],
                TelegramNetworkError(method=GetUpdates(), message="offline"),
                [Update(update_id=41)],
                asyncio.CancelledError(),
            ]
        )
        monkeypatch.setattr("fragment_bot.bot.asyncio.sleep", AsyncMock())
        with pytest.raises(asyncio.CancelledError):
            await poll(bot, dispatcher, Checkout(store, DemoGateway()), lambda *args: None)
        assert [call.kwargs["offset"] for call in bot.get_updates.await_args_list] == [
            None,
            41,
            41,
            42,
        ]
        assert dispatcher.feed_update.await_count == 2
        await dispatcher.fsm.close()


async def test_notification_outage_does_not_stop_worker_or_repeat_delivery(tmp_path, monkeypatch):
    config = Config("demo", tmp_path)
    store = Store(tmp_path, "demo")
    checkout = Checkout(store, DemoGateway())
    orders = []
    for recipient in ("firstuser", "seconduser"):
        order = checkout.create(42, Product("premium", 3, 950), recipient, "")
        DemoGateway.pay(checkout, order.id, 42)
        store.approve(order.id)
        orders.append(order)
    notify = AsyncMock(side_effect=[TelegramNetworkError(method=GetMe(), message="offline"), True])
    monkeypatch.setattr("fragment_bot.bot.notify", notify)
    monkeypatch.setattr(
        "fragment_bot.bot.asyncio.sleep", AsyncMock(side_effect=[None, asyncio.CancelledError()])
    )
    async with Bot("123:dummy") as bot:
        with pytest.raises(asyncio.CancelledError):
            await worker(bot, store, Fulfillment(config, store))
    assert notify.await_count == 2
    assert all(store.get(order.id).state == "delivered" for order in orders)
    assert store.claim() is None


@pytest.mark.parametrize("mode", ["premium_only", "off"])
async def test_strict_invoice_uses_plain_fields_and_preserves_payment(tmp_path, mode):
    store = Store(tmp_path, "test")
    async with Bot("123:dummy") as bot:
        bot.session.make_request = AsyncMock(return_value=True)
        gateway = TelegramStarsGateway(bot, icons=lambda: mode)
        order = Checkout(store, gateway).create(42, Product("stars", 50, 100), "sample", "")
        await gateway.checkout(order)
        invoice = bot.session.make_request.await_args.args[1]
        assert "⭐" not in invoice.title
        assert "⭐" not in invoice.prices[0].label
        assert invoice.payload == order.id
        assert invoice.prices[0].amount == 100
        assert invoice.reply_markup.inline_keyboard[0][0].pay
