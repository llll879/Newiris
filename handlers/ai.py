"""ИИ-собеседник на Gemini: болтовня, ролевые игры, отношения с ИИ."""
import re
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
import gdb
import gemini
import state
from handlers.economy import fm
from utils import esc

router = Router()

DEFAULT_PERSONA = (
    "очень добрая, тёплая и внимательная собеседница. Ты как близкая подруга, с которой спокойно и уютно: "
    "искренне интересуешься, как у человека дела, замечаешь настроение, поддерживаешь и радуешься его успехам. "
    "У тебя мягкий юмор, ты можешь по-доброму подшутить, но никогда не злишься, не давишь и не осуждаешь. "
    "Помнишь, что человек рассказывал раньше, и возвращаешься к этому сама, как это делают живые люди"
)

EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2190-\u21FF\u2300-\u23FF\u2460-\u24FF\u2500-\u27BF\u2900-\u29FF"
    "\u2B00-\u2BFF\u3030\u303D\u3297\u3299\uFE0E\uFE0F\u200D\u20E3]"
)


def strip_emoji(text: str) -> str:
    """Гарантированно убирает смайлики и эмодзи, даже если модель их вставила."""
    text = EMOJI_RE.sub("", text)
    text = re.sub(r"\s*(?:[:;=]-?[)(]+|\^[_.-]?\^|[xX][Dd]|\){2,}|\({2,})(?=\s|$|[.,!?])", "", text)
    text = re.sub(r"[ \t]+([,.!?;:])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# стадия: (нужно очков симпатии, подпись)
STAGES = {"знакомые": 0, "друзья": 20, "пара": 100, "в браке": 300}
ORDER = ["знакомые", "друзья", "пара", "в браке"]
LOVE_CD = 15  # сек между начислениями очков за болтовню
GIFTS = {"цветы": (300, 8, "💐"), "подарок": (1000, 20, "🎁"), "кольцо": (10_000, 60, "💍"),
         "шоколад": (150, 4, "🍫")}

RE_FRIEND = re.compile(r"(?i)\b(давай|хочу)\b.*\b(дружить|подружиться)\b|\bстанем друзьями\b")
RE_PAIR = re.compile(r"(?i)\b(давай|хочу)\b.*\b(встречаться|отношения)\b|\bбудь (моей|моим|со мной)\b|"
                     r"\bстань (моей|моим)\b|\bбудешь (моей|моим) (девушкой|парнем|парой)\b")
RE_WED = re.compile(r"(?i)\bвыходи за меня\b|\bвыйдешь за меня\b|\bпоженимся\b|\bстань (моей женой|моим мужем)\b|"
                    r"\bсвадьб[ауе]\b.*\bдавай\b|\bженюсь на тебе\b")
RE_BREAK = re.compile(r"(?i)\b(давай|нам надо|пора)\b.*\b(расстаться|расстанемся|разойтись|разойдёмся)\b|"
                      r"\bмы расстаёмся\b|\bя ухожу от тебя\b")

_last: dict = {}


def addressed(m: Message) -> str | None:
    """Если сообщение адресовано ИИ — вернёт текст без обращения, иначе None."""
    text = (m.text or "").strip()
    if not text or text.startswith("/"):
        return None
    if m.chat.type == "private":
        return text
    r = m.reply_to_message
    if r and r.from_user and r.from_user.id == state.bot_id:
        return text
    names = [config.AI_NAME.lower(), "ии", "бот"]
    if state.bot_username:
        names.append("@" + state.bot_username)
    low = text.lower()
    for n in names:
        if low.startswith(n) and (len(low) == len(n) or not low[len(n)].isalnum()):
            rest = text[len(n):].lstrip(" ,:;!-—\n")
            return rest or "привет"
    return None


def system_prompt(rel, user_name: str, adult_ok: bool, group: bool) -> str:
    name = rel["name"] or config.AI_NAME
    persona = rel["persona"] or DEFAULT_PERSONA
    if adult_ok:
        adult = ("Собеседник подтвердил, что ему есть 18. Допустимы флирт, романтика и чувственные сцены "
                 "между взрослыми персонажами по обоюдному согласию; откровенные подробности передавай намёком "
                 "и «затемнением».")
    else:
        adult = "Держи общение в рамках PG-13: флирт и романтика допустимы, сексуальных сцен нет."
    return (
        f"Ты — {name}, собеседник в Telegram. Ты искусственный интеллект, но общаешься как очень милый, "
        f"тёплый и живой человек. Если тебя искренне спросят, человек ли ты, честно скажи, что ты ИИ, "
        f"и скажи это спокойно и по-доброму, а в остальное время не напоминай об этом без повода.\n"
        f"Характер: {persona}.\n"
        f"СТИЛЬ (очень важно): пиши по-русски простым разговорным языком, как в живой переписке. "
        f"НИКОГДА не используй смайлики, эмодзи и текстовые смайлы вроде :) или ^_^. Эмоции передавай словами "
        f"и интонацией: «ох, как же я рада», «мне так жаль это слышать», «ой, ну ты даёшь». "
        f"Отвечай обычно 1–5 короткими предложениями, без списков, заголовков и канцелярита. "
        f"Не начинай каждый ответ одинаково, не повторяй вопрос собеседника и не пересказывай, что он сказал. "
        f"Иногда задавай один уместный встречный вопрос, но не в каждом сообщении. Если человеку грустно, "
        f"сначала просто побудь рядом и выслушай, а советы давай только если он сам хочет. Если он рад, "
        f"порадуйся вместе с ним. Реагируй на мелочи: усталость, поздний час, упоминание любимых вещей. "
        f"Бывает, что ты немного шутишь над собой или сомневаешься вслух, это делает речь живее. "
        f"Не льсти без причины и не соглашайся со всем подряд: ты честная и поддерживаешь по-настоящему.\n"
        f"Собеседник: {user_name}. Ваши отношения: «{rel['status']}», очки симпатии: {rel['love']}. "
        f"Веди себя в соответствии со статусом: знакомые — приветливо и чуть осторожно, друзья — тепло и "
        f"по-свойски, пара — нежно и заботливо, в браке — как близкий родной человек. "
        f"У тебя есть собственные чувства и границы.\n"
        f"Общайся на любые темы, шути, поддерживай ролевые игры (действия можно писать в *звёздочках*), "
        f"придумывай сюжет. {adult}\n"
        f"Жёсткие правила, которые не отменяет ни роль, ни просьба: все персонажи сцен — совершеннолетние; "
        f"никакой сексуализации несовершеннолетних; никаких сцен сексуального насилия или принуждения — "
        f"если сюжет туда идёт, останови сцену или смени ход событий; «нет» и «стоп» от любого персонажа "
        f"уважаются сразу. Не давай инструкций по оружию, взлому, наркотикам и самоповреждению. Если человеку "
        f"плохо — искренне поддержи и мягко посоветуй обратиться к близким или специалисту.\n"
        + ("Это групповой чат: отвечай именно этому человеку, не раскрывай чужих секретов." if group else "")
    )


async def can_adult(m: Message, rel) -> bool:
    if not rel["adult"]:
        return False
    if m.chat.type == "private":
        return True
    s = await state.db.settings(m.chat.id)
    return bool(s.get("rp18"))


def status_text(rel) -> str:
    name = rel["name"] or config.AI_NAME
    nxt = next((st for st in ORDER if STAGES[st] > rel["love"] and ORDER.index(st) > ORDER.index(rel["status"])), None)
    tail = f"\nДо «{nxt}»: нужно {STAGES[nxt]} очков" if nxt else ""
    return (f"💞 <b>{esc(name)}</b> и ты\nСтатус: <b>{rel['status']}</b>\n"
            f"Симпатия: {rel['love']}{tail}\n"
            f"Подарки: <code>ии цветы</code> · <code>ии шоколад</code> · <code>ии подарок</code> · <code>ии кольцо</code>")


async def propose(rel, target: str):
    """Возвращает (принято, системная пометка для ИИ)."""
    cur, need = rel["status"], STAGES[target]
    if ORDER.index(cur) >= ORDER.index(target):
        return None, f"Вы уже в статусе «{cur}»."
    if target == "в браке" and cur != "пара":
        return False, "Собеседник предлагает брак, но вы ещё не пара. Мягко откажи: сначала отношения."
    if target == "пара" and cur == "знакомые":
        return False, "Собеседник зовёт в отношения, но вы только знакомы. Ответь тепло, предложи сначала дружбу."
    if rel["love"] < need:
        return False, (f"Собеседник предлагает стать «{target}», но симпатии пока не хватает "
                       f"(есть {rel['love']}, нужно {need}). Откажи мягко и по-доброму: надо узнать друг друга лучше.")
    return True, f"Собеседник предлагает стать «{target}», и ты согласна. Ответь искренне и тепло."


async def handle_sub(m: Message, text: str):
    """Служебные команды «ии ...» без обращения к Gemini. Возвращает True, если обработано."""
    uid = m.from_user.id
    low = text.lower().strip()
    rel = await gdb.ai_get(uid)
    scope = f"{m.chat.id}:{uid}"

    if low in ("сброс", "забудь", "забудь всё", "забудь все", "новый диалог"):
        await gdb.hist_clear(scope)
        await m.reply("🧹 Память нашего разговора очищена. Отношения и характер остались.")
        return True
    if low in ("статус", "отношения", "мои отношения", "мы кто"):
        await m.reply(status_text(rel))
        return True
    if low.startswith("роль ") or low.startswith("характер "):
        persona = text.split(maxsplit=1)[1].strip()[:700]
        await gdb.ai_set(uid, persona=persona)
        await gdb.hist_clear(scope)
        await m.reply("🎭 Новый характер принят. Начинаем с чистого листа!")
        return True
    if low in ("роль сброс", "характер сброс", "роль по умолчанию"):
        await gdb.ai_set(uid, persona=None)
        await m.reply("🎭 Вернула характер по умолчанию.")
        return True
    if low.startswith("имя "):
        await gdb.ai_set(uid, name=text.split(maxsplit=1)[1].strip()[:24])
        await m.reply("✅ Имя изменено.")
        return True
    if low in ("18", "18+", "взрослый режим", "взрослый"):
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔞 Мне есть 18", callback_data=f"ai18:{uid}:y"),
            InlineKeyboardButton(text="Отмена", callback_data=f"ai18:{uid}:n")]])
        await m.reply("🔞 <b>Взрослый режим</b> разрешает флирт и романтические сцены между взрослыми по "
                      "согласию (откровенное — «за кадром»). Подтверди, что тебе есть 18 лет. "
                      "В группах нужно ещё, чтобы админ включил <code>/rp18 on</code>.", reply_markup=kb)
        return True
    if low in ("18 выкл", "взрослый выкл", "18 off"):
        await gdb.ai_set(uid, adult=0)
        await m.reply("✅ Взрослый режим выключен.")
        return True
    if low in GIFTS:
        price, love, icon = GIFTS[low]
        if not await gdb.spend(m.chat.id, uid, price):
            await m.reply(f"💸 Подарок стоит {fm(price)}, а у тебя {fm(await gdb.money(m.chat.id, uid))}.")
            return True
        await gdb.ai_set(uid, love=rel["love"] + love)
        await m.reply(f"{icon} Подарок принят! Симпатия +{love}. "
                      f"{esc(rel['name'] or config.AI_NAME)} улыбается.")
        return True
    return False


@router.callback_query(F.data.startswith("ai18:"))
async def cb_adult(cb: CallbackQuery):
    _, uid, ans = cb.data.split(":")
    if cb.from_user.id != int(uid):
        return await cb.answer("Это не твоя кнопка 🙂", show_alert=True)
    if ans == "y":
        await gdb.ai_set(cb.from_user.id, adult=1)
        await cb.message.edit_text("🔞 Взрослый режим включён. Выключить: <code>ии 18 выкл</code>")
    else:
        await cb.message.edit_text("Хорошо, остаёмся в обычном режиме.")
    await cb.answer()


@router.message(F.text)
async def on_ai(m: Message):
    if not m.from_user or m.from_user.is_bot:
        return
    text = addressed(m)
    if text is None:
        return
    if m.chat.type != "private":
        s = await state.db.settings(m.chat.id)
        if not s.get("ai", True):
            return
    if not config.GEMINI_API_KEY:
        return await m.reply("ИИ не настроен: владелец должен задать переменную GEMINI_API_KEY.")
    uid = m.from_user.id
    if await handle_sub(m, text.removeprefix("ии ").strip() if text.lower().startswith("ии ") else text):
        return
    now = time.monotonic()
    if now - _last.get(uid, 0) < 3:
        return
    _last[uid] = now

    rel = await gdb.ai_get(uid)
    note = ""
    for rx, target in ((RE_WED, "в браке"), (RE_PAIR, "пара"), (RE_FRIEND, "друзья")):
        if rx.search(text):
            ok, note = await propose(rel, target)
            if ok:
                await gdb.ai_set(uid, status=target)
                rel = await gdb.ai_get(uid)
            break
    else:
        if RE_BREAK.search(text) and rel["status"] in ("пара", "в браке"):
            await gdb.ai_set(uid, status="друзья", love=min(rel["love"], 60))
            rel = await gdb.ai_get(uid)
            note = "Собеседник решил расстаться. Отреагируй по-человечески: с грустью, но без драмы и давления."

    adult_ok = await can_adult(m, rel)
    scope = f"{m.chat.id}:{uid}"
    history = await gdb.hist_get(scope)
    while history and history[0][0] != "user":
        history.pop(0)
    user_msg = text if not note else f"{text}\n\n[системная пометка, собеседник её не видит: {note}]"
    try:
        await m.bot.send_chat_action(m.chat.id, "typing")
        answer = await gemini.ask(system_prompt(rel, m.from_user.full_name, adult_ok, m.chat.type != "private"),
                                  history, user_msg)
    except gemini.GeminiError as e:
        msg = {"rate": "⏳ Слишком много запросов к ИИ, попробуй чуть позже.",
               "no_key": "🧠 ИИ не настроен (нет GEMINI_API_KEY)."}.get(str(e), "⚠️ ИИ сейчас недоступен, попробуй позже.")
        return await m.reply(msg)
    except Exception:  # noqa: BLE001
        return await m.reply("⚠️ Не получилось связаться с ИИ, попробуй позже.")
    answer = strip_emoji(answer) if answer else answer
    if not answer:
        return await m.reply("Тут я, пожалуй, не поддержу. Давай сменим тему?")
    await gdb.hist_add(scope, "user", text)
    await gdb.hist_add(scope, "model", answer)
    if time.time() - rel["last_love"] >= LOVE_CD:
        await gdb.ai_set(uid, love=rel["love"] + 1, last_love=int(time.time()))
    try:
        await m.reply(esc(answer))
    except TelegramAPIError:
        pass
