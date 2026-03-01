# -*- coding: utf-8 -*-
# sql.py

Q_RECORDS_CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS records (
        cloud_id TEXT PRIMARY KEY,
        user_id TEXT,
        last_editor_id TEXT,
        last_editor_device_id TEXT,

        profile_cloud_id TEXT,

        record_type TEXT,
        record_subtype TEXT,

        amount_type TEXT,
        amount REAL,
        amount2 REAL,
        value REAL,

        start TEXT,
        duration INTEGER,
        pause_duration INTEGER,

        date_created TEXT,
        date_updated TEXT,
        date_synced TEXT,

        deleted INTEGER,

        details TEXT,
        comment TEXT,
        reaction TEXT,

        raw_json TEXT
    );
"""

Q_SERVICE_CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS service_data (
        cloud_id TEXT PRIMARY KEY,
        user_id TEXT,
        last_editor_id TEXT,
        last_editor_device_id TEXT,

        profile_cloud_id TEXT,

        record_type TEXT,

        start TEXT,
        timer_state TEXT,
        last_pause_start TEXT,
        pause_duration INTEGER,
        max_duration INTEGER,

        date_updated TEXT,
        date_synced TEXT,

        deleted INTEGER,

        comment TEXT,
        reaction TEXT,

        raw_json TEXT
    );
"""

Q_SYNC_STATE_CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS sync_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        last_sync_utc TEXT
    );
"""


Q_UPSERT_RECORD = """
    INSERT OR REPLACE INTO records VALUES (
        :cloud_id, :user_id, :last_editor_id, :last_editor_device_id,
        :profile_cloud_id,
        :record_type, :record_subtype,
        :amount_type, :amount, :amount2, :value,
        :start, :duration, :pause_duration,
        :date_created, :date_updated, :date_synced,
        :deleted,
        :details, :comment, :reaction,
        :raw_json
    )
"""

Q_UPSERT_SERVICE = """
    INSERT OR REPLACE INTO service_data VALUES (
        :cloud_id, :user_id, :last_editor_id, :last_editor_device_id,
        :profile_cloud_id,
        :record_type,
        :start, :timer_state, :last_pause_start, :pause_duration, :max_duration,
        :date_updated, :date_synced,
        :deleted,
        :comment, :reaction,
        :raw_json
    )
"""

Q_UPSERT_SYNC_STATE = "INSERT OR REPLACE INTO sync_state VALUES (1, ?)"

SQL_TIMER_FIELDS = """
    cloud_id,
    user_id,
    last_editor_id,
    last_editor_device_id,
    profile_cloud_id,
    record_type,
    start,
    timer_state,
    last_pause_start,
    pause_duration,
    max_duration,
    date_updated,
    date_synced,
    deleted,
    comment,
    reaction
"""

Q_SELECT_ACTIVE_TIMER = f"""
    SELECT {SQL_TIMER_FIELDS}
    FROM service_data
    WHERE record_type = :record_type
      AND deleted = 0
    ORDER BY start DESC
    LIMIT 1
"""

Q_SELECT_ACTIVE_TIMER_EAT = f"""
    SELECT {SQL_TIMER_FIELDS}
    FROM service_data
    WHERE record_type IN ('left', 'right', 'liquid')
      AND deleted = 0
    ORDER BY start DESC
    LIMIT 1
"""

Q_SELECT_LAST_SYNC = """
    SELECT last_sync_utc
    FROM sync_state
    WHERE id=1
"""

Q_SELECT_LAST_SERVICE = """
    SELECT start, timer_state, last_pause_start
    FROM service_data
    WHERE record_type=?
    AND deleted=0
    ORDER BY start DESC
    LIMIT 1
"""

Q_SELECT_LAST_RECORD = """
    SELECT start, duration, pause_duration
    FROM records
    WHERE record_type=?
    AND deleted=0
    ORDER BY start DESC
    LIMIT 1
"""

Q_SELECT_LAST_MOMENTS = """
    WITH svc AS (
        SELECT
            record_type,
            CASE
                WHEN timer_state = 'active' THEN :now
                WHEN last_pause_start IS NOT NULL AND last_pause_start != '' THEN last_pause_start
                ELSE NULL
            END AS moment
        FROM service_data
        WHERE deleted = 0
    ),
    rec AS (
        SELECT
            record_type,
            SUBSTR(
                STRFTIME(
                    '%Y-%m-%d %H:%M:%f',
                    start,
                    '+' || (COALESCE(duration, 0) + COALESCE(pause_duration, 0)) || ' seconds'
                ),
                1,
                23
            ) AS moment
        FROM records
        WHERE deleted = 0
    ),
    unioned AS (
        SELECT record_type, moment FROM svc WHERE moment IS NOT NULL
        UNION ALL
        SELECT record_type, moment FROM rec WHERE moment IS NOT NULL
    )
    SELECT record_type, MAX(moment) AS last_moment
    FROM unioned
    GROUP BY record_type
"""
