import asyncio
import json
import os
import time

import aiosqlite

from config import DEFAULTS

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats(
    chat_id INTEGER PRIMARY KEY,
    title TEXT,
    settings TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS users(
    chat_id INTEGER,
    user_id INTEGER,
    username TEXT,
    name TEXT,
    nick TEXT,
    msgs INTEGER DEFAULT 0,
    warns INTEGER DEFAULT 0,
    rep INTEGER DEFAULT 0,
    rank INTEGER DEFAULT 0,
    first_seen INTEGER,
    last_seen INTEGER,
    PRIMARY KEY(chat_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_users_lname ON users(chat_id, LOWER(username));
CREATE TABLE IF NOT EXISTS warnlog(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER, user_id INTEGER, by_id INTEGER, reason TEXT, ts INTEGER
);
CREATE TABLE IF NOT EXISTS notes(
    chat_id INTEGER, name TEXT, text TEXT,
    PRIMARY KEY(chat_id, name)
);
CREATE TABLE IF NOT EXISTS words(
    chat_id INTEGER, word TEXT,
    PRIMARY KEY(chat_id, word)
);
CREATE TABLE IF NOT EXISTS marriages(
    chat_id INTEGER, u1 INTEGER, u2 INTEGER, since INTEGER,
    PRIMARY KEY(chat_id, u1)
);
CREATE TABLE IF NOT EXISTS customrp(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER, cmd TEXT, text TEXT, adult INTEGER DEFAULT 0,
    UNIQUE(chat_id, cmd)
);
"""


class DB:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None
        self._settings: dict[int, dict] = {}
        self._words: dict[int, set] = {}
        self._crp: dict[int, dict] = {}

    # ---------- служебное ----------
    async def init(self):
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def flusher(self):
        while True:
            await asyncio.sleep(10)
            try:
                await self.conn.commit()
            except Exception:
                pass

    async def q(self, sql, args=(), one=False):
        cur = await self.conn.execute(sql, args)
        rows = await cur.fetchall()
        await cur.close()
        if one:
            return rows[0] if rows else None
        return rows

    async def x(self, sql, args=()):
        cur = await self.conn.execute(sql, args)
        n = cur.rowcount
        await cur.close()
        await self.conn.commit()
        return n

    # ---------- настройки чата ----------
    async def settings(self, chat_id: int) -> dict:
        if chat_id in self._settings:
            return self._settings[chat_id]
        row = await self.q("SELECT settings FROM chats WHERE chat_id=?", (chat_id,), one=True)
        data = {}
        if row:
            try:
                data = json.loads(row["settings"] or "{}")
            except ValueError:
                data = {}
        s = {**DEFAULTS, **data}
        self._settings[chat_id] = s
        return s

    async def set(self, chat_id: int, key: str, value):
        s = await self.settings(chat_id)
        s[key] = value
        await self.x(
            "INSERT INTO chats(chat_id, settings) VALUES(?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET settings=excluded.settings",
            (chat_id, json.dumps(s, ensure_ascii=False)),
        )

    async def set_title(self, chat_id: int, title: str):
        await self.x(
            "INSERT INTO chats(chat_id, title) VALUES(?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title",
            (chat_id, title),
        )

    async def night_chats(self) -> list[int]:
        out = []
        for row in await self.q("SELECT chat_id FROM chats"):
            s = await self.settings(row["chat_id"])
            if s.get("night") or s.get("night_state"):
                out.append(row["chat_id"])
        return out

    # ---------- пользователи ----------
    async def touch(self, chat_id: int, user):
        now = int(time.time())
        await self.conn.execute(
            "INSERT INTO users(chat_id,user_id,username,name,msgs,first_seen,last_seen) "
            "VALUES(?,?,?,?,1,?,?) "
            "ON CONFLICT(chat_id,user_id) DO UPDATE SET username=excluded.username, "
            "name=excluded.name, msgs=users.msgs+1, last_seen=excluded.last_seen",
            (chat_id, user.id, user.username, user.full_name, now, now),
        )

    async def ensure(self, chat_id: int, t):
        if not t.id:
            return
        now = int(time.time())
        await self.conn.execute(
            "INSERT INTO users(chat_id,user_id,username,name,first_seen,last_seen) "
            "VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(chat_id,user_id) DO UPDATE SET "
            "username=COALESCE(excluded.username, users.username), name=excluded.name",
            (chat_id, t.id, t.username, t.name, now, now),
        )

    async def get_user(self, chat_id: int, user_id: int):
        return await self.q(
            "SELECT * FROM users WHERE chat_id=? AND user_id=?", (chat_id, user_id), one=True
        )

    async def find_username(self, chat_id: int, username: str):
        return await self.q(
            "SELECT * FROM users WHERE chat_id=? AND LOWER(username)=LOWER(?) "
            "ORDER BY last_seen DESC LIMIT 1",
            (chat_id, username),
            one=True,
        )

    async def add_warn(self, chat_id, user_id, by_id, reason) -> int:
        await self.conn.execute(
            "UPDATE users SET warns=warns+1 WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        )
        await self.conn.execute(
            "INSERT INTO warnlog(chat_id,user_id,by_id,reason,ts) VALUES(?,?,?,?,?)",
            (chat_id, user_id, by_id, reason, int(time.time())),
        )
        await self.conn.commit()
        row = await self.get_user(chat_id, user_id)
        return row["warns"] if row else 1

    async def dec_warn(self, chat_id, user_id) -> int:
        await self.x(
            "UPDATE users SET warns=CASE WHEN warns>1 THEN warns-1 ELSE 0 END WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        )
        row = await self.get_user(chat_id, user_id)
        return row["warns"] if row else 0

    async def set_warns(self, chat_id, user_id, n: int):
        await self.x(
            "UPDATE users SET warns=? WHERE chat_id=? AND user_id=?", (n, chat_id, user_id)
        )

    async def set_rank(self, chat_id, user_id, rank: int):
        await self.x(
            "UPDATE users SET rank=? WHERE chat_id=? AND user_id=?", (rank, chat_id, user_id)
        )

    async def ranked(self, chat_id):
        return await self.q(
            "SELECT * FROM users WHERE chat_id=? AND rank>0 ORDER BY rank DESC, name", (chat_id,)
        )

    async def set_nick(self, chat_id, user_id, nick):
        await self.x(
            "UPDATE users SET nick=? WHERE chat_id=? AND user_id=?", (nick, chat_id, user_id)
        )

    async def add_rep(self, chat_id, user_id, delta: int) -> int:
        await self.x(
            "UPDATE users SET rep=rep+? WHERE chat_id=? AND user_id=?", (delta, chat_id, user_id)
        )
        row = await self.get_user(chat_id, user_id)
        return row["rep"] if row else 0

    async def top(self, chat_id, col="msgs", limit=10):
        if col not in ("msgs", "rep"):
            col = "msgs"
        return await self.q(
            f"SELECT * FROM users WHERE chat_id=? AND {col}>0 ORDER BY {col} DESC LIMIT ?",
            (chat_id, limit),
        )

    # ---------- заметки ----------
    async def note_get(self, chat_id, name):
        row = await self.q(
            "SELECT text FROM notes WHERE chat_id=? AND name=?", (chat_id, name), one=True
        )
        return row["text"] if row else None

    async def note_set(self, chat_id, name, text):
        await self.x(
            "INSERT INTO notes(chat_id,name,text) VALUES(?,?,?) "
            "ON CONFLICT(chat_id,name) DO UPDATE SET text=excluded.text",
            (chat_id, name, text),
        )

    async def note_del(self, chat_id, name) -> bool:
        return (await self.x("DELETE FROM notes WHERE chat_id=? AND name=?", (chat_id, name))) > 0

    async def notes(self, chat_id):
        return [r["name"] for r in await self.q(
            "SELECT name FROM notes WHERE chat_id=? ORDER BY name", (chat_id,))]

    # ---------- стоп-слова ----------
    async def words(self, chat_id) -> set:
        if chat_id not in self._words:
            rows = await self.q("SELECT word FROM words WHERE chat_id=?", (chat_id,))
            self._words[chat_id] = {r["word"] for r in rows}
        return self._words[chat_id]

    async def word_add(self, chat_id, word):
        await self.x("INSERT INTO words(chat_id,word) VALUES(?,?) ON CONFLICT DO NOTHING", (chat_id, word))
        (await self.words(chat_id)).add(word)

    async def word_del(self, chat_id, word) -> bool:
        n = await self.x("DELETE FROM words WHERE chat_id=? AND word=?", (chat_id, word))
        (await self.words(chat_id)).discard(word)
        return n > 0

    # ---------- браки ----------
    async def marriage_of(self, chat_id, user_id):
        return await self.q(
            "SELECT * FROM marriages WHERE chat_id=? AND (u1=? OR u2=?)",
            (chat_id, user_id, user_id),
            one=True,
        )

    async def marry(self, chat_id, a, b):
        a, b = sorted((a, b))
        await self.x(
            "INSERT INTO marriages(chat_id,u1,u2,since) VALUES(?,?,?,?) "
            "ON CONFLICT(chat_id,u1) DO UPDATE SET u2=excluded.u2, since=excluded.since",
            (chat_id, a, b, int(time.time())),
        )

    async def divorce(self, chat_id, user_id):
        await self.x(
            "DELETE FROM marriages WHERE chat_id=? AND (u1=? OR u2=?)", (chat_id, user_id, user_id)
        )

    async def marriages(self, chat_id, limit=20):
        return await self.q(
            "SELECT * FROM marriages WHERE chat_id=? ORDER BY since LIMIT ?", (chat_id, limit)
        )

    # ---------- свои РП-команды ----------
    async def crp(self, chat_id) -> dict:
        if chat_id not in self._crp:
            rows = await self.q("SELECT * FROM customrp WHERE chat_id=?", (chat_id,))
            self._crp[chat_id] = {r["cmd"]: (r["id"], r["text"], bool(r["adult"])) for r in rows}
        return self._crp[chat_id]

    async def crp_add(self, chat_id, cmd, text, adult):
        await self.x(
            "INSERT INTO customrp(chat_id,cmd,text,adult) VALUES(?,?,?,?) "
            "ON CONFLICT(chat_id,cmd) DO UPDATE SET text=excluded.text, adult=excluded.adult",
            (chat_id, cmd, text, int(adult)),
        )
        self._crp.pop(chat_id, None)

    async def crp_del(self, chat_id, cmd) -> bool:
        n = await self.x("DELETE FROM customrp WHERE chat_id=? AND cmd=?", (chat_id, cmd))
        self._crp.pop(chat_id, None)
        return n > 0

    async def crp_get(self, chat_id, rid: int):
        row = await self.q(
            "SELECT * FROM customrp WHERE chat_id=? AND id=?", (chat_id, rid), one=True
        )
        return (row["text"], bool(row["adult"])) if row else None

    # ---------- статистика ----------
    async def stats(self):
        c = await self.q("SELECT COUNT(*) n FROM chats", one=True)
        u = await self.q("SELECT COUNT(*) n, COALESCE(SUM(msgs),0) m FROM users", one=True)
        return c["n"], u["n"], u["m"]
