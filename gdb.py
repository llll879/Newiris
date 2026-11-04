"""Таблицы и функции для экономики, семьи, отношений и памяти ИИ."""
import time

import state

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallet(
    chat_id INTEGER, user_id INTEGER,
    money INTEGER DEFAULT 1000,
    last_daily INTEGER DEFAULT 0, last_work INTEGER DEFAULT 0, last_income INTEGER DEFAULT 0,
    last_steal INTEGER DEFAULT 0, last_misc INTEGER DEFAULT 0, last_benefit INTEGER DEFAULT 0,
    works INTEGER DEFAULT 0,
    preg_until INTEGER DEFAULT 0, preg_with INTEGER DEFAULT 0,
    PRIMARY KEY(chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS assets(
    chat_id INTEGER, user_id INTEGER, kind TEXT, idx INTEGER,
    PRIMARY KEY(chat_id, user_id, kind)
);
CREATE TABLE IF NOT EXISTS kids(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER, p1 INTEGER, p2 INTEGER, name TEXT, gender TEXT, born INTEGER
);
CREATE INDEX IF NOT EXISTS idx_kids ON kids(chat_id, p1);
CREATE TABLE IF NOT EXISTS rels(
    chat_id INTEGER, u1 INTEGER, u2 INTEGER, kind TEXT,
    love INTEGER DEFAULT 0, since INTEGER, last_date INTEGER DEFAULT 0,
    PRIMARY KEY(chat_id, u1, u2, kind)
);
CREATE TABLE IF NOT EXISTS ai_rel(
    user_id INTEGER PRIMARY KEY,
    persona TEXT, name TEXT, love INTEGER DEFAULT 0, status TEXT DEFAULT 'знакомые',
    adult INTEGER DEFAULT 0, since INTEGER, last_love INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ai_hist(
    id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT, role TEXT, text TEXT, ts INTEGER
);
CREATE INDEX IF NOT EXISTS idx_aihist ON ai_hist(scope, id);
"""

_COLS = {"last_daily", "last_work", "last_income", "last_steal", "last_misc", "last_benefit",
         "works", "preg_until", "preg_with", "money"}


async def init():
    await state.db.conn.executescript(SCHEMA)
    await state.db.conn.commit()


# ---------- деньги ----------
async def wallet(chat: int, uid: int):
    db = state.db
    await db.conn.execute("INSERT INTO wallet(chat_id,user_id) VALUES(?,?) ON CONFLICT DO NOTHING", (chat, uid))
    return await db.q("SELECT * FROM wallet WHERE chat_id=? AND user_id=?", (chat, uid), one=True)


async def money(chat: int, uid: int) -> int:
    return (await wallet(chat, uid))["money"]


async def add_money(chat: int, uid: int, delta: int) -> int:
    await wallet(chat, uid)
    await state.db.x("UPDATE wallet SET money=CASE WHEN money+?>0 THEN money+? ELSE 0 END "
                     "WHERE chat_id=? AND user_id=?", (delta, delta, chat, uid))
    return await money(chat, uid)


async def spend(chat: int, uid: int, amount: int) -> bool:
    """Атомарно списывает деньги. False — если не хватает."""
    if amount < 0:
        return False
    await wallet(chat, uid)
    n = await state.db.x(
        "UPDATE wallet SET money=money-? WHERE chat_id=? AND user_id=? AND money>=?",
        (amount, chat, uid, amount))
    return n > 0


async def setf(chat: int, uid: int, **cols):
    await wallet(chat, uid)
    for k, v in cols.items():
        if k not in _COLS:
            raise ValueError(k)
        await state.db.conn.execute(
            f"UPDATE wallet SET {k}=? WHERE chat_id=? AND user_id=?", (v, chat, uid))
    await state.db.conn.commit()


async def incr(chat: int, uid: int, col: str, delta: int = 1):
    if col not in _COLS:
        raise ValueError(col)
    await wallet(chat, uid)
    await state.db.x(f"UPDATE wallet SET {col}={col}+? WHERE chat_id=? AND user_id=?",
                     (delta, chat, uid))


async def richest(chat: int, limit: int = 10):
    return await state.db.q(
        "SELECT w.user_id, w.money, u.name, u.nick FROM wallet w "
        "LEFT JOIN users u ON u.chat_id=w.chat_id AND u.user_id=w.user_id "
        "WHERE w.chat_id=? ORDER BY w.money DESC LIMIT ?", (chat, limit))


# ---------- имущество ----------
async def asset(chat: int, uid: int, kind: str):
    row = await state.db.q("SELECT idx FROM assets WHERE chat_id=? AND user_id=? AND kind=?",
                           (chat, uid, kind), one=True)
    return row["idx"] if row else None


async def set_asset(chat: int, uid: int, kind: str, idx: int):
    await state.db.x("INSERT INTO assets(chat_id,user_id,kind,idx) VALUES(?,?,?,?) "
                     "ON CONFLICT(chat_id,user_id,kind) DO UPDATE SET idx=excluded.idx",
                     (chat, uid, kind, idx))


async def del_asset(chat: int, uid: int, kind: str):
    await state.db.x("DELETE FROM assets WHERE chat_id=? AND user_id=? AND kind=?",
                     (chat, uid, kind))


# ---------- дети ----------
async def add_kid(chat: int, p1: int, p2: int, name: str, gender: str) -> int:
    row = await state.db.q(
        "INSERT INTO kids(chat_id,p1,p2,name,gender,born) VALUES(?,?,?,?,?,?) RETURNING id",
        (chat, p1, p2, name, gender, int(time.time())), one=True)
    await state.db.conn.commit()
    return row["id"]


async def kids_of(chat: int, uid: int):
    return await state.db.q(
        "SELECT * FROM kids WHERE chat_id=? AND (p1=? OR p2=?) ORDER BY born", (chat, uid, uid))


async def rename_last_kid(chat: int, uid: int, name: str) -> bool:
    row = await state.db.q(
        "SELECT id FROM kids WHERE chat_id=? AND (p1=? OR p2=?) ORDER BY born DESC, id DESC LIMIT 1",
        (chat, uid, uid), one=True)
    if not row:
        return False
    await state.db.x("UPDATE kids SET name=? WHERE id=?", (name, row["id"]))
    return True


# ---------- пары и друзья ----------
def _pair_key(a: int, b: int):
    return (a, b) if a < b else (b, a)


async def pair_of(chat: int, uid: int):
    return await state.db.q(
        "SELECT * FROM rels WHERE chat_id=? AND kind='pair' AND (u1=? OR u2=?)",
        (chat, uid, uid), one=True)


async def make_pair(chat: int, a: int, b: int):
    u1, u2 = _pair_key(a, b)
    await state.db.x(
        "INSERT INTO rels(chat_id,u1,u2,kind,love,since) VALUES(?,?,?,'pair',0,?) ON CONFLICT DO NOTHING",
        (chat, u1, u2, int(time.time())))


async def break_pair(chat: int, uid: int):
    await state.db.x("DELETE FROM rels WHERE chat_id=? AND kind='pair' AND (u1=? OR u2=?)",
                     (chat, uid, uid))


async def love_add(chat: int, a: int, b: int, delta: int):
    u1, u2 = _pair_key(a, b)
    await state.db.x("UPDATE rels SET love=love+? WHERE chat_id=? AND u1=? AND u2=? AND kind='pair'",
                     (delta, chat, u1, u2))


async def set_last_date(chat: int, a: int, b: int):
    u1, u2 = _pair_key(a, b)
    await state.db.x("UPDATE rels SET last_date=? WHERE chat_id=? AND u1=? AND u2=? AND kind='pair'",
                     (int(time.time()), chat, u1, u2))


async def partner_of(chat: int, uid: int):
    """(id партнёра, в_браке, строка пары|None) либо (None, False, None)."""
    mar = await state.db.marriage_of(chat, uid)
    pair = await pair_of(chat, uid)
    if mar:
        pid = mar["u2"] if mar["u1"] == uid else mar["u1"]
        return pid, True, pair
    if pair:
        pid = pair["u2"] if pair["u1"] == uid else pair["u1"]
        return pid, False, pair
    return None, False, None


async def friend_add(chat: int, a: int, b: int):
    u1, u2 = _pair_key(a, b)
    await state.db.x(
        "INSERT INTO rels(chat_id,u1,u2,kind,since) VALUES(?,?,?,'friend',?) ON CONFLICT DO NOTHING",
        (chat, u1, u2, int(time.time())))


async def friend_del(chat: int, a: int, b: int) -> bool:
    u1, u2 = _pair_key(a, b)
    return (await state.db.x(
        "DELETE FROM rels WHERE chat_id=? AND u1=? AND u2=? AND kind='friend'",
        (chat, u1, u2))) > 0


async def are_friends(chat: int, a: int, b: int) -> bool:
    u1, u2 = _pair_key(a, b)
    return bool(await state.db.q(
        "SELECT 1 FROM rels WHERE chat_id=? AND u1=? AND u2=? AND kind='friend'",
        (chat, u1, u2), one=True))


async def friends_of(chat: int, uid: int) -> list[int]:
    rows = await state.db.q(
        "SELECT u1,u2 FROM rels WHERE chat_id=? AND kind='friend' AND (u1=? OR u2=?)",
        (chat, uid, uid))
    return [r["u2"] if r["u1"] == uid else r["u1"] for r in rows]


# ---------- ИИ ----------
async def ai_get(uid: int):
    await state.db.conn.execute(
        "INSERT INTO ai_rel(user_id, since) VALUES(?,?) ON CONFLICT DO NOTHING", (uid, int(time.time())))
    return await state.db.q("SELECT * FROM ai_rel WHERE user_id=?", (uid,), one=True)


async def ai_set(uid: int, **cols):
    allowed = {"persona", "name", "love", "status", "adult", "last_love"}
    await ai_get(uid)
    for k, v in cols.items():
        if k not in allowed:
            raise ValueError(k)
        await state.db.conn.execute(f"UPDATE ai_rel SET {k}=? WHERE user_id=?", (v, uid))
    await state.db.conn.commit()


async def hist_add(scope: str, role: str, text: str):
    await state.db.conn.execute(
        "INSERT INTO ai_hist(scope,role,text,ts) VALUES(?,?,?,?)",
        (scope, role, text[:1500], int(time.time())))
    await state.db.conn.execute(
        "DELETE FROM ai_hist WHERE scope=? AND id NOT IN "
        "(SELECT id FROM ai_hist WHERE scope=? ORDER BY id DESC LIMIT 40)", (scope, scope))
    await state.db.conn.commit()


async def hist_get(scope: str, limit: int = 16):
    rows = await state.db.q(
        "SELECT role,text FROM ai_hist WHERE scope=? ORDER BY id DESC LIMIT ?", (scope, limit))
    return [(r["role"], r["text"]) for r in reversed(rows)]


async def hist_clear(scope: str):
    await state.db.x("DELETE FROM ai_hist WHERE scope=?", (scope,))
