from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from uuid import uuid4

from aiogram import Bot


class FileStorage:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir.resolve()

    def ensure_base_dir(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def make_path(
        self,
        user_id: int,
        *,
        prefix: str,
        original_name: str | None,
        default_extension: str,
        mime_type: str | None = None,
    ) -> Path:
        extension = self._resolve_extension(
            original_name=original_name,
            default_extension=default_extension,
            mime_type=mime_type,
        )
        user_dir = self.root_dir / str(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir / f"{prefix}_{uuid4().hex}{extension}"

    async def download(
        self,
        bot: Bot,
        *,
        telegram_file: object,
        destination: Path,
    ) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        await bot.download(telegram_file, destination=destination)
        return destination

    def cleanup_paths(self, *paths: str | Path | None) -> None:
        candidate_dirs: set[Path] = set()

        for raw_path in paths:
            if raw_path is None:
                continue
            resolved = self._resolve_inside_root(raw_path)
            if resolved is None:
                continue
            if resolved.exists():
                resolved.unlink()
            candidate_dirs.add(resolved.parent)

        for directory in sorted(candidate_dirs, key=lambda item: len(item.parts), reverse=True):
            self._remove_empty_directories(directory)

    def _remove_empty_directories(self, directory: Path) -> None:
        current = directory
        while current != self.root_dir and current.exists():
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    def _resolve_inside_root(self, raw_path: str | Path) -> Path | None:
        resolved = Path(raw_path).expanduser().resolve(strict=False)
        if not self._is_inside_root(resolved):
            return None
        return resolved

    def _is_inside_root(self, path: Path) -> bool:
        try:
            path.relative_to(self.root_dir)
        except ValueError:
            return False
        return True

    @staticmethod
    def _resolve_extension(
        *,
        original_name: str | None,
        default_extension: str,
        mime_type: str | None,
    ) -> str:
        extension = Path(original_name or "").suffix.lower()
        if extension:
            return FileStorage._sanitize_extension(extension)

        guessed = mimetypes.guess_extension(mime_type or "")
        if guessed:
            return FileStorage._sanitize_extension(guessed)

        return FileStorage._sanitize_extension(default_extension)

    @staticmethod
    def _sanitize_extension(extension: str) -> str:
        clean = extension.strip().lower()
        if not clean.startswith("."):
            clean = f".{clean}"
        clean = re.sub(r"[^a-z0-9.]+", "", clean)
        if clean in {"", "."}:
            return ".bin"
        return clean

