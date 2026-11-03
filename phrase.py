"""Фильтр «просто словами»: ловит фразу в начале сообщения (в т.ч. из нескольких слов)."""
from aiogram.filters import BaseFilter
from aiogram.types import Message

from filters import parse_head

_ALL = {"все", "всё", "all", "вабанк", "ва-банк"}


class Say(BaseFilter):
    """Say("купить дом", "купить квартиру") — срабатывает на «купить дом 3» и «/купить дом 3».

    Без префикса и без loose=True фраза принимается, только если дальше пусто, число,
    @упоминание или «всё» — так обычная болтовня («работать надо») не вызывает команды.
    """

    def __init__(self, *phrases: str, loose: bool = False):
        self.phrases = sorted({p.lower() for p in phrases}, key=len, reverse=True)
        self.loose = loose

    async def __call__(self, message: Message):
        text = (message.text or "").strip()
        if not text:
            return False
        prefixed = text[0] in "/!."
        if prefixed:
            if parse_head(text) is None:
                return False
            text = text[1:]
            first, sep, tail = text.partition(" ")
            text = (first.split("@")[0] + sep + tail).strip()
        low = text.lower()
        for ph in self.phrases:
            if low == ph or low.startswith(ph + " ") or low.startswith(ph + "\n"):
                rest = text[len(ph):].strip()
                if rest and not prefixed and not self.loose:
                    w = rest.split()[0].lower()
                    if not (w.startswith("@") or w[0].isdigit() or w in _ALL):
                        return False
                return {"phrase": ph, "arg": rest, "args": rest.split(), "prefixed": prefixed}
        return False
