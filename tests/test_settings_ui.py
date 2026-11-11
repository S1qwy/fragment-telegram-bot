from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from fragment_bot.catalog import DEMO_SETTINGS, SETTINGS
from fragment_bot.config import Config, ShopError
from fragment_bot.settings_ui import build_settings_router
from fragment_bot.store import Store


async def test_admin_settings_switch_edit_reset_and_demo_preset(tmp_path):
    admin = User(id=111, is_bot=False, first_name="Owner")
    visitor = User(id=222, is_bot=False, first_name="Buyer")
    config = Config(
        "demo", tmp_path, admin_ids=frozenset({admin.id}), token="123:dummy", rich_menus=False
    )
    store = Store(tmp_path, "demo")
    dispatcher = Dispatcher()
    dispatcher.include_router(build_settings_router(config, store))
    bot = Bot(config.token)
    bot.session.make_request = AsyncMock(return_value=True)

    def message(user: User, text: str) -> Message:
        return Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=user.id, type="private"),
            from_user=user,
            text=text,
        )

    async def click(data: str, *, user: User = admin):
        update = Update(
            update_id=1,
            callback_query=CallbackQuery(
                id="click",
                from_user=user,
                chat_instance="test",
                message=message(user, "Settings"),
                data=data,
            ),
        )
        await dispatcher.feed_update(bot, update)
        return bot.session.make_request.await_args.args[1]

    async def type_value(value: str):
        await dispatcher.feed_update(bot, Update(update_id=2, message=message(admin, value)))
        return bot.session.make_request.await_args.args[1]

    async with bot:
        try:
            with pytest.raises(ShopError, match="администратору"):
                await click("settings:value:allow_self:0", user=visitor)
            assert store.settings()["allow_self"] == "1"

            menu = await click("admin:settings")
            links = {
                item.callback_data for row in menu.reply_markup.inline_keyboard for item in row
            }
            assert {"settings:group:branding", "settings:group:support", "settings:demo"} <= links
            await click("settings:group:checkout")
            await click("setting:allow_self")
            await click("settings:value:allow_self:0")
            assert store.settings()["allow_self"] == "0"

            await click("setting:name")
            saved = await type_value("Store <3")
            assert store.settings()["name"] == "Store <3"
            assert "Store &lt;3" in saved.text
            await type_value("This message is outside the setting editor")
            assert store.settings()["name"] == "Store <3"

            await click("settings:demo:defaults")
            assert store.settings()["name"] == "Store <3"
            assert store.settings()["terms"] == DEMO_SETTINGS["terms"]
            await click("settings:demo:replace")
            assert store.settings()["name"] == "Store <3"
            await click("settings:demo:apply")
            assert store.settings()["name"] == DEMO_SETTINGS["name"]

            await click("settings:reset:support")
            assert store.settings()["support"] == DEMO_SETTINGS["support"]
            await click("settings:reset-ok:support")
            assert store.settings()["support"] == SETTINGS["support"][0]
        finally:
            await dispatcher.fsm.close()
