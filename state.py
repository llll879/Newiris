"""Глобальное состояние процесса."""
import time

db = None
bot_username = ""
bot_id = 0
started = time.time()
captcha: dict = {}
