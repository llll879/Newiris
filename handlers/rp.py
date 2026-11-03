"""РП-команды: обычные, 18+ (с согласием адресата) и свои для каждого чата."""
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import state
from filters import Cmd, parse_head
from rp_data import ADULT, ADULT_ITEMS, REGULAR, REGULAR_ITEMS, BUILTIN_PHRASES, render
from utils import (
    GROUP_TYPES, enrich, esc, from_user, mention, name_of, require, resolve_target, temp,
)

router = Router()


class RpFilter(BaseFilter):
    """Ищет РП-фразу в начале сообщения (в том числе многословную)."""

    async def __call__(self, message: Message):
        if message.chat.type not in GROUP_TYPES:
            return False
        text = (message.text or "").strip()
        if not text:
            return False
        prefixed = text[0] in "/!."
        if prefixed:
            if parse_head(text) is None:  # команда адресована другому боту
                return False
            text = text[1:]
            first, sep, tail = text.partition(" ")
            text = (first.split("@")[0] + sep + tail).strip()
        low = text.lower()
        crp = await state.db.crp(message.chat.id)
        phrases = sorted(set(BUILTIN_PHRASES) | set(crp), key=len, reverse=True)
        for ph in phrases:
            if low == ph or low.startswith(ph + " ") or low.startswith(ph + "\n"):
                rest = text[len(ph):].strip()
                if ph in crp:
                    cid, tpl, adult = crp[ph]
                    kind = ("a", f"c{cid}", tpl) if adult else ("r", None, tpl)
                elif ph in ADULT and ph not in REGULAR:
                    idx = ADULT[ph]
                    kind = ("a", str(idx), ADULT_ITEMS[idx][1])
                else:
                    kind = ("r", None, REGULAR[ph])
                return {"rp_kind": kind, "rp_key": ph, "rp_rest": rest, "rp_prefixed": prefixed}
        return False


def _consent_kb(a: int, b: int, ref: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Согласен(на)", callback_data=f"rp18:{a}:{b}:{ref}:y"),
        InlineKeyboardButton(text="❌ Отказаться", callback_data=f"rp18:{a}:{b}:{ref}:n"),
    ]])


@router.message(RpFilter())
async def on_rp(m: Message, rp_kind, rp_key: str, rp_rest: str, rp_prefixed: bool):
    s = await state.db.settings(m.chat.id)
    if not s["rp"] or not m.from_user:
        return
    kind, ref, tpl = rp_kind
    args = rp_rest.split()
    target, rest = await resolve_target(m, args, allow_id=False)
    if not target:
        if rp_prefixed:
            await m.reply("↩️ Ответь на сообщение того, к кому это относится, или укажи @username.")
        return
    actor = await enrich(m.chat.id, from_user(m.from_user))
    await state.db.ensure(m.chat.id, actor)
    reason = " ".join(rest)

    if target.id == state.bot_id:
        return await m.reply("😳 Ой, меня трогать не надо!")

    if kind == "r":
        text = render(tpl, mention(actor), mention(target), reason)
        return await m.answer(text)

    # ---- 18+ ----
    if not s["rp18"]:
        temp(await m.reply("🔞 В этом чате 18+ РП выключены. Админ может включить: /rp18 on"), 10)
        return
    if not target.id:
        return await m.reply("↩️ Для 18+ команд нужно ответить на сообщение адресата — "
                             "он должен подтвердить согласие кнопкой.")
    if target.id == actor.id:
        return await m.reply("😅 Это лучше делать с кем-то другим.")
    await m.answer(
        f"🔞 {mention(actor)} предлагает {mention(target)}: <b>{esc(rp_key)}</b>\n"
        f"{mention(target)}, ты согласен(на)? Решение за тобой.",
        reply_markup=_consent_kb(actor.id, target.id, ref),
    )


@router.callback_query(F.data.startswith("rp18:"))
async def cb_rp18(cb: CallbackQuery):
    try:
        _, a, b, ref, ans = cb.data.split(":")
        a, b = int(a), int(b)
    except ValueError:
        return await cb.answer()
    chat_id = cb.message.chat.id
    uid = cb.from_user.id
    if uid == a and ans == "n":
        await cb.message.edit_text("🚫 Предложение отменено.")
        return await cb.answer()
    if uid != b:
        return await cb.answer("Это решает только адресат 🙂", show_alert=True)
    if ans == "n":
        await cb.message.edit_text(f"❌ {await name_of(chat_id, b)} вежливо отказывается.")
        return await cb.answer()
    s = await state.db.settings(chat_id)
    if not s["rp18"]:
        return await cb.answer("18+ РП сейчас выключены", show_alert=True)
    if ref.startswith("c"):
        got = await state.db.crp_get(chat_id, int(ref[1:]))
        if not got:
            return await cb.answer("Команда удалена", show_alert=True)
        tpl = got[0]
    else:
        try:
            tpl = ADULT_ITEMS[int(ref)][1]
        except (ValueError, IndexError):
            return await cb.answer()
    text = render(tpl, await name_of(chat_id, a), await name_of(chat_id, b))
    try:
        await cb.message.edit_text(text)
    except TelegramAPIError:
        pass
    await cb.answer()


# ---------- управление ----------
@router.message(Cmd("rp18", "рп18", plain="none"))
async def cmd_rp18(m: Message, prefixed: bool, args: list):
    if await require(m, 5, prefixed) is None:
        return
    s = await state.db.settings(m.chat.id)
    if not args or args[0].lower() not in ("on", "off", "вкл", "выкл"):
        return await m.answer(f"🔞 18+ РП сейчас: <b>{'включены' if s['rp18'] else 'выключены'}</b>.\n"
                              "Включить: <code>/rp18 on</code> · выключить: <code>/rp18 off</code>")
    on = args[0].lower() in ("on", "вкл")
    await state.db.set(m.chat.id, "rp18", on)
    if on:
        await m.answer("🔞 18+ РП включены. Используйте только в чатах, где <b>все участники "
                       "совершеннолетние</b>. Команда выполняется только после согласия адресата.")
    else:
        await m.answer("✅ 18+ РП выключены.")


@router.message(Cmd("rp", "рп", "рпкоманды", "rplist"))
async def cmd_rplist(m: Message):
    if m.chat.type not in GROUP_TYPES:
        return
    s = await state.db.settings(m.chat.id)
    crp = await state.db.crp(m.chat.id)
    reg = ", ".join(items[0][0] for items in REGULAR_ITEMS)
    text = ("🎭 <b>РП-команды</b>\nОтветь на сообщение человека (или укажи @username) "
            "и напиши команду. Можно добавить слова: <i>обнять крепко-крепко</i>.\n\n" + esc(reg))
    own = [k for k, v in crp.items() if not v[2]]
    if own:
        text += "\n\n<b>Свои:</b> " + esc(", ".join(own))
    if s["rp18"]:
        adult = ", ".join(items[0][0] for items in ADULT_ITEMS)
        text += "\n\n🔞 <b>18+ (нужно согласие адресата):</b>\n" + esc(adult)
        own18 = [k for k, v in crp.items() if v[2]]
        if own18:
            text += "\n<b>Свои 18+:</b> " + esc(", ".join(own18))
    else:
        text += "\n\n🔞 18+ команды выключены (админ: /rp18 on)."
    await m.answer(text)


@router.message(Cmd("addrp", "добавитьрп", plain="none"))
async def cmd_addrp(m: Message, prefixed: bool, arg: str):
    if await require(m, 5, prefixed) is None:
        return
    adult = False
    body = arg
    if body.lower().startswith("18 ") or body.startswith("🔞"):
        adult = True
        body = body.split(maxsplit=1)[1] if len(body.split(maxsplit=1)) > 1 else ""
    if "|" not in body:
        return await m.reply(
            "Формат: <code>/addrp команда | шаблон</code>\n"
            "В шаблоне: {a} — кто делает, {b} — на кого. Пример:\n"
            "<code>/addrp погрозить пальцем | ☝️ {a} грозит пальцем {b}</code>\n"
            "Для 18+: <code>/addrp 18 команда | шаблон</code> (нужно включить /rp18 on).")
    cmd, tpl = (x.strip() for x in body.split("|", 1))
    cmd = cmd.lower()
    if not (2 <= len(cmd) <= 30) or cmd.startswith(("/", "!", ".")):
        return await m.reply("Команда: от 2 до 30 символов, без префикса.")
    if "{a}" not in tpl or "{b}" not in tpl or len(tpl) > 300:
        return await m.reply("В шаблоне должны быть {a} и {b}, длина до 300 символов.")
    if len(await state.db.crp(m.chat.id)) >= 60 and cmd not in await state.db.crp(m.chat.id):
        return await m.reply("Достигнут лимит: 60 своих команд на чат.")
    await state.db.crp_add(m.chat.id, cmd, tpl, adult)
    await m.answer(f"✅ Команда <b>{esc(cmd)}</b>{' (18+)' if adult else ''} добавлена.")


@router.message(Cmd("delrp", "удалитьрп", plain="none"))
async def cmd_delrp(m: Message, prefixed: bool, arg: str):
    if await require(m, 5, prefixed) is None:
        return
    ok = await state.db.crp_del(m.chat.id, arg.lower().strip())
    await m.answer("🗑 Удалено." if ok else "Такой своей команды нет.")
