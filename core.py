"""Shared foundational helpers for the My Changli local backend.

This module intentionally contains only stable, low-risk utilities used by
``server.py``: error model, time handling, SQLite connection lifecycle,
request validation, pagination and JSON response serialization.

It does not contain route definitions. Routes and business logic remain in
``server.py`` so API paths, request fields, response fields and error codes
keep their existing contract while the codebase becomes easier to maintain.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from runtime_config import RUNTIME

ROOT = Path(__file__).resolve().parent
DB_PATH = RUNTIME.database_path
MAX_BODY = RUNTIME.max_body_bytes
SESSION_SECONDS = 30 * 24 * 60 * 60
MAX_PAGE_SIZE = 100
MAX_PAGE_OFFSET = 10000
TEST_DB_PREFIX = "TEST_"
BUSINESS_TIMEZONE_NAME = "Asia/Shanghai"
# Shanghai has no daylight-saving transition; a fixed UTC+8 offset keeps the
# business-day contract dependency-free on Windows runtimes without tzdata.
BUSINESS_TIMEZONE = timezone(timedelta(hours=8), name=BUSINESS_TIMEZONE_NAME)


def configure_test_runtime(database_path: Path) -> None:
    """Point the backend at an isolated temporary database for tests.

    This is the only supported way for tests to override storage. It refuses
    to accept the default development database so automated tests can never
    accidentally write to local operational data.
    """
    global DB_PATH
    resolved = database_path.resolve()
    default = (ROOT / "data" / "my-chuannong.db").resolve()
    if resolved == default:
        raise RuntimeError("Refusing to point the test runtime at the local development database")
    DB_PATH = resolved


class ApiError(Exception):
    """Domain error translated to a stable API error response."""

    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def now_ms() -> int:
    return int(time.time() * 1000)


def business_today() -> date:
    """Return the current calendar date in the product's business timezone."""
    return datetime.now(BUSINESS_TIMEZONE).date()


def business_day_bounds_ms(day: date | None = None) -> tuple[int, int]:
    """Return the inclusive start/exclusive end of a business day in ms."""
    business_day = day or business_today()
    start = datetime.combine(business_day, datetime.min.time(), tzinfo=BUSINESS_TIMEZONE)
    end = datetime.combine(
        business_day + timedelta(days=1), datetime.min.time(), tzinfo=BUSINESS_TIMEZONE
    )
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def today_iso() -> str:
    return business_today().isoformat()


def day_start_ms() -> int:
    return business_day_bounds_ms()[0]


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connection(database_path: Path | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with foreign keys and a busy timeout.

    Tests may pass a temporary database path; production callers keep using
    the module-level ``DB_PATH`` from this module.
    """
    path = database_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(
        path,
        timeout=RUNTIME.sqlite_busy_timeout_ms / 1000,
        isolation_level=None,
        factory=ClosingConnection,
    )
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute(f"PRAGMA busy_timeout = {RUNTIME.sqlite_busy_timeout_ms}")
    db.execute("PRAGMA synchronous = NORMAL")
    return db


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def required_text(data: dict[str, Any], key: str, maximum: int) -> str:
    value = str(data.get(key, "")).strip()
    if not value:
        raise ApiError(400, "VALIDATION_ERROR", f"{key} 不能为空")
    if len(value) > maximum:
        raise ApiError(400, "VALIDATION_ERROR", f"{key} 长度不能超过 {maximum} 个字符")
    return value


def optional_text(data: dict[str, Any], key: str, maximum: int) -> str:
    value = str(data.get(key, "")).strip()
    if len(value) > maximum:
        raise ApiError(400, "VALIDATION_ERROR", f"{key} 长度不能超过 {maximum} 个字符")
    return value


def parse_pagination(
    query: dict[str, str],
    *,
    default_limit: int = 50,
    max_limit: int = MAX_PAGE_SIZE,
    max_offset: int = MAX_PAGE_OFFSET,
) -> tuple[int, int]:
    """Parse limit/offset with strict validation and safe clamping.

    Invalid or negative values are rejected instead of leaking a traceback.
    """
    raw_limit = str(query.get("limit", str(default_limit))).strip()
    raw_offset = str(query.get("offset", "0")).strip()
    if not raw_limit.isdigit() or not raw_offset.isdigit():
        raise ApiError(422, "VALIDATION_ERROR", "分页参数必须是非负整数")
    limit = min(max(int(raw_limit), 1), max_limit)
    offset = min(max(int(raw_offset), 0), max_offset)
    return limit, offset


def paginated_response(
    items: list[Any],
    total: int,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    return {
        "items": items,
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


def json_response(status: int, payload: Any) -> tuple[bytes, str]:
    """Serialize an API payload to bytes with the stable content type."""
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return encoded, "application/json; charset=utf-8"
