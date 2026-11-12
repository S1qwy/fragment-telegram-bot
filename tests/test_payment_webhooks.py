import hashlib
import hmac
import json

from aiohttp.test_utils import TestClient, TestServer

from fragment_bot.catalog import Product
from fragment_bot.external_payments import ExternalGateway
from fragment_bot.payment_webhooks import webhook_app
from fragment_bot.payments import Checkout
from fragment_bot.provider_config import ProviderConfig
from fragment_bot.providers import provider_client
from fragment_bot.store import Store


async def test_etoplatezhi_requires_signed_bound_invoice_and_deduplicates(tmp_path):
    store = Store(tmp_path, "demo")
    config = ProviderConfig(store)
    config.set("ETOPLATEZHI_PROJECT_ID", 777)
    config.set("ETOPLATEZHI_SECRET_KEY", "test-secret")
    config.set("ETOPLATEZHI_CHECKOUT_RATE", "2")
    config.set("ETOPLATEZHI_ENABLED", True)
    gateway = ExternalGateway(store, "etoplatezhi")
    order = Checkout(store, gateway).create(42, Product("stars", 50, 100), "sample", "")
    await gateway.checkout(order)
    payload = {
        "project_id": 777,
        "customer": {"id": "42"},
        "payment": {
            "id": order.id,
            "status": "success",
            "sum": {"amount": order.price, "currency": "RUB"},
        },
    }
    async with provider_client(config, "etoplatezhi") as client:
        payload["signature"] = client._sign(payload)
    async with TestClient(TestServer(webhook_app(store))) as http:
        wrong = {**payload, "customer": {"id": "99"}}
        assert (await http.post("/payments/etoplatezhi", json=wrong)).status == 401
        async with provider_client(config, "etoplatezhi") as client:
            wrong["signature"] = client._sign(wrong)
        assert (await http.post("/payments/etoplatezhi", json=wrong)).status == 409
        assert store.get(order.id).state == "unpaid"
        for _ in range(2):
            assert (await http.post("/payments/etoplatezhi", json=payload)).status == 200
    assert store.get(order.id).state == "paid"
    assert store.shop_totals()["payments"] == 1


async def test_tribute_donation_is_logged_once_without_crediting_order(tmp_path):
    store = Store(tmp_path, "demo")
    config = ProviderConfig(store)
    config.set("TRIBUTE_API_KEY", "test-secret")
    body = json.dumps({"name": "new_donation", "payload": {"telegram_user_id": 42, "amount": 100}})
    signature = hmac.new(b"test-secret", body.encode(), hashlib.sha256).hexdigest()
    async with TestClient(TestServer(webhook_app(store))) as http:
        assert (await http.post("/payments/tribute", data=body)).status == 401
        for _ in range(2):
            assert (
                await http.post(
                    "/payments/tribute", data=body, headers={"trbt-signature": signature}
                )
            ).status == 202
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM tribute_events").fetchone()[0] == 1
    assert store.shop_totals()["payments"] == 0
