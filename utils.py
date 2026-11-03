import asyncio
import html
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError

import state
from config import OWNER_IDS, TZ_OFFSET

GROUP_TYPES = ("group", "supergroup")
ANON_ID = 1087968824  # GroupAnonymousBot

esc = html.escape

RANK_NAMES = {0: "участник", 1: "модератор", 2: "ст. модератор", 5: "администратор", 9: "владелец бота"}


@dataclass
class Target:
    id: int | None
    name: str
    username: str | None = None
    is_bot: bool = False
    nick: str | None = None


def from_user(u) -> Target:
    return Target(u.id, u.full_name, u.username, u.is_bot)


def mention(t: Target, plain_name: bool = False) -> str:
    name = t.name if plain_name else (t.nick or t.name)
    if t.id:
        return f'<a href="tg://user?id={t.id}">{esc(name)}</a>'
    return esc(name)


def fmt_date(ts: int | None) -> str:
    if not ts:
        return "—"
    tz = timezone(timedelta(hours=TZ_OFFSET))
    return datetime.fromtimestamp(ts, tz).strftime("%d.%m.%Y")


# ---------- время ----------
_UNITS = {"м": 60, "m": 60, "ч": 3600, "h": 3600, "д": 86400, "d": 86400, "н": 604800, "w": 604800}
_FOREVER = {"навсегда", "forever", "inf", "вечно", "навечно"}


def parse_duration(s: str) -> int | None:
    """'30м' -> 1800, 'навсегда' -> 0, не время -> None."""
    s = s.lower().strip()
    if s in _FOREVER:
        return 0
    m = re.fullmatch(r"(\d{1,6})\s*([a-zа-яё]+)", s)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    if unit.startswith("мес"):
        return n * 30 * 86400
    mult = _UNITS.get(unit[0])
    if not mult:
        return None
    return n * mult


def fmt_dur(sec: int | float) -> str:
    if not sec:
        return "навсегда"
    d, r = divmod(int(sec), 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    parts = []
    if d:
        parts.append(f"{d} д")
    if h:
        parts.append(f"{h} ч")
    if m:
        parts.append(f"{m} мин")
    return " ".join(parts) or "меньше минуты"


# ---------- задачи в фоне ----------
_tasks: set = set()


def spawn(coro):
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return t


async def safe_delete(m):
    try:
        await m.delete()
    except TelegramAPIError:
        pass


async def _delete_later(m, sec):
    await asyncio.sleep(sec)
    await safe_delete(m)


def temp(m, sec: int = 10):
    """Удалить сообщение бота через sec секунд."""
    if m is not None:
        spawn(_delete_later(m, sec))


# ---------- ранги ----------
_admin_cache: dict = {}


async def is_chat_admin(bot, chat_id: int, user_id: int) -> bool:
    key = (chat_id, user_id)
    now = time.monotonic()
    hit = _admin_cache.get(key)
    if hit and now - hit[0] < 60:
        return hit[1]
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        ok = m.status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR)
    except TelegramAPIError:
        ok = False
    if len(_admin_cache) > 5000:
        _admin_cache.clear()
    _admin_cache[key] = (now, ok)
    return ok


async def rank_of(bot, chat_id: int, user_id: int) -> int:
    """0 участник · 1 мод · 2 ст.мод · 5 админ · 9 владелец бота."""
    if user_id in OWNER_IDS:
        return 9
    if user_id == ANON_ID:
        return 5
    if await is_chat_admin(bot, chat_id, user_id):
        return 5
    row = await state.db.get_user(chat_id, user_id)
    return row["rank"] if row else 0


async def caller_rank(message) -> int:
    chat, u = message.chat, message.from_user
    if message.sender_chat and message.sender_chat.id == chat.id:
        return 5  # анонимный админ
    if not u:
        return 0
    return await rank_of(message.bot, chat.id, u.id)


async def require(message, minrank: int, prefixed: bool = True) -> int | None:
    """Проверка группы и ранга. Возвращает ранг или None (если нельзя)."""
    if message.chat.type not in GROUP_TYPES:
        if prefixed:
            await message.reply("Эта команда работает только в группах.")
        return None
    rank = await caller_rank(message)
    if rank < minrank:
        if prefixed:
            await message.reply("⛔ Недостаточно прав.")
        return None
    return rank


# ---------- цель команды ----------
async def enrich(chat_id: int, t: Target) -> Target:
    if t.id:
        row = await state.db.get_user(chat_id, t.id)
        if row:
            t.nick = row["nick"]
    return t


async def resolve_target(message, args: list[str], allow_id: bool = True):
    """Возвращает (Target | None, остаток_аргументов).
    Цель: @username / id первым аргументом либо автор сообщения, на которое ответили."""
    chat_id = message.chat.id
    db = state.db
    if args:
        a = args[0]
        if a.startswith("@") and len(a) > 1:
            row = await db.find_username(chat_id, a[1:])
            if row:
                t = Target(row["user_id"], row["name"] or a, row["username"], False, row["nick"])
                return t, args[1:]
            return Target(None, a, a[1:]), args[1:]
        if allow_id and a.isdigit() and len(a) >= 6:
            uid = int(a)
            row = await db.get_user(chat_id, uid)
            t = Target(
                uid,
                (row["name"] if row else None) or f"id{uid}",
                row["username"] if row else None,
                False,
                row["nick"] if row else None,
            )
            return t, args[1:]
    r = message.reply_to_message
    if r and r.from_user and not r.forum_topic_created and r.from_user.id not in (777000, ANON_ID):
        t = from_user(r.from_user)
        await db.ensure(chat_id, t)
        return await enrich(chat_id, t), args
    return None, args


async def name_of(chat_id: int, user_id: int) -> str:
    """Упоминание пользователя по данным из БД."""
    row = await state.db.get_user(chat_id, user_id)
    t = Target(user_id, (row["name"] if row else None) or "Участник", None, False,
               row["nick"] if row else None)
    return mention(t)
