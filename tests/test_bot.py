from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramNotFound
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageCaption,
    EditMessageMedia,
    EditMessageText,
    SendDocument,
    SendMessage,
    SendPhoto,
    SendRichMessage,
)
from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
    PhotoSize,
    PreCheckoutQuery,
    SuccessfulPayment,
    Update,
    User,
)
from FragmentAPI import PreparedTransaction

from fragment_bot.bot import handle_payment, shop_router
from fragment_bot.catalog import Product
from fragment_bot.config import Config, ShopError
from fragment_bot.fulfillment import Fulfillment
from fragment_bot.payments import Checkout, DemoGateway, TelegramStarsGateway, make_bot
from fragment_bot.store import Store

BUYER = User(id=42, is_bot=False, first_name="Buyer")
ADMIN = User(id=99, is_bot=False, first_name="Admin")


def chat_message(text=None, *, user=BUYER, payment=None, photo=None):
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=user.id, type="private"),
        from_user=user,
        text=text,
        successful_payment=payment,
        photo=photo,
    )


async def test_demo_bot_checkout_and_admin_actions(tmp_path):
    config = Config("demo", tmp_path, admin_ids=frozenset({ADMIN.id}), token="123:dummy")
    store = Store(tmp_path, config.mode)
    bot = Bot(config.token)
    async with bot:
        bot.session.make_request = AsyncMock(return_value=True)
        checkout = Checkout(store, DemoGateway(bot))
        dispatcher = Dispatcher()
        fulfillment = Fulfillment(config, store)
        dispatcher.include_router(shop_router(config, store, checkout, fulfillment))

        async def callback(data, *, user=BUYER, photo=None):
            await dispatcher.feed_update(
                bot,
                Update(
                    update_id=1,
                    callback_query=CallbackQuery(
                        id="callback",
                        from_user=user,
                        chat_instance="chat",
                        data=data,
                        message=chat_message(user=user, photo=photo),
                    ),
                ),
                schedule=None,
            )

        await dispatcher.feed_update(bot, Update(update_id=1, message=chat_message("/start")))
        home = bot.session.make_request.await_args.args[1]
        assert isinstance(home, SendPhoto)
        assert "<b>Fragment Shop</b>" in home.caption
        assert home.reply_markup.inline_keyboard[0][0].style == "primary"
        assert home.reply_markup.inline_keyboard[0][0].icon_custom_emoji_id
        assert home.reply_markup.inline_keyboard[0][0].text == "Купить"
        assert all(
            button.callback_data != "admin"
            for row in home.reply_markup.inline_keyboard
            for button in row
        )

        photo = [PhotoSize(file_id="image", file_unique_id="cover", width=2172, height=724)]
        await callback("catalog", photo=photo)
        categories = bot.session.make_request.await_args.args[1]
        assert isinstance(categories, EditMessageCaption)
        assert categories.reply_markup.inline_keyboard[0][0].callback_data == "category:stars"
        await callback("category:stars", photo=photo)
        packages = bot.session.make_request.await_args.args[1]
        assert packages.reply_markup.inline_keyboard[0][0].callback_data == "buy:stars:50"
        await callback("buy:stars:50", photo=photo)
        await dispatcher.feed_update(bot, Update(update_id=2, message=chat_message("@sample")))
        sent = [
            call.args[1]
            for call in bot.session.make_request.await_args_list
            if isinstance(call.args[1], SendMessage)
        ]
        preview = sent[-1]
        assert isinstance(preview, SendMessage)
        assert "50" in preview.text and "@sample" in preview.text
        nonce_button = preview.reply_markup.inline_keyboard[0][0]
        await callback(nonce_button.callback_data)
        sent = [
            call.args[1]
            for call in bot.session.make_request.await_args_list
            if isinstance(call.args[1], SendMessage)
        ]
        invoice = sent[-1]
        demo_button = invoice.reply_markup.inline_keyboard[0][0]
        await callback(demo_button.callback_data)
        order = store.orders(buyer=BUYER.id)[0]
        assert order.state == "paid"
        try:
            await callback(f"act:approve:{order.id}")
        except ShopError:
            pass
        assert store.get(order.id).state == "paid"
        await callback("admin", user=ADMIN)
        dashboard = bot.session.make_request.await_args.args[1]
        assert dashboard.reply_markup.inline_keyboard[0][0].callback_data == "admin:queue"
        assert isinstance(dashboard, EditMessageText)
        assert "<table>" in dashboard.rich_message.html
        await callback("admin:search", user=ADMIN)
        await dispatcher.feed_update(
            bot, Update(update_id=8, message=chat_message("@sample", user=ADMIN))
        )
        search = bot.session.make_request.await_args.args[1]
        assert (
            search.reply_markup.inline_keyboard[0][0].callback_data
            == f"adminorder:{order.id}:all:0"
        )
        await callback("admin:payments", user=ADMIN)
        payments = bot.session.make_request.await_args.args[1]
        assert "100 ⭐" in payments.rich_message.html
        await callback("adminorders:payments:0", user=ADMIN)
        payment = bot.session.make_request.await_args.args[1]
        assert (
            payment.reply_markup.inline_keyboard[0][0].callback_data
            == f"adminorder:{order.id}:payments:0"
        )
        await callback("admin:queue", user=ADMIN)
        queue = bot.session.make_request.await_args.args[1]
        assert queue.reply_markup.inline_keyboard[0][0].callback_data == f"adminorder:{order.id}"
        await callback(f"adminorder:{order.id}", user=ADMIN)
        await callback(f"ask:approve:{order.id}", user=ADMIN)
        await callback(f"act:approve:{order.id}", user=ADMIN)
        assert store.get(order.id).state == "queued"
        await fulfillment.deliver(store.claim(order.id))
        with pytest.raises(ShopError, match="администратору"):
            await callback(f"prepared:{order.id}")
        await callback(f"prepared:{order.id}", user=ADMIN)
        download = bot.session.make_request.await_args.args[1]
        assert isinstance(download, SendDocument)
        assert download.chat_id == ADMIN.id
        assert PreparedTransaction.model_validate_json(
            download.document.data
        ) == fulfillment.prepared(order.id)
        await callback("admin:brand", user=ADMIN)
        await callback("setting:name", user=ADMIN)
        await dispatcher.feed_update(
            bot, Update(update_id=3, message=chat_message("Store <3", user=ADMIN))
        )
        await callback("menu", user=ADMIN)
        updated_home = bot.session.make_request.await_args.args[1]
        assert isinstance(updated_home, EditMessageMedia)
        assert "<b>Store &lt;3</b>" in updated_home.media.caption
        await callback("admin:catalog", user=ADMIN)
        await callback("adminitem:stars:50", user=ADMIN)
        await callback("adminprice:stars:50", user=ADMIN)
        await dispatcher.feed_update(
            bot, Update(update_id=4, message=chat_message("120", user=ADMIN))
        )
        assert Product("stars", 50, 120) in store.products()
        await callback("adminhide:stars:50", user=ADMIN)
        assert Product("stars", 50, 120) in store.products()
        await callback("adminconfirmhide:stars:50", user=ADMIN)
        assert Product("stars", 50, 120) not in store.products()
        calls = [call.args[1] for call in bot.session.make_request.await_args_list]
        assert any(isinstance(call, AnswerCallbackQuery) for call in calls)
        await dispatcher.fsm.close()


async def test_telegram_xtr_invoice_pre_checkout_and_receipt(tmp_path):
    proxy = "http://127.0.0.1:10809"
    config = Config("test", tmp_path, token="123:dummy", telegram_proxy=proxy)
    store = Store(tmp_path, config.mode)
    bot = make_bot(config)
    async with bot:
        assert "/test/" in bot.session.api.api_url(bot.token, "getMe")
        assert bot.session.proxy == proxy
        bot.session.make_request = AsyncMock(return_value=True)
        checkout = Checkout(store, TelegramStarsGateway(bot))
        order = checkout.create(BUYER.id, Product("premium", 3, 950), "@sample", "")
        await checkout.gateway.checkout(order)
        invoice = bot.session.make_request.await_args.args[1]
        assert invoice.currency == "XTR"
        assert invoice.payload == order.id
        assert invoice.prices[0].amount == 950
        query = PreCheckoutQuery(
            id="query",
            from_user=BUYER,
            currency="XTR",
            total_amount=950,
            invoice_payload=order.id,
        )
        assert await handle_payment(bot, Update(update_id=1, pre_checkout_query=query), checkout)
        assert bot.session.make_request.await_args.args[1].ok
        assert store.get(order.id).state == "unpaid"
        payment = SuccessfulPayment(
            currency="XTR",
            total_amount=950,
            invoice_payload=order.id,
            telegram_payment_charge_id="charge",
            provider_payment_charge_id="",
        )
        update = Update(update_id=2, message=chat_message(payment=payment))
        assert await handle_payment(bot, update, checkout)
        assert await handle_payment(bot, update, checkout)
        assert store.get(order.id).state == "paid"
        assert store.get(order.id).charge_id == "charge"
        assert not store.payment_exceptions()


async def test_start_falls_back_to_menu_when_banner_upload_times_out(tmp_path):
    config = Config("demo", tmp_path, token="123:dummy")
    store = Store(tmp_path, config.mode)
    bot = Bot(config.token)
    async with bot:

        async def request(_bot, method, timeout=None):
            if isinstance(method, SendPhoto):
                assert timeout == 4
                raise TelegramNetworkError(method=method, message="Request timeout error")
            return True

        bot.session.make_request = AsyncMock(side_effect=request)
        checkout = Checkout(store, DemoGateway(bot))
        dispatcher = Dispatcher()
        dispatcher.include_router(shop_router(config, store, checkout, Fulfillment(config, store)))
        try:
            await dispatcher.feed_update(bot, Update(update_id=1, message=chat_message("/start")))
            photo, menu = (call.args[1] for call in bot.session.make_request.await_args_list)
            assert isinstance(photo, SendPhoto)
            assert isinstance(menu, SendMessage)
            assert menu.text == photo.caption
            assert menu.reply_markup == photo.reply_markup
        finally:
            await dispatcher.fsm.close()


async def test_admin_falls_back_from_rich_and_unavailable_custom_icons(tmp_path):
    config = Config("demo", tmp_path, token="123:dummy", admin_ids=frozenset({ADMIN.id}))
    store = Store(tmp_path, config.mode)
    bot = Bot(config.token)
    async with bot:

        async def request(_bot, method, timeout=None):
            if isinstance(method, SendRichMessage):
                raise TelegramNotFound(method=method, message="method not found")
            if (
                isinstance(method, SendMessage)
                and method.reply_markup.inline_keyboard[0][0].icon_custom_emoji_id
            ):
                raise TelegramBadRequest(method=method, message="BUTTON_CUSTOM_EMOJI_INVALID")
            return True

        bot.session.make_request = AsyncMock(side_effect=request)
        checkout = Checkout(store, DemoGateway(bot))
        dispatcher = Dispatcher()
        dispatcher.include_router(shop_router(config, store, checkout, Fulfillment(config, store)))
        try:
            await dispatcher.feed_update(
                bot, Update(update_id=1, message=chat_message("/admin", user=ADMIN))
            )
            rich, premium, classic = (
                call.args[1] for call in bot.session.make_request.await_args_list
            )
            assert isinstance(rich, SendRichMessage)
            assert isinstance(premium, SendMessage)
            assert classic.reply_markup.inline_keyboard[0][0].text == "🧾 Заказы"
            assert classic.reply_markup.inline_keyboard[0][0].callback_data == "admin:queue"
        finally:
            await dispatcher.fsm.close()


async def test_storefront_applies_menu_order_documents_and_gift_policy(tmp_path):
    config = Config("demo", tmp_path, token="123:dummy")
    store = Store(tmp_path, config.mode)
    store.configure("home_style", "text")
    store.configure("premium_icons", "premium_only")
    store.configure("buy_label", "Выбрать пакет")
    store.configure("home_buttons", "privacy,orders")
    store.configure("privacy", "Текст конфиденциальности")
    store.configure("category_order", "ton,stars,premium")
    store.configure("allow_self", "0")
    buyer = BUYER.model_copy(update={"username": "testbuyer"})

    async with Bot(config.token) as bot:
        bot.session.make_request = AsyncMock(return_value=True)
        checkout = Checkout(store, DemoGateway(bot))
        dispatcher = Dispatcher()
        dispatcher.include_router(shop_router(config, store, checkout, Fulfillment(config, store)))

        async def callback(data):
            await dispatcher.feed_update(
                bot,
                Update(
                    update_id=1,
                    callback_query=CallbackQuery(
                        id="callback",
                        from_user=buyer,
                        chat_instance="chat",
                        data=data,
                        message=chat_message(user=buyer),
                    ),
                ),
            )

        try:
            await dispatcher.feed_update(
                bot, Update(update_id=1, message=chat_message("/start", user=buyer))
            )
            home = bot.session.make_request.await_args.args[1]
            assert isinstance(home, SendMessage)
            assert home.reply_markup.inline_keyboard[0][0].text == "Выбрать пакет"
            assert [b.callback_data for b in home.reply_markup.inline_keyboard[1]] == [
                "privacy",
                "orders",
            ]

            await callback("privacy")
            assert "Текст конфиденциальности" in bot.session.make_request.await_args.args[1].text
            await callback("catalog")
            assert (
                bot.session.make_request.await_args.args[1]
                .reply_markup.inline_keyboard[0][0]
                .callback_data
                == "category:ton"
            )
            await callback("buy:stars:50")
            recipient = bot.session.make_request.await_args.args[1]
            assert all(
                b.callback_data != "recipient:self"
                for row in recipient.reply_markup.inline_keyboard
                for b in row
            )
            await dispatcher.feed_update(
                bot, Update(update_id=2, message=chat_message("@giftuser", user=buyer))
            )
            preview = next(
                call.args[1]
                for call in reversed(bot.session.make_request.await_args_list)
                if isinstance(call.args[1], SendMessage)
            )
            confirmation = preview.reply_markup.inline_keyboard[0][0].callback_data
            store.configure("allow_gifts", "0")
            with pytest.raises(ShopError, match="только для вашего"):
                await callback(confirmation)
            assert store.orders(buyer=buyer.id) == []
        finally:
            await dispatcher.fsm.close()
