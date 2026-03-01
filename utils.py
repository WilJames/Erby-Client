# -*- coding: utf-8 -*-
# utils.py

import arrow
import re
from const import FMT, TYPE_RU, AMOUNT_TYPE, UTC_OFFSET_HOURS


def norm(d: dict, keys: list[str]) -> dict:
    out = {k: d.get(k) for k in keys}
    return out


def local_now() -> arrow.Arrow:
    return arrow.utcnow().shift(hours=UTC_OFFSET_HOURS).replace(tzinfo=None)


def local_str() -> str:
    return local_now().format(FMT)


def utc_to_local_str(utc_str: str) -> str:
    syncDateLocal = arrow.get(utc_str, FMT).shift(hours=UTC_OFFSET_HOURS).replace(tzinfo=None).format(FMT)
    return syncDateLocal


def _arrow(s: str) -> arrow.Arrow:
    return arrow.get(s, FMT)


def utils_ru_plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    n = n % 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def _unit(n: int, one: str, few: str, many: str, omit_one: bool = True) -> str:
    # omit_one=True: 1 час -> "час", 1 минута -> "минута"
    if n == 1 and omit_one:
        return one
    return f"{n} {utils_ru_plural(n, one, few, many)}"


def _join2(a_text: str, b_text: str, b_value: int) -> str:
    # "и" только если второй кусок ровно 1, иначе просто пробел
    return f"{a_text} и {b_text}"


def _dist_from_seconds(seconds: int) -> str:
    if seconds < 60:
        return "меньше минуты"

    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60

    if days:
        d = _unit(days, "день", "дня", "дней", omit_one=True)
        if hours:
            h = _unit(hours, "час", "часа", "часов", omit_one=True)
            return _join2(d, h, hours)
        return d

    if hours:
        h = _unit(hours, "час", "часа", "часов", omit_one=True)
        if minutes:
            m = _unit(minutes, "минута", "минуты", "минут", omit_one=True)
            return _join2(h, m, minutes)
        return h

    # только минуты (сюда попадём если hours==0 and days==0)
    m = _unit(minutes, "минута", "минуты", "минут", omit_one=True)
    return m


def humanize(moment: arrow.Arrow | None, real_type: str, human_type: str) -> dict:
    now = local_now()
    type_ru = TYPE_RU.get(real_type, real_type)

    if moment is None:
        return {
            "seconds": 0,
            "type": real_type,
            "type_ru": type_ru,
            "text": "нет записи"
        }

    seconds = int((now - moment).total_seconds())
    if seconds < 0:
        seconds = 0

    dist = _dist_from_seconds(seconds)

    if human_type in {"eat", "breast"}:
        human_text = f"{type_ru.capitalize()} {dist} назад"
    else:
        human_text = f"{dist} назад"

    return {
        "seconds": seconds,
        "type": real_type,
        "type_ru": type_ru,
        "text": human_text
    }


def get_amount_type(record_type: str):
    return AMOUNT_TYPE.get(record_type, "none")


def get_record_subtype(record_type: str, timer_start: str):
    if record_type != "sleep":
        return "none"

    h = arrow.get(timer_start, FMT).datetime.hour

    if h >= 20 or h <= 7:
        return "sleep_night"

    return "sleep_day"


LOG_RE = re.compile(
    r'^(?P<date>\d{4}-\d{2}-\d{2})\s+'
    r'(?P<time>\d{2}:\d{2}:\d{2},\d{3})\s+'
    r'(?P<lvl>[A-Z]+)\s+'
    r'(?P<module>\[[^\]]+\])\s*'
    r'(?P<msg>.*)$'
)


def parse_log_line(s: str) -> dict:
    m = LOG_RE.match(s)
    if not m:
        return {"ok": False, "raw": s, "lvl": ""}

    d = m.groupdict()
    d["ok"] = True
    d["raw"] = s
    return d
