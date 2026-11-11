from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest

from fragment_bot.views import button, keys, render_markup, render_text, send_html


def test_render_markup_supports_unicode_premium_only_and_plain_modes():
    markup = keys(
        [
            button("⚙️ Настройки", "admin:settings"),
            button("🫨 Новинка", "promo"),
            button("100 ⭐", "buy:stars:50"),
        ]
    )

    assert render_markup(markup, False) is markup
    rich, unknown, amount = render_markup(markup, "premium_only").inline_keyboard[0]
    assert rich.text == "Настройки"
    assert rich.icon_custom_emoji_id
    assert unknown.text == "Новинка"
    assert unknown.icon_custom_emoji_id is None
    assert amount.text == "100 XTR"
    assert amount.icon_custom_emoji_id

    plain = render_markup(markup, "off").inline_keyboard[0]
    assert [item.text for item in plain] == ["Настройки", "Новинка", "100 XTR"]
    assert all(not item.icon_custom_emoji_id for item in plain)


def test_render_text_preserves_non_decorative_user_text():
    html = "<b>🛡 Политика</b>\n🫨 Новинка\nСумма 100 ⭐\nПроизвольный текст 🙂"
    strict = render_text(html, "premium_only")
    assert '<tg-emoji emoji-id="' in strict
    assert "🫨" not in strict
    assert "100 XTR" in strict
    assert "Произвольный текст 🙂" in strict
    assert render_text(html, "off") == (
        "<b>Политика</b>\nНовинка\nСумма 100 XTR\nПроизвольный текст 🙂"
    )


async def test_premium_only_retries_with_plain_message_when_emoji_unavailable():
    async def request(method, *, request_timeout):
        assert request_timeout == 5
        if method.reply_markup.inline_keyboard[0][0].icon_custom_emoji_id:
            raise TelegramBadRequest(method=method, message="BUTTON_CUSTOM_EMOJI_INVALID")

    bot = AsyncMock(side_effect=request)
    markup = keys([button("📄 Условия", "terms")])
    await send_html(bot, 1, "<b>📄 Условия</b>", markup, icons="premium_only", request_timeout=5)
    decorated, plain = [call.args[0] for call in bot.await_args_list]
    assert "<tg-emoji" in decorated.text
    assert plain.text == "<b>Условия</b>"
    assert plain.reply_markup.inline_keyboard[0][0].text == "Условия"
    assert plain.reply_markup.inline_keyboard[0][0].callback_data == "terms"


async def test_home_cover_change_replaces_message_media():
    from datetime import UTC, datetime

    from aiogram import Bot
    from aiogram.methods import EditMessageMedia
    from aiogram.types import Chat, Message, PhotoSize

    from fragment_bot.views import screen

    bot = Bot("123:dummy")
    async with bot:
        bot.session.make_request = AsyncMock(return_value=True)
        message = Message(
            message_id=8,
            date=datetime.now(UTC),
            chat=Chat(id=1, type="private"),
            photo=[PhotoSize(file_id="old", file_unique_id="old", width=400, height=200)],
        ).as_(bot)
        await screen(
            message,
            "<b>📄 Новый экран</b>",
            keys([button("🏠 Главное меню", "menu")]),
            edit=True,
            banner="https://example.com/cover.jpg",
            replace_media=True,
            icons="premium_only",
        )
        request = bot.session.make_request.await_args.args[1]
        assert isinstance(request, EditMessageMedia)
        assert request.media.media == "https://example.com/cover.jpg"
        assert "<tg-emoji" in request.media.caption
        assert request.reply_markup.inline_keyboard[0][0].text == "Главное меню"


async def test_home_text_style_removes_old_photo_after_successful_send():
    from datetime import UTC, datetime

    from aiogram import Bot
    from aiogram.methods import DeleteMessage, SendMessage
    from aiogram.types import Chat, Message, PhotoSize

    from fragment_bot.views import screen

    bot = Bot("123:dummy")
    async with bot:
        bot.session.make_request = AsyncMock(return_value=True)
        message = Message(
            message_id=8,
            date=datetime.now(UTC),
            chat=Chat(id=1, type="private"),
            photo=[PhotoSize(file_id="old", file_unique_id="old", width=400, height=200)],
        ).as_(bot)
        await screen(
            message,
            "Текстовый экран",
            keys([button("Главное меню", "menu")]),
            edit=True,
            replace_media=True,
        )
        sent, removed = [call.args[1] for call in bot.session.make_request.await_args_list]
        assert isinstance(sent, SendMessage)
        assert isinstance(removed, DeleteMessage)
        assert removed.message_id == 8


async def test_home_keeps_old_photo_when_replacement_send_fails():
    from datetime import UTC, datetime

    import pytest
    from aiogram import Bot
    from aiogram.exceptions import TelegramNetworkError
    from aiogram.methods import SendMessage
    from aiogram.types import Chat, Message, PhotoSize

    from fragment_bot.views import screen

    bot = Bot("123:dummy")
    async with bot:

        async def fail(_bot, method, timeout=None):
            assert isinstance(method, SendMessage)
            raise TelegramNetworkError(method=method, message="Request timeout error")

        bot.session.make_request = AsyncMock(side_effect=fail)
        message = Message(
            message_id=8,
            date=datetime.now(UTC),
            chat=Chat(id=1, type="private"),
            photo=[PhotoSize(file_id="old", file_unique_id="old", width=400, height=200)],
        ).as_(bot)
        with pytest.raises(TelegramNetworkError):
            await screen(
                message,
                "Текстовый экран",
                keys([button("Главное меню", "menu")]),
                edit=True,
                replace_media=True,
            )
        assert bot.session.make_request.await_count == 1
