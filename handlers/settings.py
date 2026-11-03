import re

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import state
from filters import Cmd
from utils import GROUP_TYPES, esc, rank_of, require

router = Router()

TOGGLES = [
    ("welcome", "👋 Приветствие"),
    ("captcha", "🤖 Капча для новичков"),
    ("antiflood", "🌊 Антифлуд"),
    ("antilink", "🔗 Антиссылки"),
    ("antimat", "🤬 Антимат"),
    ("badwords", "🚫 Свои стоп-слова"),
    ("antiforward", "↪️ Запрет пересылок"),
    ("antibot", "🛡 Анти-боты"),
    ("autowarn", "⚠️ Авто-варн за нарушения"),
    ("del_service", "🧹 Скрывать «вошёл/вышел»"),
    ("rp", "🎭 РП-команды"),
    ("rp18", "🔞 18+ РП-команды"),
    ("night", "🌙 Ночной режим"),
]
ACTIONS = ["mute", "ban", "kick"]
ACTION_LABEL = {"mute": "мут", "ban": "бан", "kick": "кик"}

NUM_KEYS = {
    "flood_limit": (2, 30, "сообщений для срабатывания антифлуда"),
    "flood_window": (2, 60, "окно антифлуда, сек"),
    "flood_mute": (1, 10080, "мут за флуд, мин"),
    "warn_limit": (1, 20, "лимит варнов"),
    "warn_mute": (1, 525600, "мут при лимите варнов, мин"),
    "captcha_sec": (30, 900, "время на капчу, сек"),
}
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


async def panel(chat_id: int):
    s = await state.db.settings(chat_id)
    rows, row = [], []
    for key, label in TOGGLES:
        row.append(InlineKeyboardButton(
            text=f"{'✅' if s[key] else '❌'} {label}", callback_data=f"tg:{key}"))
        if len(row) == 1:
            rows.append(row)
            row = []
    rows.append([
        InlineKeyboardButton(text="➖", callback_data="wl:-1"),
        InlineKeyboardButton(text=f"Лимит варнов: {s['warn_limit']}", callback_data="noop"),
        InlineKeyboardButton(text="➕", callback_data="wl:1"),
    ])
    rows.append([InlineKeyboardButton(
        text=f"Наказание за лимит варнов: {ACTION_LABEL[s['warn_action']]} 🔄", callback_data="wa")])
    rows.append([InlineKeyboardButton(text="✖️ Закрыть", callback_data="close")])
    text = ("⚙️ <b>Настройки чата</b>\n"
            "Нажимай на кнопки, чтобы включать и выключать функции.\n"
            "Числа и тексты: /set, /setwelcome, /setrules, /addword")
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Cmd("settings", "настройки", "панель"))
async def cmd_settings(m: Message, prefixed: bool):
    if await require(m, 5, prefixed) is None:
        return
    text, kb = await panel(m.chat.id)
    await m.answer(text, reply_markup=kb)


async def _admin_cb(cb: CallbackQuery) -> bool:
    if cb.message.chat.type not in GROUP_TYPES:
        await cb.answer()
        return False
    if await rank_of(cb.bot, cb.message.chat.id, cb.from_user.id) < 5:
        await cb.answer("Только для администраторов", show_alert=True)
        return False
    return True


async def _refresh(cb: CallbackQuery):
    text, kb = await panel(cb.message.chat.id)
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except TelegramAPIError:
        pass


@router.callback_query(F.data.startswith("tg:"))
async def cb_toggle(cb: CallbackQuery):
    if not await _admin_cb(cb):
        return
    key = cb.data.split(":")[1]
    if key not in dict(TOGGLES):
        return await cb.answer()
    chat_id = cb.message.chat.id
    s = await state.db.settings(chat_id)
    new = not s[key]
    await state.db.set(chat_id, key, new)
    if key == "rp18" and new:
        await cb.answer("🔞 Включайте только если все участники чата совершеннолетние. "
                        "Команды работают только с согласия адресата.", show_alert=True)
    else:
        await cb.answer("Включено" if new else "Выключено")
    await _refresh(cb)


@router.callback_query(F.data.startswith("wl:"))
async def cb_warn_limit(cb: CallbackQuery):
    if not await _admin_cb(cb):
        return
    chat_id = cb.message.chat.id
    s = await state.db.settings(chat_id)
    new = max(1, min(20, s["warn_limit"] + int(cb.data.split(":")[1])))
    await state.db.set(chat_id, "warn_limit", new)
    await cb.answer()
    await _refresh(cb)


@router.callback_query(F.data == "wa")
async def cb_warn_action(cb: CallbackQuery):
    if not await _admin_cb(cb):
        return
    chat_id = cb.message.chat.id
    s = await state.db.settings(chat_id)
    nxt = ACTIONS[(ACTIONS.index(s["warn_action"]) + 1) % len(ACTIONS)]
    await state.db.set(chat_id, "warn_action", nxt)
    await cb.answer()
    await _refresh(cb)


@router.callback_query(F.data == "close")
async def cb_close(cb: CallbackQuery):
    if await rank_of(cb.bot, cb.message.chat.id, cb.from_user.id) >= 5:
        try:
            await cb.message.delete()
        except TelegramAPIError:
            pass
    await cb.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery):
    await cb.answer()


# ---------- числовые параметры ----------
@router.message(Cmd("set", "установить", plain="none"))
async def cmd_set(m: Message, prefixed: bool, args: list):
    if await require(m, 5, prefixed) is None:
        return
    s = await state.db.settings(m.chat.id)
    if len(args) < 2:
        lines = [f"<code>{k}</code> = {s[k]} — {d}" for k, (_, _, d) in NUM_KEYS.items()]
        lines.append(f"<code>night_start</code> = {s['night_start']} — начало ночного режима (ЧЧ:ММ)")
        lines.append(f"<code>night_end</code> = {s['night_end']} — конец ночного режима (ЧЧ:ММ)")
        lines.append(f"<code>warn_action</code> = {s['warn_action']} — mute / ban / kick")
        return await m.answer("<b>Параметры</b>\nФормат: <code>/set ключ значение</code>\n\n" + "\n".join(lines))
    key, val = args[0].lower(), args[1].lower()
    if key in NUM_KEYS:
        lo, hi, _ = NUM_KEYS[key]
        if not val.isdigit() or not lo <= int(val) <= hi:
            return await m.reply(f"Значение должно быть числом от {lo} до {hi}.")
        await state.db.set(m.chat.id, key, int(val))
    elif key in ("night_start", "night_end"):
        mt = TIME_RE.match(val)
        if not mt:
            return await m.reply("Время в формате ЧЧ:ММ, например 23:00.")
        await state.db.set(m.chat.id, key, f"{int(mt.group(1)):02d}:{mt.group(2)}")
    elif key == "warn_action":
        if val not in ACTIONS:
            return await m.reply("Допустимо: mute, ban, kick.")
        await state.db.set(m.chat.id, key, val)
    else:
        return await m.reply("Неизвестный параметр. Список: /set")
    await m.answer(f"✅ <code>{esc(key)}</code> = {esc(val)}")


# ---------- приветствие / правила ----------
@router.message(Cmd("setwelcome", "приветствие", plain="none"))
async def cmd_setwelcome(m: Message, prefixed: bool, arg: str):
    if await require(m, 5, prefixed) is None:
        return
    if not arg:
        s = await state.db.settings(m.chat.id)
        return await m.answer(
            "Использование: <code>/setwelcome текст</code>\n"
            "Подстановки: {name} — имя, {chat} — название чата, {id}.\n\n"
            f"<b>Сейчас:</b>\n{esc(s['welcome_text'])}")
    await state.db.set(m.chat.id, "welcome_text", arg[:1000])
    await m.answer("✅ Приветствие сохранено.")


@router.message(Cmd("setrules", "установитьправила", plain="none"))
async def cmd_setrules(m: Message, prefixed: bool, arg: str):
    if await require(m, 5, prefixed) is None:
        return
    text = arg or (m.reply_to_message.text if m.reply_to_message and m.reply_to_message.text else "")
    if not text:
        return await m.reply("Использование: <code>/setrules текст правил</code>")
    await state.db.set(m.chat.id, "rules", text[:3000])
    await m.answer("✅ Правила сохранены. Показать: /rules")


@router.message(Cmd("rules", "правила"))
async def cmd_rules(m: Message):
    if m.chat.type not in GROUP_TYPES:
        return
    s = await state.db.settings(m.chat.id)
    if not s["rules"]:
        return await m.answer("📜 Правила пока не заданы. Админ может задать: /setrules")
    await m.answer("📜 <b>Правила чата</b>\n\n" + esc(s["rules"]))


# ---------- стоп-слова ----------
@router.message(Cmd("addword", "добавитьслово", "стопслово", plain="free"))
async def cmd_addword(m: Message, prefixed: bool, arg: str):
    if await require(m, 1, prefixed) is None:
        return
    word = arg.lower().replace("ё", "е").strip()
    if not 2 <= len(word) <= 40:
        return await m.reply("Использование: <code>/addword слово</code> (от 2 до 40 символов).")
    await state.db.word_add(m.chat.id, word)
    await m.answer(f"🚫 Стоп-слово добавлено: <code>{esc(word)}</code>")


@router.message(Cmd("delword", "удалитьслово", plain="free"))
async def cmd_delword(m: Message, prefixed: bool, arg: str):
    if await require(m, 1, prefixed) is None:
        return
    ok = await state.db.word_del(m.chat.id, arg.lower().replace("ё", "е").strip())
    await m.answer("✅ Удалено." if ok else "Такого слова нет в списке.")


@router.message(Cmd("words", "стопслова"))
async def cmd_words(m: Message, prefixed: bool):
    if await require(m, 1, prefixed) is None:
        return
    words = sorted(await state.db.words(m.chat.id))
    await m.answer("🚫 <b>Стоп-слова</b>\n" + (", ".join(f"<code>{esc(w)}</code>" for w in words) or "Список пуст."))


# ---------- заметки ----------
@router.message(Cmd("save", "сохранить", "заметка", plain="free"))
async def cmd_save(m: Message, prefixed: bool, args: list):
    if await require(m, 1, prefixed) is None:
        return
    if not args:
        return await m.reply("Использование: <code>/save название текст</code> (или ответом на сообщение).")
    name = args[0].lower().lstrip("#")[:32]
    text = " ".join(args[1:])
    if not text and m.reply_to_message:
        text = m.reply_to_message.text or m.reply_to_message.caption or ""
    if not text or not re.fullmatch(r"\w+", name):
        return await m.reply("Нужны название (буквы/цифры) и текст.")
    await state.db.note_set(m.chat.id, name, text[:3000])
    await m.answer(f"💾 Заметка сохранена. Вызов: <code>#{esc(name)}</code>")


@router.message(Cmd("get", "заметка_показать", "показать"))
async def cmd_get(m: Message, args: list):
    if m.chat.type not in GROUP_TYPES or not args:
        return
    text = await state.db.note_get(m.chat.id, args[0].lower().lstrip("#"))
    await m.answer(esc(text) if text else "Заметка не найдена.")


@router.message(Cmd("delnote", "удалитьзаметку", plain="free"))
async def cmd_delnote(m: Message, prefixed: bool, args: list):
    if await require(m, 1, prefixed) is None:
        return
    if not args:
        return await m.reply("Использование: <code>/delnote название</code>")
    ok = await state.db.note_del(m.chat.id, args[0].lower().lstrip("#"))
    await m.answer("🗑 Заметка удалена." if ok else "Такой заметки нет.")


@router.message(Cmd("notes", "заметки"))
async def cmd_notes(m: Message):
    if m.chat.type not in GROUP_TYPES:
        return
    names = await state.db.notes(m.chat.id)
    await m.answer("📒 <b>Заметки чата</b>\n" + (" ".join(f"#{esc(n)}" for n in names) or "Пока нет заметок."))


@router.message(F.text.regexp(r"^#\w{1,32}\b"), F.chat.type.in_({"group", "supergroup"}))
async def on_hashtag(m: Message):
    name = m.text.split()[0][1:].lower()
    text = await state.db.note_get(m.chat.id, name)
    if text:
        await m.answer(esc(text))
