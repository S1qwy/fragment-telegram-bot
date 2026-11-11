import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable

from aiogram import Bot, Dispatcher
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import BotCommand, BotCommandScopeChat, Message, Update, User

from .config import Config, ShopError
from .fulfillment import Fulfillment
from .payments import Checkout, PaymentReceipt, make_bot, make_gateway
from .shop import shop_router
from .store import Store
from .views import notify, resolve_icon_mode

log = logging.getLogger(__name__)


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
            await notify(bot, checkout.store.get(receipt.order_id), icons=checkout.icon_mode())
    return True


async def worker(bot: Bot, store: Store, fulfillment: Fulfillment) -> None:
    while True:
        try:
            order = store.claim()
            if order is not None:
                updated = await fulfillment.deliver(order)
                icons = resolve_icon_mode(
                    store.settings()["premium_icons"], fulfillment.config.premium_icons
                )
                await notify(bot, updated, icons=icons)
                continue
        except sqlite3.Error as exc:
            log.warning("Database error in fulfillment worker (%s)", type(exc).__name__)
        except TelegramAPIError as exc:
            # Delivery is already persisted. Never repeat it because a notification failed.
            log.warning("Could not notify buyer after fulfillment (%s)", type(exc).__name__)
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
                    except TelegramAPIError as exc:
                        log.warning(
                            "Telegram could not complete a storefront action (%s)",
                            type(exc).__name__,
                        )
                offset = update.update_id + 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
        except (sqlite3.Error, TelegramNetworkError, TelegramServerError) as exc:
            log.warning("Polling will retry the current offset (%s)", type(exc).__name__)
            await asyncio.sleep(1)


async def initialize_bot(bot: Bot, config: Config, store: Store) -> User:
    delay = 1
    while True:
        try:
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
                BotCommand(command="faq", description="Частые вопросы"),
                BotCommand(command="privacy", description="Конфиденциальность"),
                BotCommand(command="cancel", description="Отменить ввод"),
            ]
            await bot.set_my_commands(commands)
            for user_id in config.admin_ids:
                try:
                    await bot.set_my_commands(
                        commands
                        + [BotCommand(command="admin", description="Управление магазином")],
                        scope=BotCommandScopeChat(chat_id=user_id),
                    )
                except TelegramBadRequest:
                    log.info("Admin command menu will be available after the admin opens the bot")
            return me
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
        except (TelegramNetworkError, TelegramServerError) as exc:
            log.warning("Telegram startup will retry in %ss (%s)", delay, type(exc).__name__)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)


async def run_bot(config: Config, store: Store) -> None:
    if not config.admin_ids:
        raise ShopError("Укажите хотя бы один ADMIN_IDS.")
    fulfillment = Fulfillment(config, store)
    if config.mode == "live":
        await fulfillment.check_live()
    bot = make_bot(config)
    async with bot:
        me = await initialize_bot(bot, config, store)
        checkout = Checkout(
            store, make_gateway(config, bot, store=store), inherited_icons=config.premium_icons
        )
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
