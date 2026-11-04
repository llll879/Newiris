"""Тонкая обёртка над Google Gemini API (generateContent)."""
import logging

from aiohttp import ClientSession, ClientTimeout

import config
import selfedit

log = logging.getLogger("gemini")
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
CATEGORIES = ["HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
              "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"]


class GeminiError(Exception):
    pass


def _model() -> str:
    return selfedit.setting("model") or config.GEMINI_MODEL


async def ask(system: str, history: list[tuple[str, str]], user_text: str,
              temperature: float | None = None, max_tokens: int | None = None) -> str | None:
    """history — [(role, text)], role: 'user' | 'model'. None — ответ заблокирован фильтром."""
    if not config.GEMINI_API_KEY:
        raise GeminiError("no_key")
    if temperature is None:
        t = selfedit.setting("temperature")
        temperature = 0.95 if t is None else t
    if max_tokens is None:
        max_tokens = selfedit.setting("max_tokens") or 700
    contents = [{"role": r, "parts": [{"text": t}]} for r, t in history]
    contents.append({"role": "user", "parts": [{"text": user_text}]})
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        "safetySettings": [{"category": c, "threshold": config.GEMINI_SAFETY} for c in CATEGORIES],
    }
    url = URL.format(model=_model())
    async with ClientSession(timeout=ClientTimeout(total=50)) as session:
        async with session.post(url, json=body, headers={"x-goog-api-key": config.GEMINI_API_KEY}) as resp:
            data = await resp.json(content_type=None)
            if resp.status == 429:
                raise GeminiError("rate")
            if resp.status != 200:
                log.warning("Gemini %s: %s", resp.status, str(data)[:300])
                raise GeminiError(f"http_{resp.status}")
    if data.get("promptFeedback", {}).get("blockReason"):
        return None
    cands = data.get("candidates") or []
    if not cands:
        return None
    parts = (cands[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    return text or None


async def chat(system: str, contents: list[dict], tools: list[dict] | None = None,
               temperature: float = 0.3, max_tokens: int = 8192) -> dict | None:
    """Низкоуровневый вызов с function calling.

    contents — готовые сообщения Gemini (с parts text / functionCall / functionResponse).
    Возвращает content ответа модели ({"role": "model", "parts": [...]}) или None, если заблокировано.
    """
    if not config.GEMINI_API_KEY:
        raise GeminiError("no_key")
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        "safetySettings": [{"category": c, "threshold": config.GEMINI_SAFETY} for c in CATEGORIES],
    }
    if tools:
        body["tools"] = tools
        body["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
    url = URL.format(model=_model())
    async with ClientSession(timeout=ClientTimeout(total=120)) as session:
        async with session.post(url, json=body, headers={"x-goog-api-key": config.GEMINI_API_KEY}) as resp:
            data = await resp.json(content_type=None)
            if resp.status == 429:
                raise GeminiError("rate")
            if resp.status != 200:
                log.warning("Gemini %s: %s", resp.status, str(data)[:300])
                raise GeminiError(f"http_{resp.status}")
    if data.get("promptFeedback", {}).get("blockReason"):
        return None
    cands = data.get("candidates") or []
    if not cands:
        return None
    content = cands[0].get("content") or {}
    if not content.get("parts"):
        content = {"role": "model", "parts": [{"text": ""}]}
    content["role"] = "model"
    return content
