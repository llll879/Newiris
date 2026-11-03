"""Автозащита чата: антифлуд, ссылки, мат, стоп-слова, пересылки, боты, капча, приветствие."""
import asyncio
import re
import time
from collections import defaultdict, deque

from aiogram import BaseMiddleware, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    CallbackQuery, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

import services
import state
from utils import (
    GROUP_TYPES, esc, fmt_dur, from_user, is_chat_admin, mention, rank_of, safe_delete, spawn,
    temp,
)

router = Router()

MAT_RE = re.compile(
    r"(?:^|[^а-яё])(?:"
    r"ху[йяеию]\w*|пизд\w*|еб[аяуоиеё]\w*|ёб\w*|бля(?:д\w*|ть)?|сука|сучара|мудак\w*|"
    r"залуп\w*|долбо[её]б\w*|пидор\w*|пидар\w*|гандон\w*|манд[аоуы]\w*|шлюх\w*"
    r")(?=$|[^а-яё])",
    re.IGNORECASE,
)
LINK_RE = re.compile(
    r"(?i)(https?://|www\.|t\.me/|telegram\.(?:me|dog)/|\b[a-z0-9-]{2,}\.(?:com|ru|net|org|io|me|xyz|club|site|online|top|info|ly|cc)\b)"
)

_flood: dict = defaultdict(deque)


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


class Guard(BaseMiddleware):
    """Считает активность и применяет защиту ко всем сообщениям группы."""

    def __init__(self, count: bool = True):
        self.count = count

    async def __call__(self, handler, event: Message, data):
        if event.chat.type not in GROUP_TYPES:
            return await handler(event, data)
        u = event.from_user
        # служебные, анонимные админы, автопересылки из канала — пропускаем
        if (not u or u.is_bot or event.new_chat_members or event.left_chat_member
                or (event.sender_chat and event.sender_chat.id == event.chat.id)
                or getattr(event, "is_automatic_forward", False)):
            return await handler(event, data)
        db = state.db
        if self.count:
            await db.touch(event.chat.id, u)
        if await rank_of(event.bot, event.chat.id, u.id) >= 1:
            return await handler(event, data)
        s = await db.settings(event.chat.id)
        if await self.violates(event, s):
            return None
        return await handler(event, data)

    async def violates(self, m: Message, s: dict) -> bool:
        text = m.text or m.caption or ""
        low = _norm(text)

        if s["antiforward"] and getattr(m, "forward_origin", None):
            return await self.punish(m, s, "пересылка сообщений запрещена")

        if s["antilink"]:
            ents = (m.entities or []) + (m.caption_entities or [])
            if any(e.type in ("url", "text_link") for e in ents) or LINK_RE.search(text):
                return await self.punish(m, s, "ссылки запрещены")

        if s["antimat"] and text and MAT_RE.search(low):
            return await self.punish(m, s, "нецензурная лексика")

        if s["badwords"] and low:
            words = await state.db.words(m.chat.id)
            if words and any(w in low for w in words):
                return await self.punish(m, s, "стоп-слово")

        if s["antiflood"]:
            key = (m.chat.id, m.from_user.id)
            dq = _flood[key]
            now = time.monotonic()
            dq.append(now)
            while dq and now - dq[0] > s["flood_window"]:
                dq.popleft()
            if len(dq) >= s["flood_limit"]:
                dq.clear()
                await safe_delete(m)
                try:
                    await services.mute(m.bot, m.chat.id, m.from_user.id, s["flood_mute"] * 60)
                    note = f"🌊 {mention(from_user(m.from_user))} флудит — мут на {s['flood_mute']} мин."
                except TelegramAPIError:
                    note = f"🌊 {mention(from_user(m.from_user))}, не флуди!"
                temp(await m.answer(note), 15)
                return True
        return False

    async def punish(self, m: Message, s: dict, reason: str) -> bool:
        await safe_delete(m)
        t = from_user(m.from_user)
        if s["autowarn"]:
            text, _ = await services.apply_warn(m.bot, m.chat.id, t, state.bot_id, reason, "🤖 автозащита")
        else:
            text = f"🚫 {mention(t)}, {esc(reason)}."
        temp(await m.answer(text), 20)
        return True


# ---------------- вход / выход участников ----------------
async def send_welcome(m: Message, u):
    s = await state.db.settings(m.chat.id)
    if not s["welcome"]:
        return
    t = from_user(u)
    text = (esc(s["welcome_text"])
            .replace("{name}", mention(t))
            .replace("{chat}", esc(m.chat.title or ""))
            .replace("{id}", str(u.id)))
    await m.answer(text)


async def captcha_timeout(bot, chat_id: int, user_id: int, msg_id: int, sec: int):
    await asyncio.sleep(sec)
    if state.captcha.pop((chat_id, user_id), None) is None:
        return
    try:
        await services.kick(bot, chat_id, user_id)
    except TelegramAPIError:
        pass
    try:
        await bot.delete_message(chat_id, msg_id)
    except TelegramAPIError:
        pass


async def start_captcha(m: Message, u, s: dict) -> bool:
    try:
        await m.bot.restrict_chat_member(
            m.chat.id, u.id, permissions=ChatPermissions(can_send_messages=False))
    except TelegramAPIError:
        return False  # нет прав — работаем без капчи
    sec = s["captcha_sec"]
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Я не бот", callback_data=f"cap:{u.id}")]])
    sent = await m.answer(
        f"🤖 {mention(from_user(u))}, нажми кнопку в течение {fmt_dur(sec)}, "
        f"чтобы подтвердить, что ты человек. Иначе придётся покинуть чат.",
        reply_markup=kb,
    )
    state.captcha[(m.chat.id, u.id)] = sent.message_id
    spawn(captcha_timeout(m.bot, m.chat.id, u.id, sent.message_id, sec))
    return True


@router.callback_query(F.data.startswith("cap:"))
async def cb_captcha(cb: CallbackQuery):
    uid = int(cb.data.split(":")[1])
    if cb.from_user.id != uid:
        return await cb.answer("Эта кнопка не для тебя 🙂", show_alert=True)
    chat_id = cb.message.chat.id
    state.captcha.pop((chat_id, uid), None)
    try:
        await services.unmute(cb.bot, chat_id, uid)
    except TelegramAPIError:
        pass
    await cb.answer("Добро пожаловать!")
    await safe_delete(cb.message)
    await send_welcome(cb.message, cb.from_user)


@router.message(F.new_chat_members)
async def on_join(m: Message):
    db = state.db
    await db.set_title(m.chat.id, m.chat.title or "")
    s = await db.settings(m.chat.id)
    adder = m.from_user
    for u in m.new_chat_members:
        if u.id == state.bot_id:
            await m.answer(
                "👋 Привет! Я бот-модератор.\n\nСделайте меня <b>администратором</b> "
                "(удаление сообщений, блокировка участников, закрепление) и отправьте /help — "
                "расскажу, что умею. Настройки: /settings")
            continue
        if u.is_bot:
            if (s["antibot"] and adder and adder.id != u.id
                    and await rank_of(m.bot, m.chat.id, adder.id) < 2):
                try:
                    await services.kick(m.bot, m.chat.id, u.id)
                    temp(await m.answer("🛡 Добавлять ботов могут только старшие модераторы и админы."), 15)
                except TelegramAPIError:
                    pass
            continue
        await db.ensure(m.chat.id, from_user(u))
        if await rank_of(m.bot, m.chat.id, u.id) >= 1:
            continue
        if s["captcha"] and await start_captcha(m, u, s):
            continue
        await send_welcome(m, u)
    if s["del_service"]:
        await safe_delete(m)


@router.message(F.left_chat_member)
async def on_left(m: Message):
    s = await state.db.settings(m.chat.id)
    if s["del_service"]:
        await safe_delete(m)


@router.edited_message()
async def on_edited(m: Message):
    """Заглушка: правки проходят через Guard (защита), а команды из правок не выполняются."""
    return
