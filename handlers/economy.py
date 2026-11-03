"""Экономика: деньги, работа, казино, магазин (дома, машины, питомцы)."""
import random
import re
import time

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import gdb
import state
from filters import Cmd
from phrase import Say
from utils import Target, enrich, esc, from_user, fmt_dur, mention, resolve_target, temp

router = Router()
GROUP = F.chat.type.in_({"group", "supergroup"})

MIN_BET, MAX_BET = 10, 100_000
DAILY_CD, WORK_CD, STEAL_CD, MISC_CD, INCOME_CAP_H = 86400, 1800, 1800, 900, 24


def fm(n: int) -> str:
    return f"{int(n):,}".replace(",", " ") + " 💰"


async def gate(m: Message) -> bool:
    s = await state.db.settings(m.chat.id)
    if not s.get("games", True):
        temp(await m.reply("🎰 Экономика и игры в этом чате выключены (админ: /settings)."), 8)
        return False
    return bool(m.from_user)


def parse_amount(tok: str, balance: int):
    t = tok.lower()
    if t in ("все", "всё", "all", "вабанк", "ва-банк"):
        return min(balance, MAX_BET)
    mm = re.fullmatch(r"(\d{1,9})([кk]?)", t)
    if not mm:
        return None
    n = int(mm.group(1)) * (1000 if mm.group(2) else 1)
    return n


async def take_bet(m: Message, tok: str | None):
    """Проверяет ставку и сразу списывает. Возвращает сумму либо None."""
    bal = await gdb.money(m.chat.id, m.from_user.id)
    if not tok:
        await m.reply(f"Укажи ставку: от {MIN_BET} до {MAX_BET}, или «все». У тебя {fm(bal)}")
        return None
    bet = parse_amount(tok, bal)
    if bet is None or bet < MIN_BET:
        await m.reply(f"Ставка — число от {MIN_BET} до {MAX_BET} (можно «5к» или «все»).")
        return None
    if bet > MAX_BET:
        await m.reply(f"Максимальная ставка — {fm(MAX_BET)}.")
        return None
    if not await gdb.spend(m.chat.id, m.from_user.id, bet):
        await m.reply(f"💸 Не хватает денег. У тебя {fm(bal)}")
        return None
    return bet


_game_cd: dict = {}


def cooling(uid: int, sec: float = 2.0) -> bool:
    now = time.monotonic()
    if now - _game_cd.get(uid, 0) < sec:
        return True
    _game_cd[uid] = now
    return False


# ---------- кошелёк ----------
@router.message(Say("баланс", "кошелёк", "кошелек", "деньги", "мои деньги", "бабки", "мой баланс"), GROUP)
async def cmd_balance(m: Message, args: list):
    if not await gate(m):
        return
    t, _ = await resolve_target(m, args)
    if not t or not t.id:
        t = await enrich(m.chat.id, from_user(m.from_user))
    bal = await gdb.money(m.chat.id, t.id)
    await m.reply(f"👛 Кошелёк {mention(t)}: <b>{fm(bal)}</b>")


@router.message(Say("ежедневка", "бонус", "ежедневный бонус", "получить бонус", "халява"), GROUP)
async def cmd_daily(m: Message):
    if not await gate(m):
        return
    cid, uid = m.chat.id, m.from_user.id
    w = await gdb.wallet(cid, uid)
    left = w["last_daily"] + DAILY_CD - time.time()
    if left > 0:
        return await m.reply(f"⏳ Бонус уже получен. Приходи через {fmt_dur(left)}.")
    amount = random.randint(400, 900)
    await gdb.setf(cid, uid, last_daily=int(time.time()))
    total = await gdb.add_money(cid, uid, amount)
    await m.reply(f"🎁 Ежедневный бонус: <b>+{fm(amount)}</b>\nТеперь у тебя {fm(total)}")


JOBS = [  # (название, нужно смен, (мин, макс))
    ("🚴 Курьер", 0, (80, 160)), ("🛒 Продавец", 5, (120, 240)), ("🍽 Официант", 15, (180, 320)),
    ("🚚 Водитель", 30, (250, 450)), ("💻 Программист", 60, (450, 800)),
    ("🩺 Врач", 100, (600, 1000)), ("👔 Директор", 200, (900, 1600)),
]


def job_for(works: int):
    cur = JOBS[0]
    for j in JOBS:
        if works >= j[1]:
            cur = j
    return cur


@router.message(Say("работать", "работа", "пойти на работу", "на работу", "смена"), GROUP)
async def cmd_work(m: Message):
    if not await gate(m):
        return
    cid, uid = m.chat.id, m.from_user.id
    w = await gdb.wallet(cid, uid)
    left = w["last_work"] + WORK_CD - time.time()
    if left > 0:
        return await m.reply(f"😮‍💨 Ты ещё отдыхаешь после смены. Ещё {fmt_dur(left)}.")
    name, _, (lo, hi) = job_for(w["works"])
    pay = random.randint(lo, hi)
    await gdb.setf(cid, uid, last_work=int(time.time()))
    await gdb.incr(cid, uid, "works")
    total = await gdb.add_money(cid, uid, pay)
    nxt = next((j for j in JOBS if j[1] > w["works"] + 1), None)
    tail = f"\n📈 До повышения ({nxt[0]}): {nxt[1] - w['works'] - 1} смен." if nxt else "\n👑 Ты на вершине карьеры!"
    await m.reply(f"💼 {name}: смена закончена, <b>+{fm(pay)}</b>\nВсего: {fm(total)}{tail}")


@router.message(Say("работы", "профессии", "карьера"), GROUP)
async def cmd_jobs(m: Message):
    w = await gdb.wallet(m.chat.id, m.from_user.id)
    cur = job_for(w["works"])
    lines = [f"{'👉 ' if j == cur else '▫️ '}{j[0]} — с {j[1]} смен, {j[2][0]}–{j[2][1]} за смену" for j in JOBS]
    await m.reply(f"<b>Карьера</b> (смен отработано: {w['works']})\n" + "\n".join(lines) +
                  "\nРаботать: <code>работать</code> (раз в 30 минут)")


@router.message(Say("рыбалка", "рыбачить", "пойти на рыбалку"), GROUP)
async def cmd_fish(m: Message):
    await _misc(m, "🎣", [("ничего не поймал, только водоросли", 0), ("поймал карася", 60),
                          ("поймал щуку", 150), ("выловил старый сундучок", 400),
                          ("поймал золотую рыбку и загадал деньги", 700)],
                [30, 30, 25, 10, 5])


@router.message(Say("шахта", "копать", "пойти в шахту"), GROUP)
async def cmd_mine(m: Message):
    await _misc(m, "⛏", [("накопал только камней", 0), ("нашёл уголь", 90), ("нашёл железную руду", 180),
                         ("нашёл золотую жилу", 500), ("откопал алмаз!", 1200)],
                [30, 30, 24, 12, 4])


async def _misc(m: Message, icon: str, outcomes: list, weights: list):
    if not await gate(m):
        return
    cid, uid = m.chat.id, m.from_user.id
    w = await gdb.wallet(cid, uid)
    left = w["last_misc"] + MISC_CD - time.time()
    if left > 0:
        return await m.reply(f"⏳ Сил нет. Отдохни ещё {fmt_dur(left)}.")
    text, gain = random.choices(outcomes, weights)[0]
    await gdb.setf(cid, uid, last_misc=int(time.time()))
    if gain:
        await gdb.add_money(cid, uid, gain)
    await m.reply(f"{icon} {mention(from_user(m.from_user))} {text}" + (f": <b>+{fm(gain)}</b>" if gain else "."))


@router.message(Say("таксовать", "такси", "подработать в такси"), GROUP)
async def cmd_taxi(m: Message):
    if not await gate(m):
        return
    cid, uid = m.chat.id, m.from_user.id
    car = await gdb.asset(cid, uid, "car")
    if car is None:
        return await m.reply("🚕 Для такси нужна машина. Смотри: <code>магазин машины</code>")
    w = await gdb.wallet(cid, uid)
    left = w["last_misc"] + MISC_CD - time.time()
    if left > 0:
        return await m.reply(f"⏳ Сил нет. Отдохни ещё {fmt_dur(left)}.")
    pay = random.randint(200, 500) * (1 + car // 2)
    await gdb.setf(cid, uid, last_misc=int(time.time()))
    await gdb.add_money(cid, uid, pay)
    await m.reply(f"🚕 {CARS[car][0]}: отвёз пассажиров, <b>+{fm(pay)}</b>")


@router.message(Say("перевести", "передать", "дать денег", "отправить деньги", "скинуть"), GROUP)
async def cmd_pay(m: Message, args: list):
    if not await gate(m):
        return
    t, rest = await resolve_target(m, args)
    if not t or not t.id or not rest:
        return await m.reply("Формат: ответом на сообщение — <code>перевести 500</code> или "
                             "<code>перевести @ник 500</code>")
    if t.id == m.from_user.id or t.id == state.bot_id:
        return await m.reply("🙃 Так не получится.")
    bal = await gdb.money(m.chat.id, m.from_user.id)
    amount = parse_amount(rest[0], bal)
    if not amount or amount < 1:
        return await m.reply("Сумма должна быть положительным числом.")
    if not await gdb.spend(m.chat.id, m.from_user.id, amount):
        return await m.reply(f"💸 Не хватает денег. У тебя {fm(bal)}")
    await state.db.ensure(m.chat.id, t)
    await gdb.add_money(m.chat.id, t.id, amount)
    me = await enrich(m.chat.id, from_user(m.from_user))
    await m.answer(f"💸 {mention(me)} переводит {mention(t)} <b>{fm(amount)}</b>")


@router.message(Say("украсть", "своровать", "обокрасть", "ограбить"), GROUP)
async def cmd_steal(m: Message, args: list):
    if not await gate(m):
        return
    cid, uid = m.chat.id, m.from_user.id
    t, _ = await resolve_target(m, args)
    if not t or not t.id or t.id == uid or t.id == state.bot_id:
        return await m.reply("🥷 Ответь на сообщение того, у кого хочешь… одолжить без спроса.")
    w = await gdb.wallet(cid, uid)
    left = w["last_steal"] + STEAL_CD - time.time()
    if left > 0:
        return await m.reply(f"🥷 Полиция ещё рядом. Подожди {fmt_dur(left)}.")
    victim = await gdb.money(cid, t.id)
    mine = await gdb.money(cid, uid)
    if victim < 200:
        return await m.reply("🪙 У него почти ничего нет — не стоит рисковать.")
    await gdb.setf(cid, uid, last_steal=int(time.time()))
    me = await enrich(cid, from_user(m.from_user))
    if random.random() < 0.4:
        loot = max(1, int(victim * random.uniform(0.05, 0.2)))
        if await gdb.spend(cid, t.id, loot):
            await gdb.add_money(cid, uid, loot)
            return await m.answer(f"🥷 {mention(me)} незаметно утащил у {mention(t)} <b>{fm(loot)}</b>!")
    fine = min(mine, max(100, int(mine * 0.1)))
    if await gdb.spend(cid, uid, fine):
        await gdb.add_money(cid, t.id, fine)
    await m.answer(f"🚔 {mention(me)} попался и выплатил {mention(t)} штраф <b>{fm(fine)}</b>.")


@router.message(Say("богачи", "топ богачей", "рейтинг богатых", "топ денег"), GROUP)
async def cmd_rich(m: Message):
    rows = await gdb.richest(m.chat.id)
    if not rows:
        return await m.answer("Пока нет данных.")
    medals = ["🥇", "🥈", "🥉"] + ["▫️"] * 7
    lines = [f"{medals[i]} {mention(Target(r['user_id'], r['name'] or 'Участник', None, False, r['nick']))}"
             f" — {fm(r['money'])}" for i, r in enumerate(rows)]
    await m.answer("<b>💎 Самые богатые</b>\n" + "\n".join(lines))


# ---------- казино ----------
SYMS = ["🍒", "🍋", "🔔", "⭐", "💎", "7️⃣"]
SYM_W = [30, 25, 20, 13, 8, 4]
SYM_X = [5, 8, 12, 25, 50, 100]


@router.message(Say("слоты", "слот", "казино", "крутить", "автомат"), GROUP)
async def cmd_slots(m: Message, args: list):
    if not await gate(m) or cooling(m.from_user.id):
        return
    bet = await take_bet(m, args[0] if args else None)
    if bet is None:
        return
    reels = random.choices(range(6), SYM_W, k=3)
    line = " ".join(SYMS[i] for i in reels)
    if reels[0] == reels[1] == reels[2]:
        mult, note = SYM_X[reels[0]], "ДЖЕКПОТ!"
    elif len(set(reels)) == 2:
        mult, note = 1, "пара — ставка возвращается"
    else:
        mult, note = 0, "не повезло"
    win = bet * mult
    if win:
        await gdb.add_money(m.chat.id, m.from_user.id, win)
    bal = await gdb.money(m.chat.id, m.from_user.id)
    res = f"+{fm(win - bet)}" if win > bet else ("±0" if win == bet else f"-{fm(bet)}")
    await m.reply(f"🎰 [ {line} ]\n{note} · {res}\nБаланс: {fm(bal)}")


@router.message(Say("кости", "кубики", "кубик"), GROUP)
async def cmd_dice(m: Message, args: list):
    if not await gate(m) or cooling(m.from_user.id):
        return
    bet = await take_bet(m, args[0] if args else None)
    if bet is None:
        return
    a = (random.randint(1, 6), random.randint(1, 6))
    b = (random.randint(1, 6), random.randint(1, 6))
    if sum(a) > sum(b):
        await gdb.add_money(m.chat.id, m.from_user.id, int(bet * 1.9))
        res = f"🎉 Победа! <b>+{fm(int(bet * 0.9))}</b>"
    else:
        res = f"😔 Проигрыш: <b>-{fm(bet)}</b>"
    bal = await gdb.money(m.chat.id, m.from_user.id)
    await m.reply(f"🎲 Ты: {a[0]}+{a[1]} = <b>{sum(a)}</b>\n🤖 Бот: {b[0]}+{b[1]} = <b>{sum(b)}</b>\n"
                  f"{res}\nБаланс: {fm(bal)}")


@router.message(Say("монетка", "монета", "орел", "орёл", "решка", loose=True), GROUP)
async def cmd_coin(m: Message, phrase: str, args: list):
    if not await gate(m) or cooling(m.from_user.id):
        return
    side = {"орел": "орёл", "орёл": "орёл", "решка": "решка"}.get(phrase)
    toks = list(args)
    if not side:
        for t in toks:
            if t.lower() in ("орел", "орёл", "решка"):
                side = "орёл" if t.lower().startswith("ор") else "решка"
                toks.remove(t)
                break
    if not side:
        return await m.reply("Формат: <code>орёл 100</code> или <code>решка 100</code>")
    bet = await take_bet(m, toks[0] if toks else None)
    if bet is None:
        return
    flip = random.choices(["орёл", "решка"], [50, 50])[0]
    if random.random() < 0.03:  # небольшое преимущество казино
        flip = "решка" if side == "орёл" else "орёл"
    if flip == side:
        await gdb.add_money(m.chat.id, m.from_user.id, bet * 2)
        res = f"🎉 Выиграл <b>+{fm(bet)}</b>"
    else:
        res = f"😔 Проиграл <b>-{fm(bet)}</b>"
    bal = await gdb.money(m.chat.id, m.from_user.id)
    await m.reply(f"🪙 Выпал: <b>{flip}</b>. {res}\nБаланс: {fm(bal)}")


REDS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}


def _roulette_choice(tokens: list):
    for i, t in enumerate(tokens):
        low = t.lower()
        if low in ("красное", "красный", "красн", "к", "red"):
            return "red", i
        if low in ("черное", "чёрное", "черный", "чёрный", "ч", "black"):
            return "black", i
        if low in ("зеро", "зеленое", "зелёное", "зеленый", "0"):
            return "zero", i
        if low in ("чет", "чёт", "четное", "чётное", "чётное"):
            return "even", i
        if low in ("нечет", "нечёт", "нечетное", "нечётное"):
            return "odd", i
        if low.isdigit() and 0 <= int(low) <= 36:
            return int(low), i
    return None, -1


@router.message(Say("рулетка", "рулеточка"), GROUP)
async def cmd_roulette(m: Message, args: list):
    if not await gate(m) or cooling(m.from_user.id):
        return
    usage = ("🎡 <b>Рулетка</b>: <code>рулетка 100 красное</code> · <code>чёрное</code> · "
             "<code>чёт</code> · <code>нечет</code> · <code>число 17</code> (выплата ×36) · "
             "<code>зеро</code>")
    toks = [t for t in args if t.lower() != "число"]
    if len(toks) < 2:
        return await m.reply(usage)
    choice, _ = _roulette_choice(toks[1:])
    if choice is None:
        return await m.reply(usage)
    bet = await take_bet(m, toks[0])
    if bet is None:
        return
    n = random.randint(0, 36)
    color = "🟢" if n == 0 else ("🔴" if n in REDS else "⚫")
    win = 0
    if choice == "red" and n in REDS:
        win = bet * 2
    elif choice == "black" and n != 0 and n not in REDS:
        win = bet * 2
    elif choice == "even" and n != 0 and n % 2 == 0:
        win = bet * 2
    elif choice == "odd" and n % 2 == 1:
        win = bet * 2
    elif choice == "zero" and n == 0:
        win = bet * 36
    elif isinstance(choice, int) and choice == n:
        win = bet * 36
    if win:
        await gdb.add_money(m.chat.id, m.from_user.id, win)
    bal = await gdb.money(m.chat.id, m.from_user.id)
    res = f"🎉 <b>+{fm(win - bet)}</b>" if win else f"😔 <b>-{fm(bet)}</b>"
    await m.reply(f"🎡 Выпало: {color} <b>{n}</b>\n{res}\nБаланс: {fm(bal)}")


# ---------- блэкджек ----------
BJ: dict = {}
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["♠", "♥", "♦", "♣"]


def hand_val(hand) -> int:
    total = sum(10 if r in "JQK" else (11 if r == "A" else int(r)) for r, _ in hand)
    aces = sum(1 for r, _ in hand if r == "A")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def show(hand) -> str:
    return " ".join(f"{r}{s}" for r, s in hand)


def bj_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="➕ Ещё", callback_data="bj:hit"),
        InlineKeyboardButton(text="✋ Хватит", callback_data="bj:stand"),
    ]])


def bj_text(g, reveal=False, result=""):
    d = show(g["dealer"]) if reveal else f"{show(g['dealer'][:1])} ??"
    dv = f" ({hand_val(g['dealer'])})" if reveal else ""
    return (f"🃏 <b>Блэкджек</b> · ставка {fm(g['bet'])}\n"
            f"Ты: {show(g['player'])} ({hand_val(g['player'])})\n"
            f"Дилер: {d}{dv}" + (f"\n\n{result}" if result else ""))


@router.message(Say("блэкджек", "блекджек"), GROUP)
async def cmd_bj(m: Message, args: list):
    if not await gate(m) or cooling(m.from_user.id):
        return
    now = time.time()
    for k in [k for k, g in BJ.items() if now - g["ts"] > 3600]:
        BJ.pop(k, None)
    if any(g["uid"] == m.from_user.id and g["chat"] == m.chat.id for g in BJ.values()):
        return await m.reply("🃏 Ты уже играешь — закончи текущую партию.")
    bet = await take_bet(m, args[0] if args else None)
    if bet is None:
        return
    deck = [(r, s) for r in RANKS for s in SUITS]
    random.shuffle(deck)
    g = {"uid": m.from_user.id, "chat": m.chat.id, "bet": bet, "deck": deck, "ts": now,
         "player": [deck.pop(), deck.pop()], "dealer": [deck.pop(), deck.pop()]}
    pv, dv = hand_val(g["player"]), hand_val(g["dealer"])
    if pv == 21:
        if dv == 21:
            await gdb.add_money(m.chat.id, m.from_user.id, bet)
            return await m.reply(bj_text(g, True, "🤝 У обоих блэкджек — ставка возвращена."))
        await gdb.add_money(m.chat.id, m.from_user.id, int(bet * 2.5))
        return await m.reply(bj_text(g, True, f"🎉 Блэкджек! <b>+{fm(int(bet * 1.5))}</b>"))
    msg = await m.reply(bj_text(g), reply_markup=bj_kb())
    BJ[(m.chat.id, msg.message_id)] = g


@router.callback_query(F.data.startswith("bj:"))
async def cb_bj(cb: CallbackQuery):
    key = (cb.message.chat.id, cb.message.message_id)
    g = BJ.get(key)
    if not g:
        return await cb.answer("Партия уже закончена", show_alert=True)
    if cb.from_user.id != g["uid"]:
        return await cb.answer("Это не твоя партия 🙂", show_alert=True)
    act = cb.data.split(":")[1]
    cid, uid, bet = g["chat"], g["uid"], g["bet"]
    if act == "hit":
        g["player"].append(g["deck"].pop())
        if hand_val(g["player"]) > 21:
            BJ.pop(key, None)
            return await cb.message.edit_text(bj_text(g, True, f"💥 Перебор! <b>-{fm(bet)}</b>"))
        if hand_val(g["player"]) == 21:
            act = "stand"
        else:
            await cb.message.edit_text(bj_text(g), reply_markup=bj_kb())
            return await cb.answer()
    # stand
    while hand_val(g["dealer"]) < 17:
        g["dealer"].append(g["deck"].pop())
    pv, dv = hand_val(g["player"]), hand_val(g["dealer"])
    BJ.pop(key, None)
    if dv > 21 or pv > dv:
        await gdb.add_money(cid, uid, bet * 2)
        res = f"🎉 Победа! <b>+{fm(bet)}</b>"
    elif pv == dv:
        await gdb.add_money(cid, uid, bet)
        res = "🤝 Ничья — ставка возвращена."
    else:
        res = f"😔 Дилер выиграл. <b>-{fm(bet)}</b>"
    await cb.message.edit_text(bj_text(g, True, res))
    await cb.answer()


# ---------- магазин ----------
HOUSES = [("🏚 Комната в общежитии", 5_000), ("🏢 Квартира-студия", 25_000), ("🏠 Двушка в центре", 80_000),
          ("🏡 Дом в пригороде", 250_000), ("🏰 Особняк", 1_000_000), ("🏯 Замок", 5_000_000)]
CARS = [("🛵 Скутер", 4_000), ("🚗 Старые Жигули", 15_000), ("🚙 Б/у иномарка", 60_000),
        ("🚘 Новая Kia", 200_000), ("🏎 BMW M5", 600_000), ("⚡ Tesla", 1_200_000),
        ("🏁 Lamborghini", 5_000_000)]
PETS = [("🐹 Хомяк", 500), ("🐟 Аквариумные рыбки", 800), ("🦜 Попугай", 1_500), ("🐈 Кот", 2_000),
        ("🐕 Собака", 3_000), ("🐴 Лошадь", 40_000), ("🐉 Дракончик", 250_000)]
KINDS = {
    "house": ("🏠 Недвижимость", HOUSES, ("дом", "квартира", "квартиру", "недвижимость", "жильё", "жилье")),
    "car": ("🚗 Транспорт", CARS, ("машина", "машину", "авто", "транспорт")),
    "pet": ("🐾 Питомцы", PETS, ("питомец", "питомца", "животное", "питомцы", "животные")),
}
SELL_RATE = 0.7
RENT_RATE = 0.004  # доход в час от цены дома


def kind_of(word: str):
    w = word.lower()
    for k, (_, _, aliases) in KINDS.items():
        if w in aliases or w in (k, k + "s", "дома", "машины"):
            return k
    return None


def catalog(kind: str) -> str:
    title, items, _ = KINDS[kind]
    lines = [f"{i + 1}. {n} — {fm(p)}" for i, (n, p) in enumerate(items)]
    extra = f"\n💰 Дом приносит аренду: {RENT_RATE * 100:.1f}% цены в час (<code>собрать доход</code>)" if kind == "house" else ""
    return f"<b>{title}</b>\n" + "\n".join(lines) + extra


@router.message(Say("магазин", "шоп", "каталог", "рынок", loose=True), GROUP)
async def cmd_shop(m: Message, args: list):
    if not await gate(m):
        return
    kind = kind_of(args[0]) if args else None
    if kind:
        return await m.reply(catalog(kind) + f"\n\nКупить: <code>купить {args[0].lower()} НОМЕР</code>")
    await m.reply("🛍 <b>Магазин</b>\n<code>магазин дома</code> · <code>магазин машины</code> · "
                  "<code>магазин питомцы</code>\n\nКупить: <code>купить дом 3</code>, "
                  "<code>купить машину 2</code>, <code>купить питомца 4</code>\n"
                  "Продать (70% цены): <code>продать дом</code>\nВсё моё: <code>имущество</code>")


@router.message(Say("купить", "покупка", "приобрести", loose=True), GROUP, F.text.regexp(r"(?i)^[/!.]?\S+\s+(дом|квартир|недвиж|жил|машин|авто|транспорт|питом|животн)"))
async def cmd_buy(m: Message, args: list):
    if not await gate(m):
        return
    kind = kind_of(args[0]) if args else None
    if not kind:
        return await m.reply("Что купить? <code>магазин</code>")
    _, items, _ = KINDS[kind]
    if len(args) < 2 or not args[1].isdigit() or not 1 <= int(args[1]) <= len(items):
        return await m.reply(catalog(kind) + f"\n\nУкажи номер: <code>купить {args[0].lower()} 2</code>")
    idx = int(args[1]) - 1
    name, price = items[idx]
    cid, uid = m.chat.id, m.from_user.id
    cur = await gdb.asset(cid, uid, kind)
    cost, note = price, ""
    if cur is not None:
        old_name, old_price = items[cur]
        if cur == idx:
            return await m.reply("У тебя это уже есть 🙂")
        if old_price >= price:
            return await m.reply(f"У тебя уже есть «{old_name}» — не хуже. Сначала продай: "
                                 f"<code>продать {args[0].lower()}</code>")
        cost = price - int(old_price * SELL_RATE)
        note = f"\n♻️ Старое «{old_name}» сдано в зачёт ({fm(int(old_price * SELL_RATE))})."
    if not await gdb.spend(cid, uid, cost):
        bal = await gdb.money(cid, uid)
        return await m.reply(f"💸 Не хватает: нужно {fm(cost)}, у тебя {fm(bal)}")
    await gdb.set_asset(cid, uid, kind, idx)
    if kind == "house":
        await gdb.setf(cid, uid, last_income=int(time.time()))
    await m.reply(f"🎉 Покупка: <b>{name}</b> за {fm(cost)}!{note}")


@router.message(Say("продать", loose=True), GROUP, F.text.regexp(r"(?i)^[/!.]?\S+\s+(дом|квартир|недвиж|жил|машин|авто|транспорт|питом|животн)"))
async def cmd_sell(m: Message, args: list):
    kind = kind_of(args[0]) if args else None
    if not kind:
        return
    cid, uid = m.chat.id, m.from_user.id
    cur = await gdb.asset(cid, uid, kind)
    if cur is None:
        return await m.reply("Тебе нечего продавать.")
    name, price = KINDS[kind][1][cur]
    back = int(price * SELL_RATE)
    await gdb.del_asset(cid, uid, kind)
    await gdb.add_money(cid, uid, back)
    await m.reply(f"🤝 Продано: {name} за {fm(back)}")


async def assets_text(cid: int, uid: int) -> str:
    out = []
    for kind, (title, items, _) in KINDS.items():
        idx = await gdb.asset(cid, uid, kind)
        out.append(f"{title.split()[0]} {items[idx][0] if idx is not None else '—'}")
    return "\n".join(out)


@router.message(Say("имущество", "мои вещи", "инвентарь", "мой дом", "моя машина", "мои питомцы"), GROUP)
async def cmd_assets(m: Message):
    if not await gate(m):
        return
    bal = await gdb.money(m.chat.id, m.from_user.id)
    await m.reply(f"🎒 <b>Твоё имущество</b>\n{await assets_text(m.chat.id, m.from_user.id)}\n\n"
                  f"👛 {fm(bal)}")


@router.message(Say("собрать доход", "доход", "аренда", "собрать аренду", "рента"), GROUP)
async def cmd_income(m: Message):
    if not await gate(m):
        return
    cid, uid = m.chat.id, m.from_user.id
    house = await gdb.asset(cid, uid, "house")
    if house is None:
        return await m.reply("🏠 Нет дома — нет аренды. <code>магазин дома</code>")
    w = await gdb.wallet(cid, uid)
    now = int(time.time())
    last = w["last_income"] or now
    hours = min((now - last) / 3600, INCOME_CAP_H)
    amount = int(HOUSES[house][1] * RENT_RATE * hours)
    await gdb.setf(cid, uid, last_income=now)
    if amount < 1:
        return await m.reply("🏠 Пока копится — загляни позже.")
    await gdb.add_money(cid, uid, amount)
    await m.reply(f"🏠 Аренда за {hours:.1f} ч: <b>+{fm(amount)}</b> (копится максимум {INCOME_CAP_H} ч)")
