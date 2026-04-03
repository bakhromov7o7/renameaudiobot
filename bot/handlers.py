from __future__ import annotations

import logging
from pathlib import Path

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Document, FSInputFile, Message

from bot.config import Settings
from bot.database import SessionRepository
from bot.models import UserSession
from bot.services.audio_metadata import AudioMetadataService, CoverImageError, UnsupportedFormatError
from bot.services.storage import FileStorage

logger = logging.getLogger(__name__)

SUPERADMIN_ID = 7402633908
SUPERADMIN_USERNAME = "bakhromov7o7"


def create_router(
    session_repository: SessionRepository,
    file_storage: FileStorage,
    audio_service: AudioMetadataService,
    settings: Settings,
) -> Router:
    router = Router()

    @router.message(CommandStart())
    async def start_command(message: Message) -> None:
        if message.from_user is None:
            return

        await session_repository.track_user(message.from_user.id)
        await _cleanup_session(
            user_id=message.from_user.id,
            session_repository=session_repository,
            file_storage=file_storage,
        )
        await message.answer(
            "Salom! Jarayon 4 qadamdan iborat:\n"
            "1. Rasm yuboring.\n"
            "2. Audio uchun nom yuboring.\n"
            "3. Ijrochi nomini yuboring.\n"
            "4. Audio yuboring."
        )

    @router.message(Command("cancel"))
    async def cancel_command(message: Message) -> None:
        if message.from_user is None:
            return

        await session_repository.track_user(message.from_user.id)
        await _cleanup_session(
            user_id=message.from_user.id,
            session_repository=session_repository,
            file_storage=file_storage,
        )
        await message.answer("Joriy sessiya tozalandi. Qaytadan boshlash uchun rasm yuboring.")

    @router.message(Command("superadmin"))
    async def superadmin_command(message: Message) -> None:
        if message.from_user is None:
            return

        await session_repository.track_user(message.from_user.id)

        if not _is_superadmin(message):
            await message.answer("Bu buyruq faqat superadmin uchun.")
            return

        total_users, total_renames = await session_repository.get_usage_stats()
        await message.answer(
            "Superadmin statistika:\n"
            f"Foydalanuvchilar soni: {total_users}\n"
            f"Rename qilingan audio soni: {total_renames}"
        )

    @router.message()
    async def handle_message(message: Message) -> None:
        if message.from_user is None:
            return

        user_id = message.from_user.id
        await session_repository.track_user(user_id)

        if message.text and message.text.startswith("/"):
            await message.answer("Noma'lum buyruq. Yangi jarayon uchun /start yoki bekor qilish uchun /cancel yuboring.")
            return

        session = await session_repository.get_or_create_session(user_id)

        if session.step == "processing":
            await message.answer("Audio fayl qayta ishlanmoqda. Biroz kutib turing yoki /cancel yuboring.")
            return

        if _is_image_message(message):
            await _handle_image_message(
                message=message,
                session=session,
                session_repository=session_repository,
                file_storage=file_storage,
                audio_service=audio_service,
            )
            return

        if _is_audio_message(message):
            await _handle_audio_message(
                message=message,
                session=session,
                session_repository=session_repository,
                file_storage=file_storage,
                audio_service=audio_service,
                settings=settings,
            )
            return

        if message.text:
            if session.step == "awaiting_name":
                await _handle_name_message(
                    message=message,
                    session=session,
                    session_repository=session_repository,
                )
            elif session.step == "awaiting_artist":
                await _handle_artist_message(
                    message=message,
                    session=session,
                    session_repository=session_repository,
                )
            else:
                await message.answer(_prompt_for_step(session.step))
            return

        await message.answer(_prompt_for_step(session.step))

    return router


async def _handle_image_message(
    *,
    message: Message,
    session: UserSession,
    session_repository: SessionRepository,
    file_storage: FileStorage,
    audio_service: AudioMetadataService,
) -> None:
    if message.from_user is None:
        return

    source = _extract_image_source(message)
    if source is None:
        await message.answer("Rasmni topa olmadim. Iltimos, qaytadan yuboring.")
        return

    image_path = file_storage.make_path(
        message.from_user.id,
        prefix="image",
        original_name=source.file_name,
        default_extension=".jpg",
        mime_type=source.mime_type,
    )
    try:
        await file_storage.download(message.bot, telegram_file=source.telegram_file, destination=image_path)
        await audio_service.validate_cover(image_path)
    except CoverImageError:
        file_storage.cleanup_paths(image_path)
        await message.answer("Rasm fayli yaroqsiz. Iltimos, boshqa rasm yuboring.")
        return
    except Exception:
        logger.exception("Rasmni saqlashda xatolik yuz berdi", extra={"user_id": message.from_user.id})
        file_storage.cleanup_paths(image_path)
        await message.answer("Rasmni saqlashda xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.")
        return

    previous_image_path = session.image_path
    previous_audio_path = session.audio_path
    try:
        await session_repository.save_image(message.from_user.id, str(image_path))
    except Exception:
        logger.exception("Rasm yo'lini bazaga yozishda xatolik yuz berdi", extra={"user_id": message.from_user.id})
        file_storage.cleanup_paths(image_path)
        await message.answer("Rasmni saqlashda xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.")
        return

    file_storage.cleanup_paths(previous_image_path, previous_audio_path)
    await message.answer("Rasm saqlandi. Endi audio uchun nom yuboring.")


async def _handle_name_message(
    *,
    message: Message,
    session: UserSession,
    session_repository: SessionRepository,
) -> None:
    if message.from_user is None:
        return

    if not session.image_path or session.step == "awaiting_image":
        await message.answer("Avval rasm yuboring.")
        return

    track_name = (message.text or "").strip()
    if not track_name:
        await message.answer("Nom bo'sh bo'lmasligi kerak. Iltimos, audio nomini matn ko'rinishida yuboring.")
        return

    if len(track_name) > 120:
        track_name = track_name[:120].rstrip()

    await session_repository.save_name(message.from_user.id, track_name)
    await message.answer("Nom saqlandi. Endi ijrochi yoki artist nomini yuboring.")


async def _handle_artist_message(
    *,
    message: Message,
    session: UserSession,
    session_repository: SessionRepository,
) -> None:
    if message.from_user is None:
        return

    if not session.desired_name or session.step in {"awaiting_image", "awaiting_name"}:
        await message.answer("Avval audio nomini yuboring.")
        return

    artist_name = (message.text or "").strip()
    if not artist_name:
        await message.answer("Ijrochi nomi bo'sh bo'lmasligi kerak. Iltimos, matn yuboring.")
        return

    if len(artist_name) > 120:
        artist_name = artist_name[:120].rstrip()

    await session_repository.save_artist(message.from_user.id, artist_name)
    await message.answer("Ijrochi saqlandi. Endi audio yuboring.")


async def _handle_audio_message(
    *,
    message: Message,
    session: UserSession,
    session_repository: SessionRepository,
    file_storage: FileStorage,
    audio_service: AudioMetadataService,
    settings: Settings,
) -> None:
    if message.from_user is None:
        return

    if session.step == "processing":
        await message.answer("Oldingi audio hali qayta ishlanmoqda. Biroz kutib turing.")
        return

    if not session.image_path:
        await message.answer("Avval rasm yuboring.")
        return

    if not session.desired_name or session.step == "awaiting_name":
        await message.answer("Rasm saqlandi. Endi audio uchun nom yuboring.")
        return

    if not session.desired_artist or session.step == "awaiting_artist":
        await message.answer("Nom saqlandi. Endi ijrochi yoki artist nomini yuboring.")
        return

    source = _extract_audio_source(message)
    if source is None:
        await message.answer("Audio fayl topilmadi. Iltimos, oddiy audio yoki audio document yuboring.")
        return

    if source.file_size is not None and source.file_size > settings.max_file_size_bytes:
        await message.answer(
            f"Audio fayl juda katta. Maksimal ruxsat etilgan hajm: {settings.max_file_size_mb} MB."
        )
        return

    audio_path = file_storage.make_path(
        message.from_user.id,
        prefix="audio",
        original_name=source.file_name,
        default_extension=source.default_extension,
        mime_type=source.mime_type,
    )
    processed_audio = None
    should_cleanup_session = False
    try:
        await file_storage.download(message.bot, telegram_file=source.telegram_file, destination=audio_path)
        await session_repository.save_audio(message.from_user.id, str(audio_path))
        await message.answer("Audio qabul qilindi. Fayl tayyorlanmoqda...")

        processed_audio = await audio_service.build_processed_audio(
            audio_path=audio_path,
            image_path=Path(session.image_path),
            desired_name=session.desired_name,
            desired_artist=session.desired_artist,
        )

        clean_title = audio_service.build_visible_name(session.desired_name)
        clean_artist = audio_service.build_visible_artist(session.desired_artist)
        if processed_audio.send_as_audio:
            upload = FSInputFile(processed_audio.path, filename=clean_title)
            await message.answer_audio(
                audio=upload,
                title=clean_title,
                performer=clean_artist,
                caption="Tayyor audio fayl.",
            )
        else:
            upload = FSInputFile(processed_audio.path, filename=processed_audio.download_name)
            await message.answer_document(
                document=upload,
                caption="Tayyor audio fayl.",
            )

        await session_repository.increment_audio_rename_count()
        await message.answer("Jarayon yakunlandi. Vaqtinchalik fayllar va DB yozuvi o'chirildi.")
        should_cleanup_session = True
    except UnsupportedFormatError as error:
        logger.warning(
            "Audio formatini yangilab bo'lmadi: user_id=%s error=%s",
            message.from_user.id,
            str(error),
        )
        await _reset_after_audio_failure(
            user_id=message.from_user.id,
            session_repository=session_repository,
            file_storage=file_storage,
            extra_paths=[audio_path, processed_audio.path] if processed_audio is not None else [audio_path],
        )
        await message.answer(
            f"Audio formatini yangilab bo'lmadi: {error}\n"
            "Nom, artist va rasm saqlandi. Iltimos, boshqa audio yuborib ko'ring."
        )
    except Exception:
        logger.exception("Audio qayta ishlashda xatolik yuz berdi", extra={"user_id": message.from_user.id})
        await _reset_after_audio_failure(
            user_id=message.from_user.id,
            session_repository=session_repository,
            file_storage=file_storage,
            extra_paths=[audio_path, processed_audio.path] if processed_audio is not None else [audio_path],
        )
        await message.answer(
            "Audio faylni tayyorlashda xatolik yuz berdi. Nom, artist va rasm saqlandi. Iltimos, audioni qayta yuboring."
        )
    finally:
        if should_cleanup_session:
            extra_paths: list[Path] = [audio_path]
            if processed_audio is not None:
                extra_paths.append(processed_audio.path)
            await _cleanup_session(
                user_id=message.from_user.id,
                session_repository=session_repository,
                file_storage=file_storage,
                extra_paths=extra_paths,
            )


async def _cleanup_session(
    *,
    user_id: int,
    session_repository: SessionRepository,
    file_storage: FileStorage,
    extra_paths: list[Path] | None = None,
) -> None:
    session = await session_repository.get_session(user_id)
    cleanup_targets: list[str | Path | None] = []

    if session is not None:
        cleanup_targets.extend([session.image_path, session.audio_path])

    if extra_paths:
        cleanup_targets.extend(extra_paths)

    file_storage.cleanup_paths(*cleanup_targets)
    await session_repository.delete_session(user_id)


async def _reset_after_audio_failure(
    *,
    user_id: int,
    session_repository: SessionRepository,
    file_storage: FileStorage,
    extra_paths: list[Path] | None = None,
) -> None:
    file_storage.cleanup_paths(*(extra_paths or []))
    await session_repository.reset_to_awaiting_audio(user_id)


def _prompt_for_step(step: str) -> str:
    prompts = {
        "awaiting_image": "Avval rasm yuboring.",
        "awaiting_name": "Endi audio uchun nom yuboring.",
        "awaiting_artist": "Endi ijrochi yoki artist nomini yuboring.",
        "awaiting_audio": "Endi audio yuboring.",
        "processing": "Audio fayl qayta ishlanmoqda. Biroz kutib turing.",
    }
    return prompts.get(step, "Iltimos, rasm yuborib jarayonni qayta boshlang.")


def _is_superadmin(message: Message) -> bool:
    if message.from_user is None:
        return False

    username = (message.from_user.username or "").strip().lower()
    return message.from_user.id == SUPERADMIN_ID or username == SUPERADMIN_USERNAME


def _is_image_message(message: Message) -> bool:
    if message.photo:
        return True

    document = message.document
    if document is None:
        return False
    return _is_image_document(document)


def _is_audio_message(message: Message) -> bool:
    if message.audio or message.voice:
        return True

    document = message.document
    if document is None:
        return False
    return _is_audio_document(document)


def _is_image_document(document: Document) -> bool:
    mime_type = (document.mime_type or "").lower()
    suffix = Path(document.file_name or "").suffix.lower()
    return mime_type.startswith("image/") or suffix in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _is_audio_document(document: Document) -> bool:
    mime_type = (document.mime_type or "").lower()
    suffix = Path(document.file_name or "").suffix.lower()
    return mime_type.startswith("audio/") or suffix in {".mp3", ".m4a", ".mp4", ".flac", ".wav", ".ogg", ".opus", ".oga", ".webm", ".mka", ".mkv"}


class _IncomingFile:
    def __init__(
        self,
        *,
        telegram_file: object,
        file_name: str | None,
        mime_type: str | None,
        file_size: int | None,
        default_extension: str,
    ) -> None:
        self.telegram_file = telegram_file
        self.file_name = file_name
        self.mime_type = mime_type
        self.file_size = file_size
        self.default_extension = default_extension


def _extract_image_source(message: Message) -> _IncomingFile | None:
    if message.photo:
        return _IncomingFile(
            telegram_file=message.photo[-1],
            file_name="cover.jpg",
            mime_type="image/jpeg",
            file_size=message.photo[-1].file_size,
            default_extension=".jpg",
        )

    if message.document and _is_image_document(message.document):
        return _IncomingFile(
            telegram_file=message.document,
            file_name=message.document.file_name,
            mime_type=message.document.mime_type,
            file_size=message.document.file_size,
            default_extension=".jpg",
        )

    return None


def _extract_audio_source(message: Message) -> _IncomingFile | None:
    if message.audio:
        return _IncomingFile(
            telegram_file=message.audio,
            file_name=message.audio.file_name,
            mime_type=message.audio.mime_type,
            file_size=message.audio.file_size,
            default_extension=".mp3",
        )

    if message.voice:
        return _IncomingFile(
            telegram_file=message.voice,
            file_name="voice.ogg",
            mime_type=message.voice.mime_type,
            file_size=message.voice.file_size,
            default_extension=".ogg",
        )

    if message.document and _is_audio_document(message.document):
        return _IncomingFile(
            telegram_file=message.document,
            file_name=message.document.file_name,
            mime_type=message.document.mime_type,
            file_size=message.document.file_size,
            default_extension=".mp3",
        )

    return None
