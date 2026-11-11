import re
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
from .settings_ui import build_settings_router
from .store import ADMIN_PAGE_SIZE, STATUS, Order, Store
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
    resolve_icon_mode,
    rich_table,
)
from .views import (
    screen as render_screen,
)

ORDER_SECTIONS = {
    "queue": "В работе",
    "all": "Все заказы",
    "review": "На проверке",
    "closed": "Завершённые",
    "payments": "История платежей",
}
ACTIONS = {"approve": "Подтвердить выдачу", "refund": "Вернуть оплату"}


class AdminInput(StatesGroup):
    product = State()
    search = State()


def admin_only(config: Config, user_id: int) -> None:
    if user_id not in config.admin_ids:
        raise ShopError("Команда доступна администратору магазина.")


async def show_order(
    message: Message,
    order: Order,
    *,
    admin: bool = False,
    edit: bool = False,
    icons: bool | str = False,
    source: str = "",
) -> None:
    if not admin:
        await render_screen(
            message, order_card(order), buyer_order_keys(order), edit=edit, icons=icons
        )
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
            [button("↻ Обновить", f"adminorder:{order.id}{source}")],
            back(f"adminorders{source}" if source else "admin:queue", "К списку"),
        ]
    )
    await render_screen(message, order_card(order, admin=True), keys(*rows), edit=edit, icons=icons)


def admin_router(
    config: Config, store: Store, checkout: Checkout, fulfillment: Fulfillment
) -> Router:
    router = Router()
    router.include_router(build_settings_router(config, store))

    def current_icons():
        return resolve_icon_mode(store.settings()["premium_icons"], config.premium_icons)

    async def screen(message, text, markup, **kwargs):
        await render_screen(message, text, markup, icons=current_icons(), **kwargs)

    async def dashboard(message: Message, *, edit: bool = False) -> None:
        settings = store.settings()
        totals = store.shop_totals()
        status = "Открыта" if settings["open"] == "1" else "Закрыта"
        rows = [
            ("Витрина", status),
            ("Режим", config.mode),
            ("Заказы в работе", str(totals["pending"])),
            ("Платежи на проверке", str(totals["unmatched"])),
        ]
        plain = "\n".join(f"{label}  <b>{value}</b>" for label, value in rows)
        await screen(
            message,
            f"<b>⚙️ Админ-панель</b>\n{escape(settings['name'])}\n\n<blockquote>{plain}</blockquote>",
            keys(
                [
                    button("🧾 Заказы", "admin:queue", style="primary"),
                    button("💳 Платежи", "admin:payments"),
                ],
                [
                    button("📦 Товары и цены", "admin:catalog"),
                    button("📊 Статистика", "admin:stats"),
                ],
                [button("🎨 Оформление", "admin:brand"), button("⚙️ Настройки", "admin:settings")],
                [button("🛠 Система", "admin:system"), button("📄 Команды", "admin:help")],
                back("menu", "В магазин"),
            ),
            edit=edit,
            rich=rich_table("Админ-панель · " + settings["name"], rows)
            if config.rich_menus
            else None,
        )

    @router.message(Command("admin"))
    async def admin_command(message: Message, state: FSMContext) -> None:
        admin_only(config, message.from_user.id)
        await state.clear()
        await dashboard(message)

    async def orders_page(
        message: Message,
        *,
        group: str = "queue",
        page: int = 0,
        edit: bool = False,
        search: str = "",
    ) -> None:
        orders, total, page = store.admin_orders(group, page, search=search)
        title = "Поиск заказов" if search else ORDER_SECTIONS[group]
        source = f":{group}:{page}" if group != "queue" or page else ""
        rows = [
            [
                button(
                    (
                        f"{STATUS_ICONS[order.state]} {price(order.price, order.currency)} · {STATUS[order.state]}"
                        if group == "payments"
                        else f"{STATUS_ICONS[order.state]} {package(order.product)} · @{order.recipient} · #{order.id[:6]}"
                    ),
                    f"adminorder:{order.id}{source}",
                )
            ]
            for order in orders
        ]
        pages = max(1, (total + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE)
        if pages > 1:
            arrows = []
            if page:
                previous = (
                    f"adminsearch:{page - 1}:{search}"
                    if search
                    else f"adminorders:{group}:{page - 1}"
                )
                arrows.append(button("‹ Предыдущая", previous))
            if page + 1 < pages:
                following = (
                    f"adminsearch:{page + 1}:{search}"
                    if search
                    else f"adminorders:{group}:{page + 1}"
                )
                arrows.append(button("Следующая ›", following))
            rows.append(arrows)
        if group == "payments":
            rows.append(back("admin:payments", "К платежам"))
        else:
            filters = [
                button(
                    label,
                    f"adminorders:{key}:0",
                    style="primary" if group == key and not search else None,
                )
                for key, label in ORDER_SECTIONS.items()
                if key != "payments"
            ]
            rows.extend(
                [
                    filters[:2],
                    filters[2:],
                    [button("🔎 Поиск", "admin:search")],
                    back("admin", "Управление"),
                ]
            )
        summary = (
            f"Записей  <b>{total}</b> · Страница <b>{page + 1}/{pages}</b>"
            if orders
            else "Здесь пока пусто."
        )
        if search:
            summary = f"<code>{escape(search)}</code>\n\n{summary}"
        await screen(message, f"<b>🧾 {title}</b>\n\n{summary}", keys(*rows), edit=edit)

    async def payment_home(message: Message, *, edit: bool = False) -> None:
        totals = store.shop_totals()
        title = "Платежи XTR" if config.mode == "live" else "Тестовые платежи XTR"
        rows = [
            ("Платежей", str(totals["payments"])),
            ("Принято", price(totals["received_xtr"])),
            ("Возвращено", price(totals["returned_xtr"])),
            ("На проверке", str(totals["unmatched"])),
        ]
        plain = "\n".join(f"{label}  <b>{value}</b>" for label, value in rows)
        await screen(
            message,
            f"<b>💳 {title}</b>\n\n<blockquote>{plain}</blockquote>",
            keys(
                [button("🧾 История платежей", "adminorders:payments:0", style="primary")],
                [button("🔎 Несопоставленные платежи", "admin:unmatched")],
                back("admin", "Управление"),
            ),
            edit=edit,
            rich=rich_table(title, rows) if config.rich_menus else None,
        )

    async def payment_errors(message: Message, *, edit: bool = False) -> None:
        errors = store.payment_exceptions(pending_only=True)[:10]
        text = "\n".join(
            f"#{escape(row['order_id'][:40])} · <code>{row['buyer']}</code> · "
            f"{escape(price(row['amount'], row['currency']))}"
            for row in errors
        )
        await screen(
            message,
            "<b>💳 Платежи на проверке</b>\n\n" + (text or "Несопоставленных платежей пока нет."),
            keys(back("admin:payments", "К платежам")),
            edit=edit,
        )

    async def inventory(message: Message, *, edit: bool = False) -> None:
        if store.settings()["pricing_mode"] == "rates":
            lines = [f"{package(p)} · {price(p.price)}" for p in store.sale_products()]
            await screen(
                message,
                "<b>💰 Курсы и товары</b>\n\n" + "\n".join(lines),
                keys(
                    [
                        button(
                            "⚙️ Курсы, цены и доступность", "settings:group:pricing", style="primary"
                        )
                    ],
                    back("admin"),
                ),
                edit=edit,
            )
            return
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
                [button("💰 Курсы и доступность", "settings:group:pricing")],
                [button("＋ Добавить товар", "admin:add", style="success")],
                *rows,
                back("admin", "Управление"),
            ),
            edit=edit,
        )

    @router.callback_query(
        (F.data == "admin")
        | (F.data.startswith("admin:") & ~F.data.in_({"admin:settings", "admin:brand"}))
    )
    async def navigate(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        admin_only(config, query.from_user.id)
        await state.clear()
        page = query.data
        if page == "admin":
            await dashboard(query.message, edit=True)
        elif page == "admin:queue":
            await orders_page(query.message, edit=True)
        elif page == "admin:payments":
            await payment_home(query.message, edit=True)
        elif page == "admin:unmatched":
            await payment_errors(query.message, edit=True)
        elif page in {"admin:open", "admin:close"}:
            store.configure("open", "1" if page == "admin:open" else "0")
            await dashboard(query.message, edit=True)
        elif page == "admin:stats":
            totals = store.shop_totals()
            rows = [
                ("Всего заказов", str(totals["orders"])),
                ("Покупателей с заказами", str(totals["buyers"])),
                ("Выдано", str(totals["delivered"])),
                ("Возвратов", str(totals["refunds"])),
                ("Принято XTR", price(totals["received_xtr"])),
                ("Возвращено XTR", price(totals["returned_xtr"])),
            ]
            plain = "\n".join(f"{label}  <b>{value}</b>" for label, value in rows)
            await screen(
                query.message,
                f"<b>📊 Статистика за всё время</b>\n\n<blockquote>{plain}</blockquote>",
                keys([button("↻ Обновить", "admin:stats")], back("admin", "Управление")),
                edit=True,
                rich=rich_table("Статистика за всё время", rows) if config.rich_menus else None,
            )
        elif page == "admin:system":
            proxy = "через прокси" if config.telegram_proxy else "напрямую"
            fragment = "сессия задана" if config.cookies else "сессия не задана"
            await screen(
                query.message,
                f"<b>🛠 Система</b>\n\n<blockquote>Режим  <code>{config.mode}</code>\n"
                f"Telegram  {proxy}\nFragment  {fragment}\nАдминистраторов  {len(config.admin_ids)}\n"
                f"Кошелёк  <code>{escape(config.wallet_version)}</code>\n"
                f"Лимит выдачи  <b>{config.max_nanoton / 10**9:g} TON</b></blockquote>",
                keys(
                    [button("📄 Параметры запуска", "admin:environment")],
                    back("admin", "Управление"),
                ),
                edit=True,
            )
        elif page == "admin:environment":
            await screen(
                query.message,
                "<b>📄 Параметры запуска</b>\n\n"
                "<b>Telegram</b>  <code>BOT_TOKEN</code>, <code>ADMIN_IDS</code>, <code>TELEGRAM_PROXY</code>\n"
                "<b>Fragment</b>  <code>FRAGMENT_COOKIES</code>, <code>FRAGMENT_PROXY</code>, "
                "<code>TON_SENDER_ACCOUNT</code>, <code>TON_SEED</code>, <code>TON_API_KEY</code>\n"
                "<b>Кошелёк</b>  <code>TON_WALLET_VERSION</code>, <code>TON_MNEMONIC_TYPE</code>, "
                "<code>TON_API_PROVIDER</code>, <code>FRAGMENT_MAX_TON</code>\n"
                "<b>Запуск</b>  <code>SHOP_MODE</code>, <code>DATA_DIR</code>\n"
                "<b>Внешний вид по умолчанию</b>  <code>SHOP_BANNER_FILE</code>, "
                "<code>SHOP_RICH_MENUS</code>, <code>SHOP_PREMIUM_ICONS</code>\n\n"
                "Параметры запуска читаются из локального файла <code>.env</code>.",
                keys(back("admin:system", "К системе")),
                edit=True,
            )
        elif page == "admin:search":
            await state.set_state(AdminInput.search)
            await screen(
                query.message,
                "<b>🔎 Поиск заказов</b>\n\nПришлите номер заказа, @username получателя или Telegram ID покупателя.",
                keys(back("admin:queue", "К заказам")),
                edit=True,
            )
        elif page == "admin:catalog":
            await inventory(query.message, edit=True)
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

    @router.callback_query(F.data.startswith("adminorders:") | F.data.startswith("adminsearch:"))
    async def order_pages(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        admin_only(config, query.from_user.id)
        await state.clear()
        action, first, second = query.data.split(":", 2)
        if action == "adminsearch":
            await orders_page(
                query.message, group="all", page=number(first, "Страница"), search=second, edit=True
            )
        else:
            await orders_page(
                query.message, group=first, page=number(second, "Страница"), edit=True
            )

    @router.message(AdminInput.search, F.text, ~F.text.startswith("/"))
    async def search_orders(message: Message, state: FSMContext) -> None:
        admin_only(config, message.from_user.id)
        search = message.text.strip().lstrip("@#").lower()
        if not re.fullmatch(r"[a-z0-9_]{1,32}", search):
            raise ShopError("Введите номер заказа, username или Telegram ID.")
        await state.clear()
        await orders_page(message, group="all", search=search)

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
    async def admin_list(message: Message, command: CommandObject, state: FSMContext) -> None:
        admin_only(config, message.from_user.id)
        await state.clear()
        if command.command == "pending":
            await orders_page(message)
        else:
            await payment_errors(message)

    @router.callback_query(F.data.startswith("adminorder:"))
    async def admin_order_callback(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        admin_only(config, query.from_user.id)
        await state.clear()
        _, order_id, *context = query.data.split(":", 3)
        source = ""
        if len(context) == 2 and context[0] in ORDER_SECTIONS:
            source = f":{context[0]}:{number(context[1], 'Страница')}"
        await show_order(
            query.message,
            store.get(order_id),
            admin=True,
            edit=True,
            icons=current_icons(),
            source=source,
        )

    @router.callback_query(F.data.startswith("ask:"))
    async def ask_action(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        admin_only(config, query.from_user.id)
        _, action, order_id = query.data.split(":", 2)
        order = store.get(order_id)
        if action not in ACTIONS:
            raise ShopError("Неизвестное действие.")
        await state.clear()
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
    async def admin_action(query: CallbackQuery, schedule: Callable, state: FSMContext) -> None:
        await query.answer()
        admin_only(config, query.from_user.id)
        _, action, order_id = query.data.split(":", 2)
        await state.clear()
        if action == "approve":
            await show_order(
                query.message,
                store.approve(order_id),
                admin=True,
                edit=True,
                icons=current_icons(),
            )
        elif action == "refund":
            order = store.get(order_id)

            async def refund() -> None:
                try:
                    updated = await fulfillment.refund(order.id, checkout.gateway)
                    await notify(query.bot, updated, icons=current_icons())
                    await show_order(query.message, updated, admin=True, icons=current_icons())
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
    async def download_prepared(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        admin_only(config, query.from_user.id)
        order_id = query.data.split(":", 1)[1]
        transaction = fulfillment.prepared(order_id)
        await state.clear()
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
        await show_order(message, order, admin=True, icons=current_icons())
        await notify(message.bot, order, icons=current_icons())

    @router.message(Command("reconcile"))
    async def reconcile(message: Message, command: CommandObject, schedule: Callable) -> None:
        admin_only(config, message.from_user.id)
        order = store.get((command.args or "").strip())

        async def check() -> None:
            try:
                updated = await fulfillment.reconcile(order.id)
                await show_order(message, updated, admin=True, icons=current_icons())
                if updated.state == "delivered" and order.state != "delivered":
                    await notify(message.bot, updated, icons=current_icons())
            except ShopError as exc:
                await message.answer(str(exc))

        queued = schedule(order.id, check)
        await message.answer(
            f"Сверяю заказ #{order.id}." if queued else "Заказ уже обрабатывается."
        )

    return router
