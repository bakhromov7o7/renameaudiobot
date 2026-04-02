from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

from bot.config import Settings
from bot.database import SessionRepository
from bot.handlers import create_router
from bot.services.audio_metadata import AudioMetadataService
from bot.services.storage import FileStorage


async def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    file_storage = FileStorage(settings.storage_dir)
    file_storage.ensure_base_dir()

    session_repository = SessionRepository(settings.database_url)
    await session_repository.connect()
    await session_repository.init_schema()

    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_router(
            session_repository=session_repository,
            file_storage=file_storage,
            audio_service=AudioMetadataService(),
            settings=settings,
        )
    )

    bot = Bot(token=settings.bot_token)
    try:
        await dispatcher.start_polling(bot)
    finally:
        await session_repository.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
