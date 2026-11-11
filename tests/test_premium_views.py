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
