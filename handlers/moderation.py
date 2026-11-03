from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message

import services
import state
from filters import Cmd
from utils import (
    GROUP_TYPES, Target, caller_rank, esc, fmt_dur, mention, parse_duration, rank_of,
    require, resolve_target, safe_delete, temp,
)

router = Router()


def by_name(m: Message) -> str:
    return esc(m.from_user.full_name) if m.from_user else "Админ"


async def attempt(m: Message, coro) -> bool:
    try:
        await coro
        return True
    except TelegramAPIError as e:
        await m.reply(services.explain(e))
        return False


async def prep(m: Message, minrank: int, prefixed: bool, args: list, check_target_rank=True):
    """Общая подготовка: права, цель, ранг цели. Возвращает (rank, target, rest) или None."""
    rank = await require(m, minrank, prefixed)
    if rank is None:
        return None
    t, rest = await resolve_target(m, args)
    if not t:
        await m.reply("↩️ Ответь на сообщение пользователя или укажи @username / id.")
        return None
    if not t.id:
        await m.reply("❓ Не знаю такого пользователя. Ответь на его сообщение командой.")
        return None
    if t.id == state.bot_id:
        await m.reply("🙃 Со мной так нельзя.")
        return None
    if check_target_rank:
        trank = await rank_of(m.bot, m.chat.id, t.id)
        if trank >= rank:
            await m.reply("⛔ Нельзя применить к этому пользователю (равный или более высокий ранг).")
            return None
    return rank, t, rest


def split_duration(rest: list[str], default: int):
    if rest:
        d = parse_duration(rest[0])
        if d is not None:
            return d, rest[1:]
    return default, rest


# ---------------- варны ----------------
@router.message(Cmd("warn", "варн", "пред", "предупреждение", "предупредить", plain="free"))
async def cmd_warn(m: Message, prefixed: bool, args: list):
    p = await prep(m, 1, prefixed, args)
    if not p:
        return
    _, t, rest = p
    uid = m.from_user.id if m.from_user else 0
    text, markup = await services.apply_warn(m.bot, m.chat.id, t, uid, " ".join(rest), by_name(m))
    await m.answer(text, reply_markup=markup)


@router.message(Cmd("unwarn", "анварн", "снятьварн", "-варн", plain="free"))
async def cmd_unwarn(m: Message, prefixed: bool, args: list):
    p = await prep(m, 1, prefixed, args, check_target_rank=False)
    if not p:
        return
    _, t, _ = p
    n = await state.db.dec_warn(m.chat.id, t.id)
    await m.answer(f"✅ С {mention(t)} снято предупреждение. Сейчас: {n}.")


@router.message(Cmd("resetwarns", "сбросварны", "обнулитьварны", plain="free"))
async def cmd_resetwarns(m: Message, prefixed: bool, args: list):
    p = await prep(m, 1, prefixed, args, check_target_rank=False)
    if not p:
        return
    _, t, _ = p
    await state.db.set_warns(m.chat.id, t.id, 0)
    await m.answer(f"✅ Предупреждения {mention(t)} обнулены.")


@router.message(Cmd("warns", "варны", "преды"))
async def cmd_warns(m: Message, prefixed: bool, args: list):
    if m.chat.type not in GROUP_TYPES:
        return
    t, _ = await resolve_target(m, args)
    if not t and m.from_user:
        from utils import from_user
        t = from_user(m.from_user)
    if not t or not t.id:
        return await m.reply("❓ Не знаю такого пользователя.")
    row = await state.db.get_user(m.chat.id, t.id)
    s = await state.db.settings(m.chat.id)
    n = row["warns"] if row else 0
    await m.answer(f"⚠️ У {mention(t)} предупреждений: {n}/{s['warn_limit']}")


@router.callback_query(F.data.startswith("unw:"))
async def cb_unwarn(cb: CallbackQuery):
    chat = cb.message.chat
    if await rank_of(cb.bot, chat.id, cb.from_user.id) < 1:
        return await cb.answer("Только для модераторов", show_alert=True)
    uid = int(cb.data.split(":")[1])
    n = await state.db.dec_warn(chat.id, uid)
    await cb.answer(f"Варн снят. Сейчас: {n}")
    try:
        await cb.message.edit_text(cb.message.html_text + f"\n\n✅ Варн снят ({esc(cb.from_user.full_name)}).")
    except TelegramAPIError:
        pass


# ---------------- мут ----------------
@router.message(Cmd("mute", "мут", "ро", "ro", "молчи", "замолчи", plain="free"))
async def cmd_mute(m: Message, prefixed: bool, args: list):
    p = await prep(m, 1, prefixed, args)
    if not p:
        return
    _, t, rest = p
    sec, rest = split_duration(rest, 3600)
    if sec:
        sec = max(60, min(sec, 366 * 86400))
    reason = " ".join(rest)
    if not await attempt(m, services.mute(m.bot, m.chat.id, t.id, sec)):
        return
    text = f"🔇 {mention(t)} в муте: {fmt_dur(sec)}.\n👮 Модератор: {by_name(m)}"
    if reason:
        text += f"\n📝 Причина: {esc(reason)}"
    await m.answer(text)


@router.message(Cmd("unmute", "анмут", "размут", "говори", plain="free"))
async def cmd_unmute(m: Message, prefixed: bool, args: list):
    p = await prep(m, 1, prefixed, args, check_target_rank=False)
    if not p:
        return
    _, t, _ = p
    if await attempt(m, services.unmute(m.bot, m.chat.id, t.id)):
        await m.answer(f"🔊 {mention(t)} снова может писать.")


# ---------------- бан / кик ----------------
@router.message(Cmd("ban", "бан", "забанить", plain="free"))
async def cmd_ban(m: Message, prefixed: bool, args: list):
    p = await prep(m, 2, prefixed, args)
    if not p:
        return
    _, t, rest = p
    sec, rest = split_duration(rest, 0)
    if sec:
        sec = max(60, min(sec, 366 * 86400))
    reason = " ".join(rest)
    if not await attempt(m, services.ban(m.bot, m.chat.id, t.id, sec)):
        return
    text = f"🔨 {mention(t)} забанен: {fmt_dur(sec)}.\n👮 Модератор: {by_name(m)}"
    if reason:
        text += f"\n📝 Причина: {esc(reason)}"
    await m.answer(text)


@router.message(Cmd("unban", "разбан", "разбанить", plain="free"))
async def cmd_unban(m: Message, prefixed: bool, args: list):
    p = await prep(m, 2, prefixed, args, check_target_rank=False)
    if not p:
        return
    _, t, _ = p
    if await attempt(m, services.unban(m.bot, m.chat.id, t.id)):
        await m.answer(f"✅ {mention(t)} разбанен.")


@router.message(Cmd("kick", "кик", "исключить", "выкинуть", plain="free"))
async def cmd_kick(m: Message, prefixed: bool, args: list):
    p = await prep(m, 2, prefixed, args)
    if not p:
        return
    _, t, rest = p
    if not await attempt(m, services.kick(m.bot, m.chat.id, t.id)):
        return
    text = f"👢 {mention(t)} исключён из чата.\n👮 Модератор: {by_name(m)}"
    if rest:
        text += f"\n📝 Причина: {esc(' '.join(rest))}"
    await m.answer(text)


# ---------------- удаление сообщений ----------------
@router.message(Cmd("del", "delete", "дел", "удалить", plain="free"))
async def cmd_del(m: Message, prefixed: bool):
    if await require(m, 1, prefixed) is None:
        return
    ids = [m.message_id]
    r = m.reply_to_message
    if r and not r.forum_topic_created:
        ids.append(r.message_id)
    try:
        await m.bot.delete_messages(m.chat.id, ids)
    except TelegramAPIError as e:
        await m.reply(services.explain(e))


@router.message(Cmd("purge", "чистка", "очистить", plain="free"))
async def cmd_purge(m: Message, prefixed: bool, args: list):
    if await require(m, 1, prefixed) is None:
        return
    r = m.reply_to_message
    if r and not r.forum_topic_created:
        start = r.message_id
    elif args and args[0].isdigit():
        start = m.message_id - min(int(args[0]), 200)
    else:
        return await m.reply("Ответь на сообщение, с которого начать чистку, или укажи число: «чистка 30».")
    ids = list(range(max(start, m.message_id - 300), m.message_id + 1))
    try:
        for i in range(0, len(ids), 100):
            await m.bot.delete_messages(m.chat.id, ids[i:i + 100])
    except TelegramAPIError as e:
        return await m.answer(services.explain(e))
    temp(await m.answer("🧹 Чистка завершена."), 5)


# ---------------- закрепы ----------------
@router.message(Cmd("pin", "закрепить", "закреп", plain="free"))
async def cmd_pin(m: Message, prefixed: bool, args: list):
    if await require(m, 1, prefixed) is None:
        return
    if not m.reply_to_message:
        return await m.reply("Ответь на сообщение, которое нужно закрепить.")
    silent = any(a.lower() in ("тихо", "silent", "quiet") for a in args)
    await attempt(m, m.bot.pin_chat_message(
        m.chat.id, m.reply_to_message.message_id, disable_notification=silent))


@router.message(Cmd("unpin", "открепить", "открепи", plain="free"))
async def cmd_unpin(m: Message, prefixed: bool):
    if await require(m, 1, prefixed) is None:
        return
    r = m.reply_to_message
    await attempt(m, m.bot.unpin_chat_message(m.chat.id, message_id=r.message_id if r else None))


@router.message(Cmd("unpinall", "открепитьвсё", "открепитьвсе", plain="free"))
async def cmd_unpinall(m: Message, prefixed: bool):
    if await require(m, 2, prefixed) is None:
        return
    if await attempt(m, m.bot.unpin_all_chat_messages(m.chat.id)):
        await m.answer("📌 Все закрепы сняты.")


# ---------------- ранги бота ----------------
async def _set_rank(m: Message, prefixed: bool, args: list, new_rank: int, label: str):
    p = await prep(m, 5, prefixed, args, check_target_rank=True)
    if not p:
        return
    _, t, _ = p
    if t.is_bot:
        return await m.reply("Ботов назначать нельзя.")
    await state.db.ensure(m.chat.id, t)
    await state.db.set_rank(m.chat.id, t.id, new_rank)
    await m.answer(f"{label}: {mention(t)}")


@router.message(Cmd("mod", "модер", "назначитьмода", plain="free"))
async def cmd_mod(m: Message, prefixed: bool, args: list):
    await _set_rank(m, prefixed, args, 1, "🛡 Назначен модератором")


@router.message(Cmd("smod", "стармод", "старшиймод", plain="free"))
async def cmd_smod(m: Message, prefixed: bool, args: list):
    await _set_rank(m, prefixed, args, 2, "⚔️ Назначен старшим модератором")


@router.message(Cmd("unmod", "разжаловать", "снятьмода", plain="free"))
async def cmd_unmod(m: Message, prefixed: bool, args: list):
    await _set_rank(m, prefixed, args, 0, "➖ Снят с должности")


@router.message(Cmd("mods", "модеры", "модераторы", "staff", "стафф"))
async def cmd_mods(m: Message):
    if m.chat.type not in GROUP_TYPES:
        return
    rows = await state.db.ranked(m.chat.id)
    lines = []
    try:
        admins = await m.bot.get_chat_administrators(m.chat.id)
        for a in admins:
            if a.user.is_bot:
                continue
            tag = "👑" if a.status == "creator" else "👮"
            lines.append(f"{tag} {mention(Target(a.user.id, a.user.full_name))}")
    except TelegramAPIError:
        pass
    for r in rows:
        icon = "⚔️" if r["rank"] >= 2 else "🛡"
        lines.append(f"{icon} {mention(Target(r['user_id'], r['name'] or 'Участник'))}")
    await m.answer("<b>Команда чата</b>\n" + ("\n".join(lines) if lines else "Пока никого."))
