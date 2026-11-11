from dataclasses import dataclass
from typing import Protocol

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import InlineKeyboardButton, LabeledPrice
from aiogram.utils.token import TokenValidationError

from .catalog import Product, username
from .config import Config, ShopError
from .store import Order, Store
from .views import back, button, keys, order_card, price


@dataclass(frozen=True)
class PaymentReceipt:
    """A final payment verified by the provider adapter."""

    order_id: str
    buyer: int
    amount: int
    currency: str
    charge_id: str


class PaymentGateway(Protocol):
    name: str
    currency: str

    async def checkout(self, order: Order) -> None: ...

    async def refund(self, buyer: int, charge_id: str) -> None: ...


class Checkout:
    def __init__(self, store: Store, gateway: PaymentGateway) -> None:
        self.store = store
        self.gateway = gateway

    def create(self, buyer: int, product: Product, recipient: str, terms: str) -> Order:
        return self.store.create_order(
            buyer,
            product,
            username(recipient),
            terms,
            provider=self.gateway.name,
            currency=self.gateway.currency,
        )

    def authorize(self, order_id: str, buyer: int, amount: int, currency: str, query: str) -> bool:
        return currency == self.gateway.currency and self.store.reserve_checkout(
            order_id,
            buyer,
            amount,
            query,
            provider=self.gateway.name,
            currency=currency,
        )

    def capture(self, receipt: PaymentReceipt) -> bool:
        return self.store.accept_payment(
            receipt.order_id,
            receipt.buyer,
            receipt.amount,
            receipt.currency,
            receipt.charge_id,
            provider=self.gateway.name,
        )


class DemoGateway:
    name = "demo"
    currency = "XTR"

    def __init__(self, bot: Bot | None = None) -> None:
        self.bot = bot

    async def checkout(self, order: Order) -> None:
        if self.bot is None:
            raise ShopError("Для отправки счёта нужен запущенный бот.")
        await self.bot.send_message(
            order.buyer,
            "<b>🧪 Тестовый счёт</b>\n\n" + order_card(order),
            parse_mode=ParseMode.HTML,
            reply_markup=keys(
                [button("💳 Имитировать оплату", f"demo:{order.id}", style="success")],
                back("orders", "К заказам"),
            ),
        )

    async def refund(self, buyer: int, charge_id: str) -> None:
        if not charge_id.startswith("demo-"):
            raise ShopError("Это не тестовый платёж.")

    @staticmethod
    def pay(checkout: Checkout, order_id: str, buyer: int) -> Order:
        if not isinstance(checkout.gateway, DemoGateway) or checkout.store.mode != "demo":
            raise ShopError("Имитация оплаты доступна в demo.")
        order = checkout.store.get(order_id, buyer=buyer)
        charge = f"demo-{order.id}"
        if checkout.authorize(order.id, buyer, order.price, order.currency, charge):
            checkout.capture(PaymentReceipt(order.id, buyer, order.price, order.currency, charge))
        return checkout.store.get(order.id, buyer=buyer)


class TelegramStarsGateway:
    name = "telegram_stars"
    currency = "XTR"

    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def checkout(self, order: Order) -> None:
        await self.bot.send_invoice(
            chat_id=order.buyer,
            title=order.product.label,
            description=f"Получатель @{order.recipient} · заказ #{order.id}",
            payload=order.id,
            currency=order.currency,
            prices=[LabeledPrice(label=order.product.label, amount=order.price)],
            provider_token="",
            start_parameter=f"order-{order.id}",
            reply_markup=keys(
                [
                    InlineKeyboardButton(
                        text=f"Оплатить {price(order.price)}", pay=True, style="success"
                    )
                ],
                back("orders", "К заказам"),
            ),
        )

    async def refund(self, buyer: int, charge_id: str) -> None:
        try:
            refunded = await self.bot.refund_star_payment(
                user_id=buyer,
                telegram_payment_charge_id=charge_id,
                request_timeout=5,
            )
        except TelegramBadRequest as exc:
            if "CHARGE_ALREADY_REFUNDED" not in exc.message.upper():
                raise ShopError("Telegram отклонил возврат. Проверьте исходный платёж.") from None
        except TelegramAPIError:
            raise ShopError(
                "Исход возврата неясен. Повторите возврат по этому же заказу."
            ) from None
        else:
            if not refunded:
                raise ShopError("Telegram не подтвердил возврат.")


def make_bot(config: Config) -> Bot:
    api = TelegramAPIServer.from_base("https://api.telegram.org", is_local=False)
    if config.mode == "test":
        api = TelegramAPIServer(
            base="https://api.telegram.org/bot{token}/test/{method}",
            file="https://api.telegram.org/file/bot{token}/test/{path}",
        )
    try:
        return Bot(
            config.token, session=AiohttpSession(api=api, proxy=config.telegram_proxy, timeout=8)
        )
    except TokenValidationError:
        raise ShopError("Укажите корректный BOT_TOKEN в .env.") from None
    except ValueError:
        raise ShopError("Проверьте URL прокси TELEGRAM_PROXY в .env.") from None


def make_gateway(config: Config, bot: Bot | None = None) -> PaymentGateway:
    if config.mode == "demo":
        return DemoGateway(bot)
    if bot is None:
        raise ShopError("Для платежей XTR нужен BOT_TOKEN.")
    return TelegramStarsGateway(bot)
