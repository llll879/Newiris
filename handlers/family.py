"""Отношения, друзья, свидания, семья, беременность и дети (всё по согласию обеих сторон)."""
import asyncio
import logging
import random
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
import gdb
import state
from handlers.economy import CARS, HOUSES, fm
from phrase import Say
from utils import enrich, esc, fmt_date, fmt_dur, from_user, mention, name_of, resolve_target, temp

router = Router()
log = logging.getLogger("family")
GROUP = F.chat.type.in_({"group", "supergroup"})

MAX_KIDS = 6
DATE_COST, DATE_CD = 500, 3600
ADOPT_COST = 15_000
NAMES_M = ["Артём", "Максим", "Миша", "Тимофей", "Ярослав", "Егор", "Лев", "Фёдор", "Марк", "Даня"]
NAMES_F = ["Соня", "Алиса", "Мила", "Ева", "Варя", "Полина", "Ника", "Арина", "Злата", "Тася"]
LOVE_LEVELS = [(0, "💫 Только начали"), (30, "💗 Влюблённые"), (100, "💞 Сильные чувства"),
               (250, "💖 Настоящая любовь"), (600, "👑 Родственные души")]


def love_name(love: int) -> str:
    cur = LOVE_LEVELS[0][1]
    for need, name in LOVE_LEVELS:
        if love >= need:
            cur = name
    return cur


async def gate(m: Message) -> bool:
    s = await state.db.settings(m.chat.id)
    if not s.get("family", True):
        temp(await m.reply("👪 Семья и отношения в этом чате выключены (админ: /settings)."), 8)
        return False
    return bool(m.from_user)


def yn(prefix: str, a: int, b: int, yes: str, no: str, extra: str = "") -> InlineKeyboardMarkup:
    tail = f":{extra}" if extra else ""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=yes, callback_data=f"{prefix}:{a}:{b}:y{tail}"),
        InlineKeyboardButton(text=no, callback_data=f"{prefix}:{a}:{b}:n{tail}"),
    ]])


async def need_target(m: Message, args: list, hint: str):
    t, _ = await resolve_target(m, args)
    if not t or not t.id:
        await m.reply(hint)
        return None
    if t.id == m.from_user.id:
        await m.reply("😅 Нужен кто-то другой.")
        return None
    if t.is_bot or t.id == state.bot_id:
        await m.reply("🤖 Боты в отношения не вступают.")
        return None
    return t


async def decide(cb: CallbackQuery, a: int, b: int, ans: str, cancel_text: str, refuse_text: str):
    """Общая логика кнопок «да/нет». Возвращает True, если нужно продолжать (адресат сказал «да»)."""
    uid = cb.from_user.id
    chat_id = cb.message.chat.id
    if uid == a and ans == "n":
        await cb.message.edit_text(cancel_text)
        await cb.answer()
        return False
    if uid != b:
        await cb.answer("Это решает только адресат 🙂", show_alert=True)
        return False
    if ans == "n":
        await cb.message.edit_text(refuse_text.format(b=await name_of(chat_id, b), a=await name_of(chat_id, a)))
        await cb.answer()
        return False
    return True


# ---------- отношения ----------
@router.message(Say("встречаться", "начать отношения", "давай встречаться", "быть парой",
                    "предложить отношения", "позвать встречаться"), GROUP)
async def cmd_date_ask(m: Message, args: list):
    if not await gate(m):
        return
    t = await need_target(m, args, "💘 Ответь на сообщение человека, с которым хочешь встречаться.")
    if not t:
        return
    cid, uid = m.chat.id, m.from_user.id
    for who in (uid, t.id):
        pid, married, _ = await gdb.partner_of(cid, who)
        if pid:
            who_txt = "Ты уже" if who == uid else "Этот человек уже"
            return await m.reply(f"💔 {who_txt} {'в браке' if married else 'в отношениях'}.")
    me = await enrich(cid, from_user(m.from_user))
    await state.db.ensure(cid, me)
    await state.db.ensure(cid, t)
    await m.answer(f"💘 {mention(me)} предлагает {mention(t)} начать отношения!\n{mention(t)}, что скажешь?",
                   reply_markup=yn("dt", me.id, t.id, "💞 Да", "💔 Нет"))


@router.callback_query(F.data.startswith("dt:"))
async def cb_date(cb: CallbackQuery):
    try:
        _, a, b, ans = cb.data.split(":")
        a, b = int(a), int(b)
    except ValueError:
        return await cb.answer()
    if not await decide(cb, a, b, ans, "🚫 Предложение отозвано.", "💔 {b} отказывает {a}."):
        return
    cid = cb.message.chat.id
    for who in (a, b):
        if (await gdb.partner_of(cid, who))[0]:
            await cb.message.edit_text("💔 Кто-то из вас уже в отношениях.")
            return await cb.answer()
    await gdb.make_pair(cid, a, b)
    await cb.message.edit_text(f"💞 {await name_of(cid, a)} и {await name_of(cid, b)} теперь пара! "
                               f"Свидания: <code>свидание</code> · статус: <code>отношения</code>")
    await cb.answer()


@router.message(Say("расстаться", "порвать отношения"), GROUP)
async def cmd_breakup(m: Message):
    if not await gate(m):
        return
    cid, uid = m.chat.id, m.from_user.id
    pid, married, _ = await gdb.partner_of(cid, uid)
    if not pid:
        return await m.reply("Ты ни с кем не встречаешься.")
    if married:
        return await m.reply("💍 Вы в браке — для этого есть <code>развод</code>.")
    await gdb.break_pair(cid, uid)
    await m.answer(f"💔 {await name_of(cid, uid)} и {await name_of(cid, pid)} расстаются.")


@router.message(Say("отношения", "мои отношения", "пара", "моя пара", "любовь"), GROUP)
async def cmd_rel(m: Message):
    if not await gate(m):
        return
    cid, uid = m.chat.id, m.from_user.id
    pid, married, pair = await gdb.partner_of(cid, uid)
    if not pid:
        return await m.reply("💘 Ты свободен(на). Начать: <code>встречаться</code> (ответом на сообщение).")
    love = pair["love"] if pair else 0
    since = pair["since"] if pair else None
    mar = await state.db.marriage_of(cid, uid)
    if mar:
        since = mar["since"]
    await m.reply(f"{'💍 Брак' if married else '💘 Отношения'}: {await name_of(cid, uid)} + "
                  f"{await name_of(cid, pid)}\n{love_name(love)} · очков любви: {love}\n"
                  f"📅 С {fmt_date(since)}")


DATE_SCENES = [
    "🍝 сходили в уютный ресторанчик и проболтали до закрытия",
    "🎬 посмотрели фильм в кино, деля один попкорн на двоих",
    "🌅 встретили закат на крыше и долго молчали — и это было лучше любых слов",
    "🎡 покатались на колесе обозрения, глядя на огни города",
    "🛶 катались на лодке по озеру",
    "🍦 гуляли по набережной с мороженым",
    "🎳 сыграли в боулинг — проигравший угощает",
    "🌌 лежали на траве и считали звёзды",
    "☕ засели в кофейне с пледами и какао",
]


@router.message(Say("свидание", "сходить на свидание", "пойти на свидание", "романтический вечер"), GROUP)
async def cmd_dating(m: Message):
    if not await gate(m):
        return
    cid, uid = m.chat.id, m.from_user.id
    pid, married, pair = await gdb.partner_of(cid, uid)
    if not pid:
        return await m.reply("💘 Сначала нужна пара: <code>встречаться</code> или <code>брак</code>.")
    if not pair:
        await gdb.make_pair(cid, uid, pid)
        pair = await gdb.pair_of(cid, uid)
    left = pair["last_date"] + DATE_CD - time.time()
    if left > 0:
        return await m.reply(f"⏳ Вы недавно гуляли. Следующее свидание через {fmt_dur(left)}.")
    if not await gdb.spend(cid, uid, DATE_COST):
        return await m.reply(f"💸 На свидание нужно {fm(DATE_COST)}.")
    gain = random.randint(8, 18)
    if await gdb.asset(cid, uid, "car") is not None:
        gain += 4
    if await gdb.asset(cid, pid, "car") is not None:
        gain += 4
    await gdb.love_add(cid, uid, pid, gain)
    await gdb.set_last_date(cid, uid, pid)
    scene = random.choice(DATE_SCENES)
    await m.answer(f"💑 {await name_of(cid, uid)} и {await name_of(cid, pid)} {scene}.\n"
                   f"💗 Любовь +{gain} · потрачено {fm(DATE_COST)}")


# ---------- друзья ----------
@router.message(Say("дружить", "подружиться", "стать друзьями", "предложить дружбу"), GROUP)
async def cmd_friend_ask(m: Message, args: list):
    if not await gate(m):
        return
    t = await need_target(m, args, "🤝 Ответь на сообщение человека, с которым хочешь дружить.")
    if not t:
        return
    if await gdb.are_friends(m.chat.id, m.from_user.id, t.id):
        return await m.reply("🤝 Вы уже друзья!")
    me = await enrich(m.chat.id, from_user(m.from_user))
    await state.db.ensure(m.chat.id, me)
    await state.db.ensure(m.chat.id, t)
    await m.answer(f"🤝 {mention(me)} предлагает {mention(t)} стать друзьями!",
                   reply_markup=yn("fr", me.id, t.id, "🤝 Давай", "🙅 Не сейчас"))


@router.callback_query(F.data.startswith("fr:"))
async def cb_friend(cb: CallbackQuery):
    try:
        _, a, b, ans = cb.data.split(":")
        a, b = int(a), int(b)
    except ValueError:
        return await cb.answer()
    if not await decide(cb, a, b, ans, "🚫 Предложение отозвано.", "🙅 {b} вежливо отказывается."):
        return
    cid = cb.message.chat.id
    await gdb.friend_add(cid, a, b)
    await cb.message.edit_text(f"🤝 {await name_of(cid, a)} и {await name_of(cid, b)} теперь друзья!")
    await cb.answer()


@router.message(Say("друзья", "мои друзья"), GROUP)
async def cmd_friends(m: Message):
    ids = await gdb.friends_of(m.chat.id, m.from_user.id)
    if not ids:
        return await m.reply("🤝 Друзей пока нет. <code>дружить</code> (ответом на сообщение)")
    names = [await name_of(m.chat.id, i) for i in ids[:30]]
    await m.reply("🤝 <b>Твои друзья:</b> " + ", ".join(names))


@router.message(Say("раздружиться", "прекратить дружбу"), GROUP)
async def cmd_unfriend(m: Message, args: list):
    t = await need_target(m, args, "Ответь на сообщение бывшего друга.")
    if t and await gdb.friend_del(m.chat.id, m.from_user.id, t.id):
        await m.answer(f"💔 {await name_of(m.chat.id, m.from_user.id)} больше не дружит с {mention(t)}.")


# ---------- беременность ----------
@router.message(Say("завести ребёнка", "завести ребенка", "завести детей", "родить ребёнка",
                    "родить ребенка", "забеременеть", "хочу ребёнка", "хочу ребенка", "давай заведём ребёнка"), GROUP)
async def cmd_baby(m: Message, args: list):
    if not await gate(m):
        return
    cid, uid = m.chat.id, m.from_user.id
    pid, married, _ = await gdb.partner_of(cid, uid)
    if not pid:
        return await m.reply("👶 Для ребёнка нужна пара: <code>встречаться</code> или <code>брак</code>.")
    t, _ = await resolve_target(m, args)
    if t and t.id and t.id != pid:
        return await m.reply("👶 Ребёнка можно завести только со своим партнёром.")
    for who in (uid, pid):
        w = await gdb.wallet(cid, who)
        if w["preg_until"] > time.time():
            return await m.reply("🤰 Сейчас уже ждёте малыша — подождите рождения.")
    if len(await gdb.kids_of(cid, uid)) >= MAX_KIDS:
        return await m.reply(f"👨‍👩‍👧‍👦 Это уже большая семья (максимум {MAX_KIDS} детей).")
    me = await enrich(cid, from_user(m.from_user))
    await m.answer(f"👶 {mention(me)} предлагает {await name_of(cid, pid)} завести ребёнка.\n"
                   f"{await name_of(cid, pid)}, ты согласен(на)? Решение за тобой.",
                   reply_markup=yn("bb", uid, pid, "👶 Согласен(на)", "🙅 Пока рано"))


@router.callback_query(F.data.startswith("bb:"))
async def cb_baby(cb: CallbackQuery):
    try:
        _, a, b, ans = cb.data.split(":")
        a, b = int(a), int(b)
    except ValueError:
        return await cb.answer()
    if not await decide(cb, a, b, ans, "🚫 Предложение отозвано.", "🙅 {b} пока не готов(а) к ребёнку."):
        return
    cid = cb.message.chat.id
    async def label(uid):
        row = await state.db.get_user(cid, uid)
        return f"🤰 {((row['name'] if row else None) or 'Участник')[:20]}"

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=await label(a), callback_data=f"bc:{a}:{b}:{a}"),
        InlineKeyboardButton(text=await label(b), callback_data=f"bc:{a}:{b}:{b}"),
    ]])
    await cb.message.edit_text("💞 Вы решили завести малыша! Кто будет ждать ребёнка? "
                               "(выбирает любой из пары)", reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("bc:"))
async def cb_carrier(cb: CallbackQuery):
    try:
        _, a, b, who = cb.data.split(":")
        a, b, who = int(a), int(b), int(who)
    except ValueError:
        return await cb.answer()
    if cb.from_user.id not in (a, b):
        return await cb.answer("Это решают только родители 🙂", show_alert=True)
    cid = cb.message.chat.id
    other = b if who == a else a
    w = await gdb.wallet(cid, who)
    if w["preg_until"] > time.time():
        return await cb.answer("Уже в ожидании", show_alert=True)
    await gdb.setf(cid, who, preg_until=int(time.time() + config.PREG_HOURS * 3600), preg_with=other)
    await cb.message.edit_text(
        f"🤰 {await name_of(cid, who)} ждёт малыша вместе с {await name_of(cid, other)}!\n"
        f"Ожидайте через ~{config.PREG_HOURS:g} ч. Прогресс: <code>беременность</code>")
    await cb.answer()


@router.message(Say("беременность", "срок", "как малыш", "мой срок"), GROUP)
async def cmd_preg(m: Message):
    cid, uid = m.chat.id, m.from_user.id
    w = await gdb.wallet(cid, uid)
    left = w["preg_until"] - time.time()
    if left <= 0:
        return await m.reply("Сейчас ты не в ожидании. Завести малыша: <code>завести ребёнка</code>")
    total = config.PREG_HOURS * 3600
    pct = max(0, min(100, int((1 - left / total) * 100)))
    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
    await m.reply(f"🤰 Ожидание малыша\n[{bar}] {pct}%\nДо рождения: {fmt_dur(left)}")


async def births_once(bot):
    now = int(time.time())
    rows = await state.db.q(
        "SELECT chat_id,user_id,preg_with FROM wallet WHERE preg_until>0 AND preg_until<=?", (now,))
    for r in rows:
        cid, mom, dad = r["chat_id"], r["user_id"], r["preg_with"]
        await gdb.setf(cid, mom, preg_until=0, preg_with=0)
        gender = random.choice(["m", "f"])
        name = random.choice(NAMES_M if gender == "m" else NAMES_F)
        await gdb.add_kid(cid, mom, dad, name, gender)
        word = "родился сын" if gender == "m" else "родилась дочка"
        text = (f"👶🎉 У {await name_of(cid, mom)} и {await name_of(cid, dad)} {word}!\n"
                f"Имя: <b>{esc(name)}</b>. Переименовать: <code>назвать ребёнка Имя</code>")
        try:
            await bot.send_message(cid, text)
        except TelegramAPIError as e:
            log.warning("не смог отправить сообщение о рождении в %s: %s", cid, e)


async def loop(bot):
    while True:
        await asyncio.sleep(60)
        try:
            await births_once(bot)
        except Exception:  # noqa: BLE001
            log.exception("ошибка в цикле родов")


# ---------- дети и семья ----------
@router.message(Say("назвать ребёнка", "назвать ребенка", "переименовать ребёнка", "переименовать ребенка",
                    loose=True), GROUP)
async def cmd_name_kid(m: Message, arg: str):
    name = " ".join(arg.split())[:20]
    if not name:
        return await m.reply("Формат: <code>назвать ребёнка Имя</code>")
    ok = await gdb.rename_last_kid(m.chat.id, m.from_user.id, name)
    await m.reply(f"👶 Теперь малыша зовут <b>{esc(name)}</b>!" if ok else "У тебя ещё нет детей.")


@router.message(Say("усыновить", "взять ребёнка из приюта", "взять ребенка из приюта", "удочерить"), GROUP)
async def cmd_adopt(m: Message):
    if not await gate(m):
        return
    cid, uid = m.chat.id, m.from_user.id
    if len(await gdb.kids_of(cid, uid)) >= MAX_KIDS:
        return await m.reply(f"👨‍👩‍👧‍👦 Максимум {MAX_KIDS} детей.")
    if not await gdb.spend(cid, uid, ADOPT_COST):
        return await m.reply(f"💸 Оформление документов стоит {fm(ADOPT_COST)}.")
    pid, _, _ = await gdb.partner_of(cid, uid)
    gender = random.choice(["m", "f"])
    name = random.choice(NAMES_M if gender == "m" else NAMES_F)
    await gdb.add_kid(cid, uid, pid or 0, name, gender)
    await m.answer(f"🏠👶 {await name_of(cid, uid)} усыновляет малыша по имени <b>{esc(name)}</b>! "
                   f"Переименовать: <code>назвать ребёнка Имя</code>")


@router.message(Say("дети", "мои дети", "ребёнок", "ребенок"), GROUP)
async def cmd_kids(m: Message):
    kids = await gdb.kids_of(m.chat.id, m.from_user.id)
    if not kids:
        return await m.reply("👶 Детей пока нет. <code>завести ребёнка</code> или <code>усыновить</code>")
    lines = [f"{'👦' if k['gender'] == 'm' else '👧'} {esc(k['name'])} — с {fmt_date(k['born'])}" for k in kids]
    await m.reply("👨‍👩‍👧‍👦 <b>Твои дети</b>\n" + "\n".join(lines))


KID_SCENES = ["сходили в парк", "построили башню из кубиков", "испекли печенье", "почитали сказку на ночь",
              "запустили воздушного змея", "порисовали красками", "сходили в зоопарк"]


@router.message(Say("гулять с ребёнком", "гулять с ребенком", "погулять с детьми", "заняться детьми",
                    "покормить ребёнка", "покормить ребенка"), GROUP)
async def cmd_play_kid(m: Message):
    cid, uid = m.chat.id, m.from_user.id
    kids = await gdb.kids_of(cid, uid)
    if not kids:
        return await m.reply("У тебя пока нет детей.")
    k = random.choice(kids)
    await m.reply(f"👨‍👩‍👧 {await name_of(cid, uid)} и {esc(k['name'])} {random.choice(KID_SCENES)}. Какой чудесный день! ☀️")


@router.message(Say("пособие", "детское пособие", "получить пособие"), GROUP)
async def cmd_benefit(m: Message):
    if not await gate(m):
        return
    cid, uid = m.chat.id, m.from_user.id
    kids = await gdb.kids_of(cid, uid)
    if not kids:
        return await m.reply("Пособие положено семьям с детьми.")
    w = await gdb.wallet(cid, uid)
    left = w["last_benefit"] + 86400 - time.time()
    if left > 0:
        return await m.reply(f"⏳ Пособие уже получено. Через {fmt_dur(left)}.")
    amount = 250 * len(kids)
    await gdb.setf(cid, uid, last_benefit=int(time.time()))
    await gdb.add_money(cid, uid, amount)
    await m.reply(f"👪 Пособие на {len(kids)} дет.: <b>+{fm(amount)}</b>")


async def family_card(chat_id: int, uid: int) -> str:
    pid, married, pair = await gdb.partner_of(chat_id, uid)
    lines = [f"👪 <b>Семья {await name_of(chat_id, uid)}</b>"]
    if pid:
        love = pair["love"] if pair else 0
        lines.append(f"{'💍 Супруг(а)' if married else '💘 Партнёр(ша)'}: {await name_of(chat_id, pid)} · {love_name(love)}")
    else:
        lines.append("💘 Партнёра нет")
    kids = await gdb.kids_of(chat_id, uid)
    lines.append("👶 Дети: " + (", ".join(esc(k["name"]) for k in kids) if kids else "нет"))
    ids = [uid] + ([pid] if pid else [])
    total = 0
    for i in ids:
        total += await gdb.money(chat_id, i)
    h, c = await gdb.asset(chat_id, uid, "house"), await gdb.asset(chat_id, uid, "car")
    if pid:
        h = h if h is not None else await gdb.asset(chat_id, pid, "house")
        c = c if c is not None else await gdb.asset(chat_id, pid, "car")
    lines.append(f"🏠 Дом: {HOUSES[h][0] if h is not None else '—'}")
    lines.append(f"🚗 Машина: {CARS[c][0] if c is not None else '—'}")
    lines.append(f"💰 Семейный бюджет: {fm(total)}")
    friends = await gdb.friends_of(chat_id, uid)
    lines.append(f"🤝 Друзей: {len(friends)}")
    return "\n".join(lines)


@router.message(Say("семья", "моя семья"), GROUP)
async def cmd_family(m: Message, args: list):
    if not await gate(m):
        return
    t, _ = await resolve_target(m, args)
    uid = t.id if t and t.id else m.from_user.id
    await m.reply(await family_card(m.chat.id, uid))


async def profile_lines(chat_id: int, uid: int) -> list[str]:
    """Дополнительные строки для /профиль."""
    out = []
    w = await gdb.wallet(chat_id, uid)
    out.append(f"💰 Деньги: {fm(w['money'])}")
    pid, married, pair = await gdb.partner_of(chat_id, uid)
    if pid and not married:
        out.append(f"💘 В отношениях с {await name_of(chat_id, pid)}")
    kids = await gdb.kids_of(chat_id, uid)
    if kids:
        out.append(f"👶 Детей: {len(kids)}")
    h, c = await gdb.asset(chat_id, uid, "house"), await gdb.asset(chat_id, uid, "car")
    if h is not None:
        out.append(f"🏠 {HOUSES[h][0]}")
    if c is not None:
        out.append(f"🚗 {CARS[c][0]}")
    return out
