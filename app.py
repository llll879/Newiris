import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import config
import gdb
import night
import selfedit
import state
from db import DB
from handlers import ai, dev, economy, family, moderation, protection, rp, settings, social, start, tools
from keepalive import pinger, start_web
from utils import spawn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")


async def main():
    if not config.BOT_TOKEN:
        raise SystemExit("Не задан BOT_TOKEN (получи у @BotFather)")

    if config.DATABASE_URL:
        from pgdb import PgDB  # asyncpg нужен только при работе с PostgreSQL
        state.db = PgDB(config.DATABASE_URL)
        log.info("База данных: PostgreSQL")
    else:
        state.db = DB(config.DB_PATH)
        log.info("База данных: SQLite (%s)", config.DB_PATH)
    try:
        await state.db.init()
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"Не удалось подключиться к базе данных: {e}") from e
    await gdb.init()

    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    state.bot_username = (me.username or "").lower()
    state.bot_id = me.id
    log.info("Бот @%s запущен", me.username)

    dp = Dispatcher()
    # Защита работает до хендлеров: считает активность и режет нарушения
    dp.message.outer_middleware(protection.Guard(count=True))
    dp.edited_message.outer_middleware(protection.Guard(count=False))
    dp.message.outer_middleware(tools.RecapLog())  # копит сообщения для «пересказа чата» (только в памяти)

    # Порядок важен: сначала команды, потом РП и общая обработка
    # dev — первым: режим диалога владельца должен перехватывать обычные сообщения до остальных
    for module in (dev, start, moderation, settings, social, economy, family, rp, protection, tools, ai):
        dp.include_router(module.router)

    @dp.errors()
    async def on_error(event):
        log.exception("Ошибка в обработчике: %s", event.exception)
        return True

    await start_web()
    spawn(pinger())
    spawn(state.db.flusher())
    spawn(night.loop(bot))
    spawn(family.loop(bot))
    spawn(selfedit.confirm_boot())
    spawn(selfedit.mirror_loop())
    spawn(selfedit.announce(bot, config.OWNER_IDS))

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=["message", "edited_message", "callback_query"])

