"""Режим разработчика: владелец (OWNER_IDS) в личке просит ИИ изменить себя.

/dev <просьба>   — ИИ читает код и готовит правки (или меняет настройки на лету)
/dev on | off    — режим диалога: все обычные сообщения в личке идут в режим разработчика
/diff            — показать накопленные правки      /history — применённые правки
/rollback [id]   — откатить правку                   /restart — перезапуск
/prompt          — текущие настройки ИИ              /prompt undo — отменить последнее изменение
"""
import logging
import time

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

import config
import devagent
import gdb
import gemini
import selfedit
from filters import Cmd
from selfedit import SelfEditError
from utils import esc

log = logging.getLogger("dev")
router = Router()

DEV_MODE: set[int] = set()   # владельцы, у которых включён режим диалога
BUSY: set[int] = set()       # чтобы не запускать два агента одновременно


def is_owner(m: Message) -> bool:
    return bool(m.from_user and m.from_user.id in config.OWNER_IDS)


async def owner_private(m: Message) -> bool:
    """Проверка доступа. Всем, кроме владельца в личке, молча (в группах) или кратко отказываем."""
    if not is_owner(m):
        if m.chat.type == "private":
            await m.answer("Эта команда только для владельца бота.")
        return False
    if m.chat.type != "private":
        await m.reply("Правки кода — только в личке со мной, чтобы ничего не утекло в чат.")
        return False
    return True


class DevMode(BaseFilter):
    async def __call__(self, m: Message) -> bool:
        return bool(m.text and not m.text.startswith(("/", "!")) and m.chat.type == "private"
                    and m.from_user and m.from_user.id in DEV_MODE and is_owner(m))


def confirm_kb(sid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Применить", callback_data=f"dev:ok:{sid}"),
         InlineKeyboardButton(text="🔄 Применить и перезапустить", callback_data=f"dev:okr:{sid}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"dev:no:{sid}")]])


def stage_summary(stage) -> str:
    rows = [f"• <code>{esc(r)}</code> +{p} −{mi}{' (новый)' if new else ''}"
            for r, p, mi, new in selfedit.diff_stats(stage)]
    return "\n".join(rows)


async def present_stage(m: Message, uid: int):
    """Показывает накопленные правки и просит подтверждение (или применяет сразу в авто-режиме)."""
    stage = selfedit.get_stage(uid)
    if not stage.files:
        return
    if selfedit.AUTO_APPLY:
        summary = stage_summary(stage)
        bid = selfedit.apply_stage(stage)
        selfedit.drop_stage(uid)
        await m.answer(f"⚡ Авто-режим: правка <code>{bid}</code> применена.\n{summary}\n"
                       f"Перезапусти: /restart. Откат: /rollback", reply_markup=InlineKeyboardMarkup(
                           inline_keyboard=[[InlineKeyboardButton(text="🔄 Перезапустить", callback_data="dev:rs")]]))
        return
    diff = selfedit.diff_text(stage)
    await m.answer_document(BufferedInputFile(diff.encode("utf-8"), filename=f"changes-{stage.id}.diff"),
                            caption=f"🧾 <b>Готовы правки</b> ({len(stage.files)} файл.)\n{stage_summary(stage)}\n\n"
                                    f"Применить? Код заработает после перезапуска.",
                            reply_markup=confirm_kb(stage.id))


async def run_request(m: Message, text: str):
    uid = m.from_user.id
    if uid in BUSY:
        return await m.reply("Я ещё работаю над предыдущей просьбой, подожди секунду.")
    if not config.GEMINI_API_KEY:
        return await m.reply("Нет GEMINI_API_KEY — без него я не могу думать.")
    BUSY.add(uid)
    status = await m.answer("🛠 Принято, смотрю…")
    last = [0.0]

    async def on_step(line: str):
        if time.monotonic() - last[0] < 1.5:
            return
        last[0] = time.monotonic()
        try:
            await status.edit_text(f"🛠 {line}")
            await m.bot.send_chat_action(m.chat.id, "typing")
        except Exception:  # noqa: BLE001
            pass

    try:
        answer = await devagent.run(uid, text, config.AI_NAME, on_step)
    except gemini.GeminiError as e:
        answer = {"rate": "⏳ Слишком много запросов к Gemini, попробуй чуть позже."}.get(
            str(e), "⚠️ Gemini сейчас недоступен, попробуй позже.")
    except Exception as e:  # noqa: BLE001
        log.exception("dev request failed")
        answer = f"⚠️ Что-то сломалось внутри: {e}"
    finally:
        BUSY.discard(uid)
    try:
        await status.delete()
    except Exception:  # noqa: BLE001
        pass
    await m.answer(esc(answer[:3500]))
    await present_stage(m, uid)


HELP = (
    "🛠 <b>Режим разработчика</b>\n"
    "Просто опиши, что поменять в боте:\n"
    "<code>/dev стань строже и отвечай короче</code>\n"
    "<code>/dev добавь команду /кубик, которая кидает d20</code>\n\n"
    "• Характер, имя, модель, температура меняются сразу.\n"
    "• Код сначала показывается как diff — ты подтверждаешь, потом перезапуск.\n"
    "• Если после правки бот не запустится, он сам откатит её.\n\n"
    "<code>/dev on</code> / <code>/dev off</code> — диалог без префикса\n"
    "<code>/dev clear</code> — забыть этот разговор\n"
    "<code>/diff</code> · <code>/history</code> · <code>/rollback</code> · <code>/restart</code>\n"
    "<code>/prompt</code> — настройки ИИ · <code>/prompt undo</code> — отменить\n"
    "<code>/devreset</code> — выбросить ВСЕ правки (вернуть код из репозитория)"
)


@router.message(Cmd("dev", plain="none"))
async def cmd_dev(m: Message, arg: str):
    if not await owner_private(m):
        return
    low = arg.lower().strip()
    uid = m.from_user.id
    if not arg:
        mode = "включён" if uid in DEV_MODE else "выключен"
        return await m.answer(HELP + f"\n\nРежим диалога: <b>{mode}</b>.")
    if low in ("on", "вкл"):
        DEV_MODE.add(uid)
        return await m.answer("🛠 Режим диалога включён: пиши просьбы обычными сообщениями. Выключить: /dev off")
    if low in ("off", "выкл"):
        DEV_MODE.discard(uid)
        return await m.answer("Вернулась в обычный режим общения.")
    if low in ("clear", "сброс"):
        await gdb.hist_clear(f"dev:{uid}")
        return await m.answer("🧹 Разговор в режиме разработчика забыт.")
    await run_request(m, arg)


@router.message(DevMode())
async def dev_chat(m: Message):
    await run_request(m, m.text.strip())


@router.message(Cmd("diff", plain="none"))
async def cmd_diff(m: Message):
    if not await owner_private(m):
        return
    stage = selfedit.get_stage(m.from_user.id)
    if not stage.files:
        return await m.answer("Накопленных правок нет.")
    await present_stage(m, m.from_user.id)


@router.message(Cmd("history", "devlog", plain="none"))
async def cmd_history(m: Message):
    if not await owner_private(m):
        return
    items = selfedit.history(10)
    if not items:
        return await m.answer("Применённых правок пока нет.")
    lines = []
    for h in items:
        when = time.strftime("%d.%m %H:%M", time.localtime(h["ts"]))
        mark = "↩️ откачена" if h["rolled_back"] else "✅"
        lines.append(f"{mark} <code>{h['id']}</code> · {when}\n   {esc(', '.join(h['files']))}")
    await m.answer("🗂 <b>Применённые правки</b>\n" + "\n".join(lines) +
                   "\n\nОткат: <code>/rollback</code> (последняя) или <code>/rollback id</code>")


@router.message(Cmd("rollback", "откат", plain="none"))
async def cmd_rollback(m: Message, arg: str):
    if not await owner_private(m):
        return
    try:
        bid, files = selfedit.rollback(arg.strip() or None)
    except SelfEditError as e:
        return await m.answer(f"❌ {esc(str(e))}")
    await m.answer(f"↩️ Правка <code>{bid}</code> откачена: {esc(', '.join(files))}.\nПерезапусти, чтобы код заработал: /restart",
                   reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                       [InlineKeyboardButton(text="🔄 Перезапустить", callback_data="dev:rs")]]))


@router.message(Cmd("restart", "перезапуск", plain="none"))
async def cmd_restart(m: Message):
    if not await owner_private(m):
        return
    await m.answer("🔄 Перезапускаюсь… вернусь через несколько секунд.")
    await selfedit.restart()


@router.message(Cmd("devreset", plain="none"))
async def cmd_devreset(m: Message, arg: str):
    if not await owner_private(m):
        return
    if arg.strip().lower() != "да":
        return await m.answer("Это выбросит ВСЕ мои правки кода и вернёт код из репозитория. "
                              "Подтверди: <code>/devreset да</code>")
    n = selfedit.reset_all()
    await m.answer(f"♻️ Правок откачено: {n}. Перезапусти: /restart")


@router.message(Cmd("prompt", "промт", plain="none"))
async def cmd_prompt(m: Message, arg: str):
    if not await owner_private(m):
        return
    if arg.strip().lower() in ("undo", "отмена"):
        try:
            key = selfedit.undo_setting()
        except SelfEditError as e:
            return await m.answer(f"❌ {esc(str(e))}")
        return await m.answer(f"↩️ Отменено последнее изменение: <b>{key}</b>.")
    snap = selfedit.settings_snapshot()
    if not snap:
        return await m.answer("Переопределённых настроек нет — работают значения из кода и переменных окружения.\n"
                              "Попроси меня: <code>/dev сделай характер мягче</code>")
    rows = [f"<b>{k}</b>: {esc(str(v))[:600]}" for k, v in snap.items()]
    await m.answer("⚙️ <b>Настройки ИИ</b>\n\n" + "\n\n".join(rows))


@router.callback_query(F.data.startswith("dev:"))
async def cb_dev(cb: CallbackQuery):
    if cb.from_user.id not in config.OWNER_IDS:
        return await cb.answer("Только для владельца.", show_alert=True)
    parts = cb.data.split(":")
    action = parts[1]
    uid = cb.from_user.id
    if action == "rs":
        await cb.answer("Перезапускаюсь…")
        await cb.message.answer("🔄 Перезапускаюсь…")
        return await selfedit.restart()
    stage = selfedit.get_stage(uid)
    if len(parts) < 3 or parts[2] != stage.id or not stage.files:
        return await cb.answer("Эти правки уже неактуальны.", show_alert=True)
    if action == "no":
        selfedit.drop_stage(uid)
        await cb.message.edit_caption(caption="❌ Правки отклонены.")
        return await cb.answer()
    try:
        bid = selfedit.apply_stage(stage)
    except SelfEditError as e:
        return await cb.answer(f"Не применилось: {e}"[:190], show_alert=True)
    selfedit.drop_stage(uid)
    if action == "okr":
        await cb.message.edit_caption(caption=f"✅ Правка <code>{bid}</code> применена. Перезапускаюсь…")
        await cb.answer()
        return await selfedit.restart()
    await cb.message.edit_caption(
        caption=f"✅ Правка <code>{bid}</code> применена. Код заработает после перезапуска (/restart). "
                f"Откат: /rollback",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Перезапустить", callback_data="dev:rs")]]))
    await cb.answer("Применено")
