from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SessionStep = Literal["awaiting_image", "awaiting_name", "awaiting_artist", "awaiting_audio", "processing"]


@dataclass
class UserSession:
    user_id: int
    step: SessionStep
    image_path: str | None
    audio_path: str | None
    desired_name: str | None
    desired_artist: str | None


@dataclass
class UsageUserSummary:
    user_id: int
    username: str | None
    full_name: str | None
    rename_count: int
    last_source_name: str | None
    last_target_name: str | None
    last_target_artist: str | None
