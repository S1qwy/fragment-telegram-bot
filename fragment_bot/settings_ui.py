from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from .catalog import (
    DEMO_SETTINGS,
    SETTING_GROUPS,
    SETTING_HELP,
    SETTING_LABELS,
    SETTING_OPTIONS,
    SETTINGS,
)
from .config import Config, ShopError
from .store import Store
from .views import back, button, keys, resolve_icon_mode, rich_table, screen

GROUP_BY_SETTING = {key: group for group, (_, fields) in SETTING_GROUPS.items() for key in fields}


class SettingsInput(StatesGroup):
    value = State()


def display_value(key: str, value: str) -> str:
    return SETTING_OPTIONS.get(key, {}).get(value, value) or "Не задано"


def build_settings_router(config: Config, store: Store) -> Router:
    router = Router(name=__name__)
    router.message.filter(F.chat.type == "private", F.from_user)
    router.callback_query.filter(
        lambda query: isinstance(query.message, Message) and query.message.chat.type == "private"
    )

    def require_admin(user_id: int) -> None:
        if user_id not in config.admin_ids:
            raise ShopError("Настройки доступны администратору магазина.")

    def require_setting(key: str) -> None:
        if key not in GROUP_BY_SETTING:
            raise ShopError("Эта настройка недоступна.")

    def require_demo() -> None:
        if config.mode not in {"demo", "test"}:
            raise ShopError("Демо-профиль доступен в режимах demo и test.")

    async def render(
        message: Message,
        text: str,
        markup: InlineKeyboardMarkup,
        *,
        edit: bool = True,
        rich: str | None = None,
    ) -> None:
        icons = resolve_icon_mode(
            store.settings().get("premium_icons", "inherit"), config.premium_icons
        )
        await screen(message, text, markup, edit=edit, rich=rich, icons=icons)

    async def group_page(message: Message, group: str) -> None:
        section = SETTING_GROUPS.get(group)
        if section is None:
            raise ShopError("Этот раздел настроек недоступен.")
        title, fields = section
        settings = store.settings()
        rows = []
        for key in fields:
            value = settings[key]
            if key in SETTING_OPTIONS:
                summary = display_value(key, value)
            elif value:
                summary = " ".join(value.split())[:27]
            else:
                summary = "не задано"
            rows.append([button(f"{SETTING_LABELS[key]} · {summary}", f"setting:{key}")])
        await render(
            message,
            f"<b>{escape(title)}</b>",
            keys(*rows, back("settings", "К настройкам")),
        )

    @router.callback_query(F.data.in_({"admin:settings", "admin:brand", "settings"}))
    async def settings_menu(query: CallbackQuery, state: FSMContext) -> None:
        require_admin(query.from_user.id)
        await query.answer()
        await state.clear()
        if query.data == "admin:brand":
            await group_page(query.message, "branding")
            return
        settings = store.settings()
        rows = [
            [button(title, f"settings:group:{group}")]
            for group, (title, _) in SETTING_GROUPS.items()
        ]
        rows.append([button("💳 Все платёжные провайдеры", "gateways:0")])
        if config.mode in {"demo", "test"}:
            rows.append([button("🧪 Демо-профиль", "settings:demo", style="primary")])
        shop_status = display_value("open", settings["open"])
        await render(
            query.message,
            f"<b>⚙️ Настройки магазина</b>\n\n<blockquote>Витрина · {escape(shop_status)}</blockquote>",
            keys(*rows, back("admin", "Управление")),
            rich=rich_table("Настройки магазина", [("Витрина", shop_status)])
            if config.rich_menus
            else None,
        )

    @router.callback_query(F.data.startswith("settings:group:"))
    async def open_group(query: CallbackQuery, state: FSMContext) -> None:
        require_admin(query.from_user.id)
        await query.answer()
        await state.clear()
        await group_page(query.message, query.data.split(":", 2)[2])

    async def setting_page(
        message: Message,
        key: str,
        state: FSMContext,
        *,
        edit: bool = True,
        saved: bool = False,
    ) -> None:
        require_setting(key)
        settings = store.settings()
        current = settings[key]
        default, limit = SETTINGS[key]
        rows = []
        if options := SETTING_OPTIONS.get(key):
            for option, label in options.items():
                selected = current == option
                rows.append(
                    [
                        button(
                            f"✅ {label}" if selected else label,
                            f"settings:value:{key}:{option}",
                            style="success" if selected else None,
                        )
                    ]
                )
            await state.clear()
            instruction = "Выберите значение."
        elif saved:
            await state.clear()
            rows.append([button("✏️ Изменить", f"setting:{key}", style="primary")])
            instruction = "Значение сохранено."
        else:
            await state.set_state(SettingsInput.value)
            await state.set_data({"setting": key})
            instruction = f"Отправьте новое значение одним сообщением. Лимит — {limit} симв."
        if key == "home_buttons" and current:
            rows.append([button("➖ Скрыть дополнительные кнопки", "settings:clear:home_buttons")])
        if current != default:
            reset_label = "Очистить" if not default else "Сбросить"
            rows.append([button(f"↩️ {reset_label}", f"settings:reset:{key}")])
        rows.append(back(f"settings:group:{GROUP_BY_SETTING[key]}", "К разделу"))
        status = "\n\n✅ Сохранено." if saved and key in SETTING_OPTIONS else ""
        help_text = SETTING_HELP.get(key)
        guidance = f"\n\n{escape(help_text)}" if help_text else ""
        await render(
            message,
            f"<b>{escape(SETTING_LABELS[key])}</b>\n\n"
            f"<blockquote>{escape(display_value(key, current))}</blockquote>"
            f"{guidance}\n\n{instruction}{status}",
            keys(*rows),
            edit=edit,
        )

    @router.callback_query(F.data.startswith("setting:"))
    async def open_setting(query: CallbackQuery, state: FSMContext) -> None:
        require_admin(query.from_user.id)
        await query.answer()
        await setting_page(query.message, query.data.split(":", 1)[1], state)

    @router.callback_query(F.data.startswith("settings:value:"))
    async def choose_value(query: CallbackQuery, state: FSMContext) -> None:
        require_admin(query.from_user.id)
        await query.answer()
        _, _, key, value = query.data.split(":", 3)
        require_setting(key)
        if value not in SETTING_OPTIONS.get(key, {}):
            raise ShopError("Это значение недоступно для настройки.")
        store.configure(key, value)
        await setting_page(query.message, key, state, saved=True)

    @router.message(SettingsInput.value, F.text, ~F.text.startswith("/"))
    async def save_value(message: Message, state: FSMContext) -> None:
        require_admin(message.from_user.id)
        key = (await state.get_data())["setting"]
        require_setting(key)
        store.configure(key, message.text)
        await setting_page(message, key, state, edit=False, saved=True)

    @router.callback_query(
        F.data.startswith("settings:reset:") | (F.data == "settings:clear:home_buttons")
    )
    async def confirm_reset(query: CallbackQuery, state: FSMContext) -> None:
        require_admin(query.from_user.id)
        await query.answer()
        key = query.data.split(":", 2)[2]
        require_setting(key)
        await state.clear()
        clearing = query.data == "settings:clear:home_buttons"
        value = display_value(key, "" if clearing else SETTINGS[key][0])
        action = "Очистить" if clearing or not SETTINGS[key][0] else "Сбросить"
        callback = "clear-ok" if clearing else "reset-ok"
        await render(
            query.message,
            f"<b>↩️ {action} «{escape(SETTING_LABELS[key])}»?</b>\n\n"
            f"<blockquote>{escape(value)}</blockquote>",
            keys(
                [button(action, f"settings:{callback}:{key}", style="danger")],
                back(f"setting:{key}", "К настройке"),
            ),
        )

    @router.callback_query(
        F.data.startswith("settings:reset-ok:") | (F.data == "settings:clear-ok:home_buttons")
    )
    async def reset_setting(query: CallbackQuery, state: FSMContext) -> None:
        require_admin(query.from_user.id)
        await query.answer()
        key = query.data.split(":", 2)[2]
        require_setting(key)
        if query.data == "settings:clear-ok:home_buttons":
            store.configure(key, "")
        else:
            store.reset_setting(key)
        await setting_page(query.message, key, state, saved=True)

    @router.callback_query(F.data == "settings:demo")
    async def demo_preset(query: CallbackQuery, state: FSMContext) -> None:
        require_admin(query.from_user.id)
        require_demo()
        await query.answer()
        await state.clear()
        fields = "\n".join(
            f"<b>{escape(SETTING_LABELS[key])}</b> · {escape(' '.join(display_value(key, value).split())[:90])}"
            for key, value in DEMO_SETTINGS.items()
        )
        await render(
            query.message,
            "<b>🧪 Демо-профиль</b>\n\n"
            "Готовые тестовые тексты, вид витрины и настройки оформления.\n\n"
            f"<blockquote>{fields}</blockquote>",
            keys(
                [
                    button(
                        "✅ Заполнить стандартные поля", "settings:demo:defaults", style="primary"
                    )
                ],
                [button("Заменить все поля профиля", "settings:demo:replace")],
                back("settings", "К настройкам"),
            ),
        )

    @router.callback_query(F.data == "settings:demo:replace")
    async def confirm_demo_replace(query: CallbackQuery, state: FSMContext) -> None:
        require_admin(query.from_user.id)
        require_demo()
        await query.answer()
        await state.clear()
        await render(
            query.message,
            "<b>🧪 Заменить поля демо-профиля?</b>\n\n"
            "Тексты и оформление из списка заменятся тестовыми значениями.",
            keys(
                [button("Заменить", "settings:demo:apply", style="danger")],
                back("settings:demo", "К демо-профилю"),
            ),
        )

    @router.callback_query(F.data.in_({"settings:demo:defaults", "settings:demo:apply"}))
    async def apply_demo_preset(query: CallbackQuery, state: FSMContext) -> None:
        require_admin(query.from_user.id)
        require_demo()
        await query.answer()
        await state.clear()
        changed = store.apply_demo_settings(overwrite=query.data == "settings:demo:apply")
        names = "\n".join(f"• {escape(SETTING_LABELS[key])}" for key in changed)
        result = f"Записаны настройки\n\n{names}" if names else "Поля уже заполнены."
        await render(
            query.message,
            f"<b>🧪 Демо-профиль</b>\n\n{result}",
            keys(
                [button("🏪 Открыть витрину", "menu", style="primary")],
                back("settings", "К настройкам"),
            ),
        )

    return router
