from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from mutagen import File as MutagenFile
from mutagen import MutagenError
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TIT2, TPE1
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggflac import OggFLAC
from mutagen.oggopus import OggOpus
from mutagen.oggspeex import OggSpeex
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)


class UnsupportedFormatError(Exception):
    """Raised when the audio format cannot be rewritten safely."""


class CoverImageError(Exception):
    """Raised when the image cannot be converted into a cover."""


@dataclass(frozen=True)
class ProcessedAudio:
    path: Path
    download_name: str
    send_as_audio: bool


@dataclass(frozen=True)
class CoverImage:
    data: bytes
    mime: str
    mp4_format: int
    width: int
    height: int
    depth: int

    def as_flac_picture_base64(self) -> str:
        picture = Picture()
        picture.type = 3
        picture.desc = "Cover"
        picture.mime = self.mime
        picture.width = self.width
        picture.height = self.height
        picture.depth = self.depth
        picture.data = self.data
        return base64.b64encode(picture.write()).decode("ascii")


@dataclass(frozen=True)
class DetectedAudioFormat:
    family: str
    extension: str
    format_name: str


@dataclass(frozen=True)
class ProbedMediaInfo:
    format_name: str | None
    audio_codec: str | None
    has_audio: bool


class AudioMetadataService:
    AUDIO_MESSAGE_EXTENSIONS = {".mp3", ".m4a"}
    MP4_EXTENSIONS = {".m4a", ".mp4", ".m4b"}
    OGG_EXTENSIONS = {".ogg", ".opus", ".oga"}
    MATROSKA_EXTENSIONS = {".webm", ".mka", ".mkv"}
    SUPPORTED_FAMILIES = {"mp3", "mp4", "flac", "wav", "ogg", "matroska"}
    KNOWN_AUDIO_SUFFIXES = {".mp3", ".m4a", ".mp4", ".m4b", ".flac", ".wav", ".ogg", ".opus", ".oga", ".webm", ".mka", ".mkv"}

    def __init__(self, ffmpeg_path: str | None = None, ffprobe_path: str | None = None) -> None:
        self.ffmpeg_path = ffmpeg_path or shutil.which("ffmpeg")
        self.ffprobe_path = ffprobe_path or shutil.which("ffprobe")

    async def validate_cover(self, image_path: Path) -> None:
        await asyncio.to_thread(self._load_cover, image_path)

    async def build_processed_audio(
        self,
        *,
        audio_path: Path,
        image_path: Path,
        desired_name: str,
        desired_artist: str,
    ) -> ProcessedAudio:
        return await asyncio.to_thread(
            self._build_processed_audio_sync,
            audio_path,
            image_path,
            desired_name,
            desired_artist,
        )

    def _build_processed_audio_sync(
        self,
        audio_path: Path,
        image_path: Path,
        desired_name: str,
        desired_artist: str,
    ) -> ProcessedAudio:
        detected = self._detect_audio_format(audio_path)
        if detected.family not in self.SUPPORTED_FAMILIES:
            raise UnsupportedFormatError(
                "Bu audio konteyner hozircha qo'llab-quvvatlanmaydi. Tavsiya etilgan formatlar: mp3, m4a, mp4, flac, wav, ogg, opus."
            )

        output_path = audio_path.with_name(f"ready_{uuid4().hex}{detected.extension}")
        strategies = self._build_strategy_order(detected.family)
        last_error: Exception | None = None
        cover: CoverImage | None = None

        for strategy in strategies:
            output_path.unlink(missing_ok=True)
            try:
                if strategy == "ffmpeg":
                    cover = cover or self._load_cover(image_path)
                    self._write_with_ffmpeg(
                        audio_path=audio_path,
                        image_path=image_path,
                        desired_name=desired_name,
                        desired_artist=desired_artist,
                        output_path=output_path,
                        detected=detected,
                        cover=cover,
                    )
                else:
                    cover = cover or self._load_cover(image_path)
                    shutil.copy2(audio_path, output_path)
                    self._write_with_mutagen(
                        audio_path=output_path,
                        cover=cover,
                        desired_name=desired_name,
                        desired_artist=desired_artist,
                        family=detected.family,
                    )
                last_error = None
                break
            except Exception as error:
                last_error = error
                output_path.unlink(missing_ok=True)
                logger.warning(
                    "Audio metadata strategy failed: strategy=%s family=%s format=%s path=%s error=%r",
                    strategy,
                    detected.family,
                    detected.format_name,
                    str(audio_path),
                    error,
                )

        if last_error is not None:
            raise self._map_processing_error(last_error, detected.format_name) from last_error

        logger.info(
            "Audio metadata processed: input=%s detected_family=%s detected_format=%s output=%s",
            str(audio_path),
            detected.family,
            detected.format_name,
            str(output_path),
        )

        return ProcessedAudio(
            path=output_path,
            download_name=self.build_download_name(desired_name, detected.extension),
            send_as_audio=detected.extension in self.AUDIO_MESSAGE_EXTENSIONS,
        )

    def _build_strategy_order(self, family: str) -> list[str]:
        if family == "matroska" and self.ffmpeg_path:
            return ["ffmpeg"]
        if family in {"mp3", "mp4", "flac", "matroska"} and self.ffmpeg_path:
            return ["ffmpeg", "mutagen"]
        return ["mutagen"]

    def _detect_audio_format(self, audio_path: Path) -> DetectedAudioFormat:
        media_info = self._probe_media_info(audio_path)
        format_name = media_info.format_name if media_info is not None else None
        audio_codec = media_info.audio_codec if media_info is not None else None

        if media_info is not None and not media_info.has_audio:
            raise UnsupportedFormatError("Yuborilgan faylda audio oqimi topilmadi.")

        family = self._family_from_probe(format_name, audio_codec)

        if family is None:
            family = self._family_from_mutagen(audio_path)

        if family is None:
            extension = audio_path.suffix.lower()
            family = self._family_from_extension(extension)
            format_name = format_name or extension.lstrip(".") or "unknown"
        else:
            format_name = format_name or family

        if family is None:
            raise UnsupportedFormatError("Audio formatini aniqlab bo'lmadi.")

        return DetectedAudioFormat(
            family=family,
            extension=self._canonical_extension(family, audio_path.suffix.lower()),
            format_name=format_name,
        )

    def _probe_media_info(self, audio_path: Path) -> ProbedMediaInfo | None:
        if not self.ffprobe_path:
            return None

        command = [
            self.ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=format_name:stream=codec_type,codec_name",
            "-of",
            "json",
            str(audio_path),
        ]
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return None

        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return None

        format_name = payload.get("format", {}).get("format_name")
        streams = payload.get("streams", []) or []
        audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        audio_codec = audio_stream.get("codec_name") if audio_stream is not None else None
        return ProbedMediaInfo(
            format_name=format_name,
            audio_codec=audio_codec,
            has_audio=audio_stream is not None,
        )

    def _family_from_probe(self, format_name: str | None, audio_codec: str | None) -> str | None:
        if not format_name:
            return self._family_from_codec(audio_codec)

        tokens = {token.strip().lower() for token in format_name.split(",") if token.strip()}
        if "mp3" in tokens:
            return "mp3"
        if tokens.intersection({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}):
            return "mp4"
        if "flac" in tokens:
            return "flac"
        if tokens.intersection({"wav", "wave"}):
            return "wav"
        if tokens.intersection({"matroska", "webm"}):
            return "matroska"
        if "ogg" in tokens:
            return "ogg"
        return self._family_from_codec(audio_codec)

    def _family_from_codec(self, audio_codec: str | None) -> str | None:
        if not audio_codec:
            return None

        codec = audio_codec.strip().lower()
        if codec == "mp3":
            return "mp3"
        if codec in {"aac", "alac", "ac3", "eac3", "mp4a"}:
            return "mp4"
        if codec == "flac":
            return "flac"
        if codec in {"opus", "vorbis"}:
            return "ogg"
        if codec.startswith("pcm_"):
            return "wav"
        return None

    def _family_from_mutagen(self, audio_path: Path) -> str | None:
        try:
            audio = MutagenFile(audio_path)
        except MutagenError:
            return None

        if audio is None:
            return None
        if isinstance(audio, MP3):
            return "mp3"
        if isinstance(audio, MP4):
            return "mp4"
        if isinstance(audio, FLAC):
            return "flac"
        if isinstance(audio, WAVE):
            return "wav"
        if isinstance(audio, (OggOpus, OggVorbis, OggFLAC, OggSpeex)):
            return "ogg"
        return None

    def _family_from_extension(self, extension: str) -> str | None:
        if extension == ".mp3":
            return "mp3"
        if extension in self.MP4_EXTENSIONS:
            return "mp4"
        if extension == ".flac":
            return "flac"
        if extension == ".wav":
            return "wav"
        if extension in self.OGG_EXTENSIONS:
            return "ogg"
        if extension in self.MATROSKA_EXTENSIONS:
            return "matroska"
        return None

    def _canonical_extension(self, family: str, original_extension: str) -> str:
        if family == "mp3":
            return ".mp3"
        if family == "mp4":
            return original_extension if original_extension in self.MP4_EXTENSIONS else ".m4a"
        if family == "flac":
            return ".flac"
        if family == "wav":
            return ".wav"
        if family == "ogg":
            return original_extension if original_extension in self.OGG_EXTENSIONS else ".ogg"
        if family == "matroska":
            return ".mp3"
        return original_extension or ".bin"

    def _write_with_ffmpeg(
        self,
        *,
        audio_path: Path,
        image_path: Path,
        desired_name: str,
        desired_artist: str,
        output_path: Path,
        detected: DetectedAudioFormat,
        cover: CoverImage,
    ) -> None:
        if not self.ffmpeg_path:
            raise UnsupportedFormatError("ffmpeg topilmadi.")

        if detected.family == "matroska":
            command = [
                self.ffmpeg_path,
                "-y",
                "-v",
                "error",
                "-i",
                str(audio_path),
                "-i",
                str(image_path),
                "-map",
                "0:a:0",
                "-map",
                "1:v:0",
                "-map_metadata",
                "0",
                "-c:a",
                "libmp3lame",
                "-q:a",
                "2",
                "-c:v",
                "mjpeg",
                "-id3v2_version",
                "3",
                "-metadata",
                f"title={desired_name}",
                "-metadata",
                f"artist={desired_artist}",
                "-metadata:s:v",
                "title=Album cover",
                "-metadata:s:v",
                "comment=Cover (front)",
                str(output_path),
            ]
            try:
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except OSError as error:
                raise UnsupportedFormatError("ffmpeg ni ishga tushirib bo'lmadi.") from error
            except subprocess.CalledProcessError as error:
                stderr = (error.stderr or "").strip()
                details = stderr or "noma'lum xato"
                raise UnsupportedFormatError(
                    f"ffmpeg audio metadata yozishda xatolik berdi: {details}"
                ) from error
            return

        image_codec = "png" if cover.mime == "image/png" else "mjpeg"
        command = [
            self.ffmpeg_path,
            "-y",
            "-v",
            "error",
            "-i",
            str(audio_path),
            "-i",
            str(image_path),
            "-map",
            "0:a:0",
            "-map",
            "1:v:0",
            "-map_metadata",
            "0",
            "-c:a",
            "copy",
            "-metadata",
            f"title={desired_name}",
            "-metadata",
            f"artist={desired_artist}",
        ]

        if detected.family == "mp3":
            command.extend(
                [
                    "-c:v",
                    "mjpeg",
                    "-id3v2_version",
                    "3",
                    "-metadata:s:v",
                    "title=Album cover",
                    "-metadata:s:v",
                    "comment=Cover (front)",
                ]
            )
        elif detected.family in {"mp4", "flac"}:
            command.extend(
                [
                    "-c:v",
                    image_codec,
                    "-disposition:v:0",
                    "attached_pic",
                ]
            )
        else:
            raise UnsupportedFormatError("Bu konteyner uchun ffmpeg strategiyasi yo'q.")

        command.append(str(output_path))

        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
        except OSError as error:
            raise UnsupportedFormatError("ffmpeg ni ishga tushirib bo'lmadi.") from error
        except subprocess.CalledProcessError as error:
            stderr = (error.stderr or "").strip()
            details = stderr or "noma'lum xato"
            raise UnsupportedFormatError(
                f"ffmpeg audio metadata yozishda xatolik berdi: {details}"
            ) from error

    def _write_with_mutagen(
        self,
        *,
        audio_path: Path,
        cover: CoverImage,
        desired_name: str,
        desired_artist: str,
        family: str,
    ) -> None:
        try:
            if family == "mp3":
                self._write_mp3(audio_path, cover, desired_name, desired_artist)
            elif family == "mp4":
                self._write_mp4(audio_path, cover, desired_name, desired_artist)
            elif family == "flac":
                self._write_flac(audio_path, cover, desired_name, desired_artist)
            elif family == "wav":
                self._write_wave(audio_path, cover, desired_name, desired_artist)
            elif family == "ogg":
                self._write_ogg_family(audio_path, cover, desired_name, desired_artist)
            else:
                raise UnsupportedFormatError("Mazkur audio format hozircha qo'llab-quvvatlanmaydi.")
        except MutagenError as error:
            raise UnsupportedFormatError(
                "Audio faylni o'qib bo'lmadi yoki bu formatga cover yozib bo'lmadi."
            ) from error

    def _load_cover(self, image_path: Path) -> CoverImage:
        try:
            with Image.open(image_path) as image:
                working = image.copy()
                original_format = (image.format or "").upper()
        except (UnidentifiedImageError, OSError) as error:
            raise CoverImageError("Rasm fayli o'qib bo'lmadi.") from error

        if original_format not in {"JPEG", "JPG", "PNG"}:
            if "A" in working.getbands():
                original_format = "PNG"
            else:
                original_format = "JPEG"

        if original_format == "PNG":
            if working.mode not in {"RGB", "RGBA"}:
                working = working.convert("RGBA")
            mime = "image/png"
            mp4_format = MP4Cover.FORMAT_PNG
            depth = 32 if "A" in working.getbands() else 24
        else:
            if working.mode != "RGB":
                working = working.convert("RGB")
            mime = "image/jpeg"
            mp4_format = MP4Cover.FORMAT_JPEG
            depth = 24

        buffer = BytesIO()
        working.save(buffer, format="PNG" if mime == "image/png" else "JPEG")
        data = buffer.getvalue()
        width, height = working.size

        return CoverImage(
            data=data,
            mime=mime,
            mp4_format=mp4_format,
            width=width,
            height=height,
            depth=depth,
        )

    def _write_mp3(self, audio_path: Path, cover: CoverImage, desired_name: str, desired_artist: str) -> None:
        audio = MP3(audio_path, ID3=ID3)
        if audio.tags is None:
            audio.add_tags()

        audio.tags.delall("TIT2")
        audio.tags.delall("TPE1")
        audio.tags.delall("APIC")
        audio.tags.add(TIT2(encoding=3, text=desired_name))
        audio.tags.add(TPE1(encoding=3, text=desired_artist))
        audio.tags.add(
            APIC(
                encoding=3,
                mime=cover.mime,
                type=3,
                desc="Cover",
                data=cover.data,
            )
        )
        audio.save(v2_version=3)

    def _write_wave(self, audio_path: Path, cover: CoverImage, desired_name: str, desired_artist: str) -> None:
        audio = WAVE(audio_path)
        if audio.tags is None:
            audio.add_tags()

        audio.tags.delall("TIT2")
        audio.tags.delall("TPE1")
        audio.tags.delall("APIC")
        audio.tags.add(TIT2(encoding=3, text=desired_name))
        audio.tags.add(TPE1(encoding=3, text=desired_artist))
        audio.tags.add(
            APIC(
                encoding=3,
                mime=cover.mime,
                type=3,
                desc="Cover",
                data=cover.data,
            )
        )
        audio.save()

    def _write_mp4(self, audio_path: Path, cover: CoverImage, desired_name: str, desired_artist: str) -> None:
        audio = MP4(audio_path)
        audio["\xa9nam"] = [desired_name]
        audio["\xa9ART"] = [desired_artist]
        audio["covr"] = [MP4Cover(cover.data, imageformat=cover.mp4_format)]
        audio.save()

    def _write_flac(self, audio_path: Path, cover: CoverImage, desired_name: str, desired_artist: str) -> None:
        audio = FLAC(audio_path)
        picture = Picture()
        picture.type = 3
        picture.desc = "Cover"
        picture.mime = cover.mime
        picture.width = cover.width
        picture.height = cover.height
        picture.depth = cover.depth
        picture.data = cover.data

        audio["title"] = [desired_name]
        audio["artist"] = [desired_artist]
        audio.clear_pictures()
        audio.add_picture(picture)
        audio.save()

    def _write_ogg_vorbis(self, audio_path: Path, cover: CoverImage, desired_name: str, desired_artist: str) -> None:
        audio = OggVorbis(audio_path)
        audio["title"] = [desired_name]
        audio["artist"] = [desired_artist]
        audio["metadata_block_picture"] = [cover.as_flac_picture_base64()]
        audio.save()

    def _write_ogg_flac(self, audio_path: Path, cover: CoverImage, desired_name: str, desired_artist: str) -> None:
        audio = OggFLAC(audio_path)
        audio["title"] = [desired_name]
        audio["artist"] = [desired_artist]
        audio["metadata_block_picture"] = [cover.as_flac_picture_base64()]
        audio.save()

    def _write_ogg_speex(self, audio_path: Path, cover: CoverImage, desired_name: str, desired_artist: str) -> None:
        audio = OggSpeex(audio_path)
        audio["title"] = [desired_name]
        audio["artist"] = [desired_artist]
        audio["metadata_block_picture"] = [cover.as_flac_picture_base64()]
        audio.save()

    def _write_ogg_opus(self, audio_path: Path, cover: CoverImage, desired_name: str, desired_artist: str) -> None:
        audio = OggOpus(audio_path)
        audio["title"] = [desired_name]
        audio["artist"] = [desired_artist]
        audio["metadata_block_picture"] = [cover.as_flac_picture_base64()]
        audio.save()

    def _write_ogg_family(self, audio_path: Path, cover: CoverImage, desired_name: str, desired_artist: str) -> None:
        writers = (
            self._write_ogg_opus,
            self._write_ogg_vorbis,
            self._write_ogg_flac,
            self._write_ogg_speex,
        )

        for writer in writers:
            try:
                writer(audio_path, cover, desired_name, desired_artist)
                return
            except MutagenError:
                continue

        if self.ffmpeg_path:
            self._rewrite_ogg_as_opus(audio_path, desired_name, desired_artist)
            self._write_ogg_opus(audio_path, cover, desired_name, desired_artist)
            return

        raise UnsupportedFormatError("OGG faylining ichki kodeki qo'llab-quvvatlanmaydi.")

    def _rewrite_ogg_as_opus(self, audio_path: Path, desired_name: str, desired_artist: str) -> None:
        if not self.ffmpeg_path:
            raise UnsupportedFormatError("ffmpeg topilmadi.")

        temp_output = audio_path.with_name(f"{audio_path.stem}_opus{audio_path.suffix}")
        command = [
            self.ffmpeg_path,
            "-y",
            "-v",
            "error",
            "-i",
            str(audio_path),
            "-map_metadata",
            "0",
            "-c:a",
            "libopus",
            "-metadata",
            f"title={desired_name}",
            "-metadata",
            f"artist={desired_artist}",
            str(temp_output),
        ]
        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
            temp_output.replace(audio_path)
        except OSError as error:
            temp_output.unlink(missing_ok=True)
            raise UnsupportedFormatError("ffmpeg ni ishga tushirib bo'lmadi.") from error
        except subprocess.CalledProcessError as error:
            temp_output.unlink(missing_ok=True)
            stderr = (error.stderr or "").strip()
            details = stderr or "noma'lum xato"
            raise UnsupportedFormatError(
                f"OGG faylni Opus formatiga o'tkazib bo'lmadi: {details}"
            ) from error

    @staticmethod
    def build_visible_artist(desired_artist: str) -> str:
        clean_artist = re.sub(r'[<>:"/\\\\|?*\x00-\x1F]+', "_", desired_artist).strip()
        if not clean_artist:
            clean_artist = "Unknown artist"
        return clean_artist[:80]

    def _map_processing_error(self, error: Exception, format_name: str) -> UnsupportedFormatError:
        if isinstance(error, UnsupportedFormatError):
            return error

        if isinstance(error, CoverImageError):
            return UnsupportedFormatError(str(error))

        return UnsupportedFormatError(
            f"Audio faylni qayta ishlashda ichki xatolik yuz berdi. Aniqlangan format: {format_name}."
        )

    @staticmethod
    def build_download_name(desired_name: str, extension: str) -> str:
        clean_name = AudioMetadataService.build_visible_name(desired_name)
        return f"{clean_name}{extension}"

    @staticmethod
    def build_visible_name(desired_name: str) -> str:
        clean_name = re.sub(r'[<>:"/\\\\|?*\x00-\x1F]+', "_", desired_name).strip().strip(".")
        suffix = Path(clean_name).suffix.lower()
        if suffix in AudioMetadataService.KNOWN_AUDIO_SUFFIXES:
            clean_name = clean_name[: -len(suffix)].rstrip().strip(".")
        if not clean_name:
            clean_name = "audio"
        clean_name = clean_name[:80]
        return clean_name
