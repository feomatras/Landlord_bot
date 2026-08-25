from __future__ import annotations

import re
from datetime import date, datetime


MONTH_NAMES = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)

SERVICE_NAMES = {
    "water": "Вода",
    "electricity": "Электричество",
    "gas": "Газ",
    "tko": "ТКО",
    "uk": "УК",
    "caprepair": "Капремонт",
}


def money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def month_label(month: str) -> str:
    month_number, year = (int(part) for part in month.split("."))
    return f"{MONTH_NAMES[month_number - 1]} {year}"


def parse_month(value: str) -> str | None:
    normalized = value.strip().replace("/", ".")
    match = re.fullmatch(r"(\d{1,2})\.(\d{4})", normalized)
    if not match:
        return None
    month, year = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return None
    return f"{month:02d}.{year:04d}"


def current_month(today: date | None = None) -> str:
    value = today or date.today()
    return f"{value.month:02d}.{value.year:04d}"


def display_date(value: str) -> str:
    try:
        return value[:10].split("-")[::-1]
    except Exception:
        return value


def actor_name(user) -> str:
    name = (user.first_name or "").strip()
    if name:
        return name
    username = (user.username or "").strip().lstrip("@")
    return f"@{username}" if username else str(user.user_id)


def service_name(service: str) -> str:
    return SERVICE_NAMES.get(service, service)


# --- Квартальная навигация ---

def quarter_of_month(month_str: str) -> int:
    """Возвращает номер квартала (1-4) для месяца в формате ММ.ГГГГ."""
    month_number = int(month_str.split(".")[0])
    return (month_number - 1) // 3 + 1


def current_quarter(today: date | None = None) -> int:
    """Возвращает текущий квартал (1-4)."""
    value = today or date.today()
    return (value.month - 1) // 3 + 1


def quarter_label(quarter: int, year: int) -> str:
    """Человекочитаемое название квартала, например «1-й квартал 2026»."""
    return f"{quarter}-й квартал {year}"


def months_in_quarter(quarter: int) -> list[int]:
    """Список номеров месяцев (1-12) для заданного квартала."""
    return [(quarter - 1) * 3 + 1, (quarter - 1) * 3 + 2, (quarter - 1) * 3 + 3]


def year_of_month(month_str: str) -> int:
    """Возвращает год из строки ММ.ГГГГ."""
    return int(month_str.split(".")[1])


def format_datetime(iso_str: str) -> str:
    """Форматирует ISO-строку в вид ДД.ММ.ГГГГ ЧЧ:ММ."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return iso_str