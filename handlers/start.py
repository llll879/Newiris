from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import state
from filters import Cmd

router = Router()

PAGES = {
    "main": (
        "👋 <b>Я — бот-модератор для чатов</b>\n\n"
        "Защита от спама и флуда, варны/муты/баны, капча, приветствия, правила, заметки, "
        "ранги модераторов, профили, браки, репутация — и целая библиотека РП-команд "
        "(в том числе 18+ с согласием адресата).\n\n"
        "Все команды работают и со слэшем (<code>/warn</code>), и словами (<code>варн</code>).\n"
        "Выбери раздел 👇"
    ),
    "mod": (
        "🛡 <b>Модерация</b> <i>(ответом на сообщение или с @username)</i>\n\n"
        "<code>варн [причина]</code> · <code>анварн</code> · <code>варны</code> · <code>сбросварны</code>\n"
        "<code>мут [время] [причина]</code> — время: 30м, 2ч, 1д, 1н, навсегда\n"
        "<code>анмут</code> · <code>бан [время]</code> · <code>разбан</code> · <code>кик</code>\n"
        "<code>дел</code> — удалить сообщение · <code>чистка</code> (ответом или числом)\n"
        "<code>закрепить [тихо]</code> · <code>открепить</code> · <code>открепитьвсё</code>\n\n"
        "👮 <b>Ранги</b>: <code>мод</code> (варн, мут, удаление) · <code>стармод</code> (+бан, кик) · "
        "<code>разжаловать</code> · <code>модеры</code>\n"
        "Админы Telegram получают максимальный ранг автоматически."
    ),
    "protect": (
        "🧱 <b>Защита</b> — включается в /settings\n\n"
        "• Антифлуд · антиссылки · антимат · запрет пересылок\n"
        "• Свои стоп-слова: <code>/addword</code>, <code>/delword</code>, <code>/words</code>\n"
        "• Капча для новичков · анти-боты · скрытие «вошёл/вышел»\n"
        "• Авто-варны за нарушения, наказание при лимите: мут / бан / кик\n"
        "• 🌙 Ночной режим: чат закрывается по расписанию\n"
        "• Модераторы и админы защитой не затрагиваются\n\n"
        "Числа: <code>/set</code> · приветствие: <code>/setwelcome</code> · правила: <code>/setrules</code>"
    ),
    "rp": (
        "🎭 <b>РП-команды</b>\n\n"
        "Ответь на сообщение человека и напиши: <code>обнять</code>, <code>поцеловать</code>, "
        "<code>дать пять</code>, <code>кинуть тапком</code> и другие. Список: <code>/rp</code>\n"
        "Можно добавить слова: <i>обнять крепко-крепко</i>.\n\n"
        "<b>Свои команды</b> (админ): <code>/addrp команда | шаблон с {a} и {b}</code>, "
        "<code>/delrp команда</code>."
    ),
    "adult": (
        "🔞 <b>18+ РП</b>\n\n"
        "По умолчанию выключены. Админ включает: <code>/rp18 on</code> (или в /settings) — "
        "только для чатов, где все участники совершеннолетние.\n"
        "Адресат должен <b>согласиться кнопкой</b> — без этого действие не выполняется. "
        "Отказаться можно в любой момент.\n"
        "Свои 18+ команды: <code>/addrp 18 команда | шаблон</code>."
    ),
    "social": (
        "👤 <b>Профили и общение</b>\n\n"
        "<code>профиль</code> · <code>топ</code> · <code>топрепы</code> · <code>ник Имя</code>\n"
        "Репутация: ответь «+» / «спасибо» (или «-») на сообщение\n"
        "💍 <code>брак</code> (ответом) · <code>развод</code> · <code>браки</code>\n"
        "📒 Заметки: <code>/save имя текст</code>, вызов — <code>#имя</code>, список — <code>/notes</code>\n"
        "📜 <code>/rules</code> · 🚨 <code>репорт</code> — позвать админов · <code>/id</code> · <code>/ping</code>\n"
        "🎲 <code>/roll</code> · <code>/choose чай | кофе</code> · <code>/ball вопрос</code>"
    ),
}
BUTTONS = [("🛡 Модерация", "mod"), ("🧱 Защита", "protect"), ("🎭 РП", "rp"),
           ("🔞 18+", "adult"), ("👤 Профили", "social")]


def kb(page: str) -> InlineKeyboardMarkup:
    if page == "main":
        rows = [[InlineKeyboardButton(text=t, callback_data=f"help:{k}") for t, k in BUTTONS[i:i + 2]]
                for i in range(0, len(BUTTONS), 2)]
    else:
        rows = [[InlineKeyboardButton(text="⬅️ Назад", callback_data="help:main")]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Cmd("start", "help", "помощь", "команды", "хелп", "справка"))
async def cmd_help(m: Message, cmd: str):
    markup = kb("main")
    if m.chat.type == "private" and state.bot_username:
        link = (f"https://t.me/{state.bot_username}?startgroup=true"
                "&admin=delete_messages+restrict_members+pin_messages+invite_users")
        markup.inline_keyboard.append([InlineKeyboardButton(text="➕ Добавить в чат", url=link)])
    await m.answer(PAGES["main"], reply_markup=markup)


@router.callback_query(F.data.startswith("help:"))
async def cb_help(cb: CallbackQuery):
    page = cb.data.split(":")[1]
    if page not in PAGES:
        return await cb.answer()
    markup = kb(page)
    if page == "main" and cb.message.chat.type == "private" and state.bot_username:
        link = (f"https://t.me/{state.bot_username}?startgroup=true"
                "&admin=delete_messages+restrict_members+pin_messages+invite_users")
        markup.inline_keyboard.append([InlineKeyboardButton(text="➕ Добавить в чат", url=link)])
    try:
        await cb.message.edit_text(PAGES[page], reply_markup=markup)
    except TelegramAPIError:
        pass
    await cb.answer()
