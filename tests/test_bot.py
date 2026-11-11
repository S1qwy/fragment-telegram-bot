from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import AnswerCallbackQuery, SendDocument, SendMessage
from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
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


def chat_message(text=None, *, user=BUYER, payment=None):
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=user.id, type="private"),
        from_user=user,
        text=text,
        successful_payment=payment,
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

        async def callback(data, *, user=BUYER):
            await dispatcher.feed_update(
                bot,
                Update(
                    update_id=1,
                    callback_query=CallbackQuery(
                        id="callback",
                        from_user=user,
                        chat_instance="chat",
                        data=data,
                        message=chat_message(user=user),
                    ),
                ),
                schedule=None,
            )

        await callback("catalog")
        await callback("buy:stars:50")
        await dispatcher.feed_update(bot, Update(update_id=2, message=chat_message("@sample")))
        preview = bot.session.make_request.await_args.args[1]
        assert isinstance(preview, SendMessage)
        assert "50" in preview.text and "@sample" in preview.text
        nonce_button = preview.reply_markup.inline_keyboard[0][0]
        await callback(nonce_button.callback_data)
        invoice = bot.session.make_request.await_args.args[1]
        demo_button = invoice.reply_markup.inline_keyboard[0][0]
        await callback(demo_button.callback_data)
        order = store.orders(buyer=BUYER.id)[0]
        assert order.state == "paid"
        try:
            await callback(f"act:approve:{order.id}")
        except ShopError:
            pass
        assert store.get(order.id).state == "paid"
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
