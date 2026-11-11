import argparse
import asyncio
import json
import logging
import sqlite3
import sys

from aiogram.exceptions import TelegramAPIError
from FragmentAPI import FragmentError

from .bot import run_bot
from .catalog import SETTINGS, Product, number
from .config import Config, ShopError
from .fulfillment import Fulfillment
from .payments import Checkout, DemoGateway, make_bot, make_gateway
from .store import Store


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(prog="fragment-bot", description="Управление магазином Fragment")
    sub = cli.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="Запустить бота и очередь выдачи")
    check = sub.add_parser("doctor", help="Проверить конфигурацию")
    check.add_argument("--online", action="store_true", help="Проверить Telegram и сессию Fragment")
    sub.add_parser("catalog", help="Показать товары")
    setting = sub.add_parser("set", help="Изменить настройку магазина")
    setting.add_argument("key", choices=SETTINGS)
    setting.add_argument("value")
    product = sub.add_parser("product", help="Добавить товар или изменить цену")
    product.add_argument("kind", choices=["stars", "premium", "ton"])
    product.add_argument("quantity")
    product.add_argument("price")
    hide = sub.add_parser("unlist", help="Скрыть товар")
    hide.add_argument("kind")
    hide.add_argument("quantity")
    orders = sub.add_parser("orders", help="Показать заказы")
    orders.add_argument("--pending", action="store_true")
    sub.add_parser("payment-errors", help="Показать платежи, отправленные на ручную сверку")
    refund_exception = sub.add_parser(
        "refund-payment-error", help="Вернуть несопоставленный платёж"
    )
    refund_exception.add_argument("charge_id")
    for name, help_text in (
        ("order", "Показать заказ"),
        ("approve", "Поставить оплаченный заказ в очередь"),
        ("reconcile", "Сверить исходную выдачу через Fragment"),
        ("refund", "Вернуть платёж через исходного провайдера"),
        ("retry", "Вернуть в очередь выдачу без операции Fragment"),
        ("resolve", "Записать проверенный результат транзакции Fragment"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("id")
        if name == "approve":
            command.add_argument("--now", action="store_true", help="Выполнить выдачу из консоли")
        if name == "resolve":
            command.add_argument("outcome", choices=["settled", "not_broadcast"])
            command.add_argument("--evidence", required=True, help="Хеш или ссылка и основание")
    demo = sub.add_parser("demo-buy", help="Создать и оплатить тестовый заказ локально")
    demo.add_argument("kind", choices=["stars", "premium", "ton"])
    demo.add_argument("quantity")
    demo.add_argument("recipient")
    demo.add_argument("--buyer", type=int, default=1)
    return cli


async def execute(args: argparse.Namespace) -> None:
    config = Config.load()
    store = Store(config.data_dir, config.mode)
    fulfillment = Fulfillment(config, store)
    command = args.command
    if config.token and command != "run":
        store.bind_bot(config.bot_id)
    if command == "run":
        await run_bot(config, store)
    elif command == "doctor":
        if config.mode == "live":
            async with fulfillment.client(payer=True) as client:
                if args.online:
                    await client.get_profile()
        if args.online:
            async with make_bot(config) as bot:
                info = await bot.get_me()
                store.bind_bot(info.id)
                print(f"Telegram @{info.username}")
        print(f"Режим {config.mode} · база {store.path}")
        print(f"Администраторов: {len(config.admin_ids)} · товаров: {len(store.products())}")
        print("Магазин открыт." if store.settings()["open"] == "1" else "Магазин закрыт.")
    elif command == "catalog":
        for product in store.products():
            print(f"{product.kind} {product.quantity} · {product.label} · {product.price} XTR")
    elif command == "set":
        store.configure(args.key, args.value)
        print("Настройка сохранена.")
    elif command == "product":
        store.list_product(
            Product(
                args.kind,
                number(args.quantity, "Количество"),
                number(args.price, "Цена"),
            )
        )
        print("Товар сохранён.")
    elif command == "unlist":
        store.unlist(args.kind, number(args.quantity, "Количество"))
        print("Товар скрыт.")
    elif command == "orders":
        for order in store.orders(pending=args.pending):
            print(order.summary(admin=True), end="\n\n")
    elif command == "payment-errors":
        print(json.dumps(store.payment_exceptions(), ensure_ascii=False, indent=2))
    elif command == "order":
        print(store.get(args.id).summary(admin=True))
    elif command == "approve":
        order = store.approve(args.id)
        if args.now:
            claimed = store.claim(order.id)
            order = await fulfillment.deliver(claimed) if claimed else store.get(order.id)
        print(order.summary(admin=True))
    elif command == "reconcile":
        print((await fulfillment.reconcile(args.id)).summary(admin=True))
    elif command == "resolve":
        if not args.evidence.strip():
            raise ShopError("Укажите основание ручной сверки.")
        print(await fulfillment.resolve(args.id, args.outcome, args.evidence))
    elif command == "retry":
        print((await fulfillment.retry_unsent(args.id)).summary(admin=True))
    elif command == "refund-payment-error":
        if config.mode == "demo":
            await fulfillment.refund_exception(args.charge_id, DemoGateway())
        else:
            async with make_bot(config) as bot:
                await fulfillment.refund_exception(args.charge_id, make_gateway(config, bot))
        print("Возврат записан.")
    elif command == "refund":
        if config.mode == "demo":
            order = await fulfillment.refund(args.id, DemoGateway())
        else:
            async with make_bot(config) as bot:
                order = await fulfillment.refund(args.id, make_gateway(config, bot))
        print(order.summary(admin=True))
    elif command == "demo-buy":
        if config.mode != "demo":
            raise ShopError("Команда доступна в SHOP_MODE=demo.")
        if not 0 < args.buyer <= 2**52 - 1:
            raise ShopError("Введите корректный ID покупателя.")
        quantity = number(args.quantity, "Количество")
        product = next(
            (p for p in store.products() if (p.kind, p.quantity) == (args.kind, quantity)),
            None,
        )
        if product is None:
            raise ShopError("Товар не найден. Посмотрите catalog.")
        checkout = Checkout(store, DemoGateway())
        order = checkout.create(args.buyer, product, args.recipient, store.settings()["terms"])
        order = DemoGateway.pay(checkout, order.id, args.buyer)
        print(order.summary(admin=True))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = parser().parse_args()
    try:
        asyncio.run(execute(args))
    except ShopError as exc:
        sys.exit(str(exc))
    except (TelegramAPIError, FragmentError, sqlite3.Error) as exc:
        sys.exit(
            f"Операция не завершена ({type(exc).__name__}). Проверьте настройки и статус заказа."
        )
    except KeyboardInterrupt:
        pass
