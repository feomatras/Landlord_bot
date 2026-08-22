from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from .formatting import service_name


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Отмена", callback_data="cancel")]]
    )


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Квартиры", callback_data="flats"),
                InlineKeyboardButton("Пользователи", callback_data="users"),
            ],
            [InlineKeyboardButton("Пригласить квартиранта", callback_data="invite")],
            [
                InlineKeyboardButton("Тарифы", callback_data="tariffs"),
                InlineKeyboardButton("Начальные показания", callback_data="initial"),
            ],
            [
                InlineKeyboardButton("Внести УК", callback_data="uk"),
                InlineKeyboardButton("Капремонт", callback_data="caprepair"),
            ],
            [
                InlineKeyboardButton("История", callback_data="history"),
                InlineKeyboardButton("Журнал изменений", callback_data="audit"),
            ],
            [InlineKeyboardButton("Инструкция", callback_data="help")],
        ]
    )


def tariff_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Вода", callback_data="tariff:water"),
                InlineKeyboardButton("Электричество", callback_data="tariff:electricity"),
            ],
            [
                InlineKeyboardButton("Газ", callback_data="tariff:gas"),
                InlineKeyboardButton("ТКО", callback_data="tariff:tko"),
            ],
            [InlineKeyboardButton("Капремонт", callback_data="tariff:caprepair")],
            [InlineKeyboardButton("Назад", callback_data="menu")],
        ]
    )


def initial_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Электричество", callback_data="initial:electricity"),
                InlineKeyboardButton("Вода", callback_data="initial:water"),
            ],
            [InlineKeyboardButton("Газ", callback_data="initial:gas")],
            [InlineKeyboardButton("Назад", callback_data="menu")],
        ]
    )


def tenant_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Передать показания", callback_data="reading:start")],
            [
                InlineKeyboardButton("История начислений", callback_data="tenant:history"),
                InlineKeyboardButton("Актуальные тарифы", callback_data="tenant:tariffs"),
            ],
            [InlineKeyboardButton("Изменения по квартире", callback_data="audit")],
            [InlineKeyboardButton("Инструкция", callback_data="help")],
        ]
    )


def report_keyboard(reading_id: int, statuses: dict[str, object], amounts: dict[str, float]) -> InlineKeyboardMarkup:
    """
    Создаёт клавиатуру для отчёта администратора.
    В кнопках отображаются название услуги, сумма и статус оплаты.
    """
    services = ("water", "electricity", "gas", "tko", "uk", "caprepair")
    rows = []
    for service in services:
        # Пропускаем услуги с нулевой суммой (чтобы не засорять интерфейс)
        if service not in amounts or amounts[service] == 0:
            continue
        paid = bool(statuses.get(service, {}).get("paid", 0)) if service in statuses else False
        label = f"{service_name(service)}: {amounts[service]:.2f} руб. {'✅' if paid else '⬜️'}"
        rows.append(
            [InlineKeyboardButton(label, callback_data=f"pay:{reading_id}:{service}")]
        )
    # Если все суммы нулевые, показываем кнопку "Назад"
    if not rows:
        rows.append([InlineKeyboardButton("Назад", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def confirm_remove_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Удалить и очистить историю", callback_data=f"remove:confirm:{user_id}"),
                InlineKeyboardButton("Отмена", callback_data="cancel"),
            ]
        ]
    )
