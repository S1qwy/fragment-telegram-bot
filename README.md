# Fragment Telegram Bot

White-label витрина для продажи подарочных Telegram Stars, Telegram Premium и пополнения GRAM для Telegram Ads через [fragment-api-py](https://github.com/S1qwy/fragment-api-py). Каждый владелец разворачивает свой экземпляр с собственным ботом, названием, каталогом и ценами.

| Товар | Что получает покупатель |
| --- | --- |
| `stars` | Подарочные Telegram Stars на аккаунт по публичному @username |
| `premium` | Подарочный Telegram Premium на 3, 6 или 12 месяцев |
| `ton` | GRAM на баланс Telegram Ads для получателя, которого находит Fragment |

Перевод GRAM в Telegram Wallet или на произвольный TON-адрес требует другой операции. Оплата цифровых товаров внутри бота идёт в XTR по [правилам Telegram](https://core.telegram.org/bots/payments-stars). Цена в каталоге задаётся в целых XTR, а выдача в live оплачивается с отдельного TON-кошелька владельца.

## Запуск

Нужны Python 3.11+, Git и [uv](https://docs.astral.sh/uv/). Возьмите токен у [@BotFather](https://t.me/BotFather) и укажите свой числовой Telegram ID в `ADMIN_IDS`. Для нескольких администраторов перечислите ID через запятую.

```sh
git clone --branch dev https://github.com/S1qwy/fragment-telegram-bot.git
cd fragment-telegram-bot
cp .env.example .env
uv sync --locked
uv run fragment-bot run
```

В PowerShell для копирования используйте `Copy-Item .env.example .env`. С Docker Compose после заполнения `.env` запустите `docker compose up --build -d`. Команды управления доступны через `docker compose exec bot fragment-bot …`. Используйте один процесс Telegram polling на токен. SQLite с каталогом, платежами, заказами и журналом SDK хранится в `DATA_DIR` либо в томе Compose `shop-data`.

При запуске через локальный прокси укажите его URL в `TELEGRAM_PROXY` и `FRAGMENT_PROXY`. Для процесса на той же машине подойдёт URL вида `http://127.0.0.1:10809`. В Docker укажите адрес прокси, доступный из контейнера.

| Режим | Оплата | Выдача |
| --- | --- | --- |
| `demo` | Кнопка имитации или CLI | Имитация |
| `test` | XTR в [тестовой среде Telegram](https://core.telegram.org/bots/features#dedicated-test-environment), отдельный тестовый токен | Имитация |
| `live` | Реальные XTR | Stars подготавливаются для внешней подписи. Premium и GRAM выдаются автоматически после подтверждения оператором. |

Для каждого режима и каждого токена используйте свой каталог данных. В `demo` и `test` каталог заполнен примерами. В `live` витрина закрыта до настройки каталога, условий и контакта поддержки. Данные SDK берутся из `.env` или переменных среды. Сессию можно подготовить по [документации SDK](https://github.com/S1qwy/fragment-api-py/wiki/Client-and-Authentication). Для Stars нужны `FRAGMENT_COOKIES` и публичный JSON аккаунта TON Connect в `TON_SENDER_ACCOUNT` (поля `address`, `chain`, `publicKey`, `walletStateInit`). Вместо публичного аккаунта можно указать `TON_SEED` для локального получения адреса. Для автоматической выдачи Premium и GRAM нужны `FRAGMENT_COOKIES` с `stel_ton_token`, `TON_SEED` отдельного кошелька и `TON_API_KEY`. Перепроверьте кошелёк, комиссию SDK и лимит `FRAGMENT_MAX_TON`. Лимит применяется к фактической подготовленной операции с комиссией и резервом перед отправкой. SDK устанавливается из закреплённого Git-коммита v14.4.1.

## Управление в Telegram

- Покупатель открывает `/catalog`, выбирает пакет, вводит @username и подтверждает условия. Счёт привязан к заказу и покупателю. История доступна через `/orders`, помощь с платежами через `/paysupport`.
- Администратор открывает `/admin` и меняет настройки через `/set name`, `/set welcome`, `/set support`, `/set terms`. Разделы «Товары» и «Оформление» доступны кнопками в админке. Обложку можно заменить через `SHOP_BANNER_FILE` или скрыть значением `off`. Имя и аватар аккаунта самого бота настраиваются в BotFather.
- `/product stars 50 100`, `/product premium 3 950`, `/product ton 1 350` добавляют позиции. Последнее число — цена в XTR. `/unlist stars 50` скрывает позицию, `/set open 1` открывает магазин.
- `/pending` показывает очередь. Через `/order ID` администратор подтверждает выдачу или возврат. Через `/reconcile ID` проверяет исходную операцию Fragment. `/paymenterrors` показывает поступившие платежи, которые не удалось связать со счётом.

В админ-панели есть вкладки заказов, поиск по номеру, получателю или Telegram ID, история платежей XTR и статистика по базе магазина. Панель поддерживает таблицы Rich Messages, как в [Bedolaga](https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot/blob/0fb7453a1f606c51e4ad92fa155436eb06e04081/app/utils/rich_menu.py). Набор иконок составлен по [Telegram Premium Emojis](https://github.com/abbosahmad/telegram-premium-emojis#-catalog--top-25-essential-ids) и публичному набору [TgAndroidIcons](https://t.me/addemoji/TgAndroidIcons). На боте, которому Telegram разрешает пользовательские эмодзи, они отображаются в кнопках. При ошибке рендер переключается на обычное меню. Через `SHOP_RICH_MENUS=0` и `SHOP_PREMIUM_ICONS=0` их можно выключить.

## Управление из консоли

```sh
uv run fragment-bot --help
uv run fragment-bot doctor
uv run fragment-bot set name "Мой магазин"
uv run fragment-bot product stars 50 100
uv run fragment-bot demo-buy stars 50 @example
uv run fragment-bot orders --pending
uv run fragment-bot approve ORDER_ID --now
uv run fragment-bot order ORDER_ID
```

Пример `demo-buy` работает без токена Telegram. Без `--now` команда `approve` ставит заказ в очередь запущенного бота. Для live сессии есть `doctor --online`. Для возврата используйте `refund ORDER_ID`. При неизвестном результате выдачи проверьте `reconcile ORDER_ID`. После независимой проверки операции в блокчейне можно записать `resolve ORDER_ID settled --evidence "…"` или `resolve ORDER_ID not_broadcast --evidence "…"`. `settled` отражает факт обработки транзакции в блокчейне. Для статуса выдачи требуется подтверждение Fragment. После `not_broadcast` доступен возврат. Операция с неизвестным исходом автоматически повторно не отправляется. Команда `retry ORDER_ID` возвращает в очередь только попытку без записанной операции Fragment. Для сверки платежей вне заказов есть `payment-errors` и `refund-payment-error CHARGE_ID`.

## Внешняя подпись Stars

В live одобрение заказа Stars сохраняет полный `PreparedTransaction` и переводит заказ в статус «Транзакция подготовлена, ожидает внешней подписи». В Telegram откройте `/order ID` и нажмите «Скачать PreparedTransaction». В консоли `approve ID --now` выводит JSON в stdout, а `prepared ID` повторно выгружает сохранённый JSON. Если запущенный воркер успел забрать заказ первым, дождитесь статуса `prepared` и воспользуйтесь `prepared ID`.

В demo и test тот же сценарий возвращает имитацию `PreparedTransaction` с пустым `messages` и `raw.simulated=true`.

Подпишите и отправьте транзакцию live во внешнем кошельке до `valid_until` с адреса `sender_address`. Перед подписью проверьте все `messages`, включая комиссию SDK. Пример экспорта в формат TON Connect и передачи подписанного BOC в Fragment есть в [SDK](https://github.com/S1qwy/fragment-api-py/blob/04a0601b6cc7f11af0291683ddd773b2d96eb689/examples/external_signing.py). JSON доступен оператору магазина.

После подтверждения выдачи во Fragment запишите результат через `resolve-prepared ID delivered --evidence "…"` или `/resolveprepared ID delivered основание`. Если транзакцию не отправляли, дождитесь истечения срока, проверьте блокчейн и зафиксируйте `not_broadcast` тем же способом. После этого доступен возврат XTR.

## Интеграция платежей и источник логики

Проект опирается на архитектуру [Remnawave Bedolaga Telegram Bot](https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot). Разделение провайдера, общей обработки платежа и выдачи адаптировано из [платёжных модулей](https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot/tree/0fb7453a1f606c51e4ad92fa155436eb06e04081/app/services/payment), [обработчика Stars](https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot/blob/0fb7453a1f606c51e4ad92fa155436eb06e04081/app/handlers/stars_payments.py) и [маршрутизации вебхуков](https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot/blob/0fb7453a1f606c51e4ad92fa155436eb06e04081/app/webserver/payments.py). Навигация внутри сообщения и отдельные разделы админки основаны на [меню Bedolaga](https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot/blob/0fb7453a1f606c51e4ad92fa155436eb06e04081/app/handlers/menu.py). Реализация написана для этого магазина. Выдача работает через [fragment-api-py](https://github.com/S1qwy/fragment-api-py).

[Как добавить платёжный адаптер](docs/payments.md).

## Проверки

```sh
uv sync --locked --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
```
