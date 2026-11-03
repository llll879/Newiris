from aiogram.filters import BaseFilter
from aiogram.types import Message

import state


def parse_head(text: str):
    """Разбирает первое слово сообщения: (слово, с_префиксом, остаток) либо None."""
    parts = text.split(maxsplit=1)
    if not parts:
        return None
    head = parts[0].lower()
    prefixed = head[0] in "/!."
    if prefixed:
        head = head[1:]
        if "@" in head:
            head, _, who = head.partition("@")
            if state.bot_username and who != state.bot_username:
                return None  # команда адресована другому боту
    rest = parts[1].strip() if len(parts) > 1 else ""
    return head, prefixed, rest


class Cmd(BaseFilter):
    """Команда с префиксом (/warn, !warn, .warn) или словом (варн).

    plain="free"   — слово без префикса принимается с любым хвостом (для модерации)
    plain="strict" — без префикса только если хвост пуст / @упоминание / число
    plain="none"   — только с префиксом
    """

    def __init__(self, *names: str, plain: str = "strict"):
        self.names = frozenset(n.lower() for n in names)
        self.plain = plain

    async def __call__(self, message: Message):
        text = message.text or message.caption
        if not text:
            return False
        parsed = parse_head(text)
        if not parsed:
            return False
        head, prefixed, rest = parsed
        if head not in self.names:
            return False
        if not prefixed:
            if self.plain == "none":
                return False
            if self.plain == "strict" and rest and not (rest.startswith("@") or rest[0].isdigit()):
                return False
        return {"cmd": head, "prefixed": prefixed, "arg": rest, "args": rest.split()}
