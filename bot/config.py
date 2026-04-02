from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str
    storage_dir: Path
    max_file_size_mb: int
    log_level: str

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()

        bot_token = os.getenv("BOT_TOKEN", "").strip()
        database_url = os.getenv("DATABASE_URL", "").strip()
        storage_dir = Path(os.getenv("STORAGE_DIR", "storage/tmp")).expanduser().resolve()
        max_file_size_mb = int(os.getenv("MAX_FILE_SIZE_MB", "20"))
        log_level = os.getenv("LOG_LEVEL", "INFO").upper()

        if not bot_token:
            raise ValueError("BOT_TOKEN topilmadi. Uni .env faylida belgilang.")

        if not database_url:
            raise ValueError("DATABASE_URL topilmadi. Uni .env faylida belgilang.")

        return cls(
            bot_token=bot_token,
            database_url=database_url,
            storage_dir=storage_dir,
            max_file_size_mb=max_file_size_mb,
            log_level=log_level,
        )
