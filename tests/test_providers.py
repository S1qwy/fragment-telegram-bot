import asyncio
import hashlib
import hmac
import importlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Update
from test_bot import ADMIN, BUYER, chat_message

from fragment_bot.config import Config, ShopError
from fragment_bot.provider_config import FIELDS, PROVIDERS, ProviderConfig, settings
from fragment_bot.provider_ui import provider_router
from fragment_bot.providers import provider_call, provider_client
from fragment_bot.store import Store


def configuration(tmp_path):
    return ProviderConfig(Store(tmp_path, "demo"))


def test_inventory_has_every_upstream_gateway():
    expected = set(
        "telegram_stars tribute yookassa cryptobot heleket mulenpay pal24 wata platega cloudpayments freekassa kassa_ai riopay severpay apple_iap rollypay overpay aurapay etoplatezhi antilopay jupiter cispay tabpay anore paritypay donut lava cashera".split()
    )
    assert set(PROVIDERS) == expected
    source = Path("fragment_bot/vendor/bedolaga")
    assert "Commons Clause" in (source / "LICENSE").read_text("utf-8")
    assert len(json.loads((source / "SOURCES.json").read_text("utf-8"))["files"]) == 28


@pytest.mark.parametrize("provider", [p for p in PROVIDERS if p != "telegram_stars"])
async def test_every_client_imports_without_vpn_application(provider, tmp_path):
    config = configuration(tmp_path)
    spec = PROVIDERS[provider]
    with config.bind():
        module = importlib.import_module("fragment_bot.vendor.bedolaga." + spec["module"])
        cls = getattr(module, spec["class"])
        for method in spec["methods"]:
            assert callable(getattr(cls, method))
        if provider != "apple_iap":
            async with provider_client(config, provider):
                pass


def test_encrypted_settings_mask_reset_and_env_precedence(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    monkeypatch.setenv("CRYPTOBOT_API_TOKEN", "environment-token")
    assert config.values()["CRYPTOBOT_API_TOKEN"] == "environment-token"
    config.set("CRYPTOBOT_API_TOKEN", "private-merchant-secret")
    assert "private-merchant-secret" not in json.dumps(config.display("cryptobot"))
    assert b"private-merchant-secret" not in config.store.path.read_bytes()
    assert ProviderConfig(config.store).values()["CRYPTOBOT_API_TOKEN"] == "private-merchant-secret"
    config.reset("CRYPTOBOT_API_TOKEN")
    assert config.values()["CRYPTOBOT_API_TOKEN"] == "environment-token"
    with pytest.raises(ShopError):
        config.set("CRYPTOBOT_ENABLED", "maybe")


async def test_settings_are_isolated_between_concurrent_shops(tmp_path):
    first = configuration(tmp_path / "first")
    second = configuration(tmp_path / "second")
    first.set("CRYPTOBOT_API_TOKEN", "first")
    second.set("CRYPTOBOT_API_TOKEN", "second")

    async def read(config):
        with config.bind():
            await asyncio.sleep(0)
            return settings.CRYPTOBOT_API_TOKEN

    assert await asyncio.gather(read(first), read(second)) == ["first", "second"]


@pytest.mark.parametrize(
    "provider,field",
    [
        ("cryptobot", "CRYPTOBOT_API_TOKEN"),
        ("aurapay", "AURAPAY_SECRET_KEY"),
        ("rollypay", "ROLLYPAY_SIGNING_SECRET"),
    ],
)
async def test_real_signature_verifiers_reject_tampering(tmp_path, provider, field):
    config = configuration(tmp_path)
    config.set(field, "signing-secret")
    async with provider_client(config, provider) as client:
        if provider == "cryptobot":
            body = '{"id":123,"amount":"10"}'
            secret = hashlib.sha256(b"signing-secret").digest()
            signature = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
            assert client.verify_webhook_signature(body, signature)
            assert not client.verify_webhook_signature(body.replace('"10"', '"99"'), signature)
        elif provider == "aurapay":
            data = {"amount": "10", "order_id": "abc"}
            signature = hmac.new(b"signing-secret", b"10abc", hashlib.sha256).hexdigest()
            assert client.verify_webhook_signature(data, signature)
            assert not client.verify_webhook_signature({**data, "amount": "99"}, signature)
        else:
            body = b'{"amount":"10"}'
            stamp = "1234"
            signature = hmac.new(
                b"signing-secret", stamp.encode() + b"." + body, hashlib.sha256
            ).hexdigest()
            assert client.verify_webhook_signature(body, signature, stamp)
            assert not client.verify_webhook_signature(b'{"amount":"99"}', signature, stamp)


async def test_cryptobot_creation_builds_real_api_request(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    config.set("CRYPTOBOT_API_TOKEN", "merchant-token")
    config.set("CRYPTOBOT_ENABLED", True)
    module = importlib.import_module("fragment_bot.vendor.bedolaga.cryptobot")
    request = AsyncMock(return_value={"invoice_id": 123, "status": "active"})
    monkeypatch.setattr(module.CryptoBotService, "_make_request", request)
    result = await provider_call(
        config,
        "cryptobot",
        "create_invoice",
        {"amount": "10.00", "asset": "USDT", "payload": "order-123"},
    )
    assert result["invoice_id"] == 123
    assert "createInvoice" in str(request.await_args)
    assert "order-123" in str(request.await_args)
    with pytest.raises(ShopError):
        await provider_call(config, "cryptobot", "_make_request", {})
    with pytest.raises(ShopError, match="Tribute"):
        await provider_call(config, "tribute", "refund_payment", {"payment_id": "123"})


async def test_provider_settings_authorization_and_secret_edit(tmp_path):
    config = Config("demo", tmp_path, token="123:dummy", admin_ids=frozenset({ADMIN.id}))
    store = Store(tmp_path, "demo")
    async with Bot(config.token) as bot:
        bot.session.make_request = AsyncMock(return_value=True)
        dispatcher = Dispatcher()
        dispatcher.include_router(provider_router(config, store))

        async def callback(data, user=ADMIN):
            await dispatcher.feed_update(
                bot,
                Update(
                    update_id=1,
                    callback_query=CallbackQuery(
                        id="q",
                        from_user=user,
                        chat_instance="c",
                        data=data,
                        message=chat_message(user=user),
                    ),
                ),
            )

        try:
            with pytest.raises(ShopError):
                await callback("gateways:0", BUYER)
            await callback("gateway:cryptobot:0")
            await callback(f"gwfield:{sorted(FIELDS).index('CRYPTOBOT_API_TOKEN')}")
            await dispatcher.feed_update(
                bot, Update(update_id=2, message=chat_message("new-api-secret", user=ADMIN))
            )
            assert ProviderConfig(store).values()["CRYPTOBOT_API_TOKEN"] == "new-api-secret"
            assert all(
                "new-api-secret" not in str(c.args[1])
                for c in bot.session.make_request.await_args_list
            )
        finally:
            await dispatcher.fsm.close()
