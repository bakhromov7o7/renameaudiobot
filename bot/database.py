from __future__ import annotations

from typing import Any

import asyncpg

from bot.models import UserSession


class SessionRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(self._database_url, min_size=1, max_size=5)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()

    async def init_schema(self) -> None:
        pool = self._ensure_pool()
        await pool.execute(
            """
            CREATE TABLE IF NOT EXISTS user_sessions (
                user_id BIGINT PRIMARY KEY,
                step TEXT NOT NULL CHECK (step IN ('awaiting_image', 'awaiting_name', 'awaiting_artist', 'awaiting_audio', 'processing')),
                image_path TEXT,
                audio_path TEXT,
                desired_name TEXT,
                desired_artist TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        await pool.execute("ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS desired_artist TEXT")
        await pool.execute("ALTER TABLE user_sessions DROP CONSTRAINT IF EXISTS user_sessions_step_check")
        await pool.execute(
            """
            ALTER TABLE user_sessions
            ADD CONSTRAINT user_sessions_step_check
            CHECK (step IN ('awaiting_image', 'awaiting_name', 'awaiting_artist', 'awaiting_audio', 'processing'))
            """
        )

    async def get_session(self, user_id: int) -> UserSession | None:
        pool = self._ensure_pool()
        record = await pool.fetchrow(
            """
            SELECT user_id, step, image_path, audio_path, desired_name, desired_artist
            FROM user_sessions
            WHERE user_id = $1
            """,
            user_id,
        )
        if record is None:
            return None
        return self._row_to_session(record)

    async def get_or_create_session(self, user_id: int) -> UserSession:
        pool = self._ensure_pool()
        await pool.execute(
            """
            INSERT INTO user_sessions (user_id, step)
            VALUES ($1, 'awaiting_image')
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id,
        )
        session = await self.get_session(user_id)
        if session is None:
            raise RuntimeError("Foydalanuvchi sessiyasi yaratilmadi.")
        return session

    async def save_image(self, user_id: int, image_path: str) -> UserSession:
        pool = self._ensure_pool()
        record = await pool.fetchrow(
            """
            INSERT INTO user_sessions (user_id, step, image_path, audio_path, desired_name, desired_artist)
            VALUES ($1, 'awaiting_name', $2, NULL, NULL, NULL)
            ON CONFLICT (user_id) DO UPDATE
            SET step = 'awaiting_name',
                image_path = EXCLUDED.image_path,
                audio_path = NULL,
                desired_name = NULL,
                desired_artist = NULL,
                updated_at = NOW()
            RETURNING user_id, step, image_path, audio_path, desired_name, desired_artist
            """,
            user_id,
            image_path,
        )
        return self._row_to_session(record)

    async def save_name(self, user_id: int, desired_name: str) -> UserSession:
        pool = self._ensure_pool()
        record = await pool.fetchrow(
            """
            UPDATE user_sessions
            SET step = 'awaiting_artist',
                desired_name = $2,
                desired_artist = NULL,
                updated_at = NOW()
            WHERE user_id = $1
            RETURNING user_id, step, image_path, audio_path, desired_name, desired_artist
            """,
            user_id,
            desired_name,
        )
        if record is None:
            raise RuntimeError("Nomni saqlashdan oldin sessiya mavjud emas.")
        return self._row_to_session(record)

    async def save_artist(self, user_id: int, desired_artist: str) -> UserSession:
        pool = self._ensure_pool()
        record = await pool.fetchrow(
            """
            UPDATE user_sessions
            SET step = 'awaiting_audio',
                desired_artist = $2,
                updated_at = NOW()
            WHERE user_id = $1
            RETURNING user_id, step, image_path, audio_path, desired_name, desired_artist
            """,
            user_id,
            desired_artist,
        )
        if record is None:
            raise RuntimeError("Ijrochi nomini saqlashdan oldin sessiya mavjud emas.")
        return self._row_to_session(record)

    async def save_audio(self, user_id: int, audio_path: str) -> UserSession:
        pool = self._ensure_pool()
        record = await pool.fetchrow(
            """
            UPDATE user_sessions
            SET step = 'processing',
                audio_path = $2,
                updated_at = NOW()
            WHERE user_id = $1
            RETURNING user_id, step, image_path, audio_path, desired_name, desired_artist
            """,
            user_id,
            audio_path,
        )
        if record is None:
            raise RuntimeError("Audio saqlashdan oldin sessiya mavjud emas.")
        return self._row_to_session(record)

    async def delete_session(self, user_id: int) -> None:
        pool = self._ensure_pool()
        await pool.execute(
            """
            DELETE FROM user_sessions
            WHERE user_id = $1
            """,
            user_id,
        )

    def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database pool hali yaratilmagan.")
        return self._pool

    @staticmethod
    def _row_to_session(record: asyncpg.Record | dict[str, Any]) -> UserSession:
        return UserSession(
            user_id=record["user_id"],
            step=record["step"],
            image_path=record["image_path"],
            audio_path=record["audio_path"],
            desired_name=record["desired_name"],
            desired_artist=record["desired_artist"],
        )
