import json
import math
import os
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal, InvalidOperation
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import ShopError

MANIFEST = json.loads(Path(__file__).with_name("provider_manifest.json").read_text("utf-8"))
PROVIDERS = MANIFEST["providers"]
FIELDS = MANIFEST["fields"]
_current = ContextVar("payment_provider_settings", default=None)


def provider_fields(provider):
    if provider not in PROVIDERS:
        raise ShopError("Неизвестный провайдер.")
    return sorted(key for key in FIELDS if key.startswith(provider.upper() + "_"))


def coerce(key, value):
    if key not in FIELDS:
        raise ShopError("Неизвестный параметр провайдера.")
    if value is None or value == "":
        return None if FIELDS[key]["default"] is None else FIELDS[key]["default"]
    if key.endswith("_CHECKOUT_RATE"):
        try:
            rate = Decimal(str(value))
            if not rate.is_finite() or not 0 < rate <= 1000000 or rate.as_tuple().exponent < -6:
                raise InvalidOperation
            return format(rate, "f")
        except (ValueError, InvalidOperation):
            raise ShopError(
                "Курс: положительное число до 1000000, до 6 знаков после точки."
            ) from None
    if key.endswith("_CHECKOUT_CURRENCY"):
        value = str(value).strip().upper()
        if not value.isascii() or not value.isalpha() or not 3 <= len(value) <= 10:
            raise ShopError("Укажите код валюты (например RUB, USD, USDT).")
        return value
    kind = FIELDS[key]["type"]
    try:
        if kind == "bool":
            if str(value).lower() not in {"true", "false", "1", "0"}:
                raise ValueError
            return str(value).lower() in {"true", "1"}
        if kind in {"int", "float"}:
            result = int(value) if kind == "int" else float(value)
            if not math.isfinite(result) or result < 0 or result > 10**15:
                raise ValueError
            return result
        result = str(value).strip()
        if len(result) > 32000 or "\x00" in result:
            raise ValueError
        return result
    except (ValueError, OverflowError, TypeError):
        raise ShopError(f"Неверный формат {key}: ожидается {kind}.") from None


class ProviderConfig:
    def __init__(self, store):
        self.store = store
        self.key_path = store.path.parent / "provider-settings.key"
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS provider_settings (key TEXT PRIMARY KEY, value BLOB NOT NULL)"
            )
            try:
                if not self.key_path.exists():
                    protected = ("provider_settings", "hosted_invoices", "tribute_events")
                    existing = {
                        row[0]
                        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    }
                    if any(
                        db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                        for table in protected
                        if table in existing
                    ):
                        raise ValueError
                    fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as file:
                        file.write(Fernet.generate_key())
                self.cipher = Fernet(self.key_path.read_bytes())
            except (OSError, ValueError):
                raise ShopError(
                    "Проверьте provider-settings.key или восстановите ключ из резервной копии."
                ) from None

    def values(self):
        values = {key: spec["default"] for key, spec in FIELDS.items()}
        for key in FIELDS:
            if key in os.environ:
                values[key] = coerce(key, os.environ[key])
        with self.store.connection() as db:
            rows = db.execute("SELECT key,value FROM provider_settings").fetchall()
        for key, value in rows:
            try:
                values[key] = json.loads(self.cipher.decrypt(value))
            except InvalidToken:
                raise ShopError(
                    "Не удалось расшифровать настройки. Восстановите provider-settings.key из резервной копии."
                ) from None
        return values

    def set(self, key, value):
        parsed = coerce(key, value)
        encrypted = self.cipher.encrypt(json.dumps(parsed).encode())
        with self.store.connection() as db:
            db.execute(
                "INSERT INTO provider_settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, encrypted),
            )

    def reset(self, key):
        if key not in FIELDS:
            raise ShopError("Неизвестный параметр.")
        with self.store.connection() as db:
            db.execute("DELETE FROM provider_settings WHERE key=?", (key,))

    def display(self, provider):
        values = self.values()
        return {
            key: ("••••••" if values[key] else "не задано")
            if FIELDS[key]["secret"]
            else values[key]
            for key in provider_fields(provider)
        }

    @contextmanager
    def bind(self):
        token = _current.set(self.values())
        try:
            yield
        finally:
            _current.reset(token)


class SettingsView:
    def __getattr__(self, key):
        values = _current.get()
        if values is None:
            # Import-time SDK timeout configuration uses safe defaults only.
            if key in FIELDS:
                return FIELDS[key]["default"]
            raise AttributeError(key)
        if key in values:
            return values[key]
        if key.startswith("is_") and key.endswith("_enabled"):
            provider = key[3:-8].upper()
            return lambda: bool(values.get(provider + "_ENABLED", False))
        raise AttributeError(key)

    def get_cryptobot_base_url(self):
        return (
            "https://testnet-pay.crypt.bot" if self.CRYPTOBOT_TESTNET else self.CRYPTOBOT_BASE_URL
        )

    def get_anore_methods(self):
        return [
            m.strip()
            for m in self.ANORE_PAYMENT_METHODS.split(",")
            if m.strip() in {"sbp", "card", "crypto", "yoomoney"}
        ]

    def get_apple_iap_environment(self):
        return "Sandbox" if self.APPLE_IAP_ENVIRONMENT == "Sandbox" else "Production"

    def get_apple_iap_root_cert_paths(self):
        return [
            Path(p.strip()) for p in (self.APPLE_IAP_ROOT_CERTS_PATHS or "").split(",") if p.strip()
        ]

    def get_apple_iap_private_key(self):
        if self.APPLE_IAP_PRIVATE_KEY:
            return self.APPLE_IAP_PRIVATE_KEY
        return (
            Path(self.APPLE_IAP_PRIVATE_KEY_PATH).read_text().strip()
            if self.APPLE_IAP_PRIVATE_KEY_PATH
            else None
        )


settings = SettingsView()
