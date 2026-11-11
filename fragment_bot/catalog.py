import re
from dataclasses import dataclass

from FragmentAPI.types.constants import (
    GRAM_TOPUP_MAX,
    GRAM_TOPUP_MIN,
    STARS_PURCHASE_MAX,
    STARS_PURCHASE_MIN,
)

from .config import ShopError

PRODUCTS = {"stars": "Telegram Stars", "premium": "Telegram Premium", "ton": "GRAM · Telegram Ads"}
SETTINGS = {
    "name": ("Fragment Shop", 64),
    "welcome": ("Выберите товар в каталоге.", 1000),
    "support": ("", 500),
    "terms": ("", 1500),
    "open": ("0", 1),
}


def number(value: str, label: str) -> int:
    if not re.fullmatch(r"[0-9]{1,10}", value):
        raise ShopError(f"{label} — целое положительное число.")
    return int(value)


def username(value: str) -> str:
    value = value.strip().removeprefix("@")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,31}", value):
        raise ShopError("Введите публичный @username получателя.")
    return value.lower()


def setting_value(key: str, value: str) -> str:
    if key not in SETTINGS:
        raise ShopError("Доступные настройки: name, welcome, support, terms, open.")
    _, limit = SETTINGS[key]
    value = value.strip()
    if not value or len(value) > limit or (key == "open" and value not in {"0", "1"}):
        raise ShopError(f"Проверьте значение {key} (до {limit} симв.).")
    return value


@dataclass(frozen=True)
class Product:
    kind: str
    quantity: int
    price: int

    def __post_init__(self) -> None:
        if self.kind == "premium":
            valid = self.quantity in {3, 6, 12}
        elif self.kind == "stars":
            valid = STARS_PURCHASE_MIN <= self.quantity <= STARS_PURCHASE_MAX
        elif self.kind == "ton":
            valid = GRAM_TOPUP_MIN <= self.quantity <= GRAM_TOPUP_MAX
        else:
            raise ShopError("Товар должен быть stars, premium или ton.")
        if not valid:
            raise ShopError("Количество выходит за пределы SDK. Для Premium доступны 3, 6, 12 мес.")
        if not 1 <= self.price <= 10_000:
            raise ShopError("Цена должна составлять от 1 до 10 000 XTR.")

    @property
    def label(self) -> str:
        units = {"stars": "⭐", "premium": "мес.", "ton": "GRAM"}
        return f"{PRODUCTS[self.kind]} · {self.quantity} {units[self.kind]}"


DEMO_PRODUCTS = (Product("stars", 50, 100), Product("premium", 3, 950), Product("ton", 1, 350))
