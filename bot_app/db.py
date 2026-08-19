from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .calculations import (
    ReadingCalculation,
    TariffSet,
    calculate_reading,
    next_month_key,
    previous_month_key,
)


DEFAULT_TARIFFS = {
    "water": 0.0,
    "electricity_threshold1": 150.0,
    "electricity_tariff1": 0.0,
    "electricity_threshold2": 800.0,
    "electricity_tariff2": 0.0,
    "electricity_tariff3": 0.0,
    "gas": 0.0,
    "tko": 0.0,
    "caprepair": 0.0,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str | Path = "data/communal_bot.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS flats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    role TEXT NOT NULL CHECK(role IN ('admin', 'tenant')),
                    flat_id INTEGER REFERENCES flats(id) ON DELETE SET NULL,
                    first_name TEXT NOT NULL DEFAULT '',
                    username TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS admin_state (
                    user_id INTEGER PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                    selected_flat_id INTEGER REFERENCES flats(id) ON DELETE SET NULL
                );
                CREATE TABLE IF NOT EXISTS tariffs (
                    flat_id INTEGER PRIMARY KEY REFERENCES flats(id) ON DELETE CASCADE,
                    water REAL NOT NULL DEFAULT 0,
                    electricity_threshold1 REAL NOT NULL DEFAULT 150,
                    electricity_tariff1 REAL NOT NULL DEFAULT 0,
                    electricity_threshold2 REAL NOT NULL DEFAULT 800,
                    electricity_tariff2 REAL NOT NULL DEFAULT 0,
                    electricity_tariff3 REAL NOT NULL DEFAULT 0,
                    gas REAL NOT NULL DEFAULT 0,
                    tko REAL NOT NULL DEFAULT 0,
                    caprepair REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS tariff_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flat_id INTEGER NOT NULL REFERENCES flats(id) ON DELETE CASCADE,
                    effective_month TEXT NOT NULL,
                    water REAL NOT NULL,
                    electricity_threshold1 REAL NOT NULL,
                    electricity_tariff1 REAL NOT NULL,
                    electricity_threshold2 REAL NOT NULL,
                    electricity_tariff2 REAL NOT NULL,
                    electricity_tariff3 REAL NOT NULL,
                    gas REAL NOT NULL,
                    tko REAL NOT NULL,
                    caprepair REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(flat_id, effective_month)
                );
                CREATE TABLE IF NOT EXISTS initial_readings (
                    flat_id INTEGER PRIMARY KEY REFERENCES flats(id) ON DELETE CASCADE,
                    electricity REAL NOT NULL DEFAULT 0,
                    water REAL NOT NULL DEFAULT 0,
                    gas REAL NOT NULL DEFAULT 0,
                    entered_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS uk_payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flat_id INTEGER NOT NULL REFERENCES flats(id) ON DELETE CASCADE,
                    month TEXT NOT NULL,
                    amount REAL NOT NULL,
                    paid INTEGER NOT NULL DEFAULT 0,
                    file_id TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    UNIQUE(flat_id, month)
                );
                CREATE TABLE IF NOT EXISTS meter_readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flat_id INTEGER NOT NULL REFERENCES flats(id) ON DELETE CASCADE,
                    month TEXT NOT NULL,
                    electricity REAL NOT NULL,
                    water REAL NOT NULL,
                    gas REAL NOT NULL,
                    previous_electricity REAL NOT NULL,
                    previous_water REAL NOT NULL,
                    previous_gas REAL NOT NULL,
                    electricity_consumption REAL NOT NULL,
                    water_consumption REAL NOT NULL,
                    gas_consumption REAL NOT NULL,
                    electricity_amount REAL NOT NULL,
                    water_amount REAL NOT NULL,
                    gas_amount REAL NOT NULL,
                    tko_amount REAL NOT NULL,
                    uk_amount REAL NOT NULL,
                    caprepair_amount REAL NOT NULL,
                    total_without_uk REAL NOT NULL,
                    total_with_uk REAL NOT NULL,
                    total_for_admin REAL NOT NULL,
                    submitted_by INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(flat_id, month)
                );
                CREATE TABLE IF NOT EXISTS payment_status (
                    reading_id INTEGER NOT NULL REFERENCES meter_readings(id) ON DELETE CASCADE,
                    service TEXT NOT NULL,
                    amount REAL NOT NULL,
                    paid INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(reading_id, service)
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flat_id INTEGER NOT NULL REFERENCES flats(id) ON DELETE CASCADE,
                    actor_id INTEGER NOT NULL,
                    actor_name TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS invite_tokens (
                    token TEXT PRIMARY KEY,
                    flat_id INTEGER NOT NULL REFERENCES flats(id) ON DELETE CASCADE,
                    created_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_by INTEGER,
                    used_at TEXT
                );
                """
            )

    def _row(self, query: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self.connection() as connection:
            return connection.execute(query, params).fetchone()

    def _rows(self, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self.connection() as connection:
            return list(connection.execute(query, params).fetchall())

    def user(self, user_id: int) -> sqlite3.Row | None:
        return self._row("SELECT * FROM users WHERE user_id = ? AND active = 1", (user_id,))

    def has_admin(self) -> bool:
        return self._row(
            "SELECT user_id FROM users WHERE role = 'admin' AND active = 1 LIMIT 1"
        ) is not None

    def save_user(
        self,
        user_id: int,
        role: str,
        flat_id: int | None,
        first_name: str,
        username: str,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO users(user_id, role, flat_id, first_name, username, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    role = excluded.role,
                    flat_id = excluded.flat_id,
                    first_name = excluded.first_name,
                    username = excluded.username,
                    active = 1
                """,
                (user_id, role, flat_id, first_name, username, now_iso()),
            )

    def add_flat(self, name: str) -> int:
        with self.connection() as connection:
            cursor = connection.execute(
                "INSERT INTO flats(name, created_at) VALUES (?, ?)",
                (name, now_iso()),
            )
            flat_id = int(cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO tariffs(flat_id, water, electricity_threshold1,
                    electricity_tariff1, electricity_threshold2,
                    electricity_tariff2, electricity_tariff3, gas, tko, caprepair)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (flat_id, *(DEFAULT_TARIFFS.values())),
            )
            connection.execute(
                """
                INSERT INTO tariff_versions(
                    flat_id, effective_month, water, electricity_threshold1,
                    electricity_tariff1, electricity_threshold2, electricity_tariff2,
                    electricity_tariff3, gas, tko, caprepair, created_at
                ) VALUES (?, '01.1900', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (flat_id, *DEFAULT_TARIFFS.values(), now_iso()),
            )
            return flat_id

    def flats(self) -> list[sqlite3.Row]:
        return self._rows("SELECT * FROM flats ORDER BY id")

    def flat(self, flat_id: int) -> sqlite3.Row | None:
        return self._row("SELECT * FROM flats WHERE id = ?", (flat_id,))

    def users(self, flat_id: int | None = None) -> list[sqlite3.Row]:
        if flat_id is None:
            return self._rows(
                "SELECT u.*, f.name AS flat_name FROM users u LEFT JOIN flats f ON f.id = u.flat_id "
                "WHERE u.active = 1 ORDER BY u.role, u.user_id"
            )
        return self._rows(
            "SELECT u.*, f.name AS flat_name FROM users u LEFT JOIN flats f ON f.id = u.flat_id "
            "WHERE u.active = 1 AND u.flat_id = ? ORDER BY u.user_id",
            (flat_id,),
        )

    def remove_user(self, user_id: int) -> sqlite3.Row | None:
        old_user = self.user(user_id)
        if old_user is None:
            return None
        with self.connection() as connection:
            connection.execute(
                "UPDATE users SET active = 0 WHERE user_id = ?", (user_id,)
            )
            connection.execute("DELETE FROM admin_state WHERE user_id = ?", (user_id,))
        return old_user

    def selected_flat(self, user_id: int) -> int | None:
        row = self._row("SELECT selected_flat_id FROM admin_state WHERE user_id = ?", (user_id,))
        return int(row["selected_flat_id"]) if row and row["selected_flat_id"] else None

    def select_flat(self, user_id: int, flat_id: int) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO admin_state(user_id, selected_flat_id) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET selected_flat_id = excluded.selected_flat_id
                """,
                (user_id, flat_id),
            )

    def tariff_row(self, flat_id: int, effective_month: str | None = None) -> sqlite3.Row:
        if effective_month:
            row = self._row(
                """
                SELECT * FROM tariff_versions
                WHERE flat_id = ? AND effective_month <= ?
                ORDER BY effective_month DESC, id DESC LIMIT 1
                """,
                (flat_id, effective_month),
            )
            if row:
                return row
            row = self._row(
                """
                SELECT * FROM tariff_versions
                WHERE flat_id = ?
                ORDER BY effective_month ASC, id ASC LIMIT 1
                """,
                (flat_id,),
            )
            if row:
                return row
        row = self._row("SELECT * FROM tariffs WHERE flat_id = ?", (flat_id,))
        if row is None:
            raise ValueError(f"Tariffs not found for flat {flat_id}")
        return row

    def tariffs(self, flat_id: int, effective_month: str | None = None) -> TariffSet:
        row = self.tariff_row(flat_id, effective_month)
        return TariffSet(
            water=float(row["water"]),
            electricity_threshold1=float(row["electricity_threshold1"]),
            electricity_tariff1=float(row["electricity_tariff1"]),
            electricity_threshold2=float(row["electricity_threshold2"]),
            electricity_tariff2=float(row["electricity_tariff2"]),
            electricity_tariff3=float(row["electricity_tariff3"]),
            gas=float(row["gas"]),
            tko=float(row["tko"]),
            caprepair=float(row["caprepair"]),
        )

    def save_initial_reading(self, flat_id: int, meter: str, value: float) -> None:
        columns = {"electricity": "electricity", "water": "water", "gas": "gas"}
        if meter not in columns:
            raise ValueError("Unknown meter")
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO initial_readings(flat_id, electricity, water, gas, entered_at)
                VALUES (?, 0, 0, 0, ?)
                ON CONFLICT(flat_id) DO NOTHING
                """,
                (flat_id, now_iso()),
            )
            connection.execute(
                f"UPDATE initial_readings SET {columns[meter]} = ?, entered_at = ? WHERE flat_id = ?",
                (value, now_iso(), flat_id),
            )

    def initial_readings(self, flat_id: int) -> sqlite3.Row | None:
        return self._row("SELECT * FROM initial_readings WHERE flat_id = ?", (flat_id,))

    def previous_readings(self, flat_id: int, month: str) -> tuple[float, float, float]:
        row = self._row(
            """
            SELECT electricity, water, gas FROM meter_readings
            WHERE flat_id = ? AND month < ?
            ORDER BY month DESC LIMIT 1
            """,
            (flat_id, month),
        )
        if row:
            return float(row["electricity"]), float(row["water"]), float(row["gas"])
        initial = self.initial_readings(flat_id)
        if initial:
            return (
                float(initial["electricity"]),
                float(initial["water"]),
                float(initial["gas"]),
            )
        return 0.0, 0.0, 0.0

    def uk_amount(self, flat_id: int, month: str) -> float:
        row = self._row(
            "SELECT amount FROM uk_payments WHERE flat_id = ? AND month = ?",
            (flat_id, month),
        )
        return float(row["amount"]) if row else 0.0

    def set_uk(self, flat_id: int, month: str, amount: float) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO uk_payments(flat_id, month, amount, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(flat_id, month) DO UPDATE SET amount = excluded.amount,
                    updated_at = excluded.updated_at
                """,
                (flat_id, month, amount, now_iso()),
            )

    def save_tariffs(
        self,
        flat_id: int,
        effective_month: str,
        updates: dict[str, float],
    ) -> list[tuple[sqlite3.Row, float, float]]:
        current = dict(self.tariff_row(flat_id))
        current.update(updates)
        fields = [
            "water",
            "electricity_threshold1",
            "electricity_tariff1",
            "electricity_threshold2",
            "electricity_tariff2",
            "electricity_tariff3",
            "gas",
            "tko",
            "caprepair",
        ]
        values = [float(current[field]) for field in fields]
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO tariff_versions(
                    flat_id, effective_month, water, electricity_threshold1,
                    electricity_tariff1, electricity_threshold2, electricity_tariff2,
                    electricity_tariff3, gas, tko, caprepair, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(flat_id, effective_month) DO UPDATE SET
                    water = excluded.water,
                    electricity_threshold1 = excluded.electricity_threshold1,
                    electricity_tariff1 = excluded.electricity_tariff1,
                    electricity_threshold2 = excluded.electricity_threshold2,
                    electricity_tariff2 = excluded.electricity_tariff2,
                    electricity_tariff3 = excluded.electricity_tariff3,
                    gas = excluded.gas,
                    tko = excluded.tko,
                    caprepair = excluded.caprepair,
                    created_at = excluded.created_at
                """,
                (flat_id, effective_month, *values, now_iso()),
            )
            connection.execute(
                """
                UPDATE tariffs SET water = ?, electricity_threshold1 = ?,
                    electricity_tariff1 = ?, electricity_threshold2 = ?,
                    electricity_tariff2 = ?, electricity_tariff3 = ?, gas = ?,
                    tko = ?, caprepair = ? WHERE flat_id = ?
                """,
                (*values, flat_id),
            )
        return self.recalculate_from(flat_id, effective_month)

    def _calculation_row(self, flat_id: int, month: str) -> sqlite3.Row | None:
        return self._row(
            "SELECT * FROM meter_readings WHERE flat_id = ? AND month = ?",
            (flat_id, month),
        )

    def save_reading(
        self,
        flat_id: int,
        month: str,
        electricity: float,
        water: float,
        gas: float,
        submitted_by: int,
    ) -> sqlite3.Row:
        previous_electricity, previous_water, previous_gas = self.previous_readings(
            flat_id, month
        )
        tariff = self.tariffs(flat_id, month)
        calculation = calculate_reading(
            electricity,
            water,
            gas,
            previous_electricity,
            previous_water,
            previous_gas,
            tariff,
            self.uk_amount(flat_id, previous_month_key(month)),
        )
        existing = self._calculation_row(flat_id, month)
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO meter_readings(
                    flat_id, month, electricity, water, gas,
                    previous_electricity, previous_water, previous_gas,
                    electricity_consumption, water_consumption, gas_consumption,
                    electricity_amount, water_amount, gas_amount, tko_amount,
                    uk_amount, caprepair_amount, total_without_uk, total_with_uk,
                    total_for_admin, submitted_by, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(flat_id, month) DO UPDATE SET
                    electricity = excluded.electricity,
                    water = excluded.water,
                    gas = excluded.gas,
                    previous_electricity = excluded.previous_electricity,
                    previous_water = excluded.previous_water,
                    previous_gas = excluded.previous_gas,
                    electricity_consumption = excluded.electricity_consumption,
                    water_consumption = excluded.water_consumption,
                    gas_consumption = excluded.gas_consumption,
                    electricity_amount = excluded.electricity_amount,
                    water_amount = excluded.water_amount,
                    gas_amount = excluded.gas_amount,
                    tko_amount = excluded.tko_amount,
                    uk_amount = excluded.uk_amount,
                    caprepair_amount = excluded.caprepair_amount,
                    total_without_uk = excluded.total_without_uk,
                    total_with_uk = excluded.total_with_uk,
                    total_for_admin = excluded.total_for_admin,
                    submitted_by = excluded.submitted_by,
                    updated_at = excluded.updated_at
                """,
                (
                    flat_id,
                    month,
                    electricity,
                    water,
                    gas,
                    previous_electricity,
                    previous_water,
                    previous_gas,
                    calculation.electricity_consumption,
                    calculation.water_consumption,
                    calculation.gas_consumption,
                    calculation.electricity_amount,
                    calculation.water_amount,
                    calculation.gas_amount,
                    calculation.tko_amount,
                    calculation.uk_amount,
                    calculation.caprepair_amount,
                    calculation.total_without_uk,
                    calculation.total_with_uk,
                    calculation.total_for_admin,
                    submitted_by,
                    now_iso(),
                ),
            )
            reading_id = existing["id"] if existing else connection.execute(
                "SELECT id FROM meter_readings WHERE flat_id = ? AND month = ?",
                (flat_id, month),
            ).fetchone()["id"]
            amounts = {
                "water": calculation.water_amount,
                "electricity": calculation.electricity_amount,
                "gas": calculation.gas_amount,
                "tko": calculation.tko_amount,
                "uk": calculation.uk_amount,
                "caprepair": calculation.caprepair_amount,
            }
            for service, amount in amounts.items():
                connection.execute(
                    """
                    INSERT INTO payment_status(reading_id, service, amount, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(reading_id, service) DO UPDATE SET
                        amount = excluded.amount, updated_at = excluded.updated_at
                    """,
                    (reading_id, service, amount, now_iso()),
                )
        return self._calculation_row(flat_id, month)  # type: ignore[return-value]

    def recalculate_from(self, flat_id: int, effective_month: str) -> list[tuple[sqlite3.Row, float, float]]:
        readings = self._rows(
            "SELECT * FROM meter_readings WHERE flat_id = ? AND month >= ? ORDER BY month",
            (flat_id, effective_month),
        )
        changes: list[tuple[sqlite3.Row, float, float]] = []
        for reading in readings:
            before = float(reading["total_with_uk"])
            self.save_reading(
                flat_id,
                reading["month"],
                float(reading["electricity"]),
                float(reading["water"]),
                float(reading["gas"]),
                int(reading["submitted_by"]),
            )
            updated = self._calculation_row(flat_id, reading["month"])
            if updated is not None:
                changes.append((updated, before, float(updated["total_with_uk"])))
        return changes

    def reading(self, flat_id: int, month: str) -> sqlite3.Row | None:
        return self._calculation_row(flat_id, month)

    def readings(self, flat_id: int, month: str | None = None) -> list[sqlite3.Row]:
        if month:
            return self._rows(
                "SELECT * FROM meter_readings WHERE flat_id = ? AND month = ?",
                (flat_id, month),
            )
        return self._rows(
            "SELECT * FROM meter_readings WHERE flat_id = ? ORDER BY month DESC",
            (flat_id,),
        )

    def payment_statuses(self, reading_id: int) -> dict[str, sqlite3.Row]:
        rows = self._rows(
            "SELECT * FROM payment_status WHERE reading_id = ?", (reading_id,)
        )
        return {str(row["service"]): row for row in rows}

    def toggle_payment(self, reading_id: int, service: str) -> bool:
        row = self._row(
            "SELECT paid FROM payment_status WHERE reading_id = ? AND service = ?",
            (reading_id, service),
        )
        if row is None:
            raise ValueError("Payment status not found")
        paid = not bool(row["paid"])
        with self.connection() as connection:
            connection.execute(
                "UPDATE payment_status SET paid = ?, updated_at = ? WHERE reading_id = ? AND service = ?",
                (int(paid), now_iso(), reading_id, service),
            )
        return paid

    def unpaid_services(self, flat_id: int, month: str) -> list[sqlite3.Row]:
        return self._rows(
            """
            SELECT ps.*, mr.month, mr.flat_id FROM payment_status ps
            JOIN meter_readings mr ON mr.id = ps.reading_id
            WHERE mr.flat_id = ? AND mr.month = ? AND ps.paid = 0
            ORDER BY ps.service
            """,
            (flat_id, month),
        )

    def audit(
        self, flat_id: int, actor_id: int, actor_name: str, action: str, details: str
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO audit_log(flat_id, actor_id, actor_name, action, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (flat_id, actor_id, actor_name, action, details, now_iso()),
            )

    def audit_entries(self, flat_id: int, limit: int = 20) -> list[sqlite3.Row]:
        return self._rows(
            "SELECT * FROM audit_log WHERE flat_id = ? ORDER BY id DESC LIMIT ?",
            (flat_id, limit),
        )

    def delete_flat_history(self, flat_id: int) -> None:
        with self.connection() as connection:
            connection.execute(
                "DELETE FROM payment_status WHERE reading_id IN "
                "(SELECT id FROM meter_readings WHERE flat_id = ?)",
                (flat_id,),
            )
            connection.execute("DELETE FROM meter_readings WHERE flat_id = ?", (flat_id,))
            connection.execute("DELETE FROM uk_payments WHERE flat_id = ?", (flat_id,))
            connection.execute("DELETE FROM tariff_versions WHERE flat_id = ?", (flat_id,))
            connection.execute("DELETE FROM audit_log WHERE flat_id = ?", (flat_id,))

    def tenants(self, flat_id: int) -> list[sqlite3.Row]:
        return self._rows(
            "SELECT * FROM users WHERE active = 1 AND role = 'tenant' AND flat_id = ?",
            (flat_id,),
        )

    def admins(self) -> list[sqlite3.Row]:
        return self._rows("SELECT * FROM users WHERE active = 1 AND role = 'admin'")

    def create_invite(
        self,
        token: str,
        flat_id: int,
        created_by: int,
        expires_at: str,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO invite_tokens(
                    token, flat_id, created_by, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (token, flat_id, created_by, now_iso(), expires_at),
            )

    def claim_invite(self, token: str, user_id: int) -> sqlite3.Row | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT i.*, f.name AS flat_name
                FROM invite_tokens i
                JOIN flats f ON f.id = i.flat_id
                WHERE i.token = ? AND i.used_at IS NULL AND i.expires_at > ?
                """,
                (token, now_iso()),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE invite_tokens SET used_by = ?, used_at = ? "
                "WHERE token = ? AND used_at IS NULL",
                (user_id, now_iso(), token),
            )
            return row

    def flats_without_reading(self, month: str) -> list[sqlite3.Row]:
        return self._rows(
            """
            SELECT f.id, f.name FROM flats f
            LEFT JOIN meter_readings mr ON mr.flat_id = f.id AND mr.month = ?
            JOIN users u ON u.flat_id = f.id AND u.role = 'tenant' AND u.active = 1
            WHERE mr.id IS NULL GROUP BY f.id ORDER BY f.id
            """,
            (month,),
        )