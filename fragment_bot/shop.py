from html import escape
from secrets import token_hex

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message, User

from .admin import admin_router, show_order
from .catalog import (
    GRAM_TOPUP_MAX,
    GRAM_TOPUP_MIN,
    STARS_PURCHASE_MAX,
    STARS_PURCHASE_MIN,
    Product,
    number,
    username,
)
from .config import Config, ShopError
from .fulfillment import Fulfillment
from .payments import Checkout, DemoGateway
from .store import STATUS, Store
from .views import (
    PRODUCTS,
    STATUS_ICONS,
    back,
    button,
    buyer_order_keys,
    clear_actions,
    keys,
    mode_note,
    order_card,
    package,
    price,
    resolve_icon_mode,
)
from .views import (
    screen as render_screen,
)

INFO_BUTTONS = {
    "terms": ("📄", "Условия магазина"),
    "faq": ("ℹ️", "Частые вопросы"),
    "privacy": ("🔒", "Конфиденциальность"),
}


def check_recipient_policy(settings: dict[str, str], buyer: User, recipient: str) -> None:
    if settings["allow_gifts"] == "0" and recipient != (buyer.username or "").lower():
        raise ShopError("Магазин принимает заказы только для вашего @username.")


class Draft(StatesGroup):
    quantity = State()
    recipient = State()
    confirm = State()


def shop_router(
    config: Config, store: Store, checkout: Checkout, fulfillment: Fulfillment
) -> Router:
    router = Router()
    router.message.filter(F.chat.type == "private", F.from_user)
    router.callback_query.filter(
        lambda query: isinstance(query.message, Message) and query.message.chat.type == "private"
    )
    router.include_router(admin_router(config, store, checkout, fulfillment))

    def current_icons():
        return resolve_icon_mode(store.settings()["premium_icons"], config.premium_icons)

    async def screen(message, text, markup, **kwargs):
        await render_screen(message, text, markup, icons=current_icons(), **kwargs)

    async def home(message: Message, user: User, *, edit: bool = False) -> None:
        settings = store.settings()
        rows = [[button(f"🏪 {settings['buy_label']}", "catalog", style="primary")]]
        actions = {
            "orders": button("👤 Мои заказы", "orders"),
            "support": button(f"💬 {settings['support_title']}", "support"),
            **{
                key: button(f"{icon} {label}", key)
                for key, (icon, label) in INFO_BUTTONS.items()
                if settings[key]
            },
        }
        visible = [actions[key] for key in settings["home_buttons"].split(",") if key in actions]
        rows.extend(visible[index : index + 2] for index in range(0, len(visible), 2))
        orders = store.orders(buyer=user.id) if settings["show_recent_order"] == "1" else []
        last = ""
        if orders:
            order = orders[0]
            last = (
                f"\n\n<blockquote>Последний заказ  <code>#{escape(order.id)}</code>\n"
                f"{STATUS_ICONS[order.state]} {STATUS[order.state]}</blockquote>"
            )
        if user.id in config.admin_ids:
            rows.append([button("⚙️ Управление магазином", "admin")])

        name = escape(settings["name"])
        tagline = escape(settings["tagline"])
        welcome = escape(settings["welcome"])
        blurb = f"<i>{tagline}</i>\n\n{welcome}" if tagline else welcome
        text = f"<b>{name}</b>\n\n{blurb}{last}{mode_note(config.mode)}"
        style = settings["home_style"]
        banner = None
        rich = None
        if style == "cover":
            banner = settings["banner_url"] or config.banner_path
        elif style == "rich" and config.rich_menus:
            rich = f"<h3>{name}</h3><p>{blurb.replace(chr(10), '<br>')}</p>{last}{mode_note(config.mode)}"
            if settings["banner_url"]:
                rich = f'<img src="{escape(settings["banner_url"], quote=True)}"/>{rich}'
        await screen(
            message, text, keys(*rows), edit=edit, banner=banner, rich=rich, replace_media=edit
        )

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        await home(message, message.from_user)

    @router.callback_query(F.data == "menu")
    async def home_callback(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        await state.clear()
        await home(query.message, query.from_user, edit=True)

    async def catalog(message: Message, *, kind: str | None = None, edit: bool = False) -> None:
        settings = store.settings()
        title = f"<b>🛍 {escape(settings['catalog_title'])}</b>"
        if settings["open"] != "1":
            closed = escape(settings["closed_text"] or "Магазин сейчас закрыт.")
            await screen(
                message,
                f"{title}\n\n{closed}",
                keys([button(f"💬 {settings['support_title']}", "support")], back()),
                edit=edit,
            )
            return
        products = store.sale_products()
        if kind is None:
            rows = []
            for key in settings["category_order"].split(","):
                icon, label, _ = PRODUCTS[key]
                prices = [item.price for item in products if item.kind == key]
                if prices:
                    rows.append(
                        [button(f"{icon} {label} · от {price(min(prices))}", f"category:{key}")]
                    )
            description = "Выберите товар." if rows else "Каталог пока пуст."
            await screen(message, f"{title}\n\n{description}", keys(*rows, back()), edit=edit)
            return
        icon, label, description = PRODUCTS[kind]
        if settings[f"enabled_{kind}"] != "1":
            raise ShopError("Этот товар отключён.")
        rows = [
            [button(f"{package(item)}   ·   {price(item.price)}", f"buy:{kind}:{item.quantity}")]
            for item in products
            if item.kind == kind
        ]
        if settings["pricing_mode"] == "rates" and (
            kind == "ton" or (kind == "stars" and settings["stars_custom"] == "1")
        ):
            if kind == "ton":
                rows = []
            rows.append([button("✏️ Указать количество", f"custom:{kind}", style="primary")])
            description += f"\nКурс: {settings[f'{kind}_rate']} XTR за единицу."
        await screen(
            message,
            f"<b>{icon} {label}</b>\n\n{description}\n\n"
            + ("Выберите пакет." if rows else "Пакеты пока недоступны."),
            keys(*rows, back("catalog", "Каталог")),
            edit=edit,
        )

    @router.message(Command("catalog"))
    async def show_catalog(message: Message, state: FSMContext) -> None:
        await state.clear()
        await catalog(message)

    @router.callback_query(F.data == "catalog")
    async def catalog_callback(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        await state.clear()
        await catalog(query.message, edit=True)

    @router.callback_query(F.data.startswith("category:"))
    async def category_callback(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        await state.clear()
        kind = query.data.split(":", 1)[1]
        if kind not in PRODUCTS:
            raise ShopError("Раздел каталога недоступен.")
        await catalog(query.message, kind=kind, edit=True)

    @router.message(Command("cancel"))
    async def cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await home(message, message.from_user)

    async def info(message: Message, kind: str, *, edit: bool = False) -> None:
        settings = store.settings()
        rows = []
        if kind == "support":
            icon, heading = "💬", settings["support_title"]
            if settings["support_url"]:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text="💬 Связаться с поддержкой",
                            url=settings["support_url"],
                            style="primary",
                        )
                    ]
                )
            rows.extend(
                [
                    [button(f"{symbol} {label}", key)]
                    for key, (symbol, label) in INFO_BUTTONS.items()
                    if settings[key]
                ]
            )
            rows.append(back())
        else:
            icon, heading = INFO_BUTTONS[kind]
            rows.append(back("support", settings["support_title"]))
        value = settings[kind] or "Магазин пока не указал эту информацию."
        await screen(
            message,
            f"<b>{icon} {escape(heading)}</b>\n\n{escape(value)}",
            keys(*rows),
            edit=edit,
        )

    @router.message(Command("support", "paysupport", "terms", "faq", "privacy"))
    async def info_command(message: Message, command: CommandObject, state: FSMContext) -> None:
        await state.clear()
        await info(message, "support" if command.command == "paysupport" else command.command)

    @router.callback_query(F.data.in_({"support", *INFO_BUTTONS}))
    async def info_callback(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        await state.clear()
        await info(query.message, query.data, edit=True)

    async def history(message: Message, user_id: int, *, edit: bool = False) -> None:
        orders = store.orders(buyer=user_id)
        rows = [
            [
                button(
                    f"{STATUS_ICONS[order.state]} {package(order.product)} · #{order.id[:7]}",
                    f"show:{order.id}",
                )
            ]
            for order in orders
        ]
        description = (
            "Откройте заказ, чтобы посмотреть статус." if orders else "У вас пока нет заказов."
        )
        if not orders:
            rows.append([button("🛍 Открыть каталог", "catalog", style="primary")])
        await screen(
            message, f"<b>👤 Мои заказы</b>\n\n{description}", keys(*rows, back()), edit=edit
        )

    @router.message(Command("orders"))
    async def my_orders(message: Message, state: FSMContext) -> None:
        await state.clear()
        await history(message, message.from_user.id)

    @router.callback_query(F.data == "orders")
    async def orders_callback(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        await state.clear()
        await history(query.message, query.from_user.id, edit=True)

    @router.message(Command("order"))
    async def order_details(message: Message, command: CommandObject, state: FSMContext) -> None:
        admin = message.from_user.id in config.admin_ids
        order = store.get(
            (command.args or "").strip(), buyer=None if admin else message.from_user.id
        )
        await state.clear()
        await show_order(message, order, admin=admin, icons=current_icons())

    @router.callback_query(F.data.startswith("show:"))
    async def order_callback(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        order = store.get(query.data.split(":", 1)[1], buyer=query.from_user.id)
        await state.clear()
        await show_order(query.message, order, edit=True, icons=current_icons())

    async def ask_recipient(
        message: Message, buyer: User, state: FSMContext, product: Product, *, edit: bool = True
    ) -> None:
        settings = store.settings()
        if settings["allow_gifts"] == "0" and not buyer.username:
            raise ShopError(
                "Добавьте публичный @username в настройках Telegram, чтобы оформить заказ."
            )
        await state.set_data(
            {
                "kind": product.kind,
                "quantity": product.quantity,
                "price": product.price,
                "prompt_id": message.message_id if edit else None,
            }
        )
        await state.set_state(Draft.recipient)
        icon, label, _ = PRODUCTS[product.kind]
        rows = []
        if buyer.username and settings["allow_self"] == "1":
            rows.append([button("👤 Отправить себе", "recipient:self", style="primary")])
        await screen(
            message,
            f"<b>{icon} {label}</b>\n{package(product)} · <b>{price(product.price)}</b>\n\n"
            + (
                "Пришлите <code>@username</code> получателя."
                if settings["allow_gifts"] == "1"
                else "Пришлите свой <code>@username</code>."
            ),
            keys(*rows, back(f"category:{product.kind}", "К пакетам")),
            edit=edit,
        )

    @router.callback_query(F.data.startswith("custom:"))
    async def custom_amount(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        kind = query.data.split(":", 1)[1]
        settings = store.settings()
        if (
            kind not in {"stars", "ton"}
            or settings["pricing_mode"] != "rates"
            or settings["open"] != "1"
            or settings[f"enabled_{kind}"] != "1"
            or (kind == "stars" and settings["stars_custom"] != "1")
        ):
            raise ShopError("Произвольное количество недоступно.")
        await state.set_state(Draft.quantity)
        await state.set_data({"kind": kind})
        lower, upper = (
            (STARS_PURCHASE_MIN, STARS_PURCHASE_MAX)
            if kind == "stars"
            else (GRAM_TOPUP_MIN, GRAM_TOPUP_MAX)
        )
        await screen(
            query.message,
            f"<b>Укажите количество</b>\n\nОт {lower} до {upper}, целое число.\nКурс: {settings[f'{kind}_rate']} XTR за единицу.\nИтоговая цена счёта — до 10 000 XTR.",
            keys(back(f"category:{kind}")),
            edit=True,
        )

    @router.message(Draft.quantity, F.text, ~F.text.startswith("/"))
    async def enter_quantity(message: Message, state: FSMContext) -> None:
        kind = (await state.get_data())["kind"]
        product = store.quote(kind, number(message.text.strip(), "Количество"))
        await ask_recipient(
            message,
            message.from_user,
            state,
            product,
            edit=False,
        )

    @router.callback_query(F.data.startswith("buy:"))
    async def select_product(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        _, kind, quantity = query.data.split(":", 2)
        selected = next(
            (
                item
                for item in store.sale_products()
                if item.kind == kind and str(item.quantity) == quantity
            ),
            None,
        )
        if selected is None or store.settings()["open"] != "1":
            raise ShopError("Позиция сейчас недоступна. Откройте /catalog.")
        await ask_recipient(query.message, query.from_user, state, selected)

    @router.callback_query(Draft.confirm, F.data == "recipient:edit")
    async def edit_recipient(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        data = await state.get_data()
        await ask_recipient(
            query.message,
            query.from_user,
            state,
            Product(data["kind"], data["quantity"], data["price"]),
        )

    async def preview(
        message: Message, state: FSMContext, value: str, buyer: User, *, edit: bool = False
    ) -> None:
        recipient = username(value)
        check_recipient_policy(store.settings(), buyer, recipient)
        data = await state.get_data()
        product = Product(data["kind"], data["quantity"], data["price"])
        name = await fulfillment.verify_recipient(product, recipient)
        terms = store.settings()["terms"]
        nonce = token_hex(8)
        await state.update_data(recipient=recipient, terms=terms, nonce=nonce)
        await state.set_state(Draft.confirm)
        resolved = f" · {escape(name)}" if name else ""
        icon, label, _ = PRODUCTS[product.kind]
        text = (
            f"<b>🧾 Проверьте заказ</b>\n\n{icon} {label} · {package(product)}\n"
            f"Получатель  <b>@{escape(recipient)}</b>{resolved}\n"
            f"К оплате  <b>{price(product.price)}</b>"
        )
        cta = "Получить счёт"
        if terms:
            text += f"\n\n<b>Условия покупки</b>\n{escape(terms)}"
            cta = "Принять условия и получить счёт"
        await screen(
            message,
            text,
            keys(
                [button(cta, f"confirm:{nonce}", style="success")],
                [button("✏️ Изменить получателя", "recipient:edit")],
                back(f"category:{product.kind}", "К пакетам"),
            ),
            edit=edit,
        )
        if not edit and data.get("prompt_id"):
            try:
                await message.bot.edit_message_reply_markup(
                    chat_id=message.chat.id, message_id=data["prompt_id"], reply_markup=None
                )
            except TelegramBadRequest:
                pass

    @router.message(Draft.recipient, F.text, ~F.text.startswith("/"))
    async def enter_recipient(message: Message, state: FSMContext) -> None:
        await preview(message, state, message.text, message.from_user)

    @router.callback_query(Draft.recipient, F.data == "recipient:self")
    async def send_to_self(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        if store.settings()["allow_self"] != "1":
            raise ShopError("Быстрый выбор отключён. Пришлите свой @username.")
        if not query.from_user.username:
            raise ShopError("У вашего аккаунта нет публичного @username.")
        await preview(query.message, state, query.from_user.username, query.from_user, edit=True)

    @router.callback_query(F.data.in_({"recipient:self", "recipient:edit"}))
    async def expired_recipient(query: CallbackQuery) -> None:
        await query.answer("Откройте товар заново через каталог.", show_alert=True)

    @router.callback_query(F.data.startswith("confirm:"))
    async def confirm_order(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        data = await state.get_data()
        if data.get("nonce") != query.data.split(":", 1)[1]:
            raise ShopError("Откройте товар заново через /catalog.")
        product = Product(data["kind"], data["quantity"], data["price"])
        check_recipient_policy(store.settings(), query.from_user, data["recipient"])
        order = checkout.create(query.from_user.id, product, data["recipient"], data["terms"])
        await state.clear()
        try:
            await checkout.gateway.checkout(order)
        except TelegramAPIError:
            raise ShopError(f"Счёт сохранён. Повторите через /order {order.id}.") from None
        await clear_actions(query.message)

    @router.callback_query(F.data.startswith("invoice:"))
    async def resend_invoice(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        order = store.get(query.data.split(":", 1)[1], buyer=query.from_user.id)
        if order.state != "unpaid" or order.checkout_id or store.settings()["open"] != "1":
            raise ShopError("Счёт недоступен. Если оплата не прошла, создайте новый заказ.")
        await state.clear()
        await checkout.gateway.checkout(order)

    @router.callback_query(F.data.startswith("demo:"))
    async def demo_payment(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        order = DemoGateway.pay(checkout, query.data.split(":", 1)[1], query.from_user.id)
        await state.clear()
        await screen(query.message, order_card(order), buyer_order_keys(order), edit=True)

    return router
