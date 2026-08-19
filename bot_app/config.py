from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    initial_admin_code: str
    database_path: str

    @classmethod
    def from_environment(cls) -> "Settings":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        admin_code = os.environ.get("INITIAL_ADMIN_CODE", "").strip()
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
        if not admin_code:
            raise RuntimeError("INITIAL_ADMIN_CODE is not configured")
        return cls(
            telegram_token=token,
            initial_admin_code=admin_code,
            database_path=os.environ.get(
                "BOT_DATABASE_PATH", "data/communal_bot.sqlite3"
            ),
        )