import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from FragmentAPI.types.constants import (
    GRAM_TOPUP_MAX,
    GRAM_TOPUP_MIN,
    STARS_PURCHASE_MAX,
    STARS_PURCHASE_MIN,
)

from .config import ShopError

PRODUCTS = {"stars": "Telegram Stars", "premium": "Telegram Premium", "ton": "GRAM · Telegram Ads"}
HOME_BUTTONS = ("orders", "support", "terms", "faq", "privacy")
SETTINGS = {
    "name": ("Fragment Shop", 64),
    "welcome": ("Выберите товар в каталоге.", 1000),
    "tagline": ("", 140),
    "buy_label": ("Купить", 40),
    "home_buttons": (",".join(HOME_BUTTONS), 100),
    "home_style": ("cover", 8),
    "premium_icons": ("inherit", 12),
    "show_recent_order": ("1", 1),
    "banner_url": ("", 1000),
    "catalog_title": ("Каталог", 64),
    "category_order": (",".join(PRODUCTS), 100),
    "closed_text": ("", 1000),
    "allow_self": ("1", 1),
    "allow_gifts": ("1", 1),
    "show_sender": ("0", 1),
    "checkout_minutes": ("30", 3),
    "support_title": ("Поддержка", 64),
    "support": ("", 500),
    "support_url": ("", 500),
    "terms": ("", 1500),
    "privacy": ("", 1500),
    "faq": ("", 1800),
    "open": ("0", 1),
}

SETTING_GROUPS = {
    "branding": (
        "🎨 Оформление",
        (
            "name",
            "tagline",
            "welcome",
            "buy_label",
            "home_style",
            "home_buttons",
            "banner_url",
            "premium_icons",
            "show_recent_order",
        ),
    ),
    "catalog": ("📦 Витрина", ("open", "catalog_title", "category_order", "closed_text")),
    "checkout": (
        "💳 Оформление заказа",
        ("allow_self", "allow_gifts", "checkout_minutes", "show_sender"),
    ),
    "support": (
        "💬 Помощь и документы",
        ("support_title", "support", "support_url", "terms", "faq", "privacy"),
    ),
}

SETTING_LABELS = {
    "name": "Название магазина",
    "welcome": "Приветствие",
    "tagline": "Подзаголовок",
    "buy_label": "Текст кнопки покупки",
    "home_buttons": "Кнопки главного меню",
    "home_style": "Главный экран",
    "premium_icons": "Стиль иконок",
    "show_recent_order": "Последний заказ в меню",
    "banner_url": "Ссылка на обложку",
    "catalog_title": "Название каталога",
    "category_order": "Порядок категорий",
    "closed_text": "Текст закрытой витрины",
    "allow_self": "Кнопка «Отправить себе»",
    "allow_gifts": "Покупка для другого человека",
    "show_sender": "Показывать отправителя подарка",
    "checkout_minutes": "Срок оплаты в минутах",
    "support_title": "Название раздела помощи",
    "support": "Текст поддержки",
    "support_url": "Ссылка на поддержку",
    "terms": "Условия покупки",
    "privacy": "Конфиденциальность",
    "faq": "Вопросы и ответы",
    "open": "Приём заказов",
}

SETTING_OPTIONS = {
    **{
        key: {"1": "Включено", "0": "Выключено"}
        for key in ("show_recent_order", "allow_self", "allow_gifts", "show_sender")
    },
    "open": {"1": "Открыто", "0": "Закрыто"},
    "home_style": {"cover": "Обложка", "rich": "Rich Message", "text": "Текст"},
    "premium_icons": {
        "inherit": "Настройки запуска",
        "premium": "Премиум с заменой",
        "premium_only": "Только премиум",
        "unicode": "Обычные эмодзи",
        "off": "Без иконок",
    },
}

SETTING_HELP = {
    "buy_label": "Этот текст будет на главной кнопке витрины.",
    "home_buttons": (
        "Перечислите кнопки в нужном порядке через запятую: orders, support, terms, faq, privacy. "
        "Пустой список скрывает дополнительные кнопки."
    ),
    "home_style": "Обложка использует картинку, Rich Message использует расширенное оформление Telegram.",
    "premium_icons": "В режиме «Только премиум» запасной вариант использует кнопки без иконок.",
    "banner_url": "Укажите прямую HTTPS-ссылку на публичное изображение.",
    "category_order": "Перечислите все три категории через запятую: stars, premium, ton.",
    "allow_self": "Показывать кнопку быстрой подстановки username покупателя.",
    "allow_gifts": "Получателем можно указать другой публичный Telegram-аккаунт.",
    "show_sender": "Fragment может показать отправителя при выдаче подарка.",
    "checkout_minutes": "От 5 до 120 минут с момента создания заказа.",
    "support_url": "Укажите HTTPS-ссылку или Telegram-ссылку на чат, пользователя или приглашение.",
}

DEMO_SETTINGS = {
    "name": "Fragment Shop · Демо",
    "tagline": "Stars, Premium и GRAM",
    "welcome": "Выберите товар в каталоге и пройдите тестовую покупку.",
    "support": (
        "Это тестовый магазин. Если возникла ошибка, сообщите администратору теста "
        "номер заказа, выбранный товар и статус из раздела «Мои заказы»."
    ),
    "terms": (
        "Оплата и выдача выполняются в тестовом режиме. Цены и суммы служат для проверки сценария. "
        "Укажите публичный Telegram-аккаунт получателя и проверьте его перед оформлением."
    ),
    "privacy": (
        "Тестовый магазин хранит Telegram ID покупателя, username получателя, товар и статус заказа. "
        "Эти сведения используются для показа истории и разбора ошибок."
    ),
    "faq": (
        "Как оформить заказ? Выберите товар, укажите получателя и откройте тестовый счёт.\n\n"
        "Где проверить статус? Откройте «Мои заказы» и выберите нужный заказ.\n\n"
        "Куда сообщить об ошибке? Откройте «Поддержку» и скопируйте номер заказа."
    ),
    "closed_text": "Тестовая витрина на паузе. Загляните позже или откройте раздел поддержки.",
    "premium_icons": "premium_only",
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
        raise ShopError("Неизвестная настройка.")
    default, limit = SETTINGS[key]
    value = value.strip()
    if (not value and default and key != "home_buttons") or len(value) > limit:
        raise ShopError(f"Проверьте значение настройки «{SETTING_LABELS[key]}» (до {limit} симв.).")
    if key in SETTING_OPTIONS and value not in SETTING_OPTIONS[key]:
        raise ShopError(f"Допустимые значения: {', '.join(SETTING_OPTIONS[key])}.")
    if key == "checkout_minutes":
        if not re.fullmatch(r"[0-9]{1,3}", value) or not 5 <= int(value) <= 120:
            raise ShopError("Укажите целое число от 5 до 120 минут.")
        return str(int(value))
    if key in {"category_order", "home_buttons"}:
        entries = [entry.strip().lower() for entry in value.split(",")] if value else []
        allowed = PRODUCTS if key == "category_order" else HOME_BUTTONS
        if (
            len(entries) != len(set(entries))
            or any(entry not in allowed for entry in entries)
            or (key == "category_order" and len(entries) != len(allowed))
        ):
            raise ShopError(SETTING_HELP[key])
        return ",".join(entries)
    if value and key in {"support_url", "banner_url"}:
        _validate_setting_url(key, value)
    return value


def _validate_setting_url(key: str, value: str) -> None:
    try:
        url = urlsplit(value)
        safe_chars = not any(char.isspace() or ord(char) < 32 or char == "\\" for char in value)
        if not (safe_chars and url.hostname and url.username is None and url.password is None):
            raise ValueError
        if url.scheme == "https" and url.port != 0:
            return
        if key == "support_url" and url.scheme == "tg" and url.port is None and not url.path:
            parameter, pattern = {
                "resolve": ("domain", r"[A-Za-z][A-Za-z0-9_]{2,31}"),
                "user": ("id", r"[1-9][0-9]{0,15}"),
                "join": ("invite", r"[A-Za-z0-9_-]{1,100}"),
            }.get(url.hostname, ("", ""))
            targets = parse_qs(url.query).get(parameter, [])
            if parameter and len(targets) == 1 and re.fullmatch(pattern, targets[0]):
                return
    except ValueError:
        pass
    raise ShopError(SETTING_HELP[key])


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
