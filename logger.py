# -*- coding: utf-8 -*-
# logger.py

import logging
import configparser
import atexit
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler
import threading
import time


# ======================================================
# НАСТРОЙКИ
# ======================================================

LOG_FORMAT = (
    "%(asctime)s %(levelname)s "
    "[%(name)s | %(filename)s:%(lineno)d] "
    "%(message)s"
)

DEFAULT_LOG_LEVEL = logging.INFO
BACKUP_DAYS = 7
HOT_RELOAD_INTERVAL = 5  # секунд

# logger.py лежит в корне проекта → рядом папка logs
BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"

# Опционально: config.ini с уровнем логирования
CONFIG_FILE = BASE_DIR / "config.ini"

# ======================================================
# ВНУТРЕННЯЯ ЛОГИКА (НЕ ТРОГАТЬ)
# ======================================================

_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

LOG_LEVEL_NAMES = tuple(_LOG_LEVELS.keys())

# --- dynamic filtering state ---
_CURRENT_ROOT_LEVEL = DEFAULT_LOG_LEVEL


class _WebsocketsDebugGate(logging.Filter):
    """
    Пропускаем DEBUG от websockets только когда общий уровень = DEBUG.
    Иначе режем только DEBUG, но оставляем INFO/WARNING/ERROR.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        global _CURRENT_ROOT_LEVEL
        if record.name.startswith("websockets") and record.levelno == logging.DEBUG:
            return _CURRENT_ROOT_LEVEL <= logging.DEBUG
        return True


def _get_log_level() -> int:
    if not CONFIG_FILE.exists():
        return DEFAULT_LOG_LEVEL

    try:
        config = configparser.ConfigParser()
        config.read(CONFIG_FILE, encoding="utf-8")

        level = config.get(
            "DEFAULT",
            "log_level",
            fallback=""
        ).upper()

        return _LOG_LEVELS.get(level, DEFAULT_LOG_LEVEL)

    except (OSError, configparser.Error):
        return DEFAULT_LOG_LEVEL


def _apply_library_levels(root_level: int) -> None:
    # websockets всегда DEBUG, а “видимость” управляется фильтром на handler
    for name in [
        "websockets",
        "websockets.client",
        "websockets.server",
        "websockets.protocol",
        "websockets.connection",
        "websockets.legacy",
        "websockets.legacy.client",
        "websockets.legacy.server",
        "websockets.http",
    ]:
        logging.getLogger(name).setLevel(logging.DEBUG)

    # прочие шумные оставляем тихими
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)


def _watch_log_level(logger: logging.Logger):
    last_level = logger.level

    while True:
        time.sleep(HOT_RELOAD_INTERVAL)
        new_level = _get_log_level()

        if new_level != last_level:
            logger.setLevel(new_level)
            global _CURRENT_ROOT_LEVEL
            _CURRENT_ROOT_LEVEL = new_level

            _apply_library_levels(new_level)
            logger.warning("Уровень лога изменен на %s", logging.getLevelName(new_level))
            last_level = new_level


def _create_logger() -> logging.Logger:
    logger = logging.getLogger()  # ROOT LOGGER

    if getattr(logger, "_configured", False):
        return logger

    root_level = _get_log_level()
    logger.setLevel(root_level)

    global _CURRENT_ROOT_LEVEL
    _CURRENT_ROOT_LEVEL = root_level

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "logfile.log"

    handler = TimedRotatingFileHandler(
        filename=str(log_file),
        when="midnight",
        interval=1,
        backupCount=BACKUP_DAYS,
        encoding="utf-8",
        delay=True,
        utc=False,
    )

    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.setLevel(logging.NOTSET)

    handler.addFilter(_WebsocketsDebugGate())

    logger.addHandler(handler)

    _apply_library_levels(root_level)

    def _shutdown():
        for h in logger.handlers[:]:
            try:
                h.flush()
                h.close()
            finally:
                logger.removeHandler(h)

    atexit.register(_shutdown)

    logger._configured = True  # type: ignore

    # горячая смена режима лога из config.ini
    watcher = threading.Thread(
        target=_watch_log_level,
        args=(logger,),
        daemon=True
    )
    watcher.start()

    return logger


def setup_logging():
    _create_logger()  # твоя логика с handler, formatter, rotation


def get_log_level_name() -> str:
    """Текущий уровень из config.ini (или дефолт) как строка: DEBUG/INFO/..."""
    # Обратная мапа int -> name
    inv = {v: k for k, v in _LOG_LEVELS.items()}
    return inv.get(_get_log_level(), "INFO")


def set_log_level_name(level: str, apply_now: bool = True) -> str:
    """
    Записывает log_level в config.ini и опционально применяет сразу к root logger.
    Возвращает нормализованное имя уровня.
    """
    level = str(level).upper()
    if level not in _LOG_LEVELS:
        raise ValueError(f"bad log level: {level}")

    config = configparser.ConfigParser()
    if CONFIG_FILE.exists():
        config.read(CONFIG_FILE, encoding="utf-8")
    if "DEFAULT" not in config:
        config["DEFAULT"] = {}

    config["DEFAULT"]["log_level"] = level
    with CONFIG_FILE.open("w", encoding="utf-8") as f:
        config.write(f)

    if apply_now:
        new_level = _LOG_LEVELS[level]
        logging.getLogger().setLevel(new_level)

        global _CURRENT_ROOT_LEVEL
        _CURRENT_ROOT_LEVEL = new_level

        _apply_library_levels(new_level)

    return level
