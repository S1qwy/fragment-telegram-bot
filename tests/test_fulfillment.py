from time import time
from unittest.mock import AsyncMock, Mock

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
    product = Product("premium", 3, 100)
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
            item_kind="premium", target="sample", amount=3, valid_until=100
        ),
        receipt=TransactionResult(tx_hash="hash"),
    )

    async def broadcast(*_args, **_kwargs):
        await fulfillment.journal.save(record)
        return PurchaseResult(
            transaction_id="hash",
            type="premium",
            username="sample",
            amount=3,
            operation_id=record.operation_id,
            confirmed=False,
        )

    sdk.purchase.side_effect = broadcast
    fulfillment.client = Mock(return_value=sdk)
    result = await fulfillment.deliver(store.claim(order.id))
    fulfillment.client.assert_called_once_with("pay")
    sdk.purchase.assert_awaited_once_with("premium", "sample", amount=3, show_sender=False)
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


async def test_stars_persists_prepared_transaction_and_waits_for_external_signer(
    tmp_path, monkeypatch
):
    store = Store(tmp_path, "live")
    store.configure("terms", "Условия")
    store.configure("support", "@shop_support")
    product = Product("stars", 50, 100)
    store.list_product(product)
    store.configure("open", "1")
    bot = AsyncMock()
    gateway = TelegramStarsGateway(bot)
    checkout = Checkout(store, gateway)
    order = checkout.create(42, product, "@sample", "Условия")
    assert checkout.authorize(order.id, 42, 100, "XTR", "query")
    assert checkout.capture(PaymentReceipt(order.id, 42, 100, "XTR", "charge"))
    config = Config("live", tmp_path)
    fulfillment = Fulfillment(config, store)
    store.approve(order.id)

    prepared = PreparedTransaction(
        req_id="stars-request",
        item_kind="stars",
        target="sample",
        amount=50,
        valid_until=int(time()) + 60,
    )
    sdk = AsyncMock()
    sdk.__aenter__.return_value = sdk
    sdk.purchase.return_value = prepared
    fulfillment.client = Mock(return_value=sdk)

    result = await fulfillment.deliver(store.claim(order.id))
    fulfillment.client.assert_called_once_with("prepare")
    sdk.purchase.assert_awaited_once_with("stars", "sample", amount=50, show_sender=False)
    assert result.state == "prepared"
    assert result.operation_id is None and result.transaction_id is None
    assert store.claim(order.id) is None

    restarted = Fulfillment(config, Store(tmp_path, "live"))
    assert restarted.prepared(order.id) == prepared
    assert restarted.store.orders(pending=True)[0].state == "prepared"
    with pytest.raises(ShopError, match="Возврат недоступен"):
        await restarted.refund(order.id, gateway)
    with pytest.raises(ShopError, match="Дождитесь"):
        await restarted.resolve_prepared(order.id, "not_broadcast", "Отсутствует в блокчейне")
    monkeypatch.setattr("fragment_bot.fulfillment.time", lambda: prepared.valid_until + 1)
    void = await restarted.resolve_prepared(order.id, "not_broadcast", "Отсутствует в блокчейне")
    assert void.state == "prepared_void"
    bot.refund_star_payment.return_value = True
    refunded = await restarted.refund(order.id, gateway)
    assert refunded.state == "refunded"


async def test_preparation_client_uses_public_account_without_wallet_credentials(tmp_path):
    account = {
        "address": "public-address",
        "chain": "-239",
        "publicKey": "public-key",
        "walletStateInit": "state-init",
    }
    config = Config(
        "live",
        tmp_path,
        cookies="stel_ssid=session; stel_dt=date; stel_token=token",
        sender_account=account,
        seed="invalid-seed-that-the-prepare-client-must-ignore",
        api_key="configured-for-other-products",
    )
    async with Fulfillment(config, Store(tmp_path, "live")).client("prepare") as client:
        assert client.sender_account == account
        assert client.seed is None and client.api_key is None
        assert not client.has_wallet
