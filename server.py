"""MyChuannong campus-tools backend MVP.

Small HTTP + SQLite service for local development. It deliberately
does not include payment, reservation codes, public deployment, or academic-system
credentials.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import sqlite3
import time
import uuid
import warnings
from functools import lru_cache
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib import error as urllib_error
from urllib import request as urllib_request
from cryptography.fernet import Fernet, InvalidToken
from PIL import Image, UnidentifiedImageError
from core import (
    ApiError,
    ClosingConnection,
    DB_PATH,
    MAX_BODY,
    SESSION_SECONDS,
    BUSINESS_TIMEZONE_NAME,
    BUSINESS_TIMEZONE,
    business_day_bounds_ms,
    business_today,
    connection,
    json_response,
    now_ms,
    paginated_response,
    parse_pagination,
    required_text,
    row_dict,
)
from runtime_config import RUNTIME
from security import RateLimiter

ROOT = Path(__file__).resolve().parent
UPLOADS = RUNTIME.upload_path
ADMIN_WEB = ROOT / "admin"
HOST = RUNTIME.host
PORT = RUNTIME.port
RATE_LIMITER = RateLimiter()
SPORTS = {"BADMINTON", "TABLE_TENNIS"}
LOST_TYPES = {"LOST", "FOUND"}
LOST_STATUSES = {
    "LOST": {"SEARCHING", "FOUND"},
    "FOUND": {"WAITING", "CLAIMED", "RETURNED"},
}
FEEDBACK_CATEGORIES = {
    "FEATURE_SUGGESTION",
    "RESERVATION",
    "LOST_FOUND",
    "RESOURCE_LIBRARY",
    "ACCOUNT_SECURITY",
    "CONTENT_REPORT",
    "OTHER",
}
FEEDBACK_STATUSES = {"OPEN", "PROCESSING", "RESOLVED", "CLOSED"}
RESERVATION_PROFILE_MODES = {"OFF", "OPTIONAL", "REQUIRED"}
RESERVATION_PROFILE_VERSION = 1
ACCOUNT_STATUSES = {"ACTIVE", "LIMITED", "DISABLED", "PENDING_DELETION", "DELETED"}
USER_EVENT_RESULTS = {"SUCCESS", "FAILURE", "DENIED"}
USER_EVENT_RETENTION_DAYS = 180
ADMIN_ROLES = {
    "SUPER_ADMIN",
    "SPORTS_OPERATOR",
    "LOST_FOUND_OPERATOR",
    "LIBRARY_OPERATOR",
    "CONTENT_OPERATOR",
}
ROLE_PERMISSIONS = {
    "SUPER_ADMIN": {"*", "USER_PURGE"},
    "SPORTS_OPERATOR": {"SPORTS_VIEW", "SPORTS_MANAGE", "SPORTS_PII_VIEW"},
    "LOST_FOUND_OPERATOR": {"LOST_VIEW", "LOST_MANAGE", "LOST_PII_VIEW"},
    "LIBRARY_OPERATOR": {"LIBRARY_VIEW", "LIBRARY_MANAGE", "LIBRARY_PII_VIEW"},
    "CONTENT_OPERATOR": {"CONTENT_VIEW", "CONTENT_MANAGE"},
}
CONTROLLED_FEATURES = {"sports_reservation", "lost_found", "resource_library"}
DEFAULT_DEVICE_NICKNAME = "川农同学"
OFFICIAL_PACKAGE_NAME = "com.mychuannong.app"
# No inherited signing identity. Publishing requires an explicitly configured
# MyChuannong release certificate; an internal debug APK is not a release key.
OFFICIAL_SIGNING_CERTIFICATE_SHA256 = os.environ.get(
    "MY_CHUANNONG_SIGNING_CERTIFICATE_SHA256", ""
).strip().upper()

# App-config is a deliberately small, allow-listed control plane.  It can
# select existing native routes and official-site URLs, but cannot introduce
# code, arbitrary intents, JavaScript, or new business capabilities.
APP_CONFIG_SCHEMA_VERSION = 1
APP_CONFIG_MAX_BYTES = 256 * 1024
APP_CONFIG_MAX_NAVIGATION = 100
APP_CONFIG_MAX_CARDS = 50
APP_CONFIG_MAX_REMOTE_PAGES = 30
APP_CONFIG_MAX_RESOURCES = 200
APP_CONFIG_NATIVE_ACTIONS = {
    "schedule",
    "grade",
    "classroom",
    "homework",
    "electric",
    "exam",
    "calendar",
    "cet",
    "lost_found",
    "account",
    "document",
    "mandarin",
    "official_web",
}
APP_CONFIG_ICON_KEYS = {
    "calendar",
    "assessment",
    "meeting_room",
    "assignment",
    "electric",
    "event_note",
    "language",
    "find_in_page",
    "wallet",
    "folder",
    "record_voice_over",
    "globe",
}
APP_CONFIG_SECTIONS = {"learning", "life", "primary", "secondary"}
APP_CONFIG_FEATURE_KEYS = {
    "lost_found",
    "resource_library",
}
OFFICIAL_WEBSITE_CONFIG_HOST = ""

# A canonical product user is either a PHONE account or an otherwise-unlinked
# app identity.  Trusted devices are stable across installations; legacy
# DEVICE rows are retained as the best available anonymous identity when the
# client did not provide a stable device-scoped identifier.
CANONICAL_IDENTITY_ROWS_SQL = """
SELECT identity_key, identity_kind, first_seen_at, user_id, device_id
  FROM (
        SELECT 'USER:' || u.id AS identity_key,
               'USER' AS identity_kind,
               MIN(
                   CASE
                       WHEN ud.first_seen_at IS NULL OR u.created_at <= ud.first_seen_at
                           THEN u.created_at
                       ELSE ud.first_seen_at
                   END
               ) AS first_seen_at,
               u.id AS user_id,
               NULL AS device_id
          FROM users u
          LEFT JOIN user_devices ud ON ud.user_id=u.id
         WHERE u.role='USER' AND u.account_type='PHONE'
         GROUP BY u.id
        UNION ALL
        SELECT 'DEVICE:' || d.id AS identity_key,
               'DEVICE' AS identity_kind,
               d.first_seen_at AS first_seen_at,
               NULL AS user_id,
               d.id AS device_id
          FROM devices d
         WHERE NOT EXISTS (
                   SELECT 1
                     FROM user_devices ud
                     JOIN users u ON u.id=ud.user_id
                    WHERE ud.device_id=d.id
                      AND u.role='USER'
                      AND u.account_type='PHONE'
               )
        UNION ALL
        SELECT 'LEGACY:' || u.id AS identity_key,
               'LEGACY' AS identity_kind,
               u.created_at AS first_seen_at,
               u.id AS user_id,
               NULL AS device_id
          FROM users u
         WHERE u.role='USER'
           AND u.account_type='DEVICE'
           AND NOT EXISTS (
                   SELECT 1 FROM user_devices ud WHERE ud.user_id=u.id
               )
           AND NOT EXISTS (
                   SELECT 1 FROM devices d WHERE u.username='device_' || d.id
               )
       )
"""

# Activity still comes from the retained event stream, but the identity key is
# resolved through the current device-to-PHONE ownership relation first.  This
# is what prevents pre-registration DEVICE events and post-registration PHONE
# events from producing two active users for one person.
CANONICAL_EVENT_IDENTITIES_SQL = """
WITH phone_device_owners AS (
    SELECT device_id, user_id
      FROM (
            SELECT ud.device_id,
                   ud.user_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY ud.device_id
                       ORDER BY ud.last_seen_at DESC,
                                ud.first_seen_at ASC,
                                ud.user_id ASC
                   ) AS owner_rank
              FROM user_devices ud
              JOIN users u ON u.id=ud.user_id
             WHERE u.role='USER' AND u.account_type='PHONE'
           ) ranked
     WHERE owner_rank=1
), event_observations AS (
    SELECT e.id,
           e.created_at,
           e.user_id,
           u.role,
           u.account_type,
           COALESCE(
               e.device_id,
               i.device_id,
               (
                   SELECT ud.device_id
                     FROM user_devices ud
                    WHERE ud.user_id=e.user_id
                    ORDER BY ud.last_seen_at DESC,
                             ud.first_seen_at ASC,
                             ud.device_id ASC
                    LIMIT 1
               )
           ) AS observed_device_id
      FROM user_behavior_events e
      LEFT JOIN users u ON u.id=e.user_id
      LEFT JOIN installations i ON i.installation_id=e.installation_id
     WHERE e.result='SUCCESS'
), event_identities AS (
    SELECT o.id,
           o.created_at,
           CASE
               WHEN o.role='USER' AND o.account_type='PHONE' AND o.user_id IS NOT NULL
                   THEN 'USER:' || o.user_id
               WHEN p.user_id IS NOT NULL
                   THEN 'USER:' || p.user_id
               WHEN o.observed_device_id IS NOT NULL
                   THEN 'DEVICE:' || o.observed_device_id
               WHEN o.role='USER' AND o.account_type='DEVICE' AND o.user_id IS NOT NULL
                   THEN 'LEGACY:' || o.user_id
               ELSE NULL
           END AS identity_key
      FROM event_observations o
      LEFT JOIN phone_device_owners p ON p.device_id=o.observed_device_id
)
SELECT id, created_at, identity_key
  FROM event_identities
"""


def ensure_column(
    db: sqlite3.Connection, table: str, column: str, definition: str
) -> None:
    columns = {
        row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def canonical_identity_snapshot(
    db: sqlite3.Connection,
    day_start: int,
    day_end: int,
) -> dict[str, int]:
    """Return persistent canonical-user cohorts for the dashboard.

    The source of truth is the identity registry (PHONE users plus stable
    devices), not a count of user rows or retained event rows.  A device that
    is linked to any PHONE user is represented only by that user; this keeps an
    anonymous pre-registration session from becoming a second person.
    """
    row = db.execute(
        f"""SELECT
                   COUNT(DISTINCT identity_key) AS total_users,
                   COUNT(DISTINCT CASE WHEN identity_kind='USER'
                                       THEN identity_key END) AS registered_users,
                   COUNT(DISTINCT CASE WHEN identity_kind IN ('DEVICE','LEGACY')
                                       THEN identity_key END) AS guest_users,
                   COUNT(DISTINCT CASE
                       WHEN first_seen_at>=? AND first_seen_at<?
                           THEN identity_key END) AS today_new_users
              FROM ({CANONICAL_IDENTITY_ROWS_SQL}) identities""",
        (day_start, day_end),
    ).fetchone()
    registration_row = db.execute(
        """SELECT COUNT(DISTINCT id) AS today_new_registrations
             FROM users
            WHERE role='USER'
              AND account_type='PHONE'
              AND created_at>=?
              AND created_at<?""",
        (day_start, day_end),
    ).fetchone()
    return {
        "totalUsers": int(row["total_users"] or 0),
        "registeredUsers": int(row["registered_users"] or 0),
        "guestUsers": int(row["guest_users"] or 0),
        "todayNewUsers": int(row["today_new_users"] or 0),
        "todayNewRegistrations": int(
            registration_row["today_new_registrations"] or 0
        ),
    }


def canonical_activity_count(
    db: sqlite3.Connection,
    start: int,
    end: int | None = None,
) -> int:
    """Count distinct canonical users represented by successful activity."""
    clauses = ["identity_key IS NOT NULL", "created_at>=?"]
    params: list[Any] = [start]
    if end is not None:
        clauses.append("created_at<?")
        params.append(end)
    return int(
        db.execute(
            f"""SELECT COUNT(DISTINCT identity_key) AS count
                 FROM ({CANONICAL_EVENT_IDENTITIES_SQL}) identities
                WHERE {" AND ".join(clauses)}""",
            params,
        ).fetchone()["count"]
    )


def _short_business_code(prefix: str, value: str) -> str:
    """Return a stable support-facing code without exposing an internal identifier."""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10].upper()
    return f"{prefix}-{digest}"


def _canonical_identity_conditions(
    query: dict[str, str], day_start: int, day_end: int, current: int
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    values: list[Any] = []
    cohort = str(query.get("cohort", "all")).strip().lower()
    if cohort not in {"all", "registered", "guest"}:
        raise ApiError(422, "VALIDATION_ERROR", "用户范围筛选无效")
    if cohort == "registered":
        clauses.append("identities.identity_kind='USER'")
    elif cohort == "guest":
        clauses.append("identities.identity_kind IN ('DEVICE','LEGACY')")

    if str(query.get("firstSeen", "")).strip().lower() == "today":
        clauses.extend(["identities.first_seen_at>=?", "identities.first_seen_at<?"])
        values.extend([day_start, day_end])

    activity = str(query.get("activity", "")).strip().lower()
    starts = {
        "5m": current - 5 * 60 * 1000,
        "today": day_start,
        "7d": current - 7 * 86400000,
        "30d": current - 30 * 86400000,
    }
    if activity:
        if activity not in starts:
            raise ApiError(422, "VALIDATION_ERROR", "活跃时间筛选无效")
        clauses.append("activity.last_active_at>=?")
        values.append(starts[activity])
        if activity == "today":
            clauses.append("activity.last_active_at<?")
            values.append(day_end)
    return clauses, values


def canonical_identity_listing(
    db: sqlite3.Connection, query: dict[str, str]
) -> dict[str, Any]:
    """List the same canonical identities used by dashboard user metrics."""
    day_start, day_end = business_day_bounds_ms()
    current = int(query["asOf"]) if str(query.get("asOf", "")).isdigit() else now_ms()
    clauses, values = _canonical_identity_conditions(
        query, day_start, day_end, current
    )
    where_sql = " AND ".join(clauses) if clauses else "1=1"
    cte = f"""
        WITH identities AS ({CANONICAL_IDENTITY_ROWS_SQL}),
             activity AS (
                 SELECT identity_key,MIN(created_at) AS first_active_at,
                        MAX(created_at) AS last_active_at
                   FROM ({CANONICAL_EVENT_IDENTITIES_SQL}) events
                  WHERE identity_key IS NOT NULL
                  GROUP BY identity_key
             )
    """
    total = int(
        db.execute(
            cte
            + f"""SELECT COUNT(*) AS count
                    FROM identities
                    LEFT JOIN activity ON activity.identity_key=identities.identity_key
                   WHERE {where_sql}""",
            values,
        ).fetchone()["count"]
    )
    limit, offset = parse_pagination(query, default_limit=100, max_limit=200)
    rows = db.execute(
        cte
        + f"""SELECT identities.identity_key,identities.identity_kind,
                      identities.first_seen_at,identities.user_id,identities.device_id,
                      u.user_code,u.nickname,u.phone_masked,u.account_status,
                      COALESCE(activity.last_active_at,u.last_active_at,d.last_seen_at,
                               identities.first_seen_at) AS last_active_at,
                      CASE WHEN identities.identity_kind='USER' THEN
                           (SELECT COUNT(DISTINCT ud.device_id) FROM user_devices ud
                             WHERE ud.user_id=identities.user_id)
                           WHEN identities.identity_kind='DEVICE' THEN 1 ELSE 0 END
                           AS device_count,
                      CASE WHEN identities.identity_kind='USER' THEN
                           (SELECT COUNT(DISTINCT i.installation_id)
                              FROM user_devices ud JOIN installations i ON i.device_id=ud.device_id
                             WHERE ud.user_id=identities.user_id)
                           WHEN identities.identity_kind='DEVICE' THEN
                           (SELECT COUNT(*) FROM installations i
                             WHERE i.device_id=identities.device_id)
                           ELSE 0 END AS installation_count
                 FROM identities
                 LEFT JOIN activity ON activity.identity_key=identities.identity_key
                 LEFT JOIN users u ON u.id=identities.user_id
                 LEFT JOIN devices d ON d.id=identities.device_id
                WHERE {where_sql}
                ORDER BY identities.first_seen_at DESC,identities.identity_key ASC
                LIMIT ? OFFSET ?""",
        [*values, limit, offset],
    ).fetchall()
    items = []
    for row in rows:
        internal = str(row["user_id"] or row["device_id"] or row["identity_key"])
        registered = row["identity_kind"] == "USER"
        items.append(
            {
                "identityCode": row["user_code"]
                if registered and row["user_code"]
                else _short_business_code("GUEST", internal),
                "identityType": "REGISTERED" if registered else "GUEST",
                "displayName": row["nickname"] if registered else "未注册用户",
                "phoneMasked": row["phone_masked"] if registered else None,
                "firstSeenAt": row["first_seen_at"],
                "lastActiveAt": row["last_active_at"],
                "deviceCount": int(row["device_count"] or 0),
                "installationCount": int(row["installation_count"] or 0),
                "status": row["account_status"] if registered else "ANONYMOUS",
            }
        )
    return paginated_response(items, total, limit, offset)


def _device_conditions(
    query: dict[str, str], day_start: int, day_end: int, current: int
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    values: list[Any] = []
    window = str(query.get("window", "all")).strip().lower()
    if window not in {"all", "today", "5m", "7d", "30d"}:
        raise ApiError(422, "VALIDATION_ERROR", "设备时间筛选无效")
    if window == "today":
        clauses.extend(["d.created_at>=?", "d.created_at<?"])
        values.extend([day_start, day_end])
    elif window != "all":
        starts = {
            "5m": current - 5 * 60 * 1000,
            "7d": current - 7 * 86400000,
            "30d": current - 30 * 86400000,
        }
        clauses.append("d.last_seen_at>=?")
        values.append(starts[window])
    return clauses, values


def device_listing(db: sqlite3.Connection, query: dict[str, str]) -> dict[str, Any]:
    day_start, day_end = business_day_bounds_ms()
    current = int(query["asOf"]) if str(query.get("asOf", "")).isdigit() else now_ms()
    clauses, values = _device_conditions(query, day_start, day_end, current)
    where_sql = " AND ".join(clauses) if clauses else "1=1"
    total = int(
        db.execute(
            f"SELECT COUNT(*) AS count FROM devices d WHERE {where_sql}", values
        ).fetchone()["count"]
    )
    limit, offset = parse_pagination(query, default_limit=100, max_limit=200)
    rows = db.execute(
        f"""SELECT d.id,d.first_seen_at,d.last_seen_at,
                   d.first_app_version_name,d.first_app_version_code,
                   d.last_app_version_name,d.last_app_version_code,
                   (SELECT COUNT(*) FROM installations i WHERE i.device_id=d.id)
                       AS installation_count,
                   EXISTS(SELECT 1 FROM user_devices ud JOIN users u ON u.id=ud.user_id
                           WHERE ud.device_id=d.id AND u.role='USER'
                             AND u.account_type='PHONE') AS linked_registered_user
              FROM devices d WHERE {where_sql}
             ORDER BY d.first_seen_at DESC,d.id ASC LIMIT ? OFFSET ?""",
        [*values, limit, offset],
    ).fetchall()
    return paginated_response(
        [
            {
                "deviceCode": _short_business_code("DEVICE", str(row["id"])),
                "firstSeenAt": row["first_seen_at"],
                "lastSeenAt": row["last_seen_at"],
                "firstVersionName": row["first_app_version_name"],
                "firstVersionCode": row["first_app_version_code"],
                "lastVersionName": row["last_app_version_name"],
                "lastVersionCode": row["last_app_version_code"],
                "installationCount": int(row["installation_count"] or 0),
                "linkedRegisteredUser": bool(row["linked_registered_user"]),
            }
            for row in rows
        ],
        total,
        limit,
        offset,
    )


def device_count(db: sqlite3.Connection, query: dict[str, str]) -> int:
    day_start, day_end = business_day_bounds_ms()
    current = int(query["asOf"]) if str(query.get("asOf", "")).isdigit() else now_ms()
    clauses, values = _device_conditions(query, day_start, day_end, current)
    where_sql = " AND ".join(clauses) if clauses else "1=1"
    return int(
        db.execute(
            f"SELECT COUNT(*) AS count FROM devices d WHERE {where_sql}", values
        ).fetchone()["count"]
    )


def _installation_conditions(
    query: dict[str, str], day_start: int, day_end: int
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    values: list[Any] = []
    window = str(query.get("window", "all")).strip().lower()
    if window not in {"all", "today"}:
        raise ApiError(422, "VALIDATION_ERROR", "安装时间筛选无效")
    if window == "today":
        clauses.extend(["ranked.created_at>=?", "ranked.created_at<?"])
        values.extend([day_start, day_end])
    if str(query.get("reinstallation", "")).strip().lower() == "true":
        clauses.append("ranked.device_id IS NOT NULL AND ranked.install_rank>1")
    return clauses, values


INSTALLATION_RANKED_CTE = """
    WITH ranked AS (
        SELECT i.*,
               CASE WHEN i.device_id IS NULL THEN 1 ELSE
                    ROW_NUMBER() OVER (
                        PARTITION BY i.device_id
                        ORDER BY i.first_seen_at ASC,i.installation_id ASC
                    ) END AS install_rank
          FROM installations i
    )
"""


def installation_listing(
    db: sqlite3.Connection, query: dict[str, str]
) -> dict[str, Any]:
    day_start, day_end = business_day_bounds_ms()
    clauses, values = _installation_conditions(query, day_start, day_end)
    where_sql = " AND ".join(clauses) if clauses else "1=1"
    total = int(
        db.execute(
            INSTALLATION_RANKED_CTE
            + f"SELECT COUNT(*) AS count FROM ranked WHERE {where_sql}",
            values,
        ).fetchone()["count"]
    )
    limit, offset = parse_pagination(query, default_limit=100, max_limit=200)
    rows = db.execute(
        INSTALLATION_RANKED_CTE
        + f"""SELECT ranked.* FROM ranked WHERE {where_sql}
                ORDER BY ranked.first_seen_at DESC,ranked.installation_id ASC
                LIMIT ? OFFSET ?""",
        [*values, limit, offset],
    ).fetchall()
    return paginated_response(
        [
            {
                "installationCode": _short_business_code(
                    "INSTALL", str(row["installation_id"])
                ),
                "deviceCode": _short_business_code("DEVICE", str(row["device_id"]))
                if row["device_id"]
                else "LEGACY",
                "firstSeenAt": row["first_seen_at"],
                "lastActiveAt": row["last_seen_at"],
                "versionName": row["last_app_version_name"],
                "versionCode": row["last_app_version_code"],
                "isReinstallation": bool(row["device_id"] and row["install_rank"] > 1),
            }
            for row in rows
        ],
        total,
        limit,
        offset,
    )


def installation_count(db: sqlite3.Connection, query: dict[str, str]) -> int:
    day_start, day_end = business_day_bounds_ms()
    clauses, values = _installation_conditions(query, day_start, day_end)
    where_sql = " AND ".join(clauses) if clauses else "1=1"
    return int(
        db.execute(
            INSTALLATION_RANKED_CTE
            + f"SELECT COUNT(*) AS count FROM ranked WHERE {where_sql}",
            values,
        ).fetchone()["count"]
    )


def _user_code_components(created_at: int | None) -> tuple[str, int]:
    timestamp = int(created_at or now_ms())
    local_time = datetime.fromtimestamp(timestamp / 1000, tz=BUSINESS_TIMEZONE)
    prefix = local_time.strftime("%Y%m%d%H%M")
    return prefix, int(prefix)


def backfill_user_codes(db: sqlite3.Connection) -> None:
    """Assign stable, time-readable codes without using mutable profile data."""
    rows = db.execute(
        "SELECT id,created_at,user_code FROM users ORDER BY created_at ASC,id ASC"
    ).fetchall()
    counters: dict[str, int] = {}
    occupied_codes: set[str] = {
        str(row["user_code"])
        for row in rows
        if re.fullmatch(r"\d{17}", str(row["user_code"] or ""))
    }
    seen_codes: set[str] = set()
    updates: list[tuple[str, str]] = []
    for row in rows:
        prefix, _ = _user_code_components(row["created_at"])
        existing = str(row["user_code"] or "")
        if (
            re.fullmatch(r"\d{17}", existing)
            and existing not in seen_codes
        ):
            seen_codes.add(existing)
            if existing[:12] == prefix:
                sequence = int(existing[-5:])
                counters[prefix] = max(counters.get(prefix, 0), sequence)
            continue
        sequence = counters.get(prefix, 0) + 1
        while f"{prefix}{sequence:05d}" in occupied_codes:
            sequence += 1
        if sequence > 99999:
            raise RuntimeError(f"user_code minute capacity exceeded: {prefix}")
        counters[prefix] = sequence
        code = f"{prefix}{sequence:05d}"
        occupied_codes.add(code)
        updates.append((code, row["id"]))
    for code, user_id in updates:
        db.execute("UPDATE users SET user_code=? WHERE id=?", (code, user_id))
    for prefix, sequence in counters.items():
        db.execute(
            """INSERT INTO user_code_sequences(registration_minute,next_sequence)
               VALUES(?,?) ON CONFLICT(registration_minute) DO UPDATE SET
               next_sequence=MAX(user_code_sequences.next_sequence,excluded.next_sequence)""",
            (prefix, sequence + 1),
        )


def allocate_user_code(db: sqlite3.Connection, created_at: int | None = None) -> str:
    """Reserve one immutable code under a SQLite write lock.

    A failed user insert can leave a gap, but a business number is never reused.
    """
    timestamp = int(created_at or now_ms())
    prefix, _ = _user_code_components(timestamp)
    started_transaction = not db.in_transaction
    if started_transaction:
        db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute(
            "SELECT next_sequence FROM user_code_sequences WHERE registration_minute=?",
            (prefix,),
        ).fetchone()
        sequence = int(row["next_sequence"]) if row else 1
        if sequence > 99999:
            raise ApiError(409, "USER_CODE_CAPACITY_EXCEEDED", "当前分钟用户编号已达上限")
        next_sequence = sequence + 1
        if row:
            db.execute(
                "UPDATE user_code_sequences SET next_sequence=? WHERE registration_minute=?",
                (next_sequence, prefix),
            )
        else:
            db.execute(
                "INSERT INTO user_code_sequences(registration_minute,next_sequence) VALUES(?,?)",
                (prefix, next_sequence),
            )
        if started_transaction:
            db.commit()
        return f"{prefix}{sequence:05d}"
    except Exception:
        if started_transaction:
            db.rollback()
        raise


def sync_release_history(db: sqlite3.Connection) -> None:
    row = db.execute(
        "SELECT * FROM app_versions WHERE platform='android'"
    ).fetchone()
    if not row:
        return
    def existing_user_reference(value: str | None) -> str | None:
        if not value:
            return None
        return value if db.execute("SELECT 1 FROM users WHERE id=?", (value,)).fetchone() else None

    db.execute(
        """INSERT INTO app_release_versions(
               platform,version_code,version_name,update_message,download_url,
               apk_sha256,apk_size,package_name,signing_certificate_sha256,
               metadata_verified,status,published_at,created_at,created_by,
               updated_by,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(platform,version_code) DO UPDATE SET
               version_name=excluded.version_name,
               update_message=excluded.update_message,
               download_url=excluded.download_url,
               apk_sha256=excluded.apk_sha256,
               apk_size=excluded.apk_size,
               package_name=excluded.package_name,
               signing_certificate_sha256=excluded.signing_certificate_sha256,
               metadata_verified=excluded.metadata_verified,
               status=excluded.status,
               published_at=excluded.published_at,
               updated_by=excluded.updated_by,
               updated_at=excluded.updated_at""",
        (
            row["platform"],
            row["latest_version_code"],
            row["latest_version_name"],
            row["update_message"],
            row["download_url"],
            row["apk_sha256"],
            row["apk_size"],
            row["package_name"],
            row["signing_certificate_sha256"],
            row["metadata_verified"],
            row["status"],
            row["published_at"],
            row["created_at"] or row["updated_at"],
            existing_user_reference(row["created_by"]),
            existing_user_reference(row["updated_by"]),
            row["updated_at"],
        ),
    )


def update_policy_state(db: sqlite3.Connection) -> dict[str, Any]:
    """Return the live Android update policy with release facts attached."""

    release = db.execute(
        "SELECT latest_version_code FROM app_versions "
        "WHERE platform='android' AND status='PUBLISHED'"
    ).fetchone()
    policy = db.execute(
        "SELECT * FROM app_update_policies WHERE platform='android'"
    ).fetchone()
    latest_version_code = int(release["latest_version_code"]) if release else 0
    if not policy:
        return {
            "platform": "android",
            "updateEnabled": False,
            "forceUpdateEnabled": False,
            "latestVersionCode": latest_version_code,
            "minimumSupportedVersionCode": 1,
            "policyRevision": 0,
            "updatedAt": None,
            "updatedBy": None,
        }
    return {
        "platform": "android",
        "updateEnabled": bool(policy["update_enabled"]),
        "forceUpdateEnabled": bool(policy["force_update_enabled"]),
        "latestVersionCode": latest_version_code,
        "minimumSupportedVersionCode": int(
            policy["minimum_supported_version_code"]
        ),
        "policyRevision": int(policy["policy_revision"]),
        "updatedAt": policy["updated_at"],
        "updatedBy": policy["updated_by"],
    }


def update_decision(
    current_version_code: int,
    release: sqlite3.Row | None,
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate update availability separately from mandatory enforcement."""

    latest = int(release["latest_version_code"]) if release else 0
    update_available = bool(policy["updateEnabled"]) and latest > current_version_code
    minimum_violated = current_version_code < int(
        policy["minimumSupportedVersionCode"]
    )
    mandatory = (
        update_available
        and bool(policy["forceUpdateEnabled"])
        and minimum_violated
    )
    return {
        "updateAvailable": update_available,
        "minimumVersionViolated": minimum_violated,
        "mandatoryUpdate": mandatory,
        "decision": "FORCE" if mandatory else "OPTIONAL" if update_available else "NONE",
    }


def default_app_config() -> dict[str, Any]:
    """Return the V1.0.4-equivalent dynamic catalogue used for Config V1."""

    navigation = [
        ("schedule", "schedule", "课表", "calendar", 10, "learning", None, None),
        ("grade", "grade", "成绩查询", "assessment", 20, "learning", None, None),
        ("classroom", "classroom", "空教室", "meeting_room", 30, "learning", None, None),
        ("homework", "homework", "作业", "assignment", 40, "learning", None, None),
        ("electric", "electric", "电费查询", "electric", 50, "life", None, None),
        ("exam", "exam", "考试安排", "event_note", 60, "learning", None, None),
        ("cet", "cet", "四六级", "language", 70, "learning", None, None),
        ("lost_found", "lost_found", "失物招领", "find_in_page", 80, "life", "lost_found", None),
        ("document", "document", "资料库", "folder", 90, "learning", "resource_library", None),
        ("mandarin", "mandarin", "普通话测试", "record_voice_over", 100, "learning", None, None),
    ]
    return {
        "navigation": [
            {
                "id": item_id,
                "actionId": action_id,
                "title": title,
                "iconKey": icon_key,
                "order": order,
                "section": section,
                "visible": True,
                "featureKey": feature_key,
                "url": url,
            }
            for item_id, action_id, title, icon_key, order, section, feature_key, url in navigation
        ],
        "cards": [],
        "remotePages": [],
        "resources": [],
    }


def canonical_app_config_json(config: dict[str, Any]) -> str:
    try:
        return json.dumps(
            config,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ApiError(422, "INVALID_APP_CONFIG", "配置必须是可规范化的 JSON 对象") from exc


def app_config_hash(config: dict[str, Any]) -> str:
    canonical = canonical_app_config_json(config).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest().upper()


def _config_text(value: Any, field: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise ApiError(422, "INVALID_APP_CONFIG", f"{field} 必须是字符串")
    value = value.strip()
    if required and not value:
        raise ApiError(422, "INVALID_APP_CONFIG", f"{field} 不能为空")
    if len(value) > maximum:
        raise ApiError(422, "INVALID_APP_CONFIG", f"{field} 长度不能超过 {maximum} 个字符")
    return value


def validate_official_config_url(value: Any, field: str) -> str | None:
    if value in (None, ""):
        return None
    url = _config_text(value, field, 2048, required=True)
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ApiError(422, "UNSAFE_APP_CONFIG_URL", f"{field} 端口无效") from exc
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or hostname != OFFICIAL_WEBSITE_CONFIG_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or not parsed.path.startswith("/")
        or parsed.path.startswith("//")
    ):
        raise ApiError(
            422,
            "UNSAFE_APP_CONFIG_URL",
            f"{field} 只能使用官方 HTTPS 域名路径",
        )
    return url


def _config_int(value: Any, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ApiError(422, "INVALID_APP_CONFIG", f"{field} 必须是大于等于 {minimum} 的整数")
    return value


def _config_bool(value: Any, field: str, default: bool = True) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ApiError(422, "INVALID_APP_CONFIG", f"{field} 必须是布尔值")
    return value


def validate_app_config(raw: Any) -> dict[str, Any]:
    """Validate and normalize the safe, finite V1 dynamic-config surface."""

    if not isinstance(raw, dict):
        raise ApiError(422, "INVALID_APP_CONFIG", "config 必须是对象")
    allowed_root = {"navigation", "cards", "remotePages", "resources"}
    unknown_root = sorted(set(raw) - allowed_root)
    if unknown_root:
        raise ApiError(422, "INVALID_APP_CONFIG", f"config 包含未知字段：{','.join(unknown_root)}")

    navigation_raw = raw.get("navigation", [])
    if not isinstance(navigation_raw, list) or len(navigation_raw) > APP_CONFIG_MAX_NAVIGATION:
        raise ApiError(422, "INVALID_APP_CONFIG", "navigation 数量超出限制")
    navigation: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, item in enumerate(navigation_raw):
        if not isinstance(item, dict):
            raise ApiError(422, "INVALID_APP_CONFIG", f"navigation[{index}] 必须是对象")
        item_id = _config_text(item.get("id"), f"navigation[{index}].id", 64, required=True)
        if item_id in ids:
            raise ApiError(422, "INVALID_APP_CONFIG", f"navigation[{index}].id 重复")
        ids.add(item_id)
        action_id = _config_text(item.get("actionId"), f"navigation[{index}].actionId", 64, required=True)
        if action_id not in APP_CONFIG_NATIVE_ACTIONS:
            raise ApiError(422, "INVALID_APP_CONFIG", f"navigation[{index}].actionId 未注册")
        icon_key = _config_text(item.get("iconKey"), f"navigation[{index}].iconKey", 64, required=True)
        if icon_key not in APP_CONFIG_ICON_KEYS:
            raise ApiError(422, "INVALID_APP_CONFIG", f"navigation[{index}].iconKey 未注册")
        section = _config_text(item.get("section", "learning"), f"navigation[{index}].section", 32, required=True)
        if section not in APP_CONFIG_SECTIONS:
            raise ApiError(422, "INVALID_APP_CONFIG", f"navigation[{index}].section 未注册")
        feature_key = item.get("featureKey")
        if feature_key is not None:
            feature_key = _config_text(feature_key, f"navigation[{index}].featureKey", 64, required=True)
            if feature_key not in APP_CONFIG_FEATURE_KEYS:
                raise ApiError(422, "INVALID_APP_CONFIG", f"navigation[{index}].featureKey 不允许从配置开启")
        navigation.append(
            {
                "id": item_id,
                "actionId": action_id,
                "title": _config_text(item.get("title"), f"navigation[{index}].title", 40, required=True),
                "iconKey": icon_key,
                "order": _config_int(item.get("order", index), f"navigation[{index}].order"),
                "section": section,
                "visible": _config_bool(item.get("visible"), f"navigation[{index}].visible"),
                "featureKey": feature_key,
                "url": validate_official_config_url(item.get("url"), f"navigation[{index}].url"),
            }
        )
    if "schedule" not in ids:
        raise ApiError(422, "INVALID_APP_CONFIG", "navigation 必须保留课表入口")

    cards_raw = raw.get("cards", [])
    if not isinstance(cards_raw, list) or len(cards_raw) > APP_CONFIG_MAX_CARDS:
        raise ApiError(422, "INVALID_APP_CONFIG", "cards 数量超出限制")
    cards: list[dict[str, Any]] = []
    card_ids: set[str] = set()
    for index, item in enumerate(cards_raw):
        if not isinstance(item, dict):
            raise ApiError(422, "INVALID_APP_CONFIG", f"cards[{index}] 必须是对象")
        item_id = _config_text(item.get("id"), f"cards[{index}].id", 64, required=True)
        if item_id in card_ids:
            raise ApiError(422, "INVALID_APP_CONFIG", f"cards[{index}].id 重复")
        card_ids.add(item_id)
        card_type = _config_text(item.get("cardType", "notice"), f"cards[{index}].cardType", 32, required=True)
        if card_type not in {"notice", "link", "announcement", "remote_page"}:
            raise ApiError(422, "INVALID_APP_CONFIG", f"cards[{index}].cardType 未注册")
        cards.append(
            {
                "id": item_id,
                "cardType": card_type,
                "title": _config_text(item.get("title", ""), f"cards[{index}].title", 40),
                "description": _config_text(item.get("description", ""), f"cards[{index}].description", 500),
                "order": _config_int(item.get("order", index), f"cards[{index}].order"),
                "visible": _config_bool(item.get("visible"), f"cards[{index}].visible"),
                "actionId": _config_text(item.get("actionId", ""), f"cards[{index}].actionId", 64),
                "url": validate_official_config_url(item.get("url"), f"cards[{index}].url"),
            }
        )

    def normalize_link_list(raw_items: Any, field: str, maximum: int) -> list[dict[str, Any]]:
        if not isinstance(raw_items, list) or len(raw_items) > maximum:
            raise ApiError(422, "INVALID_APP_CONFIG", f"{field} 数量超出限制")
        result: list[dict[str, Any]] = []
        item_ids: set[str] = set()
        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                raise ApiError(422, "INVALID_APP_CONFIG", f"{field}[{index}] 必须是对象")
            item_id = _config_text(item.get("id"), f"{field}[{index}].id", 64, required=True)
            if item_id in item_ids:
                raise ApiError(422, "INVALID_APP_CONFIG", f"{field}[{index}].id 重复")
            item_ids.add(item_id)
            result.append(
                {
                    "id": item_id,
                    "title": _config_text(item.get("title"), f"{field}[{index}].title", 40, required=True),
                    "description": _config_text(item.get("description", ""), f"{field}[{index}].description", 500),
                    "url": validate_official_config_url(item.get("url"), f"{field}[{index}].url"),
                    "visible": _config_bool(item.get("visible"), f"{field}[{index}].visible"),
                    "order": _config_int(item.get("order", index), f"{field}[{index}].order"),
                }
            )
        return result

    normalized = {
        "navigation": navigation,
        "cards": cards,
        "remotePages": normalize_link_list(raw.get("remotePages", []), "remotePages", APP_CONFIG_MAX_REMOTE_PAGES),
        "resources": normalize_link_list(raw.get("resources", []), "resources", APP_CONFIG_MAX_RESOURCES),
    }
    if len(canonical_app_config_json(normalized).encode("utf-8")) > APP_CONFIG_MAX_BYTES:
        raise ApiError(422, "APP_CONFIG_TOO_LARGE", "配置解压后不能超过 256 KiB")
    return normalized


def serialize_app_config_revision(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    config = json.loads(row["config_json"])
    return {
        "revision": int(row["revision"]),
        "platform": row["platform"],
        "schemaVersion": int(row["schema_version"]),
        "configVersion": int(row["config_version"]),
        "minAppVersionCode": int(row["min_app_version_code"]),
        "maxAppVersionCode": row["max_app_version_code"],
        "configHash": row["config_hash"],
        "config": config,
        "status": row["status"],
        "createdBy": row["created_by"],
        "createdAt": row["created_at"],
        "updatedBy": row["updated_by"],
        "updatedAt": row["updated_at"],
    }


def ensure_default_app_config(db: sqlite3.Connection) -> None:
    """Seed Config V1 exactly once in a local/empty database."""

    state = db.execute(
        "SELECT platform FROM app_config_state WHERE platform='android'"
    ).fetchone()
    if state:
        return
    active = db.execute(
        "SELECT * FROM app_config_revisions WHERE platform='android' AND status='ACTIVE' "
        "ORDER BY config_version DESC LIMIT 1"
    ).fetchone()
    timestamp = now_ms()
    if active:
        db.execute(
            "INSERT INTO app_config_state(platform,active_revision,config_version,updated_at) VALUES('android',?,?,?)",
            (active["revision"], active["config_version"], timestamp),
        )
        return
    config = validate_app_config(default_app_config())
    config_hash = app_config_hash(config)
    cursor = db.execute(
        """INSERT INTO app_config_revisions(
               platform,schema_version,config_version,min_app_version_code,
               max_app_version_code,config_hash,config_json,status,created_at,updated_at
           ) VALUES('android',?,?,?,?,?,?,'ACTIVE',?,?)""",
        (
            APP_CONFIG_SCHEMA_VERSION,
            1,
            0,
            None,
            config_hash,
            canonical_app_config_json(config),
            timestamp,
            timestamp,
        ),
    )
    db.execute(
        "INSERT INTO app_config_state(platform,active_revision,config_version,updated_at) VALUES('android',?,?,?)",
        (cursor.lastrowid, 1, timestamp),
    )


def active_app_config(db: sqlite3.Connection) -> sqlite3.Row | None:
    state = db.execute(
        "SELECT active_revision FROM app_config_state WHERE platform='android'"
    ).fetchone()
    if not state or state["active_revision"] is None:
        return None
    return db.execute(
        "SELECT * FROM app_config_revisions WHERE revision=? AND platform='android'",
        (state["active_revision"],),
    ).fetchone()


def admin_app_config_state(db: sqlite3.Connection) -> dict[str, Any]:
    active = active_app_config(db)
    state = db.execute(
        "SELECT * FROM app_config_state WHERE platform='android'"
    ).fetchone()
    revisions = [
        serialize_app_config_revision(row)
        for row in db.execute(
            "SELECT * FROM app_config_revisions WHERE platform='android' "
            "ORDER BY config_version DESC LIMIT 50"
        )
    ]
    return {
        "activeRevision": int(state["active_revision"]) if state and state["active_revision"] else None,
        "configVersion": int(state["config_version"]) if state else 0,
        "active": serialize_app_config_revision(active),
        "revisions": revisions,
    }


def initialize_database() -> None:
    UPLOADS.mkdir(parents=True, exist_ok=True)
    _safe_chmod(UPLOADS, 0o750)
    with connection() as db:
        journal_mode = db.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        if str(journal_mode).lower() != "wal":
            raise RuntimeError("SQLite WAL mode could not be enabled")
        db.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
        ensure_default_app_config(db)
        ensure_column(db, "venues", "operating_status", "TEXT NOT NULL DEFAULT 'OPEN'")
        ensure_column(db, "venues", "maintenance_note", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "venues", "expected_restore_at", "INTEGER")
        ensure_column(db, "users", "account_type", "TEXT NOT NULL DEFAULT 'DEVICE'")
        ensure_column(db, "users", "user_code", "TEXT")
        ensure_column(db, "users", "nickname", "TEXT NOT NULL DEFAULT '川农同学'")
        ensure_column(db, "users", "phone_hash", "TEXT")
        ensure_column(db, "users", "phone_masked", "TEXT")
        ensure_column(db, "users", "phone_encrypted", "TEXT")
        ensure_column(db, "users", "student_id", "TEXT")
        ensure_column(db, "users", "real_name", "TEXT")
        ensure_column(db, "users", "campus_verified", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "users", "campus_verified_at", "INTEGER")
        ensure_column(db, "users", "reservation_student_id", "TEXT")
        ensure_column(db, "users", "reservation_name", "TEXT")
        ensure_column(db, "users", "reservation_profile_consented_at", "INTEGER")
        ensure_column(db, "users", "reservation_student_id_hash", "TEXT")
        ensure_column(db, "users", "reservation_name_hash", "TEXT")
        ensure_column(
            db, "users", "reservation_profile_version", "INTEGER NOT NULL DEFAULT 0"
        )
        ensure_column(db, "users", "reservation_profile_updated_at", "INTEGER")
        ensure_column(db, "users", "admin_permissions_json", "TEXT NOT NULL DEFAULT '[]'")
        ensure_column(db, "users", "admin_role", "TEXT")
        ensure_column(db, "users", "admin_enabled", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(db, "users", "account_status", "TEXT NOT NULL DEFAULT 'ACTIVE'")
        ensure_column(db, "users", "account_status_reason", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "users", "account_status_changed_at", "INTEGER")
        ensure_column(db, "users", "account_status_expires_at", "INTEGER")
        ensure_column(db, "users", "last_active_at", "INTEGER")
        ensure_column(db, "users", "first_login_at", "INTEGER")
        ensure_column(db, "app_versions", "apk_sha256", "TEXT")
        ensure_column(db, "app_versions", "apk_size", "INTEGER")
        ensure_column(db, "app_versions", "package_name", "TEXT")
        ensure_column(db, "app_versions", "signing_certificate_sha256", "TEXT")
        ensure_column(db, "app_versions", "metadata_verified", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "app_versions", "force_update", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "app_versions", "status", "TEXT NOT NULL DEFAULT 'DRAFT'")
        ensure_column(db, "app_versions", "published_at", "INTEGER")
        ensure_column(db, "app_versions", "created_at", "INTEGER")
        ensure_column(db, "app_versions", "created_by", "TEXT")
        ensure_column(db, "app_versions", "updated_by", "TEXT")
        # Migrate the legacy combined release/policy row exactly once. After
        # this bootstrap, app_update_policies is the live-control authority.
        # app_versions.force_update remains only as an old-tooling mirror.
        db.execute(
            """INSERT INTO app_update_policies(
                   platform,update_enabled,force_update_enabled,
                   minimum_supported_version_code,policy_revision,updated_at,updated_by
               )
               SELECT platform,
                      CASE WHEN status='PUBLISHED' THEN 1 ELSE 0 END,
                      force_update,minimum_version_code,1,updated_at,NULL
                 FROM app_versions
                WHERE platform='android'
               ON CONFLICT(platform) DO NOTHING"""
        )
        ensure_column(db, "remote_flags", "updated_by", "TEXT")
        ensure_column(db, "remote_flags", "change_note", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "announcements", "service_visible", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(db, "announcements", "notification_enabled", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "announcements", "popup_enabled", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "reservations", "refund_status", "TEXT NOT NULL DEFAULT 'NOT_APPLICABLE'")
        ensure_column(db, "reservations", "reservation_code", "TEXT")
        ensure_column(db, "reservations", "verification_status", "TEXT NOT NULL DEFAULT 'NOT_REQUIRED'")
        ensure_column(db, "reservations", "cancelled_at", "INTEGER")
        ensure_column(db, "reservations", "cancelled_by", "TEXT")
        ensure_column(db, "reservations", "cancel_reason", "TEXT")
        ensure_column(db, "reservations", "reservation_student_id_snapshot", "TEXT")
        ensure_column(db, "reservations", "reservation_name_snapshot", "TEXT")
        ensure_column(db, "feedback", "workflow_status", "TEXT NOT NULL DEFAULT 'OPEN'")
        ensure_column(db, "feedback", "title", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "feedback", "public_reply", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "feedback", "internal_note", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "feedback", "handled_by", "TEXT")
        ensure_column(db, "feedback", "resolved_at", "INTEGER")
        ensure_column(db, "user_behavior_events", "device_id", "TEXT")
        ensure_column(db, "user_behavior_events", "installation_id", "TEXT")
        ensure_column(
            db,
            "user_behavior_events",
            "identity_kind",
            "TEXT NOT NULL DEFAULT 'LEGACY'",
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS user_behavior_events_identity "
            "ON user_behavior_events(identity_kind, device_id, created_at DESC)"
        )
        ensure_column(
            db,
            "reservation_rules",
            "require_reservation_student_id",
            "INTEGER NOT NULL DEFAULT 0",
        )
        ensure_column(
            db,
            "reservation_rules",
            "reservation_profile_mode",
            "TEXT NOT NULL DEFAULT 'OPTIONAL'",
        )
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_phone_hash ON users(phone_hash) WHERE phone_hash IS NOT NULL")
        db.execute(
            """UPDATE users SET account_type=CASE
               WHEN role='ADMIN' THEN 'ADMIN'
               WHEN username LIKE 'device_%' THEN 'DEVICE'
               ELSE account_type END"""
        )
        # Legacy device accounts are real anonymous App users, not test fixtures.
        # Keep this migration deliberately narrow so user-selected nicknames are
        # never changed.
        db.execute(
            """UPDATE users SET nickname=?
               WHERE role='USER' AND account_type='DEVICE' AND nickname='测试设备'""",
            (DEFAULT_DEVICE_NICKNAME,),
        )
        if not RUNTIME.is_production:
            # Explicit development-only compatibility for local databases created
            # before role-scoped administrators existed. Production must preserve
            # invalid legacy rows so readiness can fail closed for operator review.
            db.execute(
                """UPDATE users SET admin_permissions_json='["*"]'
                   WHERE role='ADMIN' AND
                   (admin_permissions_json IS NULL OR admin_permissions_json='[]'
                    OR admin_permissions_json='["PII_VIEW"]')"""
            )
            db.execute(
                """UPDATE users SET admin_role='SUPER_ADMIN',admin_enabled=1
                   WHERE role='ADMIN' AND (admin_role IS NULL OR TRIM(admin_role)='')"""
            )
        # Legacy releases sometimes stored an operator label instead of a
        # users.id in nullable audit-reference columns.  The new release
        # history table enforces those foreign keys; preserve the version row
        # while clearing only references that cannot be resolved.
        for table_name in (
            "app_versions",
            "app_release_versions",
            "app_update_policies",
        ):
            if table_name == "app_update_policies":
                db.execute(
                    """UPDATE app_update_policies SET updated_by=NULL
                        WHERE updated_by IS NOT NULL
                          AND NOT EXISTS (
                              SELECT 1 FROM users
                               WHERE users.id=app_update_policies.updated_by
                          )"""
                )
                continue
            db.execute(
                f"""UPDATE {table_name} SET created_by=NULL
                    WHERE created_by IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM users WHERE users.id={table_name}.created_by
                      )"""
            )
            db.execute(
                f"""UPDATE {table_name} SET updated_by=NULL
                    WHERE updated_by IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM users WHERE users.id={table_name}.updated_by
                      )"""
            )
        db.execute(
            """UPDATE app_versions SET status='PUBLISHED',created_at=updated_at,
               published_at=COALESCE(published_at,updated_at)
               WHERE created_at IS NULL"""
        )
        db.execute("CREATE INDEX IF NOT EXISTS users_admin_role ON users(role,admin_role,admin_enabled)")
        backfill_user_codes(db)
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS users_user_code_unique "
            "ON users(user_code) WHERE user_code IS NOT NULL"
        )
        db.execute("CREATE INDEX IF NOT EXISTS users_last_active ON users(last_active_at)")
        db.execute("CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id,expires_at)")
        db.execute("CREATE INDEX IF NOT EXISTS reservations_booking ON reservations(booking_date,status)")
        db.execute(
            "UPDATE feedback SET workflow_status=status WHERE workflow_status IS NULL OR workflow_status=''"
        )
        db.execute(
            """UPDATE reservation_rules
               SET reservation_profile_mode=CASE
                   WHEN require_reservation_student_id=1 THEN 'REQUIRED'
                   ELSE COALESCE(NULLIF(reservation_profile_mode,''),'OPTIONAL')
               END
               WHERE id='default'"""
        )
        db.execute(
            """UPDATE users SET reservation_profile_version=?
               WHERE reservation_profile_consented_at IS NOT NULL
                 AND reservation_profile_version=0""",
            (RESERVATION_PROFILE_VERSION,),
        )
        db.execute(
            """UPDATE users SET reservation_profile_updated_at=reservation_profile_consented_at
               WHERE reservation_profile_consented_at IS NOT NULL
                 AND reservation_profile_updated_at IS NULL"""
        )
        for profile in db.execute(
            """SELECT id,reservation_student_id,reservation_name
               FROM users
               WHERE reservation_student_id IS NOT NULL OR reservation_name IS NOT NULL"""
        ).fetchall():
            db.execute(
                """UPDATE users SET reservation_student_id_hash=?,
                   reservation_name_hash=? WHERE id=?""",
                (
                    reservation_pii_digest("student-id", profile["reservation_student_id"]),
                    reservation_pii_digest("real-name", profile["reservation_name"]),
                    profile["id"],
                ),
            )
        timestamp = now_ms()
        venues = [] # No school facilities or operating data are inherited.
        slots = [
            ("08:00", "09:00"), ("09:00", "10:00"), ("10:00", "11:00"),
            ("14:00", "15:00"), ("15:00", "16:00"), ("16:00", "17:00"),
            ("19:00", "20:00"), ("20:00", "21:00"),
        ]
        for venue_id, sport, name, unit, count, price in venues:
            db.execute(
                """INSERT INTO venues(id,sport,name,unit_label,court_count,price_yuan,updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO NOTHING""",
                (venue_id, sport, name, unit, count, price, timestamp),
            )
            for start, end in slots:
                db.execute(
                    """INSERT INTO venue_time_slots(id,venue_id,start_time,end_time)
                       VALUES(?,?,?,?) ON CONFLICT(venue_id,start_time,end_time) DO NOTHING""",
                    (f"{venue_id}-{start}", venue_id, start, end),
                )
        db.execute(
            """INSERT INTO app_versions(platform,latest_version_code,minimum_version_code,
                       latest_version_name,update_message,created_at,updated_at)
               VALUES('android',2,1,'0.1.0-preview.2','内部预览，未正式发布',?,?)
               ON CONFLICT(platform) DO NOTHING""",
            (timestamp, timestamp),
        )
        db.execute(
            """INSERT INTO app_update_policies(
                   platform,update_enabled,force_update_enabled,
                   minimum_supported_version_code,policy_revision,updated_at
               ) VALUES('android',0,0,1,1,?)
               ON CONFLICT(platform) DO NOTHING""",
            (timestamp,),
        )
        sync_release_history(db)
        for key, enabled in (
            ("sports_reservation", 0),
            ("lost_found", 1),
            ("resource_library", 1),
            ("campus_channel", 0),
            ("buddy_club", 0),
        ):
            db.execute(
                """INSERT INTO remote_flags(key,enabled,updated_at) VALUES(?,?,?)
                   ON CONFLICT(key) DO NOTHING""",
                (key, enabled, timestamp),
            )
        db.execute(
            """INSERT INTO reservation_rules(
                   id,max_active_per_day,max_user_cancellations_per_day,
                   cancel_cutoff_minutes,restriction_hours_after_excess_cancel,updated_at
               ) VALUES('default',2,2,30,24,?)
               ON CONFLICT(id) DO NOTHING""",
            (timestamp,),
        )
        for feature_key in ("sports_reservation", "lost_found", "resource_library"):
            row = db.execute(
                "SELECT payload_json FROM remote_flags WHERE key=?", (feature_key,)
            ).fetchone()
            payload = json.loads(row["payload_json"] or "{}") if row else {}
            if "status" not in payload:
                payload.update(
                    {
                        "status": "OPEN",
                        "maintenanceNote": "",
                        "expectedRestoreAt": None,
                    }
                )
                db.execute(
                    "UPDATE remote_flags SET payload_json=?,updated_at=? WHERE key=?",
                    (json.dumps(payload, ensure_ascii=False), timestamp, feature_key),
                )
            if feature_key == "resource_library" and "allowGuestBrowsing" not in payload:
                payload["allowGuestBrowsing"] = False
                db.execute(
                    "UPDATE remote_flags SET payload_json=?,updated_at=? WHERE key=?",
                    (json.dumps(payload, ensure_ascii=False), timestamp, feature_key),
                )
        # Independent catalog starts empty; no original school resources are seeded.
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
    _safe_chmod(DB_PATH, 0o600)
    if not RUNTIME.is_production:
        bootstrap_admin()


def _safe_chmod(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        # Windows ACLs are managed outside Python; Linux deployment applies
        # the documented owner/mode before startup.
        pass


def database_integrity_check(database_path: Path | None = None) -> bool:
    try:
        with connection(database_path) as db:
            return db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    except sqlite3.Error:
        return False


def ensure_production_database_ready() -> None:
    if not database_integrity_check():
        raise RuntimeError("Production database integrity check failed")
    with connection() as db:
        valid_roles = tuple(sorted(ADMIN_ROLES))
        placeholders = ",".join("?" for _ in valid_roles)
        invalid_admin = db.execute(
            f"""SELECT COUNT(*) AS count FROM users
                WHERE role='ADMIN' AND (
                    admin_role IS NULL OR TRIM(admin_role)=''
                    OR admin_role NOT IN ({placeholders})
                )""",
            valid_roles,
        ).fetchone()["count"]
        if invalid_admin:
            raise RuntimeError(
                "Production database contains administrator records with an invalid role"
            )
        admin = db.execute(
            """SELECT 1 FROM users
               WHERE role='ADMIN' AND admin_role='SUPER_ADMIN'
                 AND admin_enabled=1 AND account_status='ACTIVE'
               LIMIT 1"""
        ).fetchone()
    if not admin:
        raise RuntimeError(
            "Production database has no enabled SUPER_ADMIN; run manage.py bootstrap-production"
        )


def password_record(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32
    )
    return base64.b64encode(digest).decode(), base64.b64encode(salt).decode()


def verify_password(password: str, encoded_hash: str, encoded_salt: str) -> bool:
    expected, _ = password_record(password, base64.b64decode(encoded_salt))
    return hmac.compare_digest(expected, encoded_hash)


def bootstrap_admin() -> None:
    username = os.getenv("MY_CHUANNONG_ADMIN_USERNAME", "").strip()
    password = os.getenv("MY_CHUANNONG_ADMIN_PASSWORD", "")
    if not username or len(password) < 12:
        return
    digest, salt = password_record(password)
    with connection() as db:
        if not db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            created_at = now_ms()
            db.execute(
                """INSERT INTO users(
                       id,username,user_code,password_hash,password_salt,role,account_type,nickname,
                       admin_permissions_json,admin_role,admin_enabled,account_status,created_at
                   ) VALUES(?,?,?,?,?,'ADMIN','ADMIN','本地管理员','["*"]',
                            'SUPER_ADMIN',1,'ACTIVE',?)""",
                (
                    str(uuid.uuid4()),
                    username,
                    allocate_user_code(db, created_at),
                    digest,
                    salt,
                    created_at,
                ),
            )


def issue_token(db: sqlite3.Connection, user_id: str) -> str:
    user = db.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        raise RuntimeError("Cannot issue a session for an unknown user")
    ttl_seconds = (
        RUNTIME.admin_session_seconds
        if user["role"] == "ADMIN"
        else SESSION_SECONDS
    )
    timestamp = now_ms()
    token = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)",
        (
            hashlib.sha256(token.encode()).hexdigest(),
            user_id,
            timestamp + ttl_seconds * 1000,
            timestamp,
        ),
    )
    return token


def parse_iso_date(value: str, *, future_allowed: bool = True) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ApiError(400, "INVALID_DATE", "日期格式必须为 YYYY-MM-DD") from exc
    if not future_allowed and parsed > date.today():
        raise ApiError(400, "INVALID_DATE", "日期不能晚于今天")
    return parsed.isoformat()


def normalize_phone(value: Any) -> str:
    phone = re.sub(r"\s+", "", str(value or ""))
    if not re.fullmatch(r"1[3-9]\d{9}", phone):
        raise ApiError(400, "INVALID_PHONE", "请输入有效的 11 位手机号")
    return phone


@lru_cache(maxsize=1)
def phone_hash_secret() -> bytes:
    configured = os.getenv("MY_CHUANNONG_PHONE_HASH_SECRET", "").encode()
    if len(configured) >= 32:
        return configured
    if RUNTIME.is_production:
        raise RuntimeError("MY_CHUANNONG_PHONE_HASH_SECRET is required in production")
    secret_path = DB_PATH.parent / ".phone-hash-secret"
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    if secret_path.is_file():
        existing = secret_path.read_bytes()
        if len(existing) >= 32:
            return existing
    generated = secrets.token_bytes(48)
    secret_path.write_bytes(generated)
    return generated


def phone_digest(phone: str) -> str:
    return hmac.new(phone_hash_secret(), phone.encode(), hashlib.sha256).hexdigest()


def reservation_pii_digest(kind: str, value: Any) -> str | None:
    normalized = re.sub(r"\s+", "", str(value or "")).casefold()
    if not normalized:
        return None
    payload = f"reservation-profile:{kind}:{normalized}".encode()
    return hmac.new(phone_hash_secret(), payload, hashlib.sha256).hexdigest()


def phone_mask(phone: str) -> str:
    return f"{phone[:3]}****{phone[-4:]}"


@lru_cache(maxsize=1)
def device_key_secret() -> bytes:
    """Derive a separate device HMAC key from the existing server secret."""
    configured = os.getenv("MY_CHUANNONG_DEVICE_KEY_SECRET", "").encode()
    source = configured if len(configured) >= 32 else phone_hash_secret()
    return hmac.new(source, b"my-chuannong-device-key-v1", hashlib.sha256).digest()


def normalize_device_scoped_id(value: Any) -> str | None:
    candidate = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", candidate):
        return None
    return candidate


def device_key_hash(device_scoped_id: str) -> str:
    return hmac.new(
        device_key_secret(),
        f"device-scoped-v1:{device_scoped_id}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _client_version(data: dict[str, Any]) -> tuple[str | None, int | None]:
    version_name = str(data.get("versionName", "")).strip()[:40] or None
    raw_code = data.get("versionCode")
    try:
        version_code = int(raw_code) if raw_code not in (None, "") else None
    except (TypeError, ValueError):
        version_code = None
    if version_code is not None and version_code < 1:
        version_code = None
    return version_name, version_code


def record_device_identity(
    db: sqlite3.Connection,
    data: dict[str, Any],
    *,
    user_id: str | None = None,
    create_anonymous_user: bool = False,
) -> dict[str, Any]:
    """Record an installation and, when needed, one stable anonymous user.

    V1.0.3 payloads omit ``deviceScopedId`` and are retained as legacy
    installations. They never enter trusted device counts.
    """
    installation_id = str(data.get("installationId", "")).strip()
    if not installation_id or len(installation_id) > 100:
        raise ApiError(400, "INVALID_INSTALLATION_ID", "安装实例标识无效")
    scoped_id = normalize_device_scoped_id(data.get("deviceScopedId"))
    version_name, version_code = _client_version(data)
    timestamp = now_ms()
    db.execute("BEGIN IMMEDIATE")
    try:
        trusted_device_id: str | None = None
        if scoped_id:
            key_hash = device_key_hash(scoped_id)
            device = db.execute(
                "SELECT * FROM devices WHERE device_key_hash=?", (key_hash,)
            ).fetchone()
            if not device:
                trusted_device_id = str(uuid.uuid4())
                db.execute(
                    """INSERT INTO devices(
                           id,device_key_hash,first_seen_at,last_seen_at,
                           first_app_version_name,first_app_version_code,
                           last_app_version_name,last_app_version_code,
                           install_count,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,0,?,?)""",
                    (
                        trusted_device_id,
                        key_hash,
                        timestamp,
                        timestamp,
                        version_name,
                        version_code,
                        version_name,
                        version_code,
                        timestamp,
                        timestamp,
                    ),
                )
            else:
                trusted_device_id = device["id"]
                db.execute(
                    """UPDATE devices SET last_seen_at=?,last_app_version_name=?,
                       last_app_version_code=?,updated_at=? WHERE id=?""",
                    (timestamp, version_name, version_code, timestamp, device["id"]),
                )

        installation = db.execute(
            "SELECT * FROM installations WHERE installation_id=?", (installation_id,)
        ).fetchone()
        if not installation:
            db.execute(
                """INSERT INTO installations(
                       installation_id,device_id,first_seen_at,last_seen_at,
                       first_app_version_name,first_app_version_code,
                       last_app_version_name,last_app_version_code,legacy,
                       created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    installation_id,
                    trusted_device_id,
                    timestamp,
                    timestamp,
                    version_name,
                    version_code,
                    version_name,
                    version_code,
                    0 if trusted_device_id else 1,
                    timestamp,
                    timestamp,
                ),
            )
            if trusted_device_id:
                db.execute(
                    "UPDATE devices SET install_count=install_count+1,updated_at=? WHERE id=?",
                    (timestamp, trusted_device_id),
                )
        else:
            # Never silently reassign an installation to a different device.
            # A replay or malformed client is retained as legacy evidence.
            if installation["device_id"] not in (None, trusted_device_id):
                trusted_device_id = None
            elif installation["device_id"] is None and trusted_device_id:
                db.execute(
                    """UPDATE installations SET device_id=?,legacy=0,
                       last_seen_at=?,last_app_version_name=?,last_app_version_code=?,
                       updated_at=? WHERE installation_id=?""",
                    (
                        trusted_device_id,
                        timestamp,
                        version_name,
                        version_code,
                        timestamp,
                        installation_id,
                    ),
                )
                db.execute(
                    "UPDATE devices SET install_count=install_count+1,updated_at=? WHERE id=?",
                    (timestamp, trusted_device_id),
                )
            else:
                db.execute(
                    """UPDATE installations SET last_seen_at=?,last_app_version_name=?,
                       last_app_version_code=?,updated_at=? WHERE installation_id=?""",
                    (timestamp, version_name, version_code, timestamp, installation_id),
                )

        anonymous_user_id: str | None = None
        if create_anonymous_user:
            if trusted_device_id:
                row = db.execute(
                    """SELECT u.* FROM users u JOIN user_devices ud ON ud.user_id=u.id
                       WHERE ud.device_id=? AND u.role='USER' AND u.account_type='DEVICE'
                         AND u.account_status!='DELETED'
                       ORDER BY ud.first_seen_at ASC LIMIT 1""",
                    (trusted_device_id,),
                ).fetchone()
                if row:
                    anonymous_user_id = row["id"]
                else:
                    legacy = db.execute(
                        """SELECT * FROM users WHERE username=? AND role='USER'
                           AND account_type='DEVICE' AND account_status!='DELETED'""",
                        (device_username(installation_id),),
                    ).fetchone()
                    if legacy:
                        anonymous_user_id = legacy["id"]
                    else:
                        anonymous_user_id = str(uuid.uuid4())
                        digest, salt = password_record(secrets.token_urlsafe(32))
                        created_at = now_ms()
                        db.execute(
                            """INSERT INTO users(
                                   id,username,user_code,password_hash,password_salt,
                                   role,account_type,nickname,account_status,created_at
                               ) VALUES(?,?,?,?,?,'USER','DEVICE',?,'ACTIVE',?)""",
                            (
                                anonymous_user_id,
                                f"device_{trusted_device_id}",
                                allocate_user_code(db, created_at),
                                digest,
                                salt,
                                DEFAULT_DEVICE_NICKNAME,
                                created_at,
                            ),
                        )
                db.execute(
                    """INSERT INTO user_devices(user_id,device_id,first_seen_at,last_seen_at)
                       VALUES(?,?,?,?) ON CONFLICT(user_id,device_id) DO UPDATE SET
                       last_seen_at=excluded.last_seen_at""",
                    (anonymous_user_id, trusted_device_id, timestamp, timestamp),
                )
            else:
                legacy = db.execute(
                    "SELECT * FROM users WHERE username=? AND role='USER' AND account_type='DEVICE'",
                    (device_username(installation_id),),
                ).fetchone()
                if legacy and legacy["account_status"] != "DELETED":
                    anonymous_user_id = legacy["id"]
                else:
                    anonymous_user_id = str(uuid.uuid4())
                    digest, salt = password_record(secrets.token_urlsafe(32))
                    created_at = now_ms()
                    db.execute(
                        """INSERT INTO users(
                               id,username,user_code,password_hash,password_salt,
                               role,account_type,nickname,account_status,created_at
                           ) VALUES(?,?,?,?,?,'USER','DEVICE',?,'ACTIVE',?)""",
                        (
                            anonymous_user_id,
                            device_username(installation_id),
                            allocate_user_code(db, created_at),
                            digest,
                            salt,
                            DEFAULT_DEVICE_NICKNAME,
                            created_at,
                        ),
                    )

        if user_id and trusted_device_id:
            db.execute(
                """INSERT INTO user_devices(user_id,device_id,first_seen_at,last_seen_at)
                   VALUES(?,?,?,?) ON CONFLICT(user_id,device_id) DO UPDATE SET
                   last_seen_at=excluded.last_seen_at""",
                (user_id, trusted_device_id, timestamp, timestamp),
            )
        db.commit()
        return {
            "installation_id": installation_id,
            "device_id": trusted_device_id,
            "anonymous_user_id": anonymous_user_id,
            "trusted": bool(trusted_device_id),
        }
    except Exception:
        db.rollback()
        raise


@lru_cache(maxsize=1)
def pii_cipher() -> Fernet:
    configured = os.getenv("MY_CHUANNONG_PII_ENCRYPTION_KEY", "").strip().encode()
    key_path = DB_PATH.parent / ".pii-encryption-key"
    if configured:
        key = configured
    elif RUNTIME.is_production:
        raise RuntimeError("MY_CHUANNONG_PII_ENCRYPTION_KEY is required in production")
    elif key_path.is_file():
        key = key_path.read_bytes().strip()
    else:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        key_path.write_bytes(key)
    try:
        return Fernet(key)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("MY_CHUANNONG_PII_ENCRYPTION_KEY 配置无效") from exc


def encrypt_phone(phone: str) -> str:
    return pii_cipher().encrypt(phone.encode()).decode()


def decrypt_phone(value: str | None) -> str | None:
    if not value:
        return None
    try:
        phone = pii_cipher().decrypt(value.encode()).decode()
        return phone if re.fullmatch(r"1[3-9]\d{9}", phone) else None
    except (InvalidToken, ValueError, UnicodeDecodeError):
        return None


def normalize_reservation_student_id(value: Any, *, required: bool = False) -> str | None:
    student_id = re.sub(r"\s+", "", str(value or ""))[:32]
    if not student_id:
        if required:
            raise ApiError(400, "RESERVATION_PROFILE_REQUIRED", "请先填写预约识别学号")
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,32}", student_id):
        raise ApiError(400, "INVALID_RESERVATION_STUDENT_ID", "预约识别学号格式不正确")
    return student_id


def normalize_reservation_name(value: Any, *, required: bool = False) -> str | None:
    name = re.sub(r"\s+", " ", str(value or "").strip())
    if not name:
        if required:
            raise ApiError(400, "RESERVATION_PROFILE_REQUIRED", "请填写真实姓名")
        return None
    if not 2 <= len(name) <= 40 or re.search(r"[<>]", name):
        raise ApiError(400, "INVALID_RESERVATION_NAME", "真实姓名格式不正确")
    return name


def mask_reservation_student_id(value: Any) -> str | None:
    text = str(value or "")
    if not text:
        return None
    if len(text) <= 4:
        return "*" * len(text)
    return f"{text[:2]}{'*' * max(2, len(text) - 4)}{text[-2:]}"


def mask_reservation_name(value: Any) -> str | None:
    text = str(value or "")
    if not text:
        return None
    if len(text) == 1:
        return "*"
    if len(text) == 2:
        return f"{text[0]}*"
    return f"{text[0]}{'*' * (len(text) - 2)}{text[-1]}"


def mask_admin_user_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data.pop("phone_encrypted", None)
    if "reservation_student_id" in data:
        data["reservation_student_id"] = mask_reservation_student_id(
            data["reservation_student_id"]
        )
    if "reservation_name" in data:
        data["reservation_name"] = mask_reservation_name(data["reservation_name"])
    return data


def full_admin_user_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """SUPER_ADMIN view: complete business profile, never credentials."""
    data = dict(row)
    encrypted = data.pop("phone_encrypted", None)
    data["phone"] = decrypt_phone(encrypted) if encrypted else None
    return data


def mask_admin_reservation_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = mask_admin_user_row(row)
    if "reservation_student_id_snapshot" in data:
        data["reservation_student_id_snapshot"] = mask_reservation_student_id(
            data["reservation_student_id_snapshot"]
        )
    if "reservation_name_snapshot" in data:
        data["reservation_name_snapshot"] = mask_reservation_name(
            data["reservation_name_snapshot"]
        )
    return data


def full_sports_reservation_row(
    row: sqlite3.Row | dict[str, Any],
) -> dict[str, Any]:
    """Complete reservation contact/profile data for the sports workspace only."""
    data = dict(row)
    encrypted = data.pop("phone_encrypted", None)
    data["phone"] = decrypt_phone(encrypted) if encrypted else None
    # Queries feeding this serializer select only reservation and the minimum
    # sports-account fields; credentials and cross-module data are never selected.
    return data


def normalize_feedback_category(value: Any) -> str:
    raw = str(value or "").strip().upper()
    legacy = {
        "问题反馈": "OTHER",
        "功能建议": "FEATURE_SUGGESTION",
        "体验改进": "FEATURE_SUGGESTION",
        "其他": "OTHER",
    }
    category = legacy.get(str(value or "").strip(), raw)
    if category not in FEEDBACK_CATEGORIES:
        raise ApiError(400, "INVALID_FEEDBACK_CATEGORY", "反馈类型无效")
    return category


def admin_permissions(user: sqlite3.Row) -> set[str]:
    role = admin_role(user)
    return set(ROLE_PERMISSIONS.get(role, set()))


def admin_role(user: sqlite3.Row | dict[str, Any]) -> str:
    data = dict(user)
    value = data.get("admin_role")
    if value in ADMIN_ROLES:
        return str(value)
    return ""


def is_super_admin(user: sqlite3.Row | dict[str, Any]) -> bool:
    return admin_role(user) == "SUPER_ADMIN"


def require_admin_permission(user: sqlite3.Row, permission: str) -> None:
    permissions = admin_permissions(user)
    if "*" not in permissions and permission not in permissions:
        raise ApiError(403, "ADMIN_PERMISSION_REQUIRED", "当前管理员没有此敏感操作权限")


def require_admin_path_access(user: sqlite3.Row, method: str, path: str) -> None:
    """Enforce module isolation before any admin route reads data."""
    if is_super_admin(user):
        return
    role = admin_role(user)
    if not bool(user["admin_enabled"]):
        raise ApiError(403, "ADMIN_DISABLED", "管理员账号已停用")
    allowed = False
    if role == "SPORTS_OPERATOR":
        allowed = any(token in path for token in ("/venues", "/inventory", "/reservations", "/reservation-rules", "/overview/sports"))
    elif role == "LOST_FOUND_OPERATOR":
        allowed = "/lost-found" in path or "/overview/lost-found" in path
    elif role == "LIBRARY_OPERATOR":
        allowed = any(token in path for token in ("/resources", "/resource-complaints", "/resource-activity", "/overview/library"))
    elif role == "CONTENT_OPERATOR":
        allowed = any(
            token in path
            for token in ("/announcements", "/website-announcements", "/overview/content")
        )
    if not allowed:
        raise ApiError(403, "ADMIN_MODULE_FORBIDDEN", "当前管理员无权访问该业务模块")


def require_account_operation(user: sqlite3.Row) -> None:
    if user["account_status"] in {"LIMITED", "SUSPENDED"}:
        reason = user["account_status_reason"] or "账号当前限制敏感操作，请联系管理员"
        raise ApiError(403, "ACCOUNT_OPERATION_RESTRICTED", reason)


def restore_expired_account_limit(
    db: sqlite3.Connection, user: sqlite3.Row
) -> sqlite3.Row:
    expires_at = user["account_status_expires_at"]
    if (
        user["account_status"] == "LIMITED"
        and expires_at is not None
        and int(expires_at) <= now_ms()
    ):
        timestamp = now_ms()
        db.execute(
            """UPDATE users SET account_status='ACTIVE',account_status_reason='',
               account_status_changed_at=?,account_status_expires_at=NULL WHERE id=?""",
            (timestamp, user["id"]),
        )
        write_user_event(
            db,
            user["id"],
            "ACCOUNT",
            "ACCOUNT_LIMIT_EXPIRED",
            "SUCCESS",
            "USER",
            user["id"],
        )
        return db.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    return user


def append_user_search(
    clauses: list[str],
    values: list[Any],
    raw_term: str | None,
    alias: str = "u",
) -> None:
    term = str(raw_term or "").strip()[:100]
    if not term:
        return
    compact = re.sub(r"\s+", "", term)
    if re.fullmatch(r"1[3-9]\d{9}", compact):
        clauses.append(f"{alias}.phone_hash=?")
        values.append(phone_digest(compact))
        return
    if re.fullmatch(r"\d{17}", compact):
        clauses.append(f"{alias}.user_code=?")
        values.append(compact)
        return
    if re.fullmatch(r"[A-Za-z0-9_-]{6,32}", compact):
        clauses.append(
            f"({alias}.reservation_student_id_hash=? OR {alias}.id=?)"
        )
        values.extend(
            [reservation_pii_digest("student-id", compact), compact]
        )
        return
    if 2 <= len(term) <= 40 and re.fullmatch(r"[\u3400-\u9fff·.\- ]+", term):
        clauses.append(f"{alias}.reservation_name_hash=?")
        values.append(reservation_pii_digest("real-name", term))
        return
    token = f"%{term}%"
    clauses.append(
        f"""({alias}.nickname LIKE ? OR {alias}.phone_masked LIKE ?
            OR {alias}.id LIKE ? OR {alias}.user_code LIKE ?)"""
    )
    values.extend([token, token, token, token])


def validate_external_url(value: Any, *, required: bool = True) -> str | None:
    url = str(value or "").strip()[:1000]
    if not url:
        if required:
            raise ApiError(400, "INVALID_RESOURCE_URL", "资料链接不能为空")
        return None
    parsed = urlparse(url)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ApiError(400, "INVALID_RESOURCE_URL", "仅允许安全的 HTTP/HTTPS 资料链接")
    host = parsed.hostname.lower()
    if host in {"0.0.0.0"} or host.endswith(".local"):
        raise ApiError(400, "INVALID_RESOURCE_URL", "资料链接目标不可用")
    return url


def seed_resources(db, timestamp):
    return # Intentionally empty for the new school, management capability retained.


def device_username(installation_id: str) -> str:
    return "device_" + hashlib.sha256(installation_id.encode()).hexdigest()[:32]


def migrate_device_data(
    db: sqlite3.Connection, installation_id: str | None, target_user_id: str
) -> bool:
    if not installation_id:
        return False
    source = db.execute(
        "SELECT * FROM users WHERE username=? AND account_type='DEVICE'",
        (device_username(installation_id),),
    ).fetchone()
    if not source:
        source = db.execute(
            """SELECT u.* FROM users u
               JOIN user_devices ud ON ud.user_id=u.id
               JOIN installations i ON i.device_id=ud.device_id
               WHERE i.installation_id=? AND u.account_type='DEVICE'
                 AND u.account_status!='DELETED'
               ORDER BY ud.first_seen_at ASC LIMIT 1""",
            (installation_id,),
        ).fetchone()
    if not source or source["id"] == target_user_id:
        return False
    source_id = source["id"]
    db.execute(
        """UPDATE lost_found_posts SET client_local_id=NULL
           WHERE owner_user_id=? AND client_local_id IN (
               SELECT client_local_id FROM lost_found_posts
               WHERE owner_user_id=? AND client_local_id IS NOT NULL
           )""",
        (source_id, target_user_id),
    )
    db.execute("UPDATE reservations SET user_id=? WHERE user_id=?", (target_user_id, source_id))
    db.execute(
        "UPDATE lost_found_posts SET owner_user_id=? WHERE owner_user_id=?",
        (target_user_id, source_id),
    )
    db.execute(
        """INSERT OR IGNORE INTO lost_found_deletion_tombstones(
               id,owner_user_id,client_local_id,deleted_by_user_id,reason,created_at)
           SELECT id,?,client_local_id,deleted_by_user_id,reason,created_at
           FROM lost_found_deletion_tombstones WHERE owner_user_id=?""",
        (target_user_id, source_id),
    )
    db.execute(
        "DELETE FROM lost_found_deletion_tombstones WHERE owner_user_id=?",
        (source_id,),
    )
    db.execute("UPDATE feedback SET user_id=? WHERE user_id=?", (target_user_id, source_id))
    for table, key in (
        ("resource_favorites", "resource_id"),
        ("resource_unlocks", "resource_id"),
    ):
        db.execute(
            f"""INSERT OR IGNORE INTO {table}
                SELECT ?,{key},"""
            + ("created_at" if table == "resource_favorites" else "unlock_status,price_fen,created_at")
            + f" FROM {table} WHERE user_id=?",
            (target_user_id, source_id),
        )
        db.execute(f"DELETE FROM {table} WHERE user_id=?", (source_id,))
    db.execute(
        """INSERT INTO resource_views(user_id,resource_id,view_count,last_viewed_at)
           SELECT ?,resource_id,view_count,last_viewed_at FROM resource_views
           WHERE user_id=? ON CONFLICT(user_id,resource_id) DO UPDATE SET
           view_count=resource_views.view_count+excluded.view_count,
           last_viewed_at=MAX(resource_views.last_viewed_at,excluded.last_viewed_at)""",
        (target_user_id, source_id),
    )
    db.execute("DELETE FROM resource_views WHERE user_id=?", (source_id,))
    db.execute(
        "UPDATE resource_complaints SET user_id=? WHERE user_id=?",
        (target_user_id, source_id),
    )
    for table in ("lost_found_favorites", "lost_found_claims"):
        db.execute(
            f"""INSERT OR IGNORE INTO {table}(user_id,post_id,created_at)
                SELECT ?,post_id,created_at FROM {table} WHERE user_id=?""",
            (target_user_id, source_id),
        )
        db.execute(f"DELETE FROM {table} WHERE user_id=?", (source_id,))
    db.execute(
        """INSERT OR IGNORE INTO lost_found_reports(
               id,user_id,post_id,reason,status,created_at
           ) SELECT id,?,post_id,reason,status,created_at
             FROM lost_found_reports WHERE user_id=?""",
        (target_user_id, source_id),
    )
    db.execute("DELETE FROM lost_found_reports WHERE user_id=?", (source_id,))
    # Preserve the canonical identity's earliest trusted-device timestamp.
    # Without this transfer, registering after anonymous use would make the
    # same person appear as a newly-created PHONE user in cohort metrics.
    db.execute(
        """INSERT INTO user_devices(user_id,device_id,first_seen_at,last_seen_at)
           SELECT ?,device_id,first_seen_at,last_seen_at
             FROM user_devices WHERE user_id=?
           ON CONFLICT(user_id,device_id) DO UPDATE SET
               first_seen_at=MIN(user_devices.first_seen_at,excluded.first_seen_at),
               last_seen_at=MAX(user_devices.last_seen_at,excluded.last_seen_at)""",
        (target_user_id, source_id),
    )
    # Keep user-behavior history attached to the canonical PHONE account.  The
    # event rows themselves are retained, including their device/install
    # identity fields, so the migration never creates a second person or drops
    # the anonymous session from activity history.
    db.execute(
        "UPDATE user_behavior_events SET user_id=? WHERE user_id=?",
        (target_user_id, source_id),
    )
    db.execute(
        "UPDATE reservation_events SET user_id=? WHERE user_id=?",
        (target_user_id, source_id),
    )
    db.execute("DELETE FROM sessions WHERE user_id=?", (source_id,))
    db.execute("DELETE FROM users WHERE id=?", (source_id,))
    return True


def serialize_user(user: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": user["id"],
        "role": user["role"],
        "accountType": user["account_type"],
        "userCode": user["user_code"],
        "nickname": user["nickname"],
        "phoneMasked": user["phone_masked"],
        # Academic-system identity is deliberately not part of the MyChuannong account API.
        "reservationStudentId": user["reservation_student_id"],
        "reservationName": user["reservation_name"],
        "reservationProfileConsentedAt": user["reservation_profile_consented_at"],
        "reservationProfileVersion": user["reservation_profile_version"],
        "reservationProfileUpdatedAt": user["reservation_profile_updated_at"],
        "adminPermissions": sorted(admin_permissions(user)) if user["role"] == "ADMIN" else [],
        "adminRole": admin_role(user) if user["role"] == "ADMIN" else None,
        "accountStatus": user["account_status"],
        "accountStatusReason": user["account_status_reason"],
        "accountStatusChangedAt": user["account_status_changed_at"],
        "accountStatusExpiresAt": user["account_status_expires_at"],
        "lastActiveAt": user["last_active_at"],
    }


def serialize_public_version(
    row: sqlite3.Row,
    *,
    effective_force_update: bool | None = None,
    legacy_minimum_version_code: int | None = None,
) -> dict[str, Any]:
    """Keep the legacy snake_case DTO and add the direct-update contract fields."""
    version = row_dict(row) or {}
    configured_force_update = bool(version.get("force_update"))
    force_update = (
        configured_force_update
        if effective_force_update is None
        else bool(effective_force_update)
    )
    minimum_version_code = (
        version.get("minimum_version_code")
        if legacy_minimum_version_code is None
        else int(legacy_minimum_version_code)
    )
    # Android's Gson DTO declares both snake_case compatibility booleans as
    # Boolean. SQLite integers must not leak here or the entire envelope fails
    # deserialization before a policy reversal can be observed.
    version["metadata_verified"] = bool(version.get("metadata_verified"))
    version["force_update"] = force_update
    version["minimum_version_code"] = minimum_version_code
    version.update(
        {
            "versionCode": version.get("latest_version_code"),
            "versionName": version.get("latest_version_name"),
            "minimumSupportedVersionCode": minimum_version_code,
            "releaseNotes": version.get("update_message") or "",
            "downloadUrl": version.get("download_url"),
            "apkUrl": version.get("download_url"),
            "apkSha256": version.get("apk_sha256"),
            "apkSize": version.get("apk_size"),
            "packageName": version.get("package_name"),
            "signingCertificateSha256": version.get("signing_certificate_sha256"),
            "metadataVerified": bool(version.get("metadata_verified")),
            "publishedAt": version.get("published_at"),
            "forceUpdate": force_update,
        }
    )
    return version


def serialize_release_metadata(row: sqlite3.Row) -> dict[str, Any]:
    """Serialize publication facts without embedding mutable policy fields."""

    return {
        "platform": row["platform"],
        "versionCode": row["latest_version_code"],
        "versionName": row["latest_version_name"],
        "apkUrl": row["download_url"],
        "apkSha256": row["apk_sha256"],
        "apkSize": row["apk_size"],
        "packageName": row["package_name"],
        "signingCertificateSha256": row["signing_certificate_sha256"],
        "metadataVerified": bool(row["metadata_verified"]),
        "releaseNotes": row["update_message"] or "",
        "publishedAt": row["published_at"],
    }


def parse_boolean(value: Any, field: str, default: bool = False) -> bool:
    """Parse JSON booleans without treating the string ``"false"`` as true."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ApiError(400, "VALIDATION_ERROR", f"{field} 必须为布尔值")


def write_audit(
    db: sqlite3.Connection,
    actor_user_id: str | None,
    action: str,
    entity_type: str,
    entity_id: str | None,
    details: dict[str, Any] | None = None,
) -> None:
    db.execute(
        """INSERT INTO admin_audit_logs(
               id,actor_user_id,action,entity_type,entity_id,details_json,created_at
           ) VALUES(?,?,?,?,?,?,?)""",
        (
            str(uuid.uuid4()),
            actor_user_id,
            action,
            entity_type,
            entity_id,
            json.dumps(details or {}, ensure_ascii=False, separators=(",", ":")),
            now_ms(),
        ),
    )


def write_user_event(
    db: sqlite3.Connection,
    user_id: str | None,
    module: str,
    event_type: str,
    result: str = "SUCCESS",
    target_type: str | None = None,
    target_id: str | None = None,
    client_source: str = "APP",
    client_version: str | None = None,
    risk_flag: str | None = None,
    device_id: str | None = None,
    installation_id: str | None = None,
    identity_kind: str | None = None,
) -> None:
    """Store only a minimal operational event; never accepts free-form user content."""
    normalized_result = result.upper()
    if normalized_result not in USER_EVENT_RESULTS:
        normalized_result = "FAILURE"
    timestamp = now_ms()
    if identity_kind not in {"USER", "DEVICE", "LEGACY"}:
        identity_kind = "DEVICE" if device_id else "LEGACY"
        if user_id:
            user = db.execute(
                "SELECT account_type FROM users WHERE id=?", (user_id,)
            ).fetchone()
            if user and user["account_type"] == "PHONE":
                identity_kind = "USER"
    db.execute(
        """INSERT INTO user_behavior_events(
               id,user_id,module,event_type,result,target_type,target_id,
               client_source,client_version,risk_flag,device_id,installation_id,
               identity_kind,created_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            str(uuid.uuid4()), user_id, module[:40], event_type[:60],
            normalized_result, (target_type or "")[:40] or None,
            (target_id or "")[:100] or None, client_source[:20],
            (client_version or "")[:40] or None, (risk_flag or "")[:60] or None,
            device_id,
            (installation_id or "")[:100] or None,
            identity_kind,
            timestamp,
        ),
    )
    if not user_id:
        return
    rule = None
    threshold = 0
    window_ms = 10 * 60 * 1000
    if event_type == "LOGIN_FAILURE":
        rule, threshold = ("LOGIN_FAILURE_BURST", "短时间连续登录失败"), 5
    elif event_type == "FEEDBACK_SUBMITTED":
        rule, threshold = ("FEEDBACK_BURST", "短时间高频提交反馈"), 5
    elif event_type in {"LOST_FOUND_PUBLISHED", "LOST_FOUND_REPORTED"}:
        rule, threshold = ("LOST_FOUND_BURST", "短时间高频发布或举报失物"), 8
    elif event_type == "RESERVATION_CANCELLED":
        rule, threshold, window_ms = ("RESERVATION_CANCEL_BURST", "短时间异常取消预约"), 5, 24 * 60 * 60 * 1000
    if not rule:
        return
    window_start = timestamp - window_ms
    count = db.execute(
        """SELECT COUNT(*) AS count FROM user_behavior_events
           WHERE user_id=? AND event_type=? AND created_at>=?""",
        (user_id, event_type, window_start),
    ).fetchone()["count"]
    if count < threshold:
        return
    bucket = timestamp - (timestamp % window_ms)
    db.execute(
        """INSERT INTO risk_candidates(
               id,user_id,rule_key,rule_label,trigger_count,window_started_at,
               window_ended_at,status,created_at,updated_at
           ) VALUES(?,?,?,?,?,?,?,'OPEN',?,?)
           ON CONFLICT(user_id,rule_key,window_started_at) DO UPDATE SET
               trigger_count=excluded.trigger_count,
               window_ended_at=excluded.window_ended_at,
               updated_at=excluded.updated_at""",
        (str(uuid.uuid4()), user_id, rule[0], rule[1], count, bucket, timestamp, timestamp, timestamp),
    )


def write_reservation_event(
    db: sqlite3.Connection,
    reservation_id: str | None,
    user_id: str | None,
    actor_user_id: str | None,
    action: str,
    reason: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    db.execute(
        """INSERT INTO reservation_events(
               id,reservation_id,user_id,actor_user_id,action,reason,details_json,created_at
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (
            str(uuid.uuid4()),
            reservation_id,
            user_id,
            actor_user_id,
            action,
            reason[:300],
            json.dumps(details or {}, ensure_ascii=False, separators=(",", ":")),
            now_ms(),
        ),
    )


def delete_user_account_data(
    db: sqlite3.Connection,
    user: sqlite3.Row,
    *,
    reason: str,
) -> dict[str, Any]:
    """Delete a USER account's personal data while preserving platform records.

    The users row remains as an anonymous tombstone so audit rows with a foreign
    key stay valid.  Its login identifier and phone digest are released, which
    allows the former phone number to register a new account later.  Admin
    accounts, announcements, library catalog entries, flags and version records
    are deliberately outside this operation.
    """
    if user["role"] != "USER":
        raise ApiError(409, "ADMIN_ACCOUNT_PROTECTED", "管理员账号不能通过用户删除流程处理")

    user_id = user["id"]
    timestamp = now_ms()
    normalized_reason = reason.strip()[:300] or "账号删除"
    image_names = [
        row["image_name"]
        for row in db.execute(
            "SELECT image_name FROM lost_found_posts WHERE owner_user_id=? AND image_name IS NOT NULL",
            (user_id,),
        ).fetchall()
    ]
    reservation_ids = [
        row["id"]
        for row in db.execute(
            "SELECT id FROM reservations WHERE user_id=?", (user_id,)
        ).fetchall()
    ]

    # Remove user-authored content and user-specific interaction records.
    db.execute("DELETE FROM feedback WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM lost_found_posts WHERE owner_user_id=?", (user_id,))
    db.execute("DELETE FROM lost_found_favorites WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM lost_found_claims WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM lost_found_reports WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM lost_found_deletion_tombstones WHERE owner_user_id=?", (user_id,))
    db.execute("DELETE FROM resource_favorites WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM resource_views WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM resource_complaints WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM resource_unlocks WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM risk_candidates WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM user_restrictions WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM user_behavior_events WHERE user_id=?", (user_id,))

    if reservation_ids:
        placeholders = ",".join("?" for _ in reservation_ids)
        db.execute(
            f"DELETE FROM reservation_events WHERE reservation_id IN ({placeholders})",
            reservation_ids,
        )
    db.execute(
        "DELETE FROM reservation_events WHERE user_id=? OR actor_user_id=?",
        (user_id, user_id),
    )
    db.execute("DELETE FROM reservations WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))

    random_digest, random_salt = password_record(secrets.token_urlsafe(40))
    anonymous_username = f"deleted_{uuid.uuid4().hex}"
    db.execute(
        """UPDATE users SET username=?,password_hash=?,password_salt=?,
           account_status='DELETED',account_status_reason=?,
           account_status_changed_at=?,account_status_expires_at=NULL,
           nickname='已注销用户',phone_hash=NULL,phone_masked=NULL,phone_encrypted=NULL,
           student_id=NULL,real_name=NULL,campus_verified=0,campus_verified_at=NULL,
           reservation_student_id=NULL,reservation_name=NULL,
           reservation_profile_consented_at=NULL,reservation_student_id_hash=NULL,
           reservation_name_hash=NULL,reservation_profile_version=0,
           reservation_profile_updated_at=?,last_active_at=NULL,first_login_at=NULL
           WHERE id=? AND role='USER'""",
        (
            anonymous_username,
            random_digest,
            random_salt,
            normalized_reason,
            timestamp,
            timestamp,
            user_id,
        ),
    )
    for image_name in image_names:
        run_safely_delete_upload(image_name)
    return {
        "deleted": True,
        "id": user_id,
        "removedImages": len(image_names),
        "removedReservations": len(reservation_ids),
    }


def purge_deleted_user(
    db: sqlite3.Connection,
    user: sqlite3.Row,
) -> dict[str, Any]:
    """Permanently remove one already-deleted user after FK-aware cleanup."""
    if user["role"] != "USER":
        raise ApiError(409, "ADMIN_ACCOUNT_PROTECTED", "管理员账号不能永久清理")
    if user["account_status"] != "DELETED":
        raise ApiError(409, "PURGE_REQUIRES_DELETED", "只有已注销账号可以永久清理")

    user_id = user["id"]
    image_names = [
        row["image_name"]
        for row in db.execute(
            "SELECT image_name FROM lost_found_posts WHERE owner_user_id=? AND image_name IS NOT NULL",
            (user_id,),
        ).fetchall()
    ]
    association_counts = {
        "reservations": db.execute(
            "SELECT COUNT(*) AS count FROM reservations WHERE user_id=?", (user_id,)
        ).fetchone()["count"],
        "lostFound": db.execute(
            "SELECT COUNT(*) AS count FROM lost_found_posts WHERE owner_user_id=?", (user_id,)
        ).fetchone()["count"],
        "feedback": db.execute(
            "SELECT COUNT(*) AS count FROM feedback WHERE user_id=?", (user_id,)
        ).fetchone()["count"],
        "resourceViews": db.execute(
            "SELECT COUNT(*) AS count FROM resource_views WHERE user_id=?", (user_id,)
        ).fetchone()["count"],
        "resourceFavorites": db.execute(
            "SELECT COUNT(*) AS count FROM resource_favorites WHERE user_id=?", (user_id,)
        ).fetchone()["count"],
        "userEvents": db.execute(
            "SELECT COUNT(*) AS count FROM user_behavior_events WHERE user_id=?", (user_id,)
        ).fetchone()["count"],
    }
    reservation_ids = [
        row["id"]
        for row in db.execute(
            "SELECT id FROM reservations WHERE user_id=?", (user_id,)
        ).fetchall()
    ]
    if reservation_ids:
        placeholders = ",".join("?" for _ in reservation_ids)
        db.execute(
            f"DELETE FROM reservation_events WHERE reservation_id IN ({placeholders})",
            reservation_ids,
        )

    # Delete dependent/user-authored records before the users row. Tables that
    # represent shared platform history retain their rows but lose the user FK.
    db.execute("DELETE FROM feedback WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM lost_found_posts WHERE owner_user_id=?", (user_id,))
    db.execute("DELETE FROM lost_found_favorites WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM lost_found_claims WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM lost_found_reports WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM lost_found_deletion_tombstones WHERE owner_user_id=?", (user_id,))
    db.execute("DELETE FROM resource_favorites WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM resource_views WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM resource_complaints WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM resource_unlocks WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM risk_candidates WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM user_restrictions WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM user_behavior_events WHERE user_id=?", (user_id,))
    db.execute(
        "UPDATE reservation_events SET user_id=NULL,actor_user_id=NULL "
        "WHERE user_id=? OR actor_user_id=?",
        (user_id, user_id),
    )
    db.execute("DELETE FROM reservations WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    db.execute("UPDATE feedback SET handled_by=NULL WHERE handled_by=?", (user_id,))
    db.execute("UPDATE admin_audit_logs SET actor_user_id=NULL WHERE actor_user_id=?", (user_id,))
    db.execute("DELETE FROM user_devices WHERE user_id=?", (user_id,))
    deleted = db.execute(
        "DELETE FROM users WHERE id=? AND role='USER' AND account_status='DELETED'",
        (user_id,),
    ).rowcount
    if deleted != 1:
        raise ApiError(409, "PURGE_TARGET_CHANGED", "账号状态已变化，请刷新后重试")
    for image_name in image_names:
        run_safely_delete_upload(image_name)
    return {
        "userCode": user["user_code"],
        "associationCounts": association_counts,
        "removedImages": len(image_names),
    }


def feature_state(db: sqlite3.Connection, key: str) -> dict[str, Any]:
    row = db.execute("SELECT * FROM remote_flags WHERE key=?", (key,)).fetchone()
    if not row:
        return {
            "key": key,
            "enabled": True,
            "status": "OPEN",
            "maintenanceNote": "",
            "expectedRestoreAt": None,
            "updatedAt": None,
            "updatedBy": None,
            "changeNote": "",
        }
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}
    status = str(payload.get("status", "OPEN")).upper()
    if status not in {"OPEN", "CLOSED", "MAINTENANCE"}:
        status = "OPEN"
    if not bool(row["enabled"]):
        status = "CLOSED"
    return {
        "key": key,
        "enabled": bool(row["enabled"]),
        "status": status,
        "maintenanceNote": str(payload.get("maintenanceNote", ""))[:300],
        "expectedRestoreAt": payload.get("expectedRestoreAt"),
        "updatedAt": row["updated_at"],
        "updatedBy": row["updated_by"],
        "changeNote": str(row["change_note"] or "")[:300],
    }


def ensure_feature_open(db: sqlite3.Connection, key: str, label: str) -> None:
    state = feature_state(db, key)
    if state["status"] == "OPEN":
        return
    if state["status"] == "MAINTENANCE":
        note = state["maintenanceNote"] or f"{label}维护中，请稍后再试"
        raise ApiError(503, "FEATURE_MAINTENANCE", note)
    note = state["maintenanceNote"] or f"{label}暂不开放"
    raise ApiError(503, "FEATURE_CLOSED", note)


def reservation_rules(db: sqlite3.Connection) -> sqlite3.Row:
    return db.execute(
        "SELECT * FROM reservation_rules WHERE id='default'"
    ).fetchone()


def active_restriction(
    db: sqlite3.Connection, user_id: str
) -> sqlite3.Row | None:
    return db.execute(
        "SELECT * FROM user_restrictions WHERE user_id=?", (user_id,)
    ).fetchone()


def reservation_usage(
    db: sqlite3.Connection, user_id: str, booking_date: str | None = None
) -> dict[str, int]:
    target_date = booking_date or date.today().isoformat()
    active_count = db.execute(
        """SELECT COUNT(*) AS count FROM reservations
           WHERE user_id=? AND booking_date=? AND status='ACTIVE'""",
        (user_id, target_date),
    ).fetchone()["count"]
    day = date.fromisoformat(target_date)
    day_start = int(datetime.combine(day, datetime.min.time()).timestamp() * 1000)
    day_end = int(
        datetime.combine(day + timedelta(days=1), datetime.min.time()).timestamp() * 1000
    )
    cancellation_count = db.execute(
        """SELECT COUNT(*) AS count FROM reservation_events
           WHERE user_id=? AND action='USER_CANCELLED'
           AND created_at>=? AND created_at<?""",
        (user_id, day_start, day_end),
    ).fetchone()["count"]
    return {"active": active_count, "cancelled": cancellation_count}


def serialize_reservation_context(
    db: sqlite3.Connection, user: sqlite3.Row
) -> dict[str, Any]:
    rules = reservation_rules(db)
    usage = reservation_usage(db, user["id"])
    restriction = active_restriction(db, user["id"])
    timestamp = now_ms()
    reservation_blocked_until = (
        restriction["reservation_blocked_until"] if restriction else None
    )
    cancellation_blocked_until = (
        restriction["cancellation_blocked_until"] if restriction else None
    )
    return {
        "rules": {
            "maxActivePerDay": rules["max_active_per_day"],
            "maxUserCancellationsPerDay": rules[
                "max_user_cancellations_per_day"
            ],
            "cancelCutoffMinutes": rules["cancel_cutoff_minutes"],
            "restrictionHoursAfterExcessCancel": rules[
                "restriction_hours_after_excess_cancel"
            ],
            "requireReservationStudentId": bool(
                rules["reservation_profile_mode"] == "REQUIRED"
            ),
            "reservationProfileMode": rules["reservation_profile_mode"],
            "updatedAt": rules["updated_at"],
        },
        "today": {
            "activeReservations": usage["active"],
            "remainingReservations": max(
                0, rules["max_active_per_day"] - usage["active"]
            ),
            "userCancellations": usage["cancelled"],
            "remainingCancellations": max(
                0,
                rules["max_user_cancellations_per_day"] - usage["cancelled"],
            ),
        },
        "restriction": {
            "reservationBlocked": bool(
                reservation_blocked_until
                and reservation_blocked_until > timestamp
            ),
            "cancellationBlocked": bool(
                cancellation_blocked_until
                and cancellation_blocked_until > timestamp
            ),
            "reservationBlockedUntil": reservation_blocked_until,
            "cancellationBlockedUntil": cancellation_blocked_until,
            "reason": restriction["reason"] if restriction else "",
        },
        "profile": {
            "studentId": user["reservation_student_id"],
            "name": user["reservation_name"],
            "consentedAt": user["reservation_profile_consented_at"],
            "version": user["reservation_profile_version"],
            "updatedAt": user["reservation_profile_updated_at"],
        },
    }


def cancellation_preview(
    db: sqlite3.Connection, reservation: sqlite3.Row
) -> dict[str, Any]:
    if reservation["status"] != "ACTIVE":
        reason = (
            "该预约已由管理员取消"
            if reservation["cancelled_by"] == "ADMIN"
            else "该预约已经取消"
        )
        return {"canCancel": False, "reason": reason}
    rules = reservation_rules(db)
    usage = reservation_usage(db, reservation["user_id"])
    restriction = active_restriction(db, reservation["user_id"])
    timestamp = now_ms()
    if (
        restriction
        and restriction["cancellation_blocked_until"]
        and restriction["cancellation_blocked_until"] > timestamp
    ):
        return {
            "canCancel": False,
            "reason": restriction["reason"] or "当前账号暂时无法继续取消预约",
        }
    start_text = reservation["time_slot"].split("-", 1)[0]
    start_at = datetime.strptime(
        f"{reservation['booking_date']} {start_text}", "%Y-%m-%d %H:%M"
    )
    if datetime.now() + timedelta(minutes=rules["cancel_cutoff_minutes"]) >= start_at:
        return {
            "canCancel": False,
            "reason": f"开场前 {rules['cancel_cutoff_minutes']} 分钟内不可自行取消",
        }
    if usage["cancelled"] >= rules["max_user_cancellations_per_day"]:
        return {
            "canCancel": False,
            "reason": f"今日主动取消已达 {rules['max_user_cancellations_per_day']} 次",
        }
    next_count = usage["cancelled"] + 1
    return {
        "canCancel": True,
        "reason": "",
        "nextCancellationCount": next_count,
        "remainingAfterCancel": max(
            0, rules["max_user_cancellations_per_day"] - next_count
        ),
    }


def upsert_restriction(
    db: sqlite3.Connection,
    user_id: str,
    reservation_blocked_until: int | None,
    cancellation_blocked_until: int | None,
    reason: str,
) -> None:
    db.execute(
        """INSERT INTO user_restrictions(
               user_id,reservation_blocked_until,cancellation_blocked_until,
               reason,updated_at
           ) VALUES(?,?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET
           reservation_blocked_until=excluded.reservation_blocked_until,
           cancellation_blocked_until=excluded.cancellation_blocked_until,
           reason=excluded.reason,updated_at=excluded.updated_at""",
        (
            user_id,
            reservation_blocked_until,
            cancellation_blocked_until,
            reason[:300],
            now_ms(),
        ),
    )


def verify_release_artifact(
    download_url: str,
    expected_sha256: str,
    expected_size: int,
) -> None:
    """Verify the exact published bytes before a production version is saved."""
    if not RUNTIME.is_production:
        return
    request = urllib_request.Request(
        download_url,
        headers={"User-Agent": "MyChuannong-ReleaseGate/1.0"},
    )
    digest = hashlib.sha256()
    size = 0
    try:
        with urllib_request.urlopen(request, timeout=30) as response:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
    except (urllib_error.URLError, TimeoutError, OSError) as exc:
        raise ApiError(409, "APK_DOWNLOAD_UNAVAILABLE", "下载地址当前无法完成发布校验") from exc
    actual_sha256 = digest.hexdigest()
    if size != expected_size:
        raise ApiError(409, "APK_SIZE_MISMATCH", "下载文件大小与发布元数据不一致")
    if actual_sha256 != expected_sha256:
        raise ApiError(409, "APK_SHA256_MISMATCH", "下载文件 SHA-256 与发布元数据不一致")


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "MyChuannongBackend"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Request logging is emitted once in dispatch() with a route-only path.
        return

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        # BaseHTTPRequestHandler calls this from send_response(); suppress it so
        # query strings and request lines cannot leak into production logs.
        return

    def request_identity(self) -> str:
        address = self.client_address[0] if self.client_address else "unknown"
        return str(address or "unknown")

    def enforce_rate_limit(
        self, bucket: str, identity: str, limit: int, window_seconds: int
    ) -> None:
        key_digest = hashlib.sha256(identity.encode("utf-8", errors="ignore")).hexdigest()
        retry_after = RATE_LIMITER.check(
            f"{bucket}:{key_digest}", limit, window_seconds
        )
        if retry_after is not None:
            raise ApiError(
                429,
                "RATE_LIMITED",
                f"请求过于频繁，请在约 {retry_after} 秒后重试",
            )

    def enforce_route_rate_limit(self, method: str, path: str) -> None:
        client = self.request_identity()
        if method == "POST" and path == "/api/v1/auth/login":
            self.enforce_rate_limit("login-ip", client, 120, 300)
        elif method == "POST" and path == "/api/v1/auth/register":
            self.enforce_rate_limit("register-ip", client, 30, 300)
        elif method == "POST" and path == "/api/v1/auth/device":
            self.enforce_rate_limit("device-ip", client, 120, 3600)
        elif method == "POST" and path == "/api/v1/feedback":
            token = self.headers.get("Authorization", "")
            self.enforce_rate_limit("feedback", f"{client}:{token}", 10, 3600)
        elif method == "POST" and path.startswith("/api/v1/lost-found"):
            token = self.headers.get("Authorization", "")
            self.enforce_rate_limit("lost-found-write", f"{client}:{token}", 30, 3600)

    def send_json(self, status: int, payload: Any) -> None:
        self._response_status = status
        encoded, content_type = json_response(status, payload)
        self.send_response(status)
        self.send_header("X-Product-Id", "my-chuannong")
        self.send_header("X-Application-Id", "com.mychuannong.app")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header(
            "Cache-Control",
            "no-store, no-cache, must-revalidate, max-age=0",
        )
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Request-ID", getattr(self, "request_id", ""))
        self.end_headers()
        self.wfile.write(encoded)

    def body_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ApiError(400, "BAD_REQUEST", "请求长度无效") from exc
        if length <= 0 or length > MAX_BODY:
            raise ApiError(400, "BAD_REQUEST", "请求内容为空或过大")
        try:
            body = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(400, "BAD_JSON", "请求必须是有效 JSON") from exc
        if not isinstance(body, dict):
            raise ApiError(400, "BAD_JSON", "请求 JSON 必须是对象")
        return body

    def current_user(self, db: sqlite3.Connection, role: str | None = None) -> sqlite3.Row:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise ApiError(401, "UNAUTHORIZED", "需要登录")
        token_hash = hashlib.sha256(header[7:].strip().encode()).hexdigest()
        user = db.execute(
            """SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
               WHERE s.token_hash=? AND s.expires_at>?""",
            (token_hash, now_ms()),
        ).fetchone()
        if not user:
            raise ApiError(401, "SESSION_EXPIRED", "登录状态已过期")
        user = restore_expired_account_limit(db, user)
        if user["account_status"] in {"DISABLED", "PENDING_DELETION", "DELETED", "SUSPENDED"}:
            raise ApiError(403, "ACCOUNT_UNAVAILABLE", "账号当前不可用，请联系管理员")
        if user["role"] == "ADMIN" and not bool(user["admin_enabled"]):
            raise ApiError(403, "ADMIN_DISABLED", "管理员账号已停用")
        if user["role"] == "ADMIN" and not admin_role(user):
            raise ApiError(403, "ADMIN_ROLE_INVALID", "管理员角色配置无效")
        if role and user["role"] != role:
            raise ApiError(403, "FORBIDDEN", "权限不足")
        timestamp = now_ms()
        last_active = user["last_active_at"] or 0
        if user["role"] == "USER" and timestamp - last_active >= 5 * 60 * 1000:
            db.execute("UPDATE users SET last_active_at=? WHERE id=?", (timestamp, user["id"]))
            write_user_event(
                db, user["id"], "PLATFORM", "APP_ACTIVE", "SUCCESS",
                client_version=self.headers.get("X-App-Version", "")[:40] or None,
            )
        return user

    def optional_user(self, db: sqlite3.Connection) -> sqlite3.Row | None:
        if not self.headers.get("Authorization", "").strip():
            return None
        return self.current_user(db)

    def library_reader(self, db: sqlite3.Connection) -> sqlite3.Row | None:
        user = self.optional_user(db)
        if user:
            require_account_operation(user)
            return user
        row = db.execute(
            "SELECT payload_json FROM remote_flags WHERE key='resource_library'"
        ).fetchone()
        payload = json.loads(row["payload_json"] or "{}") if row else {}
        if not bool(payload.get("allowGuestBrowsing", False)):
            raise ApiError(401, "UNAUTHORIZED", "登录我的川农账号后可浏览资料库")
        return None

    def do_GET(self) -> None:
        self.dispatch("GET")

    def do_POST(self) -> None:
        self.dispatch("POST")

    def do_PATCH(self) -> None:
        self.dispatch("PATCH")

    def do_PUT(self) -> None:
        self.dispatch("PUT")

    def do_DELETE(self) -> None:
        self.dispatch("DELETE")

    def dispatch(self, method: str) -> None:
        started = time.perf_counter()
        self.request_id = uuid.uuid4().hex
        self._response_status = 500
        route_path = "/"
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            route_path = path
            self.enforce_route_rate_limit(method, path)
            if method == "GET" and (path == "/admin" or path.startswith("/admin/")):
                self.send_admin_asset(path)
                return
            if method == "GET" and path.startswith("/uploads/"):
                self.send_upload(path.removeprefix("/uploads/"))
                return
            query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
            result, status = self.route(method, path, query)
            self.send_json(status, {"ok": True, "data": result})
        except ApiError as error:
            self.send_json(error.status, {"ok": False, "error": {"code": error.code, "message": error.message}})
        except Exception:
            logging.getLogger("mychuannong.server").exception(
                "Unhandled API error",
                extra={"request_id": self.request_id, "route": route_path},
            )
            self.send_json(500, {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": "服务器内部错误"}})
        finally:
            logging.getLogger("mychuannong.access").info(
                "request completed",
                extra={
                    "request_id": self.request_id,
                    "route": route_path,
                    "method": method,
                    "status": self._response_status,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "event": "HTTP_REQUEST",
                },
            )

    def send_admin_asset(self, path: str) -> None:
        relative = "index.html" if path in {"/admin", "/admin/"} else path.removeprefix("/admin/")
        allowed_assets = {"index.html", "app.js", "styles.css", "assets/my-chuannong-app-icon.png"}
        if relative not in allowed_assets:
            raise ApiError(404, "NOT_FOUND", "管理后台资源不存在")
        target = (ADMIN_WEB / relative).resolve()
        allowed_parents = {ADMIN_WEB.resolve(), (ADMIN_WEB / "assets").resolve()}
        if target.parent not in allowed_parents or not target.is_file():
            raise ApiError(404, "NOT_FOUND", "管理后台资源不存在")
        content = target.read_bytes()
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".png": "image/png",
        }[target.suffix]
        self._response_status = 200
        self.send_response(200)
        self.send_header("X-Product-Id", "my-chuannong")
        self.send_header("X-Application-Id", "com.mychuannong.app")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Request-ID", getattr(self, "request_id", ""))
        self.end_headers()
        self.wfile.write(content)

    def send_upload(self, name: str) -> None:
        if not re.fullmatch(r"[0-9a-f-]+\.(jpg|png|webp)", name):
            raise ApiError(404, "NOT_FOUND", "图片不存在")
        target = (UPLOADS / name).resolve()
        if target.parent != UPLOADS.resolve() or not target.is_file():
            raise ApiError(404, "NOT_FOUND", "图片不存在")
        content = target.read_bytes()
        media_type = {
            ".jpg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }[target.suffix]
        self._response_status = 200
        self.send_response(200)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
        self.send_header("X-Request-ID", getattr(self, "request_id", ""))
        self.end_headers()
        self.wfile.write(content)

    def route(self, method: str, path: str, query: dict[str, str]) -> tuple[Any, int]:
        if method == "GET" and path == "/health":
            return {"status": "ok"}, 200
        if method == "GET" and path == "/ready":
            if not database_integrity_check():
                raise ApiError(503, "NOT_READY", "服务暂未就绪")
            return {"status": "ready"}, 200
        if method == "POST" and path == "/api/v1/auth/device":
            return self.auth_device(), 200
        if method == "POST" and path == "/api/v1/auth/register":
            return self.auth_register(), 201
        if method == "POST" and path == "/api/v1/auth/login":
            return self.auth_login(), 200
        if method == "POST" and path == "/api/v1/auth/logout":
            return self.auth_logout(), 200
        if method == "GET" and path == "/api/v1/auth/me":
            return self.auth_me(), 200
        if method == "PATCH" and path == "/api/v1/auth/profile":
            return self.auth_profile(), 200
        if method == "GET" and path == "/api/v1/auth/reservation-profile":
            return self.auth_reservation_profile(), 200
        if method == "PUT" and path == "/api/v1/auth/reservation-profile":
            return self.auth_reservation_profile_update(), 200
        if method == "DELETE" and path == "/api/v1/auth/reservation-profile":
            return self.auth_reservation_profile_delete(), 200
        if method == "DELETE" and path == "/api/v1/auth/account":
            return self.auth_delete_account(), 200
        if path.startswith("/api/v1/sports/"):
            # A closed module still lets a signed-in user view/cancel an existing
            # reservation. Inventory browsing and every new reservation path are
            # blocked server-side so deep links and old clients cannot bypass it.
            is_existing_reservation_access = (
                method == "GET" and path == "/api/v1/sports/reservations"
            ) or (
                method == "POST"
                and re.fullmatch(r"/api/v1/sports/reservations/[^/]+/cancel", path)
            )
            if not is_existing_reservation_access:
                with connection() as db:
                    ensure_feature_open(db, "sports_reservation", "体育预约")
        if path.startswith("/api/v1/lost-found"):
            with connection() as db:
                ensure_feature_open(db, "lost_found", "失物招领")
        if path.startswith("/api/v1/library"):
            with connection() as db:
                ensure_feature_open(db, "resource_library", "资料库")
        if method == "GET" and path == "/api/v1/sports/context":
            return self.sports_context(), 200
        if method == "GET" and path == "/api/v1/sports/venues":
            return self.sports_venues(), 200
        if method == "GET" and path == "/api/v1/sports/availability":
            return self.sports_availability(query), 200
        if method == "GET" and path == "/api/v1/sports/reservations":
            return self.sports_reservations(), 200
        if method == "POST" and path == "/api/v1/sports/reservations":
            return self.sports_reserve(), 201
        match = re.fullmatch(r"/api/v1/sports/reservations/([^/]+)/cancel", path)
        if method == "POST" and match:
            return self.sports_cancel(match.group(1)), 200
        if method == "GET" and path == "/api/v1/lost-found":
            return self.lost_list(query), 200
        if method == "POST" and path == "/api/v1/lost-found":
            return self.lost_create(), 201
        if method == "GET" and path == "/api/v1/lost-found/favorites":
            return self.lost_favorites(), 200
        match = re.fullmatch(r"/api/v1/lost-found/([^/]+)", path)
        if match and method == "GET":
            return self.lost_get(match.group(1)), 200
        if match and method == "PATCH":
            return self.lost_update(match.group(1)), 200
        if match and method == "DELETE":
            return self.lost_delete(match.group(1)), 200
        match = re.fullmatch(r"/api/v1/lost-found/([^/]+)/favorite", path)
        if match and method == "POST":
            return self.lost_favorite(match.group(1)), 200
        match = re.fullmatch(r"/api/v1/lost-found/([^/]+)/claim", path)
        if match and method == "POST":
            return self.lost_claim(match.group(1)), 200
        match = re.fullmatch(r"/api/v1/lost-found/([^/]+)/report", path)
        if match and method == "POST":
            return self.lost_report(match.group(1)), 201
        if method == "GET" and path == "/api/v1/library":
            return self.library_list(query), 200
        match = re.fullmatch(r"/api/v1/library/([^/]+)", path)
        if match and method == "GET":
            return self.library_get(match.group(1)), 200
        match = re.fullmatch(r"/api/v1/library/([^/]+)/view", path)
        if match and method == "POST":
            return self.library_view(match.group(1)), 200
        match = re.fullmatch(r"/api/v1/library/([^/]+)/favorite", path)
        if match and method == "POST":
            return self.library_favorite(match.group(1)), 200
        match = re.fullmatch(r"/api/v1/library/([^/]+)/complaints", path)
        if match and method == "POST":
            return self.library_complaint(match.group(1)), 201
        if method == "GET" and path == "/api/v1/public/bootstrap":
            return self.public_bootstrap(query), 200
        if method == "GET" and path == "/api/v1/public/website-announcements":
            return self.public_website_announcements(), 200
        if method == "GET" and path == "/api/v1/public/version/android":
            return self.public_version_check(query), 200
        if method == "GET" and path == "/api/v1/public/update-policy/android":
            return self.public_version_check(query), 200
        if method == "GET" and path == "/api/v1/public/service-updates":
            return self.public_service_updates(), 200
        if method == "POST" and path == "/api/v1/feedback":
            return self.feedback_create(), 201
        if method == "GET" and path == "/api/v1/feedback":
            return self.feedback_list(), 200
        match = re.fullmatch(r"/api/v1/feedback/([^/]+)", path)
        if method == "GET" and match:
            return self.feedback_get(match.group(1)), 200
        if path.startswith("/api/v1/admin/"):
            return self.admin_route(method, path, query)
        raise ApiError(404, "NOT_FOUND", "接口不存在")

    def auth_device(self) -> dict[str, Any]:
        data = self.body_json()
        install_id = required_text(data, "installationId", 100)
        with connection() as db:
            identity = record_device_identity(
                db,
                data,
                create_anonymous_user=True,
            )
            user = db.execute(
                "SELECT * FROM users WHERE id=?",
                (identity["anonymous_user_id"],),
            ).fetchone()
            if not user:
                raise ApiError(500, "DEVICE_IDENTITY_FAILED", "设备身份初始化失败")
            timestamp = now_ms()
            db.execute(
                """UPDATE users SET first_login_at=COALESCE(first_login_at,?),
                   last_active_at=? WHERE id=?""",
                (timestamp, timestamp, user["id"]),
            )
            write_user_event(
                db,
                user["id"],
                "ACCOUNT",
                "LOGIN_SUCCESS",
                device_id=identity["device_id"],
                installation_id=install_id,
                client_version=str(data.get("versionName", "")).strip() or None,
            )
            user = db.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
            return {"token": issue_token(db, user["id"]), "user": serialize_user(user)}

    def auth_register(self) -> dict[str, Any]:
        data = self.body_json()
        phone = normalize_phone(data.get("phone"))
        self.enforce_rate_limit(
            "register-identity",
            f"{self.request_identity()}:{phone}",
            3,
            300,
        )
        password = required_text(data, "password", 200)
        if len(password) < 8:
            raise ApiError(400, "WEAK_PASSWORD", "密码至少 8 位")
        nickname = str(data.get("nickname", "川农同学")).strip()[:30] or "川农同学"
        digest_phone = phone_digest(phone)
        username = "phone_" + digest_phone[:32]
        digest, salt = password_record(password)
        with connection() as db:
            user_id = str(uuid.uuid4())
            created_at = now_ms()
            try:
                db.execute(
                    """INSERT INTO users(
                           id,username,user_code,password_hash,password_salt,role,account_type,
                           nickname,phone_hash,phone_masked,phone_encrypted,
                           account_status,created_at
                       ) VALUES(?,?,?,?,?,'USER','PHONE',?,?,?,?,'ACTIVE',?)""",
                    (
                        user_id,
                        username,
                        allocate_user_code(db, created_at),
                        digest,
                        salt,
                        nickname,
                        digest_phone,
                        phone_mask(phone),
                        encrypt_phone(phone),
                        now_ms(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ApiError(409, "PHONE_EXISTS", "该账号已存在，请直接登录") from exc
            migrated = migrate_device_data(
                db, str(data.get("installationId", "")).strip() or None, user_id
            )
            identity = (
                record_device_identity(db, data, user_id=user_id)
                if str(data.get("installationId", "")).strip()
                else {"device_id": None, "installation_id": None}
            )
            user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            timestamp = now_ms()
            # Registration returns an authenticated session. Record both the
            # ordinary user action and that session's first successful access so
            # the dashboard's daily-login metric has a matching detail row.
            db.execute(
                "UPDATE users SET first_login_at=?,last_active_at=? WHERE id=?",
                (timestamp, timestamp, user_id),
            )
            write_user_event(
                db,
                user_id,
                "ACCOUNT",
                "USER_REGISTER",
                device_id=identity["device_id"],
                installation_id=identity["installation_id"],
                client_version=str(data.get("versionName", "")).strip() or None,
            )
            write_user_event(
                db,
                user_id,
                "ACCOUNT",
                "LOGIN_SUCCESS",
                device_id=identity["device_id"],
                installation_id=identity["installation_id"],
                client_version=str(data.get("versionName", "")).strip() or None,
            )
            user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            return {"token": issue_token(db, user_id), "user": serialize_user(user)}

    def auth_login(self) -> dict[str, Any]:
        data = self.body_json()
        password = required_text(data, "password", 200)
        login_identity = str(data.get("phone") or data.get("username") or "").strip().lower()
        self.enforce_rate_limit(
            "login-identity",
            f"{self.request_identity()}:{login_identity}",
            8,
            300,
        )
        with connection() as db:
            if str(data.get("phone", "")).strip():
                phone = normalize_phone(data.get("phone"))
                user = db.execute(
                    "SELECT * FROM users WHERE phone_hash=?", (phone_digest(phone),)
                ).fetchone()
                if user and not user["phone_encrypted"]:
                    db.execute(
                        "UPDATE users SET phone_encrypted=? WHERE id=?",
                        (encrypt_phone(phone), user["id"]),
                    )
                    user = db.execute(
                        "SELECT * FROM users WHERE id=?", (user["id"],)
                    ).fetchone()
            else:
                username = required_text(data, "username", 48)
                user = db.execute(
                    "SELECT * FROM users WHERE username=?", (username,)
                ).fetchone()
            if not user or not verify_password(password, user["password_hash"], user["password_salt"]):
                if user and user["role"] == "USER":
                    write_user_event(db, user["id"], "ACCOUNT", "LOGIN_FAILURE", "FAILURE", risk_flag="AUTH_FAILURE")
                elif user and user["role"] == "ADMIN":
                    write_audit(db, user["id"], "ADMIN_LOGIN_FAILED", "ADMIN", user["id"], {"result": "FAILURE"})
                raise ApiError(401, "INVALID_CREDENTIALS", "账号或密码错误")
            user = restore_expired_account_limit(db, user)
            if user["account_status"] in {"DISABLED", "PENDING_DELETION", "DELETED", "SUSPENDED"}:
                if user["role"] == "USER":
                    write_user_event(db, user["id"], "ACCOUNT", "LOGIN_DENIED", "DENIED", risk_flag="ACCOUNT_STATUS")
                raise ApiError(403, "ACCOUNT_UNAVAILABLE", "账号当前不可用")
            if user["role"] == "ADMIN" and not bool(user["admin_enabled"]):
                write_audit(
                    db,
                    user["id"],
                    "ADMIN_LOGIN_DENIED",
                    "ADMIN",
                    user["id"],
                    {"result": "DENIED", "reason": "ADMIN_DISABLED"},
                )
                raise ApiError(403, "ADMIN_DISABLED", "管理员账号已停用")
            if user["role"] == "ADMIN" and not admin_role(user):
                write_audit(
                    db,
                    user["id"],
                    "ADMIN_LOGIN_DENIED",
                    "ADMIN",
                    user["id"],
                    {"result": "DENIED", "reason": "ADMIN_ROLE_INVALID"},
                )
                raise ApiError(403, "ADMIN_ROLE_INVALID", "管理员角色配置无效")
            migrated = migrate_device_data(
                db,
                str(data.get("installationId", "")).strip() or None,
                user["id"],
            )
            identity = (
                record_device_identity(
                    db,
                    data,
                    user_id=user["id"] if user["role"] == "USER" else None,
                )
                if str(data.get("installationId", "")).strip()
                else {"device_id": None, "installation_id": None}
            )
            user = db.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
            timestamp = now_ms()
            db.execute(
                """UPDATE users SET first_login_at=COALESCE(first_login_at,?),
                   last_active_at=? WHERE id=?""",
                (timestamp, timestamp, user["id"]),
            )
            if user["role"] == "ADMIN":
                write_audit(db, user["id"], "ADMIN_LOGIN_SUCCEEDED", "ADMIN", user["id"], {"result": "SUCCESS"})
            else:
                write_user_event(
                    db,
                    user["id"],
                    "ACCOUNT",
                    "LOGIN_SUCCESS",
                    device_id=identity["device_id"],
                    installation_id=identity["installation_id"],
                    client_version=str(data.get("versionName", "")).strip() or None,
                )
            return {
                "token": issue_token(db, user["id"]),
                "user": serialize_user(user),
                "migratedDeviceData": migrated,
            }

    def auth_logout(self) -> dict[str, Any]:
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            token_hash = hashlib.sha256(header[7:].strip().encode()).hexdigest()
            with connection() as db:
                session = db.execute(
                    "SELECT user_id FROM sessions WHERE token_hash=?", (token_hash,)
                ).fetchone()
                if session:
                    user = db.execute("SELECT role FROM users WHERE id=?", (session["user_id"],)).fetchone()
                    if user and user["role"] == "USER":
                        write_user_event(db, session["user_id"], "ACCOUNT", "LOGOUT")
                db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
        return {"loggedOut": True}

    def auth_me(self) -> dict[str, Any]:
        with connection() as db:
            return serialize_user(self.current_user(db))

    def auth_profile(self) -> dict[str, Any]:
        data = self.body_json()
        with connection() as db:
            user = self.current_user(db)
            nickname = str(data.get("nickname", user["nickname"])).strip()[:30]
            if not nickname:
                raise ApiError(400, "VALIDATION_ERROR", "昵称不能为空")
            db.execute(
                "UPDATE users SET nickname=? WHERE id=?", (nickname, user["id"])
            )
            return serialize_user(
                db.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
            )

    def auth_reservation_profile(self) -> dict[str, Any]:
        with connection() as db:
            user = self.current_user(db)
            return serialize_reservation_context(db, user)

    def auth_reservation_profile_update(self) -> dict[str, Any]:
        data = self.body_json()
        with connection() as db:
            user = self.current_user(db)
            rules = reservation_rules(db)
            if rules["reservation_profile_mode"] == "OFF":
                raise ApiError(
                    409,
                    "RESERVATION_PROFILE_DISABLED",
                    "当前无需填写预约资料",
                )
            student_id = normalize_reservation_student_id(
                data.get("studentId"), required=True
            )
            name = normalize_reservation_name(data.get("name"), required=True)
            consented = bool(data.get("consented"))
            if not consented:
                raise ApiError(
                    400,
                    "CONSENT_REQUIRED",
                    "请确认姓名和学号真实、准确并同意预约业务使用范围后再保存",
                )
            timestamp = now_ms()
            db.execute(
                """UPDATE users SET reservation_student_id=?,reservation_name=?,
                   reservation_profile_consented_at=?,
                   reservation_student_id_hash=?,reservation_name_hash=?,
                   reservation_profile_version=?,reservation_profile_updated_at=?
                   WHERE id=?""",
                (
                    student_id,
                    name,
                    timestamp,
                    reservation_pii_digest("student-id", student_id),
                    reservation_pii_digest("real-name", name),
                    RESERVATION_PROFILE_VERSION,
                    timestamp,
                    user["id"],
                ),
            )
            updated = db.execute(
                "SELECT * FROM users WHERE id=?", (user["id"],)
            ).fetchone()
            return serialize_reservation_context(db, updated)

    def auth_reservation_profile_delete(self) -> dict[str, Any]:
        with connection() as db:
            user = self.current_user(db)
            db.execute(
                """UPDATE users SET reservation_student_id=NULL,reservation_name=NULL,
                   reservation_profile_consented_at=NULL,
                   reservation_student_id_hash=NULL,reservation_name_hash=NULL,
                   reservation_profile_version=0,reservation_profile_updated_at=?
                   WHERE id=?""",
                (now_ms(), user["id"]),
            )
            updated = db.execute(
                "SELECT * FROM users WHERE id=?", (user["id"],)
            ).fetchone()
            return serialize_reservation_context(db, updated)

    def auth_delete_account(self) -> dict[str, Any]:
        data = self.body_json()
        password = required_text(data, "password", 200)
        with connection() as db:
            user = self.current_user(db)
            if user["role"] == "ADMIN" or user["account_type"] != "PHONE":
                raise ApiError(400, "ACCOUNT_DELETE_UNAVAILABLE", "该账号不能在 App 内注销")
            if not verify_password(password, user["password_hash"], user["password_salt"]):
                raise ApiError(401, "INVALID_CREDENTIALS", "密码错误")
            result = delete_user_account_data(db, user, reason="用户主动注销账号")
            write_user_event(db, user["id"], "ACCOUNT", "USER_ACCOUNT_DELETED")
            return result

    def sports_venues(self) -> list[dict[str, Any]]:
        with connection() as db:
            self.current_user(db)
            rows = db.execute(
                "SELECT * FROM venues WHERE enabled=1 AND operating_status='OPEN' ORDER BY sport"
            ).fetchall()
            result = []
            for venue in rows:
                slots = db.execute(
                    """SELECT start_time || '-' || end_time AS value FROM venue_time_slots
                       WHERE venue_id=? AND enabled=1 ORDER BY start_time""",
                    (venue["id"],),
                ).fetchall()
                result.append({
                    "id": venue["id"], "sport": venue["sport"], "name": venue["name"],
                    "unitLabel": venue["unit_label"], "courtCount": venue["court_count"],
                    "priceYuan": venue["price_yuan"], "timeSlots": [row["value"] for row in slots],
                })
            return result

    def sports_context(self) -> dict[str, Any]:
        with connection() as db:
            return serialize_reservation_context(db, self.current_user(db))

    def sports_availability(self, query: dict[str, str]) -> dict[str, Any]:
        venue_id = required_text(query, "venueId", 80)
        booking_date = parse_iso_date(required_text(query, "date", 10))
        with connection() as db:
            self.current_user(db)
            venue = db.execute(
                "SELECT * FROM venues WHERE id=? AND enabled=1 AND operating_status='OPEN'",
                (venue_id,),
            ).fetchone()
            if not venue:
                raise ApiError(404, "VENUE_NOT_FOUND", "场馆不存在")
            slots = db.execute(
                """SELECT start_time || '-' || end_time AS value FROM venue_time_slots
                   WHERE venue_id=? AND enabled=1 ORDER BY start_time""",
                (venue_id,),
            ).fetchall()
            occupied = {
                (row["time_slot"], row["court_number"]): "RESERVED"
                for row in db.execute(
                    """SELECT time_slot,court_number FROM reservations
                       WHERE venue_id=? AND booking_date=? AND status='ACTIVE'""",
                    (venue_id, booking_date),
                )
            }
            blocked = {
                (row["time_slot"], row["court_number"]): "UNAVAILABLE"
                for row in db.execute(
                    """SELECT time_slot,court_number FROM blocked_inventory
                       WHERE venue_id=? AND booking_date=?""",
                    (venue_id, booking_date),
                )
            }
            return {
                "venueId": venue_id,
                "date": booking_date,
                "slots": [
                    {
                        "timeSlot": slot["value"],
                        "courts": [
                            {"courtNumber": number, "status": blocked.get((slot["value"], number), occupied.get((slot["value"], number), "AVAILABLE"))}
                            for number in range(1, venue["court_count"] + 1)
                        ],
                    }
                    for slot in slots
                ],
            }

    def sports_reserve(self) -> dict[str, Any]:
        data = self.body_json()
        venue_id = required_text(data, "venueId", 80)
        booking_date = parse_iso_date(required_text(data, "date", 10))
        time_slot = required_text(data, "timeSlot", 20)
        court = int(data.get("courtNumber", 0))
        today = date.today()
        selected = date.fromisoformat(booking_date)
        if selected < today or selected > today + timedelta(days=6):
            raise ApiError(400, "INVALID_DATE", "仅支持今天起未来 7 天")
        with connection() as db:
            user = self.current_user(db)
            require_account_operation(user)
            reservation_id = str(uuid.uuid4())
            timestamp = now_ms()
            try:
                db.execute("BEGIN IMMEDIATE")
                rules = reservation_rules(db)
                if (
                    rules["reservation_profile_mode"] == "REQUIRED"
                    and (
                        not user["reservation_student_id"]
                        or not user["reservation_name"]
                        or not user["reservation_profile_consented_at"]
                    )
                ):
                    raise ApiError(
                        409,
                        "RESERVATION_PROFILE_REQUIRED",
                        "当前预约规则要求先填写并确认真实姓名和预约识别学号",
                    )
                restriction = active_restriction(db, user["id"])
                if (
                    restriction
                    and restriction["reservation_blocked_until"]
                    and restriction["reservation_blocked_until"] > timestamp
                ):
                    raise ApiError(
                        429,
                        "RESERVATION_RESTRICTED",
                        restriction["reason"] or "当前账号暂时无法继续预约",
                    )
                active_count = db.execute(
                    """SELECT COUNT(*) AS count FROM reservations
                       WHERE user_id=? AND booking_date=? AND status='ACTIVE'""",
                    (user["id"], booking_date),
                ).fetchone()["count"]
                if active_count >= rules["max_active_per_day"]:
                    raise ApiError(
                        429,
                        "DAILY_RESERVATION_LIMIT",
                        f"每天最多保留 {rules['max_active_per_day']} 条有效预约",
                    )
                venue = db.execute(
                    """SELECT * FROM venues WHERE id=? AND enabled=1
                       AND operating_status='OPEN'""",
                    (venue_id,),
                ).fetchone()
                if not venue or court not in range(1, venue["court_count"] + 1):
                    raise ApiError(400, "INVALID_INVENTORY", "场地选择无效或场馆维护中")
                slot_exists = db.execute(
                    """SELECT 1 FROM venue_time_slots WHERE venue_id=? AND enabled=1
                       AND start_time || '-' || end_time=?""",
                    (venue_id, time_slot),
                ).fetchone()
                if not slot_exists:
                    raise ApiError(400, "INVALID_TIME_SLOT", "时间段不可预约")
                blocked = db.execute(
                    """SELECT 1 FROM blocked_inventory WHERE venue_id=? AND booking_date=?
                       AND time_slot=? AND court_number=?""",
                    (venue_id, booking_date, time_slot, court),
                ).fetchone()
                if blocked:
                    raise ApiError(409, "INVENTORY_UNAVAILABLE", "该场地当前不可预约")
                db.execute(
                    """INSERT INTO reservations(id,user_id,venue_id,booking_date,time_slot,
                       court_number,price_yuan,reservation_student_id_snapshot,
                       reservation_name_snapshot,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (reservation_id, user["id"], venue_id, booking_date, time_slot, court,
                     venue["price_yuan"], user["reservation_student_id"],
                     user["reservation_name"], timestamp, timestamp),
                )
                write_reservation_event(
                    db,
                    reservation_id,
                    user["id"],
                    user["id"],
                    "CREATED",
                    details={
                        "bookingDate": booking_date,
                        "timeSlot": time_slot,
                        "courtNumber": court,
                    },
                )
                write_user_event(db, user["id"], "SPORTS", "RESERVATION_CREATED", "SUCCESS", "RESERVATION", reservation_id)
                db.execute("COMMIT")
            except ApiError as exc:
                write_reservation_event(
                    db,
                    None,
                    user["id"],
                    user["id"],
                    "RESERVATION_REJECTED",
                    exc.message,
                    {"code": exc.code, "venueId": venue_id, "date": booking_date},
                )
                db.execute("COMMIT")
                raise
            except sqlite3.IntegrityError as exc:
                db.execute("ROLLBACK")
                with connection() as event_db:
                    write_reservation_event(
                        event_db,
                        None,
                        user["id"],
                        user["id"],
                        "RESERVATION_REJECTED",
                        "库存刚刚被其他用户预约",
                        {"code": "INVENTORY_ALREADY_RESERVED"},
                    )
                raise ApiError(409, "INVENTORY_ALREADY_RESERVED", "该场地刚刚已被预约，请重新选择") from exc
            return self.reservation_by_id(db, reservation_id)

    def reservation_by_id(self, db: sqlite3.Connection, reservation_id: str) -> dict[str, Any]:
        row = db.execute(
            """SELECT r.*,v.sport,v.name,v.unit_label FROM reservations r
               JOIN venues v ON v.id=r.venue_id WHERE r.id=?""",
            (reservation_id,),
        ).fetchone()
        if not row:
            raise ApiError(404, "RESERVATION_NOT_FOUND", "预约不存在")
        cancel = cancellation_preview(db, row)
        return {
            "id": row["id"], "sport": row["sport"], "venueId": row["venue_id"],
            "venueName": row["name"], "unitLabel": row["unit_label"],
            "date": row["booking_date"], "timeSlot": row["time_slot"],
            "courtNumber": row["court_number"], "priceYuan": row["price_yuan"],
            "status": row["status"], "paymentStatus": row["payment_status"],
            "refundStatus": row["refund_status"],
            "verificationStatus": row["verification_status"],
            "cancelledAtMillis": row["cancelled_at"],
            "cancelledBy": row["cancelled_by"],
            "cancelReason": row["cancel_reason"],
            "reservationStudentIdSnapshot": row["reservation_student_id_snapshot"],
            "reservationNameSnapshot": row["reservation_name_snapshot"],
            "canCancel": bool(cancel.get("canCancel")),
            "cancelUnavailableReason": cancel.get("reason", ""),
            "nextCancellationCount": cancel.get("nextCancellationCount"),
            "remainingCancellationsAfterCancel": cancel.get("remainingAfterCancel"),
            "createdAtMillis": row["created_at"],
        }

    def sports_reservations(self) -> list[dict[str, Any]]:
        with connection() as db:
            user = self.current_user(db)
            ids = db.execute(
                "SELECT id FROM reservations WHERE user_id=? ORDER BY created_at DESC",
                (user["id"],),
            ).fetchall()
            return [self.reservation_by_id(db, row["id"]) for row in ids]

    def sports_cancel(self, reservation_id: str) -> dict[str, Any]:
        with connection() as db:
            user = self.current_user(db)
            require_account_operation(user)
            timestamp = now_ms()
            db.execute("BEGIN IMMEDIATE")
            reservation = db.execute(
                """SELECT * FROM reservations
                   WHERE id=? AND user_id=? AND status='ACTIVE'""",
                (reservation_id, user["id"]),
            ).fetchone()
            if not reservation:
                db.execute("ROLLBACK")
                raise ApiError(409, "CANNOT_CANCEL", "预约不存在或已取消")
            rules = reservation_rules(db)
            restriction = active_restriction(db, user["id"])
            if (
                restriction
                and restriction["cancellation_blocked_until"]
                and restriction["cancellation_blocked_until"] > timestamp
            ):
                write_reservation_event(
                    db,
                    reservation_id,
                    user["id"],
                    user["id"],
                    "CANCEL_REJECTED",
                    restriction["reason"] or "当前账号暂时无法继续取消预约",
                    {"code": "CANCELLATION_RESTRICTED"},
                )
                db.execute("COMMIT")
                raise ApiError(
                    429,
                    "CANCELLATION_RESTRICTED",
                    restriction["reason"] or "当前账号暂时无法继续取消预约",
                )
            start_text = reservation["time_slot"].split("-", 1)[0]
            start_at = datetime.strptime(
                f"{reservation['booking_date']} {start_text}", "%Y-%m-%d %H:%M"
            )
            if datetime.now() + timedelta(minutes=rules["cancel_cutoff_minutes"]) >= start_at:
                message = f"开场前 {rules['cancel_cutoff_minutes']} 分钟内不可自行取消"
                write_reservation_event(
                    db,
                    reservation_id,
                    user["id"],
                    user["id"],
                    "CANCEL_REJECTED",
                    message,
                    {"code": "CANCEL_CUTOFF"},
                )
                db.execute("COMMIT")
                raise ApiError(409, "CANCEL_CUTOFF", message)
            day_start = int(
                datetime.combine(date.today(), datetime.min.time()).timestamp() * 1000
            )
            cancellation_count = db.execute(
                """SELECT COUNT(*) AS count FROM reservation_events
                   WHERE user_id=? AND action='USER_CANCELLED' AND created_at>=?""",
                (user["id"], day_start),
            ).fetchone()["count"]
            if cancellation_count >= rules["max_user_cancellations_per_day"]:
                blocked_until = timestamp + (
                    rules["restriction_hours_after_excess_cancel"] * 60 * 60 * 1000
                )
                reason = (
                    f"今日主动取消已达 {rules['max_user_cancellations_per_day']} 次，"
                    f"请在限制结束后再试"
                )
                upsert_restriction(
                    db, user["id"], blocked_until, blocked_until, reason
                )
                write_reservation_event(
                    db,
                    reservation_id,
                    user["id"],
                    user["id"],
                    "CANCEL_REJECTED",
                    reason,
                    {"code": "DAILY_CANCELLATION_LIMIT"},
                )
                db.execute("COMMIT")
                raise ApiError(429, "DAILY_CANCELLATION_LIMIT", reason)
            db.execute(
                """UPDATE reservations SET status='CANCELLED',cancelled_at=?,
                   cancelled_by='USER',cancel_reason='用户主动取消',updated_at=?
                   WHERE id=?""",
                (timestamp, timestamp, reservation_id),
            )
            write_reservation_event(
                db,
                reservation_id,
                user["id"],
                user["id"],
                "USER_CANCELLED",
                "用户主动取消",
            )
            write_user_event(db, user["id"], "SPORTS", "RESERVATION_CANCELLED", "SUCCESS", "RESERVATION", reservation_id)
            if cancellation_count + 1 >= rules["max_user_cancellations_per_day"]:
                blocked_until = timestamp + (
                    rules["restriction_hours_after_excess_cancel"] * 60 * 60 * 1000
                )
                reason = (
                    f"今日主动取消已达 {rules['max_user_cancellations_per_day']} 次，"
                    f"已暂时限制继续预约和取消"
                )
                upsert_restriction(
                    db, user["id"], blocked_until, blocked_until, reason
                )
            db.execute("COMMIT")
            return self.reservation_by_id(db, reservation_id)

    def serialize_lost(self, db: sqlite3.Connection, row: sqlite3.Row, user_id: str) -> dict[str, Any]:
        favorite = db.execute(
            "SELECT 1 FROM lost_found_favorites WHERE user_id=? AND post_id=?",
            (user_id, row["id"]),
        ).fetchone() is not None
        claimed = db.execute(
            "SELECT 1 FROM lost_found_claims WHERE user_id=? AND post_id=?",
            (user_id, row["id"]),
        ).fetchone() is not None
        return {
            "id": row["id"], "clientLocalId": row["client_local_id"], "type": row["type"],
            "itemName": row["item_name"], "category": row["category"],
            "location": row["location"], "eventDate": row["event_date"],
            "approximateTime": row["approximate_time"], "description": row["description"],
            "claimNote": row["claim_note"], "imageUrl": f"/uploads/{row['image_name']}" if row["image_name"] else None,
            "status": row["status"], "createdAtMillis": row["created_at"],
            "updatedAtMillis": row["updated_at"], "isMine": row["owner_user_id"] == user_id,
            "isFavorite": favorite, "hasClaimRequest": claimed,
        }

    def record_lost_deletion(
        self,
        db: sqlite3.Connection,
        post: sqlite3.Row,
        actor_user_id: str,
        reason: str,
    ) -> None:
        client_local_id = str(post["client_local_id"] or "").strip()
        if not client_local_id:
            return
        db.execute(
            """INSERT INTO lost_found_deletion_tombstones(
                   id,owner_user_id,client_local_id,deleted_by_user_id,reason,created_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(owner_user_id,client_local_id) DO UPDATE SET
                   deleted_by_user_id=excluded.deleted_by_user_id,
                   reason=excluded.reason,
                   created_at=excluded.created_at""",
            (
                str(uuid.uuid4()),
                post["owner_user_id"],
                client_local_id,
                actor_user_id,
                reason[:300],
                now_ms(),
            ),
        )

    def lost_list(self, query: dict[str, str]) -> list[dict[str, Any]]:
        with connection() as db:
            user = self.current_user(db)
            require_account_operation(user)
            clauses, values = [], []
            lost_type = query.get("type", "").upper()
            if lost_type in LOST_TYPES:
                clauses.append("type=?")
                values.append(lost_type)
            if query.get("mine") == "true":
                clauses.append("owner_user_id=?")
                values.append(user["id"])
            search = query.get("q", "").strip()
            if search:
                clauses.append("(item_name LIKE ? OR description LIKE ? OR location LIKE ? OR category LIKE ?)")
                token = f"%{search[:80]}%"
                values.extend([token] * 4)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            rows = db.execute(
                "SELECT * FROM lost_found_posts" + where + " ORDER BY updated_at DESC LIMIT 200",
                values,
            ).fetchall()
            return [self.serialize_lost(db, row, user["id"]) for row in rows]

    def lost_get(self, post_id: str) -> dict[str, Any]:
        with connection() as db:
            user = self.current_user(db)
            require_account_operation(user)
            row = db.execute("SELECT * FROM lost_found_posts WHERE id=?", (post_id,)).fetchone()
            if not row:
                raise ApiError(404, "POST_NOT_FOUND", "失物信息不存在")
            return self.serialize_lost(db, row, user["id"])

    def validated_lost(self, data: dict[str, Any], existing: sqlite3.Row | None = None) -> dict[str, Any]:
        lost_type = str(data.get("type", existing["type"] if existing else "")).upper()
        if lost_type not in LOST_TYPES:
            raise ApiError(400, "VALIDATION_ERROR", "type 必须为 LOST 或 FOUND")
        status = str(data.get("status", existing["status"] if existing else ("SEARCHING" if lost_type == "LOST" else "WAITING"))).upper()
        if status not in LOST_STATUSES[lost_type]:
            raise ApiError(400, "VALIDATION_ERROR", "状态与失物类型不匹配")
        return {
            "type": lost_type,
            "item_name": required_text(data, "itemName", 80) if "itemName" in data or not existing else existing["item_name"],
            "category": required_text(data, "category", 40) if "category" in data or not existing else existing["category"],
            "location": required_text(data, "location", 120) if "location" in data or not existing else existing["location"],
            "event_date": parse_iso_date(str(data.get("eventDate", existing["event_date"] if existing else "")), future_allowed=False),
            "approximate_time": str(data.get("approximateTime", existing["approximate_time"] if existing else "") or "")[:40] or None,
            "description": str(data.get("description", existing["description"] if existing else "") or "")[:1000],
            "claim_note": str(data.get("claimNote", existing["claim_note"] if existing else "") or "")[:500],
            "status": status,
        }

    def save_image(self, encoded: Any, storage_id: str) -> str | None:
        if not encoded:
            return None
        try:
            raw = base64.b64decode(str(encoded), validate=True)
        except Exception as exc:
            raise ApiError(400, "INVALID_IMAGE", "图片数据无效") from exc
        if len(raw) > RUNTIME.upload_max_bytes:
            raise ApiError(413, "IMAGE_TOO_LARGE", "图片不能超过 3MB")
        if not re.fullmatch(r"[0-9a-f-]{36}", storage_id):
            raise ApiError(400, "INVALID_IMAGE", "图片标识无效")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as source:
                    image_format = (source.format or "").upper()
                    if image_format not in {"JPEG", "PNG", "WEBP"}:
                        raise ApiError(400, "INVALID_IMAGE", "仅支持 JPEG、PNG 或 WebP")
                    if source.width * source.height > RUNTIME.upload_max_pixels:
                        raise ApiError(413, "IMAGE_TOO_LARGE", "图片像素尺寸过大")
                    source.load()
                    output = io.BytesIO()
                    if image_format == "JPEG":
                        suffix = ".jpg"
                        normalized = source.convert("RGB")
                        normalized.save(output, "JPEG", quality=90, optimize=True)
                    elif image_format == "PNG":
                        suffix = ".png"
                        normalized = source.convert("RGBA" if "A" in source.getbands() else "RGB")
                        normalized.save(output, "PNG", optimize=True)
                    else:
                        suffix = ".webp"
                        normalized = source.convert("RGBA" if "A" in source.getbands() else "RGB")
                        normalized.save(output, "WEBP", quality=90, method=4)
                    normalized_bytes = output.getvalue()
        except ApiError:
            raise
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
            raise ApiError(400, "INVALID_IMAGE", "图片内容无法安全解析") from exc
        if len(normalized_bytes) > RUNTIME.upload_max_bytes:
            raise ApiError(413, "IMAGE_TOO_LARGE", "规范化后的图片不能超过 3MB")
        name = storage_id + suffix
        target = (UPLOADS / name).resolve()
        if target.parent != UPLOADS.resolve():
            raise ApiError(400, "INVALID_IMAGE", "图片路径无效")
        temporary = UPLOADS / f".{storage_id}.{secrets.token_hex(8)}.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(normalized_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            _safe_chmod(temporary, 0o640)
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return name

    def lost_create(self) -> dict[str, Any]:
        data = self.body_json()
        values = self.validated_lost(data)
        post_id = str(uuid.uuid4())
        timestamp = now_ms()
        with connection() as db:
            user = self.current_user(db)
            require_account_operation(user)
            client_local_id = str(data.get("clientLocalId") or "")[:100] or None
            if client_local_id and db.execute(
                """SELECT 1 FROM lost_found_deletion_tombstones
                   WHERE owner_user_id=? AND client_local_id=?""",
                (user["id"], client_local_id),
            ).fetchone():
                raise ApiError(
                    410,
                    "POST_DELETED",
                    "该失物信息已被删除，不能由旧缓存重新发布",
                )
            image_name = self.save_image(
                data.get("imageBase64"), str(uuid.uuid4())
            )
            try:
                db.execute(
                    """INSERT INTO lost_found_posts(id,owner_user_id,client_local_id,type,item_name,
                       category,location,event_date,approximate_time,description,claim_note,image_name,
                       status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (post_id, user["id"], client_local_id,
                     values["type"], values["item_name"], values["category"], values["location"],
                     values["event_date"], values["approximate_time"], values["description"],
                     values["claim_note"], image_name, values["status"], timestamp, timestamp),
                )
            except sqlite3.IntegrityError:
                existing = db.execute(
                    "SELECT * FROM lost_found_posts WHERE owner_user_id=? AND client_local_id=?",
                    (user["id"], data.get("clientLocalId")),
                ).fetchone()
                if existing:
                    if image_name:
                        run_safely_delete_upload(image_name)
                    return self.serialize_lost(db, existing, user["id"])
                if image_name:
                    run_safely_delete_upload(image_name)
                raise
            except Exception:
                if image_name:
                    run_safely_delete_upload(image_name)
                raise
            row = db.execute("SELECT * FROM lost_found_posts WHERE id=?", (post_id,)).fetchone()
            write_user_event(db, user["id"], "LOST_FOUND", "LOST_FOUND_PUBLISHED", "SUCCESS", "LOST_FOUND_POST", post_id)
            return self.serialize_lost(db, row, user["id"])

    def lost_update(self, post_id: str) -> dict[str, Any]:
        data = self.body_json()
        with connection() as db:
            user = self.current_user(db)
            require_account_operation(user)
            existing = db.execute("SELECT * FROM lost_found_posts WHERE id=?", (post_id,)).fetchone()
            if not existing:
                raise ApiError(404, "POST_NOT_FOUND", "失物信息不存在")
            if existing["owner_user_id"] != user["id"] and user["role"] != "ADMIN":
                raise ApiError(403, "FORBIDDEN", "只能修改自己的发布")
            values = self.validated_lost(data, existing)
            image_name = existing["image_name"]
            if "imageBase64" in data:
                image_name = self.save_image(
                    data.get("imageBase64"), str(uuid.uuid4())
                )
            try:
                db.execute(
                    """UPDATE lost_found_posts SET type=?,item_name=?,category=?,location=?,event_date=?,
                       approximate_time=?,description=?,claim_note=?,image_name=?,status=?,updated_at=?
                       WHERE id=?""",
                    (values["type"], values["item_name"], values["category"], values["location"],
                     values["event_date"], values["approximate_time"], values["description"],
                     values["claim_note"], image_name, values["status"], now_ms(), post_id),
                )
            except Exception:
                if image_name and image_name != existing["image_name"]:
                    run_safely_delete_upload(image_name)
                raise
            if existing["image_name"] and existing["image_name"] != image_name:
                run_safely_delete_upload(existing["image_name"])
            updated = db.execute("SELECT * FROM lost_found_posts WHERE id=?", (post_id,)).fetchone()
            write_user_event(db, user["id"], "LOST_FOUND", "LOST_FOUND_EDITED", "SUCCESS", "LOST_FOUND_POST", post_id)
            return self.serialize_lost(db, updated, user["id"])

    def lost_delete(self, post_id: str) -> dict[str, Any]:
        with connection() as db:
            user = self.current_user(db)
            require_account_operation(user)
            post = db.execute("SELECT * FROM lost_found_posts WHERE id=?", (post_id,)).fetchone()
            if not post:
                raise ApiError(404, "POST_NOT_FOUND", "失物信息不存在")
            if post["owner_user_id"] != user["id"] and user["role"] != "ADMIN":
                raise ApiError(403, "FORBIDDEN", "只能删除自己的发布")
            self.record_lost_deletion(
                db, post, user["id"], "用户删除自己的失物信息"
            )
            db.execute("DELETE FROM lost_found_posts WHERE id=?", (post_id,))
            if post["image_name"]:
                run_safely_delete_upload(post["image_name"])
            write_user_event(db, user["id"], "LOST_FOUND", "LOST_FOUND_DELETED", "SUCCESS", "LOST_FOUND_POST", post_id)
            return {"deleted": True, "id": post_id}

    def lost_favorite(self, post_id: str) -> dict[str, Any]:
        with connection() as db:
            user = self.current_user(db)
            require_account_operation(user)
            if not db.execute("SELECT 1 FROM lost_found_posts WHERE id=?", (post_id,)).fetchone():
                raise ApiError(404, "POST_NOT_FOUND", "失物信息不存在")
            existing = db.execute(
                "SELECT 1 FROM lost_found_favorites WHERE user_id=? AND post_id=?",
                (user["id"], post_id),
            ).fetchone()
            if existing:
                db.execute("DELETE FROM lost_found_favorites WHERE user_id=? AND post_id=?", (user["id"], post_id))
            else:
                db.execute("INSERT INTO lost_found_favorites VALUES(?,?,?)", (user["id"], post_id, now_ms()))
            write_user_event(db, user["id"], "LOST_FOUND", "LOST_FOUND_UNFAVORITED" if existing else "LOST_FOUND_FAVORITED", "SUCCESS", "LOST_FOUND_POST", post_id)
            return {"postId": post_id, "isFavorite": not bool(existing)}

    def lost_favorites(self) -> list[dict[str, Any]]:
        with connection() as db:
            user = self.current_user(db)
            require_account_operation(user)
            rows = db.execute(
                """SELECT p.* FROM lost_found_favorites f JOIN lost_found_posts p ON p.id=f.post_id
                   WHERE f.user_id=? ORDER BY f.created_at DESC""",
                (user["id"],),
            ).fetchall()
            return [self.serialize_lost(db, row, user["id"]) for row in rows]

    def lost_claim(self, post_id: str) -> dict[str, Any]:
        with connection() as db:
            user = self.current_user(db)
            require_account_operation(user)
            post = db.execute("SELECT * FROM lost_found_posts WHERE id=?", (post_id,)).fetchone()
            if not post or post["type"] != "FOUND" or post["status"] == "RETURNED":
                raise ApiError(409, "CLAIM_UNAVAILABLE", "当前不能申请认领")
            if post["owner_user_id"] == user["id"]:
                raise ApiError(409, "CLAIM_OWN_POST", "不能认领自己发布的信息")
            db.execute(
                "INSERT OR IGNORE INTO lost_found_claims VALUES(?,?,?)",
                (user["id"], post_id, now_ms()),
            )
            write_user_event(db, user["id"], "LOST_FOUND", "LOST_FOUND_CLAIMED", "SUCCESS", "LOST_FOUND_POST", post_id)
            return {"postId": post_id, "submitted": True, "message": "认领申请已记录；本阶段不提供站内联系"}

    def lost_report(self, post_id: str) -> dict[str, Any]:
        data = self.body_json()
        with connection() as db:
            user = self.current_user(db)
            require_account_operation(user)
            if not db.execute("SELECT 1 FROM lost_found_posts WHERE id=?", (post_id,)).fetchone():
                raise ApiError(404, "POST_NOT_FOUND", "失物信息不存在")
            report_id = str(uuid.uuid4())
            db.execute(
                """INSERT INTO lost_found_reports(id,user_id,post_id,reason,created_at)
                   VALUES(?,?,?,?,?) ON CONFLICT(user_id,post_id)
                   DO UPDATE SET reason=excluded.reason,status='OPEN',created_at=excluded.created_at""",
                (report_id, user["id"], post_id, required_text(data, "reason", 200), now_ms()),
            )
            write_user_event(db, user["id"], "LOST_FOUND", "LOST_FOUND_REPORTED", "SUCCESS", "LOST_FOUND_POST", post_id, risk_flag="CONTENT_REPORT")
            return {"postId": post_id, "recorded": True}

    def serialize_resource(
        self, db: sqlite3.Connection, row: sqlite3.Row, user_id: str | None
    ) -> dict[str, Any]:
        favorite = False
        view = None
        if user_id:
            favorite = db.execute(
                "SELECT 1 FROM resource_favorites WHERE user_id=? AND resource_id=?",
                (user_id, row["id"]),
            ).fetchone() is not None
            view = db.execute(
                """SELECT view_count,last_viewed_at FROM resource_views
                   WHERE user_id=? AND resource_id=?""",
                (user_id, row["id"]),
            ).fetchone()
        return {
            "id": row["id"],
            "title": row["title"],
            "summary": row["summary"],
            "category": row["category"],
            "tags": json.loads(row["tags_json"] or "[]"),
            "audienceScope": row["audience_scope"],
            "publisherLabel": row["publisher_label"],
            "sourceLabel": row["source_label"],
            "usageNote": row["usage_note"],
            "resourceType": row["resource_type"],
            "externalUrl": row["external_url"],
            "coverUrl": row["cover_url"],
            "isFree": bool(row["is_free"]),
            "futurePriceFen": row["future_price_fen"],
            "publishedAt": row["published_at"],
            "isFavorite": favorite,
            "viewCount": view["view_count"] if view else 0,
            "lastViewedAt": view["last_viewed_at"] if view else None,
        }

    def library_list(self, query: dict[str, str]) -> list[dict[str, Any]]:
        with connection() as db:
            user = self.library_reader(db)
            clauses = ["i.publish_status='PUBLISHED'"]
            values: list[Any] = []
            if not user:
                clauses.append("i.visibility='PUBLIC'")
            if query.get("category"):
                clauses.append("i.category=?")
                values.append(query["category"][:80])
            if query.get("q"):
                token = f"%{query['q'][:100]}%"
                clauses.append(
                    """(i.title LIKE ? OR i.summary LIKE ? OR i.category LIKE ?
                        OR i.tags_json LIKE ? OR i.audience_scope LIKE ?)"""
                )
                values.extend([token, token, token, token, token])
            joins = ""
            order = "i.sort_order DESC,i.published_at DESC"
            if query.get("favorites", "").lower() == "true":
                if not user:
                    raise ApiError(401, "UNAUTHORIZED", "登录后才能查看收藏")
                joins += (
                    " JOIN resource_favorites f ON f.resource_id=i.id"
                    " AND f.user_id=?"
                )
                values.insert(0, user["id"])
                order = "f.created_at DESC"
            if query.get("recent", "").lower() == "true":
                if not user:
                    raise ApiError(401, "UNAUTHORIZED", "登录后才能查看最近浏览")
                joins += (
                    " JOIN resource_views rv ON rv.resource_id=i.id"
                    " AND rv.user_id=?"
                )
                values.insert(0, user["id"])
                order = "rv.last_viewed_at DESC"
            rows = db.execute(
                f"""SELECT DISTINCT i.* FROM resource_items i{joins}
                    WHERE {' AND '.join(clauses)}
                    ORDER BY {order} LIMIT 300""",
                values,
            ).fetchall()
            return [
                self.serialize_resource(db, row, user["id"] if user else None)
                for row in rows
            ]

    def library_get(self, resource_id: str) -> dict[str, Any]:
        with connection() as db:
            user = self.library_reader(db)
            visibility_clause = "" if user else " AND visibility='PUBLIC'"
            row = db.execute(
                """SELECT * FROM resource_items
                   WHERE id=? AND publish_status='PUBLISHED'"""
                + visibility_clause,
                (resource_id,),
            ).fetchone()
            if not row:
                raise ApiError(404, "RESOURCE_NOT_FOUND", "资料不存在或已下架")
            return self.serialize_resource(db, row, user["id"] if user else None)

    def library_view(self, resource_id: str) -> dict[str, Any]:
        with connection() as db:
            user = self.current_user(db)
            require_account_operation(user)
            if not db.execute(
                """SELECT 1 FROM resource_items
                   WHERE id=? AND publish_status='PUBLISHED'""",
                (resource_id,),
            ).fetchone():
                raise ApiError(404, "RESOURCE_NOT_FOUND", "资料不存在或已下架")
            timestamp = now_ms()
            db.execute(
                """INSERT INTO resource_views(
                       user_id,resource_id,view_count,last_viewed_at
                   ) VALUES(?,?,1,?) ON CONFLICT(user_id,resource_id) DO UPDATE SET
                   view_count=resource_views.view_count+1,
                   last_viewed_at=excluded.last_viewed_at""",
                (user["id"], resource_id, timestamp),
            )
            write_user_event(db, user["id"], "RESOURCE_LIBRARY", "RESOURCE_VIEWED", "SUCCESS", "RESOURCE", resource_id)
            return {"resourceId": resource_id, "viewedAt": timestamp}

    def library_favorite(self, resource_id: str) -> dict[str, Any]:
        with connection() as db:
            user = self.current_user(db)
            require_account_operation(user)
            if not db.execute(
                """SELECT 1 FROM resource_items
                   WHERE id=? AND publish_status='PUBLISHED'""",
                (resource_id,),
            ).fetchone():
                raise ApiError(404, "RESOURCE_NOT_FOUND", "资料不存在或已下架")
            existing = db.execute(
                """SELECT 1 FROM resource_favorites
                   WHERE user_id=? AND resource_id=?""",
                (user["id"], resource_id),
            ).fetchone()
            if existing:
                db.execute(
                    "DELETE FROM resource_favorites WHERE user_id=? AND resource_id=?",
                    (user["id"], resource_id),
                )
            else:
                db.execute(
                    "INSERT INTO resource_favorites VALUES(?,?,?)",
                    (user["id"], resource_id, now_ms()),
                )
            write_user_event(db, user["id"], "RESOURCE_LIBRARY", "RESOURCE_UNFAVORITED" if existing else "RESOURCE_FAVORITED", "SUCCESS", "RESOURCE", resource_id)
            return {"resourceId": resource_id, "isFavorite": not bool(existing)}

    def library_complaint(self, resource_id: str) -> dict[str, Any]:
        data = self.body_json()
        with connection() as db:
            user = self.current_user(db)
            if not db.execute(
                "SELECT 1 FROM resource_items WHERE id=?", (resource_id,)
            ).fetchone():
                raise ApiError(404, "RESOURCE_NOT_FOUND", "资料不存在")
            complaint_id = str(uuid.uuid4())
            timestamp = now_ms()
            db.execute(
                """INSERT INTO resource_complaints(
                       id,user_id,resource_id,reason,detail,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    complaint_id,
                    user["id"],
                    resource_id,
                    required_text(data, "reason", 80),
                    str(data.get("detail", "")).strip()[:1000],
                    timestamp,
                    timestamp,
                ),
            )
            write_user_event(db, user["id"], "RESOURCE_LIBRARY", "RESOURCE_COMPLAINT_SUBMITTED", "SUCCESS", "RESOURCE", resource_id, risk_flag="CONTENT_REPORT")
            return {"id": complaint_id, "status": "OPEN"}

    def public_bootstrap(self, query: dict[str, str] | None = None) -> dict[str, Any]:
        query = query or {}
        wants_dynamic_config = (
            str(query.get("schemaVersion", "")) == str(APP_CONFIG_SCHEMA_VERSION)
            and str(query.get("appVersionCode", "")).isdigit()
        )
        with connection() as db:
            flags = []
            for row in db.execute("SELECT key FROM remote_flags ORDER BY key"):
                state = feature_state(db, row["key"])
                flags.append(
                    {
                        "key": state["key"],
                        "enabled": state["enabled"],
                        "updatedAt": state["updatedAt"],
                        "payload": {
                            "status": state["status"],
                            "maintenanceNote": state["maintenanceNote"],
                            "expectedRestoreAt": state["expectedRestoreAt"],
                        },
                    }
                )
            announcements = []
            for row in db.execute(
                """SELECT id,title,content,
                          notification_enabled AS notificationEnabled,
                          popup_enabled AS popupEnabled,
                          published_at AS publishedAt
                     FROM announcements
                    WHERE enabled=1
                    ORDER BY published_at DESC
                    LIMIT 10"""
            ):
                announcements.append(
                    {
                        **row_dict(row),
                        "notificationEnabled": bool(row["notificationEnabled"]),
                        "popupEnabled": bool(row["popupEnabled"]),
                    }
                )
            version_row = db.execute(
                "SELECT * FROM app_versions WHERE platform='android' AND status='PUBLISHED'"
            ).fetchone()
            policy = update_policy_state(db)
            # Bootstrap is not a client-version-aware policy check. Keep its
            # legacy version object non-mandatory and expose the actual live
            # policy separately so it can never become a second force source.
            version = (
                serialize_public_version(
                    version_row,
                    effective_force_update=False,
                    legacy_minimum_version_code=1,
                )
                if version_row
                else None
            )
            payload = {
                "flags": flags,
                "announcements": announcements,
                "version": version,
                "releaseMetadata": (
                    serialize_release_metadata(version_row) if version_row else None
                ),
                "updatePolicy": policy,
            }
            if wants_dynamic_config:
                config_row = active_app_config(db)
                if config_row:
                    app_version = int(query["appVersionCode"])
                    min_code = int(config_row["min_app_version_code"])
                    max_code = config_row["max_app_version_code"]
                    compatible = app_version >= min_code and (
                        max_code is None or app_version <= int(max_code)
                    )
                    if compatible:
                        payload.update(
                            {
                                "schemaVersion": int(config_row["schema_version"]),
                                "configVersion": int(config_row["config_version"]),
                                "minAppVersionCode": min_code,
                                "maxAppVersionCode": max_code,
                                "issuedAt": int(config_row["updated_at"]),
                                "expiresAt": None,
                                "configHash": config_row["config_hash"],
                                "config": json.loads(config_row["config_json"]),
                            }
                        )
            return payload

    def public_website_announcements(self) -> dict[str, Any]:
        """Return enabled website announcements, never App announcements."""
        with connection() as db:
            announcements = [
                row_dict(row)
                for row in db.execute(
                    """SELECT id,title,content,
                              published_at AS publishedAt,
                              updated_at AS updatedAt
                         FROM website_announcements
                        WHERE enabled=1
                        ORDER BY published_at DESC, updated_at DESC
                        LIMIT 10"""
                )
            ]
            return {"websiteAnnouncements": announcements}

    def public_version_check(self, query: dict[str, str]) -> dict[str, Any]:
        current_text = str(query.get("currentVersionCode", "0"))
        if not current_text.isdigit():
            raise ApiError(400, "INVALID_VERSION_CODE", "当前版本号无效")
        current = int(current_text)
        with connection() as db:
            row = db.execute(
                """SELECT platform,latest_version_code,minimum_version_code,
                   latest_version_name,update_message,download_url,apk_sha256,
                   apk_size,package_name,signing_certificate_sha256,metadata_verified,
                   force_update,published_at,status
                   FROM app_versions WHERE platform='android' AND status='PUBLISHED'"""
            ).fetchone()
            policy = update_policy_state(db)
            decision = update_decision(current, row, policy)
            # V1.0.4 reads force_update and minimum_version_code from the nested
            # release object.  Feed it this request's effective server decision,
            # not the historical fields stored with a published release.  When
            # force control is off, a neutral legacy minimum prevents an old
            # client from recreating FORCE solely from a version comparison.
            legacy_version = None
            if row:
                legacy_version = serialize_public_version(
                    row,
                    effective_force_update=decision["mandatoryUpdate"],
                    legacy_minimum_version_code=(
                        policy["minimumSupportedVersionCode"]
                        if policy["forceUpdateEnabled"]
                        else 1
                    ),
                )
                if not policy["updateEnabled"]:
                    # V1.0.4 treats a missing latestVersion as a backend
                    # failure and falls back to the static website manifest.
                    # Return a request-scoped neutral version instead so the
                    # server's explicit OFF decision wins that legacy flow.
                    legacy_version["latest_version_code"] = current
                    legacy_version["versionCode"] = current
            request_timestamp = now_ms()
            logging.getLogger("mychuannong.update").info(
                "update policy decision",
                extra={
                    "event": "UPDATE_POLICY_DECISION",
                    "client_version_code": current,
                    "latest_version_code": policy["latestVersionCode"],
                    "update_decision": decision["decision"],
                    "policy_revision": policy["policyRevision"],
                },
            )
            return {
                **decision,
                "forceUpdate": decision["mandatoryUpdate"],
                # Retained as the V1 compatibility alias for existing clients.
                "mandatoryUpdate": decision["mandatoryUpdate"],
                "currentVersionCode": current,
                "latestVersion": legacy_version,
                "releaseMetadata": serialize_release_metadata(row) if row else None,
                "policy": policy,
                "updateEnabled": policy["updateEnabled"],
                "forceUpdateEnabled": policy["forceUpdateEnabled"],
                "latestVersionCode": policy["latestVersionCode"],
                "minimumSupportedVersionCode": policy[
                    "minimumSupportedVersionCode"
                ],
                "policyRevision": policy["policyRevision"],
                "policyUpdatedAt": policy["updatedAt"],
                "requestTimestamp": request_timestamp,
            }

    def public_service_updates(self) -> list[dict[str, Any]]:
        """Aggregate user-facing service dynamics for the App service page.

        Returns a stable, priority-sorted list of update cards. Only public
        information is included: PUBLISHED Android versions, current feature
        OPEN/MAINTENANCE/CLOSED states and enabled announcements marked visible
        on the service page. No admin, audit, token, download key or internal
        fields are exposed.
        """
        items: list[dict[str, Any]] = []
        with connection() as db:
            version = db.execute(
                """SELECT platform,latest_version_code,minimum_version_code,
                   latest_version_name,update_message,download_url,apk_sha256,
                   apk_size,package_name,signing_certificate_sha256,metadata_verified,
                   force_update,published_at
                   FROM app_versions WHERE platform='android' AND status='PUBLISHED'"""
            ).fetchone()
            policy = update_policy_state(db)
            if version and policy["updateEnabled"]:
                force_update = bool(policy["forceUpdateEnabled"])
                items.append(
                    {
                        "id": f"update-{version['latest_version_code']}",
                        "type": "UPDATE",
                        "priority": 100 if force_update else 70,
                        "title": "发现新版本" if not force_update else "请升级到新版本",
                        "summary": version["update_message"]
                        or f"新版本 {version['latest_version_name']} 已发布",
                        "targetType": "ABOUT_UPDATE",
                        "targetId": None,
                        "versionCode": version["latest_version_code"],
                        "versionName": version["latest_version_name"],
                        "forceUpdate": force_update,
                        "minimumVersionCode": policy[
                            "minimumSupportedVersionCode"
                        ],
                        "policyRevision": policy["policyRevision"],
                        "downloadUrl": version["download_url"],
                        "apkSha256": version["apk_sha256"],
                        "apkSize": version["apk_size"],
                        "moduleKey": None,
                        "recoverAt": None,
                        "publishedAt": version["published_at"],
                        "dismissible": False,
                    }
                )

            feature_labels = {
                "sports_reservation": "体育预约",
                "lost_found": "失物招领",
                "resource_library": "资料库",
            }
            for key, label in feature_labels.items():
                state = feature_state(db, key)
                status = state["status"]
                if status == "MAINTENANCE":
                    items.append(
                        {
                            "id": f"maintenance-{key}",
                            "type": "MAINTENANCE",
                            "priority": 80,
                            "title": f"{label}维护中",
                            "summary": state["maintenanceNote"] or f"{label}正在维护，请稍后再试",
                            "targetType": "FEATURE",
                            "targetId": key,
                            "versionCode": None,
                            "versionName": None,
                            "forceUpdate": None,
                            "minimumVersionCode": None,
                            "moduleKey": key,
                            "recoverAt": state["expectedRestoreAt"],
                            "publishedAt": state["updatedAt"],
                            "dismissible": True,
                        }
                    )
                elif status == "CLOSED":
                    items.append(
                        {
                            "id": f"closed-{key}",
                            "type": "CLOSED",
                            "priority": 90,
                            "title": f"{label}暂不开放",
                            "summary": state["maintenanceNote"] or f"{label}当前暂不开放",
                            "targetType": "FEATURE",
                            "targetId": key,
                            "versionCode": None,
                            "versionName": None,
                            "forceUpdate": None,
                            "minimumVersionCode": None,
                            "moduleKey": key,
                            "recoverAt": state["expectedRestoreAt"],
                            "publishedAt": state["updatedAt"],
                            "dismissible": True,
                        }
                    )

            announcements = db.execute(
                """SELECT id,title,content,published_at FROM announcements
                   WHERE enabled=1 AND service_visible=1
                   ORDER BY published_at DESC LIMIT 5"""
            ).fetchall()
            for announcement in announcements:
                items.append(
                    {
                        "id": f"announcement-{announcement['id']}",
                        "type": "ANNOUNCEMENT",
                        "priority": 60,
                        "title": announcement["title"],
                        "summary": announcement["content"],
                        "targetType": "ANNOUNCEMENT",
                        "targetId": announcement["id"],
                        "versionCode": None,
                        "versionName": None,
                        "forceUpdate": None,
                        "minimumVersionCode": None,
                        "moduleKey": None,
                        "recoverAt": None,
                        "publishedAt": announcement["published_at"],
                        "dismissible": True,
                    }
                )

        return sorted(
            items,
            key=lambda item: (
                -int(item["priority"]),
                -int(item["publishedAt"] or 0),
                item["id"],
            ),
        )

    def feedback_create(self) -> dict[str, Any]:
        data = self.body_json()
        with connection() as db:
            user = self.current_user(db)
            require_account_operation(user)
            feedback_id = str(uuid.uuid4())
            timestamp = now_ms()
            title = required_text(data, "title", 80)
            category = normalize_feedback_category(data.get("category"))
            content = required_text(data, "content", 2000)
            db.execute(
                """INSERT INTO feedback(
                       id,user_id,category,title,content,app_version,
                       status,workflow_status,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,'OPEN','OPEN',?,?)""",
                (
                    feedback_id,
                    user["id"],
                    category,
                    title,
                    content,
                    str(data.get("appVersion", ""))[:40],
                    timestamp,
                    timestamp,
                ),
            )
            db.execute(
                """INSERT INTO feedback_events(
                       id,feedback_id,actor_user_id,actor_role,action,
                       old_status,new_status,public_visible,public_message,created_at
                   ) VALUES(?,?,?,'USER','SUBMITTED',NULL,'OPEN',1,'',?)""",
                (str(uuid.uuid4()), feedback_id, user["id"], timestamp),
            )
            write_user_event(db, user["id"], "FEEDBACK", "FEEDBACK_SUBMITTED", "SUCCESS", "FEEDBACK", feedback_id)
            return {"id": feedback_id, "status": "OPEN", "workflowStatus": "OPEN"}

    def serialize_feedback_for_user(
        self, db: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        events = [
            {
                "action": event["action"],
                "oldStatus": event["old_status"],
                "newStatus": event["new_status"],
                "message": event["public_message"],
                "createdAt": event["created_at"],
            }
            for event in db.execute(
                """SELECT action,old_status,new_status,public_message,created_at
                   FROM feedback_events
                   WHERE feedback_id=? AND public_visible=1
                   ORDER BY created_at ASC""",
                (row["id"],),
            )
        ]
        return {
            "id": row["id"],
            "category": row["category"],
            "title": row["title"],
            "content": row["content"],
            "status": row["workflow_status"],
            "publicReply": row["public_reply"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "events": events,
        }

    def feedback_list(self) -> list[dict[str, Any]]:
        with connection() as db:
            user = self.current_user(db)
            rows = db.execute(
                """SELECT * FROM feedback WHERE user_id=?
                   ORDER BY created_at DESC LIMIT 200""",
                (user["id"],),
            ).fetchall()
            return [self.serialize_feedback_for_user(db, row) for row in rows]

    def feedback_get(self, feedback_id: str) -> dict[str, Any]:
        with connection() as db:
            user = self.current_user(db)
            row = db.execute(
                "SELECT * FROM feedback WHERE id=? AND user_id=?",
                (feedback_id, user["id"]),
            ).fetchone()
            if not row:
                raise ApiError(404, "FEEDBACK_NOT_FOUND", "反馈不存在")
            return self.serialize_feedback_for_user(db, row)

    def admin_route(
        self, method: str, path: str, query: dict[str, str]
    ) -> tuple[Any, int]:
        with connection() as db:
            admin = self.current_user(db, "ADMIN")
            try:
                require_admin_path_access(admin, method, path)
            except ApiError:
                write_audit(
                    db,
                    admin["id"],
                    "ADMIN_ACCESS_DENIED",
                    "ADMIN_ROUTE",
                    path,
                    {"method": method, "role": admin_role(admin)},
                )
                raise

            if method == "GET" and path.startswith("/api/v1/admin/overview/"):
                today = date.today().isoformat()
                if path.endswith("/sports"):
                    return {
                        "workspace": "SPORTS_OPERATOR",
                        "todayReservations": db.execute("SELECT COUNT(*) count FROM reservations WHERE booking_date=?", (today,)).fetchone()["count"],
                        "activeReservations": db.execute("SELECT COUNT(*) count FROM reservations WHERE status='ACTIVE'").fetchone()["count"],
                        "pendingVerification": db.execute("SELECT COUNT(*) count FROM reservations WHERE status='ACTIVE' AND verification_status='NOT_REQUIRED'").fetchone()["count"],
                        "cancelledToday": db.execute("SELECT COUNT(*) count FROM reservations WHERE status='CANCELLED' AND booking_date=?", (today,)).fetchone()["count"],
                        "venues": db.execute("SELECT COUNT(*) count FROM venues").fetchone()["count"],
                    }, 200
                if path.endswith("/lost-found"):
                    return {
                        "workspace": "LOST_FOUND_OPERATOR",
                        "openItems": db.execute("SELECT COUNT(*) count FROM lost_found_posts WHERE status IN ('SEARCHING','WAITING','CLAIMED')").fetchone()["count"],
                        "openReports": db.execute("SELECT COUNT(*) count FROM lost_found_reports WHERE status='OPEN'").fetchone()["count"],
                    }, 200
                if path.endswith("/library"):
                    return {
                        "workspace": "LIBRARY_OPERATOR",
                        "publishedResources": db.execute("SELECT COUNT(*) count FROM resource_items WHERE publish_status='PUBLISHED'").fetchone()["count"],
                        "openComplaints": db.execute("SELECT COUNT(*) count FROM resource_complaints WHERE status IN ('OPEN','PROCESSING')").fetchone()["count"],
                    }, 200
                if path.endswith("/content"):
                    return {
                        "workspace": "CONTENT_OPERATOR",
                        "publishedAnnouncements": db.execute("SELECT COUNT(*) count FROM announcements WHERE enabled=1").fetchone()["count"],
                    }, 200

            if method == "GET" and path == "/api/v1/admin/admins":
                return [
                    {
                        "id": row["id"], "username": row["username"],
                        "nickname": row["nickname"], "adminRole": admin_role(row),
                        "enabled": bool(row["admin_enabled"]), "createdAt": row["created_at"],
                    }
                    for row in db.execute("SELECT * FROM users WHERE role='ADMIN' ORDER BY created_at")
                ], 200

            if method == "GET" and path == "/api/v1/admin/canonical-users":
                return canonical_identity_listing(db, query), 200

            if method == "GET" and path == "/api/v1/admin/devices":
                return device_listing(db, query), 200

            if method == "GET" and path == "/api/v1/admin/installations":
                return installation_listing(db, query), 200

            if method == "POST" and path == "/api/v1/admin/admins":
                data = self.body_json()
                username = required_text(data, "username", 48)
                nickname = required_text(data, "nickname", 40)
                password = required_text(data, "password", 200)
                role_name = str(data.get("adminRole", "")).upper()
                if role_name not in ADMIN_ROLES - {"SUPER_ADMIN"}:
                    raise ApiError(400, "INVALID_ADMIN_ROLE", "运营管理员角色无效")
                if len(password) < 12:
                    raise ApiError(400, "WEAK_PASSWORD", "管理员密码至少 12 位")
                digest, salt = password_record(password)
                admin_id = str(uuid.uuid4())
                created_at = now_ms()
                try:
                    db.execute(
                        """INSERT INTO users(id,username,user_code,password_hash,password_salt,role,
                           account_type,nickname,admin_permissions_json,admin_role,admin_enabled,
                           account_status,created_at)
                           VALUES(?,?,?,?,?,'ADMIN','ADMIN',?,'[]',?,1,'ACTIVE',?)""",
                        (
                            admin_id,
                            username,
                            allocate_user_code(db, created_at),
                            digest,
                            salt,
                            nickname,
                            role_name,
                            created_at,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ApiError(409, "ADMIN_EXISTS", "管理员用户名已存在") from exc
                write_audit(db, admin["id"], "ADMIN_CREATED", "ADMIN", admin_id, {"adminRole": role_name})
                return {"id": admin_id, "username": username, "nickname": nickname, "adminRole": role_name, "enabled": True}, 201

            match_admin = re.fullmatch(r"/api/v1/admin/admins/([^/]+)", path)
            if method == "PATCH" and match_admin:
                data = self.body_json()
                target = db.execute("SELECT * FROM users WHERE id=? AND role='ADMIN'", (match_admin.group(1),)).fetchone()
                if not target:
                    raise ApiError(404, "ADMIN_NOT_FOUND", "管理员不存在")
                if target["id"] == admin["id"]:
                    raise ApiError(409, "CANNOT_EDIT_SELF", "不能编辑当前登录管理员本人")
                role_name = str(data.get("adminRole", admin_role(target))).upper()
                if role_name not in ADMIN_ROLES:
                    raise ApiError(400, "INVALID_ADMIN_ROLE", "管理员角色无效")
                enabled = 1 if bool(data.get("enabled", bool(target["admin_enabled"]))) else 0
                nickname = str(data.get("nickname", target["nickname"] or "")).strip()[:40] or target["nickname"]
                target_role = admin_role(target)
                target_enabled = bool(target["admin_enabled"])
                demoting_or_disabling_super = (
                    target_role == "SUPER_ADMIN"
                    and target_enabled
                    and (role_name != "SUPER_ADMIN" or not enabled)
                )
                if demoting_or_disabling_super:
                    other_active_super = db.execute(
                        """SELECT COUNT(*) AS count FROM users
                           WHERE role='ADMIN' AND admin_role='SUPER_ADMIN'
                           AND admin_enabled=1 AND id!=?""",
                        (target["id"],),
                    ).fetchone()["count"]
                    if other_active_super == 0:
                        raise ApiError(
                            409,
                            "LAST_SUPER_ADMIN_PROTECTED",
                            "系统必须至少保留一个启用的超级管理员，无法停用或降级最后一个超级管理员",
                        )
                db.execute(
                    "UPDATE users SET nickname=?,admin_role=?,admin_enabled=? WHERE id=?",
                    (nickname, role_name, enabled, target["id"]),
                )
                if not enabled:
                    db.execute("DELETE FROM sessions WHERE user_id=?", (target["id"],))
                write_audit(
                    db,
                    admin["id"],
                    "ADMIN_ROLE_UPDATED",
                    "ADMIN",
                    target["id"],
                    {
                        "oldRole": target_role,
                        "newRole": role_name,
                        "enabled": bool(enabled),
                        "nicknameChanged": nickname != target["nickname"],
                    },
                )
                return {
                    "id": target["id"],
                    "nickname": nickname,
                    "adminRole": role_name,
                    "enabled": bool(enabled),
                }, 200

            match_reset = re.fullmatch(r"/api/v1/admin/admins/([^/]+)/reset-password", path)
            if method == "POST" and match_reset:
                password = required_text(self.body_json(), "password", 200)
                if len(password) < 12:
                    raise ApiError(400, "WEAK_PASSWORD", "管理员密码至少 12 位")
                target = db.execute("SELECT id FROM users WHERE id=? AND role='ADMIN'", (match_reset.group(1),)).fetchone()
                if not target:
                    raise ApiError(404, "ADMIN_NOT_FOUND", "管理员不存在")
                digest, salt = password_record(password)
                db.execute("UPDATE users SET password_hash=?,password_salt=? WHERE id=?", (digest, salt, target["id"]))
                db.execute("DELETE FROM sessions WHERE user_id=?", (target["id"],))
                write_audit(db, admin["id"], "ADMIN_PASSWORD_RESET", "ADMIN", target["id"], {"sessionsRevoked": True})
                return {"id": target["id"], "reset": True}, 200

            if method == "GET" and path == "/api/v1/admin/users":
                db.execute(
                    """UPDATE users SET account_status='ACTIVE',account_status_reason='',
                       account_status_changed_at=?,account_status_expires_at=NULL
                       WHERE role='USER' AND account_status='LIMITED'
                         AND account_status_expires_at IS NOT NULL
                         AND account_status_expires_at<=?""",
                    (now_ms(), now_ms()),
                )
                clauses = ["u.role='USER'"]
                values: list[Any] = []
                append_user_search(clauses, values, query.get("q"))
                requested_status = query.get("status", "").upper()
                if requested_status in ACCOUNT_STATUSES | {"SUSPENDED"}:
                    clauses.append("u.account_status=?")
                    values.append(requested_status)
                elif query.get("includeDeleted", "").lower() != "true":
                    clauses.append("u.account_status!='DELETED'")
                if query.get("accountType", "").upper() in {"PHONE", "DEVICE"}:
                    clauses.append("u.account_type=?")
                    values.append(query["accountType"].upper())
                active_since = str(query.get("activeSince", "")).strip()
                if active_since.isdigit():
                    clauses.append("u.last_active_at>=?")
                    values.append(int(active_since))
                created_since = str(query.get("createdSince", "")).strip()
                if created_since.isdigit():
                    clauses.append("u.created_at>=?")
                    values.append(int(created_since))
                if query.get("createdToday", "").lower() == "true":
                    created_day_start, created_day_end = business_day_bounds_ms()
                    clauses.extend(["u.created_at>=?", "u.created_at<?"])
                    values.extend([created_day_start, created_day_end])
                changed_since = str(query.get("statusChangedSince", "")).strip()
                if changed_since.isdigit():
                    clauses.append("u.account_status_changed_at>=?")
                    values.append(int(changed_since))
                if query.get("anomaly", "").lower() == "true":
                    clauses.append("EXISTS (SELECT 1 FROM risk_candidates rc WHERE rc.user_id=u.id AND rc.status IN ('OPEN','WATCHING'))")
                where_sql = " AND ".join(clauses)
                select_sql = """SELECT u.id,u.user_code,u.account_type,u.nickname,u.phone_masked,u.phone_encrypted,
                                  u.reservation_student_id,u.reservation_name,
                                  u.reservation_profile_consented_at,
                                  u.account_status,u.account_status_reason,
                                  u.account_status_changed_at,u.account_status_expires_at,
                                  u.last_active_at,u.first_login_at,u.created_at,
                                  (SELECT COUNT(*) FROM reservations r
                                   WHERE r.user_id=u.id) AS reservation_count,
                                  (SELECT COUNT(*) FROM reservation_events e
                                   WHERE e.user_id=u.id AND e.action='USER_CANCELLED') AS cancellation_count,
                                  (SELECT COUNT(*) FROM lost_found_posts p WHERE p.owner_user_id=u.id) AS lost_found_count,
                                  (SELECT COUNT(*) FROM lost_found_reports lr WHERE lr.user_id=u.id) AS lost_report_count,
                                  (SELECT COUNT(*) FROM lost_found_claims lc WHERE lc.user_id=u.id) AS lost_claim_count,
                                  (SELECT COALESCE(SUM(rv.view_count),0) FROM resource_views rv WHERE rv.user_id=u.id) AS resource_view_count,
                                  (SELECT COUNT(*) FROM resource_favorites rf WHERE rf.user_id=u.id) AS resource_favorite_count,
                                  (SELECT COUNT(*) FROM resource_complaints rc WHERE rc.user_id=u.id) AS resource_complaint_count,
                                  (SELECT COUNT(*) FROM feedback f WHERE f.user_id=u.id) AS feedback_count,
                                  ur.reservation_blocked_until,ur.cancellation_blocked_until,
                                  ur.reason AS restriction_reason
                           FROM users u
                           LEFT JOIN user_restrictions ur ON ur.user_id=u.id
                           WHERE """ + where_sql
                paged = query.get("paged", "").lower() == "true"
                limit, offset = parse_pagination(
                    query, default_limit=100 if paged else 500, max_limit=500
                )
                rows = [
                    full_admin_user_row(row)
                    for row in db.execute(
                        select_sql + " ORDER BY u.created_at DESC LIMIT ? OFFSET ?",
                        [*values, limit, offset],
                    )
                ]
                if not paged:
                    return rows, 200
                total = int(
                    db.execute(
                        "SELECT COUNT(*) AS count FROM users u WHERE " + where_sql,
                        values,
                    ).fetchone()["count"]
                )
                return paginated_response(rows, total, limit, offset), 200

            match = re.fullmatch(r"/api/v1/admin/users/([^/]+)/details", path)
            if method == "GET" and match:
                user = db.execute(
                    """SELECT id,user_code,account_type,nickname,phone_masked,phone_encrypted,
                              reservation_student_id,reservation_name,
                              reservation_profile_consented_at,
                              reservation_profile_version,
                              reservation_profile_updated_at,
                              account_status,account_status_reason,
                              account_status_changed_at,account_status_expires_at,
                              last_active_at,first_login_at,created_at
                       FROM users WHERE id=? AND role='USER'""",
                    (match.group(1),),
                ).fetchone()
                if not user:
                    raise ApiError(404, "USER_NOT_FOUND", "用户不存在")
                return {
                    "user": full_admin_user_row(user),
                    "reservations": [
                        mask_admin_reservation_row(row)
                        for row in db.execute(
                            """SELECT r.*,v.name AS venue_name FROM reservations r
                               JOIN venues v ON v.id=r.venue_id
                               WHERE r.user_id=? ORDER BY r.created_at DESC LIMIT 100""",
                            (user["id"],),
                        )
                    ],
                    "lostFound": [
                        row_dict(row)
                        for row in db.execute(
                            """SELECT * FROM lost_found_posts WHERE owner_user_id=?
                               ORDER BY updated_at DESC LIMIT 100""",
                            (user["id"],),
                        )
                    ],
                    "lostReports": [row_dict(row) for row in db.execute(
                        "SELECT * FROM lost_found_reports WHERE user_id=? ORDER BY created_at DESC LIMIT 100", (user["id"],)
                    )],
                    "lostClaims": [row_dict(row) for row in db.execute(
                        "SELECT * FROM lost_found_claims WHERE user_id=? ORDER BY created_at DESC LIMIT 100", (user["id"],)
                    )],
                    "feedback": [
                        row_dict(row)
                        for row in db.execute(
                            """SELECT * FROM feedback WHERE user_id=?
                               ORDER BY created_at DESC LIMIT 100""",
                            (user["id"],),
                        )
                    ],
                    "resourceActivity": {
                        "favorites": db.execute(
                            "SELECT COUNT(*) AS count FROM resource_favorites WHERE user_id=?",
                            (user["id"],),
                        ).fetchone()["count"],
                        "views": db.execute(
                            "SELECT COUNT(*) AS count FROM resource_views WHERE user_id=?",
                            (user["id"],),
                        ).fetchone()["count"],
                        "complaints": db.execute(
                            "SELECT COUNT(*) AS count FROM resource_complaints WHERE user_id=?",
                            (user["id"],),
                        ).fetchone()["count"],
                        "unlocks": db.execute(
                            "SELECT COUNT(*) AS count FROM resource_unlocks WHERE user_id=?",
                            (user["id"],),
                        ).fetchone()["count"],
                    },
                    "resourceViews": [row_dict(row) for row in db.execute(
                        """SELECT rv.*,ri.title FROM resource_views rv
                           JOIN resource_items ri ON ri.id=rv.resource_id
                           WHERE rv.user_id=? ORDER BY rv.last_viewed_at DESC LIMIT 100""", (user["id"],)
                    )],
                    "resourceFavorites": [row_dict(row) for row in db.execute(
                        """SELECT rf.*,ri.title FROM resource_favorites rf
                           JOIN resource_items ri ON ri.id=rf.resource_id
                           WHERE rf.user_id=? ORDER BY rf.created_at DESC LIMIT 100""", (user["id"],)
                    )],
                    "resourceComplaints": [row_dict(row) for row in db.execute(
                        "SELECT * FROM resource_complaints WHERE user_id=? ORDER BY created_at DESC LIMIT 100", (user["id"],)
                    )],
                    "behaviorEvents": [row_dict(row) for row in db.execute(
                        """SELECT id,module,event_type,result,target_type,target_id,
                           client_source,client_version,risk_flag,created_at
                           FROM user_behavior_events WHERE user_id=?
                           ORDER BY created_at DESC LIMIT 100""", (user["id"],)
                    )],
                    "devices": [row_dict(row) for row in db.execute(
                        """SELECT d.id,d.first_seen_at,d.last_seen_at,
                                  d.first_app_version_name,d.first_app_version_code,
                                  d.last_app_version_name,d.last_app_version_code,
                                  d.install_count,d.created_at,d.updated_at
                           FROM devices d JOIN user_devices ud ON ud.device_id=d.id
                          WHERE ud.user_id=? ORDER BY d.last_seen_at DESC""",
                        (user["id"],),
                    )],
                    "installations": [row_dict(row) for row in db.execute(
                        """SELECT i.installation_id,i.device_id,i.first_seen_at,
                                  i.last_seen_at,i.last_app_version_name AS app_version_name,
                                  i.last_app_version_code AS app_version_code,
                                  i.legacy,i.created_at,i.updated_at
                           FROM installations i JOIN user_devices ud ON ud.device_id=i.device_id
                          WHERE ud.user_id=? ORDER BY i.last_seen_at DESC LIMIT 100""",
                        (user["id"],),
                    )],
                    "restriction": row_dict(active_restriction(db, user["id"])),
                }, 200

            match = re.fullmatch(r"/api/v1/admin/users/([^/]+)/phone", path)
            if method == "POST" and match:
                require_admin_permission(admin, "PII_VIEW")
                data = self.body_json()
                reason = required_text(data, "reason", 200)
                if len(reason) < 4:
                    raise ApiError(400, "REASON_REQUIRED", "请填写具体查看原因")
                user = db.execute(
                    """SELECT id,phone_encrypted FROM users
                       WHERE id=? AND role='USER'""",
                    (match.group(1),),
                ).fetchone()
                if not user:
                    raise ApiError(404, "USER_NOT_FOUND", "用户不存在")
                phone = decrypt_phone(user["phone_encrypted"])
                write_audit(
                    db,
                    admin["id"],
                    "USER_PHONE_REVEALED",
                    "USER",
                    user["id"],
                    {"reason": reason, "result": "AVAILABLE" if phone else "UNAVAILABLE"},
                )
                if not phone:
                    raise ApiError(
                        409,
                        "PHONE_REVEAL_UNAVAILABLE",
                        "该历史账号尚无可解密号码，请用户重新登录后再试",
                    )
                return {"userId": user["id"], "phone": phone, "expiresInSeconds": 60}, 200

            match = re.fullmatch(
                r"/api/v1/admin/users/([^/]+)/reservation-profile", path
            )
            if method == "POST" and match:
                require_admin_permission(admin, "PII_VIEW")
                data = self.body_json()
                reason = required_text(data, "reason", 200)
                if len(reason) < 4:
                    raise ApiError(400, "REASON_REQUIRED", "请填写具体查看原因")
                user = db.execute(
                    """SELECT id,reservation_student_id,reservation_name
                       FROM users WHERE id=? AND role='USER'""",
                    (match.group(1),),
                ).fetchone()
                if not user:
                    raise ApiError(404, "USER_NOT_FOUND", "用户不存在")
                available = bool(
                    user["reservation_student_id"] and user["reservation_name"]
                )
                write_audit(
                    db,
                    admin["id"],
                    "RESERVATION_PROFILE_REVEALED",
                    "USER",
                    user["id"],
                    {"reason": reason, "result": "AVAILABLE" if available else "UNAVAILABLE"},
                )
                if not available:
                    raise ApiError(409, "PROFILE_REVEAL_UNAVAILABLE", "该用户尚未完整填写预约资料")
                return {
                    "userId": user["id"],
                    "studentId": user["reservation_student_id"],
                    "name": user["reservation_name"],
                    "expiresInSeconds": 60,
                }, 200

            match = re.fullmatch(
                r"/api/v1/admin/reservations/([^/]+)/profile", path
            )
            if method == "POST" and match:
                if admin_role(admin) == "SPORTS_OPERATOR":
                    require_admin_permission(admin, "SPORTS_PII_VIEW")
                else:
                    require_admin_permission(admin, "PII_VIEW")
                data = self.body_json()
                reason = str(data.get("reason", "")).strip()[:200]
                reservation = db.execute(
                    """SELECT r.id,r.user_id,r.reservation_student_id_snapshot,
                              r.reservation_name_snapshot,r.verification_status,
                              u.phone_encrypted,u.nickname
                       FROM reservations r JOIN users u ON u.id=r.user_id
                       WHERE r.id=?""",
                    (match.group(1),),
                ).fetchone()
                if not reservation:
                    raise ApiError(404, "RESERVATION_NOT_FOUND", "预约不存在")
                available = bool(
                    reservation["reservation_student_id_snapshot"]
                    and reservation["reservation_name_snapshot"]
                )
                write_audit(
                    db,
                    admin["id"],
                    "RESERVATION_SNAPSHOT_REVEALED",
                    "RESERVATION",
                    reservation["id"],
                    {"reason": reason, "userId": reservation["user_id"],
                     "result": "AVAILABLE" if available else "UNAVAILABLE"},
                )
                if not available:
                    raise ApiError(409, "PROFILE_REVEAL_UNAVAILABLE", "该预约没有完整资料快照")
                return {
                    "reservationId": reservation["id"],
                    "studentId": reservation["reservation_student_id_snapshot"],
                    "name": reservation["reservation_name_snapshot"],
                    "phone": decrypt_phone(reservation["phone_encrypted"]),
                    "nickname": reservation["nickname"],
                    "verificationStatus": reservation["verification_status"],
                    "expiresInSeconds": 60,
                }, 200

            if method == "POST" and path == "/api/v1/admin/users/purge":
                require_admin_permission(admin, "USER_PURGE")
                if not is_super_admin(admin):
                    raise ApiError(403, "SUPER_ADMIN_REQUIRED", "只有超级管理员可以永久清理账号")
                data = self.body_json()
                raw_ids = data.get("ids")
                if not isinstance(raw_ids, list):
                    raise ApiError(400, "VALIDATION_ERROR", "ids 必须是数组")
                user_ids = list(dict.fromkeys(str(value) for value in raw_ids))
                if not user_ids or len(user_ids) > 100 or any(
                    not re.fullmatch(r"[A-Za-z0-9-]{6,80}", value)
                    for value in user_ids
                ):
                    raise ApiError(400, "VALIDATION_ERROR", "永久清理对象无效或超过 100 个")
                confirmation = str(data.get("confirmation", "")).strip()
                expected_confirmation = f"PURGE {len(user_ids)} DELETED USERS"
                if confirmation != expected_confirmation:
                    raise ApiError(400, "CONFIRMATION_REQUIRED", "永久清理确认文本不匹配")
                placeholders = ",".join("?" for _ in user_ids)
                targets = db.execute(
                    f"""SELECT * FROM users WHERE role='USER' AND account_status='DELETED'
                        AND id IN ({placeholders})""",
                    user_ids,
                ).fetchall()
                if len(targets) != len(user_ids):
                    raise ApiError(
                        409,
                        "PURGE_TARGET_CHANGED",
                        "只能永久清理仍处于已注销状态的普通用户，请刷新后重试",
                    )
                operation_id = str(uuid.uuid4())
                db.execute("BEGIN IMMEDIATE")
                try:
                    results = [purge_deleted_user(db, target) for target in targets]
                    violations = db.execute("PRAGMA foreign_key_check").fetchall()
                    if violations:
                        raise ApiError(409, "PURGE_FOREIGN_KEY_BLOCKED", "账号关联清理后仍存在外键关联")
                    write_audit(
                        db,
                        admin["id"],
                        "USER_PURGE_COMPLETED",
                        "USER_PURGE",
                        operation_id,
                        {
                            "operationId": operation_id,
                            "count": len(results),
                            "userCodes": [item["userCode"] for item in results],
                            "result": "SUCCESS",
                        },
                    )
                    db.commit()
                except Exception:
                    db.rollback()
                    raise
                return {
                    "operationId": operation_id,
                    "purged": len(results),
                    "userCodes": [item["userCode"] for item in results],
                    "results": results,
                }, 200

            if method == "POST" and path == "/api/v1/admin/users/bulk-status":
                require_admin_permission(admin, "ACCOUNT_MANAGE")
                data = self.body_json()
                raw_ids = data.get("ids")
                if not isinstance(raw_ids, list):
                    raise ApiError(400, "VALIDATION_ERROR", "ids 必须是数组")
                user_ids = list(dict.fromkeys(str(value) for value in raw_ids))
                if not user_ids or len(user_ids) > 100 or any(
                    not re.fullmatch(r"[A-Za-z0-9-]{6,80}", value)
                    for value in user_ids
                ):
                    raise ApiError(400, "VALIDATION_ERROR", "批量用户对象无效或超过 100 个")
                account_status = str(data.get("accountStatus", "")).upper()
                if account_status not in {"ACTIVE", "LIMITED", "DELETED"}:
                    raise ApiError(400, "VALIDATION_ERROR", "批量账号状态无效")
                reason = required_text(data, "reason", 300)
                confirmation = str(data.get("confirmation", "")).strip()
                expected_confirmation = (
                    f"DELETE {len(user_ids)} USERS"
                    if account_status == "DELETED"
                    else f"UPDATE {len(user_ids)} USERS"
                )
                if confirmation != expected_confirmation:
                    raise ApiError(400, "CONFIRMATION_REQUIRED", "批量账号确认文本不匹配")
                placeholders = ",".join("?" for _ in user_ids)
                users = db.execute(
                    f"SELECT id FROM users WHERE role='USER' AND id IN ({placeholders})",
                    user_ids,
                ).fetchall()
                if len(users) != len(user_ids):
                    raise ApiError(
                        409,
                        "BULK_TARGET_CHANGED",
                        "部分用户不存在或不是普通用户，请刷新后重试",
                    )
                duration = str(data.get("suspensionDuration", "PERMANENT")).upper()
                expires_at = None
                if account_status == "LIMITED":
                    if duration == "7_DAYS":
                        expires_at = now_ms() + 7 * 24 * 60 * 60 * 1000
                    elif duration == "30_DAYS":
                        expires_at = now_ms() + 30 * 24 * 60 * 60 * 1000
                    elif duration != "PERMANENT":
                        raise ApiError(400, "VALIDATION_ERROR", "暂停时长无效")
                if account_status == "DELETED":
                    for user_id in user_ids:
                        target = db.execute(
                            "SELECT * FROM users WHERE id=? AND role='USER'",
                            (user_id,),
                        ).fetchone()
                        delete_user_account_data(db, target, reason=reason)
                else:
                    changed_at = now_ms()
                    db.execute(
                        f"""UPDATE users SET account_status=?,account_status_reason=?,
                            account_status_changed_at=?,account_status_expires_at=?
                            WHERE role='USER' AND id IN ({placeholders})""",
                        [account_status, "" if account_status == "ACTIVE" else reason,
                         changed_at, expires_at, *user_ids],
                    )
                    for user_id in user_ids:
                        write_user_event(
                            db,
                            user_id,
                            "ACCOUNT",
                            "ACCOUNT_STATUS_CHANGED",
                            "SUCCESS",
                            "USER",
                            user_id,
                        )
                write_audit(
                    db,
                    admin["id"],
                    "USER_STATUS_BULK_UPDATED",
                    "USER",
                    None,
                    {
                        "count": len(user_ids),
                        "accountStatus": account_status,
                        "reason": reason[:120],
                        "suspensionDuration": duration
                        if account_status == "LIMITED"
                        else None,
                    },
                )
                return {
                    "updated": len(user_ids),
                    "accountStatus": account_status,
                    "expiresAt": expires_at,
                }, 200

            match = re.fullmatch(r"/api/v1/admin/users/([^/]+)", path)
            if method == "DELETE" and match:
                require_admin_permission(admin, "ACCOUNT_MANAGE")
                data = self.body_json()
                user = db.execute(
                    "SELECT * FROM users WHERE id=? AND role='USER'", (match.group(1),)
                ).fetchone()
                if not user:
                    raise ApiError(404, "USER_NOT_FOUND", "用户不存在或账号受保护")
                reason = required_text(data, "reason", 300)
                confirmation = str(data.get("confirmation", "")).strip()
                expected_confirmation = f"DELETE USER {user['id']}"
                if confirmation != expected_confirmation:
                    raise ApiError(400, "CONFIRMATION_REQUIRED", "删除账号确认文本不匹配")
                result = delete_user_account_data(db, user, reason=reason)
                write_audit(
                    db,
                    admin["id"],
                    "USER_ACCOUNT_ADMIN_DELETED",
                    "USER",
                    user["id"],
                    {"reason": reason[:120]},
                )
                return result, 200

            if method == "PATCH" and match:
                require_admin_permission(admin, "ACCOUNT_MANAGE")
                data = self.body_json()
                user = db.execute(
                    "SELECT * FROM users WHERE id=? AND role='USER'", (match.group(1),)
                ).fetchone()
                if not user:
                    raise ApiError(404, "USER_NOT_FOUND", "用户不存在")
                if user["account_status"] == "DELETED":
                    raise ApiError(
                        409,
                        "ACCOUNT_DELETION_FINAL",
                        "已注销账号的个人数据已清理，不能恢复；用户可使用原手机号重新注册",
                    )
                account_status = str(
                    data.get("accountStatus", user["account_status"])
                ).upper()
                if account_status not in ACCOUNT_STATUSES:
                    raise ApiError(400, "VALIDATION_ERROR", "账号状态无效")
                if account_status == "DELETED":
                    raise ApiError(
                        409,
                        "USE_ACCOUNT_DELETE_API",
                        "注销账号必须使用受保护的账号删除操作",
                    )
                if account_status != user["account_status"]:
                    reason = required_text(data, "reason", 300)
                else:
                    reason = str(data.get("reason", user["account_status_reason"] or "")).strip()[:300]
                expires_at = data.get("expiresAt")
                if expires_at is not None:
                    try:
                        expires_at = int(expires_at)
                    except (TypeError, ValueError) as exc:
                        raise ApiError(400, "VALIDATION_ERROR", "状态到期时间无效") from exc
                duration = str(data.get("suspensionDuration", "")).upper()
                if account_status == "LIMITED" and duration:
                    if duration == "7_DAYS":
                        expires_at = now_ms() + 7 * 24 * 60 * 60 * 1000
                    elif duration == "30_DAYS":
                        expires_at = now_ms() + 30 * 24 * 60 * 60 * 1000
                    elif duration == "PERMANENT":
                        expires_at = None
                    else:
                        raise ApiError(400, "VALIDATION_ERROR", "暂停时长无效")
                elif account_status != "LIMITED":
                    expires_at = None
                if expires_at is not None and expires_at <= now_ms():
                    raise ApiError(400, "VALIDATION_ERROR", "状态到期时间必须晚于当前时间")
                nickname = str(data.get("nickname", user["nickname"] or "川农同学")).strip()[:40]
                db.execute(
                    """UPDATE users SET account_status=?,nickname=?,account_status_reason=?,
                       account_status_changed_at=?,account_status_expires_at=? WHERE id=?""",
                    (
                        account_status,
                        nickname or "川农同学",
                        reason,
                        now_ms() if account_status != user["account_status"] else user["account_status_changed_at"],
                        expires_at,
                        user["id"],
                    ),
                )
                if account_status in {"DISABLED", "PENDING_DELETION", "DELETED"}:
                    db.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
                write_audit(
                    db,
                    admin["id"],
                    "USER_STATUS_UPDATED" if account_status != user["account_status"] else "USER_UPDATED",
                    "USER",
                    user["id"],
                    {
                        "oldStatus": user["account_status"],
                        "accountStatus": account_status,
                        "reason": reason[:120],
                        "suspensionDuration": duration or None,
                        "expiresAt": expires_at,
                    },
                )
                write_user_event(db, user["id"], "ACCOUNT", "ACCOUNT_STATUS_CHANGED", "SUCCESS", "USER", user["id"])
                return mask_admin_user_row(
                    db.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
                ), 200

            match = re.fullmatch(r"/api/v1/admin/users/([^/]+)/clear-restrictions", path)
            if method == "POST" and match:
                user = db.execute(
                    "SELECT id FROM users WHERE id=? AND role='USER'", (match.group(1),)
                ).fetchone()
                if not user:
                    raise ApiError(404, "USER_NOT_FOUND", "用户不存在")
                db.execute("DELETE FROM user_restrictions WHERE user_id=?", (user["id"],))
                write_audit(
                    db, admin["id"], "USER_RESTRICTION_CLEARED", "USER", user["id"]
                )
                return {"id": user["id"], "restricted": False}, 200

            if method == "GET" and path == "/api/v1/admin/reservation-rules":
                return row_dict(reservation_rules(db)), 200

            if method == "PUT" and path == "/api/v1/admin/reservation-rules":
                data = self.body_json()
                previous = row_dict(reservation_rules(db))
                profile_mode = str(
                    data.get(
                        "reservationProfileMode",
                        "REQUIRED"
                        if bool(data.get("requireReservationStudentId"))
                        else "OPTIONAL",
                    )
                ).upper()
                if profile_mode not in RESERVATION_PROFILE_MODES:
                    raise ApiError(400, "VALIDATION_ERROR", "预约资料规则无效")
                values = {
                    "max_active_per_day": int(data.get("maxActivePerDay", 0)),
                    "max_user_cancellations_per_day": int(
                        data.get("maxUserCancellationsPerDay", 0)
                    ),
                    "cancel_cutoff_minutes": int(data.get("cancelCutoffMinutes", -1)),
                    "restriction_hours_after_excess_cancel": int(
                        data.get("restrictionHoursAfterExcessCancel", 0)
                    ),
                    "require_reservation_student_id": 1
                    if profile_mode == "REQUIRED"
                    else 0,
                    "reservation_profile_mode": profile_mode,
                }
                reason = str(data.get("reason", "")).strip()[:200] or "常规规则调整"
                if (
                    values["max_active_per_day"] not in range(1, 21)
                    or values["max_user_cancellations_per_day"] not in range(1, 21)
                    or values["cancel_cutoff_minutes"] not in range(0, 1441)
                    or values["restriction_hours_after_excess_cancel"] not in range(1, 721)
                ):
                    raise ApiError(400, "VALIDATION_ERROR", "预约规则参数无效")
                db.execute(
                    """UPDATE reservation_rules SET max_active_per_day=?,
                       max_user_cancellations_per_day=?,cancel_cutoff_minutes=?,
                       restriction_hours_after_excess_cancel=?,
                       require_reservation_student_id=?,reservation_profile_mode=?,
                       updated_at=?
                       WHERE id='default'""",
                    (
                        values["max_active_per_day"],
                        values["max_user_cancellations_per_day"],
                        values["cancel_cutoff_minutes"],
                        values["restriction_hours_after_excess_cancel"],
                        values["require_reservation_student_id"],
                        values["reservation_profile_mode"],
                        now_ms(),
                    ),
                )
                write_audit(
                    db,
                    admin["id"],
                    "RESERVATION_RULES_UPDATED",
                    "RESERVATION_RULES",
                    "default",
                    {
                        "old": previous,
                        "new": values,
                        "reason": reason,
                    },
                )
                return row_dict(reservation_rules(db)), 200

            if method == "GET" and path == "/api/v1/admin/audit-logs":
                require_admin_permission(admin, "ADMIN_AUDIT_VIEW")
                limit, _ = parse_pagination(query, default_limit=100, max_limit=200)
                return [
                    row_dict(row)
                    for row in db.execute(
                        """SELECT a.*,u.nickname AS actor_name,
                                  u.admin_role AS actor_role,
                                  'ADMIN' AS actor_type
                           FROM admin_audit_logs a
                           JOIN users u ON u.id=a.actor_user_id AND u.role='ADMIN'
                           ORDER BY a.created_at DESC LIMIT ?""",
                        (limit,),
                    )
                ], 200

            if method == "GET" and path == "/api/v1/admin/user-events":
                require_admin_permission(admin, "USER_EVENTS_VIEW")
                clauses = ["u.role='USER'"]
                values: list[Any] = []
                first_login_today = str(query.get("firstLoginToday", "")).lower() == "true"
                append_user_search(clauses, values, query.get("user"), "u")
                for parameter, column, allowed in (
                    ("module", "e.module", None),
                    ("eventType", "e.event_type", None),
                    ("result", "e.result", USER_EVENT_RESULTS),
                    ("risk", "e.risk_flag", None),
                ):
                    raw = str(query.get(parameter, "")).strip().upper()
                    if raw and (allowed is None or raw in allowed):
                        clauses.append(f"{column}=?")
                        values.append(raw)
                for parameter, operator in (("from", ">="), ("to", "<=")):
                    raw = str(query.get(parameter, "")).strip()
                    if raw.isdigit():
                        clauses.append(f"e.created_at{operator}?")
                        values.append(int(raw))
                if first_login_today:
                    day_start, day_end = business_day_bounds_ms()
                    clauses.extend(
                        [
                            "u.account_type='PHONE'",
                            "e.event_type='LOGIN_SUCCESS'",
                            "e.result='SUCCESS'",
                            "e.created_at>=?",
                            "e.created_at<?",
                            """NOT EXISTS (
                                SELECT 1 FROM user_behavior_events first_login
                                WHERE first_login.user_id=e.user_id
                                  AND first_login.event_type='LOGIN_SUCCESS'
                                  AND first_login.result='SUCCESS'
                                  AND first_login.created_at>=?
                                  AND first_login.created_at<?
                                  AND (
                                      first_login.created_at < e.created_at
                                      OR (first_login.created_at=e.created_at AND first_login.id < e.id)
                                  )
                            )""",
                        ]
                    )
                    values.extend([day_start, day_end, day_start, day_end])
                limit, offset = parse_pagination(query, default_limit=50, max_limit=100)
                total = db.execute(
                    """SELECT COUNT(*) AS count FROM user_behavior_events e
                       LEFT JOIN users u ON u.id=e.user_id WHERE """ + " AND ".join(clauses),
                    values,
                ).fetchone()["count"]
                rows = db.execute(
                    """SELECT e.*,u.nickname,u.phone_masked,u.account_status
                       FROM user_behavior_events e
                       LEFT JOIN users u ON u.id=e.user_id WHERE """ + " AND ".join(clauses) +
                    " ORDER BY e.created_at DESC LIMIT ? OFFSET ?",
                    values + [limit, offset],
                ).fetchall()
                return {
                    **paginated_response(
                        [row_dict(row) for row in rows],
                        total,
                        limit,
                        offset,
                    ),
                    "retentionDays": USER_EVENT_RETENTION_DAYS,
                    "businessTimezone": BUSINESS_TIMEZONE_NAME,
                    "firstLoginDefinition": (
                        "Asia/Shanghai 当日内至少一次成功认证的去重账号数；每个账号返回当日最早 LOGIN_SUCCESS"
                        if first_login_today
                        else None
                    ),
                }, 200

            if method == "GET" and path == "/api/v1/admin/risk-candidates":
                require_admin_permission(admin, "ACCOUNT_MANAGE")
                status = str(query.get("status", "")).upper()
                clauses = ["1=1"]
                values: list[Any] = []
                if status in {"OPEN", "WATCHING", "IGNORED", "CLOSED"}:
                    clauses.append("r.status=?")
                    values.append(status)
                return [row_dict(row) for row in db.execute(
                    """SELECT r.*,u.nickname,u.phone_masked,u.account_status
                       FROM risk_candidates r JOIN users u ON u.id=r.user_id
                       WHERE """ + " AND ".join(clauses) + " ORDER BY r.updated_at DESC LIMIT 300",
                    values,
                )], 200

            match = re.fullmatch(r"/api/v1/admin/risk-candidates/([^/]+)", path)
            if method == "PATCH" and match:
                require_admin_permission(admin, "ACCOUNT_MANAGE")
                data = self.body_json()
                candidate = db.execute("SELECT * FROM risk_candidates WHERE id=?", (match.group(1),)).fetchone()
                if not candidate:
                    raise ApiError(404, "RISK_CANDIDATE_NOT_FOUND", "异常候选不存在")
                status = str(data.get("status", candidate["status"])).upper()
                if status not in {"OPEN", "WATCHING", "IGNORED", "CLOSED"}:
                    raise ApiError(400, "VALIDATION_ERROR", "候选状态无效")
                reason = required_text(data, "reason", 300)
                db.execute(
                    "UPDATE risk_candidates SET status=?,admin_note=?,updated_at=? WHERE id=?",
                    (status, reason, now_ms(), candidate["id"]),
                )
                write_audit(db, admin["id"], "RISK_CANDIDATE_REVIEWED", "RISK_CANDIDATE", candidate["id"], {"oldStatus": candidate["status"], "newStatus": status, "reason": reason[:120]})
                return {"id": candidate["id"], "status": status}, 200

            if method == "GET" and path == "/api/v1/admin/overview":
                today = business_today().isoformat()
                day_start, day_end = business_day_bounds_ms()
                now = now_ms()
                def count(sql: str, params: tuple[Any, ...] = ()) -> int:
                    return int(db.execute(sql, params).fetchone()["count"])
                identity_metrics = canonical_identity_snapshot(db, day_start, day_end)
                return {
                    "asOf": now,
                    # New user-level metrics.  These are intentionally kept
                    # separate from the account-governance counters below.
                    **identity_metrics,
                    "todayFirstUseUsers": identity_metrics["todayNewUsers"],
                    # Existing names remain in the response for old admin
                    # assets and integrations; their values now use canonical
                    # identities rather than account rows.
                    "registeredAccounts": count("SELECT COUNT(*) AS count FROM users WHERE role='USER' AND account_type='PHONE'"),
                    "activeAccounts": count("SELECT COUNT(*) AS count FROM users WHERE role='USER' AND account_type='PHONE' AND account_status='ACTIVE'"),
                    "limitedAccounts": count("SELECT COUNT(*) AS count FROM users WHERE role='USER' AND account_type='PHONE' AND account_status IN ('LIMITED','SUSPENDED')"),
                    "disabledAccounts": count("SELECT COUNT(*) AS count FROM users WHERE role='USER' AND account_type='PHONE' AND account_status='DISABLED'"),
                    "pendingDeletionAccounts": count("SELECT COUNT(*) AS count FROM users WHERE role='USER' AND account_type='PHONE' AND account_status='PENDING_DELETION'"),
                    "deletedAccounts": count("SELECT COUNT(*) AS count FROM users WHERE role='USER' AND account_type='PHONE' AND account_status='DELETED'"),
                    "todayRegistrations": count("SELECT COUNT(*) AS count FROM users WHERE role='USER' AND account_type='PHONE' AND created_at>=? AND created_at<?", (day_start, day_end)),
                    "active5m": canonical_activity_count(db, now - 5 * 60 * 1000),
                    "dau": canonical_activity_count(db, day_start, day_end),
                    "wau": canonical_activity_count(db, now - 7 * 86400000),
                    "mau": canonical_activity_count(db, now - 30 * 86400000),
                    "todayFirstLogins": count(
                        """SELECT COUNT(DISTINCT e.user_id) AS count
                           FROM user_behavior_events e
                           JOIN users u ON u.id=e.user_id
                           WHERE u.role='USER' AND u.account_type='PHONE'
                             AND e.event_type='LOGIN_SUCCESS'
                             AND e.result='SUCCESS'
                             AND e.created_at>=? AND e.created_at<?""",
                        (day_start, day_end),
                    ),
                    "totalDevices": device_count(db, {"window": "all", "asOf": str(now)}),
                    "todayNewDevices": device_count(db, {"window": "today", "asOf": str(now)}),
                    "activeDevices": device_count(db, {"window": "5m", "asOf": str(now)}),
                    "activeDevices7d": device_count(db, {"window": "7d", "asOf": str(now)}),
                    "activeDevices30d": device_count(db, {"window": "30d", "asOf": str(now)}),
                    "totalInstallations": installation_count(db, {"window": "all"}),
                    "todayNewInstallations": installation_count(db, {"window": "today"}),
                    "reinstallations": installation_count(db, {"reinstallation": "true"}),
                    "activeReservations": count("SELECT COUNT(*) AS count FROM reservations WHERE status='ACTIVE'"),
                    "todayReservations": count("SELECT COUNT(*) AS count FROM reservations WHERE booking_date=? AND status='ACTIVE'", (today,)),
                    "todayCancelledReservations": count("SELECT COUNT(*) AS count FROM reservations WHERE status='CANCELLED' AND cancelled_at>=?", (day_start,)),
                    "lostFoundOpen": count("SELECT COUNT(*) AS count FROM lost_found_posts WHERE status IN ('SEARCHING','WAITING','CLAIMED')"),
                    "openReports": count("SELECT COUNT(*) AS count FROM lost_found_reports WHERE status='OPEN'"),
                    "openResourceComplaints": count("SELECT COUNT(*) AS count FROM resource_complaints WHERE status IN ('OPEN','PROCESSING')"),
                    "openFeedback": count("SELECT COUNT(*) AS count FROM feedback WHERE workflow_status IN ('OPEN','PROCESSING')"),
                    "enabledAnnouncements": count("SELECT COUNT(*) AS count FROM announcements WHERE enabled=1"),
                    "riskCandidates": count("SELECT COUNT(*) AS count FROM risk_candidates WHERE status='OPEN'"),
                    "loginFailures24h": count("SELECT COUNT(*) AS count FROM user_behavior_events WHERE event_type='LOGIN_FAILURE' AND created_at>=?", (now - 86400000,)),
                    "newLimitedOrDisabled24h": count("SELECT COUNT(*) AS count FROM users WHERE role='USER' AND account_status IN ('LIMITED','DISABLED','SUSPENDED') AND account_status_changed_at>=?", (now - 86400000,)),
                }, 200

            if method == "GET" and path == "/api/v1/admin/venues":
                venues = []
                for venue in db.execute("SELECT * FROM venues ORDER BY sport"):
                    item = row_dict(venue)
                    item["enabled"] = bool(item["enabled"])
                    item["timeSlots"] = [
                        {
                            "id": slot["id"],
                            "startTime": slot["start_time"],
                            "endTime": slot["end_time"],
                            "timeSlot": f"{slot['start_time']}-{slot['end_time']}",
                            "enabled": bool(slot["enabled"]),
                        }
                        for slot in db.execute(
                            "SELECT * FROM venue_time_slots WHERE venue_id=? ORDER BY start_time",
                            (venue["id"],),
                        )
                    ]
                    venues.append(item)
                return venues, 200

            if method == "GET" and path == "/api/v1/admin/inventory":
                venue_id = required_text(query, "venueId", 80)
                booking_date = parse_iso_date(required_text(query, "date", 10))
                return [
                    row_dict(row)
                    for row in db.execute(
                        """SELECT * FROM blocked_inventory
                           WHERE venue_id=? AND booking_date=?
                           ORDER BY time_slot,court_number""",
                        (venue_id, booking_date),
                    )
                ], 200

            if method == "POST" and path == "/api/v1/admin/inventory/block":
                data = self.body_json()
                venue_id = required_text(data, "venueId", 80)
                booking_date = parse_iso_date(required_text(data, "date", 10))
                time_slot = required_text(data, "timeSlot", 20)
                court_number = int(data.get("courtNumber", 0))
                venue = db.execute("SELECT * FROM venues WHERE id=?", (venue_id,)).fetchone()
                if not venue or court_number not in range(1, venue["court_count"] + 1):
                    raise ApiError(400, "INVALID_INVENTORY", "场地编号无效")
                slot = db.execute(
                    """SELECT 1 FROM venue_time_slots WHERE venue_id=?
                       AND start_time || '-' || end_time=?""",
                    (venue_id, time_slot),
                ).fetchone()
                if not slot:
                    raise ApiError(400, "INVALID_TIME_SLOT", "时间段无效")
                block_id = str(uuid.uuid4())
                db.execute(
                    """INSERT INTO blocked_inventory(
                           id,venue_id,booking_date,time_slot,court_number,reason
                       ) VALUES(?,?,?,?,?,?)
                       ON CONFLICT(venue_id,booking_date,time_slot,court_number)
                       DO UPDATE SET reason=excluded.reason""",
                    (
                        block_id,
                        venue_id,
                        booking_date,
                        time_slot,
                        court_number,
                        str(data.get("reason", "场馆暂不可用")).strip()[:100]
                        or "场馆暂不可用",
                    ),
                )
                write_audit(
                    db,
                    admin["id"],
                    "INVENTORY_BLOCKED",
                    "INVENTORY",
                    block_id,
                    {
                        "venueId": venue_id,
                        "date": booking_date,
                        "timeSlot": time_slot,
                        "courtNumber": court_number,
                    },
                )
                return {"blocked": True}, 200

            match = re.fullmatch(r"/api/v1/admin/inventory/block/([^/]+)", path)
            if method == "DELETE" and match:
                changed = db.execute(
                    "DELETE FROM blocked_inventory WHERE id=?", (match.group(1),)
                ).rowcount
                if not changed:
                    raise ApiError(404, "BLOCK_NOT_FOUND", "关闭记录不存在")
                write_audit(
                    db, admin["id"], "INVENTORY_UNBLOCKED", "INVENTORY", match.group(1)
                )
                return {"blocked": False}, 200

            match = re.fullmatch(r"/api/v1/admin/venues/([^/]+)", path)
            if method == "PATCH" and match:
                data = self.body_json()
                venue = db.execute(
                    "SELECT * FROM venues WHERE id=?", (match.group(1),)
                ).fetchone()
                if not venue:
                    raise ApiError(404, "VENUE_NOT_FOUND", "场馆不存在")
                name = str(data.get("name", venue["name"])).strip()[:80]
                count = int(data.get("courtCount", venue["court_count"]))
                price = int(data.get("priceYuan", venue["price_yuan"]))
                enabled = 1 if bool(data.get("enabled", bool(venue["enabled"]))) else 0
                operating_status = str(
                    data.get("operatingStatus", venue["operating_status"])
                ).upper()
                maintenance_note = str(
                    data.get("maintenanceNote", venue["maintenance_note"])
                ).strip()[:200]
                expected_restore_at = data.get(
                    "expectedRestoreAt", venue["expected_restore_at"]
                )
                if expected_restore_at in {"", 0, "0"}:
                    expected_restore_at = None
                elif expected_restore_at is not None:
                    expected_restore_at = int(expected_restore_at)
                if (
                    not name
                    or count < 1
                    or count > 100
                    or price < 0
                    or operating_status not in {"OPEN", "CLOSED", "MAINTENANCE"}
                ):
                    raise ApiError(400, "VALIDATION_ERROR", "场馆配置无效")
                db.execute(
                    """UPDATE venues SET name=?,court_count=?,price_yuan=?,enabled=?,
                       operating_status=?,maintenance_note=?,expected_restore_at=?,
                       updated_at=? WHERE id=?""",
                    (
                        name,
                        count,
                        price,
                        enabled,
                        operating_status,
                        maintenance_note,
                        expected_restore_at,
                        now_ms(),
                        venue["id"],
                    ),
                )
                write_audit(
                    db,
                    admin["id"],
                    "VENUE_UPDATED",
                    "VENUE",
                    venue["id"],
                    {
                        "enabled": bool(enabled),
                        "operatingStatus": operating_status,
                    },
                )
                return row_dict(
                    db.execute("SELECT * FROM venues WHERE id=?", (venue["id"],)).fetchone()
                ), 200

            match = re.fullmatch(r"/api/v1/admin/venues/([^/]+)/time-slots", path)
            if method == "POST" and match:
                data = self.body_json()
                start = required_text(data, "startTime", 5)
                end = required_text(data, "endTime", 5)
                try:
                    datetime.strptime(start, "%H:%M")
                    datetime.strptime(end, "%H:%M")
                except ValueError as exc:
                    raise ApiError(400, "VALIDATION_ERROR", "时间格式必须为 HH:MM") from exc
                if start >= end:
                    raise ApiError(400, "VALIDATION_ERROR", "结束时间必须晚于开始时间")
                venue_id = match.group(1)
                if not db.execute("SELECT 1 FROM venues WHERE id=?", (venue_id,)).fetchone():
                    raise ApiError(404, "VENUE_NOT_FOUND", "场馆不存在")
                slot_id = f"{venue_id}-{start}"
                db.execute(
                    """INSERT INTO venue_time_slots(id,venue_id,start_time,end_time,enabled)
                       VALUES(?,?,?,?,1) ON CONFLICT(venue_id,start_time,end_time)
                       DO UPDATE SET enabled=1""",
                    (slot_id, venue_id, start, end),
                )
                write_audit(
                    db,
                    admin["id"],
                    "TIME_SLOT_CREATED",
                    "TIME_SLOT",
                    slot_id,
                    {"venueId": venue_id, "startTime": start, "endTime": end},
                )
                return {
                    "id": slot_id,
                    "venueId": venue_id,
                    "timeSlot": f"{start}-{end}",
                    "enabled": True,
                }, 201

            match = re.fullmatch(r"/api/v1/admin/time-slots/([^/]+)", path)
            if method == "PATCH" and match:
                data = self.body_json()
                changed = db.execute(
                    "UPDATE venue_time_slots SET enabled=? WHERE id=?",
                    (1 if bool(data.get("enabled")) else 0, match.group(1)),
                ).rowcount
                if not changed:
                    raise ApiError(404, "TIME_SLOT_NOT_FOUND", "时间段不存在")
                write_audit(
                    db,
                    admin["id"],
                    "TIME_SLOT_UPDATED",
                    "TIME_SLOT",
                    match.group(1),
                    {"enabled": bool(data.get("enabled"))},
                )
                return {"id": match.group(1), "enabled": bool(data.get("enabled"))}, 200

            if method == "GET" and path == "/api/v1/admin/reservations":
                clauses, values = [], []
                if query.get("venueId"):
                    clauses.append("r.venue_id=?")
                    values.append(query["venueId"])
                if query.get("date"):
                    clauses.append("r.booking_date=?")
                    values.append(parse_iso_date(query["date"]))
                if query.get("status", "").upper() in {"ACTIVE", "CANCELLED"}:
                    clauses.append("r.status=?")
                    values.append(query["status"].upper())
                if query.get("verificationStatus", "").upper() in {
                    "NOT_REQUIRED", "PENDING", "VERIFIED", "REJECTED"
                }:
                    clauses.append("r.verification_status=?")
                    values.append(query["verificationStatus"].upper())
                if str(query.get("courtNumber", "")).isdigit():
                    clauses.append("r.court_number=?")
                    values.append(int(query["courtNumber"]))
                if query.get("reservationId"):
                    clauses.append("r.id=?")
                    values.append(str(query["reservationId"]).strip()[:80])
                if str(query.get("cancelledSince", "")).isdigit():
                    clauses.append("r.cancelled_at>=?")
                    values.append(int(query["cancelledSince"]))
                if query.get("user"):
                    append_user_search(clauses, values, query.get("user"))
                where = " WHERE " + " AND ".join(clauses) if clauses else ""
                page = max(1, int(query.get("page", "1")) if str(query.get("page", "1")).isdigit() else 1)
                raw_page_size = str(query.get("pageSize", "50")).strip()
                if not raw_page_size.isdigit():
                    raise ApiError(422, "VALIDATION_ERROR", "分页参数必须是非负整数")
                page_size = min(max(int(raw_page_size), 10), 100)
                total = db.execute(
                    """SELECT COUNT(*) AS count FROM reservations r
                       JOIN venues v ON v.id=r.venue_id
                       JOIN users u ON u.id=r.user_id""" + where,
                    values,
                ).fetchone()["count"]
                rows = db.execute(
                        """SELECT r.*,v.name AS venue_name,v.unit_label,u.nickname,
                                  u.phone_masked,u.phone_encrypted,u.reservation_student_id,
                                  u.reservation_name,
                                  u.account_type
                           FROM reservations r
                           JOIN venues v ON v.id=r.venue_id
                           JOIN users u ON u.id=r.user_id"""
                        + where
                        + " ORDER BY r.created_at DESC LIMIT ? OFFSET ?",
                        [*values, page_size, (page - 1) * page_size],
                    ).fetchall()
                items = [full_sports_reservation_row(row) for row in rows]
                write_audit(
                    db, admin["id"], "SPORTS_RESERVATIONS_VIEWED", "SPORTS", None,
                    {"resultCount": len(items), "filtered": bool(clauses), "page": page},
                )
                if str(query.get("paged", "")).lower() == "true":
                    return {"items": items, "total": total, "page": page, "pageSize": page_size}, 200
                return items, 200

            match = re.fullmatch(r"/api/v1/admin/reservations/([^/]+)", path)
            if method == "GET" and match:
                row = db.execute(
                    """SELECT r.*,v.name AS venue_name,v.unit_label,u.nickname,
                              u.phone_masked,u.phone_encrypted,u.reservation_student_id,
                              u.reservation_name,u.account_type
                       FROM reservations r
                       JOIN venues v ON v.id=r.venue_id
                       JOIN users u ON u.id=r.user_id WHERE r.id=?""",
                    (match.group(1),),
                ).fetchone()
                if not row:
                    raise ApiError(404, "RESERVATION_NOT_FOUND", "预约不存在")
                history = db.execute(
                    """SELECT r.*,v.name AS venue_name,v.unit_label,u.nickname,
                              u.phone_masked,u.phone_encrypted,u.reservation_student_id,
                              u.reservation_name,u.account_type
                       FROM reservations r
                       JOIN venues v ON v.id=r.venue_id
                       JOIN users u ON u.id=r.user_id
                       WHERE r.user_id=? ORDER BY r.created_at DESC LIMIT 100""",
                    (row["user_id"],),
                ).fetchall()
                write_audit(
                    db, admin["id"], "SPORTS_RESERVATION_DETAILS_VIEWED",
                    "RESERVATION", row["id"], {"historyCount": len(history)},
                )
                return {
                    "reservation": full_sports_reservation_row(row),
                    "sportsHistory": [full_sports_reservation_row(item) for item in history],
                }, 200

            match = re.fullmatch(r"/api/v1/admin/reservations/([^/]+)/verify", path)
            if method == "POST" and match:
                data = self.body_json()
                status = str(data.get("status", "VERIFIED")).upper()
                if status not in {"PENDING", "VERIFIED", "REJECTED", "NOT_REQUIRED"}:
                    raise ApiError(400, "INVALID_VERIFICATION_STATUS", "核验状态无效")
                note = str(data.get("note", "")).strip()[:300]
                current = db.execute(
                    "SELECT id,verification_status FROM reservations WHERE id=?",
                    (match.group(1),),
                ).fetchone()
                if not current:
                    raise ApiError(404, "RESERVATION_NOT_FOUND", "预约不存在")
                db.execute(
                    "UPDATE reservations SET verification_status=?,updated_at=? WHERE id=?",
                    (status, now_ms(), current["id"]),
                )
                write_audit(
                    db, admin["id"], "RESERVATION_PROFILE_VERIFIED",
                    "RESERVATION", current["id"],
                    {"oldStatus": current["verification_status"], "newStatus": status,
                     "noteLength": len(note)},
                )
                return {"id": current["id"], "verificationStatus": status}, 200

            match = re.fullmatch(r"/api/v1/admin/reservations/([^/]+)/cancel", path)
            if method == "POST" and match:
                data = self.body_json()
                reservation = db.execute(
                    "SELECT * FROM reservations WHERE id=? AND status='ACTIVE'",
                    (match.group(1),),
                ).fetchone()
                if not reservation:
                    raise ApiError(409, "CANNOT_CANCEL", "预约不存在或已取消")
                timestamp = now_ms()
                changed = db.execute(
                    """UPDATE reservations SET status='CANCELLED',cancelled_at=?,
                       cancelled_by='ADMIN',cancel_reason=?,updated_at=?
                       WHERE id=? AND status='ACTIVE'""",
                    (
                        timestamp,
                        str(data.get("reason", "管理员取消")).strip()[:200] or "管理员取消",
                        timestamp,
                        match.group(1),
                    ),
                ).rowcount
                if not changed:
                    raise ApiError(409, "CANNOT_CANCEL", "预约不存在或已取消")
                write_reservation_event(
                    db,
                    reservation["id"],
                    reservation["user_id"],
                    admin["id"],
                    "ADMIN_CANCELLED",
                    str(data.get("reason", "管理员取消")).strip()[:200],
                )
                write_audit(
                    db,
                    admin["id"],
                    "RESERVATION_CANCELLED",
                    "RESERVATION",
                    reservation["id"],
                    {"userId": reservation["user_id"]},
                )
                return {"id": match.group(1), "status": "CANCELLED"}, 200

            if method == "GET" and path == "/api/v1/admin/lost-found":
                require_admin_permission(admin, "LOST_VIEW")
                clauses, values = [], []
                if query.get("type", "").upper() in LOST_TYPES:
                    clauses.append("p.type=?")
                    values.append(query["type"].upper())
                if query.get("status"):
                    clauses.append("p.status=?")
                    values.append(query["status"].upper())
                if query.get("q"):
                    clauses.append(
                        "(p.item_name LIKE ? OR p.location LIKE ? OR p.description LIKE ?)"
                    )
                    token = f"%{query['q'][:80]}%"
                    values.extend([token, token, token])
                if query.get("user"):
                    append_user_search(clauses, values, query.get("user"))
                where = " WHERE " + " AND ".join(clauses) if clauses else ""
                rows = db.execute(
                        """SELECT p.*,u.nickname,u.phone_masked,
                           u.phone_encrypted,
                           u.reservation_student_id,u.reservation_name,
                           (SELECT COUNT(*) FROM lost_found_reports rr
                            WHERE rr.post_id=p.id AND rr.status='OPEN') AS open_reports
                           FROM lost_found_posts p
                           JOIN users u ON u.id=p.owner_user_id"""
                        + where
                        + " ORDER BY p.updated_at DESC LIMIT 500",
                        values,
                    ).fetchall()
                return [full_admin_user_row(row) if is_super_admin(admin) else mask_admin_user_row(row) for row in rows], 200

            if method == "POST" and path == "/api/v1/admin/lost-found/bulk-delete":
                require_admin_permission(admin, "LOST_MANAGE")
                data = self.body_json()
                raw_ids = data.get("ids")
                if not isinstance(raw_ids, list):
                    raise ApiError(400, "VALIDATION_ERROR", "ids 必须是数组")
                post_ids = list(dict.fromkeys(str(value) for value in raw_ids))
                if not post_ids or len(post_ids) > 100 or any(
                    not re.fullmatch(r"[0-9a-f-]{36}", value) for value in post_ids
                ):
                    raise ApiError(400, "VALIDATION_ERROR", "批量删除对象无效或超过 100 条")
                reason = required_text(data, "reason", 300)
                confirmation = str(data.get("confirmation", "")).strip()
                expected_confirmation = f"DELETE {len(post_ids)} ITEMS"
                if confirmation != expected_confirmation:
                    raise ApiError(400, "CONFIRMATION_REQUIRED", "批量删除确认文本不匹配")
                placeholders = ",".join("?" for _ in post_ids)
                posts = db.execute(
                    f"SELECT * FROM lost_found_posts WHERE id IN ({placeholders})",
                    post_ids,
                ).fetchall()
                if len(posts) != len(post_ids):
                    raise ApiError(409, "BULK_TARGET_CHANGED", "部分对象已不存在，请刷新后重试")
                for post in posts:
                    self.record_lost_deletion(db, post, admin["id"], reason)
                db.execute(
                    f"DELETE FROM lost_found_posts WHERE id IN ({placeholders})",
                    post_ids,
                )
                for post in posts:
                    if post["image_name"]:
                        run_safely_delete_upload(post["image_name"])
                write_audit(
                    db,
                    admin["id"],
                    "LOST_FOUND_BULK_DELETED",
                    "LOST_FOUND",
                    None,
                    {"count": len(post_ids), "reason": reason[:120]},
                )
                return {"deleted": len(post_ids), "ids": post_ids}, 200

            match = re.fullmatch(r"/api/v1/admin/lost-found/([^/]+)", path)
            if method == "GET" and match:
                require_admin_permission(admin, "LOST_VIEW")
                row = db.execute(
                    """SELECT p.*,u.id AS user_id,u.nickname,u.phone_masked,
                              u.phone_encrypted,u.reservation_student_id,u.reservation_name,
                              (SELECT COUNT(*) FROM lost_found_reports rr
                               WHERE rr.post_id=p.id AND rr.status='OPEN') AS open_reports
                       FROM lost_found_posts p JOIN users u ON u.id=p.owner_user_id
                       WHERE p.id=?""",
                    (match.group(1),),
                ).fetchone()
                if not row:
                    raise ApiError(404, "POST_NOT_FOUND", "失物信息不存在")
                return (
                    full_admin_user_row(row)
                    if is_super_admin(admin)
                    else mask_admin_user_row(row)
                ), 200
            if method == "PATCH" and match:
                require_admin_permission(admin, "LOST_MANAGE")
                data = self.body_json()
                post = db.execute(
                    "SELECT * FROM lost_found_posts WHERE id=?", (match.group(1),)
                ).fetchone()
                if not post:
                    raise ApiError(404, "POST_NOT_FOUND", "失物信息不存在")
                status = str(data.get("status", post["status"])).upper()
                if status not in LOST_STATUSES[post["type"]]:
                    raise ApiError(400, "VALIDATION_ERROR", "失物状态无效")
                db.execute(
                    "UPDATE lost_found_posts SET status=?,updated_at=? WHERE id=?",
                    (status, now_ms(), post["id"]),
                )
                write_audit(
                    db,
                    admin["id"],
                    "LOST_FOUND_STATUS_UPDATED",
                    "LOST_FOUND",
                    post["id"],
                    {"status": status},
                )
                return {"id": post["id"], "status": status}, 200

            if method == "DELETE" and match:
                require_admin_permission(admin, "LOST_MANAGE")
                post = db.execute(
                    "SELECT * FROM lost_found_posts WHERE id=?", (match.group(1),)
                ).fetchone()
                if not post:
                    raise ApiError(404, "POST_NOT_FOUND", "失物信息不存在")
                self.record_lost_deletion(
                    db, post, admin["id"], "管理员删除失物信息"
                )
                db.execute("DELETE FROM lost_found_posts WHERE id=?", (match.group(1),))
                if post["image_name"]:
                    run_safely_delete_upload(post["image_name"])
                write_audit(
                    db, admin["id"], "LOST_FOUND_DELETED", "LOST_FOUND", match.group(1)
                )
                return {"id": match.group(1), "deleted": True}, 200

            if method == "GET" and path == "/api/v1/admin/lost-found-reports":
                clauses, values = [], []
                if query.get("status"):
                    clauses.append("r.status=?")
                    values.append(str(query["status"]).upper())
                if query.get("user"):
                    append_user_search(clauses, values, query.get("user"))
                where = " WHERE " + " AND ".join(clauses) if clauses else ""
                rows = db.execute(
                        """SELECT r.*,p.item_name,p.type,u.nickname,u.phone_masked,
                           u.phone_encrypted
                           FROM lost_found_reports r
                           JOIN lost_found_posts p ON p.id=r.post_id
                           JOIN users u ON u.id=r.user_id""" + where +
                           " ORDER BY r.created_at DESC LIMIT 500", values
                    ).fetchall()
                return [
                    full_admin_user_row(row) if is_super_admin(admin)
                    else mask_admin_user_row(row) for row in rows
                ], 200

            if method == "GET" and path == "/api/v1/admin/lost-found-claims":
                clauses, values = [], []
                if query.get("user"):
                    append_user_search(clauses, values, query.get("user"))
                where = " WHERE " + " AND ".join(clauses) if clauses else ""
                rows = db.execute(
                    """SELECT c.*,p.item_name,p.type,u.nickname,u.phone_masked,
                       u.phone_encrypted
                       FROM lost_found_claims c
                       JOIN lost_found_posts p ON p.id=c.post_id
                       JOIN users u ON u.id=c.user_id""" + where +
                    " ORDER BY c.created_at DESC LIMIT 500", values
                ).fetchall()
                return [
                    full_admin_user_row(row) if is_super_admin(admin)
                    else mask_admin_user_row(row) for row in rows
                ], 200

            match = re.fullmatch(r"/api/v1/admin/lost-found-reports/([^/]+)", path)
            if method == "PATCH" and match:
                data = self.body_json()
                status = str(data.get("status", "")).upper()
                if status not in {"OPEN", "REVIEWED", "CLOSED"}:
                    raise ApiError(400, "VALIDATION_ERROR", "举报状态无效")
                changed = db.execute(
                    "UPDATE lost_found_reports SET status=? WHERE id=?",
                    (status, match.group(1)),
                ).rowcount
                if not changed:
                    raise ApiError(404, "REPORT_NOT_FOUND", "举报不存在")
                write_audit(
                    db,
                    admin["id"],
                    "LOST_FOUND_REPORT_UPDATED",
                    "LOST_FOUND_REPORT",
                    match.group(1),
                    {"status": status},
                )
                return {"id": match.group(1), "status": status}, 200

            if method == "GET" and path == "/api/v1/admin/resources":
                clauses, values = [], []
                if query.get("status", "").upper() in {
                    "DRAFT",
                    "PUBLISHED",
                    "UNLISTED",
                }:
                    clauses.append("publish_status=?")
                    values.append(query["status"].upper())
                if query.get("category"):
                    clauses.append("category=?")
                    values.append(query["category"][:80])
                if query.get("q"):
                    token = f"%{query['q'][:100]}%"
                    clauses.append(
                        "(title LIKE ? OR summary LIKE ? OR tags_json LIKE ?)"
                    )
                    values.extend([token, token, token])
                where = " WHERE " + " AND ".join(clauses) if clauses else ""
                return [
                    {
                        **row_dict(row),
                        "tags": json.loads(row["tags_json"] or "[]"),
                    }
                    for row in db.execute(
                        "SELECT * FROM resource_items"
                        + where
                        + " ORDER BY sort_order DESC,updated_at DESC LIMIT 500",
                        values,
                    )
                ], 200

            if method == "GET" and path == "/api/v1/admin/resource-activity":
                user_id = required_text(query, "user", 80)
                kind = str(query.get("kind", "views")).lower()
                if kind == "views":
                    rows = db.execute(
                        """SELECT rv.user_id,rv.resource_id,rv.view_count,rv.last_viewed_at,
                           ri.title,u.nickname,u.phone_masked,u.phone_encrypted
                           FROM resource_views rv
                           JOIN resource_items ri ON ri.id=rv.resource_id
                           JOIN users u ON u.id=rv.user_id WHERE rv.user_id=?
                           ORDER BY rv.last_viewed_at DESC LIMIT 500""", (user_id,)
                    ).fetchall()
                elif kind == "favorites":
                    rows = db.execute(
                        """SELECT rf.user_id,rf.resource_id,rf.created_at,
                           ri.title,u.nickname,u.phone_masked,u.phone_encrypted
                           FROM resource_favorites rf
                           JOIN resource_items ri ON ri.id=rf.resource_id
                           JOIN users u ON u.id=rf.user_id WHERE rf.user_id=?
                           ORDER BY rf.created_at DESC LIMIT 500""", (user_id,)
                    ).fetchall()
                else:
                    raise ApiError(400, "INVALID_ACTIVITY_KIND", "资料活动类型无效")
                return [
                    full_admin_user_row(row) if is_super_admin(admin)
                    else mask_admin_user_row(row) for row in rows
                ], 200

            if method == "POST" and path == "/api/v1/admin/resources":
                data = self.body_json()
                status = str(data.get("publishStatus", "DRAFT")).upper()
                visibility = str(data.get("visibility", "MEMBERS")).upper()
                if status not in {"DRAFT", "PUBLISHED", "UNLISTED"}:
                    raise ApiError(400, "VALIDATION_ERROR", "发布状态无效")
                if visibility not in {"MEMBERS", "PUBLIC"}:
                    raise ApiError(400, "VALIDATION_ERROR", "可见范围无效")
                tags = data.get("tags", [])
                if isinstance(tags, str):
                    tags = [value.strip() for value in tags.split(",") if value.strip()]
                if not isinstance(tags, list):
                    raise ApiError(400, "VALIDATION_ERROR", "标签格式无效")
                future_price = (
                    int(data["futurePriceFen"])
                    if data.get("futurePriceFen") not in {None, ""}
                    else None
                )
                if future_price is not None and future_price < 0:
                    raise ApiError(400, "VALIDATION_ERROR", "预留价格不能为负数")
                resource_id = str(uuid.uuid4())
                timestamp = now_ms()
                db.execute(
                    """INSERT INTO resource_items(
                           id,title,summary,category,tags_json,audience_scope,
                           publisher_label,source_label,usage_note,resource_type,
                           external_url,cover_url,publish_status,visibility,is_free,
                           future_price_fen,sort_order,published_at,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        resource_id,
                        required_text(data, "title", 160),
                        str(data.get("summary", "")).strip()[:2000],
                        required_text(data, "category", 80),
                        json.dumps([str(value)[:40] for value in tags[:20]], ensure_ascii=False),
                        str(data.get("audienceScope", "")).strip()[:200],
                        str(data.get("publisherLabel", "我的川农资料库")).strip()[:100]
                        or "我的川农资料库",
                        str(data.get("sourceLabel", "")).strip()[:100],
                        str(data.get("usageNote", "")).strip()[:1000],
                        str(data.get("resourceType", "EXTERNAL_LINK")).strip()[:40]
                        or "EXTERNAL_LINK",
                        validate_external_url(data.get("externalUrl")),
                        validate_external_url(data.get("coverUrl"), required=False),
                        status,
                        visibility,
                        1 if bool(data.get("isFree", True)) else 0,
                        future_price,
                        int(data.get("sortOrder", 0)),
                        timestamp if status == "PUBLISHED" else None,
                        timestamp,
                        timestamp,
                    ),
                )
                write_audit(
                    db,
                    admin["id"],
                    "RESOURCE_CREATED",
                    "RESOURCE",
                    resource_id,
                    {"publishStatus": status},
                )
                return {"id": resource_id, "publishStatus": status}, 201

            match = re.fullmatch(r"/api/v1/admin/resources/([^/]+)", path)
            if method == "PATCH" and match:
                current = db.execute(
                    "SELECT * FROM resource_items WHERE id=?", (match.group(1),)
                ).fetchone()
                if not current:
                    raise ApiError(404, "RESOURCE_NOT_FOUND", "资料不存在")
                data = self.body_json()
                status = str(
                    data.get("publishStatus", current["publish_status"])
                ).upper()
                visibility = str(
                    data.get("visibility", current["visibility"])
                ).upper()
                if status not in {"DRAFT", "PUBLISHED", "UNLISTED"}:
                    raise ApiError(400, "VALIDATION_ERROR", "发布状态无效")
                if visibility not in {"MEMBERS", "PUBLIC"}:
                    raise ApiError(400, "VALIDATION_ERROR", "可见范围无效")
                tags = data.get("tags", json.loads(current["tags_json"] or "[]"))
                if isinstance(tags, str):
                    tags = [value.strip() for value in tags.split(",") if value.strip()]
                if not isinstance(tags, list):
                    raise ApiError(400, "VALIDATION_ERROR", "标签格式无效")
                title = str(data.get("title", current["title"])).strip()[:160]
                category = str(
                    data.get("category", current["category"])
                ).strip()[:80]
                if not title or not category:
                    raise ApiError(400, "VALIDATION_ERROR", "资料标题和分类不能为空")
                future_price = (
                    int(data["futurePriceFen"])
                    if data.get("futurePriceFen") not in {None, ""}
                    else None
                )
                if future_price is not None and future_price < 0:
                    raise ApiError(400, "VALIDATION_ERROR", "预留价格不能为负数")
                timestamp = now_ms()
                published_at = current["published_at"]
                if status == "PUBLISHED" and not published_at:
                    published_at = timestamp
                db.execute(
                    """UPDATE resource_items SET title=?,summary=?,category=?,tags_json=?,
                       audience_scope=?,publisher_label=?,source_label=?,usage_note=?,
                       resource_type=?,external_url=?,cover_url=?,publish_status=?,
                       visibility=?,is_free=?,future_price_fen=?,sort_order=?,
                       published_at=?,updated_at=? WHERE id=?""",
                    (
                        title,
                        str(data.get("summary", current["summary"])).strip()[:2000],
                        category,
                        json.dumps([str(value)[:40] for value in tags[:20]], ensure_ascii=False),
                        str(data.get("audienceScope", current["audience_scope"])).strip()[:200],
                        str(data.get("publisherLabel", current["publisher_label"])).strip()[:100],
                        str(data.get("sourceLabel", current["source_label"])).strip()[:100],
                        str(data.get("usageNote", current["usage_note"])).strip()[:1000],
                        str(data.get("resourceType", current["resource_type"])).strip()[:40],
                        validate_external_url(
                            data.get("externalUrl", current["external_url"])
                        ),
                        validate_external_url(
                            data.get("coverUrl", current["cover_url"]), required=False
                        ),
                        status,
                        visibility,
                        1 if bool(data.get("isFree", bool(current["is_free"]))) else 0,
                        future_price,
                        int(data.get("sortOrder", current["sort_order"])),
                        published_at,
                        timestamp,
                        current["id"],
                    ),
                )
                write_audit(
                    db,
                    admin["id"],
                    "RESOURCE_UPDATED",
                    "RESOURCE",
                    current["id"],
                    {
                        "oldStatus": current["publish_status"],
                        "newStatus": status,
                    },
                )
                return {"id": current["id"], "publishStatus": status}, 200

            if method == "DELETE" and match:
                changed = db.execute(
                    "DELETE FROM resource_items WHERE id=?", (match.group(1),)
                ).rowcount
                if not changed:
                    raise ApiError(404, "RESOURCE_NOT_FOUND", "资料不存在")
                write_audit(
                    db,
                    admin["id"],
                    "RESOURCE_DELETED",
                    "RESOURCE",
                    match.group(1),
                )
                return {"id": match.group(1), "deleted": True}, 200

            if method == "GET" and path == "/api/v1/admin/resource-complaints":
                clauses, values = [], []
                if query.get("status", "").upper() in {
                    "OPEN",
                    "PROCESSING",
                    "RESOLVED",
                    "CLOSED",
                }:
                    clauses.append("c.status=?")
                    values.append(query["status"].upper())
                append_user_search(clauses, values, query.get("user"))
                where = " WHERE " + " AND ".join(clauses) if clauses else ""
                rows = db.execute(
                        """SELECT c.*,i.title,u.nickname,u.phone_masked,
                                  u.phone_encrypted,u.reservation_student_id,
                                  u.reservation_name
                           FROM resource_complaints c
                           JOIN resource_items i ON i.id=c.resource_id
                           JOIN users u ON u.id=c.user_id"""
                        + where
                        + " ORDER BY c.created_at DESC LIMIT 500",
                        values,
                    ).fetchall()
                return [
                    full_admin_user_row(row) if is_super_admin(admin)
                    else mask_admin_user_row(row) for row in rows
                ], 200

            match = re.fullmatch(
                r"/api/v1/admin/resource-complaints/([^/]+)", path
            )
            if method == "PATCH" and match:
                data = self.body_json()
                status = str(data.get("status", "")).upper()
                if status not in {"OPEN", "PROCESSING", "RESOLVED", "CLOSED"}:
                    raise ApiError(400, "VALIDATION_ERROR", "投诉状态无效")
                note = str(data.get("resolutionNote", "")).strip()[:1000]
                changed = db.execute(
                    """UPDATE resource_complaints SET status=?,resolution_note=?,
                       updated_at=? WHERE id=?""",
                    (status, note, now_ms(), match.group(1)),
                ).rowcount
                if not changed:
                    raise ApiError(404, "COMPLAINT_NOT_FOUND", "投诉不存在")
                write_audit(
                    db,
                    admin["id"],
                    "RESOURCE_COMPLAINT_UPDATED",
                    "RESOURCE_COMPLAINT",
                    match.group(1),
                    {"status": status},
                )
                return {"id": match.group(1), "status": status}, 200

            if method == "GET" and path == "/api/v1/admin/announcements":
                return [
                    {
                        **row_dict(row),
                        "enabled": bool(row["enabled"]),
                        "serviceVisible": bool(row["service_visible"]),
                        "notificationEnabled": bool(row["notification_enabled"]),
                        "popupEnabled": bool(row["popup_enabled"]),
                    }
                    for row in db.execute(
                        "SELECT * FROM announcements ORDER BY published_at DESC"
                    )
                ], 200

            if method == "GET" and path == "/api/v1/admin/website-announcements":
                return [
                    {
                        **row_dict(row),
                        "enabled": bool(row["enabled"]),
                    }
                    for row in db.execute(
                        "SELECT * FROM website_announcements ORDER BY published_at DESC, updated_at DESC"
                    )
                ], 200

            if method == "POST" and path == "/api/v1/admin/website-announcements":
                data = self.body_json()
                announcement_id = str(uuid.uuid4())
                timestamp = now_ms()
                enabled = 1 if bool(data.get("enabled", True)) else 0
                db.execute(
                    """INSERT INTO website_announcements(
                           id,title,content,enabled,published_at,updated_at
                       ) VALUES(?,?,?,?,?,?)""",
                    (
                        announcement_id,
                        required_text(data, "title", 100),
                        required_text(data, "content", 4000),
                        enabled,
                        timestamp,
                        timestamp,
                    ),
                )
                write_audit(
                    db,
                    admin["id"],
                    "WEBSITE_ANNOUNCEMENT_CREATED",
                    "WEBSITE_ANNOUNCEMENT",
                    announcement_id,
                    {"enabled": bool(enabled)},
                )
                return {"id": announcement_id, "enabled": bool(enabled)}, 201

            website_match = re.fullmatch(
                r"/api/v1/admin/website-announcements/([^/]+)", path
            )
            if method == "PATCH" and website_match:
                data = self.body_json()
                current = db.execute(
                    "SELECT * FROM website_announcements WHERE id=?",
                    (website_match.group(1),),
                ).fetchone()
                if not current:
                    raise ApiError(404, "WEBSITE_ANNOUNCEMENT_NOT_FOUND", "网站公告不存在")
                title = str(data.get("title", current["title"])).strip()[:100]
                content_value = str(data.get("content", current["content"])).strip()[:4000]
                enabled = 1 if bool(data.get("enabled", bool(current["enabled"]))) else 0
                if not title or not content_value:
                    raise ApiError(400, "VALIDATION_ERROR", "网站公告标题和内容不能为空")
                updated_at = now_ms()
                db.execute(
                    """UPDATE website_announcements
                          SET title=?,content=?,enabled=?,updated_at=?
                        WHERE id=?""",
                    (title, content_value, enabled, updated_at, current["id"]),
                )
                write_audit(
                    db,
                    admin["id"],
                    "WEBSITE_ANNOUNCEMENT_UPDATED",
                    "WEBSITE_ANNOUNCEMENT",
                    current["id"],
                    {"enabled": bool(enabled)},
                )
                return {
                    "id": current["id"],
                    "enabled": bool(enabled),
                    "updatedAt": updated_at,
                }, 200

            if method == "DELETE" and website_match:
                changed = db.execute(
                    "DELETE FROM website_announcements WHERE id=?",
                    (website_match.group(1),),
                ).rowcount
                if not changed:
                    raise ApiError(404, "WEBSITE_ANNOUNCEMENT_NOT_FOUND", "网站公告不存在")
                write_audit(
                    db,
                    admin["id"],
                    "WEBSITE_ANNOUNCEMENT_DELETED",
                    "WEBSITE_ANNOUNCEMENT",
                    website_match.group(1),
                )
                return {"id": website_match.group(1), "deleted": True}, 200

            if method == "POST" and path == "/api/v1/admin/announcements":
                data = self.body_json()
                announcement_id = str(uuid.uuid4())
                timestamp = now_ms()
                service_visible = 1 if bool(data.get("serviceVisible", True)) else 0
                notification_enabled = 1 if bool(data.get("notificationEnabled", False)) else 0
                popup_enabled = 1 if bool(data.get("popupEnabled", False)) else 0
                db.execute(
                    """INSERT INTO announcements(
                           id,title,content,enabled,service_visible,
                           notification_enabled,popup_enabled,published_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        announcement_id,
                        required_text(data, "title", 100),
                        required_text(data, "content", 4000),
                        1 if bool(data.get("enabled", True)) else 0,
                        service_visible,
                        notification_enabled,
                        popup_enabled,
                        timestamp,
                        timestamp,
                    ),
                )
                write_audit(
                    db,
                    admin["id"],
                    "ANNOUNCEMENT_CREATED",
                    "ANNOUNCEMENT",
                    announcement_id,
                    {
                        "enabled": bool(data.get("enabled", True)),
                        "serviceVisible": bool(service_visible),
                        "notificationEnabled": bool(notification_enabled),
                        "popupEnabled": bool(popup_enabled),
                    },
                )
                return {
                    "id": announcement_id,
                    "enabled": bool(data.get("enabled", True)),
                    "serviceVisible": bool(service_visible),
                    "notificationEnabled": bool(notification_enabled),
                    "popupEnabled": bool(popup_enabled),
                }, 201

            match = re.fullmatch(r"/api/v1/admin/announcements/([^/]+)", path)
            if method == "PATCH" and match:
                data = self.body_json()
                current = db.execute(
                    "SELECT * FROM announcements WHERE id=?", (match.group(1),)
                ).fetchone()
                if not current:
                    raise ApiError(404, "ANNOUNCEMENT_NOT_FOUND", "公告不存在")
                title = str(data.get("title", current["title"])).strip()[:100]
                content = str(data.get("content", current["content"])).strip()[:4000]
                enabled = 1 if bool(data.get("enabled", bool(current["enabled"]))) else 0
                service_visible = (
                    1
                    if bool(data.get("serviceVisible", bool(current["service_visible"])))
                    else 0
                )
                notification_enabled = (
                    1
                    if bool(data.get("notificationEnabled", bool(current["notification_enabled"])))
                    else 0
                )
                popup_enabled = (
                    1
                    if bool(data.get("popupEnabled", bool(current["popup_enabled"])))
                    else 0
                )
                if not title or not content:
                    raise ApiError(400, "VALIDATION_ERROR", "公告标题和内容不能为空")
                db.execute(
                    """UPDATE announcements SET title=?,content=?,enabled=?,
                       service_visible=?,notification_enabled=?,popup_enabled=?,updated_at=?
                       WHERE id=?""",
                    (
                        title,
                        content,
                        enabled,
                        service_visible,
                        notification_enabled,
                        popup_enabled,
                        now_ms(),
                        current["id"],
                    ),
                )
                write_audit(
                    db,
                    admin["id"],
                    "ANNOUNCEMENT_UPDATED",
                    "ANNOUNCEMENT",
                    current["id"],
                    {
                        "enabled": bool(enabled),
                        "serviceVisible": bool(service_visible),
                        "notificationEnabled": bool(notification_enabled),
                        "popupEnabled": bool(popup_enabled),
                    },
                )
                return {
                    "id": current["id"],
                    "enabled": bool(enabled),
                    "serviceVisible": bool(service_visible),
                    "notificationEnabled": bool(notification_enabled),
                    "popupEnabled": bool(popup_enabled),
                }, 200

            if method == "DELETE" and match:
                changed = db.execute(
                    "DELETE FROM announcements WHERE id=?", (match.group(1),)
                ).rowcount
                if not changed:
                    raise ApiError(404, "ANNOUNCEMENT_NOT_FOUND", "公告不存在")
                write_audit(
                    db,
                    admin["id"],
                    "ANNOUNCEMENT_DELETED",
                    "ANNOUNCEMENT",
                    match.group(1),
                )
                return {"id": match.group(1), "deleted": True}, 200

            if method == "GET" and path == "/api/v1/admin/config":
                flags = []
                for row in db.execute("SELECT * FROM remote_flags ORDER BY key"):
                    flags.append(
                        {
                            "key": row["key"],
                            "enabled": bool(row["enabled"]),
                            "payload": json.loads(row["payload_json"]),
                            "updatedAt": row["updated_at"],
                            "updatedBy": row["updated_by"],
                            "changeNote": row["change_note"],
                        }
                    )
                version_row = db.execute(
                    "SELECT * FROM app_versions WHERE platform='android'"
                ).fetchone()
                version = row_dict(version_row)
                policy = update_policy_state(db)
                if version:
                    # Compatibility aliases only. The policy table, returned
                    # separately below, is the authority for these controls.
                    version["force_update"] = policy["forceUpdateEnabled"]
                    version["forceUpdate"] = policy["forceUpdateEnabled"]
                    version["minimum_version_code"] = policy[
                        "minimumSupportedVersionCode"
                    ]
                    version["metadata_verified"] = bool(version.get("metadata_verified"))
                available_versions = [
                    row_dict(row)
                    for row in db.execute(
                        """SELECT platform,version_code,version_name,update_message,
                                  download_url,apk_sha256,apk_size,package_name,
                                  signing_certificate_sha256,metadata_verified,status,
                                  published_at,created_at,updated_at
                           FROM app_release_versions
                          WHERE platform='android' AND status='PUBLISHED'
                          ORDER BY version_code DESC"""
                    )
                ]
                for release in available_versions:
                    release["metadata_verified"] = bool(release["metadata_verified"])
                return {
                    "flags": flags,
                    "version": version,
                    "releaseMetadata": (
                        serialize_release_metadata(version_row) if version_row else None
                    ),
                    "updatePolicy": policy,
                    "appConfig": admin_app_config_state(db),
                    "availableVersions": available_versions,
                }, 200

            if method == "POST" and path == "/api/v1/admin/app-config/validate":
                require_admin_permission(admin, "*")
                data = self.body_json()
                config = validate_app_config(data.get("config", data))
                canonical = canonical_app_config_json(config)
                return {
                    "schemaVersion": APP_CONFIG_SCHEMA_VERSION,
                    "configHash": app_config_hash(config),
                    "configBytes": len(canonical.encode("utf-8")),
                    "config": config,
                }, 200

            if method == "POST" and path == "/api/v1/admin/app-config/revisions":
                require_admin_permission(admin, "*")
                data = self.body_json()
                config = validate_app_config(data.get("config", {}))
                try:
                    min_code = int(data.get("minAppVersionCode", 0))
                    max_raw = data.get("maxAppVersionCode")
                    max_code = None if max_raw in (None, "") else int(max_raw)
                except (TypeError, ValueError) as exc:
                    raise ApiError(422, "INVALID_APP_CONFIG", "配置兼容版本范围无效") from exc
                if min_code < 0 or (max_code is not None and max_code < min_code):
                    raise ApiError(422, "INVALID_APP_CONFIG", "配置兼容版本范围无效")
                config_json = canonical_app_config_json(config)
                config_hash = app_config_hash(config)
                db.execute("BEGIN IMMEDIATE")
                current = db.execute(
                    "SELECT COALESCE(MAX(config_version),0) AS config_version "
                    "FROM app_config_revisions WHERE platform='android'"
                ).fetchone()
                config_version = int(current["config_version"]) + 1
                timestamp = now_ms()
                try:
                    cursor = db.execute(
                        """INSERT INTO app_config_revisions(
                               platform,schema_version,config_version,min_app_version_code,
                               max_app_version_code,config_hash,config_json,status,
                               created_by,created_at,updated_by,updated_at
                           ) VALUES('android',?,?,?,?,?,?, 'DRAFT',?,?,?,?)""",
                        (
                            APP_CONFIG_SCHEMA_VERSION,
                            config_version,
                            min_code,
                            max_code,
                            config_hash,
                            config_json,
                            admin["id"],
                            timestamp,
                            admin["id"],
                            timestamp,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ApiError(409, "APP_CONFIG_REVISION_CONFLICT", "配置版本生成冲突，请重试") from exc
                revision = db.execute(
                    "SELECT * FROM app_config_revisions WHERE revision=?",
                    (cursor.lastrowid,),
                ).fetchone()
                write_audit(
                    db,
                    admin["id"],
                    "APP_CONFIG_DRAFT_CREATED",
                    "APP_CONFIG",
                    str(cursor.lastrowid),
                    {
                        "configVersion": config_version,
                        "configHash": config_hash,
                        "reasonLength": len(str(data.get("reason", ""))[:300]),
                    },
                )
                db.execute("COMMIT")
                return serialize_app_config_revision(revision), 201

            if method == "POST" and path == "/api/v1/admin/app-config/activate":
                require_admin_permission(admin, "*")
                data = self.body_json()
                try:
                    revision_id = int(data.get("revision", 0))
                    expected_active = int(data.get("expectedActiveRevision", 0))
                except (TypeError, ValueError) as exc:
                    raise ApiError(422, "INVALID_APP_CONFIG", "配置 Revision 无效") from exc
                db.execute("BEGIN IMMEDIATE")
                state = db.execute(
                    "SELECT * FROM app_config_state WHERE platform='android'"
                ).fetchone()
                actual_active = int(state["active_revision"]) if state and state["active_revision"] else 0
                if expected_active != actual_active:
                    raise ApiError(409, "APP_CONFIG_REVISION_CONFLICT", "配置已被其他管理员修改，请刷新后重试")
                revision = db.execute(
                    "SELECT * FROM app_config_revisions WHERE revision=? AND platform='android'",
                    (revision_id,),
                ).fetchone()
                if not revision:
                    raise ApiError(404, "APP_CONFIG_REVISION_NOT_FOUND", "配置 Revision 不存在")
                if revision["status"] != "DRAFT":
                    raise ApiError(409, "APP_CONFIG_NOT_DRAFT", "只有草稿配置可以激活")
                if int(revision["config_version"]) <= int(state["config_version"]):
                    raise ApiError(409, "APP_CONFIG_VERSION_NOT_MONOTONIC", "配置版本必须单调增加")
                timestamp = now_ms()
                db.execute(
                    "UPDATE app_config_revisions SET status='ARCHIVED',updated_by=?,updated_at=? "
                    "WHERE platform='android' AND status='ACTIVE'",
                    (admin["id"], timestamp),
                )
                db.execute(
                    "UPDATE app_config_revisions SET status='ACTIVE',updated_by=?,updated_at=? WHERE revision=?",
                    (admin["id"], timestamp, revision_id),
                )
                db.execute(
                    """UPDATE app_config_state
                       SET active_revision=?,config_version=?,updated_by=?,updated_at=?
                       WHERE platform='android'""",
                    (revision_id, revision["config_version"], admin["id"], timestamp),
                )
                write_audit(
                    db,
                    admin["id"],
                    "APP_CONFIG_ACTIVATED",
                    "APP_CONFIG",
                    str(revision_id),
                    {
                        "configVersion": revision["config_version"],
                        "configHash": revision["config_hash"],
                    },
                )
                db.execute("COMMIT")
                return admin_app_config_state(db), 200

            if method == "POST" and path == "/api/v1/admin/app-config/rollback":
                require_admin_permission(admin, "*")
                data = self.body_json()
                try:
                    target_revision = int(data.get("targetRevision", 0))
                    expected_active = int(data.get("expectedActiveRevision", 0))
                except (TypeError, ValueError) as exc:
                    raise ApiError(422, "INVALID_APP_CONFIG", "回滚 Revision 无效") from exc
                db.execute("BEGIN IMMEDIATE")
                state = db.execute(
                    "SELECT * FROM app_config_state WHERE platform='android'"
                ).fetchone()
                actual_active = int(state["active_revision"]) if state and state["active_revision"] else 0
                if expected_active != actual_active:
                    raise ApiError(409, "APP_CONFIG_REVISION_CONFLICT", "配置已被其他管理员修改，请刷新后重试")
                target = db.execute(
                    "SELECT * FROM app_config_revisions WHERE revision=? AND platform='android'",
                    (target_revision,),
                ).fetchone()
                if not target:
                    raise ApiError(404, "APP_CONFIG_REVISION_NOT_FOUND", "回滚目标不存在")
                next_version = db.execute(
                    "SELECT COALESCE(MAX(config_version),0) AS config_version "
                    "FROM app_config_revisions WHERE platform='android'"
                ).fetchone()["config_version"] + 1
                timestamp = now_ms()
                cursor = db.execute(
                    """INSERT INTO app_config_revisions(
                           platform,schema_version,config_version,min_app_version_code,
                           max_app_version_code,config_hash,config_json,status,
                           created_by,created_at,updated_by,updated_at
                       ) VALUES('android',?,?,?,?,?,?, 'ACTIVE',?,?,?,?)""",
                    (
                        target["schema_version"],
                        next_version,
                        target["min_app_version_code"],
                        target["max_app_version_code"],
                        target["config_hash"],
                        target["config_json"],
                        admin["id"],
                        timestamp,
                        admin["id"],
                        timestamp,
                    ),
                )
                db.execute(
                    "UPDATE app_config_revisions SET status='ARCHIVED',updated_by=?,updated_at=? "
                    "WHERE platform='android' AND status='ACTIVE'",
                    (admin["id"], timestamp),
                )
                db.execute(
                    "UPDATE app_config_state SET active_revision=?,config_version=?,updated_by=?,updated_at=? "
                    "WHERE platform='android'",
                    (cursor.lastrowid, next_version, admin["id"], timestamp),
                )
                write_audit(
                    db,
                    admin["id"],
                    "APP_CONFIG_ROLLED_BACK",
                    "APP_CONFIG",
                    str(cursor.lastrowid),
                    {
                        "targetRevision": target_revision,
                        "configVersion": next_version,
                        "configHash": target["config_hash"],
                    },
                )
                db.execute("COMMIT")
                return admin_app_config_state(db), 200

            match = re.fullmatch(r"/api/v1/admin/flags/([^/]+)", path)
            if method == "PUT" and match:
                require_admin_permission(admin, "*")
                feature_key = match.group(1)
                if feature_key not in CONTROLLED_FEATURES:
                    raise ApiError(404, "FEATURE_NOT_FOUND", "功能开关不存在")
                data = self.body_json()
                payload = data.get("payload", {})
                if not isinstance(payload, dict):
                    raise ApiError(400, "VALIDATION_ERROR", "功能开关配置必须是对象")
                status = str(payload.get("status", "OPEN")).upper()
                if status not in {"OPEN", "CLOSED", "MAINTENANCE"}:
                    raise ApiError(400, "VALIDATION_ERROR", "功能运行状态无效")
                payload = {
                    **payload,
                    "status": status,
                    "maintenanceNote": str(payload.get("maintenanceNote", "")).strip()[:300],
                    "expectedRestoreAt": payload.get("expectedRestoreAt") or None,
                }
                change_note = str(data.get("changeNote", "")).strip()[:300]
                previous = feature_state(db, feature_key)
                timestamp = now_ms()
                enabled = status != "CLOSED"
                db.execute(
                    """INSERT INTO remote_flags(
                           key,enabled,payload_json,updated_by,change_note,updated_at
                       ) VALUES(?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET
                       enabled=excluded.enabled,payload_json=excluded.payload_json,
                       updated_by=excluded.updated_by,change_note=excluded.change_note,
                       updated_at=excluded.updated_at""",
                    (
                        feature_key,
                        1 if enabled else 0,
                        json.dumps(payload, ensure_ascii=False),
                        admin["id"],
                        change_note,
                        timestamp,
                    ),
                )
                write_audit(
                    db,
                    admin["id"],
                    "FEATURE_FLAG_UPDATED",
                    "REMOTE_FLAG",
                    feature_key,
                    {
                        "enabled": enabled,
                        "status": str(payload.get("status", "")),
                        "previousStatus": previous["status"],
                        "changeNoteLength": len(change_note),
                    },
                )
                return {
                    "key": feature_key,
                    "enabled": enabled,
                    "payload": payload,
                    "updatedAt": timestamp,
                    "updatedBy": admin["id"],
                    "changeNote": change_note,
                }, 200

            if method == "PUT" and path == "/api/v1/admin/update-policy/android":
                require_admin_permission(admin, "*")
                data = self.body_json()
                update_enabled = parse_boolean(
                    data.get("updateEnabled"), "updateEnabled"
                )
                force_update_enabled = parse_boolean(
                    data.get("forceUpdateEnabled"), "forceUpdateEnabled"
                )
                if force_update_enabled and not update_enabled:
                    raise ApiError(
                        400,
                        "INVALID_UPDATE_POLICY",
                        "关闭更新检查时不能开启强制更新",
                    )
                try:
                    minimum = int(data.get("minimumSupportedVersionCode", 0))
                    expected_revision = int(data.get("expectedPolicyRevision", 0))
                except (TypeError, ValueError) as exc:
                    raise ApiError(400, "VALIDATION_ERROR", "更新策略版本号无效") from exc
                # Take the write lock before reading revision/release state so
                # two admins cannot both accept the same expected revision.
                db.execute("BEGIN IMMEDIATE")
                release = db.execute(
                    "SELECT latest_version_code FROM app_versions "
                    "WHERE platform='android' AND status='PUBLISHED'"
                ).fetchone()
                if not release:
                    raise ApiError(409, "PUBLISHED_RELEASE_REQUIRED", "当前没有正式发布版本")
                latest = int(release["latest_version_code"])
                if minimum < 1 or minimum > latest:
                    raise ApiError(400, "VALIDATION_ERROR", "最低支持版本号无效")
                if not db.execute(
                    """SELECT 1 FROM app_release_versions
                       WHERE platform='android' AND version_code=? AND status='PUBLISHED'""",
                    (minimum,),
                ).fetchone():
                    raise ApiError(
                        409,
                        "MINIMUM_VERSION_NOT_PUBLISHED",
                        "最低支持版本必须来自已发布版本记录",
                    )
                previous = update_policy_state(db)
                if expected_revision != previous["policyRevision"]:
                    raise ApiError(
                        409,
                        "POLICY_REVISION_CONFLICT",
                        "更新策略已被其他管理员修改，请刷新后重试",
                    )
                timestamp = now_ms()
                revision = previous["policyRevision"] + 1
                new_policy = {
                    "platform": "android",
                    "updateEnabled": update_enabled,
                    "forceUpdateEnabled": force_update_enabled,
                    "latestVersionCode": latest,
                    "minimumSupportedVersionCode": minimum,
                    "policyRevision": revision,
                    "updatedAt": timestamp,
                    "updatedBy": admin["id"],
                }
                db.execute(
                    """INSERT INTO app_update_policies(
                           platform,update_enabled,force_update_enabled,
                           minimum_supported_version_code,policy_revision,
                           updated_at,updated_by
                       ) VALUES('android',?,?,?,?,?,?)
                       ON CONFLICT(platform) DO UPDATE SET
                           update_enabled=excluded.update_enabled,
                           force_update_enabled=excluded.force_update_enabled,
                           minimum_supported_version_code=
                               excluded.minimum_supported_version_code,
                           policy_revision=excluded.policy_revision,
                           updated_at=excluded.updated_at,
                           updated_by=excluded.updated_by""",
                    (
                        1 if update_enabled else 0,
                        1 if force_update_enabled else 0,
                        minimum,
                        revision,
                        timestamp,
                        admin["id"],
                    ),
                )
                write_audit(
                    db,
                    admin["id"],
                    "APP_UPDATE_POLICY_UPDATED",
                    "APP_UPDATE_POLICY",
                    "android",
                    {
                        "operator": admin["id"],
                        "oldPolicy": previous,
                        "newPolicy": new_policy,
                        "policyRevision": revision,
                        "updatedAt": timestamp,
                    },
                )
                db.execute("COMMIT")
                return new_policy, 200

            if method == "PUT" and path == "/api/v1/admin/version/android":
                require_admin_permission(admin, "*")
                data = self.body_json()
                try:
                    latest = int(data.get("latestVersionCode", 0))
                except (TypeError, ValueError) as exc:
                    raise ApiError(400, "VALIDATION_ERROR", "版本号配置无效") from exc
                if latest < 1:
                    raise ApiError(400, "VALIDATION_ERROR", "版本号配置无效")
                current_policy = update_policy_state(db)
                minimum = int(current_policy["minimumSupportedVersionCode"])
                force_update = bool(current_policy["forceUpdateEnabled"])
                if minimum > latest:
                    raise ApiError(
                        409,
                        "UPDATE_POLICY_INCOMPATIBLE",
                        "当前最低支持版本高于要保存的发布版本，请先调整更新策略",
                    )
                status = str(data.get("status", "DRAFT")).upper()
                if status not in {"DRAFT", "PUBLISHED", "DISABLED"}:
                    raise ApiError(400, "VALIDATION_ERROR", "版本发布状态无效")
                latest_name = required_text(data, "latestVersionName", 40)
                download_url = str(data.get("downloadUrl", "")).strip()[:500] or None
                if download_url and not re.fullmatch(r"https://[^\s]+|http://127\.0\.0\.1(?::\d+)?/[^\s]+", download_url):
                    raise ApiError(400, "UNSAFE_DOWNLOAD_URL", "下载地址必须为 HTTPS、本机地址或留空")
                sha256 = str(data.get("apkSha256", "")).strip().lower()
                if sha256 and not re.fullmatch(r"[0-9a-f]{64}", sha256):
                    raise ApiError(400, "INVALID_APK_SHA256", "APK SHA-256 格式无效")
                apk_size = data.get("apkSize")
                if apk_size not in (None, ""):
                    try:
                        apk_size = int(apk_size)
                    except (TypeError, ValueError) as exc:
                        raise ApiError(400, "INVALID_APK_SIZE", "APK 大小必须为整数") from exc
                else:
                    apk_size = None
                if apk_size is not None and apk_size <= 0:
                    raise ApiError(400, "INVALID_APK_SIZE", "APK 大小必须大于 0")
                package_name = str(data.get("packageName", "")).strip()[:200] or None
                certificate = str(data.get("signingCertificateSha256", "")).strip().upper() or None
                metadata_verified = parse_boolean(
                    data.get("metadataVerified"),
                    "metadataVerified",
                    default=False,
                )
                if certificate and not re.fullmatch(r"[0-9A-F]{64}", certificate):
                    raise ApiError(400, "INVALID_SIGNING_CERTIFICATE", "签名证书 SHA-256 格式无效")
                if status == "PUBLISHED":
                    if not re.fullmatch(r"[0-9A-F]{64}", OFFICIAL_SIGNING_CERTIFICATE_SHA256):
                        raise ApiError(409, "RELEASE_NOT_CONFIGURED", "尚未配置本产品正式签名，不能发布版本")
                    if not download_url or not sha256 or apk_size is None:
                        raise ApiError(409, "PUBLISHED_ARTIFACT_REQUIRED", "已发布版本必须具备下载地址、SHA-256 和 APK 大小")
                    if package_name != OFFICIAL_PACKAGE_NAME:
                        raise ApiError(409, "PACKAGE_NAME_MISMATCH", "APK 包名不是正式包名")
                    if certificate != OFFICIAL_SIGNING_CERTIFICATE_SHA256:
                        raise ApiError(409, "SIGNING_CERTIFICATE_MISMATCH", "APK 签名证书不是正式证书")
                    if not metadata_verified:
                        raise ApiError(409, "APK_METADATA_UNVERIFIED", "APK 元数据尚未完成自动校验")
                    if minimum != latest and not db.execute(
                        """SELECT 1 FROM app_release_versions
                           WHERE platform='android' AND version_code=? AND status='PUBLISHED'""",
                        (minimum,),
                    ).fetchone():
                        raise ApiError(409, "MINIMUM_VERSION_NOT_PUBLISHED", "最低支持版本必须来自已发布版本记录")
                    verify_release_artifact(download_url, sha256, apk_size)
                timestamp = now_ms()
                db.execute(
                    """INSERT INTO app_versions(
                           platform,latest_version_code,minimum_version_code,
                           latest_version_name,update_message,download_url,apk_sha256,
                           apk_size,package_name,signing_certificate_sha256,
                           metadata_verified,force_update,status,published_at,
                           created_at,created_by,updated_by,updated_at
                       ) VALUES('android',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(platform) DO UPDATE SET
                       latest_version_code=excluded.latest_version_code,
                       minimum_version_code=excluded.minimum_version_code,
                       latest_version_name=excluded.latest_version_name,
                       update_message=excluded.update_message,
                       download_url=excluded.download_url,
                       apk_sha256=excluded.apk_sha256,
                       apk_size=excluded.apk_size,
                       package_name=excluded.package_name,
                       signing_certificate_sha256=excluded.signing_certificate_sha256,
                       metadata_verified=excluded.metadata_verified,
                       force_update=excluded.force_update,
                       status=excluded.status,
                       published_at=CASE WHEN excluded.status='PUBLISHED'
                           THEN COALESCE(app_versions.published_at,excluded.updated_at)
                           ELSE app_versions.published_at END,
                       updated_by=excluded.updated_by,
                       updated_at=excluded.updated_at""",
                    (
                        latest,
                        minimum,
                        latest_name,
                        str(data.get("updateMessage", ""))[:1000],
                        download_url,
                        sha256 or None,
                        apk_size,
                        package_name,
                        certificate,
                        1 if metadata_verified else 0,
                        1 if force_update else 0,
                        status,
                        timestamp if status == "PUBLISHED" else None,
                        timestamp,
                        admin["id"],
                        admin["id"],
                        timestamp,
                    ),
                )
                db.execute(
                    """INSERT INTO app_release_versions(
                           platform,version_code,version_name,update_message,
                           download_url,apk_sha256,apk_size,package_name,
                           signing_certificate_sha256,metadata_verified,status,
                           published_at,created_at,created_by,updated_by,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(platform,version_code) DO UPDATE SET
                           version_name=excluded.version_name,
                           update_message=excluded.update_message,
                           download_url=excluded.download_url,
                           apk_sha256=excluded.apk_sha256,
                           apk_size=excluded.apk_size,
                           package_name=excluded.package_name,
                           signing_certificate_sha256=excluded.signing_certificate_sha256,
                           metadata_verified=excluded.metadata_verified,
                           status=excluded.status,
                           published_at=CASE WHEN excluded.status='PUBLISHED'
                               THEN COALESCE(app_release_versions.published_at,excluded.updated_at)
                               ELSE app_release_versions.published_at END,
                           updated_by=excluded.updated_by,
                           updated_at=excluded.updated_at""",
                    (
                        "android",
                        latest,
                        latest_name,
                        str(data.get("updateMessage", ""))[:1000],
                        download_url,
                        sha256 or None,
                        apk_size,
                        package_name,
                        certificate,
                        1 if metadata_verified else 0,
                        status,
                        timestamp if status == "PUBLISHED" else None,
                        timestamp,
                        admin["id"],
                        admin["id"],
                        timestamp,
                    ),
                )
                write_audit(
                    db,
                    admin["id"],
                    "APP_VERSION_UPDATED",
                    "APP_VERSION",
                    "android",
                    {
                        "latestVersionCode": latest,
                        "minimumVersionCode": minimum,
                        "status": status,
                        "forceUpdate": force_update,
                        "metadataVerified": metadata_verified,
                    },
                )
                saved = db.execute(
                    "SELECT download_url,apk_sha256,apk_size,package_name,"
                    "signing_certificate_sha256,metadata_verified,force_update,published_at "
                    "FROM app_versions WHERE platform='android'"
                ).fetchone()
                return {
                    "platform": "android",
                    "latestVersionCode": latest,
                    "minimumVersionCode": minimum,
                    "status": status,
                    "forceUpdate": bool(saved["force_update"]),
                    "downloadUrl": saved["download_url"],
                    "apkSha256": saved["apk_sha256"],
                    "apkSize": saved["apk_size"],
                    "packageName": saved["package_name"],
                    "signingCertificateSha256": saved["signing_certificate_sha256"],
                    "metadataVerified": bool(saved["metadata_verified"]),
                    "publishedAt": saved["published_at"],
                }, 200

            if method == "GET" and path == "/api/v1/admin/feedback":
                clauses, values = [], []
                if query.get("status", "").upper() in FEEDBACK_STATUSES:
                    clauses.append("f.workflow_status=?")
                    values.append(query["status"].upper())
                if query.get("category", "").upper() in FEEDBACK_CATEGORIES:
                    clauses.append("f.category=?")
                    values.append(query["category"].upper())
                if query.get("q"):
                    keyword = str(query["q"]).strip()[:100]
                    token = f"%{keyword}%"
                    clauses.append(
                        "(f.title LIKE ? OR f.content LIKE ? OR f.id LIKE ?)"
                    )
                    values.extend([token, token, token])
                if query.get("user"):
                    append_user_search(clauses, values, query.get("user"))
                if query.get("from"):
                    start = date.fromisoformat(parse_iso_date(query["from"]))
                    clauses.append("f.created_at>=?")
                    values.append(
                        int(datetime.combine(start, datetime.min.time()).timestamp() * 1000)
                    )
                if query.get("to"):
                    end = date.fromisoformat(parse_iso_date(query["to"]))
                    clauses.append("f.created_at<?")
                    values.append(
                        int(
                            datetime.combine(
                                end + timedelta(days=1), datetime.min.time()
                            ).timestamp()
                            * 1000
                        )
                    )
                where = " WHERE " + " AND ".join(clauses) if clauses else ""
                order = "ASC" if query.get("sort", "").upper() == "ASC" else "DESC"
                return [
                    row_dict(row)
                    for row in db.execute(
                        """SELECT f.id,f.user_id,f.category,f.title,f.content,
                                  f.app_version,f.workflow_status,
                                  f.public_reply,f.created_at,f.updated_at,
                                  u.nickname,u.phone_masked
                           FROM feedback f
                           LEFT JOIN users u ON u.id=f.user_id"""
                        + where
                        + f" ORDER BY f.created_at {order} LIMIT 500",
                        values,
                    )
                ], 200

            match = re.fullmatch(r"/api/v1/admin/feedback/([^/]+)", path)
            if method == "GET" and match:
                feedback = db.execute(
                    """SELECT f.*,u.nickname,u.phone_masked
                       FROM feedback f LEFT JOIN users u ON u.id=f.user_id
                       WHERE f.id=?""",
                    (match.group(1),),
                ).fetchone()
                if not feedback:
                    raise ApiError(404, "FEEDBACK_NOT_FOUND", "反馈不存在")
                events = [
                    row_dict(row)
                    for row in db.execute(
                        """SELECT e.id,e.actor_user_id,e.actor_role,e.action,
                                  e.old_status,e.new_status,e.public_visible,
                                  e.public_message,e.created_at,
                                  u.nickname AS actor_name
                           FROM feedback_events e
                           LEFT JOIN users u ON u.id=e.actor_user_id
                           WHERE e.feedback_id=? ORDER BY e.created_at ASC""",
                        (match.group(1),),
                    )
                ]
                audits = [
                    row_dict(row)
                    for row in db.execute(
                        """SELECT a.id,a.actor_user_id,a.action,a.details_json,
                                  a.created_at,u.nickname AS actor_name
                           FROM admin_audit_logs a
                           LEFT JOIN users u ON u.id=a.actor_user_id
                           WHERE a.entity_type='FEEDBACK' AND a.entity_id=?
                           ORDER BY a.created_at ASC""",
                        (match.group(1),),
                    )
                ]
                return {
                    "feedback": row_dict(feedback),
                    "events": events,
                    "audit": audits,
                }, 200

            if method == "PATCH" and match:
                data = self.body_json()
                status = str(data.get("status", "")).upper()
                if status not in FEEDBACK_STATUSES:
                    raise ApiError(400, "VALIDATION_ERROR", "反馈状态无效")
                current = db.execute(
                    "SELECT * FROM feedback WHERE id=?", (match.group(1),)
                ).fetchone()
                if not current:
                    raise ApiError(404, "FEEDBACK_NOT_FOUND", "反馈不存在")
                public_reply = str(
                    data.get("publicReply", current["public_reply"] or "")
                ).strip()
                internal_note = str(
                    data.get("internalNote", current["internal_note"] or "")
                ).strip()
                if len(public_reply) > 2000 or len(internal_note) > 2000:
                    raise ApiError(400, "VALIDATION_ERROR", "处理说明或内部备注不能超过 2000 字")
                if status in {"RESOLVED", "CLOSED"} and not public_reply:
                    raise ApiError(400, "PUBLIC_REPLY_REQUIRED", "已解决或已关闭时请填写对用户可见的处理说明")
                timestamp = now_ms()
                db.execute(
                    """UPDATE feedback SET status=?,workflow_status=?,
                       public_reply=?,internal_note=?,handled_by=?,resolved_at=?,
                       updated_at=? WHERE id=?""",
                    (
                        "CLOSED" if status in {"RESOLVED", "CLOSED"} else status,
                        status,
                        public_reply,
                        internal_note,
                        admin["id"],
                        timestamp if status in {"RESOLVED", "CLOSED"} else None,
                        timestamp,
                        current["id"],
                    ),
                )
                public_changed = public_reply != (current["public_reply"] or "")
                note_changed = internal_note != (current["internal_note"] or "")
                db.execute(
                    """INSERT INTO feedback_events(
                           id,feedback_id,actor_user_id,actor_role,action,
                           old_status,new_status,public_visible,public_message,created_at
                       ) VALUES(?,?,?,'ADMIN','UPDATED',?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()),
                        current["id"],
                        admin["id"],
                        current["workflow_status"],
                        status,
                        1 if public_changed else 0,
                        public_reply if public_changed else "",
                        timestamp,
                    ),
                )
                write_audit(
                    db,
                    admin["id"],
                    "FEEDBACK_UPDATED",
                    "FEEDBACK",
                    current["id"],
                    {
                        "oldStatus": current["workflow_status"],
                        "newStatus": status,
                        "publicReplyChanged": public_changed,
                        "publicReplyLength": len(public_reply),
                        "internalNoteChanged": note_changed,
                        "internalNoteLength": len(internal_note),
                    },
                )
                return {
                    "id": current["id"],
                    "status": status,
                    "updatedAt": timestamp,
                }, 200

        raise ApiError(404, "NOT_FOUND", "管理员接口不存在")



def run_safely_delete_upload(name: str) -> None:
    target = (UPLOADS / name).resolve()
    if target.parent == UPLOADS.resolve() and target.is_file():
        target.unlink()


def main() -> None:
    if RUNTIME.is_production:
        raise RuntimeError(
            "server.py uses the development HTTP server; run python production.py"
        )
    initialize_database()
    print(f"MyChuannong backend listening on http://{HOST}:{PORT}")
    print(f"Database: {DB_PATH}")
    ThreadingHTTPServer((HOST, PORT), ApiHandler).serve_forever()


if __name__ == "__main__":
    main()
