import os


def _ids(value: str) -> set[int]:
    out = set()
    for part in value.replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return out


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
# ID владельцев бота (через запятую). Имеют максимальный ранг во всех чатах.
OWNER_IDS = _ids(os.getenv("OWNER_IDS", ""))
DB_PATH = os.getenv("DB_PATH", "data/bot.db")

# --- Render / будилка ---
PORT = int(os.getenv("PORT", "10000"))
# Render сам задаёт RENDER_EXTERNAL_URL для web-сервисов
PUBLIC_URL = (os.getenv("RENDER_EXTERNAL_URL") or os.getenv("PUBLIC_URL") or "").rstrip("/")
PING_INTERVAL = int(os.getenv("PING_INTERVAL", "240"))  # сек; Render усыпляет через 15 мин

# --- ИИ (Google Gemini) ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
# BLOCK_NONE | BLOCK_ONLY_HIGH | BLOCK_MEDIUM_AND_ABOVE | BLOCK_LOW_AND_ABOVE
GEMINI_SAFETY = os.getenv("GEMINI_SAFETY", "BLOCK_ONLY_HIGH").strip()
AI_NAME = os.getenv("AI_NAME", "Мия").strip()

# --- Семья: сколько часов длится беременность (в реальном времени) ---
PREG_HOURS = float(os.getenv("PREG_HOURS", "6"))

# Часовой пояс для ночного режима (смещение от UTC, по умолчанию Москва)
TZ_OFFSET = int(os.getenv("TZ_OFFSET", "3"))

# Настройки чата по умолчанию
DEFAULTS = {
    # приветствие / правила
    "welcome": True,
    "welcome_text": "👋 Привет, {name}! Добро пожаловать в «{chat}».\nПравила чата: /rules",
    "rules": "",
    # капча
    "captcha": False,
    "captcha_sec": 120,
    # защита
    "antiflood": True,
    "flood_limit": 7,
    "flood_window": 8,
    "flood_mute": 10,
    "antilink": False,
    "antimat": False,
    "antiforward": False,
    "antibot": True,
    "badwords": True,
    # наказания
    "autowarn": True,
    "warn_limit": 3,
    "warn_action": "mute",  # mute | ban | kick
    "warn_mute": 1440,  # минут
    # прочее
    "del_service": True,
    "rp": True,
    "rp18": False,
    "games": True,   # экономика и казино
    "family": True,  # отношения, дети, беременность
    "ai": True,      # ИИ-собеседник
    # ночной режим
    "night": False,
    "night_start": "23:00",
    "night_end": "07:00",
    "night_state": False,
    "night_saved": {},
}
