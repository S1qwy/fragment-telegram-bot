import json
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, cast

from dotenv import load_dotenv

Mode = Literal["demo", "test", "live"]
DEFAULT_BANNER = Path(__file__).with_name("assets") / "shop-cover.jpg"


class ShopError(ValueError):
    """An actionable error safe to display in the bot or terminal."""


@dataclass(frozen=True)
class Config:
    mode: Mode
    data_dir: Path
    admin_ids: frozenset[int] = frozenset()
    token: str = field(default="", repr=False)
    cookies: str = field(default="", repr=False)
    seed: str = field(default="", repr=False)
    api_key: str = field(default="", repr=False)
    sender_account: dict[str, str] | None = None
    api_provider: str = "tonapi"
    wallet_version: str = "V5R1"
    mnemonic_type: str | None = None
    max_nanoton: int = 10_000_000_000
    telegram_proxy: str | None = field(default=None, repr=False)
    fragment_proxy: str | None = field(default=None, repr=False)
    banner_path: Path | None = DEFAULT_BANNER
    premium_icons: bool = True
    rich_menus: bool = True

    @property
    def bot_id(self) -> int:
        if not re.fullmatch(r"[1-9][0-9]*:[A-Za-z0-9_-]+", self.token):
            raise ShopError("Укажите BOT_TOKEN в .env.")
        return int(self.token.split(":", 1)[0])

    def require_wallet(self) -> None:
        if not all((self.cookies, self.seed, self.api_key)):
            raise ShopError(
                "Для выдачи Premium и GRAM заполните FRAGMENT_COOKIES, TON_SEED и TON_API_KEY."
            )

    @classmethod
    def load(cls) -> "Config":
        load_dotenv(".env")
        mode = os.getenv("SHOP_MODE", "demo").strip()
        if mode not in {"demo", "test", "live"}:
            raise ShopError("SHOP_MODE должен быть demo, test или live.")
        raw_ids = os.getenv("ADMIN_IDS", "").replace(",", " ").split()
        if any(not re.fullmatch(r"[1-9][0-9]{0,18}", value) for value in raw_ids):
            raise ShopError("ADMIN_IDS должен содержать числовые Telegram ID.")
        try:
            limit = Decimal(os.getenv("FRAGMENT_MAX_TON", "10"))
            if not limit.is_finite() or not 0 < limit <= 1_000_000_000:
                raise InvalidOperation
            nanoton = limit * 10**9
            if nanoton != nanoton.to_integral_value():
                raise InvalidOperation
        except InvalidOperation:
            raise ShopError("FRAGMENT_MAX_TON должен быть положительной суммой TON.") from None
        raw_account = os.getenv("TON_SENDER_ACCOUNT", "").strip()
        account = None
        if raw_account:
            try:
                account = json.loads(raw_account)
            except ValueError:
                raise ShopError(
                    "TON_SENDER_ACCOUNT должен быть JSON-объектом TON Connect."
                ) from None
            if not isinstance(account, dict):
                raise ShopError("TON_SENDER_ACCOUNT должен быть JSON-объектом TON Connect.")
        banner_setting = os.getenv("SHOP_BANNER_FILE", "").strip()
        banner = Path(banner_setting).expanduser() if banner_setting else DEFAULT_BANNER
        return cls(
            mode=cast(Mode, mode),
            data_dir=Path(os.getenv("DATA_DIR", "data")).expanduser().resolve(),
            admin_ids=frozenset(map(int, raw_ids)),
            token=os.getenv("BOT_TOKEN", "").strip(),
            cookies=os.getenv("FRAGMENT_COOKIES", "").strip(),
            seed=os.getenv("TON_SEED", "").strip(),
            api_key=os.getenv("TON_API_KEY", "").strip(),
            sender_account=account,
            api_provider=os.getenv("TON_API_PROVIDER", "tonapi").strip(),
            wallet_version=os.getenv("TON_WALLET_VERSION", "V5R1").strip(),
            mnemonic_type=os.getenv("TON_MNEMONIC_TYPE", "").strip() or None,
            max_nanoton=int(nanoton),
            telegram_proxy=os.getenv("TELEGRAM_PROXY", "").strip() or None,
            fragment_proxy=os.getenv("FRAGMENT_PROXY", "").strip() or None,
            banner_path=None if banner_setting.lower() == "off" else banner,
            premium_icons=os.getenv("SHOP_PREMIUM_ICONS", "1").strip() != "0",
            rich_menus=os.getenv("SHOP_RICH_MENUS", "1").strip() != "0",
        )
