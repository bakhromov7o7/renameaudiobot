from __future__ import annotations

from typing import Any

import asyncpg

from bot.models import UsageUserSummary, UserSession


class SessionRepository:
    STATS_ROW_ID = 1

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
        await pool.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                rename_count BIGINT NOT NULL DEFAULT 0 CHECK (rename_count >= 0),
                last_source_name TEXT,
                last_target_name TEXT,
                last_target_artist TEXT,
                first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        await pool.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_stats (
                stats_id SMALLINT PRIMARY KEY,
                total_users BIGINT NOT NULL DEFAULT 0 CHECK (total_users >= 0),
                total_renames BIGINT NOT NULL DEFAULT 0 CHECK (total_renames >= 0)
            );
            """
        )
        await pool.execute("ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS desired_artist TEXT")
        await pool.execute("ALTER TABLE usage_users ADD COLUMN IF NOT EXISTS username TEXT")
        await pool.execute("ALTER TABLE usage_users ADD COLUMN IF NOT EXISTS full_name TEXT")
        await pool.execute(
            "ALTER TABLE usage_users ADD COLUMN IF NOT EXISTS rename_count BIGINT NOT NULL DEFAULT 0"
        )
        await pool.execute("ALTER TABLE usage_users ADD COLUMN IF NOT EXISTS last_source_name TEXT")
        await pool.execute("ALTER TABLE usage_users ADD COLUMN IF NOT EXISTS last_target_name TEXT")
        await pool.execute("ALTER TABLE usage_users ADD COLUMN IF NOT EXISTS last_target_artist TEXT")
        await pool.execute("ALTER TABLE usage_users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
        await pool.execute("ALTER TABLE user_sessions DROP CONSTRAINT IF EXISTS user_sessions_step_check")
        await pool.execute(
            """
            ALTER TABLE user_sessions
            ADD CONSTRAINT user_sessions_step_check
            CHECK (step IN ('awaiting_image', 'awaiting_name', 'awaiting_artist', 'awaiting_audio', 'processing'))
            """
        )
        await pool.execute(
            """
            INSERT INTO usage_stats (stats_id, total_users, total_renames)
            VALUES ($1, 0, 0)
            ON CONFLICT (stats_id) DO NOTHING
            """,
            self.STATS_ROW_ID,
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

    async def reset_to_awaiting_audio(self, user_id: int) -> UserSession | None:
        pool = self._ensure_pool()
        record = await pool.fetchrow(
            """
            UPDATE user_sessions
            SET step = 'awaiting_audio',
                audio_path = NULL,
                updated_at = NOW()
            WHERE user_id = $1
            RETURNING user_id, step, image_path, audio_path, desired_name, desired_artist
            """,
            user_id,
        )
        if record is None:
            return None
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

    async def track_user(self, user_id: int, username: str | None = None, full_name: str | None = None) -> None:
        pool = self._ensure_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                inserted = await connection.fetchval(
                    """
                    INSERT INTO usage_users (user_id, username, full_name)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id) DO NOTHING
                    RETURNING user_id
                    """,
                    user_id,
                    username,
                    full_name,
                )
                if inserted is not None:
                    await connection.execute(
                        """
                        UPDATE usage_stats
                        SET total_users = total_users + 1
                        WHERE stats_id = $1
                        """,
                        self.STATS_ROW_ID,
                    )
                else:
                    await connection.execute(
                        """
                        UPDATE usage_users
                        SET username = COALESCE($2, username),
                            full_name = COALESCE($3, full_name),
                            last_seen_at = NOW()
                        WHERE user_id = $1
                        """,
                        user_id,
                        username,
                        full_name,
                    )

    async def record_audio_rename(
        self,
        *,
        user_id: int,
        desired_name: str,
        desired_artist: str,
        source_name: str | None,
    ) -> None:
        pool = self._ensure_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    UPDATE usage_stats
                    SET total_renames = total_renames + 1
                    WHERE stats_id = $1
                    """,
                    self.STATS_ROW_ID,
                )
                await connection.execute(
                    """
                    UPDATE usage_users
                    SET rename_count = rename_count + 1,
                        last_source_name = $2,
                        last_target_name = $3,
                        last_target_artist = $4,
                        last_seen_at = NOW()
                    WHERE user_id = $1
                    """,
                    user_id,
                    source_name,
                    desired_name,
                    desired_artist,
                )

    async def get_usage_stats(self) -> tuple[int, int]:
        pool = self._ensure_pool()
        record = await pool.fetchrow(
            """
            SELECT total_users, total_renames
            FROM usage_stats
            WHERE stats_id = $1
            """,
            self.STATS_ROW_ID,
        )
        if record is None:
            return 0, 0
        return int(record["total_users"]), int(record["total_renames"])

    async def list_usage_users(self) -> list[UsageUserSummary]:
        pool = self._ensure_pool()
        records = await pool.fetch(
            """
            SELECT user_id, username, full_name, rename_count, last_source_name, last_target_name, last_target_artist
            FROM usage_users
            ORDER BY last_seen_at DESC, user_id DESC
            """
        )
        return [
            UsageUserSummary(
                user_id=int(record["user_id"]),
                username=record["username"],
                full_name=record["full_name"],
                rename_count=int(record["rename_count"]),
                last_source_name=record["last_source_name"],
                last_target_name=record["last_target_name"],
                last_target_artist=record["last_target_artist"],
            )
            for record in records
        ]

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
