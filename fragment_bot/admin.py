from collections.abc import Callable
from datetime import UTC, datetime
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from .catalog import Product, number
from .config import Config, ShopError
from .fulfillment import Fulfillment
from .payments import Checkout
from .store import Order, Store
from .views import (
    PRODUCTS,
    STATUS_ICONS,
    back,
    button,
    buyer_order_keys,
    keys,
    notify,
    order_card,
    package,
    price,
    screen,
)

ACTIONS = {"approve": "Подтвердить выдачу", "refund": "Вернуть оплату"}
BRAND = {"name": "Название", "welcome": "Приветствие", "support": "Поддержка", "terms": "Условия"}


class AdminInput(StatesGroup):
    brand = State()
    product = State()


def admin_only(config: Config, user_id: int) -> None:
    if user_id not in config.admin_ids:
        raise ShopError("Команда доступна администратору магазина.")


async def show_order(
    message: Message, order: Order, *, admin: bool = False, edit: bool = False
) -> None:
    if not admin:
        await screen(message, order_card(order), buyer_order_keys(order), edit=edit)
        return
    rows = []
    if order.state == "paid":
        rows.append([button("✅ Выдать заказ", f"ask:approve:{order.id}", style="success")])
    if order.state == "prepared":
        rows.append(
            [button("📥 Скачать PreparedTransaction", f"prepared:{order.id}", style="primary")]
        )
    if order.state in {"paid", "queued", "review", "sending", "prepared_void", "refunding"}:
        rows.append([button("↩️ Вернуть оплату", f"ask:refund:{order.id}", style="danger")])
    rows.extend(
        [
            [button("↻ Обновить", f"adminorder:{order.id}")],
            back("admin:queue", "К заказам"),
        ]
    )
    await screen(message, order_card(order, admin=True), keys(*rows), edit=edit)


def admin_router(
    config: Config, store: Store, checkout: Checkout, fulfillment: Fulfillment
) -> Router:
    router = Router()

    async def dashboard(message: Message, *, edit: bool = False) -> None:
        settings = store.settings()
        opened = settings["open"] == "1"
        status = "🟢 Витрина открыта" if opened else "🔴 Витрина закрыта"
        await screen(
            message,
            f"<b>⚙️ Управление магазином</b>\n\n{escape(settings['name'])}\n"
            f"{status} · <code>{config.mode}</code>",
            keys(
                [button("🧾 Заказы", "admin:queue", style="primary")],
                [button("📦 Товары", "admin:catalog"), button("🎨 Оформление", "admin:brand")],
                [button("💳 Платежи", "admin:payments"), button("📖 Команды", "admin:help")],
                [
                    button(
                        "Закрыть витрину" if opened else "Открыть витрину",
                        "admin:close" if opened else "admin:open",
                        style="danger" if opened else "success",
                    )
                ],
                back("menu", "В магазин"),
            ),
            edit=edit,
        )

    @router.message(Command("admin"))
    async def admin_command(message: Message, state: FSMContext) -> None:
        admin_only(config, message.from_user.id)
        await state.clear()
        await dashboard(message)

    async def queue(message: Message, *, edit: bool = False) -> None:
        orders = store.orders(pending=True)
        rows = [
            [
                button(
                    f"{STATUS_ICONS[order.state]} {package(order.product)} · @{order.recipient}",
                    f"adminorder:{order.id}",
                )
            ]
            for order in orders
        ]
        text = "Выберите заказ." if orders else "Заказов в работе пока нет."
        await screen(
            message,
            f"<b>🧾 Заказы в работе</b>\n\n{text}",
            keys(*rows, back("admin", "Управление")),
            edit=edit,
        )

    async def payment_errors(message: Message, *, edit: bool = False) -> None:
        errors = store.payment_exceptions()[:10]
        text = "\n".join(
            f"#{escape(row['order_id'][:40])} · <code>{row['buyer']}</code> · "
            f"{escape(price(row['amount'], row['currency']))}"
            for row in errors
        )
        await screen(
            message,
            "<b>💳 Платежи на проверке</b>\n\n" + (text or "Несопоставленных платежей пока нет."),
            keys(back("admin", "Управление")),
            edit=edit,
        )

    async def inventory(message: Message, *, edit: bool = False) -> None:
        rows = [
            [
                button(
                    f"{PRODUCTS[p.kind][0]} {package(p)} · {price(p.price)}",
                    f"adminitem:{p.kind}:{p.quantity}",
                )
            ]
            for p in store.products()
        ]
        await screen(
            message,
            "<b>📦 Товары и цены</b>\n\n" + ("Выберите позицию." if rows else "Каталог пуст."),
            keys(
                [button("＋ Добавить товар", "admin:add", style="success")],
                *rows,
                back("admin", "Управление"),
            ),
            edit=edit,
        )

    @router.callback_query((F.data == "admin") | F.data.startswith("admin:"))
    async def navigate(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        admin_only(config, query.from_user.id)
        await state.clear()
        page = query.data
        if page in {"admin", "admin:open", "admin:close"}:
            if page != "admin":
                store.configure("open", "1" if page == "admin:open" else "0")
            await dashboard(query.message, edit=True)
        elif page == "admin:queue":
            await queue(query.message, edit=True)
        elif page == "admin:payments":
            await payment_errors(query.message, edit=True)
        elif page == "admin:catalog":
            await inventory(query.message, edit=True)
        elif page == "admin:brand":
            await screen(
                query.message,
                "<b>🎨 Оформление магазина</b>\n\nВыберите, что изменить.",
                keys(
                    [
                        button("🏷 Название", "setting:name"),
                        button("👋 Приветствие", "setting:welcome"),
                    ],
                    [
                        button("💬 Поддержка", "setting:support"),
                        button("📄 Условия", "setting:terms"),
                    ],
                    back("admin", "Управление"),
                ),
                edit=True,
            )
        elif page == "admin:add":
            rows = [
                [button(f"{icon} {label}", f"adminnew:{kind}")]
                for kind, (icon, label, _) in PRODUCTS.items()
            ]
            await screen(
                query.message,
                "<b>＋ Добавить товар</b>\n\nВыберите тип товара.",
                keys(*rows, back("admin:catalog", "К товарам")),
                edit=True,
            )
        elif page == "admin:help":
            await screen(
                query.message,
                "<b>📖 Команды администратора</b>\n\n"
                "/pending — заказы в работе\n/order ID — открыть заказ\n"
                "/reconcile ID — сверить автоматическую выдачу\n"
                "/resolveprepared ID delivered основание — подтвердить внешнюю выдачу\n"
                "/resolveprepared ID not_broadcast основание — разрешить возврат после сверки\n"
                "/set, /product, /unlist — быстрые настройки",
                keys(back("admin", "Управление")),
                edit=True,
            )

    @router.callback_query(F.data.startswith("setting:"))
    async def edit_setting(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        admin_only(config, query.from_user.id)
        key = query.data.split(":", 1)[1]
        if key not in BRAND:
            raise ShopError("Настройка недоступна.")
        current = store.settings()[key] or "Пока пусто"
        await state.set_data({"setting": key})
        await state.set_state(AdminInput.brand)
        await screen(
            query.message,
            f"<b>🎨 {BRAND[key]}</b>\n\n{escape(current)}\n\nПришлите новое значение.",
            keys(back("admin:brand", "К оформлению")),
            edit=True,
        )

    @router.message(AdminInput.brand, F.text, ~F.text.startswith("/"))
    async def save_setting(message: Message, state: FSMContext) -> None:
        admin_only(config, message.from_user.id)
        key = (await state.get_data())["setting"]
        store.configure(key, message.text)
        await state.clear()
        await screen(
            message,
            f"<b>✅ {BRAND[key]}</b>\n\nИзменение сохранено.",
            keys(back("admin:brand", "К оформлению")),
        )

    @router.callback_query(F.data.startswith("adminitem:"))
    async def product_details(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        admin_only(config, query.from_user.id)
        await state.clear()
        _, kind, quantity = query.data.split(":", 2)
        product = next(
            (p for p in store.products() if p.kind == kind and str(p.quantity) == quantity), None
        )
        if product is None:
            raise ShopError("Товар уже скрыт из каталога.")
        icon, label, _ = PRODUCTS[kind]
        await screen(
            query.message,
            f"<b>{icon} {label}</b>\n\n{package(product)} · <b>{price(product.price)}</b>",
            keys(
                [button("✏️ Изменить цену", f"adminprice:{kind}:{quantity}", style="primary")],
                [button("Скрыть товар", f"adminhide:{kind}:{quantity}", style="danger")],
                back("admin:catalog", "К товарам"),
            ),
            edit=True,
        )

    @router.callback_query(F.data.startswith("adminhide:"))
    async def ask_hide_product(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        admin_only(config, query.from_user.id)
        await state.clear()
        _, kind, quantity = query.data.split(":", 2)
        await screen(
            query.message,
            "<b>Скрыть товар из витрины?</b>\n\nСуществующие заказы останутся в истории.",
            keys(
                [button("Скрыть", f"adminconfirmhide:{kind}:{quantity}", style="danger")],
                back(f"adminitem:{kind}:{quantity}", "К товару"),
            ),
            edit=True,
        )

    @router.callback_query(F.data.startswith("adminconfirmhide:"))
    async def hide_product(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        admin_only(config, query.from_user.id)
        await state.clear()
        _, kind, quantity = query.data.split(":", 2)
        store.unlist(kind, number(quantity, "Количество"))
        await inventory(query.message, edit=True)

    @router.callback_query(F.data.startswith("adminnew:") | F.data.startswith("adminprice:"))
    async def edit_product(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        admin_only(config, query.from_user.id)
        _, kind, *quantity = query.data.split(":", 2)
        if kind not in PRODUCTS:
            raise ShopError("Товар недоступен.")
        await state.set_data({"kind": kind, "quantity": quantity[0] if quantity else None})
        await state.set_state(AdminInput.product)
        icon, label, _ = PRODUCTS[kind]
        instruction = (
            "Пришлите новую цену в XTR."
            if quantity
            else ("Пришлите количество и цену в XTR через пробел.")
        )
        if not quantity:
            example = {"stars": "50 100", "premium": "3 950", "ton": "1 350"}[kind]
            instruction += f"\nНапример, <code>{example}</code>."
        await screen(
            query.message,
            f"<b>{icon} {label}</b>\n\n{instruction}",
            keys(back("admin:catalog", "К товарам")),
            edit=True,
        )

    @router.message(AdminInput.product, F.text, ~F.text.startswith("/"))
    async def save_product(message: Message, state: FSMContext) -> None:
        admin_only(config, message.from_user.id)
        data = await state.get_data()
        parts = message.text.split()
        existing_only = data["quantity"] is not None
        if existing_only:
            parts.insert(0, data["quantity"])
        if len(parts) != 2:
            raise ShopError("Проверьте количество и цену.")
        store.list_product(
            Product(data["kind"], number(parts[0], "Количество"), number(parts[1], "Цена")),
            existing_only=existing_only,
        )
        await state.clear()
        await inventory(message)

    @router.message(Command("set", "product", "unlist"))
    async def settings_command(message: Message, command: CommandObject, state: FSMContext) -> None:
        admin_only(config, message.from_user.id)
        parts = (command.args or "").split(maxsplit=1 if command.command == "set" else -1)
        if command.command == "set" and len(parts) == 2:
            store.configure(*parts)
        elif command.command == "product" and len(parts) == 3:
            store.list_product(
                Product(parts[0], number(parts[1], "Количество"), number(parts[2], "Цена"))
            )
        elif command.command == "unlist" and len(parts) == 2:
            store.unlist(parts[0], number(parts[1], "Количество"))
        else:
            raise ShopError("Откройте /admin или укажите параметры команды.")
        await state.clear()
        await dashboard(message)

    @router.message(Command("pending", "paymenterrors"))
    async def admin_list(message: Message, command: CommandObject) -> None:
        admin_only(config, message.from_user.id)
        if command.command == "pending":
            await queue(message)
        else:
            await payment_errors(message)

    @router.callback_query(F.data.startswith("adminorder:"))
    async def admin_order_callback(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        admin_only(config, query.from_user.id)
        await state.clear()
        await show_order(
            query.message, store.get(query.data.split(":", 1)[1]), admin=True, edit=True
        )

    @router.callback_query(F.data.startswith("ask:"))
    async def ask_action(query: CallbackQuery) -> None:
        await query.answer()
        admin_only(config, query.from_user.id)
        _, action, order_id = query.data.split(":", 2)
        order = store.get(order_id)
        if action not in ACTIONS:
            raise ShopError("Неизвестное действие.")
        await screen(
            query.message,
            f"<b>{ACTIONS[action]}?</b>\n\n{order_card(order, admin=True)}",
            keys(
                [
                    button(
                        "Подтвердить",
                        f"act:{action}:{order.id}",
                        style="success" if action == "approve" else "danger",
                    )
                ],
                back(f"adminorder:{order.id}", "К заказу"),
            ),
            edit=True,
        )

    @router.callback_query(F.data.startswith("act:"))
    async def admin_action(query: CallbackQuery, schedule: Callable) -> None:
        await query.answer()
        admin_only(config, query.from_user.id)
        _, action, order_id = query.data.split(":", 2)
        if action == "approve":
            await show_order(query.message, store.approve(order_id), admin=True, edit=True)
        elif action == "refund":
            order = store.get(order_id)

            async def refund() -> None:
                try:
                    updated = await fulfillment.refund(order.id, checkout.gateway)
                    await notify(query.bot, updated)
                    await show_order(query.message, updated, admin=True)
                except ShopError as exc:
                    await query.message.answer(str(exc))

            queued = schedule(order.id, refund)
            await screen(
                query.message,
                "<b>↩️ Возврат оплаты</b>\n\n"
                + ("Запрос принят." if queued else "Заказ уже обрабатывается."),
                keys(back(f"adminorder:{order.id}", "К заказу")),
                edit=True,
            )
        else:
            raise ShopError("Неизвестное действие.")

    @router.callback_query(F.data.startswith("prepared:"))
    async def download_prepared(query: CallbackQuery) -> None:
        await query.answer()
        admin_only(config, query.from_user.id)
        order_id = query.data.split(":", 1)[1]
        transaction = fulfillment.prepared(order_id)
        document = BufferedInputFile(
            transaction.model_dump_json(indent=2).encode("utf-8"),
            filename=f"stars-{order_id}.json",
        )
        expires = datetime.fromtimestamp(transaction.valid_until, UTC)
        caption = (
            f"Заказ #{order_id} · срок подписи до {expires:%Y-%m-%d %H:%M:%S} UTC"
            if config.mode == "live"
            else f"Заказ #{order_id} · имитация PreparedTransaction"
        )
        await query.message.answer_document(document, caption=caption)

    @router.message(Command("resolveprepared"))
    async def resolve_prepared(message: Message, command: CommandObject) -> None:
        admin_only(config, message.from_user.id)
        parts = (command.args or "").split(maxsplit=2)
        if len(parts) != 3:
            raise ShopError("Формат: /resolveprepared ID delivered|not_broadcast основание")
        order_id, outcome, evidence = parts
        if outcome not in {"delivered", "not_broadcast"}:
            raise ShopError("Результат должен быть delivered или not_broadcast.")
        order = await fulfillment.resolve_prepared(order_id, outcome, evidence)
        await show_order(message, order, admin=True)
        await notify(message.bot, order)

    @router.message(Command("reconcile"))
    async def reconcile(message: Message, command: CommandObject, schedule: Callable) -> None:
        admin_only(config, message.from_user.id)
        order = store.get((command.args or "").strip())

        async def check() -> None:
            try:
                updated = await fulfillment.reconcile(order.id)
                await show_order(message, updated, admin=True)
                if updated.state == "delivered" and order.state != "delivered":
                    await notify(message.bot, updated)
            except ShopError as exc:
                await message.answer(str(exc))

        queued = schedule(order.id, check)
        await message.answer(
            f"Сверяю заказ #{order.id}." if queued else "Заказ уже обрабатывается."
        )

    return router
