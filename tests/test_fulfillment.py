from unittest.mock import AsyncMock

import pytest
from FragmentAPI import PurchaseResult
from FragmentAPI.types.models import PaymentRecord, PreparedTransaction, TransactionResult

from fragment_bot.catalog import Product
from fragment_bot.config import Config, ShopError
from fragment_bot.fulfillment import Fulfillment
from fragment_bot.payments import Checkout, PaymentReceipt, TelegramStarsGateway
from fragment_bot.store import Store


async def test_live_requires_fragment_confirmation_and_recovers_original_order(tmp_path):
    store = Store(tmp_path, "live")
    store.configure("terms", "Условия")
    store.configure("support", "@shop_support")
    product = Product("stars", 50, 100)
    store.list_product(product)
    store.configure("open", "1")
    gateway = TelegramStarsGateway(AsyncMock())
    checkout = Checkout(store, gateway)
    order = checkout.create(42, product, "@sample", "Условия")
    assert checkout.authorize(order.id, 42, 100, "XTR", "query")
    assert checkout.capture(PaymentReceipt(order.id, 42, 100, "XTR", "charge"))
    fulfillment = Fulfillment(Config("live", tmp_path), store)
    store.approve(order.id)

    sdk = AsyncMock()
    sdk.__aenter__.return_value = sdk
    record = PaymentRecord(
        operation_id="sdk-operation",
        sender_address="wallet",
        state="broadcast",
        prepared=PreparedTransaction(
            item_kind="stars", target="sample", amount=50, valid_until=100
        ),
        receipt=TransactionResult(tx_hash="hash"),
    )

    async def broadcast(*_args, **_kwargs):
        await fulfillment.journal.save(record)
        return PurchaseResult(
            transaction_id="hash",
            type="stars",
            username="sample",
            amount=50,
            operation_id=record.operation_id,
            confirmed=False,
        )

    sdk.purchase.side_effect = broadcast
    fulfillment.client = lambda **_kwargs: sdk
    result = await fulfillment.deliver(store.claim(order.id))
    sdk.purchase.assert_awaited_once_with("stars", "sample", amount=50, show_sender=False)
    assert result.state == "review"
    assert result.operation_id == record.operation_id
    with pytest.raises(ShopError, match="Возврат недоступен"):
        await fulfillment.refund(order.id, gateway)
    with pytest.raises(ShopError, match="Повтор доступен"):
        await fulfillment.retry_unsent(order.id)

    record.state = "confirmed"
    sdk.reconcile_payment.return_value = record
    confirmed = await fulfillment.reconcile(order.id)
    sdk.reconcile_payment.assert_awaited_once_with(record.operation_id)
    assert confirmed.state == "delivered"
    assert confirmed.transaction_id == "hash"
    assert sdk.purchase.await_count == 1
