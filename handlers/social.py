import random
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import state
from filters import Cmd
from utils import (
    GROUP_TYPES, RANK_NAMES, Target, enrich, esc, fmt_date, fmt_dur, from_user, mention, name_of,
    rank_of, resolve_target,
)

router = Router()
GROUP = F.chat.type.in_({"group", "supergroup"})


async def _target_or_self(m: Message, args: list):
    t, rest = await resolve_target(m, args)
    if not t and m.from_user:
        t = await enrich(m.chat.id, from_user(m.from_user))
    return t, rest


# ---------- профиль ----------
@router.message(Cmd("me", "profile", "профиль", "кто", "ктоя", "статистика"), GROUP)
async def cmd_profile(m: Message, args: list):
    if args and args[0].lower() == "я":
        args = args[1:]
    t, _ = await _target_or_self(m, args)
    if not t or not t.id:
        return await m.reply("❓ Не знаю такого пользователя.")
    db = state.db
    row = await db.get_user(m.chat.id, t.id)
    s = await db.settings(m.chat.id)
    rank = await rank_of(m.bot, m.chat.id, t.id)
    lines = [f"👤 <b>{mention(t, plain_name=True)}</b>"]
    if row and row["nick"]:
        lines.append(f"🏷 Ник: {esc(row['nick'])}")
    lines += [
        f"🆔 <code>{t.id}</code>",
        f"🎖 Ранг: {RANK_NAMES.get(rank, 'участник')}",
        f"💬 Сообщений: {row['msgs'] if row else 0}",
        f"⚠️ Варнов: {row['warns'] if row else 0}/{s['warn_limit']}",
        f"⭐ Репутация: {row['rep'] if row else 0}",
    ]
    mar = await db.marriage_of(m.chat.id, t.id)
    if mar:
        partner = mar["u2"] if mar["u1"] == t.id else mar["u1"]
        lines.append(f"💍 В браке с {await name_of(m.chat.id, partner)} (с {fmt_date(mar['since'])})")
    if row:
        lines.append(f"📅 Впервые замечен: {fmt_date(row['first_seen'])}")
    await m.answer("\n".join(lines))


@router.message(Cmd("top", "топ", "актив", "toprep", "топрепы"), GROUP)
async def cmd_top(m: Message, cmd: str):
    by_rep = cmd in ("toprep", "топрепы")
    rows = await state.db.top(m.chat.id, "rep" if by_rep else "msgs", 10)
    if not rows:
        return await m.answer("Пока нет данных.")
    medals = ["🥇", "🥈", "🥉"] + ["▫️"] * 7
    col = "rep" if by_rep else "msgs"
    lines = [
        f"{medals[i]} {mention(Target(r['user_id'], r['name'] or 'Участник', None, False, r['nick']))} — {r[col]}"
        for i, r in enumerate(rows)
    ]
    title = "⭐ Топ по репутации" if by_rep else "🏆 Самые активные"
    await m.answer(f"<b>{title}</b>\n" + "\n".join(lines))


# ---------- репутация ----------
REP_PLUS = {"+", "+1", "спасибо", "спс", "благодарю", "👍", "респект", "сяп"}
REP_MINUS = {"-", "-1", "👎", "минус"}
_rep_cd: dict = {}


@router.message(GROUP, F.reply_to_message, F.text.lower().in_(REP_PLUS | REP_MINUS))
async def on_rep(m: Message):
    r = m.reply_to_message
    if not m.from_user or not r.from_user or r.from_user.is_bot or r.from_user.id == m.from_user.id:
        return
    now = time.monotonic()
    if now - _rep_cd.get(m.from_user.id, 0) < 120:
        return
    _rep_cd[m.from_user.id] = now
    delta = 1 if m.text.lower() in REP_PLUS else -1
    giver = await enrich(m.chat.id, from_user(m.from_user))
    taker = await enrich(m.chat.id, from_user(r.from_user))
    await state.db.ensure(m.chat.id, taker)
    total = await state.db.add_rep(m.chat.id, taker.id, delta)
    sign = "повышает" if delta > 0 else "понижает"
    await m.answer(f"⭐ {mention(giver)} {sign} репутацию {mention(taker)} ({'+' if delta > 0 else ''}{delta}) → {total}")


# ---------- ник ----------
@router.message(Cmd("nick", "ник"), GROUP)
async def cmd_nick(m: Message, arg: str):
    if not m.from_user:
        return
    t = from_user(m.from_user)
    await state.db.ensure(m.chat.id, t)
    if not arg:
        row = await state.db.get_user(m.chat.id, t.id)
        cur = row["nick"] if row and row["nick"] else None
        return await m.reply(f"🏷 Твой ник: <b>{esc(cur)}</b>\nИзменить: <code>/nick Новый ник</code>, "
                             "убрать: <code>/nick -</code>" if cur else
                             "🏷 Ник не задан. Установить: <code>/nick Новый ник</code>")
    if arg == "-":
        await state.db.set_nick(m.chat.id, t.id, None)
        return await m.reply("🏷 Ник убран.")
    nick = " ".join(arg.split())[:32]
    await state.db.set_nick(m.chat.id, t.id, nick)
    await m.reply(f"🏷 Теперь тебя зовут <b>{esc(nick)}</b>")


# ---------- браки ----------
@router.message(Cmd("marry", "брак", "пожениться", "свадьба"), GROUP)
async def cmd_marry(m: Message, args: list):
    if not m.from_user:
        return
    t, _ = await resolve_target(m, args)
    if not t or not t.id:
        return await m.reply("💍 Ответь на сообщение того, кому делаешь предложение.")
    if t.id == m.from_user.id:
        return await m.reply("😅 На себе жениться нельзя.")
    if t.is_bot or t.id == state.bot_id:
        return await m.reply("🤖 Боты не женятся.")
    db = state.db
    if await db.marriage_of(m.chat.id, m.from_user.id):
        return await m.reply("💔 Ты уже в браке. Сначала разведись: /divorce")
    if await db.marriage_of(m.chat.id, t.id):
        return await m.reply("💔 Этот человек уже в браке.")
    me = await enrich(m.chat.id, from_user(m.from_user))
    await db.ensure(m.chat.id, me)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💍 Согласен(на)", callback_data=f"mar:{me.id}:{t.id}:y"),
        InlineKeyboardButton(text="💔 Отказать", callback_data=f"mar:{me.id}:{t.id}:n"),
    ]])
    await m.answer(f"💍 {mention(me)} делает предложение {mention(t)}!\n{mention(t)}, что ответишь?",
                   reply_markup=kb)


@router.callback_query(F.data.startswith("mar:"))
async def cb_marry(cb: CallbackQuery):
    _, a, b, ans = cb.data.split(":")
    a, b = int(a), int(b)
    chat_id = cb.message.chat.id
    uid = cb.from_user.id
    if uid == a and ans == "n":
        await cb.message.edit_text("🚫 Предложение отозвано.")
        return await cb.answer()
    if uid != b:
        return await cb.answer("Это решает только тот, кому сделали предложение 🙂", show_alert=True)
    if ans == "n":
        await cb.message.edit_text(f"💔 {await name_of(chat_id, b)} отказывает {await name_of(chat_id, a)}.")
        return await cb.answer()
    db = state.db
    if await db.marriage_of(chat_id, a) or await db.marriage_of(chat_id, b):
        await cb.message.edit_text("💔 Кто-то из вас уже в браке.")
        return await cb.answer()
    await db.marry(chat_id, a, b)
    await cb.message.edit_text(f"💒 {await name_of(chat_id, a)} и {await name_of(chat_id, b)} теперь пара! Совет да любовь! 🎉")
    await cb.answer()


@router.message(Cmd("divorce", "развод"), GROUP)
async def cmd_divorce(m: Message):
    if not m.from_user:
        return
    mar = await state.db.marriage_of(m.chat.id, m.from_user.id)
    if not mar:
        return await m.reply("Ты не в браке.")
    partner = mar["u2"] if mar["u1"] == m.from_user.id else mar["u1"]
    await state.db.divorce(m.chat.id, m.from_user.id)
    await m.answer(f"💔 {await name_of(m.chat.id, m.from_user.id)} и {await name_of(m.chat.id, partner)} разводятся.")


@router.message(Cmd("marriages", "браки", "пары"), GROUP)
async def cmd_marriages(m: Message):
    rows = await state.db.marriages(m.chat.id)
    if not rows:
        return await m.answer("💍 В этом чате пока нет браков. Стань первым: /marry")
    lines = [f"💞 {await name_of(m.chat.id, r['u1'])} + {await name_of(m.chat.id, r['u2'])} — с {fmt_date(r['since'])}"
             for r in rows]
    await m.answer("<b>Пары чата</b>\n" + "\n".join(lines))


# ---------- жалоба ----------
@router.message(Cmd("report", "репорт", "жалоба", "admin", "админ", plain="free"), GROUP)
async def cmd_report(m: Message, arg: str):
    try:
        admins = await m.bot.get_chat_administrators(m.chat.id)
    except TelegramAPIError:
        return await m.reply("Не могу получить список админов.")
    pings = "".join(f'<a href="tg://user?id={a.user.id}">⁣</a>' for a in admins if not a.user.is_bot)
    who = mention(from_user(m.from_user)) if m.from_user else "Кто-то"
    text = f"🚨 Жалоба от {who}"
    r = m.reply_to_message
    if r and r.from_user:
        text += f" на {mention(from_user(r.from_user))}"
    if arg:
        text += f"\n📝 {esc(arg[:300])}"
    text += pings
    await (r or m).reply(text)


# ---------- инфо ----------
@router.message(Cmd("id", "айди", "ид"))
async def cmd_id(m: Message, args: list):
    lines = [f"💬 Чат: <code>{m.chat.id}</code>"]
    if m.from_user:
        lines.append(f"👤 Ты: <code>{m.from_user.id}</code>")
    t, _ = await resolve_target(m, args) if m.chat.type in GROUP_TYPES else (None, [])
    if t and t.id and (not m.from_user or t.id != m.from_user.id):
        lines.append(f"🎯 {mention(t)}: <code>{t.id}</code>")
    await m.answer("\n".join(lines))


@router.message(Cmd("ping", "пинг", "бот"))
async def cmd_ping(m: Message):
    await m.answer(f"🏓 Понг! Работаю уже {fmt_dur(time.time() - state.started)}.")


@router.message(Cmd("botstats", "ботстат", plain="none"))
async def cmd_botstats(m: Message):
    from config import OWNER_IDS
    if not m.from_user or m.from_user.id not in OWNER_IDS:
        return
    chats, users, msgs = await state.db.stats()
    await m.answer(f"📊 Чатов: {chats}\n👥 Пользователей: {users}\n💬 Сообщений учтено: {msgs}")


# ---------- мини-игры ----------
@router.message(Cmd("roll", "ролл", "рандом"))
async def cmd_roll(m: Message, args: list):
    n = int(args[0]) if args and args[0].isdigit() and int(args[0]) > 1 else 100
    await m.reply(f"🎲 Выпало: <b>{random.randint(1, n)}</b> (из {n})")


@router.message(Cmd("choose", "выбери", "выбор", plain="free"))
async def cmd_choose(m: Message, arg: str):
    sep = "|" if "|" in arg else (" или " if " или " in arg else ",")
    opts = [o.strip() for o in arg.split(sep) if o.strip()]
    if len(opts) < 2:
        return await m.reply("Формат: <code>/choose чай | кофе</code>")
    await m.reply(f"🎯 Я выбираю: <b>{esc(random.choice(opts))}</b>")


BALL = ["Да, определённо", "Скорее да", "Знаки говорят «да»", "Пока не ясно, спроси ещё раз",
        "Лучше не рассказывать", "Не рассчитывай на это", "Скорее нет", "Мой ответ — нет",
        "Без сомнений", "Весьма сомнительно"]


@router.message(Cmd("ball", "шар", "8ball", plain="free"))
async def cmd_ball(m: Message):
    await m.reply(f"🔮 {random.choice(BALL)}")
