from functools import partial
from html import escape
from secrets import token_hex

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, User

from .admin import admin_router, show_order
from .catalog import Product, username
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
)
from .views import (
    screen as render_screen,
)


class Draft(StatesGroup):
    recipient = State()
    confirm = State()


def shop_router(
    config: Config, store: Store, checkout: Checkout, fulfillment: Fulfillment
) -> Router:
    router = Router()
    screen = partial(render_screen, icons=config.premium_icons)
    router.message.filter(F.chat.type == "private", F.from_user)
    router.callback_query.filter(
        lambda query: isinstance(query.message, Message) and query.message.chat.type == "private"
    )
    router.include_router(admin_router(config, store, checkout, fulfillment))

    async def home(message: Message, user: User, *, edit: bool = False) -> None:
        settings = store.settings()
        rows = [
            [button("🏪 Купить", "catalog", style="primary")],
            [button("👤 Мои заказы", "orders"), button("💬 Поддержка", "support")],
        ]
        if settings["terms"]:
            rows.append([button("📄 Условия магазина", "terms")])
        orders = store.orders(buyer=user.id)
        last = ""
        if orders:
            order = orders[0]
            last = (
                f"\n\n<blockquote>Последний заказ  <code>#{escape(order.id)}</code>\n"
                f"{STATUS_ICONS[order.state]} {STATUS[order.state]}</blockquote>"
            )
        if user.id in config.admin_ids:
            rows.append([button("⚙️ Управление магазином", "admin")])
        await screen(
            message,
            f"<b>{escape(settings['name'])}</b>\n\n{escape(settings['welcome'])}"
            f"{last}{mode_note(config.mode)}",
            keys(*rows),
            edit=edit,
            banner=config.banner_path,
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
        products = store.products()
        if store.settings()["open"] != "1":
            await screen(
                message, "<b>🛍 Каталог</b>\n\nМагазин сейчас закрыт.", keys(back()), edit=edit
            )
            return
        if kind is None:
            rows = []
            for key, (icon, label, _) in PRODUCTS.items():
                prices = [item.price for item in products if item.kind == key]
                if prices:
                    rows.append(
                        [button(f"{icon} {label} · от {price(min(prices))}", f"category:{key}")]
                    )
            description = "Выберите товар." if rows else "Каталог пока пуст."
            await screen(
                message, f"<b>🛍 Каталог</b>\n\n{description}", keys(*rows, back()), edit=edit
            )
            return
        icon, label, description = PRODUCTS[kind]
        rows = [
            [button(f"{package(item)}   ·   {price(item.price)}", f"buy:{kind}:{item.quantity}")]
            for item in products
            if item.kind == kind
        ]
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
        if kind == "terms":
            heading = "📄 Условия магазина"
            rows = [back("support", "Поддержка")]
        else:
            heading = "💬 Поддержка"
            rows = [[button("📄 Условия магазина", "terms")], back()]
        value = store.settings()[kind] or "Магазин пока не указал эту информацию."
        await screen(message, f"<b>{heading}</b>\n\n{escape(value)}", keys(*rows), edit=edit)

    @router.message(Command("support", "paysupport", "terms"))
    async def info_command(message: Message, command: CommandObject) -> None:
        await info(message, "terms" if command.command == "terms" else "support")

    @router.callback_query(F.data.in_({"support", "terms"}))
    async def info_callback(query: CallbackQuery) -> None:
        await query.answer()
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
    async def order_details(message: Message, command: CommandObject) -> None:
        admin = message.from_user.id in config.admin_ids
        order = store.get(
            (command.args or "").strip(), buyer=None if admin else message.from_user.id
        )
        await show_order(message, order, admin=admin, icons=config.premium_icons)

    @router.callback_query(F.data.startswith("show:"))
    async def order_callback(query: CallbackQuery) -> None:
        await query.answer()
        order = store.get(query.data.split(":", 1)[1], buyer=query.from_user.id)
        await show_order(query.message, order, edit=True, icons=config.premium_icons)

    async def ask_recipient(query: CallbackQuery, state: FSMContext, product: Product) -> None:
        await state.set_data(
            {
                "kind": product.kind,
                "quantity": product.quantity,
                "price": product.price,
                "prompt_id": query.message.message_id,
            }
        )
        await state.set_state(Draft.recipient)
        icon, label, _ = PRODUCTS[product.kind]
        rows = []
        if query.from_user.username:
            rows.append([button("👤 Отправить себе", "recipient:self", style="primary")])
        await screen(
            query.message,
            f"<b>{icon} {label}</b>\n{package(product)} · <b>{price(product.price)}</b>\n\n"
            "Пришлите <code>@username</code> получателя.",
            keys(*rows, back(f"category:{product.kind}", "К пакетам")),
            edit=True,
        )

    @router.callback_query(F.data.startswith("buy:"))
    async def select_product(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        _, kind, quantity = query.data.split(":", 2)
        selected = next(
            (
                item
                for item in store.products()
                if item.kind == kind and str(item.quantity) == quantity
            ),
            None,
        )
        if selected is None or store.settings()["open"] != "1":
            raise ShopError("Позиция сейчас недоступна. Откройте /catalog.")
        await ask_recipient(query, state, selected)

    @router.callback_query(Draft.confirm, F.data == "recipient:edit")
    async def edit_recipient(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        data = await state.get_data()
        await ask_recipient(query, state, Product(data["kind"], data["quantity"], data["price"]))

    async def preview(
        message: Message, state: FSMContext, value: str, *, edit: bool = False
    ) -> None:
        recipient = username(value)
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
        await preview(message, state, message.text)

    @router.callback_query(Draft.recipient, F.data == "recipient:self")
    async def send_to_self(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        if not query.from_user.username:
            raise ShopError("У вашего аккаунта нет публичного @username.")
        await preview(query.message, state, query.from_user.username, edit=True)

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
        order = checkout.create(query.from_user.id, product, data["recipient"], data["terms"])
        await state.clear()
        try:
            await checkout.gateway.checkout(order)
        except TelegramAPIError:
            raise ShopError(f"Счёт сохранён. Повторите через /order {order.id}.") from None
        await clear_actions(query.message)

    @router.callback_query(F.data.startswith("invoice:"))
    async def resend_invoice(query: CallbackQuery) -> None:
        await query.answer()
        order = store.get(query.data.split(":", 1)[1], buyer=query.from_user.id)
        if order.state != "unpaid" or order.checkout_id or store.settings()["open"] != "1":
            raise ShopError("Счёт недоступен. Если оплата не прошла, создайте новый заказ.")
        await checkout.gateway.checkout(order)

    @router.callback_query(F.data.startswith("demo:"))
    async def demo_payment(query: CallbackQuery) -> None:
        await query.answer()
        order = DemoGateway.pay(checkout, query.data.split(":", 1)[1], query.from_user.id)
        await screen(query.message, order_card(order), buyer_order_keys(order), edit=True)

    return router
