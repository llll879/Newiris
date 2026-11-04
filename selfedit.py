"""Самоправка бота: безопасное изменение собственного кода и настроек ИИ по просьбе владельца.

Как это устроено
----------------
* Правки сначала «стейджатся» (копятся в памяти), владелец видит diff и подтверждает.
* Применённые файлы пишутся на диск и дублируются в «оверлей» (data/selfedit/overlay),
  который main.py накладывает на код при каждом старте. Так правки переживают перезапуск
  (если диск постоянный, см. README), а не только текущий процесс.
* Перед каждой правкой старая версия сохраняется в истории, правку можно откатить.
* Сторож запуска: если после правки бот не дожил до «здорового» состояния,
  при следующем старте неподтверждённые правки откатываются автоматически.
* Настройки ИИ (промт, имя, модель, температура) меняются на лету, без перезапуска.

ВАЖНО: этот модуль не импортирует config и другие модули проекта — он работает
до их загрузки и защищён от самоправки вместе с main.py.
"""
import asyncio
import difflib
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

log = logging.getLogger("selfedit")

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv("DB_PATH", "data/bot.db")).parent / "selfedit"
OVERLAY = DATA / "overlay"
HISTORY = DATA / "history"
BASE_FILE = DATA / "base.json"
GUARD_FILE = DATA / "guard.json"
SETTINGS_FILE = DATA / "settings.json"

# Канал управления: ИИ может их читать, но не менять (иначе одна ошибка = потеря контроля)
PROTECTED = {"main.py", "selfedit.py", "devagent.py", "handlers/dev.py"}
EDITABLE_EXT = {".py", ".md", ".txt", ".yaml", ".yml", ".json", ".example"}
DENY_PARTS = {".git", "data", "__pycache__", "venv", ".venv", "node_modules"}
MAX_FILE = 400_000
HEALTHY_AFTER = 45  # сек работы, после которых запуск считается удачным

AUTO_APPLY = os.getenv("SELFEDIT_AUTO", "0").strip() in ("1", "true", "yes")


class SelfEditError(Exception):
    pass


# ====================== файлы и пути ======================
def _sha(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha1(data).hexdigest()


def norm(rel: str) -> str:
    rel = (rel or "").strip().replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    rel = rel.strip("/")
    if not rel:
        raise SelfEditError("пустой путь")
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts:
        raise SelfEditError("путь должен быть внутри проекта")
    if any(part in DENY_PARTS for part in p.parts):
        raise SelfEditError(f"доступ к «{rel}» закрыт")
    if p.name.startswith(".env") and p.name != ".env.example":
        raise SelfEditError("файлы с секретами недоступны")
    if p.suffix.lower() in (".db", ".sqlite", ".pyc") or ".db-" in p.name:
        raise SelfEditError("базы данных недоступны")
    target = (ROOT / p).resolve()
    if ROOT not in target.parents:
        raise SelfEditError("путь должен быть внутри проекта")
    return p.as_posix()


def check_editable(rel: str) -> str:
    rel = norm(rel)
    if rel in PROTECTED:
        raise SelfEditError(f"«{rel}» защищён от самоправки (канал управления). Его может менять только человек.")
    p = Path(rel)
    if p.suffix.lower() not in EDITABLE_EXT and p.name != ".gitignore":
        raise SelfEditError(f"тип файла «{p.suffix}» менять нельзя")
    return rel


def _disk_bytes(rel: str) -> bytes | None:
    f = ROOT / rel
    return f.read_bytes() if f.is_file() else None


def _write_disk(rel: str, content: str | None):
    f = ROOT / rel
    if content is None:
        if f.exists():
            f.unlink()
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(content.encode("utf-8"))
    if f.parent != ROOT and rel.startswith("handlers/"):
        (f.parent / "__init__.py").touch(exist_ok=True)


# ====================== состояние на диске ======================
def _jload(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def _jsave(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, path)


def _guard() -> dict:
    return _jload(GUARD_FILE, {"booting": False, "unconfirmed": [], "notice": []})


def _guard_save(g: dict):
    _jsave(GUARD_FILE, g)


def _set_state(rel: str, content: str | None, base: dict):
    """Ставит файл в заданное состояние на диске и в оверлее."""
    _write_disk(rel, content)
    ov = OVERLAY / rel
    b = base.get(rel, "__none__")
    back_to_base = (b != "__none__") and (
        (content is None and b is None) or (content is not None and _sha(content.encode()) == b))
    if content is None or back_to_base:
        if ov.exists():
            ov.unlink()
        base.pop(rel, None)
    else:
        ov.parent.mkdir(parents=True, exist_ok=True)
        ov.write_bytes(content.encode("utf-8"))


# ====================== хранение в PostgreSQL (Neon) ======================
# Если задан DATABASE_URL, папка data/selfedit зеркалируется в таблицу selfedit_files:
# на Render диск временный, а правки кода, история и настройки ИИ должны переживать деплой.
_TABLE = "CREATE TABLE IF NOT EXISTS selfedit_files(path TEXT PRIMARY KEY, content TEXT NOT NULL, ts BIGINT)"
_synced: dict = {}   # путь -> sha последней выгруженной/загруженной версии


def clean_dsn(url: str) -> str:
    """Приводит строку подключения (в т.ч. скопированную как `psql '...'`) к виду, понятному asyncpg."""
    m = re.search(r"postgres(?:ql)?://[^\s'\"]+", url or "")
    if not m:
        raise ValueError("DATABASE_URL должен начинаться с postgresql://")
    u = urlsplit(m.group(0).replace("postgres://", "postgresql://", 1))
    # asyncpg не знает эти параметры из строки Neon и принял бы их за настройки сервера
    drop = {"channel_binding", "pgbouncer", "connect_timeout", "options"}
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k not in drop]
    if not any(k == "sslmode" for k, _ in q) and (u.hostname or "").endswith("neon.tech"):
        q.append(("sslmode", "require"))
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(q), ""))


def _dsn() -> str | None:
    url = os.getenv("DATABASE_URL", "").strip()
    try:
        return clean_dsn(url) if url else None
    except ValueError:
        return None


def _scan() -> dict:
    out = {}
    if DATA.exists():
        for f in DATA.rglob("*"):
            if f.is_file() and not f.name.endswith(".tmp"):
                b = f.read_bytes()
                out[f.relative_to(DATA).as_posix()] = (hashlib.sha1(b).hexdigest(), b.decode("utf-8", "replace"))
    return out


def _pull():
    """Перед запуском: если в БД уже есть сохранённые правки, они главнее локальной папки."""
    dsn = _dsn()
    if not dsn:
        return
    try:
        import asyncpg

        async def go():
            conn = await asyncpg.connect(dsn, statement_cache_size=0, timeout=60)
            try:
                await conn.execute(_TABLE)
                return await conn.fetch("SELECT path, content FROM selfedit_files")
            finally:
                await conn.close()

        rows = asyncio.run(go())
    except Exception as e:  # noqa: BLE001  — без БД работаем с локальными файлами
        print(f"selfedit: не удалось загрузить правки из БД: {e}", file=sys.stderr)
        return
    if not rows:
        return  # БД пуста: локальное (если есть) выгрузит mirror_loop
    shutil.rmtree(DATA, ignore_errors=True)
    _synced.clear()
    for r in rows:
        rel = r["path"]
        if ".." in Path(rel).parts or Path(rel).is_absolute():
            continue
        f = DATA / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(r["content"].encode("utf-8"))
        _synced[rel] = hashlib.sha1(r["content"].encode("utf-8")).hexdigest()


async def _push(dsn: str):
    cur = _scan()
    changed = {p: c for p, (sha, c) in cur.items() if _synced.get(p) != sha}
    gone = [p for p in _synced if p not in cur]
    if not changed and not gone:
        return
    import asyncpg
    conn = await asyncpg.connect(dsn, statement_cache_size=0, timeout=60)
    try:
        await conn.execute(_TABLE)
        async with conn.transaction():
            for p, c in changed.items():
                await conn.execute(
                    "INSERT INTO selfedit_files(path, content, ts) VALUES($1, $2, $3) "
                    "ON CONFLICT(path) DO UPDATE SET content=excluded.content, ts=excluded.ts",
                    p, c, int(time.time()))
            if gone:
                await conn.execute("DELETE FROM selfedit_files WHERE path = ANY($1::text[])", gone)
    finally:
        await conn.close()
    _synced.clear()
    _synced.update({p: sha for p, (sha, _c) in cur.items()})


async def mirror_loop(interval: int = 5):
    """Фоновая выгрузка изменений в БД (сама ходит в сеть, только когда файлы изменились)."""
    dsn = _dsn()
    if not dsn:
        return
    log.info("Правки и настройки ИИ хранятся в PostgreSQL")
    while True:
        await asyncio.sleep(interval)
        try:
            await _push(dsn)
        except Exception as e:  # noqa: BLE001
            log.warning("selfedit: выгрузка в БД не удалась: %s", e)


async def flush():
    dsn = _dsn()
    if dsn:
        try:
            await _push(dsn)
        except Exception as e:  # noqa: BLE001
            log.warning("selfedit: flush не удался: %s", e)


# ====================== запуск: оверлей и сторож ======================
_boot_ids: set = set()


def boot_apply():
    """Вызывается из main.py до импорта остального кода."""
    try:
        _pull()
        DATA.mkdir(parents=True, exist_ok=True)
        g = _guard()
        base = _jload(BASE_FILE, {})
        notes = list(g.get("notice", []))

        # прошлый запуск после правки не стал здоровым -> откат неподтверждённых правок
        if g.get("booting") and g.get("unconfirmed"):
            for bid in reversed(g["unconfirmed"]):
                try:
                    done = _rollback(bid, base)
                    notes.append(f"⚠️ Бот не запустился после правки {bid} — она автоматически откачена ({', '.join(done)}).")
                except Exception as e:  # noqa: BLE001
                    notes.append(f"⚠️ Не удалось откатить {bid}: {e}")
            g["unconfirmed"] = []

        # накладываем оверлей на свежий код
        if OVERLAY.exists():
            for f in sorted(OVERLAY.rglob("*")):
                if not f.is_file():
                    continue
                rel = f.relative_to(OVERLAY).as_posix()
                new = f.read_bytes()
                try:
                    if rel.endswith(".py"):
                        compile(new, rel, "exec")
                    cur = _disk_bytes(rel)
                    b = base.get(rel, "__none__")
                    if cur is not None and b not in ("__none__", None) and _sha(cur) not in (b, _sha(new)):
                        raise SelfEditError("файл в репозитории изменился после правки")
                    if cur != new:
                        _write_disk(rel, new.decode("utf-8"))
                except Exception as e:  # noqa: BLE001
                    notes.append(f"⚠️ Правка «{rel}» пропущена и удалена: {e}")
                    f.unlink(missing_ok=True)
                    base.pop(rel, None)

        g["booting"] = True
        _boot_ids.update(g.get("unconfirmed", []))
        g["notice"] = notes
        _guard_save(g)
        _jsave(BASE_FILE, base)
    except Exception as e:  # noqa: BLE001  — самоправка не должна ломать запуск
        print(f"selfedit.boot_apply: {e}", file=sys.stderr)


async def confirm_boot(delay: int = HEALTHY_AFTER):
    """Через delay секунд работы считаем запуск удачным и подтверждаем правки, действовавшие при старте."""
    await asyncio.sleep(delay)
    g = _guard()
    g["booting"] = False
    g["unconfirmed"] = [x for x in g.get("unconfirmed", []) if x not in _boot_ids]
    _guard_save(g)
    log.info("Запуск подтверждён, правки зафиксированы")


async def announce(bot, owner_ids):
    """Сообщает владельцам об автооткатах и пропущенных правках."""
    g = _guard()
    notes = g.get("notice") or []
    if not notes:
        return
    for uid in owner_ids:
        try:
            await bot.send_message(uid, "🛠 <b>Самоправка</b>\n" + "\n".join(notes))
        except Exception:  # noqa: BLE001
            pass
    g["notice"] = []
    _guard_save(g)


async def restart():
    """Перезапускает процесс на месте (после сохранения БД)."""
    log.warning("Перезапуск процесса")
    try:
        import state  # локально: модуль не должен зависеть от проекта при загрузке
        if state.db and state.db.conn:
            await state.db.conn.commit()
    except Exception:  # noqa: BLE001
        pass
    await flush()
    await asyncio.sleep(1)
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, *sys.argv])


# ====================== стейджинг правок ======================
@dataclass
class Stage:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    files: dict = field(default_factory=dict)   # rel -> новое содержимое
    note: str = ""


STAGES: dict[int, Stage] = {}


def get_stage(uid: int) -> Stage:
    return STAGES.setdefault(uid, Stage())


def drop_stage(uid: int):
    STAGES.pop(uid, None)


def read_text(rel: str, stage: Stage | None = None) -> str | None:
    rel = norm(rel)
    if stage and rel in stage.files:
        return stage.files[rel]
    b = _disk_bytes(rel)
    return None if b is None else b.decode("utf-8", errors="replace")


def list_files(stage: Stage | None = None) -> list[tuple[str, int, bool]]:
    out = {}
    for f in ROOT.rglob("*"):
        if not f.is_file():
            continue
        try:
            rel = norm(f.relative_to(ROOT).as_posix())
        except SelfEditError:
            continue
        if Path(rel).suffix.lower() in EDITABLE_EXT or rel == ".gitignore":
            out[rel] = (f.stat().st_size, False)
    if stage:
        for rel, c in stage.files.items():
            out[rel] = (len(c.encode()), True)
    return sorted((r, s, st) for r, (s, st) in out.items())


def validate(rel: str, content: str):
    if len(content.encode("utf-8")) > MAX_FILE:
        raise SelfEditError("файл слишком большой")
    if rel.endswith(".py"):
        try:
            compile(content, rel, "exec")
        except SyntaxError as e:
            raise SelfEditError(f"синтаксическая ошибка в {rel}: строка {e.lineno}: {e.msg}") from None
    if rel.endswith(".json"):
        try:
            json.loads(content)
        except ValueError as e:
            raise SelfEditError(f"некорректный JSON: {e}") from None


def stage_replace(stage: Stage, rel: str, old: str, new: str, replace_all: bool = False) -> str:
    rel = check_editable(rel)
    cur = read_text(rel, stage)
    if cur is None:
        raise SelfEditError(f"файла «{rel}» нет (для нового файла используй write_file)")
    if not old:
        raise SelfEditError("old пустой")
    n = cur.count(old)
    if n == 0:
        first = next((ln.strip() for ln in old.splitlines() if ln.strip()), "")
        hint = difflib.get_close_matches(first, [ln.strip() for ln in cur.splitlines()], n=1, cutoff=0.6)
        raise SelfEditError("old не найден в файле (проверь пробелы и отступы)."
                            + (f" Похожая строка: {hint[0]!r}" if hint else ""))
    if n > 1 and not replace_all:
        raise SelfEditError(f"old встречается {n} раз — добавь контекста или поставь replace_all=true")
    updated = cur.replace(old, new) if replace_all else cur.replace(old, new, 1)
    validate(rel, updated)
    stage.files[rel] = updated
    return f"ок: {rel}, замен: {n if replace_all else 1}"


def stage_write(stage: Stage, rel: str, content: str) -> str:
    rel = check_editable(rel)
    validate(rel, content)
    existed = _disk_bytes(rel) is not None
    stage.files[rel] = content
    return f"ок: {'перезаписан' if existed else 'создан'} {rel} ({len(content.splitlines())} строк)"


def unstage(stage: Stage, rel: str):
    stage.files.pop(norm(rel), None)


def diff_text(stage: Stage) -> str:
    chunks = []
    for rel, new in sorted(stage.files.items()):
        old = _disk_bytes(rel)
        a = [] if old is None else old.decode("utf-8", errors="replace").splitlines(keepends=True)
        b = new.splitlines(keepends=True)
        chunks.extend(difflib.unified_diff(a, b, f"a/{rel}", f"b/{rel}", n=3))
    return "".join(chunks)


def diff_stats(stage: Stage) -> list[tuple[str, int, int, bool]]:
    out = []
    for rel, new in sorted(stage.files.items()):
        old = _disk_bytes(rel)
        a = [] if old is None else old.decode("utf-8", errors="replace").splitlines()
        b = new.splitlines()
        plus = minus = 0
        for ln in difflib.ndiff(a, b):
            if ln.startswith("+ "):
                plus += 1
            elif ln.startswith("- "):
                minus += 1
        out.append((rel, plus, minus, old is None))
    return out


# ====================== применение и откат ======================
def apply_stage(stage: Stage) -> str:
    if not stage.files:
        raise SelfEditError("нечего применять")
    for rel, content in stage.files.items():
        check_editable(rel)
        validate(rel, content)
    base = _jload(BASE_FILE, {})
    bid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    hdir = HISTORY / bid
    meta = {"ts": int(time.time()), "note": stage.note, "files": {}}
    for rel, content in stage.files.items():
        old = _disk_bytes(rel)
        meta["files"][rel] = {"existed": old is not None}
        if old is not None:
            (hdir / "old" / rel).parent.mkdir(parents=True, exist_ok=True)
            (hdir / "old" / rel).write_bytes(old)
        if rel not in base:
            base[rel] = _sha(old)  # то, что лежит в репозитории
        _set_state(rel, content, base)
    _jsave(hdir / "meta.json", meta)
    _jsave(BASE_FILE, base)
    g = _guard()
    g.setdefault("unconfirmed", []).append(bid)
    _guard_save(g)
    return bid


def _rollback(bid: str, base: dict) -> list[str]:
    hdir = HISTORY / bid
    meta = _jload(hdir / "meta.json", None)
    if not meta:
        raise SelfEditError(f"правка {bid} не найдена")
    if meta.get("rolled_back"):
        raise SelfEditError(f"правка {bid} уже откачена")
    done = []
    for rel, info in meta["files"].items():
        if info["existed"]:
            old = (hdir / "old" / rel).read_bytes().decode("utf-8")
            _set_state(rel, old, base)
        else:
            _write_disk(rel, None)
            (OVERLAY / rel).unlink(missing_ok=True)
            base.pop(rel, None)
        done.append(rel)
    meta["rolled_back"] = True
    _jsave(hdir / "meta.json", meta)
    return done


def rollback(bid: str | None = None) -> tuple[str, list[str]]:
    """Откатывает правку bid или последнюю не откаченную."""
    if bid is None:
        items = history(50)
        item = next((h for h in items if not h["rolled_back"]), None)
        if not item:
            raise SelfEditError("откатывать нечего")
        bid = item["id"]
    base = _jload(BASE_FILE, {})
    done = _rollback(bid, base)
    _jsave(BASE_FILE, base)
    g = _guard()
    g["unconfirmed"] = [x for x in g.get("unconfirmed", []) if x != bid]
    _guard_save(g)
    return bid, done


def history(limit: int = 10) -> list[dict]:
    out = []
    if HISTORY.exists():
        for d in sorted(HISTORY.iterdir(), reverse=True)[:limit]:
            m = _jload(d / "meta.json", None)
            if m:
                out.append({"id": d.name, "ts": m["ts"], "note": m.get("note", ""),
                            "files": list(m["files"]), "rolled_back": bool(m.get("rolled_back"))})
    return out


def reset_all() -> int:
    """Выбрасывает все правки: код снова такой, как в репозитории. Возвращает число файлов."""
    base = _jload(BASE_FILE, {})
    n = 0
    for rel in list(base):
        # исходник восстанавливаем из самой старой версии в истории
        oldest = None
        for d in sorted(HISTORY.iterdir()) if HISTORY.exists() else []:
            m = _jload(d / "meta.json", None)
            if m and rel in m["files"] and not m.get("rolled_back"):
                oldest = (d, m["files"][rel])
                break
        if oldest and oldest[1]["existed"]:
            _set_state(rel, (oldest[0] / "old" / rel).read_bytes().decode("utf-8"), base)
        else:
            _write_disk(rel, None)
            (OVERLAY / rel).unlink(missing_ok=True)
            base.pop(rel, None)
        n += 1
    _jsave(BASE_FILE, base)
    shutil.rmtree(OVERLAY, ignore_errors=True)
    g = _guard()
    g["unconfirmed"] = []
    _guard_save(g)
    return n


# ====================== настройки ИИ (на лету) ======================
# ключ -> (тип, описание)
SETTING_KEYS = {
    "persona": ("text", "характер ИИ по умолчанию (вместо встроенного)"),
    "extra": ("text", "дополнительные указания в системный промт, действуют для всех"),
    "ai_name": ("text", "имя ИИ (на него отзывается в группах)"),
    "model": ("text", "модель Gemini, например gemini-2.5-flash"),
    "temperature": ("float", "креативность 0.0–2.0"),
    "max_tokens": ("int", "максимум токенов ответа 100–2000"),
}
_S: dict | None = None


def _load_settings() -> dict:
    global _S
    if _S is None:
        _S = _jload(SETTINGS_FILE, {"values": {}, "history": []})
        _S.setdefault("values", {})
        _S.setdefault("history", [])
    return _S


def setting(key: str):
    """Текущее значение настройки или None (тогда используется значение по умолчанию из кода)."""
    return _load_settings()["values"].get(key)


def settings_snapshot() -> dict:
    return dict(_load_settings()["values"])


def set_setting(key: str, value) -> str:
    if key not in SETTING_KEYS:
        raise SelfEditError(f"неизвестная настройка «{key}». Доступны: {', '.join(SETTING_KEYS)}")
    kind = SETTING_KEYS[key][0]
    s = _load_settings()
    new = None
    if value is not None and str(value).strip() != "":
        try:
            if kind == "float":
                new = float(value)
                if not 0.0 <= new <= 2.0:
                    raise ValueError
            elif kind == "int":
                new = int(float(value))
                if not 100 <= new <= 2000:
                    raise ValueError
            else:
                new = str(value).strip()
                limit = 3000 if key in ("persona", "extra") else 60
                if len(new) > limit:
                    raise SelfEditError(f"слишком длинно (максимум {limit} символов)")
                if key == "model" and not all(c.isalnum() or c in "-._" for c in new):
                    raise SelfEditError("некорректное имя модели")
        except ValueError:
            raise SelfEditError(f"недопустимое значение для «{key}»") from None
    old = s["values"].get(key)
    if new is None:
        s["values"].pop(key, None)
    else:
        s["values"][key] = new
    s["history"] = (s["history"] + [{"ts": int(time.time()), "key": key, "old": old, "new": new}])[-30:]
    _jsave(SETTINGS_FILE, s)
    return "сброшено к значению по умолчанию" if new is None else "сохранено"


def undo_setting() -> str:
    s = _load_settings()
    if not s["history"]:
        raise SelfEditError("отменять нечего")
    h = s["history"].pop()
    if h["old"] is None:
        s["values"].pop(h["key"], None)
    else:
        s["values"][h["key"]] = h["old"]
    _jsave(SETTINGS_FILE, s)
    return h["key"]
