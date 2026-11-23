import asyncio
from pathlib import Path

import aiosqlite


class DatabaseManager:
    """Owns the application's SQLite connection and schema."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._connection_path = (
            f"file:chatbot_{id(self)}?mode=memory&cache=shared"
            if db_path == ":memory:"
            else db_path
        )
        self.write_conn: aiosqlite.Connection | None = None
        self.read_conn: aiosqlite.Connection | None = None
        self.write_lock = asyncio.Lock()

    async def connect(self) -> None:
        if self.write_conn is not None and self.read_conn is not None:
            return

        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        uri = self.db_path == ":memory:"
        self.write_conn = await aiosqlite.connect(self._connection_path, uri=uri)
        self.write_conn.row_factory = aiosqlite.Row
        await self._configure(self.write_conn, writer=True)
        await self._create_tables()
        await self.write_conn.commit()

        self.read_conn = await aiosqlite.connect(self._connection_path, uri=uri)
        self.read_conn.row_factory = aiosqlite.Row
        await self._configure(self.read_conn)

    async def _configure(
        self, conn: aiosqlite.Connection, writer: bool = False
    ) -> None:
        # WAL даёт читателям снимок данных, пока единственный writer выполняет короткую запись.
        if writer:
            await conn.execute("PRAGMA journal_mode = WAL")
        await conn.execute("PRAGMA synchronous = NORMAL")
        await conn.execute("PRAGMA busy_timeout = 5000")
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("PRAGMA temp_store = MEMORY")

    async def _create_tables(self) -> None:
        conn = self.write_connection
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                is_blocked INTEGER NOT NULL DEFAULT 0,
                gender TEXT,
                age INTEGER,
                preferred_gender TEXT,
                min_partner_age INTEGER NOT NULL DEFAULT 13,
                max_partner_age INTEGER NOT NULL DEFAULT 120,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS queue (
                user_id INTEGER PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                enqueued_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS pairs (
                user_a INTEGER PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                user_b INTEGER NOT NULL UNIQUE REFERENCES users(user_id) ON DELETE CASCADE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (user_a != user_b)
            );
            CREATE TABLE IF NOT EXISTS blocks (
                user_id INTEGER PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                reason TEXT NOT NULL DEFAULT '',
                blocked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_queue_enqueued_at ON queue(enqueued_at);
            CREATE INDEX IF NOT EXISTS idx_users_blocked ON users(is_blocked);
            """
        )
        user_columns = {
            row[1]
            for row in await (await conn.execute("PRAGMA table_info(users)")).fetchall()
        }
        if "gender" not in user_columns:
            await conn.execute("ALTER TABLE users ADD COLUMN gender TEXT")
        if "age" not in user_columns:
            await conn.execute("ALTER TABLE users ADD COLUMN age INTEGER")
        if "preferred_gender" not in user_columns:
            await conn.execute("ALTER TABLE users ADD COLUMN preferred_gender TEXT")
        if "min_partner_age" not in user_columns:
            await conn.execute(
                "ALTER TABLE users ADD COLUMN min_partner_age INTEGER NOT NULL DEFAULT 13"
            )
        if "max_partner_age" not in user_columns:
            await conn.execute(
                "ALTER TABLE users ADD COLUMN max_partner_age INTEGER NOT NULL DEFAULT 120"
            )
        await conn.execute("INSERT OR IGNORE INTO users (user_id) SELECT user_id FROM queue")
        await conn.execute("INSERT OR IGNORE INTO users (user_id) SELECT user_a FROM pairs")
        await conn.execute("INSERT OR IGNORE INTO users (user_id) SELECT user_b FROM pairs")
        await conn.execute("INSERT OR IGNORE INTO users (user_id) SELECT user_id FROM blocks")
        await conn.execute(
            "UPDATE users SET is_blocked = 1 WHERE user_id IN (SELECT user_id FROM blocks)"
        )

    @property
    def connection(self) -> aiosqlite.Connection:
        return self.write_connection

    @property
    def write_connection(self) -> aiosqlite.Connection:
        if self.write_conn is None:
            raise RuntimeError("Database is not connected; call connect() first")
        return self.write_conn

    @property
    def read_connection(self) -> aiosqlite.Connection:
        if self.read_conn is None:
            raise RuntimeError("Database is not connected; call connect() first")
        return self.read_conn

    async def close_read(self) -> None:
        if self.read_conn is not None:
            await self.read_conn.close()
            self.read_conn = None

    async def close_write(self) -> None:
        if self.write_conn is not None:
            await self.write_conn.close()
            self.write_conn = None

    async def close(self) -> None:
        try:
            await self.close_read()
        finally:
            await self.close_write()
