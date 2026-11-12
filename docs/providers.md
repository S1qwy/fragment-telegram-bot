# Payment provider clients and configuration

Source snapshot: [Bedolaga 0fb7453a](https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot/tree/0fb7453a1f606c51e4ad92fa155436eb06e04081). All 28 gateway identifiers are represented: Telegram Stars plus 27 API integrations. Internal balance/manual credit operations and `platega_recurrent` are not independent gateways. Recurring API methods are included with their owning provider.

## What is connected

The Telegram storefront charges in XTR under [Telegram's rules for digital goods](https://core.telegram.org/bots/payments-stars#faq). Use the other gateways from an independent website or another external sales channel through the CLI or Python API. The admin editor is available inside the bot.

The 25 hosted adapters create order-linked payment links and persist the provider reference. Status checks query the provider API and require matching invoice ID, amount and currency before crediting an order. Etoplatezhi uses its signed callback instead of status polling. Individual merchant accounts and live payment/settlement behavior require verification with the provider. Hosted order refunds currently require the provider dashboard. Where upstream exposes real refund calls, they are also available as direct API operations through `provider-call`.

Apple IAP verifies signed transactions and notifications for a native app. It requires App Store credentials, product configuration, and Apple root certificates. Tribute supplies donation links and signed events. The webhook stores verified donations for reconciliation because the provider event has no store order ID. Upstream's Tribute status/refund placeholders were disabled.

## External order checkout

Set `PROVIDER_ENABLED`, the provider's merchant credentials, `PROVIDER_CHECKOUT_RATE` in settlement currency units per XTR and `PROVIDER_CHECKOUT_CURRENCY`. Replace `PROVIDER` with the actual prefix, such as `CRYPTOBOT`. The quote uses decimal arithmetic and rounds up to hundredths. Provider currency and amount limits must match the merchant account.

```sh
uv run fragment-bot external-order cryptobot stars 50 recipient --buyer 123456 --accept-terms
uv run fragment-bot external-link ORDER_ID
uv run fragment-bot external-check ORDER_ID
```

`external-order` requires live mode and an open storefront. Pass `--accept-terms` after recording buyer consent in the external channel. Supply `--email` when the provider needs a payer contact. The first command prints an order ID and payment URL. Use the existing order ID for recovery after an ambiguous response. The bot does not send hosted payment links to the customer.

For signed callbacks, enable `PAYMENT_WEBHOOK_PORT` and reverse proxy HTTPS POST requests to `/payments/etoplatezhi` or `/payments/tribute`. The listener defaults to loopback. Set `ETOPLATEZHI_CALLBACK_URL` to the public endpoint for Etoplatezhi. Register the Tribute endpoint in its dashboard. API clients using aiohttp/httpx use `PAYMENT_PROXY`, falling back to `FRAGMENT_PROXY` or `TELEGRAM_PROXY`.

## Install and configure

```sh
uv sync --locked --extra providers
uv run fragment-bot providers
uv run fragment-bot provider-info cryptobot
uv run fragment-bot provider-set CRYPTOBOT_API_TOKEN
uv run fragment-bot provider-set CRYPTOBOT_ENABLED true
uv run fragment-bot provider-call cryptobot get_me --params-file empty.json
```

`empty.json` contains `{}`. For invoice creation, pass a JSON object with the exact keyword arguments shown by `provider-info`. API create/refund calls can change merchant state and may charge money; their output is the provider response, not an order fulfillment receipt. Do not retry a timed-out creation blindly: inspect the merchant dashboard/order identifier first. `provider-call` rejects private methods and wrong argument signatures. All provider clients are also available through `provider_client` for application integration.

Bot administration: `/admin` → Settings → All payment providers. Each provider has a paginated field editor. Secrets are masked, never echoed in responses, and their input messages are deleted when Telegram allows it. CLI `provider-info` also masks secrets. To reset an override use `provider-reset FIELD` or `/providerreset` while editing a field. Database overrides take precedence over environment values; resetting restores the environment/default value.

Settings are Fernet-encrypted in the existing SQLite database. Back up both the database and `provider-settings.key` from the same data directory. The encryption key is separate from the database; neither belongs in Git or the Docker image. Database encryption is not a substitute for access control on the host. Upstream diagnostic logging is filtered to avoid printing request bodies, tokens, signatures or customer details.

## Ported API surface

| Provider | Public API operations |
| --- | --- |
| anore | `snapshot`, `verify_callback_signature`, `create_payment`, `get_payment` |
| antilopay | `close`, `create_payment`, `check_payment`, `verify_callback_signature` |
| apple_iap | `verify_transaction`, `verify_signed_transaction_info`, `verify_notification`, `validate_transaction_info` |
| aurapay | `close`, `create_invoice`, `get_invoice_status`, `verify_webhook_signature` |
| cashera | `close`, `create_transaction`, `get_transaction`, `get_transaction_by_external_id`, `get_h2h`, `create_subscription`, `get_subscription`, `get_subscription_by_external_id`, `list_subscription_charges`, `cancel_subscription`, `verify_webhook` |
| cispay | `snapshot`, `close`, `create_payment`, `create_subscription`, `get_subscription`, `cancel_subscription`, `update_subscription`, `list_subscriptions`, `list_transactions`, `check_payment`, `get_store_capabilities`, `get_balance`, `verify_webhook_signature` |
| cloudpayments | `generate_payment_link`, `generate_invoice_id`, `charge_by_token`, `get_payment`, `find_payment`, `refund_payment`, `void_payment`, `verify_webhook_signature`, `parse_webhook_data` |
| cryptobot | `get_me`, `create_invoice`, `get_invoices`, `get_balance`, `get_exchange_rates`, `verify_webhook_signature`, `process_webhook` |
| donut | `close`, `create_payment`, `check_payment`, `get_balance`, `verify_callback_signature` |
| etoplatezhi | `build_payment_url`, `verify_callback_signature` |
| freekassa | `generate_form_signature`, `verify_webhook_signature`, `verify_webhook_ip`, `build_payment_url`, `create_order`, `create_order_and_get_url`, `get_order_status`, `get_balance`, `get_payment_systems` |
| heleket | `create_payment`, `get_payment_info`, `list_payments`, `verify_webhook_signature` |
| jupiter | `close`, `create_payment`, `check_payment`, `get_balance`, `verify_callback_signature` |
| kassa_ai | `verify_webhook_signature`, `create_order`, `create_order_and_get_url`, `get_order_status`, `get_balance` |
| lava | `close`, `create_invoice`, `get_invoice_status`, `get_services`, `list_recurrent_products`, `create_recurrent_consumer`, `subscribe_recurrent`, `unsubscribe_recurrent`, `get_recurrent_subscription_status`, `offset_recurrent_next_pay_time`, `verify_webhook_signature` |
| mulenpay | `create_payment`, `get_payment`, `list_payments` |
| overpay | `close`, `create_payment`, `create_payment_s2s`, `wait_for_redirect_link`, `get_payment`, `refund_payment` |
| pal24 | `create_bill`, `get_bill_status`, `get_payment_status`, `get_bill_payments`, `parse_callback`, `convert_to_kopeks`, `get_expiration` |
| paritypay | `close`, `create_invoice`, `get_invoice`, `create_invoice_reconciled`, `parse_callback_body`, `build_signature_payload`, `verify_callback_signature` |
| platega | `create_payment`, `get_transaction`, `create_subscription`, `get_subscription`, `get_subscription_status`, `list_subscriptions`, `cancel_subscription`, `parse_redirect_url`, `parse_expires_at` |
| riopay | `close`, `create_order`, `get_order`, `verify_webhook_signature` |
| rollypay | `close`, `create_payment`, `get_payment`, `verify_webhook_signature` |
| severpay | `close`, `create_payment`, `get_payment`, `verify_webhook_signature` |
| tabpay | `close`, `create_payment`, `get_payment`, `get_payment_by_order_id`, `create_payment_reconciled`, `verify_webhook_signature` |
| telegram_stars | Existing XTR invoice / pre-checkout / successful payment / refund adapter |
| tribute | `create_payment_link`, `verify_webhook_signature`, `process_webhook`, `get_payment_status`, `refund_payment` |
| wata | `create_payment_link`, `get_payment_link`, `search_transactions`, `get_transaction` |
| yookassa | `create_payment`, `create_sbp_payment`, `get_payment_info`, `create_autopayment` |

## Settings reference

The reference includes 420 upstream fields and 51 local checkout fields. The names and defaults below come from both sources. Some fields describe upstream checkout/subscription/UI policies; those policies are retained for compatibility and are not silently applied to this storefront. Credentials, endpoint settings and API service options are consumed by the ported clients. VPN subscription policies are not copied into digital gift fulfillment.

Secret defaults are omitted. `null` means unset. Private keys/certificates may be provided inline or through the upstream path settings. Example fallback payer data from upstream must be replaced with actual customer information where required.

### anore

| Field | Type | Default |
| --- | --- | --- |
| `ANORE_API_KEY` | str | (secret) |
| `ANORE_API_URL` | str | "https://api.anore.cc/v1" |
| `ANORE_DISPLAY_NAME` | str | "Anore" |
| `ANORE_ENABLED` | bool | false |
| `ANORE_FAIL_URL` | str | null |
| `ANORE_MAX_AMOUNT_KOPEKS` | int | 100000000 |
| `ANORE_MIN_AMOUNT_KOPEKS` | int | 10000 |
| `ANORE_PAYMENT_METHODS` | str | "sbp,card,yoomoney,crypto" |
| `ANORE_REQUEST_TIMEOUT` | int | 30 |
| `ANORE_SECRET_KEY` | str | (secret) |
| `ANORE_SHOP_ID` | int | null |
| `ANORE_SUCCESS_URL` | str | null |
| `ANORE_WEBHOOK_PATH` | str | "/anore-webhook" |

### antilopay

| Field | Type | Default |
| --- | --- | --- |
| `ANTILOPAY_APAY_VERIFICATION_TAG` | str | null |
| `ANTILOPAY_CARD_DISPLAY_NAME` | str | "Карта (Antilopay)" |
| `ANTILOPAY_CARD_ENABLED` | bool | false |
| `ANTILOPAY_CURRENCY` | str | "RUB" |
| `ANTILOPAY_DISPLAY_NAME` | str | "Antilopay" |
| `ANTILOPAY_ENABLED` | bool | false |
| `ANTILOPAY_MAX_AMOUNT_KOPEKS` | int | 10000000 |
| `ANTILOPAY_MIN_AMOUNT_KOPEKS` | int | 10000 |
| `ANTILOPAY_PAYMENT_LIFETIME_MINUTES` | int | 60 |
| `ANTILOPAY_PRIVATE_KEY` | str | (secret) |
| `ANTILOPAY_PRODUCT_NAME` | str | "VPN подписка" |
| `ANTILOPAY_PRODUCT_TYPE` | str | "services" |
| `ANTILOPAY_PROJECT_ID` | str | null |
| `ANTILOPAY_PUBLIC_KEY` | str | null |
| `ANTILOPAY_RETURN_URL` | str | null |
| `ANTILOPAY_SBERPAY_DISPLAY_NAME` | str | "SberPay (Antilopay)" |
| `ANTILOPAY_SBERPAY_ENABLED` | bool | false |
| `ANTILOPAY_SBP_DISPLAY_NAME` | str | "СБП (Antilopay)" |
| `ANTILOPAY_SBP_ENABLED` | bool | false |
| `ANTILOPAY_SECRET_ID` | str | (secret) |
| `ANTILOPAY_WEBHOOK_PATH` | str | "/antilopay-webhook" |

### apple_iap

| Field | Type | Default |
| --- | --- | --- |
| `APPLE_IAP_ALLOW_SANDBOX_ON_PRODUCTION` | bool | false |
| `APPLE_IAP_APP_APPLE_ID` | int | null |
| `APPLE_IAP_BUNDLE_ID` | str | "com.app.client" |
| `APPLE_IAP_ENABLED` | bool | false |
| `APPLE_IAP_ENABLE_ONLINE_CERT_CHECKS` | bool | true |
| `APPLE_IAP_ENVIRONMENT` | str | "Production" |
| `APPLE_IAP_ISSUER_ID` | str | null |
| `APPLE_IAP_KEY_ID` | str | null |
| `APPLE_IAP_PRIVATE_KEY` | str | (secret) |
| `APPLE_IAP_PRIVATE_KEY_PATH` | str | null |
| `APPLE_IAP_PRODUCTS` | str | "{\"com.app.client.topup.100\":10000,\"com.app.client.topup.300\":30000,\"com.app.client.topup.500\":50000}" |
| `APPLE_IAP_PURCHASE_FAILURE_LIMIT_PER_HOUR` | int | 20 |
| `APPLE_IAP_PURCHASE_RATE_LIMIT_PER_MINUTE` | int | 10 |
| `APPLE_IAP_RATE_LIMIT_FAIL_OPEN` | bool | false |
| `APPLE_IAP_ROOT_CERTS_PATHS` | str | "" |
| `APPLE_IAP_WEBHOOK_PATH` | str | "/apple-iap-webhook" |

### aurapay

| Field | Type | Default |
| --- | --- | --- |
| `AURAPAY_API_KEY` | str | (secret) |
| `AURAPAY_CARD_DISPLAY_NAME` | str | "Карта (AuraPay)" |
| `AURAPAY_CARD_ENABLED` | bool | false |
| `AURAPAY_CURRENCY` | str | "RUB" |
| `AURAPAY_DISPLAY_NAME` | str | "AuraPay" |
| `AURAPAY_ENABLED` | bool | false |
| `AURAPAY_MAX_AMOUNT_KOPEKS` | int | 10000000 |
| `AURAPAY_MIN_AMOUNT_KOPEKS` | int | 10000 |
| `AURAPAY_PAYMENT_LIFETIME_MINUTES` | int | 60 |
| `AURAPAY_RETURN_URL` | str | null |
| `AURAPAY_SBP_DISPLAY_NAME` | str | "СБП (AuraPay)" |
| `AURAPAY_SBP_ENABLED` | bool | false |
| `AURAPAY_SECRET_KEY` | str | (secret) |
| `AURAPAY_SHOP_ID` | str | null |
| `AURAPAY_WEBHOOK_PATH` | str | "/aurapay-webhook" |

### cashera

| Field | Type | Default |
| --- | --- | --- |
| `CASHERA_ACTIVE_METHODS` | str | "sbp,card" |
| `CASHERA_API_KEY` | str | (secret) |
| `CASHERA_API_SECRET` | str | (secret) |
| `CASHERA_BASE_URL` | str | "https://api.cashera.cash/api/v1" |
| `CASHERA_DISPLAY_NAME` | str | "Cashera" |
| `CASHERA_ENABLED` | bool | false |
| `CASHERA_FAILED_URL` | str | null |
| `CASHERA_H2H_ENABLED` | bool | false |
| `CASHERA_INLINE_METHODS` | bool | false |
| `CASHERA_MAX_AMOUNT_KOPEKS` | int | 10000000 |
| `CASHERA_MIN_AMOUNT_KOPEKS` | int | 10000 |
| `CASHERA_RECURRENT_ENABLED` | bool | false |
| `CASHERA_RETURN_URL` | str | null |
| `CASHERA_WEBHOOK_PATH` | str | "/cashera-webhook" |

### cispay

| Field | Type | Default |
| --- | --- | --- |
| `CISPAY_API_KEY` | str | (secret) |
| `CISPAY_BASE_URL` | str | "https://api.cispay.app" |
| `CISPAY_CARD_DISPLAY_NAME` | str | "Карта (CisPay)" |
| `CISPAY_CARD_ENABLED` | bool | false |
| `CISPAY_CRYPTO_DISPLAY_NAME` | str | "Криптовалюта (CisPay)" |
| `CISPAY_CRYPTO_ENABLED` | bool | false |
| `CISPAY_CURRENCY` | str | "RUB" |
| `CISPAY_DISPLAY_NAME` | str | "CisPay" |
| `CISPAY_ENABLED` | bool | false |
| `CISPAY_MAX_AMOUNT_KOPEKS` | int | 10000000 |
| `CISPAY_MIN_AMOUNT_KOPEKS` | int | 10000 |
| `CISPAY_PAYMENT_LIFETIME_MINUTES` | int | 30 |
| `CISPAY_RECURRENT_ENABLED` | bool | false |
| `CISPAY_SBP_DISPLAY_NAME` | str | "СБП (CisPay)" |
| `CISPAY_SBP_ENABLED` | bool | false |
| `CISPAY_SHOP_ID` | str | null |
| `CISPAY_WEBHOOK_PATH` | str | "/cispay-webhook" |

### cloudpayments

| Field | Type | Default |
| --- | --- | --- |
| `CLOUDPAYMENTS_API_SECRET` | str | (secret) |
| `CLOUDPAYMENTS_API_URL` | str | "https://api.cloudpayments.ru" |
| `CLOUDPAYMENTS_CURRENCY` | str | "RUB" |
| `CLOUDPAYMENTS_DESCRIPTION` | str | "Пополнение баланса" |
| `CLOUDPAYMENTS_DISPLAY_NAME` | str | "CloudPayments" |
| `CLOUDPAYMENTS_ENABLED` | bool | false |
| `CLOUDPAYMENTS_MAX_AMOUNT_KOPEKS` | int | 10000000 |
| `CLOUDPAYMENTS_MIN_AMOUNT_KOPEKS` | int | 5000 |
| `CLOUDPAYMENTS_PUBLIC_ID` | str | null |
| `CLOUDPAYMENTS_REQUIRE_EMAIL` | bool | false |
| `CLOUDPAYMENTS_RETURN_URL` | str | null |
| `CLOUDPAYMENTS_SKIN` | str | "mini" |
| `CLOUDPAYMENTS_TEST_MODE` | bool | false |
| `CLOUDPAYMENTS_WEBHOOK_HOST` | str | "0.0.0.0" |
| `CLOUDPAYMENTS_WEBHOOK_PATH` | str | "/cloudpayments-webhook" |
| `CLOUDPAYMENTS_WEBHOOK_PORT` | int | 8087 |
| `CLOUDPAYMENTS_WIDGET_URL` | str | "https://widget.cloudpayments.ru/show" |

### cryptobot

| Field | Type | Default |
| --- | --- | --- |
| `CRYPTOBOT_API_TOKEN` | str | (secret) |
| `CRYPTOBOT_ASSETS` | str | "USDT,TON,BTC,ETH" |
| `CRYPTOBOT_BASE_URL` | str | "https://pay.crypt.bot" |
| `CRYPTOBOT_DEFAULT_ASSET` | str | "USDT" |
| `CRYPTOBOT_DISPLAY_NAME` | str | "CryptoBot" |
| `CRYPTOBOT_ENABLED` | bool | false |
| `CRYPTOBOT_INVOICE_EXPIRES_HOURS` | int | 24 |
| `CRYPTOBOT_TESTNET` | bool | false |
| `CRYPTOBOT_WEBHOOK_PATH` | str | "/cryptobot-webhook" |
| `CRYPTOBOT_WEBHOOK_PORT` | int | 8083 |
| `CRYPTOBOT_WEBHOOK_SECRET` | str | (secret) |

### donut

| Field | Type | Default |
| --- | --- | --- |
| `DONUT_BASE_URL` | str | "https://gw.donut.business" |
| `DONUT_CARD_DISPLAY_NAME` | str | "Карта (Donut)" |
| `DONUT_CARD_ENABLED` | bool | false |
| `DONUT_CURRENCY` | str | "RUB" |
| `DONUT_DISPLAY_NAME` | str | "Donut" |
| `DONUT_ENABLED` | bool | false |
| `DONUT_MAX_AMOUNT_KOPEKS` | int | 10000000 |
| `DONUT_METHOD_ID` | str | null |
| `DONUT_MIN_AMOUNT_KOPEKS` | int | 10000 |
| `DONUT_PAYMENT_LIFETIME_MINUTES` | int | 60 |
| `DONUT_RETURN_URL` | str | null |
| `DONUT_SBP_DISPLAY_NAME` | str | "СБП (Donut)" |
| `DONUT_SBP_ENABLED` | bool | false |
| `DONUT_SBP_QR_DISPLAY_NAME` | str | "СБП QR (Donut)" |
| `DONUT_SBP_QR_ENABLED` | bool | false |
| `DONUT_SECRET` | str | (secret) |
| `DONUT_TOKEN` | str | (secret) |
| `DONUT_WEBHOOK_PATH` | str | "/donut-webhook" |

### etoplatezhi

| Field | Type | Default |
| --- | --- | --- |
| `ETOPLATEZHI_CARD_DISPLAY_NAME` | str | "Карта (Etoplatezhi)" |
| `ETOPLATEZHI_CARD_ENABLED` | bool | false |
| `ETOPLATEZHI_CURRENCY` | str | "RUB" |
| `ETOPLATEZHI_DISPLAY_NAME` | str | "Etoplatezhi" |
| `ETOPLATEZHI_ENABLED` | bool | false |
| `ETOPLATEZHI_MAX_AMOUNT_KOPEKS` | int | 10000000 |
| `ETOPLATEZHI_MIN_AMOUNT_KOPEKS` | int | 10000 |
| `ETOPLATEZHI_PAYMENT_LIFETIME_MINUTES` | int | 60 |
| `ETOPLATEZHI_PROJECT_ID` | int | null |
| `ETOPLATEZHI_RETURN_URL` | str | null |
| `ETOPLATEZHI_SBP_DISPLAY_NAME` | str | "СБП (Etoplatezhi)" |
| `ETOPLATEZHI_SBP_ENABLED` | bool | false |
| `ETOPLATEZHI_SECRET_KEY` | str | (secret) |
| `ETOPLATEZHI_WEBHOOK_PATH` | str | "/etoplatezhi-webhook" |

### freekassa

| Field | Type | Default |
| --- | --- | --- |
| `FREEKASSA_API_KEY` | str | (secret) |
| `FREEKASSA_CARD_DISPLAY_NAME` | str | "Карта РФ" |
| `FREEKASSA_CARD_ENABLED` | bool | false |
| `FREEKASSA_CURRENCY` | str | "RUB" |
| `FREEKASSA_DISPLAY_NAME` | str | "Freekassa" |
| `FREEKASSA_ENABLED` | bool | false |
| `FREEKASSA_MAX_AMOUNT_KOPEKS` | int | 100000000 |
| `FREEKASSA_MIN_AMOUNT_KOPEKS` | int | 10000 |
| `FREEKASSA_PAYMENT_SYSTEM_ID` | int | null |
| `FREEKASSA_PAYMENT_TIMEOUT_SECONDS` | int | 3600 |
| `FREEKASSA_SBP_DISPLAY_NAME` | str | "СБП (QR код)" |
| `FREEKASSA_SBP_ENABLED` | bool | false |
| `FREEKASSA_SECRET_WORD_1` | str | (secret) |
| `FREEKASSA_SECRET_WORD_2` | str | (secret) |
| `FREEKASSA_SHOP_ID` | int | null |
| `FREEKASSA_USE_API` | bool | false |
| `FREEKASSA_WEBHOOK_HOST` | str | "0.0.0.0" |
| `FREEKASSA_WEBHOOK_PATH` | str | "/freekassa-webhook" |
| `FREEKASSA_WEBHOOK_PORT` | int | 8088 |

### heleket

| Field | Type | Default |
| --- | --- | --- |
| `HELEKET_API_KEY` | str | (secret) |
| `HELEKET_BASE_URL` | str | "https://api.heleket.com/v1" |
| `HELEKET_CALLBACK_URL` | str | null |
| `HELEKET_DEFAULT_CURRENCY` | str | "USDT" |
| `HELEKET_DEFAULT_NETWORK` | str | null |
| `HELEKET_DISPLAY_NAME` | str | "Heleket Crypto" |
| `HELEKET_ENABLED` | bool | false |
| `HELEKET_INVOICE_LIFETIME` | int | 3600 |
| `HELEKET_MARKUP_PERCENT` | float | 0.0 |
| `HELEKET_MERCHANT_ID` | str | null |
| `HELEKET_RETURN_URL` | str | null |
| `HELEKET_SUCCESS_URL` | str | null |
| `HELEKET_WEBHOOK_HOST` | str | "0.0.0.0" |
| `HELEKET_WEBHOOK_PATH` | str | "/heleket-webhook" |
| `HELEKET_WEBHOOK_PORT` | int | 8086 |

### jupiter

| Field | Type | Default |
| --- | --- | --- |
| `JUPITER_BASE_URL` | str | "https://app.juppiter.tech" |
| `JUPITER_CURRENCY` | str | "RUB" |
| `JUPITER_DISPLAY_NAME` | str | "Jupiter" |
| `JUPITER_ENABLED` | bool | false |
| `JUPITER_FALLBACK_EMAIL` | str | "user@vpn.bot" |
| `JUPITER_FALLBACK_NAME` | str | "User" |
| `JUPITER_FALLBACK_PHONE` | str | "0000000000" |
| `JUPITER_MAX_AMOUNT_KOPEKS` | int | 10000000 |
| `JUPITER_METHOD_DESCRIPTION` | str | "SBP" |
| `JUPITER_METHOD_ID` | str | null |
| `JUPITER_MIN_AMOUNT_KOPEKS` | int | 10000 |
| `JUPITER_PAYMENT_LIFETIME_MINUTES` | int | 60 |
| `JUPITER_RETURN_URL` | str | null |
| `JUPITER_SBP_DISPLAY_NAME` | str | "СБП (Jupiter)" |
| `JUPITER_SBP_ENABLED` | bool | false |
| `JUPITER_SECRET` | str | (secret) |
| `JUPITER_TOKEN` | str | (secret) |
| `JUPITER_WEBHOOK_PATH` | str | "/jupiter-webhook" |

### kassa_ai

| Field | Type | Default |
| --- | --- | --- |
| `KASSA_AI_API_KEY` | str | (secret) |
| `KASSA_AI_CARD_DISPLAY_NAME` | str | "Карта (KassaAI)" |
| `KASSA_AI_CARD_ENABLED` | bool | false |
| `KASSA_AI_CURRENCY` | str | "RUB" |
| `KASSA_AI_DISPLAY_NAME` | str | "KassaAI" |
| `KASSA_AI_ENABLED` | bool | false |
| `KASSA_AI_MAX_AMOUNT_KOPEKS` | int | 100000000 |
| `KASSA_AI_MIN_AMOUNT_KOPEKS` | int | 10000 |
| `KASSA_AI_PAYMENT_SYSTEM_ID` | int | 44 |
| `KASSA_AI_SBERPAY_DISPLAY_NAME` | str | "SberPay (KassaAI)" |
| `KASSA_AI_SBERPAY_ENABLED` | bool | false |
| `KASSA_AI_SBP_DISPLAY_NAME` | str | "СБП (KassaAI)" |
| `KASSA_AI_SBP_ENABLED` | bool | false |
| `KASSA_AI_SECRET_WORD_2` | str | (secret) |
| `KASSA_AI_SHOP_ID` | int | null |
| `KASSA_AI_WEBHOOK_HOST` | str | "0.0.0.0" |
| `KASSA_AI_WEBHOOK_PATH` | str | "/kassa-ai-webhook" |
| `KASSA_AI_WEBHOOK_PORT` | int | 8089 |

### lava

| Field | Type | Default |
| --- | --- | --- |
| `LAVA_BASE_URL` | str | "https://api.lava.ru" |
| `LAVA_CARD_DISPLAY_NAME` | str | "Карта (Lava)" |
| `LAVA_CARD_ENABLED` | bool | false |
| `LAVA_CURRENCY` | str | "RUB" |
| `LAVA_DISPLAY_NAME` | str | "Lava" |
| `LAVA_ENABLED` | bool | false |
| `LAVA_MAX_AMOUNT_KOPEKS` | int | 10000000 |
| `LAVA_MIN_AMOUNT_KOPEKS` | int | 10000 |
| `LAVA_PAYMENT_LIFETIME_MINUTES` | int | 60 |
| `LAVA_RECURRENT_ENABLED` | bool | false |
| `LAVA_RETURN_URL` | str | null |
| `LAVA_SBP_DISPLAY_NAME` | str | "СБП (Lava)" |
| `LAVA_SBP_ENABLED` | bool | false |
| `LAVA_SECRET_KEY` | str | (secret) |
| `LAVA_SHOP_ID` | str | null |
| `LAVA_WEBHOOK_PATH` | str | "/lava-webhook" |
| `LAVA_WEBHOOK_SECRET` | str | (secret) |

### mulenpay

| Field | Type | Default |
| --- | --- | --- |
| `MULENPAY_API_KEY` | str | (secret) |
| `MULENPAY_BASE_URL` | str | "https://mulenpay.ru/api" |
| `MULENPAY_DESCRIPTION` | str | "Пополнение баланса" |
| `MULENPAY_DISPLAY_NAME` | str | "Mulen Pay" |
| `MULENPAY_ENABLED` | bool | false |
| `MULENPAY_IFRAME_EXPECTED_ORIGIN` | str | null |
| `MULENPAY_LANGUAGE` | str | "ru" |
| `MULENPAY_MAX_AMOUNT_KOPEKS` | int | 10000000 |
| `MULENPAY_MIN_AMOUNT_KOPEKS` | int | 10000 |
| `MULENPAY_PAYMENT_MODE` | int | 4 |
| `MULENPAY_PAYMENT_SUBJECT` | int | 4 |
| `MULENPAY_SECRET_KEY` | str | (secret) |
| `MULENPAY_SHOP_ID` | int | null |
| `MULENPAY_VAT_CODE` | int | 0 |
| `MULENPAY_WEBHOOK_PATH` | str | "/mulenpay-webhook" |
| `MULENPAY_WEBSITE_URL` | str | null |

### overpay

| Field | Type | Default |
| --- | --- | --- |
| `OVERPAY_API_URL` | str | "https://api.overpay.io" |
| `OVERPAY_CARD_TERMINAL_ID` | str | null |
| `OVERPAY_CURRENCY` | str | "RUB" |
| `OVERPAY_DISPLAY_NAME` | str | "Overpay" |
| `OVERPAY_ENABLED` | bool | false |
| `OVERPAY_INT_ENABLED` | bool | false |
| `OVERPAY_INT_MIN_EUR` | float | 5.0 |
| `OVERPAY_INT_TERMINAL_ID` | str | null |
| `OVERPAY_LIFETIME_MINUTES` | int | 1440 |
| `OVERPAY_MAX_AMOUNT_KOPEKS` | int | 10000000 |
| `OVERPAY_MIN_AMOUNT_KOPEKS` | int | 10000 |
| `OVERPAY_P12_PASSPHRASE` | str | (secret) |
| `OVERPAY_P12_PATH` | str | null |
| `OVERPAY_PASSWORD` | str | (secret) |
| `OVERPAY_PAYMENT_METHODS` | str | "card,fps" |
| `OVERPAY_PROJECT_ID` | str | null |
| `OVERPAY_RETURN_URL` | str | null |
| `OVERPAY_RUB_PER_EUR` | float | 0.0 |
| `OVERPAY_SBP_DIRECT_QR` | bool | false |
| `OVERPAY_SBP_TERMINAL_ID` | str | null |
| `OVERPAY_SERVER_IP` | str | null |
| `OVERPAY_USERNAME` | str | null |
| `OVERPAY_WEBHOOK_PATH` | str | "/overpay-webhook" |

### pal24

| Field | Type | Default |
| --- | --- | --- |
| `PAL24_API_TOKEN` | str | (secret) |
| `PAL24_BASE_URL` | str | "https://pal24.pro/api/v1/" |
| `PAL24_CARD_BUTTON_TEXT` | str | null |
| `PAL24_CARD_BUTTON_VISIBLE` | bool | true |
| `PAL24_DISPLAY_NAME` | str | "PAL24" |
| `PAL24_ENABLED` | bool | false |
| `PAL24_MAX_AMOUNT_KOPEKS` | int | 100000000 |
| `PAL24_MIN_AMOUNT_KOPEKS` | int | 10000 |
| `PAL24_PAYMENT_DESCRIPTION` | str | "Пополнение баланса" |
| `PAL24_REQUEST_TIMEOUT` | int | 30 |
| `PAL24_SBP_BUTTON_TEXT` | str | null |
| `PAL24_SBP_BUTTON_VISIBLE` | bool | true |
| `PAL24_SHOP_ID` | str | null |
| `PAL24_SIGNATURE_TOKEN` | str | (secret) |
| `PAL24_WEBHOOK_PATH` | str | "/pal24-webhook" |

### paritypay

| Field | Type | Default |
| --- | --- | --- |
| `PARITYPAY_BASE_URL` | str | "https://api.paritypay.net" |
| `PARITYPAY_CALLBACK_SECRET` | str | (secret) |
| `PARITYPAY_CARD_DISPLAY_NAME` | str | "Карта (ParityPay)" |
| `PARITYPAY_CARD_ENABLED` | bool | false |
| `PARITYPAY_DISPLAY_NAME` | str | "ParityPay" |
| `PARITYPAY_ENABLED` | bool | false |
| `PARITYPAY_INVOICE_LIFETIME_MINUTES` | int | 60 |
| `PARITYPAY_MAX_AMOUNT_KOPEKS` | int | 10000000 |
| `PARITYPAY_MIN_AMOUNT_KOPEKS` | int | 10000 |
| `PARITYPAY_SBP_DISPLAY_NAME` | str | "СБП (ParityPay)" |
| `PARITYPAY_SBP_ENABLED` | bool | false |
| `PARITYPAY_SECRET_KEY` | str | (secret) |
| `PARITYPAY_SHOP_ID` | str | null |
| `PARITYPAY_WEBHOOK_PATH` | str | "/paritypay-webhook" |

### platega

| Field | Type | Default |
| --- | --- | --- |
| `PLATEGA_ACTIVE_METHODS` | str | "2,11,12,13" |
| `PLATEGA_API_VERSION` | str | "v1" |
| `PLATEGA_BASE_URL` | str | "https://app.platega.io" |
| `PLATEGA_CURRENCY` | str | "RUB" |
| `PLATEGA_DISPLAY_NAME` | str | "Platega" |
| `PLATEGA_ENABLED` | bool | false |
| `PLATEGA_FAILED_URL` | str | null |
| `PLATEGA_INLINE_METHODS` | bool | true |
| `PLATEGA_MAX_AMOUNT_KOPEKS` | int | 100000000 |
| `PLATEGA_MERCHANT_ID` | str | null |
| `PLATEGA_MIN_AMOUNT_KOPEKS` | int | 10000 |
| `PLATEGA_RECURRENT_ENABLED` | bool | false |
| `PLATEGA_RETURN_URL` | str | null |
| `PLATEGA_SECRET` | str | (secret) |
| `PLATEGA_WEBHOOK_HOST` | str | "0.0.0.0" |
| `PLATEGA_WEBHOOK_PATH` | str | "/platega-webhook" |
| `PLATEGA_WEBHOOK_PORT` | int | 8086 |

### riopay

| Field | Type | Default |
| --- | --- | --- |
| `RIOPAY_API_TOKEN` | str | (secret) |
| `RIOPAY_CURRENCY` | str | "RUB" |
| `RIOPAY_DISPLAY_NAME` | str | "RioPay" |
| `RIOPAY_ENABLED` | bool | false |
| `RIOPAY_FAIL_URL` | str | null |
| `RIOPAY_MAX_AMOUNT_KOPEKS` | int | 100000000 |
| `RIOPAY_MIN_AMOUNT_KOPEKS` | int | 10000 |
| `RIOPAY_SUCCESS_URL` | str | null |
| `RIOPAY_WEBHOOK_PATH` | str | "/riopay-webhook" |
| `RIOPAY_WEBHOOK_SECRET` | str | (secret) |

### rollypay

| Field | Type | Default |
| --- | --- | --- |
| `ROLLYPAY_API_KEY` | str | (secret) |
| `ROLLYPAY_CURRENCY` | str | "RUB" |
| `ROLLYPAY_DISPLAY_NAME` | str | "RollyPay" |
| `ROLLYPAY_ENABLED` | bool | false |
| `ROLLYPAY_MAX_AMOUNT_KOPEKS` | int | 10000000 |
| `ROLLYPAY_MIN_AMOUNT_KOPEKS` | int | 10000 |
| `ROLLYPAY_RETURN_URL` | str | null |
| `ROLLYPAY_SIGNING_SECRET` | str | (secret) |
| `ROLLYPAY_WEBHOOK_PATH` | str | "/rollypay-webhook" |

### severpay

| Field | Type | Default |
| --- | --- | --- |
| `SEVERPAY_CURRENCY` | str | "RUB" |
| `SEVERPAY_DISPLAY_NAME` | str | "SeverPay" |
| `SEVERPAY_ENABLED` | bool | false |
| `SEVERPAY_LIFETIME` | int | 1440 |
| `SEVERPAY_MAX_AMOUNT_KOPEKS` | int | 10000000 |
| `SEVERPAY_MID` | int | null |
| `SEVERPAY_MIN_AMOUNT_KOPEKS` | int | 10000 |
| `SEVERPAY_RETURN_URL` | str | null |
| `SEVERPAY_TOKEN` | str | (secret) |
| `SEVERPAY_WEBHOOK_PATH` | str | "/severpay-webhook" |

### tabpay

| Field | Type | Default |
| --- | --- | --- |
| `TABPAY_API_KEY` | str | (secret) |
| `TABPAY_BASE_URL` | str | "https://tabpay.org/api" |
| `TABPAY_CARD_DISPLAY_NAME` | str | "Карта (TabPay)" |
| `TABPAY_CARD_ENABLED` | bool | false |
| `TABPAY_DISPLAY_NAME` | str | "TabPay" |
| `TABPAY_ENABLED` | bool | false |
| `TABPAY_MAX_AMOUNT_KOPEKS` | int | 10000000 |
| `TABPAY_MIN_AMOUNT_KOPEKS` | int | 10000 |
| `TABPAY_SBP_DISPLAY_NAME` | str | "СБП (TabPay)" |
| `TABPAY_SBP_ENABLED` | bool | false |
| `TABPAY_WEBHOOK_MAX_AGE_SECONDS` | int | 300 |
| `TABPAY_WEBHOOK_PATH` | str | "/tabpay-webhook" |
| `TABPAY_WEBHOOK_SECRET` | str | (secret) |

### telegram_stars

| Field | Type | Default |
| --- | --- | --- |
| `TELEGRAM_STARS_DISPLAY_NAME` | str | "Telegram Stars" |
| `TELEGRAM_STARS_ENABLED` | bool | true |
| `TELEGRAM_STARS_RATE_RUB` | float | 1.0 |

### tribute

| Field | Type | Default |
| --- | --- | --- |
| `TRIBUTE_API_KEY` | str | (secret) |
| `TRIBUTE_DONATE_LINK` | str | null |
| `TRIBUTE_ENABLED` | bool | false |
| `TRIBUTE_WEBHOOK_HOST` | str | "0.0.0.0" |
| `TRIBUTE_WEBHOOK_PATH` | str | "/tribute-webhook" |
| `TRIBUTE_WEBHOOK_PORT` | int | 8081 |

### wata

| Field | Type | Default |
| --- | --- | --- |
| `WATA_ACCESS_TOKEN` | str | (secret) |
| `WATA_BASE_URL` | str | "https://api.wata.pro/api/h2h" |
| `WATA_DISPLAY_NAME` | str | "Wata" |
| `WATA_ENABLED` | bool | false |
| `WATA_FAIL_REDIRECT_URL` | str | null |
| `WATA_LINK_TTL_MINUTES` | int | null |
| `WATA_MAX_AMOUNT_KOPEKS` | int | 100000000 |
| `WATA_MIN_AMOUNT_KOPEKS` | int | 10000 |
| `WATA_PAYMENT_DESCRIPTION` | str | "Пополнение баланса" |
| `WATA_PAYMENT_TYPE` | str | "OneTime" |
| `WATA_PUBLIC_KEY_CACHE_SECONDS` | int | 3600 |
| `WATA_PUBLIC_KEY_URL` | str | null |
| `WATA_REQUEST_TIMEOUT` | int | 30 |
| `WATA_SUCCESS_REDIRECT_URL` | str | null |
| `WATA_TERMINAL_PUBLIC_ID` | str | null |
| `WATA_WEBHOOK_HOST` | str | "0.0.0.0" |
| `WATA_WEBHOOK_PATH` | str | "/wata-webhook" |
| `WATA_WEBHOOK_PORT` | int | 8085 |

### yookassa

| Field | Type | Default |
| --- | --- | --- |
| `YOOKASSA_DEFAULT_RECEIPT_EMAIL` | str | null |
| `YOOKASSA_DISPLAY_NAME` | str | "YooKassa" |
| `YOOKASSA_ENABLED` | bool | false |
| `YOOKASSA_HTTP_CONNECT_TIMEOUT` | int | 5 |
| `YOOKASSA_HTTP_READ_TIMEOUT` | int | 10 |
| `YOOKASSA_MAX_AMOUNT_KOPEKS` | int | 1000000 |
| `YOOKASSA_MAX_CONCURRENT_REQUESTS` | int | 4 |
| `YOOKASSA_MIN_AMOUNT_KOPEKS` | int | 5000 |
| `YOOKASSA_PAYMENT_MODE` | str | "full_payment" |
| `YOOKASSA_PAYMENT_SUBJECT` | str | "service" |
| `YOOKASSA_RECURRENT_ENABLED` | bool | false |
| `YOOKASSA_RECURRENT_REQUIRED` | bool | false |
| `YOOKASSA_RETURN_URL` | str | null |
| `YOOKASSA_SBP_ENABLED` | bool | false |
| `YOOKASSA_SECRET_KEY` | str | (secret) |
| `YOOKASSA_SHOP_ID` | str | null |
| `YOOKASSA_SKIP_IP_CHECK` | bool | false |
| `YOOKASSA_TEST_MODE` | bool | false |
| `YOOKASSA_TRUSTED_PROXY_NETWORKS` | str | "" |
| `YOOKASSA_VAT_CODE` | int | 1 |
| `YOOKASSA_WEBHOOK_HOST` | str | "0.0.0.0" |
| `YOOKASSA_WEBHOOK_PATH` | str | "/yookassa-webhook" |
| `YOOKASSA_WEBHOOK_PORT` | int | 8082 |

## Etoplatezhi checkout fields

`ETOPLATEZHI_CHECKOUT_RATE`, `ETOPLATEZHI_CHECKOUT_CURRENCY` and `ETOPLATEZHI_CALLBACK_URL` configure the local hosted adapter.

## License and provenance

Ported modules live in `fragment_bot/vendor/bedolaga`. Their original MIT + Commons Clause license is preserved in the adjacent `LICENSE`; `SOURCES.json` records the pinned commit, original paths and hashes. These modules are not relicensed under the root MIT-only license. Local adaptations isolate configuration per shop, replace application imports, preserve payer requirements and suppress unsafe payload logging. Copying this code does not remove the upstream Commons Clause restriction on selling the software or services substantially deriving their value from it.
