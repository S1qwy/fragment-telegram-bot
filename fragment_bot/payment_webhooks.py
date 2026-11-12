"""Signed, order-bound callbacks for upstream webhook-only payment flows."""

import hashlib
import json
import os

from aiohttp import web

from .external_payments import ExternalGateway
from .payments import Checkout, PaymentReceipt
from .provider_config import ProviderConfig
from .providers import provider_call


def webhook_app(store):
    configuration = ProviderConfig(store)
    ExternalGateway(
        store, "etoplatezhi"
    )  # Initialize the invoice schema before the first callback.
    with store.connection() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS tribute_events (
            event_hash TEXT PRIMARY KEY, payload BLOB NOT NULL
        )""")
    app = web.Application(client_max_size=256 * 1024)

    async def etoplatezhi(request):
        try:
            payload = await request.json()
            if not configuration.values()["ETOPLATEZHI_SECRET_KEY"]:
                return web.Response(status=503)
            valid = await provider_call(
                configuration, "etoplatezhi", "verify_callback_signature", {"payload": payload}
            )
            if not valid:
                return web.Response(status=401)
            values = configuration.values()
            payment = payload["payment"]
            order = store.get(str(payment["id"]))
            if payment["status"] != "success":
                return web.json_response({"received": True})
            money = payment["sum"]
            with store.connection() as db:
                invoice = db.execute(
                    "SELECT 1 FROM hosted_invoices WHERE order_id=? AND provider='etoplatezhi'",
                    (order.id,),
                ).fetchone()
            if (
                order.provider != "etoplatezhi"
                or not invoice
                or order.checkout_id != "hosted-" + order.id
                or payload["project_id"] != values["ETOPLATEZHI_PROJECT_ID"]
                or str(payload["customer"]["id"]) != str(order.buyer)
                or type(money["amount"]) is not int
                or money["amount"] != order.price
                or money["currency"] != order.currency
            ):
                return web.Response(status=409)
            Checkout(store, ExternalGateway(store, "etoplatezhi")).capture(
                PaymentReceipt(order.id, order.buyer, order.price, order.currency, order.id)
            )
            return web.json_response({"received": True})
        except (ValueError, KeyError, TypeError, AttributeError):
            return web.Response(status=400)

    async def tribute(request):
        try:
            body = await request.text()
            if not configuration.values()["TRIBUTE_API_KEY"]:
                return web.Response(status=503)
            valid = await provider_call(
                configuration,
                "tribute",
                "verify_webhook_signature",
                {"payload": body, "signature": request.headers.get("trbt-signature", "")},
            )
            if not valid:
                return web.Response(status=401)
            raw = json.loads(body)
            if raw.get("name") != "new_donation":
                return web.json_response({"received": True, "applied": False})
            # Donation events do not contain our order ID. Keep verified events for reconciliation.
            fingerprint = hashlib.sha256(body.encode()).hexdigest()
            document = configuration.cipher.encrypt(body.encode())
            with store.connection() as db:
                db.execute(
                    "INSERT OR IGNORE INTO tribute_events VALUES (?, ?)", (fingerprint, document)
                )
            return web.json_response({"received": True, "applied": False}, status=202)
        except (ValueError, KeyError, TypeError, AttributeError):
            return web.Response(status=400)

    app.router.add_post("/payments/etoplatezhi", etoplatezhi)
    app.router.add_post("/payments/tribute", tribute)
    return app


async def start_webhooks(store):
    port = int(os.getenv("PAYMENT_WEBHOOK_PORT", "0"))
    if not port:
        return
    runner = web.AppRunner(webhook_app(store), access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, os.getenv("PAYMENT_WEBHOOK_HOST", "127.0.0.1"), port).start()
        return runner
    except BaseException:
        await runner.cleanup()
        raise
