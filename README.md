# Fragment Telegram Bot

A self-hosted, white-label Telegram storefront powered by [fragment-api-py](https://github.com/S1qwy/fragment-api-py). Each operator runs their own bot, branding, prices, customer support and TON wallet.

| Product | Delivery |
| --- | --- |
| Telegram Stars | Gifts to a public Telegram username; live orders return an unsigned `PreparedTransaction` for external signing |
| Telegram Premium | Gift subscriptions for 3, 6 or 12 months |
| GRAM | Telegram Ads balance top-ups supported by Fragment; not arbitrary TON transfers or Telegram Wallet deposits |

## Quick start

Requires Python 3.11+, Git and [uv](https://docs.astral.sh/uv/).

```sh
git clone --branch dev https://github.com/S1qwy/fragment-telegram-bot.git
cd fragment-telegram-bot
cp .env.example .env
uv sync --locked
uv run fragment-bot run
```

In PowerShell, copy the configuration with `Copy-Item .env.example .env`. Set `BOT_TOKEN` and at least one numeric Telegram user ID in `ADMIN_IDS`. Multiple admins may be separated by commas. Open `/admin` in the bot to configure the storefront. The bot account's name and avatar are managed through BotFather.

Alternatively, fill in `.env` and run `docker compose up --build -d`. Run management commands with `docker compose exec bot fragment-bot ...`. SQLite data is stored in `DATA_DIR` or the Compose `shop-data` volume. Run one polling process per bot token and back up the data directory.

## Modes and live setup

| `SHOP_MODE` | Customer payment | Fulfillment |
| --- | --- | --- |
| `demo` | Simulated payment button or CLI | Simulated |
| `test` | XTR in Telegram's dedicated test environment, using a separate test token | Simulated |
| `live` | Real Telegram Stars (XTR) | Unsigned Stars transactions; Premium and GRAM purchases after operator approval |

Use a separate `DATA_DIR` for each mode and bot. A new live database starts closed and uses rate-based pricing. Existing databases retain their catalog and settings. Demo presets cannot be applied to a live database.

To prepare live mode:

1. Set `SHOP_MODE=live` and a separate data directory such as `DATA_DIR=data-live`.
2. Set `FRAGMENT_COOKIES`. For Stars, supply the public TON Connect account JSON in `TON_SENDER_ACCOUNT` (`address`, `chain`, `publicKey`, `walletStateInit`). Alternatively, the SDK can derive the account from `TON_SEED`.
3. Automatic Premium/GRAM delivery requires `TON_SEED`, `TON_API_KEY` and Fragment cookies containing `stel_ton_token`. Disable unavailable product categories before running live. Authentication details are documented in the [SDK wiki](https://github.com/S1qwy/fragment-api-py/wiki/Client-and-Authentication).
4. Configure prices, enabled products, support details, terms and privacy information through `/admin`. Built-in prices are examples, not market quotes. Review them before opening the shop.
5. Run `uv run fragment-bot doctor --online`, then open the storefront in `/admin`.

The SDK is pinned to commit `04a0601b6cc7f11af0291683ddd773b2d96eb689` (v14.4.1). `FRAGMENT_MAX_TON` limits each prepared purchase including SDK fees and gas reserve. Credentials remain in `.env` or environment variables; storefront settings are stored in SQLite.

If using a local proxy, set `TELEGRAM_PROXY` and `FRAGMENT_PROXY`, for example `http://127.0.0.1:10809`. A Docker container needs a proxy address reachable from that container. The proxy must run separately. Telegram connection failures are retried; startup backoff grows to 30 seconds. Polling preserves its current offset, and notification failures do not repeat completed fulfillment.

## Rates, packages and availability

Open `/admin` → Settings → Rates and products (the current bot interface is in Russian: «Настройки» → «Курсы и товары»).

| Setting | Meaning | Example default |
| --- | --- | --- |
| `pricing_mode` | `rates` for quantity/duration pricing, `catalog` for individual catalog prices | `rates` on new live databases; `catalog` on existing/demo databases |
| `stars_rate` | XTR charged per delivered Star | `2` |
| `stars_packages` | Exactly five distinct Stars quantities | `50,100,250,500,1000` |
| `stars_custom` | Enable custom Stars quantity | `1` |
| `ton_rate` | XTR charged per GRAM | `350` |
| `premium_price_3` | Total price for 3 months, in XTR | `950` |
| `premium_price_6` | Total price for 6 months, in XTR | `1700` |
| `premium_price_12` | Total price for 12 months, in XTR | `3000` |
| `enabled_stars`, `enabled_premium`, `enabled_ton` | Enable/disable a whole product category | `1` |

Rates accept up to six decimal places. Totals are computed with decimal arithmetic and rounded up to whole XTR. Quantities must be integers within the SDK limits; invoice totals must be 1–10,000 XTR. Packages exceeding this invoice limit are hidden. GRAM uses quantity entry instead of preset packages. Custom Stars use the same rate as preset packages.

Prices and accepted terms are checked again when an order is created, then stored with that order. Changing a rate does not rewrite existing invoices. Disabling a category blocks new orders and pre-checkout for its unpaid invoices; captured payments remain available for fulfillment or refund.

The legacy catalog remains available in `catalog` pricing mode. `/product stars 50 100` adds a package and `/unlist stars 50` removes it. These individual package prices do not override rate-based pricing.

## Branding and administration

The settings panel also controls the shop name, welcome text, tagline, buy button, home button order/visibility, category order, homepage layout, banner URL, recent order panel, gift recipient policy, invoice lifetime, sender visibility, support link, terms, FAQ and privacy text. All persistent settings are also accessible through the CLI.

The admin panel includes order filters, search by order ID/recipient/buyer ID, payment history, statistics, fulfillment approval, reconciliation, refunds and prepared transaction downloads. Navigation uses rich tables when supported and falls back to HTML. Premium custom emoji are drawn from verified public sets, including [Telegram Premium Emojis](https://github.com/abbosahmad/telegram-premium-emojis), [TgAndroidIcons](https://t.me/addemoji/TgAndroidIcons), tgmacicons and vector_icons_by_fStikBot. `premium_only` falls back to plain text if Telegram rejects custom emoji. `SHOP_RICH_MENUS=0` and `SHOP_PREMIUM_ICONS=0` change inherited startup defaults.

Replace the local banner with `SHOP_BANNER_FILE` or set it to `off`. A public HTTPS banner URL can be changed in the bot. [Demo settings and sample texts](docs/demo-settings.md) are provided for testing and should be replaced before production sales.

Customer commands: `/start`, `/catalog`, `/orders`, `/order ID`, `/support`, `/paysupport`, `/terms`, `/faq`, `/privacy`, `/cancel`.

## Payments: implemented providers

The implemented adapters are **Telegram Stars (XTR)** and **DemoGateway**. YooKassa, CryptoBot and other Bedolaga providers are not bundled or configured. Automated tests exercise invoices, pre-checkout, receipt verification, duplicate handling and refunds; they do not prove that a particular deployment has completed a real payment.

Digital goods sold inside Telegram must use XTR under [Telegram's digital goods payment rules](https://core.telegram.org/bots/payments-stars). `provider_token` is empty for XTR. The bot waits for `successful_payment`, stores the original charge ID and verifies the payer, provider, currency, amount and order. Repeated receipts do not duplicate fulfillment. Unmatched payments are kept for manual reconciliation. TON wallet funding for Fragment fulfillment is separate from the customer's XTR payment.

The provider interface and extension workflow are described in [Payment adapters](docs/payments.md). Other sales channels can implement the same contract with their own verified incoming payment events.

## External signing for Stars

Approving a live Stars order prepares and stores its `PreparedTransaction`; the bot does not sign or broadcast it. Download the JSON from the admin order card or CLI:

```sh
uv run fragment-bot approve ORDER_ID --now
uv run fragment-bot prepared ORDER_ID
```

`approve --now` outputs the JSON when it performs preparation itself. If the running worker claimed the order first, wait for `prepared` and export it using the second command. Demo/test exports have empty `messages` and `raw.simulated=true`.

Sign and broadcast the live transaction externally before `valid_until`, using `sender_address`. Check every message, including the SDK fee. See the SDK's [external signing example](https://github.com/S1qwy/fragment-api-py/blob/04a0601b6cc7f11af0291683ddd773b2d96eb689/examples/external_signing.py). After independently confirming delivery, record it with `resolve-prepared ID delivered --evidence "..."`. If nothing was broadcast, wait for expiry, verify the blockchain and record `not_broadcast`; the order can then be refunded.

## CLI and recovery

```sh
uv run fragment-bot --help
uv run fragment-bot doctor --online
uv run fragment-bot settings
uv run fragment-bot set pricing_mode rates
uv run fragment-bot set stars_rate 2
uv run fragment-bot set premium_price_6 1700
uv run fragment-bot set enabled_ton 0
uv run fragment-bot reset-setting welcome
uv run fragment-bot catalog
uv run fragment-bot orders --pending
uv run fragment-bot order ORDER_ID
uv run fragment-bot approve ORDER_ID
uv run fragment-bot refund ORDER_ID
```

`approve` without `--now` queues the order for the running worker. Unknown delivery outcomes require reconciliation, not a fresh purchase: use `reconcile ID`. `resolve ID settled --evidence "..."` records independently verified blockchain settlement; delivery still requires Fragment confirmation. `resolve ID not_broadcast --evidence "..."` enables refund after confirming nothing was sent. `retry ID` only requeues an attempt with no recorded Fragment operation. The SDK intent and order link are journaled atomically before broadcast.

Use `payment-errors` and `refund-payment-error CHARGE_ID` for unmatched receipts. Refunds use the order's original payment adapter. Store the SQLite database and SDK journal privately.

For offline demo testing, use `demo-settings` and `demo-buy stars 50 @example`. `demo-settings` fills default fields; `--overwrite` intentionally replaces customized demo text. Demo purchase commands cannot run in live mode.

## Attribution

Architecture and interaction patterns are adapted from [Remnawave Bedolaga Telegram Bot](https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot), including its [payment modules](https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot/tree/0fb7453a1f606c51e4ad92fa155436eb06e04081/app/services/payment), [Stars payment handler](https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot/blob/0fb7453a1f606c51e4ad92fa155436eb06e04081/app/handlers/stars_payments.py), [webhook routing](https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot/blob/0fb7453a1f606c51e4ad92fa155436eb06e04081/app/webserver/payments.py), [menu](https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot/blob/0fb7453a1f606c51e4ad92fa155436eb06e04081/app/handlers/menu.py), [rich menu utilities](https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot/blob/0fb7453a1f606c51e4ad92fa155436eb06e04081/app/utils/rich_menu.py) and [menu layout settings](https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot/blob/0fb7453a1f606c51e4ad92fa155436eb06e04081/app/services/menu_layout/constants.py). This repository implements the storefront specifically for [fragment-api-py](https://github.com/S1qwy/fragment-api-py); it does not claim to include every upstream payment provider.

## Development

Work is currently on `dev`.

```sh
uv sync --locked --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

CI runs the suite on Python 3.11/3.12 and builds the Docker image.
