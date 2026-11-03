"""Мини web-сервер + самопинг («будилка»), чтобы Render не усыплял сервис."""
import asyncio
import logging

from aiohttp import ClientSession, ClientTimeout, web

import config
import state

log = logging.getLogger("keepalive")


async def _root(request):
    return web.Response(text="🤖 Moderator bot is alive")


async def _health(request):
    return web.json_response({"ok": True, "uptime": int(__import__("time").time() - state.started)})


async def start_web():
    app = web.Application()
    app.router.add_get("/", _root)
    app.router.add_get("/health", _health)
    app.router.add_head("/", _root)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.PORT)
    await site.start()
    log.info("Web server on :%s", config.PORT)
    return runner


async def pinger():
    """Каждые PING_INTERVAL секунд дёргает собственный публичный URL."""
    if not config.PUBLIC_URL:
        log.info("PUBLIC_URL не задан — самопинг выключен (локальный запуск)")
        return
    url = config.PUBLIC_URL + "/health"
    log.info("Самопинг включён: %s каждые %s сек", url, config.PING_INTERVAL)
    timeout = ClientTimeout(total=20)
    async with ClientSession(timeout=timeout) as session:
        while True:
            await asyncio.sleep(config.PING_INTERVAL)
            try:
                async with session.get(url) as resp:
                    log.debug("ping %s", resp.status)
            except Exception as e:  # noqa: BLE001
                log.warning("ping failed: %s", e)
