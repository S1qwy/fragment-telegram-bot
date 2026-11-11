import logging
from html import escape
from pathlib import Path

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

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
        f"Получатель  <b>@{escape(order.recipient)}</b>\n"
        f"Сумма  <b>{escape(price(order.price, order.currency))}</b>\n\n"
        f"{STATUS_ICONS[order.state]} <b>{STATUS[order.state]}</b>"
    )
    if admin:
        text += f"\n\nПокупатель  <code>{order.buyer}</code>"
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
) -> None:
    if edit:
        try:
            if message.photo and len(text.encode("utf-16-le")) // 2 <= 1024:
                await message.edit_caption(
                    caption=text, parse_mode=ParseMode.HTML, reply_markup=markup
                )
                return
            if not message.photo:
                await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
                return
        except TelegramBadRequest as exc:
            if "message is not modified" in exc.message.lower():
                return
        await clear_actions(message)
    elif banner and banner.is_file() and len(text.encode("utf-16-le")) // 2 <= 1024:
        try:
            await message.answer_photo(
                FSInputFile(banner), caption=text, parse_mode=ParseMode.HTML, reply_markup=markup
            )
        except TelegramBadRequest:
            log.warning("Telegram rejected the shop banner")
        else:
            return
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=markup)


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
