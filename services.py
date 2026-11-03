"""Операции модерации, общие для команд и автозащиты."""
from datetime import datetime, timedelta, timezone

from aiogram.exceptions import TelegramAPIError
from aiogram.types import ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup

import state
from utils import Target, esc, fmt_dur, mention


def full_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=True,
        can_send_audios=True,
        can_send_documents=True,
        can_send_photos=True,
        can_send_videos=True,
        can_send_video_notes=True,
        can_send_voice_notes=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_invite_users=True,
    )


async def chat_default_perms(bot, chat_id: int) -> ChatPermissions:
    try:
        chat = await bot.get_chat(chat_id)
        if chat.permissions:
            return chat.permissions
    except TelegramAPIError:
        pass
    return full_permissions()


def _until(seconds: int | None):
    if not seconds:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


async def mute(bot, chat_id: int, user_id: int, seconds: int | None):
    await bot.restrict_chat_member(
        chat_id, user_id, permissions=ChatPermissions(can_send_messages=False),
        until_date=_until(seconds),
    )


async def unmute(bot, chat_id: int, user_id: int):
    perms = await chat_default_perms(bot, chat_id)
    await bot.restrict_chat_member(chat_id, user_id, permissions=perms)


async def ban(bot, chat_id: int, user_id: int, seconds: int | None = None):
    await bot.ban_chat_member(chat_id, user_id, until_date=_until(seconds))


async def unban(bot, chat_id: int, user_id: int):
    await bot.unban_chat_member(chat_id, user_id, only_if_banned=True)


async def kick(bot, chat_id: int, user_id: int):
    await bot.ban_chat_member(chat_id, user_id)
    await bot.unban_chat_member(chat_id, user_id, only_if_banned=True)


def explain(e: TelegramAPIError) -> str:
    msg = str(getattr(e, "message", e)).lower()
    if "not enough rights" in msg or "admin_required" in msg or "need to be admin" in msg:
        return ("❗ У меня не хватает прав. Сделайте меня администратором с правами: "
                "удаление сообщений, блокировка участников, закрепление.")
    if "administrator" in msg or "owner" in msg:
        return "⛔ Нельзя применить это к администратору."
    if "user not found" in msg or "participant_id_invalid" in msg:
        return "❓ Не нашёл такого пользователя в чате."
    if "message to delete not found" in msg:
        return "❓ Сообщение уже удалено."
    return f"❗ Ошибка Telegram: {esc(str(getattr(e, 'message', e)))}"


async def apply_warn(bot, chat_id: int, target: Target, by_id: int, reason: str, by: str = ""):
    """Выдаёт варн, при лимите — наказывает. Возвращает (текст, клавиатура)."""
    db = state.db
    s = await db.settings(chat_id)
    await db.ensure(chat_id, target)
    n = await db.add_warn(chat_id, target.id, by_id, reason)
    limit = s["warn_limit"]
    text = f"⚠️ {mention(target)} получает предупреждение ({n}/{limit})."
    if by:
        text += f"\n👮 Выдал: {by}"
    if reason:
        text += f"\n📝 Причина: {esc(reason)}"
    markup = None
    if n >= limit:
        action = s["warn_action"]
        try:
            if action == "ban":
                await ban(bot, chat_id, target.id)
                text += "\n🔨 Лимит предупреждений — бан."
            elif action == "kick":
                await kick(bot, chat_id, target.id)
                text += "\n👢 Лимит предупреждений — исключён из чата."
            else:
                secs = s["warn_mute"] * 60
                await mute(bot, chat_id, target.id, secs)
                text += f"\n🔇 Лимит предупреждений — мут на {fmt_dur(secs)}."
            await db.set_warns(chat_id, target.id, 0)
        except TelegramAPIError as e:
            text += "\n" + explain(e)
    else:
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="↩️ Снять варн", callback_data=f"unw:{target.id}")
        ]])
    return text, markup
