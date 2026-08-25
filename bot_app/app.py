from __future__ import annotations

import hmac
import logging
import math
import re
import secrets
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from .calculations import next_month_key, previous_month_key
from .config import Settings
from .db import Database
from .formatting import (
    MONTH_NAMES,
    actor_name,
    current_month,
    current_quarter,
    format_datetime,
    month_label,
    money,
    number,
    parse_month,
    quarter_label,
    quarter_of_month,
    service_name,
)
from .keyboards import (
    admin_menu,
    cancel_keyboard,
    confirm_remove_keyboard,
    history_entry_keyboard,
    initial_keyboard,
    quarter_navigation_keyboard,
    report_keyboard,
    tariff_keyboard,
    tenant_menu,
)

LOGGER = logging.getLogger("communal_bot")
MSK = ZoneInfo("Europe/Moscow")


def parse_number(value: str) -> float | None:
    try:
        parsed = float(value.strip().replace(",", "."))
    except ValueError:
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


class CommunalBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = Database(settings.database_path)

    def user(self, update: Update):
        telegram_user = update.effective_user
        return self.db.user(telegram_user.id) if telegram_user else None

    def actor_name(self, update: Update) -> str:
        user = update.effective_user
        if user is None:
            return "Неизвестный пользователь"
        return (user.first_name or user.username or str(user.id)).strip()

    async def deny(self, update: Update) -> None:
        message = update.effective_message
        if message:
            await message.reply_text(
                "Доступ закрыт. Попросите администратора добавить вас в белый список."
            )

    async def require_user(self, update: Update):
        user = self.user(update)
        if user is None:
            await self.deny(update)
        return user

    async def require_admin(self, update: Update):
        user = await self.require_user(update)
        if user is not None and user["role"] != "admin":
            await update.effective_message.reply_text(
                "Эта команда доступна только администратору."
            )
            return None
        return user

    async def require_tenant(self, update: Update):
        user = await self.require_user(update)
        if user is not None and user["role"] != "tenant":
            await update.effective_message.reply_text(
                "Передачу показаний выполняет арендатор."
            )
            return None
        return user

    def selected_flat_id(self, user_id: int) -> int | None:
        selected = self.db.selected_flat(user_id)
        if selected:
            return selected
        flats = self.db.flats()
        if len(flats) == 1:
            self.db.select_flat(user_id, int(flats[0]["id"]))
            return int(flats[0]["id"])
        return None

    async def selected_flat_or_prompt(self, update: Update, user_id: int) -> int | None:
        flat_id = self.selected_flat_id(user_id)
        if flat_id:
            return flat_id
        flats = self.db.flats()
        if not flats:
            await update.effective_message.reply_text(
                "Сначала создайте квартиру командой /addflat <название>."
            )
            return None
        await update.effective_message.reply_text(
            "Выберите квартиру командой /select_flat <номер>."
        )
        return None

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        telegram_user = update.effective_user
        if telegram_user is None or update.effective_message is None:
            return
        user = self.db.user(telegram_user.id)
        invite_token = self.invite_token_from_start(context)
        if user is None and invite_token:
            await self.accept_invite(update, invite_token)
            return
        if user is None:
            if self.db.has_admin():
                await update.effective_message.reply_text(
                    "Ваш Telegram ID ещё не добавлен в белый список. "
                    "Попросите администратора выполнить /adduser."
                )
                return
            context.user_data["bootstrap"] = True
            await update.effective_message.reply_text(
                "Это первый запуск бота. Введите секретный код владельца, "
                "чтобы назначить себя администратором."
            )
            return
        await self.show_home(update, user)

    def invite_token_from_start(
        self, context: ContextTypes.DEFAULT_TYPE
    ) -> str | None:
        if not context.args:
            return None
        value = context.args[0].strip()
        if value.startswith("invite_") and len(value) > len("invite_"):
            return value[len("invite_") :]
        return None

    async def accept_invite(self, update: Update, token: str) -> None:
        telegram_user = update.effective_user
        if telegram_user is None:
            return
        if self.db.user(telegram_user.id) is not None:
            await update.effective_message.reply_text(
                "У вас уже есть активный доступ к боту. "
                "Для смены квартиры обратитесь к администратору."
            )
            return
        invite = self.db.claim_invite(token, telegram_user.id)
        if invite is None:
            await update.effective_message.reply_text(
                "Ссылка недействительна, уже использована или срок её действия истёк. "
                "Попросите администратора создать новую."
            )
            return
        self.db.save_user(
            telegram_user.id,
            "tenant",
            int(invite["flat_id"]),
            telegram_user.first_name or "",
            telegram_user.username or "",
        )
        self.db.audit(
            int(invite["flat_id"]),
            telegram_user.id,
            telegram_user.first_name or telegram_user.username or str(telegram_user.id),
            "tenant_invited",
            f"Квартирант присоединился к квартире «{invite['flat_name']}»",
        )
        await update.effective_message.reply_text(
            f"Вы добавлены как квартирант в квартиру «{invite['flat_name']}».\n"
            "Теперь вы будете получать напоминания и сможете передавать показания.",
            reply_markup=tenant_menu(),
        )

    async def show_home(self, update: Update, user) -> None:
        if user["role"] == "admin":
            flat_id = self.selected_flat_id(int(user["user_id"]))
            if flat_id is None:
                text = (
                    "Добро пожаловать, администратор.\n\n"
                    "Квартир пока нет. Создайте первую командой:\n"
                    "/addflat Квартира 1"
                )
            else:
                text = self.admin_summary(flat_id)
            await update.effective_message.reply_text(text, reply_markup=admin_menu())
            return
        flat = self.db.flat(int(user["flat_id"])) if user["flat_id"] else None
        flat_name = flat["name"] if flat else "квартира не назначена"
        await update.effective_message.reply_text(
            f"Добро пожаловать. Вы привязаны к: {flat_name}.\n"
            "Здесь можно передать показания и получить расчёт коммунальных платежей.",
            reply_markup=tenant_menu(),
        )

    def admin_summary(self, flat_id: int) -> str:
        flat = self.db.flat(flat_id)
        tariff = self.db.tariff_row(flat_id)
        assert flat is not None
        return (
            f"Настройки для {flat['name']} (№{flat_id})\n\n"
            "Текущие тарифы:\n"
            f"Вода: {money(float(tariff['water']))} руб./куб.м\n"
            "Электричество: "
            f"до {number(float(tariff['electricity_threshold1']))} — "
            f"{money(float(tariff['electricity_tariff1']))}, "
            f"до {number(float(tariff['electricity_threshold2']))} — "
            f"{money(float(tariff['electricity_tariff2']))}, "
            f"свыше — {money(float(tariff['electricity_tariff3']))}\n"
            f"Газ: {money(float(tariff['gas']))} руб./куб.м\n"
            f"ТКО: {money(float(tariff['tko']))} руб.\n"
            f"Капремонт: {money(float(tariff['caprepair']))} руб."
        )

    def tenant_tariff_text(self, flat_id: int) -> str:
        flat = self.db.flat(flat_id)
        effective_month = current_month(datetime.now(MSK).date())
        tariff = self.db.tariff_row(flat_id, effective_month)
        return (
            f"Актуальные тарифы для квартиры «{flat['name']}» "
            f"на {month_label(effective_month)}:\n\n"
            f"Вода: {money(float(tariff['water']))} руб./куб. м\n"
            "Электричество:\n"
            f"• до {number(float(tariff['electricity_threshold1']))} кВт·ч — "
            f"{money(float(tariff['electricity_tariff1']))} руб./кВт·ч\n"
            f"• до {number(float(tariff['electricity_threshold2']))} кВт·ч — "
            f"{money(float(tariff['electricity_tariff2']))} руб./кВт·ч\n"
            f"• свыше — {money(float(tariff['electricity_tariff3']))} руб./кВт·ч\n"
            f"Газ: {money(float(tariff['gas']))} руб./куб. м\n"
            f"ТКО: {money(float(tariff['tko']))} руб.\n"
            f"Капремонт: {money(float(tariff['caprepair']))} руб."
        )

    def tenant_history_text(self, flat_id: int) -> str:
        flat = self.db.flat(flat_id)
        readings = self.db.readings(flat_id)
        if not readings:
            return "История начислений пока пуста."
        lines = [f"История начислений по квартире «{flat['name']}»:\n"]
        for row in readings[:30]:
            lines.append(
                f"{month_label(row['month'])}: "
                f"{money(float(row['total_with_uk']))} руб. к оплате"
            )
        return "\n".join(lines)

    async def cmd_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = await self.require_user(update)
        if user:
            await self.show_home(update, user)

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = await self.require_user(update)
        if user is None:
            return
        if user["role"] == "admin":
            text = (
                "Инструкция администратора\n\n"
                "/addflat <название> — создать квартиру\n"
                "/select_flat <номер> — выбрать квартиру\n"
                "/flats — список квартир\n"
                "/adduser <TelegramID> <admin|tenant> <номер квартиры> — добавить пользователя\n"
                "/invite — создать одноразовую ссылку для квартиранта\n"
                "/removeuser <TelegramID> — удалить пользователя и очистить историю квартиры\n"
                "/listusers — список пользователей\n"
                "/history [ММ.ГГГГ] — история расчётов\n"
                "/stats — последние показания и тарифы\n"
                "/menu — главное меню\n\n"
                "Тарифы, УК, капремонт и начальные показания настраиваются кнопками."
            )
        else:
            text = (
                "Инструкция жильца\n\n"
                "Бот напоминает передать показания 23-го и 24-го числа. "
                "Отвечайте по порядку: электричество, вода, газ.\n"
                "До 25-го числа показания можно исправить. "
                "После расчёта бот покажет разбивку и срок оплаты.\n\n"
                "Кнопки «История начислений» и «Актуальные тарифы» "
                "показывают данные вашей квартиры.\n\n"
                "/start — открыть меню\n"
                "/help — эта инструкция"
            )
        await update.effective_message.reply_text(text)

    async def cmd_add_flat(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self.require_admin(update) is None:
            return
        name = " ".join(context.args).strip()
        if not name:
            await update.effective_message.reply_text(
                "Укажите название: /addflat Квартира 1"
            )
            return
        flat_id = self.db.add_flat(name)
        admin = self.db.user(update.effective_user.id)
        self.db.select_flat(int(admin["user_id"]), flat_id)
        await update.effective_message.reply_text(
            f"Квартира «{name}» создана и выбрана. Откройте /menu, чтобы настроить тарифы."
        )

    async def cmd_select_flat(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        admin = await self.require_admin(update)
        if admin is None:
            return
        if not context.args:
            lines = ["Квартиры:"]
            for flat in self.db.flats():
                marker = " ← выбрана" if int(flat["id"]) == self.selected_flat_id(admin["user_id"]) else ""
                lines.append(f"{flat['id']}. {flat['name']}{marker}")
            await update.effective_message.reply_text("\n".join(lines))
            return
        try:
            flat_id = int(context.args[0])
        except ValueError:
            await update.effective_message.reply_text("Номер квартиры должен быть числом.")
            return
        if self.db.flat(flat_id) is None:
            await update.effective_message.reply_text("Квартира с таким номером не найдена.")
            return
        self.db.select_flat(admin["user_id"], flat_id)
        await update.effective_message.reply_text(
            f"Выбрана квартира №{flat_id}. Откройте /menu."
        )

    async def cmd_flats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        admin = await self.require_admin(update)
        if admin is None:
            return
        flats = self.db.flats()
        if not flats:
            await update.effective_message.reply_text("Квартир пока нет.")
            return
        lines = ["Список квартир:"]
        for flat in flats:
            tenant_count = len(self.db.tenants(int(flat["id"])))
            lines.append(f"№{flat['id']} — {flat['name']} — арендаторов: {tenant_count}")
        await update.effective_message.reply_text("\n".join(lines))

    async def cmd_add_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self.require_admin(update) is None:
            return
        if len(context.args) != 3:
            await update.effective_message.reply_text(
                "Формат: /adduser <TelegramID> <admin|tenant> <номер квартиры>"
            )
            return
        try:
            user_id = int(context.args[0])
            flat_id = int(context.args[2])
        except ValueError:
            await update.effective_message.reply_text("Telegram ID и номер квартиры должны быть числами.")
            return
        role = context.args[1].lower()
        if role not in {"admin", "tenant"}:
            await update.effective_message.reply_text("Роль должна быть admin или tenant.")
            return
        if self.db.flat(flat_id) is None:
            await update.effective_message.reply_text("Квартира с таким номером не найдена.")
            return
        existing = self.db.user(user_id)
        self.db.save_user(user_id, role, flat_id, "", "")
        await update.effective_message.reply_text(
            f"Пользователь {user_id} добавлен как {role} в квартиру №{flat_id}."
            + (" Доступ восстановлен." if existing else "")
        )

    async def cmd_invite(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        admin = await self.require_admin(update)
        if admin is None:
            return
        flat_id = await self.selected_flat_or_prompt(update, admin["user_id"])
        if flat_id is None:
            return
        await self.send_invite(update, context, flat_id, admin["user_id"])

    async def cmd_remove_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self.require_admin(update) is None:
            return
        if len(context.args) != 1:
            await update.effective_message.reply_text("Формат: /removeuser <TelegramID>")
            return
        try:
            user_id = int(context.args[0])
        except ValueError:
            await update.effective_message.reply_text("Telegram ID должен быть числом.")
            return
        target = self.db.user(user_id)
        if target is None:
            await update.effective_message.reply_text("Пользователь не найден.")
            return
        warning = "Пользователь будет удалён."
        if target["role"] == "tenant" and target["flat_id"]:
            warning += (
                "\nВ соответствии с настройками проекта будет удалена вся история "
                "расчётов и оплат квартиры."
            )
        await update.effective_message.reply_text(
            warning + "\nПодтвердите действие.",
            reply_markup=confirm_remove_keyboard(user_id),
        )

    async def cmd_list_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self.require_admin(update) is None:
            return
        users = self.db.users()
        if not users:
            await update.effective_message.reply_text("Пользователей пока нет.")
            return
        lines = ["Пользователи:"]
        for user in users:
            flat = f"квартира №{user['flat_id']}" if user["flat_id"] else "без квартиры"
            lines.append(f"{user['user_id']} — {user['role']} — {flat}")
        await update.effective_message.reply_text("\n".join(lines))

    async def cmd_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = await self.require_user(update)
        if user is None:
            return
        if user["role"] == "admin":
            flat_id = await self.selected_flat_or_prompt(update, user["user_id"])
            if flat_id is None:
                return
            with_pay = True
        else:
            flat_id = int(user["flat_id"]) if user["flat_id"] else None
            if flat_id is None:
                await update.effective_message.reply_text("Вам ещё не назначена квартира.")
                return
            with_pay = False

        now = datetime.now(MSK)
        year = now.year
        quarter = current_quarter(now.date())
        text, keyboard = self.build_quarter_view(
            flat_id, year, quarter, view="history", with_pay_buttons=with_pay
        )
        await update.effective_message.reply_text(text, reply_markup=keyboard)

    async def cmd_journal(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда /journal — журнал изменений по квартире (поквартально)."""
        user = await self.require_user(update)
        if user is None:
            return
        if user["role"] == "admin":
            flat_id = await self.selected_flat_or_prompt(update, user["user_id"])
            if flat_id is None:
                return
        else:
            flat_id = int(user["flat_id"]) if user["flat_id"] else None
            if flat_id is None:
                await update.effective_message.reply_text("Вам ещё не назначена квартира.")
                return

        now = datetime.now(MSK)
        year = now.year
        quarter = current_quarter(now.date())
        text, keyboard = self.build_quarter_view(
            flat_id, year, quarter, view="journal", with_pay_buttons=False
        )
        await update.effective_message.reply_text(text, reply_markup=keyboard)

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        admin = await self.require_admin(update)
        if admin is None:
            return
        flat_id = await self.selected_flat_or_prompt(update, admin["user_id"])
        if flat_id is None:
            return
        rows = self.db.readings(flat_id)
        text = self.admin_summary(flat_id)
        if rows:
            row = rows[0]
            text += (
                f"\n\nПоследние показания ({month_label(row['month'])}):\n"
                f"Электричество: {number(float(row['electricity']))}\n"
                f"Вода: {number(float(row['water']))}\n"
                f"Газ: {number(float(row['gas']))}"
            )
        await update.effective_message.reply_text(text)

    async def start_reading(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = await self.require_tenant(update)
        if user is None:
            return
        if not user["flat_id"]:
            await update.effective_message.reply_text(
                "Вам ещё не назначена квартира. Обратитесь к администратору."
            )
            return
        month = current_month(datetime.now(MSK).date())
        previous = self.db.previous_readings(int(user["flat_id"]), month)
        context.user_data["reading_state"] = "electricity"
        context.user_data["reading_month"] = month
        context.user_data["reading_values"] = {}
        context.user_data["reading_previous"] = previous
        await update.effective_message.reply_text(
            f"Показания за {month_label(month)}.\n"
            f"Предыдущее показание электричества: {number(previous[0])}.\n"
            "Введите текущее показание электричества:",
            reply_markup=cancel_keyboard(),
        )

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        telegram_user = update.effective_user
        if message is None or telegram_user is None:
            return
        text = message.text.strip()
        user = self.db.user(telegram_user.id)

        if context.user_data.get("bootstrap"):
            if hmac.compare_digest(text, self.settings.initial_admin_code):
                context.user_data.pop("bootstrap", None)
                self.db.save_user(
                    telegram_user.id,
                    "admin",
                    None,
                    telegram_user.first_name or "",
                    telegram_user.username or "",
                )
                await message.reply_text(
                    "Код принят. Вы назначены администратором.",
                    reply_markup=admin_menu(),
                )
            else:
                await message.reply_text("Неверный код. Попробуйте ещё раз.")
            return

        if user is None:
            await self.deny(update)
            return

        if context.user_data.get("reading_state"):
            await self.handle_reading_value(update, context, user, text)
            return
        if context.user_data.get("pending"):
            await self.handle_pending(update, context, user, text)
            return
        await message.reply_text("Используйте /menu или /help.")

    async def handle_reading_value(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, user, text: str
    ) -> None:
        value = parse_number(text)
        if value is None:
            await update.effective_message.reply_text(
                "Введите неотрицательное число. Дробную часть можно указать через точку или запятую."
            )
            return
        state = context.user_data["reading_state"]
        previous = context.user_data["reading_previous"]
        index = {"electricity": 0, "water": 1, "gas": 2}[state]
        if value < previous[index]:
            await update.effective_message.reply_text(
                f"Новое показание не может быть меньше предыдущего ({number(previous[index])}). "
                "Введите корректное значение."
            )
            return
        values = context.user_data["reading_values"]
        values[state] = value
        if state == "electricity":
            context.user_data["reading_state"] = "water"
            await update.effective_message.reply_text(
                f"Принято. Предыдущее показание воды: {number(previous[1])}.\n"
                "Введите текущее показание воды:",
                reply_markup=cancel_keyboard(),
            )
            return
        if state == "water":
            context.user_data["reading_state"] = "gas"
            await update.effective_message.reply_text(
                f"Принято. Предыдущее показание газа: {number(previous[2])}.\n"
                "Введите текущее показание газа:",
                reply_markup=cancel_keyboard(),
            )
            return

        flat_id = int(user["flat_id"])
        month = context.user_data["reading_month"]
        row = self.db.save_reading(
            flat_id,
            month,
            float(values["electricity"]),
            float(values["water"]),
            float(values["gas"]),
            int(user["user_id"]),
        )
        self.db.audit(
            flat_id,
            int(user["user_id"]),
            self.actor_name(update),
            "reading_submitted",
            f"Показания за {month_label(month)} отправлены",
        )
        self.clear_flow(context)
        await update.effective_message.reply_text(self.report_text(row, flat_id, False))
        await self.send_report_to_admins(update.get_bot(), row, flat_id)
        for tenant in self.db.tenants(flat_id):
            if int(tenant["user_id"]) != int(user["user_id"]):
                await update.get_bot().send_message(
                    int(tenant["user_id"]),
                    self.report_text(row, flat_id, False),
                )

    def clear_flow(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        for key in (
            "reading_state",
            "reading_month",
            "reading_values",
            "reading_previous",
            "pending",
        ):
            context.user_data.pop(key, None)

    async def handle_pending(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, user, text: str
    ) -> None:
        pending = context.user_data["pending"]
        flat_id = await self.selected_flat_or_prompt(update, user["user_id"])
        if flat_id is None:
            self.clear_flow(context)
            return
        kind = pending["kind"]
        if kind == "initial":
            value = parse_number(text)
            if value is None:
                await update.effective_message.reply_text("Введите неотрицательное число.")
                return
            meter = pending["meter"]
            self.db.save_initial_reading(flat_id, meter, value)
            self.db.audit(
                flat_id,
                user["user_id"],
                self.actor_name(update),
                "initial_reading_changed",
                f"{service_name(meter)}: {number(value)}",
            )
            self.clear_flow(context)
            await update.effective_message.reply_text(
                f"Начальное показание ({service_name(meter)}) сохранено: {number(value)}.",
                reply_markup=admin_menu(),
            )
            return

        if kind == "uk":
            await self.handle_uk(update, context, user, flat_id, text)
            return

        if kind == "caprepair":
            value = parse_number(text)
            if value is None:
                await update.effective_message.reply_text("Введите сумму, например 200 или 200.50.")
                return
            month = current_month(datetime.now(MSK).date())
            changes = self.db.save_tariffs(flat_id, month, {"caprepair": value})
            self.db.audit(
                flat_id,
                user["user_id"],
                self.actor_name(update),
                "caprepair_changed",
                f"{month_label(month)}: {money(value)} руб.",
            )
            self.clear_flow(context)
            await update.effective_message.reply_text(
                f"Капремонт на {month_label(month)} установлен: {money(value)} руб.",
                reply_markup=admin_menu(),
            )
            await self.send_recalculation_notifications(update.get_bot(), flat_id, changes)
            return

        if kind == "tariff":
            await self.handle_tariff(update, context, user, flat_id, text)
            return

    async def handle_uk(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        user,
        flat_id: int,
        text: str,
    ) -> None:
        parts = text.replace(",", ".").split()
        month_part = next((part for part in parts if parse_month(part)), None)
        month = parse_month(month_part) if month_part else None
        amount = next(
            (
                parse_number(part)
                for part in parts
                if part != month_part and parse_number(part) is not None
            ),
            None,
        )
        if month is None or amount is None:
            await update.effective_message.reply_text(
                "Введите месяц и сумму через пробел: 07.2026 1200"
            )
            return
        self.db.set_uk(flat_id, month, amount)
        changes = self.db.recalculate_from(flat_id, next_month_key(month))
        self.db.audit(
            flat_id,
            user["user_id"],
            self.actor_name(update),
            "uk_changed",
            f"УК за {month_label(month)}: {money(amount)} руб.",
        )
        self.clear_flow(context)
        await update.effective_message.reply_text(
            f"Сумма УК за {month_label(month)} сохранена: {money(amount)} руб.",
            reply_markup=admin_menu(),
        )
        await self.send_recalculation_notifications(update.get_bot(), flat_id, changes)

    async def handle_tariff(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        user,
        flat_id: int,
        text: str,
    ) -> None:
        pending = context.user_data["pending"]
        if pending["stage"] == "month":
            month = parse_month(text)
            if month is None:
                await update.effective_message.reply_text("Введите месяц в формате ММ.ГГГГ.")
                return
            pending["stage"] = "value"
            pending["month"] = month
            if pending["meter"] == "electricity":
                prompt = (
                    "Введите один тариф или 5 значений через пробел:\n"
                    "порог1 тариф1 порог2 тариф2 тариф3\n"
                    "Например: 150 4.90 800 6.21 8.27"
                )
            else:
                prompt = f"Введите новый тариф для услуги «{service_name(pending['meter'])}»:"
            await update.effective_message.reply_text(prompt, reply_markup=cancel_keyboard())
            return

        meter = pending["meter"]
        values = text.replace(",", ".").split()
        updates: dict[str, float]
        if meter == "electricity":
            if len(values) == 1:
                unified = parse_number(values[0])
                if unified is None:
                    await update.effective_message.reply_text("Введите число или 5 значений через пробел.")
                    return
                current = self.db.tariff_row(flat_id)
                updates = {
                    "electricity_threshold1": float(current["electricity_threshold1"]),
                    "electricity_threshold2": float(current["electricity_threshold2"]),
                    "electricity_tariff1": unified,
                    "electricity_tariff2": unified,
                    "electricity_tariff3": unified,
                }
            elif len(values) == 5:
                parsed = [parse_number(value) for value in values]
                if any(value is None for value in parsed):
                    await update.effective_message.reply_text("Все 5 значений должны быть числами.")
                    return
                threshold1, tariff1, threshold2, tariff2, tariff3 = parsed  # type: ignore[misc]
                if threshold1 >= threshold2:
                    await update.effective_message.reply_text("Первый порог должен быть меньше второго.")
                    return
                updates = {
                    "electricity_threshold1": threshold1,
                    "electricity_tariff1": tariff1,
                    "electricity_threshold2": threshold2,
                    "electricity_tariff2": tariff2,
                    "electricity_tariff3": tariff3,
                }
            else:
                await update.effective_message.reply_text("Нужно ввести одно число или 5 значений.")
                return
        else:
            if len(values) != 1 or parse_number(values[0]) is None:
                await update.effective_message.reply_text("Введите одно неотрицательное число.")
                return
            updates = {meter: parse_number(values[0])}  # type: ignore[dict-item]

        month = pending["month"]
        changes = self.db.save_tariffs(flat_id, month, updates)
        self.db.audit(
            flat_id,
            user["user_id"],
            self.actor_name(update),
            "tariff_changed",
            f"{service_name(meter)} с {month_label(month)}",
        )
        self.clear_flow(context)
        await update.effective_message.reply_text(
            f"Тариф «{service_name(meter)}» изменён с {month_label(month)}.",
            reply_markup=admin_menu(),
        )
        await self.send_recalculation_notifications(update.get_bot(), flat_id, changes)

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        await query.answer()
        user = self.user(update)
        if user is None:
            await query.edit_message_text("Доступ закрыт. Попросите администратора добавить вас.")
            return
        data = query.data or ""
        if data == "cancel":
            self.clear_flow(context)
            await query.edit_message_text("Действие отменено.")
            return
        if data == "help":
            await self.cmd_help(update, context)
            return
        if data == "reading:start":
            await self.start_reading(update, context)
            return
        if data == "menu":
            self.clear_flow(context)
            await query.edit_message_text("Главное меню:")
            await self.show_home(update, user)
            return
        if data in {"tenant:history", "tenant:tariffs"}:
            flat_id = int(user["flat_id"]) if user["flat_id"] else None
            if flat_id is None:
                await query.edit_message_text(
                    "Квартира ещё не назначена.", reply_markup=tenant_menu()
                )
                return
            if data == "tenant:history":
                # Поквартальная история для арендатора
                now = datetime.now(MSK)
                year = now.year
                quarter = current_quarter(now.date())
                text, keyboard = self.build_quarter_view(
                    flat_id, year, quarter, view="journal", with_pay_buttons=False
                )
                await query.edit_message_text(text, reply_markup=keyboard)
            else:
                text = self.tenant_tariff_text(flat_id)
                await query.edit_message_text(text, reply_markup=tenant_menu())
            return

        # Журнал изменений — для арендатора по своей квартире, для админа по выбранной
        if data == "audit":
            if user["role"] == "admin":
                flat_id = self.selected_flat_id(int(user["user_id"]))
                if flat_id is None:
                    await query.edit_message_text(
                        "Сначала выберите квартиру через /select_flat",
                        reply_markup=admin_menu(),
                    )
                    return
            else:
                flat_id = int(user["flat_id"]) if user["flat_id"] else None
                if flat_id is None:
                    await query.edit_message_text("Квартира ещё не назначена.")
                    return
            # Показываем журнал изменений (audit log) для квартиры
            entries = self.db.audit_entries(flat_id)
            text = "\n".join(
                f"{row['actor_name']}: {row['details']}" for row in entries
            ) or "Изменений по квартире пока нет."
            menu = admin_menu() if user["role"] == "admin" else tenant_menu()
            await query.edit_message_text(text, reply_markup=menu)
            return

        # Навигация по кварталам (общая для журнала и истории)
        if data.startswith("qnav:"):
            await self.handle_quarter_navigation(update, context, user, data)
            return

        # Переключение оплаты из истории
        if data.startswith("paytoggle:"):
            await self.handle_pay_toggle(update, context, user, data)
            return

        if user["role"] != "admin":
            await query.edit_message_text("Эта кнопка доступна только администратору.")
            return
        flat_id = await self.selected_flat_or_prompt(update, user["user_id"])
        if data == "flats":
            await query.edit_message_text(
                "\n".join(
                    [f"№{row['id']} — {row['name']}" for row in self.db.flats()]
                )
                or "Квартир пока нет.",
                reply_markup=admin_menu(),
            )
            return
        if data == "users":
            users = self.db.users(flat_id) if flat_id else self.db.users()
            text = "\n".join(
                f"{row['user_id']} — {row['role']} — квартира №{row['flat_id']}"
                for row in users
            ) or "Пользователей пока нет."
            await query.edit_message_text(text, reply_markup=admin_menu())
            return
        if data == "invite":
            if flat_id is None:
                return
            await self.send_invite(update, context, flat_id, user["user_id"])
            return
        if flat_id is None:
            return
        if data == "tariffs":
            await query.edit_message_text(
                self.admin_summary(flat_id), reply_markup=tariff_keyboard()
            )
            return
        if data == "initial":
            await query.edit_message_text(
                "Выберите счётчик для начального показания:",
                reply_markup=initial_keyboard(),
            )
            return
        if data in {"uk", "caprepair"}:
            if data == "uk":
                self.set_pending(context, "uk")
                prompt = "Введите месяц и сумму УК: 07.2026 1200"
            else:
                self.set_pending(context, "caprepair")
                prompt = (
                    f"Введите сумму капремонта на {month_label(current_month(datetime.now(MSK).date()))}:"
                )
            await query.edit_message_text(prompt, reply_markup=cancel_keyboard())
            return
        # История — поквартальный отчёт для администратора с кнопками оплаты
        if data == "history":
            now = datetime.now(MSK)
            year = now.year
            quarter = current_quarter(now.date())
            text, keyboard = self.build_quarter_view(
                flat_id, year, quarter, view="history", with_pay_buttons=True
            )
            await query.edit_message_text(text, reply_markup=keyboard)
            return
        # Неоплаченные счета
        if data == "unpaid":
            unpaid = self.db.all_unpaid_readings(flat_id)
            if not unpaid:
                await query.edit_message_text(
                    "Все счета оплачены.", reply_markup=admin_menu()
                )
                return
            lines = ["Неоплаченные счета:\n"]
            for row in unpaid:
                paid_status = "✅" if self.db.is_reading_paid(int(row["id"])) else "⬜️"
                lines.append(
                    f"{month_label(row['month'])}: {money(float(row['total_with_uk']))} руб. {paid_status}"
                )
            await query.edit_message_text(
                "\n".join(lines), reply_markup=admin_menu()
            )
            return
        if data.startswith("initial:"):
            meter = data.split(":", 1)[1]
            self.set_pending(context, "initial", meter=meter)
            await query.edit_message_text(
                f"Введите начальное показание ({service_name(meter)}):",
                reply_markup=cancel_keyboard(),
            )
            return
        if data.startswith("tariff:"):
            meter = data.split(":", 1)[1]
            if meter == "caprepair":
                self.set_pending(context, "caprepair")
                await query.edit_message_text(
                    "Введите сумму капремонта на текущий месяц:",
                    reply_markup=cancel_keyboard(),
                )
                return
            self.set_pending(context, "tariff", meter=meter, stage="month")
            await query.edit_message_text(
                f"Введите месяц начала действия тарифа «{service_name(meter)}» в формате ММ.ГГГГ:",
                reply_markup=cancel_keyboard(),
            )
            return
        if data.startswith("pay:"):
            _, reading_id, service = data.split(":")
            paid = self.db.toggle_payment(int(reading_id), service)
            reading = next(
                (row for row in self.db.readings(flat_id) if int(row["id"]) == int(reading_id)),
                None,
            )
            if reading:
                amounts = {
                    'water': float(reading['water_amount']),
                    'electricity': float(reading['electricity_amount']),
                    'gas': float(reading['gas_amount']),
                    'tko': float(reading['tko_amount']),
                    'uk': float(reading['uk_amount']),
                    'caprepair': float(reading['caprepair_amount']),
                }
                statuses = self.db.payment_statuses(int(reading_id))
                await query.edit_message_text(
                    self.report_text(reading, flat_id, True),
                    reply_markup=report_keyboard(int(reading_id), statuses, amounts),
                )
                await query.answer(
                    f"{service_name(service)}: {'оплачено' if paid else 'не оплачено'}"
                )
            return
        if data.startswith("select:"):
            selected = int(data.split(":")[1])
            self.db.select_flat(user["user_id"], selected)
            await query.edit_message_text(self.admin_summary(selected), reply_markup=admin_menu())
            return
        if data.startswith("remove:confirm:"):
            target_id = int(data.rsplit(":", 1)[1])
            target = self.db.user(target_id)
            if target and target["role"] == "tenant" and target["flat_id"]:
                self.db.delete_flat_history(int(target["flat_id"]))
            removed = self.db.remove_user(target_id)
            await query.edit_message_text(
                "Пользователь удалён. История квартиры очищена."
                if removed and removed["role"] == "tenant"
                else "Пользователь удалён.",
                reply_markup=admin_menu(),
            )

    def set_pending(self, context: ContextTypes.DEFAULT_TYPE, kind: str, **values: Any) -> None:
        context.user_data["pending"] = {"kind": kind, **values}
        context.user_data.pop("reading_state", None)

    # --- Поквартальная навигация для журнала и истории ---

    def build_quarter_view(
        self,
        flat_id: int,
        year: int,
        quarter: int,
        view: str = "journal",
        with_pay_buttons: bool = False,
    ) -> tuple[str, InlineKeyboardMarkup]:
        """
        Строит текст и клавиатуру для поквартального просмотра.
        view: 'journal' — журнал (без кнопок оплаты), 'history' — история с кнопками оплаты.
        """
        flat = self.db.flat(flat_id)
        flat_name = flat["name"] if flat else f"№{flat_id}"
        readings = self.db.readings_for_quarter(flat_id, year, quarter)
        available_years = self.db.available_years(flat_id)

        header = f"📋 {quarter_label(quarter, year)} — {flat_name}\n\n"

        if not readings:
            text = header + "За этот период нет записей."
        else:
            lines = [header]
            for row in readings:
                submitter_name = (
                    row["first_name"] or row["username"] or str(row["submitted_by"])
                ).strip()
                if not submitter_name:
                    submitter_name = str(row["submitted_by"])
                submitted_at = format_datetime(row["updated_at"])
                total = float(row["total_with_uk"])
                line = (
                    f"📅 {month_label(row['month'])}\n"
                    f"   Передал: {submitter_name}\n"
                    f"   Дата: {submitted_at}\n"
                    f"   Сумма: {money(total)} руб."
                )
                if with_pay_buttons:
                    is_paid = self.db.is_reading_paid(int(row["id"]))
                    status = "✅ Оплачено" if is_paid else "⬜️ Не оплачено"
                    line += f"\n   Статус: {status}"
                lines.append(line)
            text = "\n\n".join(lines)

        keyboard = quarter_navigation_keyboard(
            year=year,
            quarter=quarter,
            available_years=available_years,
            view=view,
            flat_id=flat_id,
        )
        return text, keyboard

    async def handle_quarter_navigation(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, user, data: str
    ) -> None:
        """Обрабатывает навигацию по кварталам: qnav:<view>:<flat_id>:<year>:<quarter>"""
        query = update.callback_query
        parts = data.split(":")
        # qnav:view:flat_id:year:quarter  или  qnav:view:year:quarter
        view = parts[1]
        if len(parts) == 5:
            flat_id = int(parts[2])
            year = int(parts[3])
            quarter = int(parts[4])
        elif len(parts) == 4:
            year = int(parts[2])
            quarter = int(parts[3])
            if user["role"] == "admin":
                flat_id = self.selected_flat_id(int(user["user_id"])) or 0
            else:
                flat_id = int(user["flat_id"]) if user["flat_id"] else 0
        else:
            return

        if flat_id == 0:
            await query.edit_message_text("Квартира не выбрана.")
            return

        with_pay = view == "history" and user["role"] == "admin"
        text, keyboard = self.build_quarter_view(
            flat_id, year, quarter, view=view, with_pay_buttons=with_pay
        )
        await query.edit_message_text(text, reply_markup=keyboard)

    async def handle_pay_toggle(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, user, data: str
    ) -> None:
        """Переключает оплату для записи из истории: paytoggle:<reading_id>:<view>:<flat_id>:<year>:<quarter>"""
        query = update.callback_query
        parts = data.split(":")
        # paytoggle:reading_id:view:flat_id:year:quarter
        if len(parts) < 6:
            return
        reading_id = int(parts[1])
        view = parts[2]
        flat_id = int(parts[3]) if parts[3] else 0
        year = int(parts[4])
        quarter = int(parts[5])

        new_paid = self.db.toggle_reading_paid(reading_id)
        await query.answer(
            f"{'Оплачено' if new_paid else 'Оплата снята'}"
        )

        if flat_id == 0:
            return

        text, keyboard = self.build_quarter_view(
            flat_id, year, quarter, view=view, with_pay_buttons=True
        )
        await query.edit_message_text(text, reply_markup=keyboard)

    async def send_invite(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        flat_id: int,
        created_by: int,
    ) -> None:
        token = secrets.token_urlsafe(24)
        expires_at = (
            datetime.now(ZoneInfo("UTC")) + timedelta(days=7)
        ).isoformat()
        self.db.create_invite(token, flat_id, created_by, expires_at)
        bot_info = await context.bot.get_me()
        flat = self.db.flat(flat_id)
        await update.effective_message.reply_text(
            f"Персональная ссылка для квартиры «{flat['name']}»:\n\n"
            f"https://t.me/{bot_info.username}?start=invite_{token}\n\n"
            "Ссылка одноразовая и действует 7 дней. "
            "Отправьте её только нужному квартиранту.",
            reply_markup=admin_menu(),
        )

    def report_text(self, row, flat_id: int, admin: bool) -> str:
        statuses = self.db.payment_statuses(int(row["id"]))
        flat = self.db.flat(flat_id)
        uk_month = previous_month_key(row["month"])

        def line(service: str, amount: float) -> str:
            if not admin:
                return f"{service_name(service)}: {money(amount)} руб."
            paid = "✓" if bool(statuses.get(service, {"paid": 0})["paid"]) else "□"
            return f"{service_name(service)}: {money(amount)} руб. [{paid}]"

        text = (
            f"Отчёт за {month_label(row['month'])} ({flat['name'] if flat else 'квартира'})\n\n"
            f"Показания: электричество {number(float(row['electricity']))}, "
            f"вода {number(float(row['water']))}, газ {number(float(row['gas']))}\n"
            f"Расход: электричество {number(float(row['electricity_consumption']))}, "
            f"вода {number(float(row['water_consumption']))}, "
            f"газ {number(float(row['gas_consumption']))}\n\n"
            f"{line('water', float(row['water_amount']))}\n"
            f"{line('electricity', float(row['electricity_amount']))}\n"
            f"{line('gas', float(row['gas_amount']))}\n"
            f"{line('tko', float(row['tko_amount']))}\n"
        )
        if float(row["uk_amount"]) > 0:
            text += f"{line('uk', float(row['uk_amount']))} (за {month_label(uk_month)})\n"
        else:
            text += f"УК за {month_label(uk_month)} пока не внесена.\n"
        text += (
            f"\nИтого к оплате арендатором: {money(float(row['total_with_uk']))} руб.\n"
        )
        if admin:
            text += (
                f"{line('caprepair', float(row['caprepair_amount']))}\n"
                f"Итого с капремонтом: {money(float(row['total_for_admin']))} руб.\n"
            )
        deadline = self.payment_deadline(row["month"])
        text += f"Срок оплаты — до {deadline}."
        if not admin and float(row["uk_amount"]) == 0:
            text += "\nИтог рассчитан без УК. После её внесения бот отправит обновление."
        return text

    def payment_deadline(self, month: str) -> str:
        month_number, year = (int(part) for part in month.split("."))
        if month_number == 12:
            year += 1
            month_number = 1
        else:
            month_number += 1
        return f"10.{month_number:02d}.{year:04d}"

    async def send_report_to_admins(self, bot, row, flat_id: int) -> None:
        amounts = {
            'water': float(row['water_amount']),
            'electricity': float(row['electricity_amount']),
            'gas': float(row['gas_amount']),
            'tko': float(row['tko_amount']),
            'uk': float(row['uk_amount']),
            'caprepair': float(row['caprepair_amount']),
        }
        statuses = self.db.payment_statuses(int(row['id']))
        for admin in self.db.admins():
            await bot.send_message(
                int(admin["user_id"]),
                self.report_text(row, flat_id, True),
                reply_markup=report_keyboard(int(row['id']), statuses, amounts),
            )

    async def send_recalculation_notifications(
        self, bot, flat_id: int, changes: list[tuple[Any, float, float]]
    ) -> None:
        for row, before, after in changes:
            if abs(before - after) < 0.005:
                continue
            difference = after - before
            sign = "+" if difference >= 0 else ""
            text = (
                f"Сумма за {month_label(row['month'])} пересчитана.\n"
                f"Было: {money(before)} руб.\n"
                f"Стало: {money(after)} руб.\n"
                f"Разница: {sign}{money(difference)} руб.\n\n"
                "Статусы оплаты сохранены."
            )
            for tenant in self.db.tenants(flat_id):
                await bot.send_message(
                    int(tenant["user_id"]),
                    text + "\n\n" + self.report_text(row, flat_id, False),
                )
            await self.send_report_to_admins(bot, row, flat_id)

    async def handle_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        admin = await self.require_admin(update)
        if admin is None:
            return
        flat_id = await self.selected_flat_or_prompt(update, admin["user_id"])
        if flat_id is None:
            return
        caption = (update.effective_message.caption or "").strip()
        month = self.month_from_caption(caption)
        if month is None:
            await update.effective_message.reply_text(
                "Добавьте в подпись к файлу месяц квитанции, например: "
                "«Квитанция УК за 07.2026»."
            )
            return
        for tenant in self.db.tenants(flat_id):
            await context.bot.forward_message(
                chat_id=int(tenant["user_id"]),
                from_chat_id=update.effective_chat.id,
                message_id=update.effective_message.id,
            )
            await context.bot.send_message(
                int(tenant["user_id"]),
                f"Администратор загрузил квитанцию УК за {month_label(month)}.",
            )
        await update.effective_message.reply_text("Квитанция переслана арендаторам.")

    def month_from_caption(self, caption: str) -> str | None:
        match = re.search(r"\b(\d{1,2}\.\d{4})\b", caption)
        if match:
            return parse_month(match.group(1))
        lowered = caption.lower()
        for index, name in enumerate(MONTH_NAMES, start=1):
            if name.lower() in lowered:
                match_year = re.search(r"\b(20\d{2})\b", caption)
                if match_year:
                    return f"{index:02d}.{match_year.group(1)}"
        return None

    async def scheduler_tick(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        now = datetime.now(MSK)
        if now.hour != 10 or now.minute != 0:
            return
        key = now.strftime("%Y-%m-%d")
        completed = context.application.bot_data.setdefault("schedule_runs", set())
        month = current_month(now.date())
        if now.day in (23, 24) and f"reminder-{now.day}-{key}" not in completed:
            completed.add(f"reminder-{now.day}-{key}")
            for flat in self.db.flats():
                if now.day == 24 and self.db.reading(int(flat["id"]), month):
                    continue
                for tenant in self.db.tenants(int(flat["id"])):
                    await context.bot.send_message(
                        int(tenant["user_id"]),
                        f"Напоминание: передайте показания счётчиков за {month_label(month)} "
                        "до 25-го числа.",
                    )
        if now.day == 26 and f"missing-{key}" not in completed:
            completed.add(f"missing-{key}")
            missing = self.db.flats_without_reading(month)
            if missing:
                names = "\n".join(f"№{row['id']} — {row['name']}" for row in missing)
                for admin in self.db.admins():
                    await context.bot.send_message(
                        int(admin["user_id"]),
                        f"Не сданы показания за {month_label(month)}:\n{names}",
                    )
        if now.day == 8 and f"payment-{key}" not in completed:
            completed.add(f"payment-{key}")
            previous = previous_month_key(month)
            blocks = []
            for flat in self.db.flats():
                unpaid = self.db.unpaid_services(int(flat["id"]), previous)
                if unpaid:
                    lines = "\n".join(
                        f"• {service_name(row['service'])}: {money(float(row['amount']))} руб."
                        for row in unpaid
                    )
                    blocks.append(f"Квартира №{flat['id']} ({flat['name']}):\n{lines}")
            if blocks:
                text = (
                    f"Напоминание: неоплаченные услуги за {month_label(previous)}.\n\n"
                    + "\n\n".join(blocks)
                )
                for admin in self.db.admins():
                    await context.bot.send_message(int(admin["user_id"]), text)

    def build_application(self) -> Application:
        request = HTTPXRequest(
            read_timeout=30,
            write_timeout=30,
            connect_timeout=30,
            pool_timeout=30,
        )
        get_updates_request = HTTPXRequest(
            read_timeout=45,
            write_timeout=30,
            connect_timeout=30,
            pool_timeout=30,
        )
        application = (
            Application.builder()
            .token(self.settings.telegram_token)
            .request(request)
            .get_updates_request(get_updates_request)
            .build()
        )
        application.add_handler(CommandHandler("start", self.cmd_start))
        application.add_handler(CommandHandler("menu", self.cmd_menu))
        application.add_handler(CommandHandler("help", self.cmd_help))
        application.add_handler(CommandHandler("addflat", self.cmd_add_flat))
        application.add_handler(CommandHandler("select_flat", self.cmd_select_flat))
        application.add_handler(CommandHandler("flats", self.cmd_flats))
        application.add_handler(CommandHandler("adduser", self.cmd_add_user))
        application.add_handler(CommandHandler("invite", self.cmd_invite))
        application.add_handler(CommandHandler("removeuser", self.cmd_remove_user))
        application.add_handler(CommandHandler("listusers", self.cmd_list_users))
        application.add_handler(CommandHandler("history", self.cmd_history))
        application.add_handler(CommandHandler("stats", self.cmd_stats))
        application.add_handler(
            CallbackQueryHandler(self.callback)
        )
        application.add_handler(
            MessageHandler(filters.Document.ALL | filters.PHOTO, self.handle_file)
        )
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text)
        )
        if application.job_queue:
            application.job_queue.run_repeating(self.scheduler_tick, interval=60, first=5)
        return application


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings = Settings.from_environment()
    CommunalBot(settings).build_application().run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
        bootstrap_retries=-1,
    )
