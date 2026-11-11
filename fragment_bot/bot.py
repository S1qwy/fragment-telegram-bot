import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from secrets import token_hex

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)

from .catalog import Product, number, username
from .config import Config, ShopError
from .fulfillment import Fulfillment
from .payments import Checkout, DemoGateway, PaymentReceipt, make_bot, make_gateway
from .store import Order, Store

log = logging.getLogger(__name__)
MODE_LABELS = {"demo": "Демо · без списаний", "test": "Тестовая сеть Telegram"}
ACTIONS = {"approve": "Поставить в очередь на выдачу", "refund": "Вернуть оплату"}


class Draft(StatesGroup):
    recipient = State()
    confirm = State()


def keyboard(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
            for row in rows
        ]
    )


def admin_only(config: Config, user_id: int) -> None:
    if user_id not in config.admin_ids:
        raise ShopError("Команда доступна администратору магазина.")


def catalog_markup(products: list[Product]) -> InlineKeyboardMarkup:
    return keyboard(
        *[
            [(f"{product.label} · {product.price} XTR", f"buy:{product.kind}:{product.quantity}")]
            for product in products
        ]
    )


def order_markup(order: Order, *, admin: bool) -> InlineKeyboardMarkup | None:
    if admin:
        actions = []
        if order.state == "paid":
            actions.append((ACTIONS["approve"], f"ask:approve:{order.id}"))
        if order.state == "prepared":
            actions.append(("Скачать PreparedTransaction", f"prepared:{order.id}"))
        if order.state in {"paid", "queued", "review", "sending", "prepared_void", "refunding"}:
            actions.append((ACTIONS["refund"], f"ask:refund:{order.id}"))
        return keyboard(*[[action] for action in actions]) if actions else None
    if order.state == "unpaid" and not order.checkout_id:
        return keyboard([("Открыть счёт", f"invoice:{order.id}")])
    return None


def shop_router(
    config: Config, store: Store, checkout: Checkout, fulfillment: Fulfillment
) -> Router:
    router = Router()
    router.message.filter(F.chat.type == "private", F.from_user)
    router.callback_query.filter(
        lambda query: isinstance(query.message, Message) and query.message.chat.type == "private"
    )

    async def catalog(message: Message) -> None:
        if store.settings()["open"] != "1":
            await message.answer("Магазин сейчас закрыт.")
            return
        products = store.products()
        if not products:
            await message.answer("Каталог пока пуст.")
            return
        await message.answer("Выберите товар.", reply_markup=catalog_markup(products))

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        settings = store.settings()
        mode = MODE_LABELS.get(config.mode)
        heading = f"{settings['name']}\n{mode}" if mode else settings["name"]
        await message.answer(
            f"{heading}\n\n{settings['welcome']}",
            reply_markup=keyboard([("Каталог", "catalog"), ("Мои заказы", "orders")]),
        )

    @router.message(Command("catalog"))
    async def show_catalog(message: Message, state: FSMContext) -> None:
        await state.clear()
        await catalog(message)

    @router.callback_query(F.data == "catalog")
    async def catalog_callback(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        await state.clear()
        await catalog(query.message)

    @router.message(Command("cancel"))
    async def cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Ввод отменён. Открыть /catalog.")

    @router.message(Command("support", "paysupport", "terms"))
    async def info(message: Message, command: CommandObject) -> None:
        key = "terms" if command.command == "terms" else "support"
        value = store.settings()[key]
        await message.answer(value or "Магазин пока не указал эту информацию.")

    async def history(message: Message, user_id: int) -> None:
        orders = store.orders(buyer=user_id)
        if not orders:
            await message.answer("У вас пока нет заказов.")
            return
        text = "\n\n".join(order.summary() for order in orders)
        buttons = [[(f"#{order.id}", f"show:{order.id}")] for order in orders]
        await message.answer(text, reply_markup=keyboard(*buttons))

    @router.message(Command("orders"))
    async def my_orders(message: Message) -> None:
        await history(message, message.from_user.id)

    @router.callback_query(F.data == "orders")
    async def orders_callback(query: CallbackQuery) -> None:
        await query.answer()
        await history(query.message, query.from_user.id)

    @router.message(Command("order"))
    async def order_details(message: Message, command: CommandObject) -> None:
        admin = message.from_user.id in config.admin_ids
        order = store.get(
            (command.args or "").strip(), buyer=None if admin else message.from_user.id
        )
        await message.answer(
            order.summary(admin=admin), reply_markup=order_markup(order, admin=admin)
        )

    @router.callback_query(F.data.startswith("show:"))
    async def order_callback(query: CallbackQuery) -> None:
        await query.answer()
        order = store.get(query.data.split(":", 1)[1], buyer=query.from_user.id)
        await query.message.answer(order.summary(), reply_markup=order_markup(order, admin=False))

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
        await state.set_data(
            {"kind": selected.kind, "quantity": selected.quantity, "price": selected.price}
        )
        await state.set_state(Draft.recipient)
        await query.message.answer(f"{selected.label}\nУкажите @username получателя или /cancel.")

    @router.message(Draft.recipient, F.text, ~F.text.startswith("/"))
    async def enter_recipient(message: Message, state: FSMContext) -> None:
        recipient = username(message.text)
        data = await state.get_data()
        product = Product(data["kind"], data["quantity"], data["price"])
        name = await fulfillment.verify_recipient(product, recipient)
        settings = store.settings()
        nonce = token_hex(8)
        await state.update_data(recipient=recipient, terms=settings["terms"], nonce=nonce)
        await state.set_state(Draft.confirm)
        resolved = f" ({name})" if name else ""
        text = f"{product.label} → @{recipient}{resolved}\nК оплате {product.price} XTR"
        label = "Получить счёт"
        if settings["terms"]:
            text += f"\n\n{settings['terms']}"
            label = "Принимаю условия · получить счёт"
        await message.answer(text, reply_markup=keyboard([(label, f"confirm:{nonce}")]))

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
        await query.message.answer(order.summary())

    @router.message(Command("admin"))
    async def admin_help(message: Message) -> None:
        admin_only(config, message.from_user.id)
        settings = store.settings()
        products = "\n".join(f"{p.kind} {p.quantity} · {p.price} XTR" for p in store.products())
        state = "открыт" if settings["open"] == "1" else "закрыт"
        await message.answer(
            f"{settings['name']} · {config.mode} · {state}\n"
            f"{products or 'Каталог пуст'}\n\n"
            "/set name Название\n/set welcome Приветствие\n"
            "/set support Контакт\n/set terms Условия\n/set open 0 или 1\n"
            "/product stars 50 100 — тип, количество, цена XTR\n"
            "/unlist stars 50 — скрыть позицию\n/pending — заказы в работе\n"
            "/paymenterrors — платежи на ручной проверке\n"
            "/order ID — детали, транзакция Stars и возврат\n/reconcile ID — сверить автовыдачу\n"
            "/resolveprepared ID delivered|not_broadcast основание — результат внешней подписи"
        )

    @router.message(Command("set"))
    async def change_settings(message: Message, command: CommandObject) -> None:
        admin_only(config, message.from_user.id)
        parts = (command.args or "").split(maxsplit=1)
        if len(parts) != 2:
            raise ShopError("Формат: /set ключ значение")
        store.configure(*parts)
        await message.answer("Настройка сохранена.")

    @router.message(Command("product", "unlist"))
    async def change_catalog(message: Message, command: CommandObject) -> None:
        admin_only(config, message.from_user.id)
        parts = (command.args or "").split()
        if command.command == "product" and len(parts) == 3:
            store.list_product(
                Product(parts[0], number(parts[1], "Количество"), number(parts[2], "Цена"))
            )
        elif command.command == "unlist" and len(parts) == 2:
            store.unlist(parts[0], number(parts[1], "Количество"))
        else:
            raise ShopError("Формат: /product stars 50 100 или /unlist stars 50")
        await message.answer("Каталог обновлён.")

    @router.message(Command("pending"))
    async def pending(message: Message) -> None:
        admin_only(config, message.from_user.id)
        orders = store.orders(pending=True)
        text = "\n".join(
            f"/order {order.id} · {order.product.label} · {order.state}" for order in orders
        )
        buttons = [[(f"#{order.id}", f"adminorder:{order.id}")] for order in orders]
        await message.answer(
            text or "Заказов в работе нет.",
            reply_markup=keyboard(*buttons) if buttons else None,
        )

    @router.callback_query(F.data.startswith("adminorder:"))
    async def admin_order_callback(query: CallbackQuery) -> None:
        await query.answer()
        admin_only(config, query.from_user.id)
        order = store.get(query.data.split(":", 1)[1])
        await query.message.answer(
            order.summary(admin=True), reply_markup=order_markup(order, admin=True)
        )

    @router.message(Command("paymenterrors"))
    async def payment_errors(message: Message) -> None:
        admin_only(config, message.from_user.id)
        errors = store.payment_exceptions()[:10]
        text = "\n".join(
            f"#{row['order_id'][:40]} · {row['buyer']} · {row['amount']} {row['currency']}"
            for row in errors
        )
        await message.answer(text or "Платежей на ручной проверке нет.")

    @router.callback_query(F.data.startswith("ask:"))
    async def ask_action(query: CallbackQuery) -> None:
        await query.answer()
        admin_only(config, query.from_user.id)
        _, action, order_id = query.data.split(":", 2)
        order = store.get(order_id)
        if action not in ACTIONS:
            raise ShopError("Неизвестное действие.")
        await query.message.answer(
            f"{ACTIONS[action]}?\n\n{order.summary(admin=True)}",
            reply_markup=keyboard([("Подтвердить", f"act:{action}:{order.id}")]),
        )

    @router.callback_query(F.data.startswith("act:"))
    async def admin_action(query: CallbackQuery, schedule: Callable) -> None:
        await query.answer()
        admin_only(config, query.from_user.id)
        _, action, order_id = query.data.split(":", 2)
        if action == "approve":
            order = store.approve(order_id)
            await query.message.answer(order.summary(admin=True))
        elif action == "refund":
            order = store.get(order_id)

            async def refund() -> None:
                try:
                    updated = await fulfillment.refund(order.id, checkout.gateway)
                    await notify(query.bot, updated)
                    await query.message.answer(updated.summary(admin=True))
                except ShopError as exc:
                    await query.message.answer(str(exc))

            queued = schedule(order.id, refund)
            await query.message.answer(
                "Запрос на возврат принят." if queued else "Заказ уже обрабатывается."
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
        await query.message.answer_document(
            document,
            caption=caption,
        )

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
        await message.answer(order.summary(admin=True))
        await notify(message.bot, order)

    @router.message(Command("reconcile"))
    async def reconcile(message: Message, command: CommandObject, schedule: Callable) -> None:
        admin_only(config, message.from_user.id)
        order_id = (command.args or "").strip()
        order = store.get(order_id)

        async def check() -> None:
            try:
                updated = await fulfillment.reconcile(order.id)
                await message.answer(updated.summary(admin=True))
                if updated.state == "delivered" and order.state != "delivered":
                    await notify(message.bot, updated)
            except ShopError as exc:
                await message.answer(str(exc))

        queued = schedule(order.id, check)
        await message.answer(
            f"Сверяю заказ #{order.id}." if queued else "Заказ уже обрабатывается."
        )

    return router


async def notify(bot: Bot, order: Order) -> None:
    try:
        await bot.send_message(order.buyer, order.summary(), request_timeout=3)
    except TelegramAPIError:
        log.info("Could not notify buyer for order %s", order.id)


async def handle_payment(bot: Bot, update: Update, checkout: Checkout) -> bool:
    query = update.pre_checkout_query
    if query:
        ok = checkout.store.mode != "demo" and checkout.authorize(
            query.invoice_payload,
            query.from_user.id,
            query.total_amount,
            query.currency,
            query.id,
        )
        try:
            await bot.answer_pre_checkout_query(
                query.id,
                ok,
                error_message=None
                if ok
                else "Счёт недоступен. Создайте новый заказ через /catalog.",
                request_timeout=3,
            )
        except TelegramBadRequest:
            log.info("Telegram rejected the pre-checkout response")
        return True
    message = update.message
    if message is None or message.successful_payment is None:
        return False
    if checkout.store.mode == "demo" or message.from_user is None:
        return True
    payment = message.successful_payment
    receipt = PaymentReceipt(
        payment.invoice_payload,
        message.from_user.id,
        payment.total_amount,
        payment.currency,
        payment.telegram_payment_charge_id,
    )
    try:
        captured = checkout.capture(receipt)
    except ShopError as exc:
        try:
            await bot.send_message(message.chat.id, str(exc), request_timeout=3)
        except TelegramAPIError:
            log.info("Could not notify buyer about an unmatched payment")
    else:
        if captured:
            await notify(bot, checkout.store.get(receipt.order_id))
    return True


async def worker(bot: Bot, store: Store, fulfillment: Fulfillment) -> None:
    while True:
        try:
            order = store.claim()
            if order is not None:
                updated = await fulfillment.deliver(order)
                await notify(bot, updated)
                continue
        except sqlite3.Error as exc:
            log.warning("Database error in fulfillment worker (%s)", type(exc).__name__)
        await asyncio.sleep(1)


async def poll(bot: Bot, dispatcher: Dispatcher, checkout: Checkout, schedule: Callable) -> None:
    offset = None
    allowed = dispatcher.resolve_used_update_types() + ["pre_checkout_query"]
    while True:
        try:
            updates = await bot.get_updates(
                offset=offset,
                timeout=20,
                allowed_updates=allowed,
                request_timeout=26,
            )
            for update in updates:
                if not await handle_payment(bot, update, checkout):
                    try:
                        await dispatcher.feed_update(bot, update, schedule=schedule)
                    except ShopError as exc:
                        message = update.message or (
                            update.callback_query and update.callback_query.message
                        )
                        if isinstance(message, Message):
                            await bot.send_message(message.chat.id, str(exc), request_timeout=3)
                    except TelegramAPIError:
                        log.info("Telegram could not complete a storefront action")
                offset = update.update_id + 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
        except (sqlite3.Error, TelegramNetworkError, TelegramServerError) as exc:
            log.warning("Polling will retry the current offset (%s)", type(exc).__name__)
            await asyncio.sleep(1)


async def run_bot(config: Config, store: Store) -> None:
    if not config.admin_ids:
        raise ShopError("Укажите хотя бы один ADMIN_IDS.")
    fulfillment = Fulfillment(config, store)
    if config.mode == "live":
        await fulfillment.check_live()
    bot = make_bot(config)
    async with bot:
        me = await bot.get_me()
        store.bind_bot(me.id)
        await bot.delete_webhook(drop_pending_updates=False)
        commands = [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="catalog", description="Каталог"),
            BotCommand(command="orders", description="Мои заказы"),
            BotCommand(command="support", description="Поддержка"),
            BotCommand(command="paysupport", description="Помощь с оплатой"),
            BotCommand(command="terms", description="Условия магазина"),
            BotCommand(command="cancel", description="Отменить ввод"),
        ]
        await bot.set_my_commands(commands)
        for user_id in config.admin_ids:
            try:
                await bot.set_my_commands(
                    commands + [BotCommand(command="admin", description="Управление магазином")],
                    scope=BotCommandScopeChat(chat_id=user_id),
                )
            except TelegramBadRequest:
                log.info("Admin command menu will be available after the admin opens the bot")
        checkout = Checkout(store, make_gateway(config, bot))
        dispatcher = Dispatcher()
        dispatcher.include_router(shop_router(config, store, checkout, fulfillment))
        background: dict[str, asyncio.Task] = {}

        def schedule(order_id: str, action: Callable[[], Awaitable[None]]) -> bool:
            if order_id in background:
                return False

            async def run_action() -> None:
                try:
                    await action()
                except Exception as exc:
                    log.warning("Order check failed (%s)", type(exc).__name__)
                finally:
                    background.pop(order_id, None)

            background[order_id] = asyncio.create_task(run_action())
            return True

        log.info("Started @%s in %s mode", me.username, config.mode)
        fulfillment_worker = asyncio.create_task(worker(bot, store, fulfillment))
        try:
            await poll(bot, dispatcher, checkout, schedule)
        finally:
            tasks = [fulfillment_worker, *background.values()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await dispatcher.fsm.close()
