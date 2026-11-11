from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from fragment_bot.catalog import Product
from fragment_bot.config import Config
from fragment_bot.fulfillment import Fulfillment
from fragment_bot.payments import Checkout, DemoGateway
from fragment_bot.shop import shop_router
from fragment_bot.store import Store

OWNER = User(id=111, is_bot=False, first_name="Owner", username="owner")


@pytest.mark.parametrize(
    "navigation", ["support", "terms", "privacy", "faq", "orders", "show", "/pending"]
)
async def test_navigation_leaves_settings_editor_before_accepting_more_text(tmp_path, navigation):
    config = Config(
        "demo", tmp_path, admin_ids=frozenset({OWNER.id}), token="123:dummy", rich_menus=False
    )
    store = Store(tmp_path, config.mode)
    checkout = Checkout(store, DemoGateway())
    order = checkout.create(OWNER.id, Product("stars", 50, 100), "owner", "")
    dispatcher = Dispatcher()
    dispatcher.include_router(shop_router(config, store, checkout, Fulfillment(config, store)))
    bot = Bot(config.token)
    bot.session.make_request = AsyncMock(return_value=True)

    def message(text: str) -> Message:
        return Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=OWNER.id, type="private"),
            from_user=OWNER,
            text=text,
        )

    async def click(data: str) -> None:
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=1,
                callback_query=CallbackQuery(
                    id="click",
                    from_user=OWNER,
                    chat_instance="test",
                    message=message("menu"),
                    data=data,
                ),
            ),
        )

    async with bot:
        try:
            await click("setting:name")
            if navigation.startswith("/"):
                await dispatcher.feed_update(bot, Update(update_id=2, message=message(navigation)))
            else:
                await click(f"show:{order.id}" if navigation == "show" else navigation)
            context = dispatcher.fsm.get_context(bot=bot, chat_id=OWNER.id, user_id=OWNER.id)
            assert await context.get_state() is None
            await dispatcher.feed_update(
                bot, Update(update_id=3, message=message("This is not a setting value"))
            )
            assert store.settings()["name"] == "Fragment Shop"
        finally:
            await dispatcher.fsm.close()


async def test_admin_read_only_navigation_leaves_product_editor(tmp_path):
    config = Config(
        "demo", tmp_path, admin_ids=frozenset({OWNER.id}), token="123:dummy", rich_menus=False
    )
    store = Store(tmp_path, config.mode)
    dispatcher = Dispatcher()
    bot = Bot(config.token)
    bot.session.make_request = AsyncMock(return_value=True)
    dispatcher.include_router(
        shop_router(config, store, Checkout(store, DemoGateway(bot)), Fulfillment(config, store))
    )

    def message(text: str) -> Message:
        return Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=OWNER.id, type="private"),
            from_user=OWNER,
            text=text,
        )

    async with bot:
        try:
            await dispatcher.feed_update(
                bot,
                Update(
                    update_id=1,
                    callback_query=CallbackQuery(
                        id="click",
                        from_user=OWNER,
                        chat_instance="test",
                        message=message("admin"),
                        data="adminprice:stars:50",
                    ),
                ),
            )
            await dispatcher.feed_update(
                bot, Update(update_id=2, message=message("/paymenterrors"))
            )
            await dispatcher.feed_update(bot, Update(update_id=3, message=message("300")))
            assert Product("stars", 50, 100) in store.products()
        finally:
            await dispatcher.fsm.close()
