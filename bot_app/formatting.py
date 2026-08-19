from __future__ import annotations

import re
from datetime import date


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