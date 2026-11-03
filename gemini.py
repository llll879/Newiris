"""Тонкая обёртка над Google Gemini API (generateContent)."""
import logging

from aiohttp import ClientSession, ClientTimeout

import config

log = logging.getLogger("gemini")
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
CATEGORIES = ["HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
              "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"]


class GeminiError(Exception):
    pass


async def ask(system: str, history: list[tuple[str, str]], user_text: str,
              temperature: float = 1.0, max_tokens: int = 700) -> str | None:
    """history — [(role, text)], role: 'user' | 'model'. None — ответ заблокирован фильтром."""
    if not config.GEMINI_API_KEY:
        raise GeminiError("no_key")
    contents = [{"role": r, "parts": [{"text": t}]} for r, t in history]
    contents.append({"role": "user", "parts": [{"text": user_text}]})
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        "safetySettings": [{"category": c, "threshold": config.GEMINI_SAFETY} for c in CATEGORIES],
    }
    url = URL.format(model=config.GEMINI_MODEL)
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
