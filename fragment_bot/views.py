import logging
import re
from html import escape
from pathlib import Path
from typing import Literal, cast
from unicodedata import category

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramServerError,
)
from aiogram.methods import EditMessageText, SendMessage, SendRichMessage, TelegramMethod
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
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


# Curated IDs verified via getStickerSet and getCustomEmojiStickers.
# Sources: TgAndroidIcons, tgmacicons and vector_icons_by_fStikBot.
PREMIUM_ICONS = {
    "🏪": "5983399041197675256",
    "👤": "5771887475421090729",
    "💬": "5886666250158870040",
    "⚙": "5877260593903177342",
    "🛠": "5988023995125993550",
    "📄": "5839323457015256759",
    "💳": "5967548335542767952",
    "⭐": "5874948844935974490",
    "💎": "5807465992363710697",
    "📦": "5924720918826848520",
    "🎨": "5814690801665446789",
    "📊": "5877485980901971030",
    "🔎": "5874960879434338403",
    "📥": "5877307202888273539",
    "✅": "5920052658743283381",
    "⬅": "5875082500023258804",
    "➡": "5877468380125990242",
    "ℹ": "5879785854284599288",
    "🏠": "5967822972931542886",
    "🔒": "5832546462478635761",
    "🛡": "5926783847453692661",
    "❓": "5873121512445187130",
    "📖": "5897850551156084824",
    "📚": "5260512129240276089",
    "📝": "5886330010054168711",
    "🧪": "5897607722295103204",
    "➕": "5877219383691972108",
    "✏": "5879841310902324730",
    "🔄": "5839200986022812209",
    "🏷": "5854776233950188167",
    "👋": "5994750571041525522",
    "🕓": "5776213190387961618",
    "⏲": "5960751816084820359",
    "🖼": "5888799736508454231",
    "🔔": "5909201569898827582",
    "🟢": "5339135753316222622",
    "🔴": "5967333011652350314",
    "❌": "5778527486270770928",
    "💰": "5811989245761426317",
    "👁": "5960714428394507968",
    "🔗": "5877465816030515018",
    "🎁": "6032937473162614352",
    "🚫": "5872829476143894491",
    "⚡": "5843553939672274145",
}
_ICON_ALIASES = {
    "🛍": "🏪",
    "🧾": "📄",
    "💠": "💎",
    "‹": "⬅",
    "›": "➡",
    "↩": "⬅",
    "↻": "🔄",
    "+": "➕",
    "＋": "➕",
    "⏳": "⏲",
    "🔐": "🔒",
    "🔍": "🔎",
}
_VARIATION_EMOJI = frozenset("⭐⬅➡ℹ✏⏲⚡")
_ACTION_ICONS = {"buy": "📦", "confirm": "✅", "invoice": "💳", "demo": "🧪"}
_HEADING_ICON = re.compile(
    r"(?P<start>^|\n|<b>|<h[1-6]>|<blockquote>|<p>)(?P<icon>[^\s<>]+) (?=\S)"
)
_XTR_SYMBOL = re.compile(r"(?<=\d) ⭐(?=[\s<]|$)")

IconMode = Literal["premium", "premium_only", "unicode", "off"]
IconPreference = IconMode | bool


def resolve_icon_mode(preference: str, inherited: bool) -> IconMode:
    if preference in {"premium", "premium_only", "unicode", "off"}:
        return cast(IconMode, preference)
    return "premium" if inherited else "unicode"


def _icon_glyph(token: str) -> str:
    normalized = token.replace("\ufe0f", "")
    return _ICON_ALIASES.get(normalized, normalized)


def _decorative(token: str) -> bool:
    return _icon_glyph(token) in PREMIUM_ICONS or (
        bool(token)
        and all(
            category(char) in {"So", "Sk", "Mn", "Cf"}
            # Older Python Unicode tables classify newly added emoji as unassigned.
            or (category(char) == "Cn" and "\U0001f000" <= char <= "\U0001faff")
            for char in token
        )
    )


def render_markup(markup: InlineKeyboardMarkup, icons: IconPreference) -> InlineKeyboardMarkup:
    mode = resolve_icon_mode("inherit", icons) if isinstance(icons, bool) else icons
    if mode == "unicode":
        if not any(item.icon_custom_emoji_id for row in markup.inline_keyboard for item in row):
            return markup
        return keys(
            *[
                [item.model_copy(update={"icon_custom_emoji_id": None}) for item in row]
                for row in markup.inline_keyboard
            ]
        )
    rows = []
    for row in markup.inline_keyboard:
        buttons = []
        for item in row:
            prefix, _, label = item.text.partition(" ")
            decorative = bool(label) and _decorative(prefix)
            suffix_arrow = item.text.endswith(" ›")
            icon_id = None
            if mode != "off":
                glyph = (
                    _icon_glyph(prefix)
                    if decorative
                    else (
                        "➡"
                        if suffix_arrow
                        else _ACTION_ICONS.get((item.callback_data or "").partition(":")[0], "")
                    )
                )
                icon_id = item.icon_custom_emoji_id or PREMIUM_ICONS.get(glyph)
            text = (
                label if decorative and (icon_id or mode in {"off", "premium_only"}) else item.text
            )
            if suffix_arrow and (icon_id or mode in {"off", "premium_only"}):
                text = text.removesuffix(" ›")
            if mode in {"off", "premium_only"}:
                text = _XTR_SYMBOL.sub(" XTR", text)
            buttons.append(item.model_copy(update={"text": text, "icon_custom_emoji_id": icon_id}))
        rows.append(buttons)
    return keys(*rows)


def premium_markup(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    return render_markup(markup, "premium")


def render_text(html: str, icons: IconPreference) -> str:
    mode = resolve_icon_mode("inherit", icons) if isinstance(icons, bool) else icons
    if mode == "unicode":
        return html

    def replace(match: re.Match[str]) -> str:
        token = match["icon"]
        glyph = _icon_glyph(token)
        icon_id = PREMIUM_ICONS.get(glyph)
        if mode != "off" and icon_id:
            fallback = glyph + ("\ufe0f" if glyph in _VARIATION_EMOJI else "")
            return f'{match["start"]}<tg-emoji emoji-id="{icon_id}">{fallback}</tg-emoji> '
        if mode in {"off", "premium_only"} and _decorative(token):
            return match["start"]
        return match[0]

    result = _HEADING_ICON.sub(replace, html)
    return _XTR_SYMBOL.sub(" XTR", result) if mode in {"off", "premium_only"} else result


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
    if currency != "XTR":
        return f"{amount // 100:,}.{amount % 100:02d} {currency}".replace(",", " ")
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
    if (
        order.state == "unpaid"
        and not order.checkout_id
        and order.provider in {"demo", "telegram_stars"}
    ):
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


async def request_with_icon_fallback(
    bot: Bot,
    method: TelegramMethod,
    fallback: dict[str, object] | None,
    *,
    timeout: int | None = None,
) -> None:
    try:
        await bot(method, request_timeout=timeout)
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        error = exc.message.lower()
        if not fallback or not any(
            term in error for term in ("emoji", "button", "entity", "entities")
        ):
            raise
        await bot(method.model_copy(update=fallback), request_timeout=timeout)


def _fallback_mode(mode: IconMode) -> IconMode:
    return "off" if mode == "premium_only" else "unicode"


async def screen(
    message: Message,
    text: str,
    markup: InlineKeyboardMarkup,
    *,
    edit: bool = False,
    banner: Path | str | None = None,
    rich: str | None = None,
    icons: IconPreference = False,
    replace_media: bool = False,
) -> None:
    mode = resolve_icon_mode("inherit", icons) if isinstance(icons, bool) else icons
    fallback_mode = _fallback_mode(mode)
    decorated = render_markup(markup, mode)
    fallback_markup = render_markup(markup, fallback_mode)
    decorated_text = render_text(text, mode)
    fallback_text = render_text(text, fallback_mode)

    async def request(
        method: TelegramMethod, field: str, fallback_value, *, timeout: int | None = None
    ):
        fallback = None
        if mode in {"premium", "premium_only"} and (
            decorated != fallback_markup or getattr(method, field) != fallback_value
        ):
            fallback = {field: fallback_value, "reply_markup": fallback_markup}
        await request_with_icon_fallback(message.bot, method, fallback, timeout=timeout)

    caption_fits = len(decorated_text.encode("utf-16-le")) // 2 <= 1024
    photo = None
    if isinstance(banner, Path) and banner.is_file():
        photo = FSInputFile(banner)
    elif isinstance(banner, str) and banner.startswith("https://"):
        photo = banner

    if edit and replace_media and (message.photo or photo):
        if photo and caption_fits and not rich:
            media = InputMediaPhoto(media=photo, caption=decorated_text, parse_mode=ParseMode.HTML)
            try:
                await request(
                    message.edit_media(media, reply_markup=decorated),
                    "media",
                    media.model_copy(update={"caption": fallback_text}),
                    timeout=4,
                )
                return
            except TelegramBadRequest as exc:
                if "message is not modified" in exc.message.lower():
                    return
                log.info("Home cover could not be replaced, sending a fresh menu")
            except (TelegramNetworkError, TelegramServerError):
                log.info("Home cover update unavailable, sending a fresh menu")
        await screen(message, text, markup, banner=banner, rich=rich, icons=icons)
        try:
            await message.delete()
        except (TelegramBadRequest, TelegramForbiddenError):
            await clear_actions(message)
        return

    if rich and not (edit and message.photo):
        rich_message = InputRichMessage(html=render_text(rich, mode))
        fallback_rich = InputRichMessage(html=render_text(rich, fallback_mode))
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
            await request(method, "rich_message", fallback_rich, timeout=4)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in exc.message.lower():
                return
            log.info("Telegram rejected the rich screen, using HTML")
        except (TelegramNotFound, TelegramNetworkError, TelegramServerError):
            log.info("Rich screen unavailable, using HTML")

    if edit:
        try:
            if message.photo and caption_fits:
                await request(
                    message.edit_caption(
                        caption=decorated_text, parse_mode=ParseMode.HTML, reply_markup=decorated
                    ),
                    "caption",
                    fallback_text,
                )
                return
            if not message.photo:
                await request(
                    message.edit_text(
                        decorated_text, parse_mode=ParseMode.HTML, reply_markup=decorated
                    ),
                    "text",
                    fallback_text,
                )
                return
        except TelegramBadRequest as exc:
            if "message is not modified" in exc.message.lower():
                return
        await clear_actions(message)
    elif caption_fits:
        if photo:
            try:
                method = message.answer_photo(
                    photo, caption=decorated_text, parse_mode=ParseMode.HTML, reply_markup=decorated
                )
                await request(method, "caption", fallback_text, timeout=4)
            except (TelegramBadRequest, TelegramNetworkError, TelegramServerError) as exc:
                log.warning(
                    "Shop banner unavailable, sending the menu as text (%s)", type(exc).__name__
                )
            else:
                return
    await request(
        message.answer(decorated_text, parse_mode=ParseMode.HTML, reply_markup=decorated),
        "text",
        fallback_text,
    )


async def send_html(
    bot: Bot,
    chat_id: int,
    text: str,
    markup: InlineKeyboardMarkup,
    *,
    icons: IconPreference = False,
    request_timeout: int | None = None,
) -> None:
    mode = resolve_icon_mode("inherit", icons) if isinstance(icons, bool) else icons
    method = SendMessage(
        chat_id=chat_id,
        text=render_text(text, mode),
        parse_mode=ParseMode.HTML,
        reply_markup=render_markup(markup, mode),
    )
    fallback = None
    if mode in {"premium", "premium_only"}:
        fallback_mode = _fallback_mode(mode)
        fallback = {
            "text": render_text(text, fallback_mode),
            "reply_markup": render_markup(markup, fallback_mode),
        }
    await request_with_icon_fallback(bot, method, fallback, timeout=request_timeout)


async def notify(bot: Bot, order: Order, *, icons: IconPreference = False) -> None:
    try:
        await send_html(
            bot,
            order.buyer,
            order_card(order),
            buyer_order_keys(order),
            icons=icons,
            request_timeout=3,
        )
    except TelegramAPIError:
        log.info("Could not notify buyer for order %s", order.id)
