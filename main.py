# -*- coding: utf-8 -*-
# main.py

import asyncio
import signal
import contextlib
import logging
from aiohttp import web
from erby_client import ErbyClient
from server import make_app
from logger import setup_logging
from const import SERVER_IP, SERVER_PORT


async def main():
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Приложение запущено")
    stop_event = asyncio.Event()

    def _signal(*_):
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in filter(None, (getattr(signal, "SIGINT", None),
                             getattr(signal, "SIGTERM", None),
                             getattr(signal, "SIGBREAK", None))):
        try:
            loop.add_signal_handler(sig, _signal)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, _signal)

    erby = ErbyClient(stop_event=stop_event)

    runner = None
    erby_task = None
    guard_task = None

    async def ensure_erby_task():
        nonlocal erby_task
        while not stop_event.is_set():
            if erby_task is None or erby_task.done():
                logger.warning("erby.run не активен, перезапускаю")
                erby_task = asyncio.create_task(erby.run(), name="erby.run")
            await asyncio.sleep(5)

    def _on_done(t: asyncio.Task):
        try:
            t.result()
        except asyncio.CancelledError:
            logger.warning("erby.run: отменена")
        except Exception:
            logger.exception("erby.run: упала с исключением")

    try:
        await erby.init_db()

        erby_task = asyncio.create_task(erby.run(), name="erby.run")
        erby_task.add_done_callback(_on_done)
        guard_task = asyncio.create_task(ensure_erby_task(), name="erby.guard")

        app = make_app(erby, stop_event=stop_event)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", SERVER_PORT)
        await site.start()
        logger.info(f"HTTP сервер запущен на 0.0.0.0:{SERVER_PORT}, URL: http://{SERVER_IP}:{SERVER_PORT}")
        logger.info(f"Лог сервера на: http://{SERVER_IP}:{SERVER_PORT}/logs")

        await stop_event.wait()

    finally:
        logger.info("Выключение...")

        if guard_task:
            guard_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await guard_task

        if erby_task:
            erby_task.cancel()

        # shutdown-цепочка
        with contextlib.suppress(Exception):
            await erby.shutdown()

        if erby_task:
            with contextlib.suppress(asyncio.CancelledError):
                await erby_task

        if runner:
            with contextlib.suppress(Exception):
                await runner.cleanup()

        logger.info("Пока")


if __name__ == "__main__":
    asyncio.run(main())
