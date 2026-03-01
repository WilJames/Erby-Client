# -*- coding: utf-8 -*-
# server.py

import asyncio
import json
from pathlib import Path
import logging
import contextlib
from aiohttp import web
from erby_client import ErbyClient
from logger import LOG_DIR, LOG_LEVEL_NAMES, get_log_level_name, set_log_level_name
from const import RECORD_TYPES, TIMER_TYPES
from utils import parse_log_line


TIMER_ACTIONS = {
    "start": ErbyClient.start_timer,
    "pause": ErbyClient.pause_timer,
    "stop": ErbyClient.stop_timer,
    "delete": ErbyClient.delete_timer,
}
BASE_DIR = Path(__file__).resolve().parent
WEBUI_DIR = BASE_DIR / "webui"

logger = logging.getLogger(__name__)


# ---------- helpers ----------
async def _read_json(request: web.Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


def make_loglevel_get_handler():
    async def _handler(request: web.Request):
        return json_response({"level": get_log_level_name(), "levels": list(LOG_LEVEL_NAMES)})
    return _handler


def make_loglevel_set_handler():
    async def _handler(request: web.Request):
        data = await _read_json(request)
        level = str(data.get("level", "")).upper()
        try:
            level = set_log_level_name(level, apply_now=True)
        except ValueError:
            return json_response({"error": "bad level", "levels": list(LOG_LEVEL_NAMES)}, status=400)

        logger.warning("Server изменение уровня лога на %s из WebUI", level)
        return json_response({"level": level})
    return _handler


def json_response(data: dict, status: int = 200) -> web.Response:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    return web.Response(text=text, status=status, content_type="application/json")


def make_timer_handler(erby: ErbyClient, action: str, rtype: str):
    async def _handler(request):
        logger.debug("API таймера: %s %s", rtype, action)
        result = await TIMER_ACTIONS[action](erby, rtype)
        return json_response(result)
    return _handler


def make_record_handler(erby: ErbyClient, rtype: str):
    async def _handler(request):
        logger.debug("API запись: %s", rtype)
        result = await erby.create_record(rtype)
        return json_response(result)
    return _handler


def make_last_handler(erby: ErbyClient):
    async def _handler(request):
        rtype = request.match_info.get("rtype")
        if not rtype:
            logger.warning("API сенсоров: пропущен тип")
            return json_response({"error": "missing type"}, status=400)
        logger.debug("API сенсоров: %s", rtype)
        data = await erby.get_last_time(rtype)
        return json_response(data)
    return _handler


def make_last_all_handler(erby: ErbyClient):
    async def _handler(request):
        logger.debug("API сенсоров: выборка всех")
        data = await erby.get_last_payload()
        return json_response(data)
    return _handler


def make_app(erby: ErbyClient, stop_event: asyncio.Event | None = None):
    routes = web.RouteTableDef()

    # --- таймеры ---
    for name, rtype in TIMER_TYPES.items():
        for action in TIMER_ACTIONS.keys():
            path = f"/{name}/{action}"
            routes.get(path)(make_timer_handler(erby, action, rtype))

    # --- одиночные записи ---
    for name, rtype in RECORD_TYPES.items():
        routes.get(f"/{name}")(make_record_handler(erby, rtype))

    # --- last ---
    routes.get("/last")(make_last_all_handler(erby))
    routes.get("/last/{rtype}")(make_last_handler(erby))

    # --- debug ---
    routes.get("/logs")(make_logs_page())
    routes.get("/logs/stream")(make_logs_stream())

    #  --- loglevel ---
    routes.get("/api/loglevel")(make_loglevel_get_handler())
    routes.post("/api/loglevel")(make_loglevel_set_handler())

    app = web.Application()
    if stop_event is None:
        stop_event = asyncio.Event()

    app["stop_event"] = stop_event
    # app["sse_clients"] = set()
    app.on_shutdown.append(_shutdown_sse)
    app.add_routes(routes)

    app.router.add_static("/static/", path=WEBUI_DIR, name="static")
    return app


def make_logs_page():
    async def _handler(request):
        return web.FileResponse(WEBUI_DIR / "logs.html")
    return _handler


def make_logs_stream():
    def read_last_n_lines_bytes(path: Path, n: int, max_back: int = 512 * 1024) -> list[bytes]:
        """
        Читает последние n строк (bytes) без чтения всего файла.
        """
        if not path.exists():
            return []
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size <= 0:
                return []
            back = min(size, max_back)
            while True:
                f.seek(size - back)
                data = f.read(back)
                parts = data.splitlines()
                if len(parts) >= n + 1 or back >= size:
                    return parts[-n:]
                back = min(size, back * 2)

    async def _handler(request: web.Request):
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                # если nginx:
                "X-Accel-Buffering": "no",
            },
        )
        await response.prepare(request)

        log_file = LOG_DIR / "logfile.log"
        stop_event: asyncio.Event = request.app["stop_event"]

        last_n = 200
        poll_interval = 0.1          # было 0.5 — уменьшаем
        heartbeat_every = 15.0

        async def _drain():
            # В некоторых версиях aiohttp drain() deprecated, но часто помогает “продавить” буфер
            d = getattr(response, "drain", None)
            if callable(d):
                with contextlib.suppress(Exception):
                    await d() # type: ignore

        async def send_event(event: str, data_obj):
            payload = json.dumps(data_obj, ensure_ascii=False)
            msg = f"event: {event}\n" f"data: {payload}\n\n"
            await response.write(msg.encode("utf-8"))
            await _drain()

        async def send_heartbeat():
            await response.write(b": ping\n\n")
            await _drain()

        # --- INIT: последние N строк одним событием ---
        if log_file.exists():
            init_items = []
            for b in read_last_n_lines_bytes(log_file, last_n):
                s = b.decode("utf-8", errors="replace")
                init_items.append(parse_log_line(s))
            await send_event("init", init_items)
        else:
            await send_event("init", [])

        # Позиция для tail
        try:
            pos = log_file.stat().st_size if log_file.exists() else 0
        except OSError:
            pos = 0

        partial = ""
        f = None
        f_inode = None

        loop = asyncio.get_event_loop()
        last_hb = loop.time()

        try:
            while not stop_event.is_set():
                now = loop.time()
                if now - last_hb >= heartbeat_every:
                    await send_heartbeat()
                    last_hb = now

                if not log_file.exists():
                    # файл временно отсутствует
                    if f:
                        with contextlib.suppress(Exception):
                            f.close()
                        f = None
                        f_inode = None
                    partial = ""
                    await asyncio.sleep(poll_interval)
                    continue

                # (пере)открыть файл при необходимости
                if f is None:
                    try:
                        f = log_file.open("rb")
                        st = log_file.stat()
                        f_inode = (st.st_ino, st.st_dev)
                    except OSError:
                        f = None
                        f_inode = None
                        await asyncio.sleep(poll_interval)
                        continue

                # проверить ротацию: inode поменялся
                try:
                    st = log_file.stat()
                    inode_now = (st.st_ino, st.st_dev)
                except OSError:
                    inode_now = None

                if inode_now is None or (f_inode is not None and inode_now != f_inode):
                    # файл ротировали/заменили
                    with contextlib.suppress(Exception):
                        f.close()
                    f = None
                    f_inode = None
                    pos = 0
                    partial = ""
                    await asyncio.sleep(poll_interval)
                    continue

                # tail
                try:
                    f.seek(0, 2)
                    size = f.tell()
                    if pos > size:
                        # truncate
                        pos = 0
                        partial = ""

                    if size > pos:
                        f.seek(pos)
                        chunk = f.read(size - pos)
                        pos = size

                        text = chunk.decode("utf-8", errors="replace")
                        if partial:
                            text = partial + text
                            partial = ""

                        for part in text.splitlines(True):
                            if part.endswith("\n") or part.endswith("\r"):
                                await send_event("log", parse_log_line(part))
                            else:
                                partial = part

                except OSError:
                    # что-то случилось с файловой системой — попробуем переоткрыть
                    with contextlib.suppress(Exception):
                        f.close()
                    f = None
                    f_inode = None
                    partial = ""

                await asyncio.sleep(poll_interval)

        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            if f:
                with contextlib.suppress(Exception):
                    f.close()
            with contextlib.suppress(Exception):
                await response.write_eof()

        return response

    return _handler


async def _shutdown_sse(app: web.Application):
    app["stop_event"].set()
