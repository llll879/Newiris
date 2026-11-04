"""Режим разработчика: ИИ с инструментами правит собственный код и настройки по просьбе владельца.

Доступен только владельцам из OWNER_IDS и только в личке (см. handlers/dev.py).
Код стейджится и применяется после подтверждения владельцем; настройки ИИ меняются сразу.
"""
import logging
from pathlib import Path

import config
import gdb
import gemini
import selfedit
from selfedit import SelfEditError

log = logging.getLogger("devagent")

MAX_STEPS = 16
READ_LINES = 300
READ_CHARS = 24_000

SYSTEM = f"""Ты — {{name}}, и сейчас ты в режиме разработчика: владелец бота просит тебя изменить САМУ СЕБЯ \
(свой код, промт, настройки). Ты работаешь как аккуратный инженер.

Проект: Telegram-бот на Python 3.12, aiogram 3, aiosqlite, ИИ через Google Gemini REST API.
Ключевые места: handlers/ai.py (чат с ИИ, system_prompt(), DEFAULT_PERSONA, команды «ии ...»), gemini.py \
(вызов модели), config.py (переменные окружения и DEFAULTS настроек чатов), db.py и gdb.py (SQLite), \
handlers/*.py (модерация, экономика, семья, РП и т.д.), app.py (сборка бота и запуск).

Инструменты:
- list_files, read_file, search_code — изучай код ПЕРЕД правкой. Не угадывай содержимое файла.
- replace_in_file — точечная правка (предпочтительно). write_file — для новых файлов и полной перезаписи.
- Правки кода НЕ применяются сразу: они копятся, владелец видит diff и подтверждает, потом нужен перезапуск.
- get_settings / set_setting — настройки ИИ, применяются мгновенно без перезапуска: persona (характер по \
умолчанию), extra (дополнительные указания в системный промт), ai_name, model, temperature, max_tokens. \
Если просят поменять характер, тон, манеру речи или правила общения — используй их, а не код. \
Перед изменением extra/persona вызови get_settings и не затри существующий текст, если не просили.
- staged_changes, discard_changes — посмотреть/сбросить накопленные правки.

Правила:
1. Делай минимально необходимые правки, сохраняй стиль и язык комментариев проекта (русский).
2. Следи за синтаксисом, отступами и импортами; после правки сверяйся с кодом вокруг. Код проверяется \
компилятором, но логику проверяешь ты.
3. Нельзя менять: main.py, selfedit.py, devagent.py, handlers/dev.py (канал управления), а также .env, \
базу данных и секреты. Не пытайся читать токены и ключи, не добавляй отправку данных на сторонние сервера, \
не добавляй exec/eval над пользовательским вводом.
4. Не снимай защитные ограничения: правила про несовершеннолетних, сексуальное насилие, согласие и \
безопасность пользователей в промте и коде остаются.
5. Если просьба неясна или опасна для бота (может его сломать) — коротко уточни или предупреди.
6. Закончив, ответь по-русски в 2–5 коротких предложениях: что изменено, нужен ли перезапуск, что проверить. \
Без эмодзи и без списков. Не вставляй длинный код в ответ."""

# ---------------- объявления функций для Gemini ----------------
_S = {"type": "string"}


def _fn(name, desc, props=None, required=None):
    d = {"name": name, "description": desc}
    if props:
        d["parameters"] = {"type": "object", "properties": props, "required": required or list(props)}
    return d


TOOLS = [{"functionDeclarations": [
    _fn("list_files", "Список файлов проекта, которые можно читать и править, с размерами."),
    _fn("read_file", "Читает файл с нумерацией строк. Длинные файлы читай частями.",
        {"path": _S, "start": {"type": "integer", "description": "первая строка (с 1)"},
         "end": {"type": "integer", "description": "последняя строка"}}, ["path"]),
    _fn("search_code", "Поиск подстроки (без учёта регистра) по всем файлам проекта.", {"query": _S}),
    _fn("replace_in_file", "Заменяет фрагмент old на new в файле. old должен встречаться ровно один раз "
        "(или поставь replace_all). Копируй old точно, с отступами.",
        {"path": _S, "old": _S, "new": _S, "replace_all": {"type": "boolean"}}, ["path", "old", "new"]),
    _fn("write_file", "Создаёт новый файл или полностью перезаписывает существующий.",
        {"path": _S, "content": _S}),
    _fn("staged_changes", "Показывает накопленные, но ещё не применённые правки."),
    _fn("discard_changes", "Сбрасывает все накопленные правки кода."),
    _fn("get_settings", "Текущие переопределённые настройки ИИ и список доступных ключей."),
    _fn("set_setting", "Меняет настройку ИИ на лету. Пустое value сбрасывает к значению по умолчанию.",
        {"key": {"type": "string", "enum": list(selfedit.SETTING_KEYS)}, "value": _S}),
]}]


# ---------------- реализации инструментов ----------------
def _list_files(stage, **_):
    rows = selfedit.list_files(stage)
    prot = selfedit.PROTECTED
    return {"files": [f"{r} ({s} байт){' [новое/изменено]' if st else ''}{' [только чтение]' if r in prot else ''}"
                      for r, s, st in rows]}


def _read_file(stage, path, start=1, end=None, **_):
    text = selfedit.read_text(path, stage)
    if text is None:
        raise SelfEditError(f"файла «{path}» нет")
    lines = text.splitlines()
    total = len(lines)
    start = max(1, int(start or 1))
    end = min(total, int(end) if end else start + READ_LINES - 1, start + READ_LINES - 1)
    body = "\n".join(f"{i:>4}| {lines[i - 1]}" for i in range(start, end + 1))
    if len(body) > READ_CHARS:
        body = body[:READ_CHARS] + "\n... (обрезано, читай меньшими кусками)"
    note = f"строки {start}–{end} из {total}" + ("; продолжение: start=" + str(end + 1) if end < total else "")
    return {"file": selfedit.norm(path), "info": note, "content": body}


def _search_code(stage, query, **_):
    q = (query or "").lower().strip()
    if len(q) < 2:
        raise SelfEditError("запрос слишком короткий")
    hits = []
    for rel, _size, _st in selfedit.list_files(stage):
        text = selfedit.read_text(rel, stage) or ""
        for i, line in enumerate(text.splitlines(), 1):
            if q in line.lower():
                hits.append(f"{rel}:{i}: {line.strip()[:160]}")
                if len(hits) >= 40:
                    return {"hits": hits, "info": "показаны первые 40 совпадений"}
    return {"hits": hits or ["ничего не найдено"]}


def _replace(stage, path, old, new, replace_all=False, **_):
    return {"result": selfedit.stage_replace(stage, path, old, new, bool(replace_all))}


def _write(stage, path, content, **_):
    return {"result": selfedit.stage_write(stage, path, content)}


def _staged(stage, **_):
    if not stage.files:
        return {"staged": "нет накопленных правок"}
    return {"staged": [f"{r}: +{p} −{m}{' (новый)' if new else ''}" for r, p, m, new in selfedit.diff_stats(stage)]}


def _discard(stage, **_):
    n = len(stage.files)
    stage.files.clear()
    return {"result": f"сброшено файлов: {n}"}


def _get_settings(stage, **_):
    return {"overrides": selfedit.settings_snapshot(),
            "keys": {k: d for k, (_t, d) in selfedit.SETTING_KEYS.items()},
            "defaults": {"ai_name": config.AI_NAME, "model": config.GEMINI_MODEL}}


def _set_setting(stage, key, value="", **_):
    return {"result": selfedit.set_setting(key, value), "key": key}


IMPL = {
    "list_files": _list_files, "read_file": _read_file, "search_code": _search_code,
    "replace_in_file": _replace, "write_file": _write, "staged_changes": _staged,
    "discard_changes": _discard, "get_settings": _get_settings, "set_setting": _set_setting,
}


def describe(name: str, args: dict) -> str:
    p = str(args.get("path", ""))[:40]
    return {
        "list_files": "📂 смотрю список файлов",
        "read_file": f"📖 читаю {p}",
        "search_code": f"🔎 ищу «{str(args.get('query', ''))[:30]}»",
        "replace_in_file": f"✏️ правлю {p}",
        "write_file": f"📝 пишу {p}",
        "staged_changes": "🧾 проверяю накопленные правки",
        "discard_changes": "🗑 сбрасываю правки",
        "get_settings": "⚙️ смотрю настройки",
        "set_setting": f"⚙️ меняю настройку {args.get('key', '')}",
    }.get(name, f"🔧 {name}")


# ---------------- цикл агента ----------------
async def run(uid: int, request: str, name: str, on_step=None) -> str:
    """Выполняет просьбу владельца. Возвращает итоговый текст; правки кода остаются в стейдже."""
    scope = f"dev:{uid}"
    stage = selfedit.get_stage(uid)
    history = await gdb.hist_get(scope, 12)
    while history and history[0][0] != "user":
        history.pop(0)
    contents = [{"role": r, "parts": [{"text": t}]} for r, t in history]
    contents.append({"role": "user", "parts": [{"text": request}]})
    system = SYSTEM.replace("{name}", selfedit.setting("ai_name") or name)

    final = ""
    for _ in range(MAX_STEPS):
        content = await gemini.chat(system, contents, TOOLS)
        if content is None:
            final = "Ответ заблокирован фильтром Gemini. Переформулируй просьбу."
            break
        parts = content.get("parts") or []
        calls = [p["functionCall"] for p in parts if "functionCall" in p]
        if not calls:
            final = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
            break
        contents.append(content)  # целиком, чтобы сохранить служебные поля модели
        answers = []
        for c in calls:
            fname, args = c.get("name", ""), c.get("args") or {}
            if on_step:
                await on_step(describe(fname, args))
            fn = IMPL.get(fname)
            try:
                if not fn:
                    raise SelfEditError(f"неизвестный инструмент «{fname}»")
                result = fn(stage, **args)
            except SelfEditError as e:
                result = {"error": str(e)}
            except TypeError as e:
                result = {"error": f"неверные аргументы: {e}"}
            except Exception as e:  # noqa: BLE001
                log.exception("tool %s", fname)
                result = {"error": f"внутренняя ошибка: {e}"}
            answers.append({"functionResponse": {"name": fname, "response": result}})
        contents.append({"role": "user", "parts": answers})
    else:
        final = "Я упёрлась в лимит шагов. Сделанные правки сохранены в очереди — посмотри /diff и скажи, что дальше."

    final = final or "Готово."
    await gdb.hist_add(scope, "user", request)
    await gdb.hist_add(scope, "model", final)
    return final
