"""Глобальное состояние процесса."""
import time

db = None  # DB, создаётся в main.py
bot_username = ""
bot_id = 0
started = time.time()
captcha: dict = {}  # (chat_id, user_id) -> message_id
