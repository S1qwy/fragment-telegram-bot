import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from FragmentAPI.types.models import PaymentRecord, PreparedTransaction, PreparedTransactionMessage

from fragment_bot.catalog import Product
from fragment_bot.config import Config, ShopError
from fragment_bot.fulfillment import Fulfillment, OrderPaymentJournal, active_order
from fragment_bot.payments import Checkout, DemoGateway, PaymentReceipt
from fragment_bot.store import Store


def create_checkout(tmp_path):
    store = Store(tmp_path, "demo")
    checkout = Checkout(store, DemoGateway())
    order = checkout.create(42, Product("stars", 50, 100), "@Sample", "")
    return store, checkout, order


def test_checkout_binds_buyer_amount_currency_and_single_attempt(tmp_path):
    store, checkout, order = create_checkout(tmp_path)
    assert not checkout.authorize(order.id, 7, 100, "XTR", "q")
    assert not checkout.authorize(order.id, 42, 99, "XTR", "q")
    assert not checkout.authorize(order.id, 42, 100, "USD", "q")
    assert checkout.authorize(order.id, 42, 100, "XTR", "q")
    assert checkout.authorize(order.id, 42, 100, "XTR", "q")
    assert not checkout.authorize(order.id, 42, 100, "XTR", "second")
    assert store.get(order.id).state == "unpaid"


def test_wrong_receipt_stays_in_exception_ledger(tmp_path):
    store, checkout, order = create_checkout(tmp_path)
    assert checkout.authorize(order.id, 42, 100, "XTR", "query")
    receipt = PaymentReceipt(order.id, 42, 99, "XTR", "charge")
    with pytest.raises(ShopError, match="сохранён для проверки"):
        checkout.capture(receipt)
    assert not checkout.capture(receipt)
    assert store.get(order.id).state == "unpaid"
    assert store.payment_exceptions()[0]["charge_id"] == "charge"


def test_capture_and_worker_claim_are_idempotent(tmp_path):
    store, checkout, order = create_checkout(tmp_path)
    assert checkout.authorize(order.id, 42, 100, "XTR", "query")
    receipt = PaymentReceipt(order.id, 42, 100, "XTR", "charge")
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(checkout.capture, [receipt, receipt]))
    assert sorted(results) == [False, True]
    store.approve(order.id)
    with ThreadPoolExecutor(max_workers=2) as workers:
        claims = list(workers.map(store.claim, [order.id, order.id]))
    assert sum(claim is not None for claim in claims) == 1


def test_consent_and_price_are_checked_against_current_catalog(tmp_path):
    store, checkout, order = create_checkout(tmp_path)
    store.configure("terms", "New terms")
    with pytest.raises(ShopError, match="изменились"):
        checkout.create(42, order.product, "sample", "")
    store.list_product(Product("stars", 50, 110))
    with pytest.raises(ShopError, match="изменились"):
        checkout.create(42, order.product, "sample", "New terms")
    assert checkout.authorize(order.id, 42, 100, "XTR", "query")


def test_live_starts_closed_and_binds_installation(tmp_path):
    store = Store(tmp_path, "live")
    assert store.products() == []
    store.list_product(Product("premium", 3, 950))
    with pytest.raises(ShopError, match="terms"):
        store.configure("open", "1")
    store.configure("terms", "Условия")
    store.configure("support", "@shop_support")
    store.configure("open", "1")
    store.bind_bot(1)
    store.bind_bot(1)
    with pytest.raises(ShopError, match="другим"):
        store.bind_bot(2)
    with pytest.raises(ShopError, match="DATA_DIR"):
        Store(tmp_path, "demo")


def pending_record():
    return PaymentRecord(
        operation_id="sdk-operation",
        sender_address="test-sender",
        state="sending",
        prepared=PreparedTransaction(
            item_kind="stars",
            target="sample",
            amount=50,
            valid_until=2**31,
            messages=[PreparedTransactionMessage(address="test-recipient", amount="90")],
            gas_reserve_nanoton=20,
        ),
    )


async def test_journal_commits_link_before_broadcast_and_enforces_actual_amount(tmp_path):
    store, checkout, order = create_checkout(tmp_path)
    DemoGateway.pay(checkout, order.id, order.buyer)
    store.approve(order.id)
    assert store.claim(order.id)
    record = pending_record()
    token = active_order.set(order.id)
    try:
        with pytest.raises(ShopError, match="FRAGMENT_MAX_TON"):
            await OrderPaymentJournal(store, 100).save(record)
        assert store.get(order.id).operation_id is None
        journal = OrderPaymentJournal(store, 110)
        await journal.save(record)
        assert store.get(order.id).operation_id == record.operation_id
        assert await journal.get(record.operation_id) == record
        with pytest.raises(ShopError, match="Возврат недоступен"):
            store.start_refund(order.id)
    finally:
        active_order.reset(token)


async def test_refund_fences_broadcast_when_intent_is_missing(tmp_path):
    store, checkout, order = create_checkout(tmp_path)
    DemoGateway.pay(checkout, order.id, order.buyer)
    store.approve(order.id)
    assert store.claim(order.id)
    fulfillment = Fulfillment(Config("demo", tmp_path), store)
    assert (await fulfillment.refund(order.id, checkout.gateway)).state == "refunded"
    token = active_order.set(order.id)
    try:
        with pytest.raises(ShopError, match="не разрешает"):
            await fulfillment.journal.save(pending_record())
    finally:
        active_order.reset(token)


@pytest.mark.parametrize(
    ("kind", "quantity", "price"), [("stars", 50, 100), ("premium", 3, 950), ("ton", 1, 350)]
)
async def test_demo_fulfillment_for_every_product(tmp_path, kind, quantity, price):
    store = Store(tmp_path, "demo")
    checkout = Checkout(store, DemoGateway())
    fulfillment = Fulfillment(Config("demo", tmp_path), store)
    order = checkout.create(42, Product(kind, quantity, price), "sample", "")
    assert DemoGateway.pay(checkout, order.id, order.buyer).state == "paid"
    assert store.approve(order.id).state == "queued"
    result = await asyncio.wait_for(fulfillment.deliver(store.claim(order.id)), timeout=1)
    if kind == "stars":
        assert result.state == "prepared"
        prepared = fulfillment.prepared(order.id)
        assert prepared.raw == {"simulated": True}
        assert prepared.messages == []
        assert prepared.sender_address is None
    else:
        assert result.state == "delivered"
        assert result.transaction_id == f"test-{order.id}"


async def test_admin_order_pages_and_xtr_totals(tmp_path):
    store, checkout, first = create_checkout(tmp_path)
    for index in range(8):
        checkout.create(99, first.product, f"sample{index}", "")
    DemoGateway.pay(checkout, first.id, first.buyer)
    fulfillment = Fulfillment(Config("demo", tmp_path), store)
    await fulfillment.refund(first.id, checkout.gateway)

    first_page, total, page = store.admin_orders("all")
    last_page, _, last = store.admin_orders("all", 99)
    assert len(first_page) == 7 and len(last_page) == 2
    assert (total, page, last) == (9, 0, 1)
    assert store.admin_orders("all", search=first.id[:6])[0][0].id == first.id
    buyer_matches, _, _ = store.admin_orders("all", search=str(first.buyer))
    assert first.id in {order.id for order in buyer_matches}
    assert store.admin_orders("queue")[1] == 0
    assert store.admin_orders("closed")[1] == 1
    assert store.admin_orders("payments")[1] == 1
    totals = store.shop_totals()
    assert (totals["received_xtr"], totals["returned_xtr"], totals["buyers"]) == (100, 100, 2)
