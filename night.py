"""Ночной режим: закрывает чат по расписанию и открывает утром."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram.exceptions import TelegramAPIError
from aiogram.types import ChatPermissions

import state
from config import TZ_OFFSET
from services import full_permissions

log = logging.getLogger("night")


def _mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def is_night(now_min: int, start: str, end: str) -> bool:
    a, b = _mins(start), _mins(end)
    if a == b:
        return False
    return a <= now_min < b if a < b else (now_min >= a or now_min < b)


async def tick(bot):
    now = datetime.now(timezone.utc) + timedelta(hours=TZ_OFFSET)
    cur = now.hour * 60 + now.minute
    db = state.db
    for chat_id in await db.night_chats():
        s = await db.settings(chat_id)
        night = bool(s["night"]) and is_night(cur, s["night_start"], s["night_end"])
        if night == bool(s.get("night_state")):
            continue
        try:
            if night:
                chat = await bot.get_chat(chat_id)
                saved = chat.permissions.model_dump(exclude_none=True) if chat.permissions else {}
                await db.set(chat_id, "night_saved", saved)
                await bot.set_chat_permissions(chat_id, ChatPermissions(can_send_messages=False))
                await bot.send_message(
                    chat_id, f"🌙 Ночной режим: чат закрыт до {s['night_end']}. Спокойной ночи!")
            else:
                saved = s.get("night_saved") or {}
                perms = ChatPermissions(**saved) if saved else full_permissions()
                await bot.set_chat_permissions(chat_id, perms)
                await bot.send_message(chat_id, "☀️ Доброе утро! Чат снова открыт.")
        except TelegramAPIError as e:
            log.warning("night mode in %s failed: %s", chat_id, e)
        await db.set(chat_id, "night_state", night)


async def loop(bot):
    while True:
        await asyncio.sleep(30)
        try:
            await tick(bot)
        except Exception:  # noqa: BLE001
            log.exception("night tick failed")
