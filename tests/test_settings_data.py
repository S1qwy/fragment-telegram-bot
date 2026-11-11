import pytest

from fragment_bot.catalog import DEMO_PRODUCTS, DEMO_SETTINGS, SETTINGS, setting_value
from fragment_bot.config import ShopError
from fragment_bot.payments import Checkout, DemoGateway
from fragment_bot.store import Store


def test_existing_store_fills_missing_settings_and_preserves_custom_values(tmp_path):
    old = Store(tmp_path, "demo")
    old.configure("name", "Ночной магазин")
    with old.connection() as db:
        db.execute(
            "DELETE FROM settings WHERE key NOT IN ('name','welcome','support','terms','open')"
        )

    migrated = Store(tmp_path, "demo")
    values = migrated.settings()
    assert set(values) == set(SETTINGS)
    assert values["name"] == "Ночной магазин"
    assert values["open"] == "1"
    assert values["checkout_minutes"] == "30"
    assert values["premium_icons"] == "inherit"


def test_demo_preset_respects_edited_settings_and_reports_changes(tmp_path):
    store = Store(tmp_path, "demo")
    store.configure("name", "Ночной магазин")
    changed = store.apply_demo_settings()
    assert set(changed) == DEMO_SETTINGS.keys() - {"name"}
    assert store.settings()["name"] == "Ночной магазин"
    assert store.settings()["premium_icons"] == "premium_only"
    assert store.apply_demo_settings() == ()
    assert store.apply_demo_settings(overwrite=True) == ("name",)
    store.reset_setting("faq")
    assert store.settings()["faq"] == ""


def test_live_store_keeps_required_texts_while_open(tmp_path):
    store = Store(tmp_path, "live")
    with pytest.raises(ShopError, match="demo и test"):
        store.apply_demo_settings(overwrite=True)
    store.list_product(DEMO_PRODUCTS[0])
    store.configure("support", "Поддержка")
    store.configure("terms", "Условия")
    store.configure("open", "1")
    with pytest.raises(ShopError, match="Закройте витрину"):
        store.reset_setting("terms")
    assert store.settings()["terms"] == "Условия"
    store.configure("open", "0")
    store.reset_setting("terms")
    assert store.settings()["terms"] == ""
    with pytest.raises(ShopError, match="укажите terms"):
        store.configure("open", "1")


def test_checkout_uses_configured_invoice_lifetime(tmp_path):
    store = Store(tmp_path, "demo")
    checkout = Checkout(store, DemoGateway())
    order = checkout.create(42, DEMO_PRODUCTS[0], "sample", "")
    with store.connection() as db:
        db.execute(
            "UPDATE orders SET created_at=datetime('now', '-10 minutes') WHERE id=?", (order.id,)
        )
    store.configure("checkout_minutes", "5")
    assert DemoGateway.pay(checkout, order.id, order.buyer).state == "unpaid"
    store.configure("checkout_minutes", "15")
    assert DemoGateway.pay(checkout, order.id, order.buyer).state == "paid"


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("category_order", " premium, stars, ton ", "premium,stars,ton"),
        ("home_buttons", " faq,orders ", "faq,orders"),
        ("home_buttons", "", ""),
        ("checkout_minutes", "005", "5"),
        ("support_url", "https://t.me/shop_support", "https://t.me/shop_support"),
        ("support_url", "tg://resolve?domain=shop_support", "tg://resolve?domain=shop_support"),
        ("banner_url", "https://example.com/cover.jpg", "https://example.com/cover.jpg"),
    ],
)
def test_settings_accept_and_normalize_supported_values(key, value, expected):
    assert setting_value(key, value) == expected


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("category_order", "premium,stars,stars"),
        ("home_buttons", "terms,terms"),
        ("checkout_minutes", "121"),
        ("checkout_minutes", "4"),
        ("show_sender", "yes"),
        ("home_style", "unknown"),
        ("premium_icons", "unknown"),
        ("support_url", "tg://settings"),
        ("support_url", "tg://resolve"),
        ("support_url", "https://user@example.com/"),
        ("banner_url", "tg://resolve?domain=example"),
        ("banner_url", "http://example.com/cover.jpg"),
    ],
)
def test_settings_reject_invalid_values(key, value):
    with pytest.raises(ShopError):
        setting_value(key, value)
