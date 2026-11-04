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
        "ранги модераторов, профили, браки, репутация, экономика с казино, дома и машины, "
        "семья с детьми, ИИ-собеседник на Gemini и большая библиотека РП-команд "
        "(18+ — только с согласием адресата).\n\n"
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
    "money": (
        "💰 <b>Экономика и игры</b>\n\n"
        "<code>баланс</code> · <code>ежедневка</code> · <code>работать</code> (раз в 30 мин) · "
        "<code>работы</code> · <code>рыбалка</code> · <code>шахта</code> · <code>таксовать</code>\n"
        "<code>перевести @ник 500</code> · <code>украсть</code> (рискованно!) · <code>богачи</code>\n\n"
        "🎰 <b>Казино</b> (ставка числом, «5к» или «все»):\n"
        "<code>слоты 100</code> · <code>кости 100</code> · <code>орёл 100</code> / <code>решка 100</code> · "
        "<code>рулетка 100 красное</code> (или чёрное, чёт, нечет, число 17) · <code>блэкджек 100</code>\n\n"
        "🛍 <b>Магазин</b>: <code>магазин</code> · <code>купить дом 3</code> · <code>купить машину 2</code> · "
        "<code>купить питомца 4</code> · <code>продать дом</code> · <code>имущество</code> · "
        "<code>собрать доход</code> (аренда с дома)"
    ),
    "family": (
        "👪 <b>Отношения и семья</b> <i>(всё — только с согласия второго человека)</i>\n\n"
        "💘 <code>встречаться</code> · <code>расстаться</code> · <code>отношения</code> · <code>свидание</code>\n"
        "💍 <code>брак</code> · <code>развод</code> · <code>браки</code> (свадебный подарок!)\n"
        "🤝 <code>дружить</code> · <code>друзья</code> · <code>раздружиться</code>\n"
        "👶 <code>завести ребёнка</code> (пара + согласие) · <code>беременность</code> · "
        "<code>дети</code> · <code>назвать ребёнка Имя</code> · <code>усыновить</code> · <code>пособие</code>\n"
        "👪 <code>семья</code> — общий бюджет, дом, машина, дети"
    ),
    "ai": (
        "🧠 <b>ИИ-собеседник</b> (Gemini)\n\n"
        "В личке — просто пиши. В группе — начни с «Мия, …» / «ии …» / «бот …» или ответь на её сообщение.\n"
        "<code>ии роль описание характера</code> — свой персонаж (ролёвки!) · <code>ии роль сброс</code>\n"
        "<code>ии имя Имя</code> · <code>ии сброс</code> — забыть разговор · <code>ии статус</code>\n"
        "💞 Отношения с ИИ растут от общения и подарков (<code>ии цветы</code>, <code>ии подарок</code>): "
        "знакомые → друзья → пара → брак. Предложи словами: «давай встречаться», «выходи за меня».\n"
        "🔞 <code>ии 18</code> — взрослый режим (флирт и романтика между взрослыми; откровенное — за кадром)."
    ),
    "web": (
        "🌐 <b>Интернет и фишки</b> <i>(выключатель: /settings → Интернет)</i>\n\n"
        "🔎 <code>/search запрос</code> — поиск в Google с источниками · <code>новости [тема]</code>\n"
        "💬 «Мия, найди …» / «Мия, что нового в …» — ИИ сама сходит в интернет и ответит в своём стиле\n"
        "🌤 <code>погода Москва</code> · 💱 <code>курс</code>, <code>курс usd</code>, <code>курс 100 eur</code> (ЦБ РФ)\n"
        "📰 <code>перескажи https://…</code> — суть статьи (или ответом на сообщение со ссылкой)\n"
        "🕵️ <code>проверь</code> ответом на сообщение — проверка факта по источникам\n"
        "🌍 <code>переведи</code> / <code>переведи на английский</code> (ответом на сообщение)\n"
        "🎙 <code>расшифруй</code> ответом на голосовое\n"
        "👁 Пришли фото с подписью «Мия, что тут?» (в личке — просто фото) или ответь так на чужое фото\n"
        "📝 <code>пересказ</code> / «что я пропустил» — сводка чата. Включает админ в /settings: бот тогда "
        "хранит последние сообщения <b>только в памяти</b>, на диск и в базу они не попадают."
    ),
    "adult": (
        "🔞 <b>18+ РП</b>\n\n"
        "По умолчанию выключены. Админ включает: <code>/rp18 on</code> (или в /settings) — "
        "только для чатов, где все участники совершеннолетние.\n"
        "Адресат должен <b>согласиться кнопкой</b> — без этого действие не выполняется. "
        "Отказаться можно в любой момент. Принуждения и «не по согласию» в боте нет и не будет.\n"
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
           ("🔞 18+", "adult"), ("👤 Профили", "social"), ("💰 Экономика", "money"),
           ("👪 Семья", "family"), ("🧠 ИИ", "ai"),
           ("🌐 Интернет", "web")]


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
