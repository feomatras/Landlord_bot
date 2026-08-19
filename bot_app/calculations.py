from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TariffSet:
    water: float
    electricity_threshold1: float
    electricity_tariff1: float
    electricity_threshold2: float
    electricity_tariff2: float
    electricity_tariff3: float
    gas: float
    tko: float
    caprepair: float


@dataclass(frozen=True)
class ReadingCalculation:
    electricity_consumption: float
    water_consumption: float
    gas_consumption: float
    electricity_amount: float
    water_amount: float
    gas_amount: float
    tko_amount: float
    uk_amount: float
    caprepair_amount: float
    total_without_uk: float
    total_with_uk: float
    total_for_admin: float


def electricity_cost(consumption: float, tariffs: TariffSet) -> float:
    """Calculate a progressive electricity tariff."""
    if consumption <= tariffs.electricity_threshold1:
        return consumption * tariffs.electricity_tariff1
    if consumption <= tariffs.electricity_threshold2:
        return (
            tariffs.electricity_threshold1 * tariffs.electricity_tariff1
            + (consumption - tariffs.electricity_threshold1)
            * tariffs.electricity_tariff2
        )
    return (
        tariffs.electricity_threshold1 * tariffs.electricity_tariff1
        + (tariffs.electricity_threshold2 - tariffs.electricity_threshold1)
        * tariffs.electricity_tariff2
        + (consumption - tariffs.electricity_threshold2)
        * tariffs.electricity_tariff3
    )


def calculate_reading(
    current_electricity: float,
    current_water: float,
    current_gas: float,
    previous_electricity: float,
    previous_water: float,
    previous_gas: float,
    tariffs: TariffSet,
    uk_amount: float = 0.0,
) -> ReadingCalculation:
    electricity_consumption = current_electricity - previous_electricity
    water_consumption = current_water - previous_water
    gas_consumption = current_gas - previous_gas

    electricity_amount = electricity_cost(electricity_consumption, tariffs)
    water_amount = water_consumption * tariffs.water
    gas_amount = gas_consumption * tariffs.gas
    total_without_uk = (
        water_amount
        + electricity_amount
        + gas_amount
        + tariffs.tko
    )
    total_with_uk = total_without_uk + uk_amount
    total_for_admin = total_with_uk + tariffs.caprepair

    return ReadingCalculation(
        electricity_consumption=electricity_consumption,
        water_consumption=water_consumption,
        gas_consumption=gas_consumption,
        electricity_amount=electricity_amount,
        water_amount=water_amount,
        gas_amount=gas_amount,
        tko_amount=tariffs.tko,
        uk_amount=uk_amount,
        caprepair_amount=tariffs.caprepair,
        total_without_uk=total_without_uk,
        total_with_uk=total_with_uk,
        total_for_admin=total_for_admin,
    )


def month_key(year: int, month: int) -> str:
    return f"{month:02d}.{year:04d}"


def previous_month_key(month: str) -> str:
    month_number, year = (int(part) for part in month.split("."))
    if month_number == 1:
        return month_key(year - 1, 12)
    return month_key(year, month_number - 1)


def next_month_key(month: str) -> str:
    month_number, year = (int(part) for part in month.split("."))
    if month_number == 12:
        return month_key(year + 1, 1)
    return month_key(year, month_number + 1)