# -*- coding: utf-8 -*-
# erby_utils.py

import utils
from ws_const import BABY_ID, MAX_DURATION


def _build_record(
    *,
    cloud_id: str,
    record_type: str,
    start: str,
    date_updated: str,
    duration=0,
    pause_duration=0
) -> dict:
    amount_type = utils.get_amount_type(record_type)
    record_subtype = utils.get_record_subtype(record_type, start)

    record = {
        "cloud_id": cloud_id,
        "date_updated": date_updated,
        "deleted": 0,
        "record_type": record_type,
        "record_subtype": record_subtype,
        "profile_cloud_id": BABY_ID,
        "amount_type": amount_type,
        "amount": 0.0,
        "amount2": 0.0,
        "start": start,
        "value": 0,
        "reaction": "none",
        "duration": duration,
        "pause_duration": pause_duration
    }

    if (duration + pause_duration) == 0:
        record["date_created"] = date_updated

    return record


def _build_service(
    *,
    cloud_id: str,
    record_type: str,
    start: str,
    date_updated: str,
    deleted: int,
    timer_state: str,
    pause_duration: int,
    last_pause_start: str | None = None,
    date_synced: str | None = None,
) -> dict:
    sd = {
        "cloud_id": cloud_id,
        "date_updated": date_updated,
        "deleted": deleted,
        "profile_cloud_id": BABY_ID,
        "record_type": record_type,
        "start": start,
        "pause_duration": pause_duration,
        "max_duration": MAX_DURATION,
        "timer_state": timer_state,
        "reaction": "none",
    }

    if last_pause_start is not None:
        sd["last_pause_start"] = last_pause_start

    if date_synced is not None:
        sd["date_synced"] = date_synced

    return sd


def _compute_stop_durations(timer_data: dict, now: str) -> tuple[int, int]:
    """
    returns: (duration, pause_duration)
    """
    start = timer_data["start"]
    pause_duration = int(timer_data.get("pause_duration", 0))

    if timer_data.get("timer_state") == "pause":
        lps = timer_data.get("last_pause_start")
        if lps:
            pause_duration += int((utils._arrow(now) - utils._arrow(lps)).total_seconds())

    total = int((utils._arrow(now) - utils._arrow(start)).total_seconds())
    duration = total - pause_duration
    if duration < 0:
        duration = 0

    return duration, pause_duration
