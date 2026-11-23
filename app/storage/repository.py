import asyncio
import random
from functools import wraps
from typing import Awaitable, Callable, ParamSpec, TypeVar

import aiosqlite

from app.storage.db import DatabaseManager

P = ParamSpec("P")
R = TypeVar("R")


def _with_retry(
    method: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    """При исчерпании повторов пробрасывает OperationalError — handlers обязаны её обработать и показать пользователю нейтральный текст."""

    @wraps(method)
    async def wrapped(self: "Repository", *args: P.args, **kwargs: P.kwargs) -> R:
        delays = (0.05, 0.15, 0.45)
        for attempt in range(len(delays) + 1):
            try:
                # Единственный writer сериализуется, чтобы параллельные handlers не спорили за write-lock SQLite.
                async with self._write_lock:
                    try:
                        return await method(self, *args, **kwargs)
                    except aiosqlite.OperationalError:
                        if self.database.write_connection.in_transaction:
                            await self.database.write_connection.rollback()
                        raise
            except aiosqlite.OperationalError as exc:
                if "database is locked" not in str(exc).lower() or attempt == len(delays):
                    raise
                delay = delays[attempt] + random.uniform(0, delays[attempt] * 0.1)
                await asyncio.sleep(delay)
        raise RuntimeError("Unreachable retry state")

    return wrapped


class Repository:
    """Persistence operations for users, the waiting queue, and chat pairs."""

    def __init__(self, database: DatabaseManager):
        self.database = database
        self._write_lock = database.write_lock

    @property
    def conn(self) -> aiosqlite.Connection:
        return self.database.write_connection

    @_with_retry
    async def get_or_create_user(self, user_id: int) -> int:
        conn = self.database.write_connection
        await conn.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)
        )
        await conn.commit()
        return user_id

    async def get_profile(self, user_id: int) -> dict[str, int | str] | None:
        cursor = await self.database.read_connection.execute(
            "SELECT gender, age FROM users WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {"gender": row["gender"], "age": row["age"]}

    @_with_retry
    async def save_profile(self, user_id: int, gender: str, age: int) -> None:
        if gender not in {"male", "female"}:
            raise ValueError("gender must be 'male' or 'female'")
        if not 13 <= age <= 120:
            raise ValueError("age must be between 13 and 120")
        conn = self.database.write_connection
        await conn.execute(
            """INSERT INTO users (user_id, gender, age) VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET gender = excluded.gender, age = excluded.age""",
            (user_id, gender, age),
        )
        await conn.commit()

    async def get_search_preferences(self, user_id: int) -> dict[str, int | str | None] | None:
        cursor = await self.database.read_connection.execute(
            """SELECT preferred_gender, min_partner_age, max_partner_age
               FROM users WHERE user_id = ?""",
            (user_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "gender": row["preferred_gender"],
            "min_age": row["min_partner_age"],
            "max_age": row["max_partner_age"],
        }

    @_with_retry
    async def save_search_preferences(
        self, user_id: int, preferred_gender: str | None, min_age: int, max_age: int
    ) -> None:
        if preferred_gender not in {None, "male", "female"}:
            raise ValueError("preferred_gender must be None, 'male', or 'female'")
        if not 13 <= min_age <= max_age <= 120:
            raise ValueError("age range must be between 13 and 120, with min_age <= max_age")
        conn = self.database.write_connection
        await conn.execute("BEGIN IMMEDIATE")
        try:
            await conn.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)
            )
            await conn.execute(
                """UPDATE users
                   SET preferred_gender = ?, min_partner_age = ?, max_partner_age = ?
                   WHERE user_id = ?""",
                (preferred_gender, min_age, max_age, user_id),
            )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise

    async def is_blocked(self, user_id: int) -> bool:
        cursor = await self.database.read_connection.execute(
            "SELECT is_blocked FROM users WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return bool(row[0]) if row else False

    @_with_retry
    async def block_user(self, user_id: int, reason: str = "") -> None:
        conn = self.database.write_connection
        await conn.execute("BEGIN IMMEDIATE")
        try:
            await conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
            await conn.execute("UPDATE users SET is_blocked = 1 WHERE user_id = ?", (user_id,))
            await conn.execute(
                "INSERT OR REPLACE INTO blocks (user_id, reason, blocked_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (user_id, reason),
            )
            await conn.execute("DELETE FROM queue WHERE user_id = ?", (user_id,))
            await conn.execute(
                "DELETE FROM pairs WHERE user_a = ? OR user_b = ?", (user_id, user_id)
            )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise

    @_with_retry
    async def unblock_user(self, user_id: int) -> None:
        conn = self.database.write_connection
        await conn.execute("BEGIN IMMEDIATE")
        try:
            await conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
            await conn.execute("UPDATE users SET is_blocked = 0 WHERE user_id = ?", (user_id,))
            await conn.execute("DELETE FROM blocks WHERE user_id = ?", (user_id,))
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise

    async def check_user_in_queue(self, user_id: int) -> bool:
        cursor = await self.database.read_connection.execute(
            "SELECT 1 FROM queue WHERE user_id = ?", (user_id,)
        )
        return await cursor.fetchone() is not None

    async def check_active_dialog(self, user_id: int) -> bool:
        cursor = await self.database.read_connection.execute(
            "SELECT 1 FROM pairs WHERE user_a = ?", (user_id,)
        )
        return await cursor.fetchone() is not None

    @_with_retry
    async def enqueue_user(self, user_id: int, enqueued_at: str | None = None) -> None:
        conn = self.database.write_connection
        await conn.execute("BEGIN IMMEDIATE")
        try:
            await conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
            await conn.execute(
                "INSERT OR IGNORE INTO queue (user_id, enqueued_at) VALUES (?, COALESCE(?, CURRENT_TIMESTAMP))",
                (user_id, enqueued_at),
            )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise

    @_with_retry
    async def dequeue_user(self, user_id: int) -> bool:
        conn = self.database.write_connection
        cursor = await conn.execute(
            "DELETE FROM queue WHERE user_id = ?", (user_id,)
        )
        await conn.commit()
        return cursor.rowcount > 0

    @_with_retry
    async def try_match_or_enqueue(self, user_id: int) -> tuple[int | None, bool]:
        conn = self.database.write_connection
        await conn.execute("BEGIN IMMEDIATE")
        try:
            await conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
            status = await conn.execute(
                """SELECT is_blocked, gender, age, preferred_gender,
                          min_partner_age, max_partner_age
                   FROM users WHERE user_id = ?""",
                (user_id,),
            )
            user_row = await status.fetchone()
            if bool(user_row["is_blocked"]) or user_row["gender"] is None or user_row["age"] is None:
                await conn.rollback()
                return None, False

            active = await conn.execute("SELECT 1 FROM pairs WHERE user_a = ?", (user_id,))
            if await active.fetchone():
                await conn.rollback()
                return None, False

            waiting = await conn.execute(
                "SELECT 1 FROM queue WHERE user_id = ?", (user_id,)
            )
            if await waiting.fetchone():
                await conn.rollback()
                return None, True

            candidate = await conn.execute(
                """SELECT q.user_id FROM queue AS q
                   JOIN users AS u ON u.user_id = q.user_id
                   WHERE q.user_id != ? AND u.is_blocked = 0
                     AND u.gender IS NOT NULL AND u.age IS NOT NULL
                     AND (? IS NULL OR u.gender = ?)
                     AND u.age BETWEEN ? AND ?
                     AND (u.preferred_gender IS NULL OR u.preferred_gender = ?)
                     AND ? BETWEEN u.min_partner_age AND u.max_partner_age
                     AND NOT EXISTS (SELECT 1 FROM pairs p WHERE p.user_a = q.user_id)
                   ORDER BY q.enqueued_at, q.user_id LIMIT 1""",
                (
                    user_id,
                    user_row["preferred_gender"],
                    user_row["preferred_gender"],
                    user_row["min_partner_age"],
                    user_row["max_partner_age"],
                    user_row["gender"],
                    user_row["age"],
                ),
            )
            row = await candidate.fetchone()
            if row is None:
                await conn.execute(
                    "INSERT OR IGNORE INTO queue (user_id) VALUES (?)", (user_id,)
                )
                await conn.commit()
                return None, False

            partner_id = int(row[0])
            await conn.execute("DELETE FROM queue WHERE user_id IN (?, ?)", (user_id, partner_id))
            await conn.execute(
                "INSERT INTO pairs (user_a, user_b) VALUES (?, ?)", (user_id, partner_id)
            )
            await conn.execute(
                "INSERT INTO pairs (user_a, user_b) VALUES (?, ?)", (partner_id, user_id)
            )
            await conn.commit()
            return partner_id, False
        except Exception:
            await conn.rollback()
            raise

    async def get_partner_id(self, user_id: int) -> int | None:
        cursor = await self.database.read_connection.execute(
            "SELECT user_b FROM pairs WHERE user_a = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else None

    @_with_retry
    async def delete_pair(self, user_id: int, partner_id: int | None = None) -> None:
        conn = self.database.write_connection
        # Один DELETE — implicit transaction в SQLite достаточен; BEGIN IMMEDIATE не нужен.
        if partner_id is None:
            await conn.execute(
                "DELETE FROM pairs WHERE user_a = ? OR user_b = ?", (user_id, user_id)
            )
        else:
            await conn.execute(
                """DELETE FROM pairs
                   WHERE (user_a = ? AND user_b = ?) OR (user_a = ? AND user_b = ?)""",
                (user_id, partner_id, partner_id, user_id),
            )
        await conn.commit()

    async def get_system_stats(self) -> dict[str, int]:
        queries = {
            "total_users": "SELECT COUNT(*) FROM users",
            "queue_size": "SELECT COUNT(*) FROM queue",
            "active_pairs": "SELECT COUNT(*) FROM pairs WHERE user_a < user_b",
            "blocked_users": "SELECT COUNT(*) FROM users WHERE is_blocked = 1",
        }
        stats: dict[str, int] = {}
        conn = self.database.read_connection
        for key, query in queries.items():
            cursor = await conn.execute(query)
            stats[key] = int((await cursor.fetchone())[0])
        return stats
