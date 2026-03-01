# -*- coding: utf-8 -*-
# erby_client.py

from __future__ import annotations
import asyncio
import json
import uuid
import arrow
import time
import websockets
import aiosqlite
import logging
import contextlib
from websockets.exceptions import (
    ConnectionClosedOK,
    ConnectionClosedError,
    InvalidStatus,          # websockets 16: основной
    InvalidHandshake,       # на всякий
    ConnectionClosed,
)

from const import FMT, RECORD_KEYS, SERVICE_KEYS, SENSOR_TYPES, SENSOR_MAP, \
    DEFAULT_SYNC_DATE, SQL_PRAGMA, TIMERS, EAT_TYPES, TYPE_RU
from ws_const import PRIMARY_WS, FALLBACK_WS, HEADERS
import utils
import erby_utils
import sql


def _should_switch_on_connect_error(exc: Exception) -> bool:
    """
    Ошибка на этапе CONNECT / HTTP upgrade.
    websockets 16: основной класс InvalidStatus.
    """

    # 429/5xx на handshake (в разных версиях поле называется по-разному)
    if isinstance(exc, InvalidStatus):
        # пробуем разные возможные имена атрибутов
        code = (
            getattr(exc, "status_code", None) or
            getattr(exc, "status", None) or
            getattr(getattr(exc, "response", None), "status_code", None) or
            getattr(getattr(exc, "response", None), "status", None)
        )
        logging.getLogger(__name__).warning("WS handshake InvalidStatus code=%r msg=%s", code, exc)
        if code in (403, 429, 502, 503, 504):
            return True

    # страховка по тексту
    msg = str(exc)
    if any(x in msg for x in ("HTTP 403", "HTTP 429", "HTTP 502", "HTTP 503", "HTTP 504")):
        return True

    # {"error":"rejected"} обычно светится в тексте
    if "rejected" in msg and "429" in msg:
        return True

    # opening handshake timeout / сетевые
    if isinstance(exc, (asyncio.TimeoutError, OSError, InvalidHandshake)):
        return True

    if "timed out during opening handshake" in msg:
        return True

    return False


async def upsert_sync_date(db, syncDate: str):
    logging.getLogger(__name__).debug("Последняя синхронизация: %s", utils.utc_to_local_str(syncDate))
    await db.execute(sql.Q_UPSERT_SYNC_STATE, (syncDate, ))


class WsLoopError(Exception):
    """Ошибка в WS loop с признаком того, что соединение успело открыться."""
    def __init__(self, message: str, *, opened: bool, cause: Exception | None = None):
        super().__init__(message)
        self.opened = opened
        self.__cause__ = cause


class ErbyClient:
    def __init__(self, db_path="erby.db", stop_event: asyncio.Event | None = None):
        self.ws_urls = [PRIMARY_WS, FALLBACK_WS]
        self._url_idx = 0  # 0=primary, 1=fallback

        self._opened_once = False

        # --- WS health ---
        self._ws_ping_interval = 15.0
        self._ws_ping_timeout = 7.0

        self.headers = HEADERS
        self.db_path = db_path
        self.logger = logging.getLogger(__name__)
        self.stop_event = stop_event or asyncio.Event()

        self.db: aiosqlite.Connection | None = None
        self.db_lock = asyncio.Lock()

        # --- DB worker ---
        self._db_q: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=1000)
        self._db_task: asyncio.Task | None = None

        self.ws = None
        self.ws_alive = asyncio.Event()
        self.ws_alive.clear()

        self.send_lock = asyncio.Lock()
        self.sync_lock = asyncio.Lock()

        self._sync_task: asyncio.Task | None = None
        self._publish_lock = asyncio.Lock()

    # ---------------- DB ----------------
    async def init_db(self):
        self.db = await aiosqlite.connect(self.db_path)

        for PRAGMA in SQL_PRAGMA:
            await self.db.execute(PRAGMA)

        await self.db.execute(sql.Q_RECORDS_CREATE_TABLE)
        await self.db.execute(sql.Q_SERVICE_CREATE_TABLE)
        await self.db.execute(sql.Q_SYNC_STATE_CREATE_TABLE)

        await self.db.commit()
        self.db.row_factory = aiosqlite.Row
        self.logger.info("DB инициализация: %s", self.db_path)

        # стартуем воркер после того как DB готова
        if not self._db_task or self._db_task.done():
            self._db_task = asyncio.create_task(self._db_worker(), name="erby.db_worker")

    async def _db_worker(self) -> None:
        """
        Единственное место, где мы пишем в БД по входящим событиям.
        Останавливаемся ТОЛЬКО sentinel'ом (None), чтобы воркер не зависал на get().
        """
        self.logger.info("DB воркер запущен")

        try:
            while True:
                item = await self._db_q.get()
                try:
                    if item is None:
                        return  # мягкая остановка

                    await self._upsert_data_impl(item)

                except asyncio.CancelledError:
                    # отмена — это штатно, но передаём дальше
                    raise
                except Exception:
                    # DB ошибки не должны убить воркер
                    self.logger.exception("DB воркер: ошибка обработки payload")
                finally:
                    self._db_q.task_done()

        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception("DB воркер: фатальная ошибка")
        finally:
            self.logger.info("DB воркер остановлен")

    async def _enqueue_db(self, payload: dict) -> None:
        """
        Кладём payload в очередь. Если очередь забита — выкидываем САМЫЙ СТАРЫЙ элемент,
        чтобы не блокировать WS receiver и не накапливать устаревшие события.

        Важно: при drop-oldest делаем task_done(), иначе join() зависнет.
        """
        if not payload:
            return

        try:
            self._db_q.put_nowait(payload)
            return
        except asyncio.QueueFull:
            # drop oldest
            try:
                _old = self._db_q.get_nowait()
                self._db_q.task_done()
            except asyncio.QueueEmpty:
                pass

            # пробуем положить снова
            try:
                self._db_q.put_nowait(payload)
                self.logger.warning("DB очередь переполнена: drop-oldest применён")
                return
            except asyncio.QueueFull:
                self.logger.warning("DB очередь переполнена: событие потеряно (даже после drop-oldest)")
                return

    # --------------- Helpers -------------
    def _current_ws_url(self) -> str:
        return self.ws_urls[self._url_idx]

    def _switch_url(self) -> None:
        self._url_idx = (self._url_idx + 1) % len(self.ws_urls)
        self.logger.warning("WS: переключение URL -> %s", self._current_ws_url())

    def _prefer_primary(self) -> None:
        if self._url_idx != 0:
            self.logger.info("WS: приоритет PRIMARY")
        self._url_idx = 0

    # ---------------- WS ----------------
    async def run(self):
        backoff = 3

        while not self.stop_event.is_set():
            url = self._current_ws_url()
            self.logger.debug("WS loop tick (url=%s)", url)

            try:
                self._opened_once = False
                await self._connect_and_loop(url)

                # receiver завершился "тихо" (без исключения)
                self._prefer_primary()
                backoff = 3
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
                continue

            except asyncio.CancelledError:
                raise

            except WsLoopError as e:
                if self.stop_event.is_set():
                    break

                opened = e.opened
                real_exc = e.__cause__ if e.__cause__ else e

                # 1) если не успели в OPEN — это connect-fail
                if not opened:
                    self.logger.warning("WS connect-fail: %s: %s", type(real_exc).__name__, real_exc)

                    if _should_switch_on_connect_error(real_exc):  # type: ignore[arg-type]
                        self._switch_url()

                    self.logger.warning("WS реконнект через %ss: %s (url=%s)", backoff, real_exc, url)
                    try:
                        await asyncio.wait_for(self.stop_event.wait(), timeout=backoff)
                    except asyncio.TimeoutError:
                        pass
                    backoff = min(backoff * 2, 60)
                    continue

                # 2) если OPEN был — это post-connect drop
                self._prefer_primary()

                # сервер рвёт TCP без close frame обычно раз в 10 минут
                # Это НЕ повод разгонять backoff до 60с.
                msg = str(real_exc)
                if "no close frame received or sent" in msg:
                    self.logger.debug("WS dropped without close frame; reconnect soon (url=%s)", url)
                    backoff = 3
                    try:
                        await asyncio.wait_for(self.stop_event.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        pass
                    continue

                # Иначе — нормальный backoff (например, ping timeout, сетевой глюк)
                self.logger.warning("WS пост-дроп, реконнект через %ss: %s (url=%s)", backoff, real_exc, url)
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 60)

            except BaseException:
                self.logger.exception("WS: фатальная ошибка (BaseException), продолжаю цикл")
                await asyncio.sleep(1)

        return True

    async def _connect_and_loop(self, url: str) -> None:
        self.ws_alive.clear()
        self._opened_once = False

        try:
            async with websockets.connect(
                url,
                additional_headers=self.headers,
                compression="deflate",
                open_timeout=8,
                ping_interval=self._ws_ping_interval,
                ping_timeout=self._ws_ping_timeout,
                close_timeout=3,
            ) as ws:
                self.ws = ws
                self.ws_alive.set()
                self._opened_once = True  # <-- OPEN достигнут

                type_url_text = 'PRIMARY' if url == PRIMARY_WS else 'FALLBACK'
                self.logger.info("WS подключен к %s URL: %s", type_url_text, url)

                try:
                    await self.receiver()
                finally:
                    self.ws_alive.clear()
                    self.ws = None
                    self.logger.info("WS отключен")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            # ВАЖНО: наружу прокидываем обёртку с флагом opened
            raise WsLoopError(str(e), opened=self._opened_once, cause=e) from e

    async def shutdown(self):
        self.logger.info("Erby выключен...")

        # 0) сигнал остановки "внешнему миру"
        self.stop_event.set()
        self.ws_alive.clear()

        # 1) остановить sync_task
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sync_task

        # 2) закрыть websocket (если открыт)
        if self.ws:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.ws.close(), timeout=3.0)
            self.ws = None

        # 3) мягко остановить DB воркер sentinel'ом
        if self._db_task and not self._db_task.done():
            with contextlib.suppress(Exception):
                self._db_q.put_nowait(None)

            # дождаться обработки очереди
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._db_q.join(), timeout=5.0)

            # если вдруг не остановился — отменяем
            if not self._db_task.done():
                self._db_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._db_task

        # 4) закрыть DB
        if self.db:
            async with self.db_lock:
                with contextlib.suppress(Exception):
                    await self.db.close()
            self.db = None

        self.logger.info("Erby остановлен")

    async def ws_send(self, payload: dict) -> bool:
        data = json.dumps(payload, ensure_ascii=False)

        async with self.send_lock:
            ws = self.ws
            if not self.ws_alive.is_set() or ws is None:
                self.logger.warning("WS не активен, пропуск отправки")
                return False

            try:
                await ws.send(data)
                return True

            except asyncio.CancelledError:
                raise

            except ConnectionClosed as e:
                self.logger.warning("WS закрыт во время отправки: %s", e)
                self.ws_alive.clear()
                # self.ws НЕ трогаем здесь
                return False

            except Exception as e:
                self.logger.warning("WS ошибка отправки: %s", e)
                self.ws_alive.clear()
                return False

    async def receiver(self):
        """
        Читает сообщения из WS.
        Важно: CancelledError не ловим (чтобы shutdown был корректный).
        Ошибки, означающие потерю соединения, пробрасываем наверх, чтобы run() применил backoff.
        """
        if not self.ws:
            self.logger.warning("WS соединения не существует")
            return

        try:
            async for msg in self.ws:
                if self.stop_event.is_set():
                    break

                try:
                    msg_data = json.loads(msg)
                except json.JSONDecodeError:
                    self.logger.warning("WS плохой json: %r", msg[:200])
                    continue

                event = msg_data.get("event", "")

                if event == "wa:welcome" and not self.stop_event.is_set():
                    if not self._sync_task or self._sync_task.done():
                        self._sync_task = asyncio.create_task(self.safe_sync())

                elif event == "wa:sync_get":
                    data = msg_data.get("data", {})
                    await self._enqueue_db(data)

        except asyncio.CancelledError:
            raise

        except ConnectionClosedOK:
            self.logger.info("WS завершился корректно")

        except ConnectionClosedError as e:
            msg = str(e)
            if "no close frame received or sent" in msg:
                self.logger.debug("WS idle-drop: %s", e)
            else:
                self.logger.warning("WS соединение потеряно: %s", e)
            raise

        except ConnectionClosed as e:
            # на всякий, если не OK и не Error (редко, но бывает)
            self.logger.warning("WS закрыт: %s", e)
            raise

        except Exception:
            self.logger.exception("WS неожиданная ошибка")
            raise

        else:
            # async-for завершился без исключения
            self.logger.info("WS receiver: поток сообщений завершён без исключения")

    # ---------------- Sync processing ----------------
    async def safe_sync(self):
        async with self.sync_lock:
            if self.stop_event.is_set():
                return

            # если к моменту sync WS уже умер — не делаем лишнее
            if not self.ws_alive.is_set():
                self.logger.debug("Синхронизация пропущена: WS не активен")
                return

            self.logger.debug("Синхронизация запущена")
            await self.sync_get()
            await asyncio.sleep(0.3)

    async def sync_get(self):
        syncDate = await self.get_last_sync_date()
        self.logger.info("Запрос синхронизации данных: %s", utils.utc_to_local_str(syncDate))

        payload = {
            "event": "wa:sync_get",
            "data": {
                "syncDate": syncDate
            }
        }

        await self.ws_send(payload)

    async def _upsert_data_impl(self, payload: dict):
        if not payload:
            return

        db = self.db
        if not db:
            return

        async with self.db_lock:
            syncDate = payload.get("syncDate", "")
            if syncDate != "":
                await upsert_sync_date(db, syncDate)

            records = payload.get("records", [])
            service_data = payload.get("service_data", [])

            if records:
                self.logger.info(f'Вставка или обновление для {len(records)} записей')

                await db.executemany(sql.Q_UPSERT_RECORD, [
                    {**utils.norm(rec, RECORD_KEYS), "raw_json": json.dumps(rec, ensure_ascii=False)}
                    for rec in records
                ])

            if service_data:
                self.logger.info(f'Вставка или обновление для {len(service_data)} таймеров')

                await db.executemany(sql.Q_UPSERT_SERVICE, [
                    {**utils.norm(sd, SERVICE_KEYS), "raw_json": json.dumps(sd, ensure_ascii=False)}
                    for sd in service_data
                ])

            await db.commit()

    async def _sync_data(self, *, record: dict | None = None, service: dict | None = None) -> bool:
        if record is None and service is None:
            return False

        data: dict = {}
        if record is not None:
            data["records"] = [record]

        if service is not None:
            data["service_data"] = [service]

        payload = {"event": "wa:sync", "data": data}
        return await self.ws_send(payload)

    # ---------------- Records logic ----------------
    async def create_record(self, record_type: str) -> dict:
        self.logger.info("Создание записи: %s", record_type)
        cloud_id = str(uuid.uuid4())
        now = utils.local_str()

        record = erby_utils._build_record(
            cloud_id=cloud_id,
            record_type=record_type,
            start=now,
            date_updated=now,
            duration=0,
            pause_duration=0
        )

        await self._sync_data(record=record)
        return {"status": "ok", "state": "create", "text": "Записала"}

    async def _create_timer(self, record_type: str) -> dict:
        now = utils.local_str()

        sd = erby_utils._build_service(
            cloud_id=str(uuid.uuid4()),
            record_type=record_type,
            start=now,
            date_updated=now,
            deleted=0,
            timer_state="active",
            pause_duration=0,
            last_pause_start=None,
            date_synced=None,
        )

        self.logger.info("Таймер %s: создание и запуск", record_type)
        ok = await self._sync_data(service=sd)

        if not ok:
            return {"status": "error", "state": "ws_down", "text": "Нет соединения с сервером, команда не отправлена"}

        return {"status": "ok", "state": "create+start", "text": "Таймер запущен"}

    async def _resume_timer(self, timer: dict) -> dict:
        now = utils.local_str()
        lps = timer.get("last_pause_start")
        extra = (utils._arrow(now) - utils._arrow(lps)).total_seconds() if lps else 0
        pause_duration = int(timer["pause_duration"] + extra)
        record_type = timer["record_type"]

        sd = erby_utils._build_service(
            cloud_id=timer["cloud_id"],
            record_type=record_type,
            start=timer["start"],
            date_updated=now,
            deleted=0,
            timer_state="active",
            pause_duration=pause_duration,
            last_pause_start=lps,
            date_synced=timer.get("date_synced"),
        )
        self.logger.info("Таймер %s: продолжить", record_type)
        ok = await self._sync_data(service=sd)

        if not ok:
            return {"status": "error", "state": "ws_down", "text": "Нет соединения с сервером, команда не отправлена"}

        return {"status": "ok", "state": "start", "text": "Таймер запущен"}

    async def start_timer(self, record_type: str) -> dict:
        eat_alias = record_type == "eat"
        incoming_is_eat_type = record_type in EAT_TYPES
        incoming_is_eat = eat_alias or incoming_is_eat_type
        timer_key = "eat" if incoming_is_eat else record_type

        timer = await self._get_active_timer(timer_key)

        # таймера нет -> создать новый active
        if not timer:
            if eat_alias:
                return {"status": "ok", "state": "not release", "text": "Нельзя создать таймер"}

            return await self._create_timer(record_type)

        record_timer_type = timer["record_type"]

        # создаем новый таймер если нужно
        if incoming_is_eat_type and record_timer_type != record_type:
            await self.stop_timer("eat")  # тихо останавливаем
            return await self._create_timer(record_type)

        # дальше неважно какой тип получится, работаем как обычно, потому что работаем с
        # record и его типом, даже если передаст юзер eat
        state = timer.get("timer_state", "")

        # если пауза, то запускаем
        if state == "pause":
            data = await self._resume_timer(timer)
            if eat_alias:
                # обновляем текст для понимая какой был реальный тип
                data['text'] = f"Таймер запущен для {TYPE_RU[record_timer_type]}"
            return data

        # 3) уже активен -> ничего не делаем
        if state == "active":
            self.logger.info("Таймер %s: запуск пропущен, уже активен", record_timer_type)
            text = f"Таймер уже запущен для {TYPE_RU[record_timer_type]}" if eat_alias else "Таймер уже запущен"
            return {"status": "ok", "state": "start", "text": text}

        # 4) странный статус
        self.logger.warning("Таймер %s: запуск пропущен, ошибка (state=%r)", record_type, state)
        return {"status": "ok", "state": "unknown", "text": "Неизвестный статус таймера"}

    async def _pause_timer(self, timer: dict) -> dict:
        now = utils.local_str()
        record_type = timer["record_type"]

        sd = erby_utils._build_service(
            cloud_id=timer["cloud_id"],
            record_type=record_type,
            start=timer["start"],
            date_updated=now,
            deleted=0,
            timer_state="pause",
            pause_duration=int(timer["pause_duration"]),
            last_pause_start=now,
            date_synced=None,
        )
        self.logger.info("Таймер %s: пауза", record_type)
        ok = await self._sync_data(service=sd)

        if not ok:
            return {"status": "error", "state": "ws_down", "text": "Нет соединения с сервером, команда не отправлена"}

        return {"status": "ok", "state": "pause", "text": "Таймер приостановлен"}

    async def pause_timer(self, record_type: str) -> dict:
        eat_alias = record_type == "eat"
        incoming_is_eat_type = record_type in EAT_TYPES
        incoming_is_eat = eat_alias or incoming_is_eat_type
        timer_key = "eat" if incoming_is_eat else record_type

        timer = await self._get_active_timer(timer_key)

        if not timer:
            return {"status": "ok", "state": "not active", "text": "Нет активного таймера"}

        record_timer_type = timer["record_type"]
        state = timer.get("timer_state", "")

        if state == "pause":
            self.logger.info("Таймер %s: пропуск паузы", record_timer_type)
            text = (
                f"Таймер уже приостановлен для {TYPE_RU[record_timer_type]}"
                if eat_alias else
                "Таймер уже приостановлен"
            )
            return {"status": "ok", "state": "pause", "text": text}

        if state == "active":
            data = await self._pause_timer(timer)
            if eat_alias:
                data["text"] = f"Таймер приостановлен для {TYPE_RU[record_timer_type]}"
            return data

        self.logger.warning("Таймер %s: пропуск паузы, ошибка (state=%r)", record_type, state)

        return {"status": "ok", "state": "unknown", "text": "Неизвестный статус таймера"}

    async def _stop_timer(self, timer_data: dict, record_type: str | None = None) -> bool:
        now = utils.local_str()
        rt = record_type or timer_data["record_type"]

        duration, pause_duration = erby_utils._compute_stop_durations(timer_data, now)

        cloud_id = timer_data["cloud_id"]
        start = timer_data["start"]

        # создаем запись
        record = erby_utils._build_record(
            cloud_id=cloud_id,
            record_type=rt,
            start=start,
            date_updated=now,
            duration=duration,
            pause_duration=pause_duration,
        )

        # переводим таймер в статус удален, deleted=1
        service = erby_utils._build_service(
            cloud_id=cloud_id,
            record_type=rt,
            start=start,
            date_updated=now,
            deleted=1,
            timer_state=timer_data.get("timer_state", "active"),
            pause_duration=pause_duration,
            last_pause_start=timer_data.get("last_pause_start"),
            date_synced=timer_data.get("date_synced"),
        )

        self.logger.info("Таймер %s: стоп", rt)

        ok = await self._sync_data(record=record, service=service)

        return ok

    async def stop_timer(self, record_type: str) -> dict:
        timers_count = 0

        for record_type_real in TIMERS.get(record_type, []):
            timer_data = await self._get_active_timer(record_type_real)
            if not timer_data:
                continue

            ok = await self._stop_timer(timer_data, record_type=record_type_real)
            if ok:
                timers_count += 1
            else:
                return {"status": "error", "state": "ws_down", "text": "Нет соединения, остановка не отправлена"}

        if timers_count == 1:
            return {"status": "ok", "state": "stop", "text": "Таймер остановлен"}

        if timers_count > 1:
            return {"status": "ok", "state": "stop", "text": "Таймеры остановлены"}

        self.logger.info("Нет активного таймера")

        return {"status": "ok", "state": "not_active_exist", "text": "Нет активного таймера"}

    async def delete_timer(self, record_type: str) -> dict:
        now = utils.local_str()

        timer_data = await self._get_active_timer(record_type)
        if not timer_data:
            self.logger.info('Нет таймера для удаления')
            return {"status": "ok", "state": "delete", "text": "Нет таймера для удаления"}

        duration, pause_duration = erby_utils._compute_stop_durations(timer_data, now)

        cloud_id = timer_data["cloud_id"]
        start = timer_data["start"]

        service = erby_utils._build_service(
            cloud_id=cloud_id,
            record_type=record_type,
            start=start,
            date_updated=now,
            deleted=1,
            timer_state=timer_data.get("timer_state", "active"),
            pause_duration=pause_duration,
            last_pause_start=timer_data.get("last_pause_start"),
            date_synced=timer_data.get("date_synced"),
        )

        self.logger.info("Таймер %s: удален", record_type)

        ok = await self._sync_data(service=service)

        if not ok:
            return {"status": "error", "state": "ws_down", "text": "Нет соединения с сервером, команда не отправлена"}

        return {"status": "ok", "state": "delete", "text": "Таймер удалён"}
    # ---------------- Helpers ----------------

    async def _get_active_timer(self, record_type: str):
        db = self.db
        if not db:
            return

        is_eat = (record_type == "eat")
        q_sql = sql.Q_SELECT_ACTIVE_TIMER_EAT if is_eat else sql.Q_SELECT_ACTIVE_TIMER
        params = None if is_eat else {"record_type": record_type}

        async with db.execute(q_sql, params) as cur:
            row = await cur.fetchone()
            if not row:
                return None

            return dict(row)

    async def get_last_sync_date(self) -> str:
        db = self.db
        if not db or self.stop_event.is_set():
            return DEFAULT_SYNC_DATE

        async with db.execute(sql.Q_SELECT_LAST_SYNC) as cur:
            row = await cur.fetchone()
            if row:
                return row["last_sync_utc"]
            else:
                return DEFAULT_SYNC_DATE

    async def get_last_time(self, human_type: str) -> dict:
        data = await self.get_last_times_all()
        if human_type in data:
            return data[human_type]

        return utils.humanize(None, human_type, human_type)

    async def get_last_times_all(self) -> dict[str, dict]:
        db = self.db
        if not db:
            return {}

        now = utils.local_str()

        moments: dict[str, arrow.Arrow] = {}

        async with db.execute(sql.Q_SELECT_LAST_MOMENTS, {"now": now}) as cur:
            rows = await cur.fetchall()

            for row in rows:
                record_type = row["record_type"]
                moment_str = row["last_moment"]
                if moment_str:
                    moments[record_type] = arrow.get(moment_str, FMT)

        results: dict[str, dict] = {}
        for sensor in SENSOR_TYPES:
            types = SENSOR_MAP.get(sensor, [sensor])
            best_moment: arrow.Arrow | None = None
            best_type: str | None = None
            for t in types:
                moment = moments.get(t)
                if moment and (best_moment is None or moment > best_moment):
                    best_moment = moment
                    best_type = t

            if not best_moment or not best_type:
                results[sensor] = utils.humanize(None, sensor, sensor)
            else:
                results[sensor] = utils.humanize(best_moment, best_type, sensor)

        return results

    async def get_last_payload(self) -> dict[str, str]:
        data = await self.get_last_times_all()
        if not data:
            return {"summary": "нет записей", "updated": utils.local_str()}

        seconds_list: list[tuple[int, str]] = []
        for key, item in data.items():
            seconds = item.get("seconds")
            if seconds is not None:
                seconds_list.append((int(seconds), key))

        if seconds_list:
            _, last_key = min(seconds_list, key=lambda x: x[0])
            summary = data[last_key]["text"]
        else:
            summary = "нет записей"

        payload: dict[str, str] = {"summary": summary}
        for key, item in data.items():
            payload[key] = item["text"]

        payload["updated"] = utils.local_str()
        return payload
