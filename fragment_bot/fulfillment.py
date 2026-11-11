import asyncio
import logging
from contextvars import ContextVar
from typing import Literal

from FragmentAPI import FragmentClient, FragmentError, PurchaseResult
from FragmentAPI.storage.payments import PaymentStorage
from FragmentAPI.types.models import PaymentRecord
from FragmentAPI.utils.locking import file_wallet_lock

from .catalog import Product
from .config import Config, ShopError
from .payments import PaymentGateway
from .store import Order, Store

log = logging.getLogger(__name__)
active_order: ContextVar[str | None] = ContextVar("active_order", default=None)


class OrderPaymentJournal(PaymentStorage):
    """Commit the SDK intent and its order link in the same transaction."""

    def __init__(self, store: Store, max_nanoton: int) -> None:
        self.store = store
        self.max_nanoton = max_nanoton

    async def save(self, record: PaymentRecord) -> None:
        await asyncio.to_thread(self._save, record, active_order.get())

    def _save(self, record: PaymentRecord, order_id: str | None) -> None:
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT 1 FROM fragment_payments WHERE operation_id=?", (record.operation_id,)
            ).fetchone()
            if previous is None:
                prepared = record.prepared
                required = sum(int(message.amount) for message in prepared.messages)
                if required + prepared.gas_reserve_nanoton > self.max_nanoton:
                    raise ShopError(
                        "Сумма Fragment с комиссией и резервом превышает FRAGMENT_MAX_TON."
                    )
                linked = db.execute(
                    "UPDATE orders SET operation_id=? "
                    "WHERE id=? AND state='sending' AND operation_id IS NULL",
                    (record.operation_id, order_id),
                ).rowcount
                if not linked:
                    raise ShopError("Заказ больше не разрешает отправку.")
            db.execute(
                "INSERT INTO fragment_payments VALUES (?,?,?) "
                "ON CONFLICT(operation_id) DO UPDATE SET document=excluded.document",
                (record.operation_id, record.sender_address, record.model_dump_json()),
            )

    def _load(self, clause: str = "", args: tuple[str, ...] = ()) -> list[PaymentRecord]:
        with self.store.connection() as db:
            return [
                PaymentRecord.model_validate_json(row[0])
                for row in db.execute(f"SELECT document FROM fragment_payments {clause}", args)
            ]

    async def get(self, operation_id: str) -> PaymentRecord | None:
        rows = await asyncio.to_thread(self._load, "WHERE operation_id=?", (operation_id,))
        return rows[0] if rows else None

    async def list(self, sender_address: str | None = None) -> list[PaymentRecord]:
        if sender_address is None:
            return await asyncio.to_thread(self._load)
        return await asyncio.to_thread(self._load, "WHERE sender=?", (sender_address,))


class Fulfillment:
    def __init__(self, config: Config, store: Store) -> None:
        self.config = config
        self.store = store
        self.journal = OrderPaymentJournal(store, config.max_nanoton)

    def operation_lock(self):
        return file_wallet_lock(f"fragment-bot:{self.store.path.resolve()}")

    def client(self, *, payer: bool = False) -> FragmentClient:
        if not self.config.cookies:
            raise ShopError("Укажите FRAGMENT_COOKIES.")
        if payer:
            self.config.require_wallet()
        try:
            client = FragmentClient(
                cookies=self.config.cookies,
                seed=self.config.seed if payer else None,
                api_key=self.config.api_key if payer else None,
                api_provider=self.config.api_provider,
                wallet_version=self.config.wallet_version,
                mnemonic_type=self.config.mnemonic_type,
                payment_storage=self.journal,
            )
        except FragmentError:
            raise ShopError("Проверьте настройки сессии Fragment и TON-кошелька.") from None
        if payer and not client.has_ton_token:
            raise ShopError("Сессия Fragment должна содержать stel_ton_token.")
        return client

    async def verify_recipient(self, product: Product, recipient: str) -> str | None:
        if self.config.mode != "live":
            return None
        try:
            async with self.client() as client, asyncio.timeout(5):
                if product.kind == "stars":
                    result = await client.get_stars_recipient(recipient)
                elif product.kind == "premium":
                    result = await client.get_premium_recipient(recipient, product.quantity)
                else:
                    result = await client.get_ads_topup_recipient(recipient)
        except (FragmentError, TimeoutError):
            raise ShopError(
                "Fragment временно не смог проверить получателя. Повторите позже."
            ) from None
        if result is None:
            raise ShopError("Fragment не нашёл получателя для этого товара.")
        return result.name[:160]

    async def deliver(self, order: Order) -> Order:
        async with self.operation_lock():
            order = self.store.get(order.id)
            if order.state != "sending":
                return order
            if self.config.mode != "live":
                return self.store.transition(
                    order.id,
                    ("sending",),
                    "delivered",
                    transaction_id=f"test-{order.id}",
                )
            token = active_order.set(order.id)
            try:
                async with self.client(payer=True) as client:
                    result = await client.purchase(
                        order.kind,
                        order.recipient,
                        amount=order.quantity,
                        show_sender=False,
                    )
                if isinstance(result, PurchaseResult) and result.confirmed:
                    return self.store.transition(
                        order.id,
                        ("sending",),
                        "delivered",
                        transaction_id=result.transaction_id,
                    )
                transaction_id = (
                    result.transaction_id if isinstance(result, PurchaseResult) else None
                )
                note = "Fragment пока не подтвердил выдачу. Сверьте исходную операцию."
            except ShopError as exc:
                transaction_id = None
                note = str(exc)
            except Exception as exc:
                log.warning("Fulfillment for %s failed (%s)", order.id, type(exc).__name__)
                transaction_id = None
                note = f"Fragment: {type(exc).__name__}. Проверьте журнал и исходную операцию."
            finally:
                active_order.reset(token)
            try:
                return self.store.transition(
                    order.id,
                    ("sending",),
                    "review",
                    transaction_id=transaction_id,
                    note=note,
                )
            except ShopError:
                return self.store.get(order.id)

    async def reconcile(self, order_id: str) -> Order:
        async with self.operation_lock():
            order = self.store.get(order_id)
            if self.config.mode != "live" or not order.operation_id:
                raise ShopError("У заказа нет записанной операции Fragment.")
            if order.state == "delivered":
                return order
            if order.state not in {"sending", "review"}:
                raise ShopError("Сверка доступна для заказа на выдаче или проверке.")
            try:
                async with self.client() as client:
                    record = await client.reconcile_payment(order.operation_id)
            except FragmentError:
                raise ShopError(
                    "Fragment не подтвердил результат. Проверьте журнал и блокчейн."
                ) from None
            if record.state == "confirmed" and record.receipt:
                try:
                    return self.store.transition(
                        order.id,
                        ("sending", "review"),
                        "delivered",
                        transaction_id=record.receipt.tx_hash,
                    )
                except ShopError:
                    return self.store.get(order.id)
            return self.store.get(order.id)

    async def resolve(
        self, order_id: str, outcome: Literal["settled", "not_broadcast"], evidence: str
    ) -> str:
        async with self.operation_lock():
            order = self.store.get(order_id)
            if order.state not in {"review", "sending"} or not order.operation_id:
                raise ShopError("У заказа нет операции для ручной сверки.")
            try:
                async with self.client() as client:
                    record = await client.resolve_payment(
                        order.operation_id, outcome=outcome, evidence=evidence
                    )
            except FragmentError:
                raise ShopError(
                    "Fragment отклонил ручную сверку. Проверьте состояние операции."
                ) from None
            return record.state

    async def refund(self, order_id: str, gateway: PaymentGateway) -> Order:
        async with self.operation_lock():
            order = self.store.get(order_id)
            if order.provider != gateway.name:
                raise ShopError("Для возврата подключите провайдера заказа.")
            if order.state == "refunded":
                return order
            record = await self.journal.get(order.operation_id) if order.operation_id else None
            order = self.store.start_refund(
                order.id,
                verified_not_sent=bool(record and record.state == "not_broadcast"),
            )
            if not order.charge_id:
                raise ShopError("У заказа нет платежа.")
            await gateway.refund(order.buyer, order.charge_id)
            try:
                return self.store.transition(order.id, ("refunding",), "refunded")
            except ShopError:
                return self.store.get(order.id)

    async def retry_unsent(self, order_id: str) -> Order:
        async with self.operation_lock():
            return self.store.retry_unsent(order_id)

    async def refund_exception(self, charge_id: str, gateway: PaymentGateway) -> None:
        async with self.operation_lock():
            payment = self.store.payment_exception(gateway.name, charge_id)
            if payment["currency"] != gateway.currency:
                raise ShopError("Валюта платежа не совпадает с валютой провайдера.")
            if payment["refunded_at"] is None:
                await gateway.refund(payment["buyer"], charge_id)
                self.store.mark_exception_refunded(gateway.name, charge_id)
