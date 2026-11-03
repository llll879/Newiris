# Бот-модератор для Telegram-чатов

Модерация, автозащита, капча, РП и 18+ РП (с согласием адресата), профили, браки, репутация, заметки, ночной режим.
Встроенная «будилка» для Render (веб-сервер + самопинг).

## Запуск локально
    pip install -r requirements.txt
    BOT_TOKEN=... OWNER_IDS=ваш_id python main.py

## Деплой на Render
1. Создай бота у @BotFather → получи токен. В BotFather: /setprivacy → Disable (чтобы бот видел все сообщения).
2. Залей папку в GitHub-репозиторий.
3. Render → New → Blueprint (или Web Service) → выбери репозиторий. Файл render.yaml всё настроит.
4. В Environment задай BOT_TOKEN и OWNER_IDS (твой Telegram id, узнать: /id).
5. Будилка включается сама: Render задаёт RENDER_EXTERNAL_URL, бот пингует /health каждые 4 минуты.
   Для надёжности добавь ещё внешний пинг (UptimeRobot) на https://<твой-сервис>.onrender.com/health

## Важно
- Сделай бота админом чата (удаление сообщений, блокировка, закрепление).
- На бесплатном Render диск временный: база (SQLite) сбрасывается при перезапуске/деплое.
  Для сохранности подключи Persistent Disk и поставь DB_PATH=/var/data/bot.db.
