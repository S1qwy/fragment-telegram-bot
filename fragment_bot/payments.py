from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from aiogram import Bot
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.methods import SendInvoice
from aiogram.types import InlineKeyboardButton, LabeledPrice
from aiogram.utils.token import TokenValidationError

from .catalog import Product, username
from .config import Config, ShopError
from .provider_config import ProviderConfig
from .store import Order, Store
from .transport import ProxySession
from .views import (
    IconPreference,
    back,
    button,
    keys,
    order_card,
    price,
    render_markup,
    request_with_icon_fallback,
    resolve_icon_mode,
    send_html,
)


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
    def __init__(
        self, store: Store, gateway: PaymentGateway, *, inherited_icons: bool = False
    ) -> None:
        self.store = store
        self.gateway = gateway
        self.inherited_icons = inherited_icons

    def icon_mode(self) -> IconPreference:
        return resolve_icon_mode(self.store.settings()["premium_icons"], self.inherited_icons)

    def create(self, buyer: int, product: Product, recipient: str, terms: str) -> Order:
        if (
            self.gateway.name == "telegram_stars"
            and not ProviderConfig(self.store).values()["TELEGRAM_STARS_ENABLED"]
        ):
            raise ShopError("Оплата Telegram Stars отключена.")
        return self.store.create_order(
            buyer,
            product,
            username(recipient),
            terms,
            provider=self.gateway.name,
            currency=self.gateway.currency,
            payment_amount=self.gateway.amount(product.price)
            if hasattr(self.gateway, "amount")
            else None,
        )

    def authorize(self, order_id: str, buyer: int, amount: int, currency: str, query: str) -> bool:
        if (
            self.gateway.name == "telegram_stars"
            and not ProviderConfig(self.store).values()["TELEGRAM_STARS_ENABLED"]
        ):
            return False
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

    def __init__(
        self, bot: Bot | None = None, *, icons: Callable[[], IconPreference] | None = None
    ) -> None:
        self.bot = bot
        self.icons = icons

    async def checkout(self, order: Order) -> None:
        if self.bot is None:
            raise ShopError("Для отправки счёта нужен запущенный бот.")
        await send_html(
            self.bot,
            order.buyer,
            "<b>🧪 Тестовый счёт</b>\n\n" + order_card(order),
            keys(
                [button("💳 Имитировать оплату", f"demo:{order.id}", style="success")],
                back("orders", "К заказам"),
            ),
            icons=self.icons() if self.icons else False,
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

    def __init__(self, bot: Bot, *, icons: Callable[[], IconPreference] | None = None) -> None:
        self.bot = bot
        self.icons = icons

    async def checkout(self, order: Order) -> None:
        icons = self.icons() if self.icons else False
        label = order.product.label
        if icons in {"premium_only", "off"}:
            # Invoice titles and price labels do not support custom emoji entities.
            label = label.replace(" ⭐", "")
        markup = keys(
            [
                InlineKeyboardButton(
                    text=f"Оплатить {price(order.price)}", pay=True, style="success"
                )
            ],
            back("orders", "К заказам"),
        )
        fallback = render_markup(markup, "off" if icons == "premium_only" else "unicode")
        await request_with_icon_fallback(
            self.bot,
            SendInvoice(
                chat_id=order.buyer,
                title=label,
                description=f"Получатель @{order.recipient} · заказ #{order.id}",
                payload=order.id,
                currency=order.currency,
                prices=[LabeledPrice(label=label, amount=order.price)],
                provider_token="",
                start_parameter=f"order-{order.id}",
                reply_markup=render_markup(markup, icons),
            ),
            {"reply_markup": fallback} if icons in {True, "premium", "premium_only"} else None,
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
            config.token, session=ProxySession(api=api, proxy=config.telegram_proxy, timeout=8)
        )
    except TokenValidationError:
        raise ShopError("Укажите корректный BOT_TOKEN в .env.") from None
    except ValueError:
        raise ShopError("Проверьте URL прокси TELEGRAM_PROXY в .env.") from None


def make_gateway(
    config: Config, bot: Bot | None = None, *, store: Store | None = None
) -> PaymentGateway:
    def icons() -> IconPreference:
        preference = store.settings()["premium_icons"] if store is not None else "inherit"
        return resolve_icon_mode(preference, config.premium_icons)

    if config.mode == "demo":
        return DemoGateway(bot, icons=icons)
    if bot is None:
        raise ShopError("Для платежей XTR нужен BOT_TOKEN.")
    return TelegramStarsGateway(bot, icons=icons)
