"""Order-bound hosted payments using the ported Bedolaga API clients."""

import json
import os
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from urllib.parse import urlsplit

from .config import ShopError
from .provider_config import PROVIDERS, ProviderConfig
from .providers import provider_call

# Native IAP and webhook-only upstream donation/form flows have no queryable invoice API.
HOSTED = set(PROVIDERS) - {"telegram_stars", "apple_iap", "tribute"}
WEBHOOK_ONLY = {"etoplatezhi"}
RUB_ONLY = {
    "anore",
    "aurapay",
    "cashera",
    "cispay",
    "lava",
    "mulenpay",
    "pal24",
    "paritypay",
    "riopay",
    "tabpay",
}
CONFIGURED_CURRENCY = {"antilopay", "cloudpayments", "donut", "jupiter"}
REQUIRED = {
    "tribute": "API_KEY DONATE_LINK CALLBACK_URL",
    "etoplatezhi": "PROJECT_ID SECRET_KEY CALLBACK_URL",
    "anore": "API_KEY SECRET_KEY SHOP_ID",
    "antilopay": "SECRET_ID PRIVATE_KEY PROJECT_ID",
    "aurapay": "API_KEY SHOP_ID",
    "cashera": "API_KEY API_SECRET",
    "cispay": "SHOP_ID API_KEY",
    "cloudpayments": "PUBLIC_ID API_SECRET",
    "cryptobot": "API_TOKEN",
    "donut": "TOKEN SECRET METHOD_ID",
    "freekassa": "SHOP_ID API_KEY SECRET_WORD_1 SECRET_WORD_2",
    "heleket": "MERCHANT_ID API_KEY",
    "jupiter": "TOKEN SECRET METHOD_ID",
    "kassa_ai": "SHOP_ID API_KEY SECRET_WORD_2",
    "lava": "SHOP_ID SECRET_KEY",
    "mulenpay": "API_KEY SECRET_KEY SHOP_ID",
    "overpay": "USERNAME PASSWORD PROJECT_ID",
    "pal24": "API_TOKEN SHOP_ID",
    "paritypay": "SHOP_ID SECRET_KEY",
    "platega": "MERCHANT_ID SECRET",
    "riopay": "API_TOKEN",
    "rollypay": "API_KEY",
    "severpay": "MID TOKEN",
    "tabpay": "API_KEY",
    "wata": "ACCESS_TOKEN",
    "yookassa": "SHOP_ID SECRET_KEY",
}
PAID = {
    "tribute": {"paid"},
    "etoplatezhi": {"success"},
    "yookassa": {"succeeded"},
    "cryptobot": {"paid"},
    "heleket": {"paid", "paid_over"},
    "anore": {"paid"},
    "antilopay": {"SUCCESS"},
    "aurapay": {"PAID"},
    "cashera": {"paid"},
    "cispay": {"PAID"},
    "cloudpayments": {"Completed"},
    "freekassa": {"1"},
    "kassa_ai": {"1"},
    "lava": {"success"},
    "mulenpay": {"3", "success"},
    "pal24": {"PAID", "SUCCESS", "OVERPAID"},
    "platega": {"CONFIRMED"},
    "wata": {"Paid"},
    "donut": {"success"},
    "jupiter": {"success"},
    "overpay": {"charged"},
    "paritypay": {"PAID"},
    "riopay": {"COMPLETED"},
    "rollypay": {"paid"},
    "severpay": {"success"},
    "tabpay": {"SUCCESS"},
}


def nodes(value):
    if isinstance(value, dict):
        yield value
        for key in (
            "data",
            "result",
            "Model",
            "invoice",
            "transaction",
            "bill",
            "payment",
            "orders",
            "items",
            "transactions",
            "paymentDetails",
        ):
            yield from nodes(value.get(key))
    elif isinstance(value, list) and len(value) == 1:
        yield from nodes(value[0])


def field(data, *names):
    for node in nodes(data):
        for name in names:
            if name in node and node[name] is not None:
                return node[name]
    return None


def available(config):
    values = config.values()
    return [
        name
        for name in sorted(HOSTED)
        if values.get(name.upper() + "_ENABLED")
        and values.get(name.upper() + "_CHECKOUT_RATE")
        and all(values.get(name.upper() + "_" + suffix) for suffix in REQUIRED[name].split())
        and (name not in WEBHOOK_ONLY or os.getenv("PAYMENT_WEBHOOK_PORT", "0") != "0")
    ]


class ExternalGateway:
    def __init__(self, store, name, *, email=None):
        if name not in HOSTED:
            raise ShopError("Этот провайдер использует отдельный протокол IAP/webhook.")
        self.store, self.name = store, name
        self.config = ProviderConfig(store)
        values = self.config.values()
        self.currency = str(values.get(name.upper() + "_CHECKOUT_CURRENCY") or "RUB").upper()
        self.rate = values.get(name.upper() + "_CHECKOUT_RATE")
        self.email = email
        with store.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS hosted_invoices (order_id TEXT PRIMARY KEY, provider TEXT NOT NULL, remote_id TEXT, document BLOB, state TEXT NOT NULL)"
            )

    def amount(self, xtr):
        try:
            rate = Decimal(str(self.rate))
            if not rate.is_finite() or rate <= 0:
                raise InvalidOperation
            amount = int((rate * xtr * 100).to_integral_value(rounding=ROUND_CEILING))
            if not 1 <= amount <= 10**12:
                raise InvalidOperation
        except (ValueError, InvalidOperation):
            raise ShopError(
                "Настройте положительный CHECKOUT_RATE провайдера (единиц валюты за XTR)."
            ) from None
        values = self.config.values()
        expected_currency = None
        if self.name in RUB_ONLY:
            expected_currency = "RUB"
        elif self.name in CONFIGURED_CURRENCY:
            expected_currency = str(values.get(self.name.upper() + "_CURRENCY") or "RUB").upper()
        if expected_currency and self.currency != expected_currency:
            raise ShopError(f"Для {self.name} установите валюту счёта {expected_currency}.")
        lower = values.get(self.name.upper() + "_MIN_AMOUNT_KOPEKS")
        upper = values.get(self.name.upper() + "_MAX_AMOUNT_KOPEKS")
        if (lower and amount < lower) or (upper and amount > upper):
            raise ShopError("Сумма вне лимитов этого провайдера.")
        return amount

    async def invoke(self, method, params):
        return await provider_call(self.config, self.name, method, params)

    def create_args(self, order):
        p, ident = self.name, order.id
        amount = order.price / 100
        description = f"{order.product.label} / {ident}"
        email = self.email
        if p == "etoplatezhi":
            values = self.config.values()
            return "build_payment_url", dict(
                project_id=values["ETOPLATEZHI_PROJECT_ID"],
                payment_id=ident,
                payment_amount=order.price,
                payment_currency=order.currency,
                customer_id=str(order.buyer),
                description=description,
                customer_email=email,
                callback_url=values["ETOPLATEZHI_CALLBACK_URL"],
            )
        if p == "yookassa":
            return "create_payment", dict(
                amount=amount,
                currency=order.currency,
                description=description,
                metadata={"order_id": ident},
                receipt_email=email,
            )
        if p == "cryptobot":
            return "create_invoice", dict(
                amount=f"{amount:.2f}", asset=order.currency, description=description, payload=ident
            )
        if p == "heleket":
            return "create_payment", dict(
                payload={"amount": f"{amount:.2f}", "currency": order.currency, "order_id": ident}
            )
        if p == "anore":
            return "create_payment", dict(
                amount_kopeks=order.price, description=description, order_id=ident, email=email
            )
        if p == "antilopay":
            return "create_payment", dict(
                amount_rubles=amount, order_id=ident, product_name=description, customer_email=email
            )
        if p == "aurapay":
            return "create_invoice", dict(amount=amount, order_id=ident, comment=description)
        if p == "cashera":
            return "create_transaction", dict(
                amount_kopeks=order.price,
                external_id=ident,
                description=description,
                payment_method=None,
            )
        if p == "cispay":
            return "create_payment", dict(
                amount_kopeks=order.price,
                order_id=ident,
                customer_id=str(order.buyer),
                description=description,
            )
        if p == "cloudpayments":
            return "generate_payment_link", dict(
                telegram_id=order.buyer,
                user_id=order.buyer,
                amount_kopeks=order.price,
                invoice_id=ident,
                description=description,
                email=email,
            )
        if p in {"donut", "jupiter"}:
            params = dict(
                amount_rubles=amount,
                order_id=ident,
                customer_id=str(order.buyer),
                customer_email=email,
            )
            if p == "donut":
                params["method_description"] = "Telegram gift"
            else:
                params["description"] = description
            return "create_payment", params
        if p in {"freekassa", "kassa_ai"}:
            return "create_order", dict(
                order_id=ident, amount=amount, currency=order.currency, email=email
            )
        if p == "lava":
            return "create_invoice", dict(amount_rubles=amount, order_id=ident, comment=description)
        if p == "mulenpay":
            return "create_payment", dict(
                amount_kopeks=order.price,
                description=description,
                uuid=ident,
                items=[{"description": description, "quantity": 1, "price": f"{amount:.2f}"}],
                client=email,
            )
        if p == "overpay":
            return "create_payment", dict(
                amount=f"{amount:.2f}",
                currency=order.currency,
                merchant_transaction_id=ident,
                description=description,
            )
        if p == "pal24":
            return "create_bill", dict(
                amount_kopeks=order.price,
                user_id=order.buyer,
                order_id=ident,
                description=description,
                payer_email=email,
            )
        if p == "paritypay":
            return "create_invoice", dict(
                order_id=ident, amount_kopeks=order.price, comment=description
            )
        if p == "platega":
            return "create_payment", dict(
                payer={
                    "user_id": str(order.buyer),
                    "user_name": str(order.buyer),
                    "contact": email,
                    "platega_email": email,
                },
                payment_method=2,
                amount=amount,
                currency=order.currency,
                description=description,
                payload=ident,
            )
        if p == "riopay":
            return "create_order", dict(amount=amount, external_id=ident, purpose=description)
        if p == "rollypay":
            return "create_payment", dict(
                amount_value=f"{amount:.2f}",
                currency=order.currency,
                order_id=ident,
                description=description,
                customer_id=str(order.buyer),
            )
        if p == "severpay":
            return "create_payment", dict(
                order_id=ident,
                amount=amount,
                currency=order.currency,
                client_email=email,
                client_id=str(order.buyer),
            )
        if p == "tabpay":
            return "create_payment", dict(
                order_id=ident,
                amount_kopecks=order.price,
                description=description,
                email=email,
                telegram_id=order.buyer,
            )
        if p == "wata":
            return "create_payment_link", dict(
                amount_kopeks=order.price,
                currency=order.currency,
                description=description,
                order_id=ident,
            )
        raise ShopError("Не определён метод создания инвойса.")

    async def checkout(self, order):
        if order.provider != self.name or order.currency != self.currency:
            raise ShopError("Провайдер или валюта заказа изменились.")
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM hosted_invoices WHERE order_id=?", (order.id,)
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO hosted_invoices VALUES (?,?,NULL,NULL,'creating')",
                    (order.id, self.name),
                )
        if row is None:
            from .payments import Checkout

            if not Checkout(self.store, self).authorize(
                order.id, order.buyer, order.price, order.currency, "hosted-" + order.id
            ):
                raise ShopError("Этот счёт недоступен.")
            try:
                method, params = self.create_args(order)
                result = await self.invoke(method, params)
                url = (
                    result
                    if isinstance(result, str)
                    else field(
                        result,
                        "payment_url",
                        "paymentUrl",
                        "redirectUrl",
                        "redirect_url",
                        "redirect",
                        "url",
                        "link",
                        "bot_invoice_url",
                        "Link",
                        "confirmation_url",
                        "location",
                    )
                )
                if not url and isinstance(result, dict):
                    url = field(result.get("confirmation"), "confirmation_url")
                ident = (
                    field(
                        result,
                        "id",
                        "uuid",
                        "invoice_id",
                        "bill_id",
                        "transaction_id",
                        "transactionId",
                        "Id",
                        "orderId",
                        "paymentId",
                    )
                    or order.id
                )
                document = self.config.cipher.encrypt(
                    json.dumps({"url": url, "response": result}, default=str).encode()
                )
                with self.store.connection() as db:
                    db.execute(
                        "UPDATE hosted_invoices SET remote_id=?,document=?,state='created' WHERE order_id=?",
                        (str(ident), document, order.id),
                    )
            except Exception:
                raise ShopError(
                    "Исход создания счёта требует сверки в кабинете провайдера. Не повторяйте создание до проверки."
                ) from None
        return self.saved_link(order)

    def saved_link(self, order):
        with self.store.connection() as db:
            row = db.execute(
                "SELECT document FROM hosted_invoices WHERE order_id=? AND provider=?",
                (order.id, self.name),
            ).fetchone()
        if not row or not row["document"]:
            raise ShopError("Создание счёта требует сверки администратором.")
        url = json.loads(self.config.cipher.decrypt(row["document"]))["url"]
        parsed = urlsplit(url) if isinstance(url, str) else None
        if (
            not parsed
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ShopError(
                "Провайдер не вернул безопасную HTTPS-ссылку. Проверьте счёт в кабинете."
            )
        return url

    async def remote(self, order, ident):
        p = self.name
        if p == "yookassa":
            return await self.invoke("get_payment_info", {"payment_id_in_yookassa": ident})
        if p == "cryptobot":
            return await self.invoke("get_invoices", {"invoice_ids": [ident]})
        if p == "heleket":
            return await self.invoke("get_payment_info", {"uuid": ident})
        if p == "antilopay":
            return await self.invoke("check_payment", {"order_id": order.id})
        if p in {"aurapay", "lava"}:
            return await self.invoke("get_invoice_status", {"order_id": order.id})
        if p == "paritypay":
            return await self.invoke("get_invoice", {"order_id": order.id})
        if p == "cashera":
            return await self.invoke("get_transaction", {"transaction_uuid": ident})
        if p == "cispay":
            return await self.invoke("check_payment", {"payment_id": ident, "order_id": order.id})
        if p == "cloudpayments":
            return await self.invoke("find_payment", {"invoice_id": order.id})
        if p in {"jupiter", "donut"}:
            return await self.invoke("check_payment", {"transaction_id": ident})
        if p in {"freekassa", "kassa_ai"}:
            return await self.invoke("get_order_status", {"order_id": order.id})
        if p == "pal24":
            return await self.invoke("get_bill_status", {"bill_id": ident})
        if p == "platega":
            return await self.invoke("get_transaction", {"transaction_id": ident})
        if p == "riopay":
            return await self.invoke("get_order", {"order_id": ident})
        if p == "wata":
            return await self.invoke(
                "search_transactions",
                {"order_id": order.id, "payment_link_id": ident, "status": "Paid"},
            )
        return await self.invoke(
            "get_payment",
            {
                "payment_id" if p != "overpay" else "order_id": int(ident)
                if p == "mulenpay"
                else ident
            },
        )

    async def check(self, order):
        with self.store.connection() as db:
            row = db.execute(
                "SELECT * FROM hosted_invoices WHERE order_id=? AND provider=?",
                (order.id, self.name),
            ).fetchone()
        if row is None or not row["remote_id"]:
            raise ShopError("Инвойс требует сверки в кабинете провайдера.")
        if self.name in WEBHOOK_ONLY:
            return False
        try:
            remote = await self.remote(order, row["remote_id"])
        except ShopError:
            raise
        except Exception:
            raise ShopError("Статус недоступен. Проверьте счёт в кабинете провайдера.") from None
        status = field(remote, "status", "Status", "payment_status")
        if isinstance(status, dict):
            status = status.get("name") or status.get("code")
        accepted = PAID.get(self.name, set())
        if str(status) not in accepted:
            return False
        ident = field(
            remote,
            "id",
            "uuid",
            "invoice_id",
            "bill_id",
            "transaction_id",
            "transactionId",
            "Id",
            "orderId",
            "paymentId",
        )
        charge_id = ident
        if self.name == "cloudpayments":
            ident = field(remote, "InvoiceId", "invoiceId")
            charge_id = field(remote, "TransactionId", "transactionId")
        if self.name == "wata":
            ident = field(remote, "paymentLinkId", "payment_link_id")
        amount = field(remote, "amount", "Amount", "paymentAmount", "amount_value")
        currency = field(remote, "currency", "Currency", "asset", "amount_currency")
        if isinstance(amount, dict):
            currency = amount.get("currency", currency)
            amount = amount.get("value")
        try:
            decimal = Decimal(str(amount)) * (1 if self.name == "cispay" else 100)
            valid = decimal.is_finite() and decimal == order.price
        except InvalidOperation:
            valid = False
        if (
            str(ident) != row["remote_id"]
            or not charge_id
            or not valid
            or currency != order.currency
        ):
            raise ShopError(
                "Провайдер не вернул однозначное подтверждение ID, суммы и валюты. Нужна ручная сверка."
            )
        from .payments import Checkout, PaymentReceipt

        return Checkout(self.store, self).capture(
            PaymentReceipt(order.id, order.buyer, order.price, order.currency, str(charge_id))
        )

    async def refund(self, buyer, charge_id):
        raise ShopError(
            "Возврат этого провайдера выполняется в его кабинете с проверкой исходного платежа; автоматический возврат не заявлен."
        )
