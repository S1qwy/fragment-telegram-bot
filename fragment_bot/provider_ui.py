from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .config import ShopError
from .provider_config import FIELDS, PROVIDERS, ProviderConfig, provider_fields
from .views import back, button, keys, resolve_icon_mode, screen


class ProviderInput(StatesGroup):
    value = State()


def provider_router(config, store):
    router = Router(name=__name__)
    router.message.filter(F.chat.type == "private", F.from_user)
    router.callback_query.filter(
        lambda q: isinstance(q.message, Message) and q.message.chat.type == "private"
    )
    settings = ProviderConfig(store)

    def admin(user):
        if user.id not in config.admin_ids:
            raise ShopError("Доступно только администратору.")

    async def render(message, text, markup, edit=True):
        await screen(
            message,
            text,
            markup,
            edit=edit,
            icons=resolve_icon_mode(store.settings()["premium_icons"], config.premium_icons),
        )

    @router.callback_query(F.data.startswith("gateways:"))
    async def gateway_list(query: CallbackQuery, state: FSMContext):
        admin(query.from_user)
        await query.answer()
        await state.clear()
        page = max(0, min(int(query.data.split(":")[1]), (len(PROVIDERS) - 1) // 8))
        names = sorted(PROVIDERS)
        rows = [[button(name, f"gateway:{name}:0")] for name in names[page * 8 : page * 8 + 8]]
        arrows = []
        if page:
            arrows.append(button("‹", f"gateways:{page - 1}"))
        if (page + 1) * 8 < len(names):
            arrows.append(button("›", f"gateways:{page + 1}"))
        if arrows:
            rows.append(arrows)
        await render(
            query.message,
            f"<b>💳 Платёжные провайдеры</b>\n\nВсе {len(names)} интеграций исходника.\nДля внешних заказов через CLI заполните ключи, CHECKOUT_RATE и включите ENABLED.\nCHECKOUT_RATE — единиц валюты за XTR; итог округляется до сотых.\nОплата в Telegram — XTR. Apple IAP требует нативное приложение.\nНастроенные ключи не означают проверенный платёж.",
            keys(*rows, back("settings")),
        )

    async def gateway_page(message, provider, page=0, edit=True):
        fields = provider_fields(provider)
        page = max(0, min(page, max(len(fields) - 1, 0) // 7))
        display = settings.display(provider)
        rows = []
        for key in fields[page * 7 : page * 7 + 7]:
            value = str(display[key]) if display[key] is not None else "не задано"
            rows.append(
                [
                    button(
                        f"{key.removeprefix(provider.upper() + '_')} · {value[:24]}",
                        f"gwfield:{sorted(FIELDS).index(key)}",
                    )
                ]
            )
        arrows = []
        if page:
            arrows.append(button("‹", f"gateway:{provider}:{page - 1}"))
        if (page + 1) * 7 < len(fields):
            arrows.append(button("›", f"gateway:{provider}:{page + 1}"))
        if arrows:
            rows.append(arrows)
        await render(
            message,
            f"<b>{escape(provider)}</b>\n\nПараметров: {len(fields)} · страница {page + 1}.\nКлючи зашифрованы в базе. Сохранение действует для следующего API-запроса.",
            keys(*rows, back("gateways:0", "Все провайдеры")),
            edit=edit,
        )

    @router.callback_query(F.data.startswith("gateway:"))
    async def gateway(query: CallbackQuery, state: FSMContext):
        admin(query.from_user)
        await query.answer()
        await state.clear()
        _, provider, page = query.data.split(":")
        await gateway_page(query.message, provider, int(page))

    @router.callback_query(F.data.startswith("gwfield:"))
    async def field(query: CallbackQuery, state: FSMContext):
        admin(query.from_user)
        await query.answer()
        key = sorted(FIELDS)[int(query.data.split(":", 1)[1])]
        if key not in FIELDS:
            raise ShopError("Неизвестный параметр.")
        provider = next(
            (
                p
                for p in sorted(PROVIDERS, key=len, reverse=True)
                if key.startswith(p.upper() + "_")
            ),
            None,
        )
        if provider is None:
            raise ShopError("Параметр недоступен.")
        await state.set_state(ProviderInput.value)
        await state.set_data({"key": key, "provider": provider})
        help_text = (
            "Секрет скрыт; сообщение с новым значением будет удалено."
            if FIELDS[key]["secret"]
            else "Отправьте новое значение."
        )
        await render(
            query.message,
            f"<b>{escape(key)}</b>\n\nТип: {FIELDS[key]['type']}\n{help_text}\nДля сброса отправьте <code>/providerreset</code>.",
            keys(back(f"gateway:{provider}:0")),
        )

    @router.message(ProviderInput.value, F.text)
    async def save(message: Message, state: FSMContext):
        admin(message.from_user)
        data = await state.get_data()
        if message.text == "/providerreset":
            settings.reset(data["key"])
        elif message.text.startswith("/"):
            return
        else:
            settings.set(data["key"], message.text)
            if FIELDS[data["key"]]["secret"]:
                try:
                    await message.delete()
                except TelegramAPIError:
                    pass
        await state.clear()
        await gateway_page(message, data["provider"], edit=False)

    return router
