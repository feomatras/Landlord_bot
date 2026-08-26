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
            [InlineKeyboardButton("Неоплаченные счета", callback_data="unpaid")],
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
    Каждая кнопка имеет явную подпись услуги, сумму и статус оплаты.
    """
    services = ("water", "electricity", "gas", "tko", "uk", "caprepair")
    rows = []
    for service in services:
        if service not in amounts or amounts[service] == 0:
            continue
        paid = bool(statuses.get(service, {}).get("paid", 0)) if service in statuses else False
        icon = "✅" if paid else "⬜️"
        label = f"{icon} {service_name(service)}: {amounts[service]:.2f} руб."
        rows.append(
            [InlineKeyboardButton(label, callback_data=f"pay:{reading_id}:{service}")]
        )
    if not rows:
        rows.append([InlineKeyboardButton("Назад", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def quarter_navigation_keyboard(
    year: int,
    quarter: int,
    available_years: list[int],
    view: str,
    flat_id: int | None = None,
    extra_rows: list[list[InlineKeyboardButton]] | None = None,
) -> InlineKeyboardMarkup:
    """
    Клавиатура навигации по кварталам и годам.
    view: 'journal' или 'history' — определяет callback-префикс.
    flat_id передаётся в callback, чтобы сохранять контекст квартиры.
    extra_rows: дополнительные ряды кнопок (например, кнопки оплаты),
                вставляются перед кнопкой «Назад».
    """
    flat_prefix = f"{flat_id}:" if flat_id is not None else ""
    rows = []
    # Навигация по кварталам
    prev_q = quarter - 1 if quarter > 1 else 4
    prev_y = year if quarter > 1 else year - 1
    next_q = quarter + 1 if quarter < 4 else 1
    next_y = year if quarter < 4 else year + 1
    rows.append([
        InlineKeyboardButton("◀️ Предыдущий квартал", callback_data=f"qnav:{view}:{flat_prefix}{prev_y}:{prev_q}"),
        InlineKeyboardButton("Следующий квартал ▶️", callback_data=f"qnav:{view}:{flat_prefix}{next_y}:{next_q}"),
    ])
    # Кнопки выбора года
    year_buttons = []
    for y in sorted(set(available_years + [year]), reverse=True):
        label = f"{y}" + (" ←" if y == year else "")
        year_buttons.append(InlineKeyboardButton(label, callback_data=f"qnav:{view}:{flat_prefix}{y}:{quarter}"))
    # Разбиваем годы по рядам (максимум 4 в ряд)
    for i in range(0, len(year_buttons), 4):
        rows.append(year_buttons[i:i + 4])
    if extra_rows:
        rows.extend(extra_rows)
    rows.append([InlineKeyboardButton("Назад", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def history_detail_keyboard(
    reading_id: int,
    statuses: dict[str, object],
    amounts: dict[str, float],
    year: int,
    quarter: int,
    flat_id: int | None = None,
) -> InlineKeyboardMarkup:
    """
    Клавиатура для детального отчёта из истории.
    Показывает кнопки оплаты по каждой услуге, кнопку «Оплатить всё»
    и навигацию назад к кварталу.
    """
    flat_prefix = f"{flat_id}:" if flat_id is not None else ""
    services = ("water", "electricity", "gas", "tko", "uk", "caprepair")
    rows = []
    for service in services:
        if service not in amounts or amounts[service] == 0:
            continue
        paid = bool(statuses.get(service, {}).get("paid", 0)) if service in statuses else False
        icon = "✅" if paid else "⬜️"
        label = f"{icon} {service_name(service)}: {amounts[service]:.2f} руб."
        rows.append(
            [InlineKeyboardButton(label, callback_data=f"payhist:{reading_id}:{service}:{flat_prefix}{year}:{quarter}")]
        )
    all_paid = all(
        bool(statuses.get(s, {}).get("paid", 0))
        for s in services
        if s in amounts and amounts[s] > 0
    ) if statuses else False
    if rows:
        toggle_label = "❌ Снять все оплаты" if all_paid else "✅ Отметить все оплаченными"
        rows.append([
            InlineKeyboardButton(toggle_label, callback_data=f"payhistall:{reading_id}:{flat_prefix}{year}:{quarter}")
        ])
    rows.append([
        InlineKeyboardButton("◀️ Назад к кварталу", callback_data=f"qnav:history:{flat_prefix}{year}:{quarter}")
    ])
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
