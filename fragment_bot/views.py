import logging
from html import escape
from pathlib import Path

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramServerError,
)
from aiogram.methods import EditMessageText, SendRichMessage
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputRichMessage,
    Message,
)

from .catalog import Product
from .store import STATUS, Order

log = logging.getLogger(__name__)
PRODUCTS = {
    "stars": ("⭐", "Telegram Stars", "Пополнение Stars по публичному @username."),
    "premium": ("💎", "Telegram Premium", "Подарочная подписка на выбранный срок."),
    "ton": ("💠", "GRAM для Telegram Ads", "Пополнение рекламного баланса Telegram Ads."),
}
MODE_LABELS = {"demo": "Демо · без списаний", "test": "Тестовая сеть Telegram"}
STATUS_ICONS = {
    "unpaid": "🕓",
    "paid": "💳",
    "queued": "⏳",
    "sending": "⏳",
    "review": "🔎",
    "prepared": "📝",
    "prepared_void": "↩️",
    "delivered": "✅",
    "refunding": "↩️",
    "refunded": "↩️",
}


# IDs checked through getCustomEmojiStickers and the public TgAndroidIcons pack.
PREMIUM_ICONS = {
    "🏪": "5983399041197675256",
    "🛍": "5983399041197675256",
    "👤": "5771887475421090729",
    "💬": "5886666250158870040",
    "⚙": "5877260593903177342",
    "🛠": "5988023995125993550",
    "📄": "5839323457015256759",
    "🧾": "5839323457015256759",
    "💳": "5967548335542767952",
    "⭐": "5874948844935974490",
    "💎": "5807465992363710697",
    "📦": "5924720918826848520",
    "🎨": "5814690801665446789",
    "📊": "5877485980901971030",
    "🔎": "5874960879434338403",
    "📥": "5877307202888273539",
    "✅": "5920052658743283381",
    "‹": "5875082500023258804",
    "ℹ": "5258503720928288433",
}


def premium_markup(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    rows = []
    for row in markup.inline_keyboard:
        updated = []
        for item in row:
            prefix, _, label = item.text.partition(" ")
            icon = PREMIUM_ICONS.get(prefix.replace("\ufe0f", ""))
            if icon and label:
                item = item.model_copy(update={"text": label, "icon_custom_emoji_id": icon})
            updated.append(item)
        rows.append(updated)
    return keys(*rows)


def rich_table(title: str, rows: list[tuple[str, str]], footer: str = "") -> str:
    cells = "".join(
        f"<tr><td>{escape(key)}</td><td>{escape(value)}</td></tr>" for key, value in rows
    )
    return f"<h3>{escape(title)}</h3><table>{cells}</table>" + (
        f"<footer>{escape(footer)}</footer>" if footer else ""
    )


def button(text: str, action: str, *, style: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=action, style=style)


def keys(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def back(action: str = "menu", text: str = "Назад") -> list[InlineKeyboardButton]:
    return [button(f"‹ {text}", action)]


def price(amount: int, currency: str = "XTR") -> str:
    unit = "⭐" if currency == "XTR" else currency
    return f"{amount:,}".replace(",", " ") + f" {unit}"


def package(product: Product) -> str:
    unit = {"stars": "Stars", "premium": "мес.", "ton": "GRAM"}[product.kind]
    return f"{product.quantity:,}".replace(",", " ") + f" {unit}"


def mode_note(mode: str) -> str:
    return f"\n\n<i>{MODE_LABELS[mode]}</i>" if mode in MODE_LABELS else ""


def order_title(order: Order) -> str:
    icon, name, _ = PRODUCTS[order.kind]
    return f"{icon} {name} · {package(order.product)}"


def order_card(order: Order, *, admin: bool = False) -> str:
    text = (
        f"<b>🧾 Заказ</b> <code>#{escape(order.id)}</code>\n\n"
        f"<b>{order_title(order)}</b>\n"
        f"<blockquote>Получатель  <b>@{escape(order.recipient)}</b>\n"
        f"Сумма  <b>{escape(price(order.price, order.currency))}</b></blockquote>\n"
        f"{STATUS_ICONS[order.state]} <b>{STATUS[order.state]}</b>"
    )
    if admin:
        text += f"\n\nПокупатель  <code>{order.buyer}</code>"
        if order.charge_id:
            text += f"\nПровайдер  <code>{escape(order.provider)}</code>"
            text += f"\nПлатёж  <code>{escape(order.charge_id)}</code>"
        if order.operation_id:
            text += f"\nОперация Fragment  <code>{escape(order.operation_id)}</code>"
        if order.transaction_id:
            text += f"\nТранзакция  <code>{escape(order.transaction_id)}</code>"
        if order.note:
            text += f"\n<i>{escape(order.note)}</i>"
    return text


def buyer_order_keys(order: Order) -> InlineKeyboardMarkup:
    rows = []
    if order.state == "unpaid" and not order.checkout_id:
        rows.append([button("💳 Открыть счёт", f"invoice:{order.id}", style="primary")])
    rows.extend(
        [
            [button("↻ Обновить", f"show:{order.id}"), button("💬 Поддержка", "support")],
            back("orders", "К заказам"),
        ]
    )
    return keys(*rows)


async def clear_actions(message: Message) -> None:
    try:
        await message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


async def screen(
    message: Message,
    text: str,
    markup: InlineKeyboardMarkup,
    *,
    edit: bool = False,
    banner: Path | None = None,
    rich: str | None = None,
    icons: bool = False,
) -> None:
    decorated = premium_markup(markup) if icons else markup

    async def request(method, *, timeout: int | None = None):
        try:
            return await message.bot(method, request_timeout=timeout)
        except TelegramBadRequest as exc:
            error = exc.message.lower()
            if decorated == markup or not any(term in error for term in ("emoji", "button")):
                raise
            return await message.bot(
                method.model_copy(update={"reply_markup": markup}), request_timeout=timeout
            )

    if rich and not (edit and message.photo):
        rich_message = InputRichMessage(html=rich)
        if edit:
            method = EditMessageText(
                chat_id=message.chat.id,
                message_id=message.message_id,
                rich_message=rich_message,
                reply_markup=decorated,
                parse_mode=None,
            )
        else:
            method = SendRichMessage(
                chat_id=message.chat.id,
                rich_message=rich_message,
                reply_markup=decorated,
            )
        try:
            await request(method, timeout=4)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in exc.message.lower():
                return
            log.info("Telegram rejected the rich screen, using HTML")
        except (TelegramNotFound, TelegramNetworkError, TelegramServerError):
            log.info("Rich screen unavailable, using HTML")

    if edit:
        try:
            if message.photo and len(text.encode("utf-16-le")) // 2 <= 1024:
                await request(
                    message.edit_caption(
                        caption=text, parse_mode=ParseMode.HTML, reply_markup=decorated
                    )
                )
                return
            if not message.photo:
                await request(
                    message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=decorated)
                )
                return
        except TelegramBadRequest as exc:
            if "message is not modified" in exc.message.lower():
                return
        await clear_actions(message)
    elif banner and banner.is_file() and len(text.encode("utf-16-le")) // 2 <= 1024:
        try:
            photo = message.answer_photo(
                FSInputFile(banner), caption=text, parse_mode=ParseMode.HTML, reply_markup=decorated
            )
            await request(photo, timeout=4)
        except (TelegramBadRequest, TelegramNetworkError, TelegramServerError) as exc:
            log.warning(
                "Shop banner unavailable, sending the menu as text (%s)", type(exc).__name__
            )
        else:
            return
    await request(message.answer(text, parse_mode=ParseMode.HTML, reply_markup=decorated))


async def notify(bot: Bot, order: Order) -> None:
    try:
        await bot.send_message(
            order.buyer,
            order_card(order),
            parse_mode=ParseMode.HTML,
            reply_markup=buyer_order_keys(order),
            request_timeout=3,
        )
    except TelegramAPIError:
        log.info("Could not notify buyer for order %s", order.id)
