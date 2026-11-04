"""PostgreSQL (Neon) вместо SQLite: тот же интерфейс, что у db.DB.

Весь остальной код пишет SQL с `?`-плейсхолдерами в стиле SQLite; здесь они
превращаются в `$1, $2...`, а схема переводится под PostgreSQL (INTEGER -> BIGINT,
AUTOINCREMENT -> BIGSERIAL), потому что id чатов в Telegram не влезают в 32 бита.
"""
import asyncio
import logging
import os
import re

import asyncpg

import selfedit
from db import DB

log = logging.getLogger("pgdb")

# Ошибки соединения: Neon усыпляет базу после простоя, а pooler рвёт старые соединения
_RETRY = (asyncpg.exceptions.PostgresConnectionError, asyncpg.exceptions.CannotConnectNowError,
          asyncpg.exceptions.InterfaceError, asyncio.TimeoutError, ConnectionError, OSError)


def to_pg(sql: str) -> str:
    """`?` -> `$1, $2, ...` (в наших запросах `?` в строковых литералах не встречается)."""
    n = 0

    def rep(_):
        nonlocal n
        n += 1
        return f"${n}"

    return re.sub(r"\?", rep, sql)


def schema_to_pg(schema: str) -> list[str]:
    s = schema.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    s = re.sub(r"\bINTEGER\b", "BIGINT", s)
    return [part.strip() for part in s.split(";") if part.strip()]


def _args(args) -> list:
    # asyncpg строго проверяет типы: float в BIGINT-колонку не пройдёт, в SQLite проходило
    return [int(a) if isinstance(a, float) else a for a in args]


def _count(status: str) -> int:
    tail = (status or "").split()[-1:]
    return int(tail[0]) if tail and tail[0].isdigit() else 0


class PgCursor:
    def __init__(self, rows=None, rowcount: int = -1):
        self._rows = rows or []
        self.rowcount = rowcount
        self.lastrowid = None

    async def fetchall(self):
        return list(self._rows)

    async def close(self):
        pass


class PgConn:
    """Минимальная замена aiosqlite.Connection: execute / executescript / commit."""

    def __init__(self):
        self.pool: asyncpg.Pool | None = None

    async def connect(self, url: str):
        dsn = selfedit.clean_dsn(url)
        self.pool = await asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=int(os.getenv("PG_POOL_MAX", "5")),
            statement_cache_size=0,               # нужно для pooler-адреса Neon (PgBouncer)
            max_inactive_connection_lifetime=120,
            command_timeout=30,
            timeout=60,                           # первое подключение может будить базу
        )

    async def _retry(self, make):
        for attempt in range(3):
            try:
                return await make()
            except _RETRY as e:
                if attempt == 2:
                    raise
                log.warning("Соединение с БД прервано (%s), повтор…", e.__class__.__name__)
                await asyncio.sleep(1 + attempt * 2)

    async def execute(self, sql: str, args=()):
        q, a = to_pg(sql), _args(args)
        up = q.lstrip().upper()
        if up.startswith(("SELECT", "WITH")) or " RETURNING " in up:
            rows = await self._retry(lambda: self.pool.fetch(q, *a))
            return PgCursor(rows, len(rows))
        status = await self._retry(lambda: self.pool.execute(q, *a))
        return PgCursor([], _count(status))

    async def executescript(self, script: str):
        for stmt in schema_to_pg(script):
            await self._retry(lambda s=stmt: self.pool.execute(s))

    async def commit(self):
        """Каждый запрос выполняется в своей транзакции (autocommit), коммитить нечего."""

    async def close(self):
        if self.pool:
            await self.pool.close()


class PgDB(DB):
    """Все методы (settings, touch, warns...) наследуются от DB и работают через PgConn."""

    def __init__(self, url: str):
        super().__init__("postgres")
        self.url = url

    async def init(self):
        from db import SCHEMA
        self.conn = PgConn()
        await self.conn.connect(self.url)
        await self.conn.executescript(SCHEMA)
