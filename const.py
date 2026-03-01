# -*- coding: utf-8 -*-
# const.py

SERVER_IP = '192.168.1.5'
SERVER_PORT = 8035

DEFAULT_SYNC_DATE = "2026-01-01 00:00:00.000"
FMT = "YYYY-MM-DD HH:mm:ss.SSS"
UTC_OFFSET_HOURS = 3

SQL_PRAGMA = {
    "PRAGMA journal_mode=WAL;",
    "PRAGMA synchronous=NORMAL;",
    "PRAGMA busy_timeout=5000;",
    "PRAGMA temp_store=MEMORY;",
    "PRAGMA wal_autocheckpoint=1000;",
}

RECORD_KEYS = [
    "cloud_id", "user_id", "last_editor_id", "last_editor_device_id",
    "profile_cloud_id",
    "record_type", "record_subtype",
    "amount_type", "amount", "amount2", "value",
    "start", "duration", "pause_duration",
    "date_created", "date_updated", "date_synced",
    "deleted",
    "details", "comment", "reaction",
]

SERVICE_KEYS = [
    "cloud_id", "user_id", "last_editor_id", "last_editor_device_id",
    "profile_cloud_id",
    "record_type",
    "start", "timer_state", "last_pause_start", "pause_duration", "max_duration",
    "date_updated", "date_synced",
    "deleted",
    "comment", "reaction",
]

# API/TYPE
RECORD_TYPES = {
    "pee": "pee",
    "poop": "defecation",
    "diaper": "diaper",
    "srugnul": "40ad12b7-9207-4e6b-b9d8-88b9d5b29c7c",
}

# API/TYPE
TIMER_TYPES = {
    "sleep": "sleep",
    "eat": "eat",
    "left": "left",
    "right": "right",
    "liquid": "liquid",
    "ikota": "ec94a5a6-94e7-4b6d-a8f3-e715d1462c0e",
    "stolbik": "d36c6c33-aa2b-4f70-af68-ac27ff3300ed",
}

EAT_TYPES = {"left", "right", "liquid"}

SENSOR_TYPES = ["sleep", "eat", "breast", "pee", "poop", "diaper"]

SENSOR_MAP = {
    "sleep": ["sleep"],
    "eat": ["left", "right", "liquid"],
    "breast": ["left", "right"],
    "pee": ["pee"],
    "poop": ["defecation"],
    "diaper": ["diaper"]
}

TIMERS = {
    "sleep": ["sleep"],
    "eat": ["left", "right", "liquid"],
    "left": ["left"],
    "right": ["right"],
    "liquid": ["liquid"],
    "ikota": ["ec94a5a6-94e7-4b6d-a8f3-e715d1462c0e"],
    "ec94a5a6-94e7-4b6d-a8f3-e715d1462c0e": ["ec94a5a6-94e7-4b6d-a8f3-e715d1462c0e"],
    "stolbik": ["d36c6c33-aa2b-4f70-af68-ac27ff3300ed"],
    "d36c6c33-aa2b-4f70-af68-ac27ff3300ed": ["d36c6c33-aa2b-4f70-af68-ac27ff3300ed"],
}

TYPE_RU = {
    "left": "левая грудь",
    "right": "правая грудь",
    "liquid": "бутылочка",
    "sleep": "сон",
    "pee": "писал",
    "defecation": "какал",
    "diaper": "подгузник",
    "ec94a5a6-94e7-4b6d-a8f3-e715d1462c0e": "икота",
    "ikota": "икота",
    "srugnul": "срыгнул",
    "40ad12b7-9207-4e6b-b9d8-88b9d5b29c7c": "срыгнул",
    "stolbik": "столбик",
    "d36c6c33-aa2b-4f70-af68-ac27ff3300ed": "столбик",
}

AMOUNT_TYPE = {
    "weight": "kg",
    "liquid": "ml",
    "height": "cm",
    "left": "ml",
    "right": "ml",
    "pumpBoth": "ml",
    "pee": "ml",
    "ec94a5a6-94e7-4b6d-a8f3-e715d1462c0e": "gr",
    "ikota": "gr",
}
